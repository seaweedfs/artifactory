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
CLEANUP_M01_SCRIPT=""
CLEANUP_M02_SCRIPT=""

usage() {
  cat <<'USAGE'
Usage: run-mono-rdma-lab.sh [options]

Options:
  --repo URL          seaweed-mono git URL
  --ref REF           branch, tag, or SHA to test
  --profile NAME      unified (default)
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
}

checkout_source() {
  echo "== checkout source on M01 =="
  mkdir -p "$M01_WORKDIR"
  local src="$M01_WORKDIR/seaweed-mono"
  if [ ! -d "$src/.git" ]; then
    rm -rf "$src"
    git clone "$MONO_REPO" "$src"
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
  } | tee "$run_dir/provenance.txt"
}

write_summary() {
  local pass=0
  if grep -q 'UNIFIED_RDMA_GATE_PASS' "$log"; then
    pass=1
  fi
  local loader_rows=0
  loader_rows="$(grep -c 'SW-RDMA-S3-LOADER-ROW' "$log" || true)"
  {
    echo "RDMA_CI_RUN_ID=$run_id"
    echo "RDMA_CI_PROFILE=$PROFILE"
    echo "RDMA_CI_MONO_REF=$MONO_REF"
    echo "RDMA_CI_MONO_SHA=$(cat "$run_dir/mono.sha")"
    echo "RDMA_CI_PASS=$pass"
    echo "RDMA_CI_LOADER_ROWS=$loader_rows"
  } | tee "$run_dir/summary.env"
  {
    echo "<!doctype html><meta charset=\"utf-8\"><title>RDMA lab $run_id</title>"
    echo "<style>body{font-family:system-ui,Arial,sans-serif;margin:2rem;max-width:1100px}pre{background:#111;color:#eee;padding:1rem;overflow:auto}code{background:#eee;padding:.1rem .25rem}</style>"
    echo "<h1>RDMA lab $run_id</h1>"
    echo "<p><b>status:</b> $([ "$pass" = "1" ] && echo PASS || echo FAIL)</p>"
    echo "<p><b>mono:</b> <code>$MONO_REF</code> <code>$(cat "$run_dir/mono.sha")</code></p>"
    echo "<p><b>profile:</b> <code>$PROFILE</code>, <b>loader rows:</b> <code>$loader_rows</code></p>"
    echo "<h2>Pass markers</h2><pre>"
    grep -E 'UNIFIED_|SW-RDMA-S3-LOADER-ROW|RDMA_LAB_CI_PASS' "$log" | sed 's/&/\&amp;/g; s/</\&lt;/g; s/>/\&gt;/g' || true
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
sync_source_to_m02
if [ "$SKIP_BUILD" = "1" ]; then
  echo "== skip build =="
else
  build_unified_gate
fi

case "$PROFILE" in
  unified) run_unified_gate ;;
  *) echo "unsupported profile: $PROFILE" >&2; exit 2 ;;
esac

write_provenance
write_summary

echo "RDMA_LAB_CI_PASS"
