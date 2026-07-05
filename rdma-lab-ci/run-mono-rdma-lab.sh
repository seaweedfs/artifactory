#!/usr/bin/env bash
set -euo pipefail

MONO_REPO="${MONO_REPO:-https://github.com/seaweedfs/seaweed-mono.git}"
MONO_REF="${MONO_REF:-main}"
PROFILE="${RDMA_CI_PROFILE:-unified}"
SKIP_BUILD="${SKIP_BUILD:-0}"
M02_HOST="${M02_HOST:-testdev@192.168.1.184}"
M01_WORKDIR="${M01_WORKDIR:-/opt/rdma-lab-ci/work}"
M02_WORKDIR="${M02_WORKDIR:-/opt/rdma-lab-ci/work}"
ARTIFACT_DIR="${ARTIFACT_DIR:-$PWD/rdma-lab-runs}"
RDMA_PIPES="${RDMA_PIPES:-8}"
ENABLE_DC="${ENABLE_DC:-0}"
DC_INITIATORS="${DC_INITIATORS:-4}"
LMCACHE_REPO="${LMCACHE_REPO:-https://github.com/LMCache/LMCache.git}"
LMCACHE_REF="${LMCACHE_REF:-dev}"
LMCACHE_VENV="${LMCACHE_VENV:-/opt/work/lmcache-connector-venv}"
LMCACHE_SIZES="${LMCACHE_SIZES:-1048576 20971520}"
LMCACHE_WORKLOAD_SECONDS="${LMCACHE_WORKLOAD_SECONDS:-0}"
LMCACHE_WORKLOAD_SAVE_BATCH_SIZE="${LMCACHE_WORKLOAD_SAVE_BATCH_SIZE:-4}"
LMCACHE_WORKLOAD_LOAD_BATCH_SIZE="${LMCACHE_WORKLOAD_LOAD_BATCH_SIZE:-4}"
LMCACHE_WORKLOAD_SEED_SETTLE_SECS="${LMCACHE_WORKLOAD_SEED_SETTLE_SECS:-0.5}"
LMCACHE_RUN_NIXL_RUNTIME="${LMCACHE_RUN_NIXL_RUNTIME:-1}"
LMCACHE_REQUIRE_NIXL_RUNTIME="${LMCACHE_REQUIRE_NIXL_RUNTIME:-1}"
LMCACHE_NIXL_RUNTIME_BACKEND="${LMCACHE_NIXL_RUNTIME_BACKEND:-UCX}"
LMCACHE_NIXL_RUNTIME_SIZES="${LMCACHE_NIXL_RUNTIME_SIZES:-1048576}"
NIXL_PIP_SPEC="${NIXL_PIP_SPEC:-nixl==1.3.0}"
CLEANUP_M01_SCRIPT=""
CLEANUP_M02_SCRIPT=""

usage() {
  cat <<'USAGE'
Usage: run-mono-rdma-lab.sh [options]

Options:
  --repo URL          seaweed-mono git URL
  --ref REF           branch, tag, or SHA to test
  --profile NAME      unified (default), lmcache-nixl
  --lmcache-repo URL  LMCache git URL for lmcache-nixl
  --lmcache-ref REF   LMCache ref for lmcache-nixl
  --m02 HOST          ssh target for M02
  --workdir PATH      M01 work directory
  --m02-workdir PATH  M02 work directory
  --artifacts PATH    local artifact directory
  --enable-dc         enable DC rows in the unified gate
  --skip-build        reuse existing build artifacts
  -h, --help          show this help
USAGE
}

while [ "$#" -gt 0 ]; do
  case "$1" in
    --repo) MONO_REPO="$2"; shift 2 ;;
    --ref) MONO_REF="$2"; shift 2 ;;
    --profile) PROFILE="$2"; shift 2 ;;
    --lmcache-repo) LMCACHE_REPO="$2"; shift 2 ;;
    --lmcache-ref) LMCACHE_REF="$2"; shift 2 ;;
    --m02) M02_HOST="$2"; shift 2 ;;
    --workdir) M01_WORKDIR="$2"; shift 2 ;;
    --m02-workdir) M02_WORKDIR="$2"; shift 2 ;;
    --artifacts) ARTIFACT_DIR="$2"; shift 2 ;;
    --enable-dc) ENABLE_DC=1; shift ;;
    --skip-build) SKIP_BUILD=1; shift ;;
    -h|--help) usage; exit 0 ;;
    *) echo "unknown argument: $1" >&2; usage >&2; exit 2 ;;
  esac
done

slug_ref="$(printf '%s' "$MONO_REF" | tr '/:@ ' '----' | tr -cd 'A-Za-z0-9._-')"
run_id="$(date -u +%Y%m%d-%H%M%S)-${slug_ref}-${PROFILE}"
run_dir="$ARTIFACT_DIR/$run_id"
mkdir -p "$run_dir"

log="$run_dir/run.log"
exec > >(tee -a "$log") 2>&1

echo "RDMA lab CI"
echo "repo=$MONO_REPO"
echo "ref=$MONO_REF"
echo "profile=$PROFILE"
  echo "m02=$M02_HOST"
  echo "run_dir=$run_dir"

require_cmd() {
  command -v "$1" >/dev/null 2>&1 || {
    echo "missing required command: $1" >&2
    exit 1
  }
}

mark_safe_local_repo() {
  local repo="$1"
  local path="$repo"
  case "$path" in
    file://*) path="${path#file://}" ;;
  esac
  if [ -d "$path" ]; then
    git config --global --add safe.directory "$path" || true
  fi
}

cleanup_lab() {
  set +e
  if [ -n "$CLEANUP_M01_SCRIPT" ] && [ -f "$CLEANUP_M01_SCRIPT" ]; then
    bash "$CLEANUP_M01_SCRIPT" m01
  fi
  if [ -n "$CLEANUP_M02_SCRIPT" ]; then
    ssh "$M02_HOST" "bash '$CLEANUP_M02_SCRIPT' m02"
  fi
}

require_cmd git
require_cmd ssh
require_cmd rsync

preflight() {
  echo "== preflight =="
  hostname
  ssh -o BatchMode=yes "$M02_HOST" 'hostname; test -d /mnt/smb/work/share/rdma-source || true'
  ssh -o BatchMode=yes "$M02_HOST" 'command -v bash >/dev/null'
  if [ "$PROFILE" = "lmcache-nixl" ]; then
    test -x "$LMCACHE_VENV/bin/python" || {
      echo "missing LMCache venv python: $LMCACHE_VENV/bin/python" >&2
      exit 1
    }
  fi
}

checkout_source() {
  echo "== checkout source on M01 =="
  mkdir -p "$M01_WORKDIR"
  local src="$M01_WORKDIR/seaweed-mono"
  mark_safe_local_repo "$MONO_REPO"
  if [ ! -d "$src/.git" ]; then
    rm -rf "$src"
    git clone "$MONO_REPO" "$src"
  else
    git -C "$src" remote set-url origin "$MONO_REPO"
  fi
  git -C "$src" fetch --all --prune
  if git -C "$src" show-ref --verify --quiet "refs/remotes/origin/$MONO_REF"; then
    git -C "$src" checkout --force -B rdma-ci-run "origin/$MONO_REF"
  else
    git -C "$src" checkout --force "$MONO_REF"
  fi
  git -C "$src" submodule update --init --recursive || true
  git -C "$src" rev-parse HEAD > "$run_dir/mono.sha"
  git -C "$src" status --short > "$run_dir/mono.status"
  echo "mono_sha=$(cat "$run_dir/mono.sha")"
}

checkout_lmcache_source() {
  [ "$PROFILE" = "lmcache-nixl" ] || return 0
  echo "== checkout LMCache source on M01 =="
  mkdir -p "$M01_WORKDIR"
  local src="$M01_WORKDIR/LMCache"
  mark_safe_local_repo "$LMCACHE_REPO"
  if [ ! -d "$src/.git" ]; then
    rm -rf "$src"
    git clone "$LMCACHE_REPO" "$src"
  else
    git -C "$src" remote set-url origin "$LMCACHE_REPO"
  fi
  git -C "$src" fetch --all --prune
  if git -C "$src" show-ref --verify --quiet "refs/remotes/origin/$LMCACHE_REF"; then
    git -C "$src" checkout --force -B lmcache-ci-run "origin/$LMCACHE_REF"
  else
    git -C "$src" checkout --force "$LMCACHE_REF"
  fi
  git -C "$src" submodule update --init --recursive || true
  git -C "$src" rev-parse HEAD > "$run_dir/lmcache.sha"
  git -C "$src" status --short > "$run_dir/lmcache.status"
  echo "lmcache_sha=$(cat "$run_dir/lmcache.sha")"
}

sync_source_to_m02() {
  echo "== sync source to M02 =="
  local src="$M01_WORKDIR/seaweed-mono"
  ssh "$M02_HOST" "mkdir -p '$M02_WORKDIR'"
  rsync -a --delete \
    --exclude .git \
    --exclude target \
    --exclude tmp-unified-logs \
    "$src/" "$M02_HOST:$M02_WORKDIR/seaweed-mono/"
}

ensure_lmcache_nixl_runtime() {
  [ "$PROFILE" = "lmcache-nixl" ] || return 0
  echo "== ensure LMCache Python runtime =="
  "$LMCACHE_VENV/bin/python" - <<'PY' || "$LMCACHE_VENV/bin/pip" install "$NIXL_PIP_SPEC"
import importlib.util
raise SystemExit(0 if importlib.util.find_spec("nixl") else 1)
PY
  "$LMCACHE_VENV/bin/python" - <<'PY'
import importlib.metadata
print("nixl_version=" + importlib.metadata.version("nixl"))
PY
}

build_unified_gate() {
  echo "== build unified RDMA gate binaries =="
  local m01_src="$M01_WORKDIR/seaweed-mono"
  local m02_src="$M02_WORKDIR/seaweed-mono"
  local object_features="real-rdma"
  local volume_features="rdma"
  if [ "$ENABLE_DC" = "1" ]; then
    object_features="mlx5-dc"
    volume_features="rdma,rdma-dc"
  fi

  bash -lc "source ~/.cargo/env 2>/dev/null || true; cd '$m01_src/enterprise/rust' && cargo build --release -p seaweedfs-sw-rdma-object --features '$object_features' --bin sw-rdma-object-put --bin sw-rdma-object-get --bin sw-rdma-object-bench --bin sw-rdma-s3-loader && cargo build --release -p seaweedfs-sw-rdma-vfs --features daemon --bin sw-rdma-kd"
  bash -lc "source ~/.cargo/env 2>/dev/null || true; cd '$m01_src/seaweed-vfs' && cargo build --release -p sw-kd --bin sw-kd"
  ssh "$M02_HOST" "bash -lc 'source ~/.cargo/env 2>/dev/null || true; cd \"$m02_src/enterprise/seaweed-volume\" && cargo build --release --features \"$volume_features\"'"
}

build_lmcache_nixl_gate() {
  echo "== build LMCache/NIXL gate binaries =="
  local m01_src="$M01_WORKDIR/seaweed-mono"
  local m02_src="$M02_WORKDIR/seaweed-mono"
  bash -lc "source ~/.cargo/env 2>/dev/null || true; cd '$m01_src/enterprise/rust' && cargo build --release -p seaweedfs-sw-rdma-nixl --features real-rdma"
  ssh "$M02_HOST" "bash -lc 'source ~/.cargo/env 2>/dev/null || true; cd \"$m02_src/enterprise/seaweed-volume\" && cargo build --release --features rdma'"
}

run_unified_gate() {
  echo "== unified RDMA gate =="
  local m01_src="$M01_WORKDIR/seaweed-mono"
  local m02_src="$M02_WORKDIR/seaweed-mono"
  local gate="enterprise/rust/sw-rdma-loader/tests/lab/unified-rdma-gate"

  test -f "$m01_src/$gate/m01-unified.sh" || {
    echo "missing $gate/m01-unified.sh in mono ref $MONO_REF" >&2
    exit 1
  }
  ssh "$M02_HOST" "test -f '$m02_src/$gate/m02-up.sh'"

  set +e
  bash "$m01_src/$gate/teardown.sh" m01
  ssh "$M02_HOST" "bash '$m02_src/$gate/teardown.sh' m02"
  set -e

  local dc_env=""
  if [ "$ENABLE_DC" = "1" ]; then
    dc_env="ENABLE_DC=1 SWFS_RDMA_DC_INITIATORS=$DC_INITIATORS"
  fi

  ssh "$M02_HOST" "$dc_env MONO='$m02_src' bash '$m02_src/$gate/m02-up.sh'"

  CLEANUP_M01_SCRIPT="$m01_src/$gate/teardown.sh"
  CLEANUP_M02_SCRIPT="$m02_src/$gate/teardown.sh"
  trap cleanup_lab EXIT

  local dc_m01=""
  if [ "$ENABLE_DC" = "1" ]; then
    dc_m01="ENABLE_DC=1"
  fi

  RDMA_PIPES="$RDMA_PIPES" MONO="$m01_src" bash -c "$dc_m01 bash '$m01_src/$gate/m01-unified.sh'"
  ssh "$M02_HOST" "MIN_COMMITTED_BYTES=155189248 bash '$m02_src/$gate/m02-check.sh'"
  echo "UNIFIED_RDMA_GATE_PASS"
}

run_lmcache_nixl_gate() {
  echo "== LMCache NIXL CPU runtime gate =="
  local m01_src="$M01_WORKDIR/seaweed-mono"
  local m02_src="$M02_WORKDIR/seaweed-mono"
  local lmcache_src="$M01_WORKDIR/LMCache"
  local unified_gate="enterprise/rust/sw-rdma-loader/tests/lab/unified-rdma-gate"
  local lmcache_gate="enterprise/python/seaweedkv-lmcache/tests/lab/lmcache-connector-gate"
  local lmcache_run_dir="$run_dir/lmcache-run"

  test -f "$m01_src/$lmcache_gate/m01-run.sh" || {
    echo "missing $lmcache_gate/m01-run.sh in mono ref $MONO_REF" >&2
    exit 1
  }
  test -d "$lmcache_src/lmcache" || {
    echo "missing LMCache package source under $lmcache_src" >&2
    exit 1
  }
  ssh "$M02_HOST" "test -f '$m02_src/$unified_gate/m02-up.sh'"

  set +e
  bash "$m01_src/$unified_gate/teardown.sh" m01
  ssh "$M02_HOST" "bash '$m02_src/$unified_gate/teardown.sh' m02"
  set -e

  ssh "$M02_HOST" "MONO='$m02_src' bash '$m02_src/$unified_gate/m02-up.sh'"

  CLEANUP_M01_SCRIPT="$m01_src/$unified_gate/teardown.sh"
  CLEANUP_M02_SCRIPT="$m02_src/$unified_gate/teardown.sh"
  trap cleanup_lab EXIT

  mkdir -p "$lmcache_run_dir"

  MONO="$m01_src" \
  LMCACHE_SOURCE="$lmcache_src" \
  VENV="$LMCACHE_VENV" \
  RUN="$lmcache_run_dir" \
  FILER_GRPC=192.168.1.184:19105 \
  BACKING=loader-rdma \
  SIZES="$LMCACHE_SIZES" \
  WORKLOAD_SECONDS="$LMCACHE_WORKLOAD_SECONDS" \
  WORKLOAD_SAVE_BATCH_SIZE="$LMCACHE_WORKLOAD_SAVE_BATCH_SIZE" \
  WORKLOAD_LOAD_BATCH_SIZE="$LMCACHE_WORKLOAD_LOAD_BATCH_SIZE" \
  WORKLOAD_SEED_SETTLE_SECS="$LMCACHE_WORKLOAD_SEED_SETTLE_SECS" \
  RUN_NIXL_RUNTIME="$LMCACHE_RUN_NIXL_RUNTIME" \
  REQUIRE_NIXL_RUNTIME="$LMCACHE_REQUIRE_NIXL_RUNTIME" \
  NIXL_RUNTIME_BACKEND="$LMCACHE_NIXL_RUNTIME_BACKEND" \
  NIXL_RUNTIME_SIZES="$LMCACHE_NIXL_RUNTIME_SIZES" \
    bash "$m01_src/$lmcache_gate/m01-run.sh"

  local min_bytes=0
  for item in $LMCACHE_SIZES; do
    min_bytes=$((min_bytes + item))
  done
  if [ "$LMCACHE_RUN_NIXL_RUNTIME" = "1" ]; then
    for item in $LMCACHE_NIXL_RUNTIME_SIZES; do
      min_bytes=$((min_bytes + item))
    done
  fi
  ssh "$M02_HOST" "MIN_COMMITTED_BYTES=$min_bytes bash '$m02_src/$unified_gate/m02-check.sh'"
  echo "LMCACHE_NIXL_RUNTIME_GATE_PASS"
}

write_provenance() {
  {
    echo "run_id=$run_id"
    echo "repo=$MONO_REPO"
    echo "ref=$MONO_REF"
    echo "profile=$PROFILE"
    echo "mono_sha=$(cat "$run_dir/mono.sha")"
    echo "m02=$M02_HOST"
    echo "rdma_pipes=$RDMA_PIPES"
    echo "enable_dc=$ENABLE_DC"
    if [ "$PROFILE" = "lmcache-nixl" ]; then
      echo "lmcache_repo=$LMCACHE_REPO"
      echo "lmcache_ref=$LMCACHE_REF"
      echo "lmcache_sha=$(cat "$run_dir/lmcache.sha")"
      echo "lmcache_sizes=$LMCACHE_SIZES"
      echo "lmcache_workload_seconds=$LMCACHE_WORKLOAD_SECONDS"
      echo "lmcache_workload_save_batch_size=$LMCACHE_WORKLOAD_SAVE_BATCH_SIZE"
      echo "lmcache_workload_load_batch_size=$LMCACHE_WORKLOAD_LOAD_BATCH_SIZE"
      echo "lmcache_workload_seed_settle_secs=$LMCACHE_WORKLOAD_SEED_SETTLE_SECS"
      echo "lmcache_run_nixl_runtime=$LMCACHE_RUN_NIXL_RUNTIME"
      echo "lmcache_require_nixl_runtime=$LMCACHE_REQUIRE_NIXL_RUNTIME"
      echo "lmcache_nixl_runtime_backend=$LMCACHE_NIXL_RUNTIME_BACKEND"
      echo "lmcache_nixl_runtime_sizes=$LMCACHE_NIXL_RUNTIME_SIZES"
      echo "nixl_pip_spec=$NIXL_PIP_SPEC"
    fi
  } | tee "$run_dir/provenance.txt"
}

write_summary() {
  local pass=0
  case "$PROFILE" in
    unified)
      if grep -q 'UNIFIED_RDMA_GATE_PASS' "$log"; then
        pass=1
      fi
      ;;
    lmcache-nixl)
      if grep -q 'LMCACHE_NIXL_RUNTIME_GATE_PASS' "$log" && grep -q 'SEAWEEDKV_LMCACHE_NIXL_RUNTIME_CPU ' "$log"; then
        pass=1
      fi
      ;;
  esac
  local loader_rows=0
  loader_rows="$(grep -c 'SW-RDMA-S3-LOADER-ROW' "$log" || true)"
  local lmcache_rows=0
  lmcache_rows="$(grep -c 'SEAWEEDKV_LMCACHE_CONNECTOR_GATE_PASS' "$log" || true)"
  local nixl_runtime_rows=0
  nixl_runtime_rows="$(grep -c 'SEAWEEDKV_LMCACHE_NIXL_RUNTIME_CPU ' "$log" || true)"
  {
    echo "RDMA_CI_RUN_ID=$run_id"
    echo "RDMA_CI_PROFILE=$PROFILE"
    echo "RDMA_CI_MONO_REF=$MONO_REF"
    echo "RDMA_CI_MONO_SHA=$(cat "$run_dir/mono.sha")"
    echo "RDMA_CI_PASS=$pass"
    echo "RDMA_CI_LOADER_ROWS=$loader_rows"
    echo "RDMA_CI_LMCACHE_ROWS=$lmcache_rows"
    echo "RDMA_CI_NIXL_RUNTIME_ROWS=$nixl_runtime_rows"
  } | tee "$run_dir/summary.env"
  {
    echo "<!doctype html><meta charset=\"utf-8\"><title>RDMA lab $run_id</title>"
    echo "<style>body{font-family:system-ui,Arial,sans-serif;margin:2rem;max-width:1100px}pre{background:#111;color:#eee;padding:1rem;overflow:auto}code{background:#eee;padding:.1rem .25rem}</style>"
    echo "<h1>RDMA lab $run_id</h1>"
    echo "<p><b>status:</b> $([ "$pass" = "1" ] && echo PASS || echo FAIL)</p>"
    echo "<p><b>mono:</b> <code>$MONO_REF</code> <code>$(cat "$run_dir/mono.sha")</code></p>"
    echo "<p><b>profile:</b> <code>$PROFILE</code>, <b>loader rows:</b> <code>$loader_rows</code>, <b>LMCache rows:</b> <code>$lmcache_rows</code>, <b>NIXL runtime rows:</b> <code>$nixl_runtime_rows</code></p>"
    echo "<h2>Pass markers</h2><pre>"
    grep -E 'UNIFIED_|SW-RDMA-S3-LOADER-ROW|SEAWEEDKV_LMCACHE_|LMCACHE_|RDMA_LAB_CI_PASS' "$log" | sed 's/&/\&amp;/g; s/</\&lt;/g; s/>/\&gt;/g' || true
    echo "</pre>"
    echo "<p>Full log: <a href=\"run.log\">run.log</a>. Provenance: <a href=\"provenance.txt\">provenance.txt</a>.</p>"
  } > "$run_dir/index.html"
  if [ "$pass" != "1" ]; then
    echo "RDMA lab gate did not emit pass marker" >&2
    exit 1
  fi
}

preflight
checkout_source
checkout_lmcache_source
sync_source_to_m02
ensure_lmcache_nixl_runtime
if [ "$SKIP_BUILD" = "1" ]; then
  echo "== skip build =="
else
  case "$PROFILE" in
    unified) build_unified_gate ;;
    lmcache-nixl) build_lmcache_nixl_gate ;;
    *) echo "unsupported profile: $PROFILE" >&2; exit 2 ;;
  esac
fi

case "$PROFILE" in
  unified) run_unified_gate ;;
  lmcache-nixl) run_lmcache_nixl_gate ;;
  *) echo "unsupported profile: $PROFILE" >&2; exit 2 ;;
esac

write_provenance
write_summary

echo "RDMA_LAB_CI_PASS"
