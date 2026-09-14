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
GO_WEED_BIN=""
GO_WEED_SHA256=""
KMOD_BIN=""
KMOD_SHA256=""
KMOD_UNAME_R=""
KMOD_VERMAGIC=""
VFS_KERNEL_EXCLUDED="0"
VFS_KERNEL_EXCLUSION_REASON=""
GO_VERSION=""
GO_MOD_SHA256_BEFORE=""
GO_MOD_SHA256_AFTER=""
GO_SUM_SHA256_BEFORE=""
GO_SUM_SHA256_AFTER=""
RUST_CARGO_LOCK_SHA256_BEFORE=""
RUST_CARGO_LOCK_SHA256_AFTER=""
RUST_CARGO_LOCK_STATUS_BEFORE=""
RUST_CARGO_LOCK_STATUS_AFTER=""
RUST_CARGO_LOCK_DIFF_SHA256=""
M02_RUN_DIR="/tmp/unified-rdma-gate-m02-run"
M02_EVIDENCE_COLLECTED="0"
M02_VOLBIN=""
M02_SSH_TIMEOUT_SECS="${M02_SSH_TIMEOUT_SECS:-30}"
M02_GDB_TIMEOUT_SECS="${M02_GDB_TIMEOUT_SECS:-20}"

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

dc_ack_timeout_seen() {
  grep -q "rdma tcp read dc registration ack failed" "$log" 2>/dev/null
}

m02_ssh() {
  timeout "${M02_SSH_TIMEOUT_SECS}s" ssh \
    -o BatchMode=yes \
    -o ConnectTimeout=5 \
    -o ServerAliveInterval=5 \
    -o ServerAliveCountMax=2 \
    "$M02_HOST" "$@"
}

record_m02_volume_identity() {
  [ -n "$M02_VOLBIN" ] || return 0
  if ! m02_ssh "RUN='$M02_RUN_DIR' VOLBIN='$M02_VOLBIN' bash -s" <<'REMOTE'
set +e
pid=$(cat "$RUN/volume.pid" 2>/dev/null) || exit 0
case "$pid" in ''|*[!0-9]*) exit 0 ;; esac
actual_exe=$(readlink -f "/proc/$pid/exe" 2>/dev/null)
expected_exe=$(readlink -f "$VOLBIN" 2>/dev/null)
uid=$(awk '/^Uid:/ {print $2}' "/proc/$pid/status" 2>/dev/null)
stat=$(cat "/proc/$pid/stat" 2>/dev/null)
rest=${stat#*) }
set -- $rest
starttime=${20:-}
{
  echo "pid=$pid"
  echo "exe=$actual_exe"
  echo "expected_exe=$expected_exe"
  echo "uid=$uid"
  echo "starttime=$starttime"
  date -u '+recorded_at=%Y-%m-%dT%H:%M:%SZ'
} > "$RUN/volume.identity"
REMOTE
  then
    echo "M02 volume identity capture failed"
    return 0
  fi
}

collect_m02_evidence() {
  [ "$M02_EVIDENCE_COLLECTED" = "0" ] || return 0
  M02_EVIDENCE_COLLECTED=1
  local out="$run_dir/m02-evidence"
  mkdir -p "$out"
  echo "== collect M02 evidence =="
  if m02_ssh "test -d '$M02_RUN_DIR/logs'"; then
    mkdir -p "$out/run"
    if m02_ssh "tar -C '$M02_RUN_DIR' -cf - logs" > "$out/run-logs.tar"; then
      tar -C "$out/run" -xf "$out/run-logs.tar" 2>/dev/null || true
      find "$out/run/logs" -type f -maxdepth 1 -print0 | xargs -0r sha256sum > "$out/run-logs.sha256"
    else
      echo "failed to capture $M02_RUN_DIR/logs" > "$out/run-logs.capture_failed"
    fi
  else
    echo "missing $M02_RUN_DIR/logs" > "$out/run-logs.missing"
  fi
  if dc_ack_timeout_seen; then
    if ! m02_ssh "RUN='$M02_RUN_DIR' VOLBIN='$M02_VOLBIN' GDB_TIMEOUT='$M02_GDB_TIMEOUT_SECS' bash -s" > "$out/weed-volume-thread-state.tar" <<'REMOTE'
set +e
out=$(mktemp -d /tmp/rdma-dc-thread-state.XXXXXX) || exit 0
pidfile="$RUN/volume.pid"
echo "run=$RUN" > "$out/meta.txt"
echo "volbin=$VOLBIN" >> "$out/meta.txt"
ref="$RUN/volume.identity"
ref_starttime=$(awk -F= '$1=="starttime"{print $2}' "$ref" 2>/dev/null)
ref_uid=$(awk -F= '$1=="uid"{print $2}' "$ref" 2>/dev/null)
ref_exe=$(awk -F= '$1=="exe"{print $2}' "$ref" 2>/dev/null)
expected_exe=$(readlink -f "$VOLBIN" 2>>"$out/identity.err")
current_uid=$(id -u)
refuse() {
  echo "$1" > "$out/capture_refused"
  tar -C "$out" -cf - .
  rm -rf "$out"
  exit 0
}
if [ -f "$pidfile" ]; then
  pid=$(cat "$pidfile")
  echo "pid=$pid" >> "$out/meta.txt"
  case "$pid" in ''|*[!0-9]*) refuse "invalid pidfile pid=$pid" ;; esac
  ps -o pid=,ppid=,user=,lstart=,args= -p "$pid" > "$out/ps.txt" 2>&1
  if kill -0 "$pid" 2>/dev/null; then
    actual_exe=$(readlink -f "/proc/$pid/exe" 2>>"$out/identity.err")
    uid=$(awk '/^Uid:/ {print $2}' "/proc/$pid/status" 2>>"$out/identity.err")
    stat=$(cat "/proc/$pid/stat" 2>>"$out/identity.err")
    rest=${stat#*) }
    set -- $rest
    starttime=${20:-}
    {
      echo "expected_exe=$expected_exe"
      echo "actual_exe=$actual_exe"
      echo "uid=$uid"
      echo "current_uid=$current_uid"
      echo "starttime=$starttime"
      echo "ref_exe=$ref_exe"
      echo "ref_uid=$ref_uid"
      echo "ref_starttime=$ref_starttime"
    } > "$out/identity.txt"
    [ -n "$expected_exe" ] && [ "$actual_exe" = "$expected_exe" ] || refuse "executable mismatch"
    [ -n "$ref_exe" ] && [ "$actual_exe" = "$ref_exe" ] || refuse "recorded executable mismatch"
    [ -n "$uid" ] && [ "$uid" = "$current_uid" ] || refuse "uid mismatch"
    [ -n "$ref_uid" ] && [ "$uid" = "$ref_uid" ] || refuse "recorded uid mismatch"
    [ -n "$ref_starttime" ] && [ "$starttime" = "$ref_starttime" ] || refuse "starttime mismatch"
    mkdir -p "$out/tasks"
    for task in /proc/$pid/task/*; do
      tid=${task##*/}
      mkdir -p "$out/tasks/$tid"
      for f in comm wchan stat; do cat "$task/$f" > "$out/tasks/$tid/$f" 2>&1; done
    done
    if command -v gdb >/dev/null 2>&1 && command -v timeout >/dev/null 2>&1; then
      timeout "${GDB_TIMEOUT:-20}s" gdb -batch -p "$pid" -ex "thread apply all bt" > "$out/gdb-thread-bt.txt" 2>&1
      echo $? > "$out/gdb.exit"
    elif ! command -v gdb >/dev/null 2>&1; then
      echo gdb_not_found > "$out/gdb.unavailable"
    else
      echo timeout_not_found > "$out/gdb.unavailable"
    fi
  else
    echo volume_pid_not_running > "$out/not-running.txt"
  fi
else
  echo volume_pidfile_missing > "$out/pidfile.missing"
fi
tar -C "$out" -cf - .
rm -rf "$out"
REMOTE
    then
      echo "thread-state capture ssh timeout/failure" > "$out/weed-volume-thread-state.capture_failed"
    fi
    mkdir -p "$out/weed-volume-thread-state"
    tar -C "$out/weed-volume-thread-state" -xf "$out/weed-volume-thread-state.tar" 2>/dev/null || true
    find "$out/weed-volume-thread-state" -type f -print0 | xargs -0r sha256sum > "$out/weed-volume-thread-state.sha256"
  else
    echo "dc_ack_timeout_not_seen" > "$out/weed-volume-thread-state.skipped"
  fi
}

cleanup_lab() {
  set +e
  collect_m02_evidence
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

apply_rc_not_found_diagnostics() {
  local src="$M01_WORKDIR/seaweed-mono"
  echo "== apply RC NOT_FOUND diagnostic overlay =="
  python3 - "$src" <<'PY'
from pathlib import Path
import sys
root=Path(sys.argv[1])
handlers=root/'enterprise/seaweed-volume/src/rdma/handlers.rs'
gate=root/'enterprise/rust/sw-rdma-loader/tests/lab/unified-rdma-gate/m01-unified.sh'
s=handlers.read_text()
s=s.replace('''warn!(
                "RDMA push-read volume not mounted request_id={request_id} fid={fid} volume_id={}",
                file_id.volume_id.0
            );''','''warn!(
                "RDMA RC diagnostic NOT_FOUND branch=find_volume_miss request_id={request_id} fid={fid} volume_id={} key={} cookie={}",
                file_id.volume_id.0,
                file_id.key.0,
                file_id.cookie.0
            );''')
s=s.replace('''warn!("RDMA push-read needle not found request_id={request_id} fid={fid}");''','''warn!(
                    "RDMA RC diagnostic NOT_FOUND branch=needle_lookup_not_found request_id={request_id} fid={fid} volume_id={} key={} cookie={}",
                    file_id.volume_id.0,
                    file_id.key.0,
                    file_id.cookie.0
                );''')
handlers.write_text(s)
g=gate.read_text()
g=g.replace('  echo "UNIFIED_OBJECT_PUT_OK path=$path input=$input"', '  echo "UNIFIED_OBJECT_PUT_OK path=$path input=$input ts=$(date -u +%Y-%m-%dT%H:%M:%S.%NZ)"')
old='''object_bench() {
  local path=$1
  local object_mib=$2
  local requests=$3
  timeout 240 "$BENCHBIN" \\
    --rdma-addr "$RDMA" \\
    --control-addr "$CTRL" \\
    --filer "$FILER" \\
    --path "$path" \\
    --object-mib "$object_mib" \\
    --chunk-mib 4 \\
    --requests "$requests" \\
    --concurrency 32 \\
    --pipes "$RDMA_PIPES" \\
    --verify-pattern || { echo "UNIFIED_OBJECT_BENCH_TIMEOUT path=$path"; exit 1; }
}
'''
new='''object_bench() {
  local path=$1 object_mib=$2 requests=$3 out rc diag_out diag_rc
  out="$WORK/object-bench-first.log"
  echo "UNIFIED_OBJECT_BENCH_ATTEMPT_START path=$path ts=$(date -u +%Y-%m-%dT%H:%M:%S.%NZ)"
  set +e
  timeout 240 "$BENCHBIN" --rdma-addr "$RDMA" --control-addr "$CTRL" --filer "$FILER" --path "$path" --object-mib "$object_mib" --chunk-mib 4 --requests "$requests" --concurrency 32 --pipes "$RDMA_PIPES" --verify-pattern 2>&1 | tee "$out"
  rc=${PIPESTATUS[0]}
  set -e
  [ "$rc" = "0" ] && return 0
  if grep -q 'push-read response status 1' "$out"; then
    echo "UNIFIED_OBJECT_BENCH_FIRST_NOT_FOUND path=$path ts=$(date -u +%Y-%m-%dT%H:%M:%S.%NZ) log=$out"
    sleep 2
    diag_out="$WORK/object-bench-diagnostic-reread.log"
    echo "UNIFIED_OBJECT_BENCH_DIAGNOSTIC_REREAD_START path=$path ts=$(date -u +%Y-%m-%dT%H:%M:%S.%NZ)"
    set +e
    timeout 240 "$BENCHBIN" --rdma-addr "$RDMA" --control-addr "$CTRL" --filer "$FILER" --path "$path" --object-mib "$object_mib" --chunk-mib 4 --requests 1 --concurrency 1 --pipes "$RDMA_PIPES" --verify-pattern 2>&1 | tee "$diag_out"
    diag_rc=${PIPESTATUS[0]}
    set -e
    echo "UNIFIED_OBJECT_BENCH_DIAGNOSTIC_REREAD_EXIT path=$path exit=$diag_rc ts=$(date -u +%Y-%m-%dT%H:%M:%S.%NZ) log=$diag_out"
  fi
  echo "UNIFIED_OBJECT_BENCH_TIMEOUT path=$path exit=$rc"
  exit 1
}
'''
if old not in g:
    raise SystemExit('object_bench hunk not found')
gate.write_text(g.replace(old,new))
PY
  {
    git -C "$src" diff -- enterprise/seaweed-volume/src/rdma/handlers.rs enterprise/rust/sw-rdma-loader/tests/lab/unified-rdma-gate/m01-unified.sh
    sha256sum "$src/enterprise/seaweed-volume/src/rdma/handlers.rs" "$src/enterprise/rust/sw-rdma-loader/tests/lab/unified-rdma-gate/m01-unified.sh"
  } > "$run_dir/rc-not-found-diagnostic-overlay.txt"
  sha256sum "$run_dir/rc-not-found-diagnostic-overlay.txt"
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

  RUST_CARGO_LOCK_SHA256_BEFORE="$(sha256sum "$m01_src/enterprise/rust/Cargo.lock" | awk '{print $1}')"
  RUST_CARGO_LOCK_STATUS_BEFORE="$(git -C "$m01_src" status --short -- enterprise/rust/Cargo.lock | paste -sd ';' -)"
  GO_VERSION="$(ssh "$M02_HOST" "go version")"
  GO_MOD_SHA256_BEFORE="$(ssh "$M02_HOST" "sha256sum '$m02_src/enterprise/go.mod'" | awk '{print $1}')"
  GO_SUM_SHA256_BEFORE="$(ssh "$M02_HOST" "sha256sum '$m02_src/enterprise/go.sum'" | awk '{print $1}')"

  bash -lc "source ~/.cargo/env 2>/dev/null || true; cd '$m01_src/enterprise/rust' && cargo build --release -p seaweedfs-sw-rdma-object --features '$object_features' --bin sw-rdma-object-put --bin sw-rdma-object-get --bin sw-rdma-s3-loader && cargo build --release -p seaweedkv-tools --features '$object_features' --bin sw-rdma-object-bench --bin seaweedfs-sw-rdma-kvcache && cargo build --release -p seaweedfs-sw-rdma-vfs --features daemon --bin sw-rdma-kd"
  bash -lc "source ~/.cargo/env 2>/dev/null || true; cd '$m01_src/seaweed-vfs' && cargo build --release -p sw-kd --bin sw-kd"
  KMOD_UNAME_R="$(uname -r)"
  if bash -lc "cd '$m01_src/seaweed-vfs/kernel' && make"; then
    KMOD_BIN="$m01_src/seaweed-vfs/kernel/seaweedvfs.ko"
    test -f "$KMOD_BIN" || { echo "kernel module build did not produce $KMOD_BIN" >&2; exit 1; }
    KMOD_SHA256="$(sha256sum "$KMOD_BIN" | awk '{print $1}')"
    KMOD_VERMAGIC="$(modinfo -F vermagic "$KMOD_BIN")"
  else
    VFS_KERNEL_EXCLUDED="1"
    VFS_KERNEL_EXCLUSION_REASON="seaweedvfs.ko_build_failed_for_${KMOD_UNAME_R}"
  fi
  ssh "$M02_HOST" "bash -lc 'source ~/.cargo/env 2>/dev/null || true; cd \"$m02_src/enterprise\" && go build -o weed-rdma ./weed && cd \"$m02_src/enterprise/seaweed-volume\" && cargo build --release --features \"$volume_features\"'"
  GO_WEED_BIN="$m02_src/enterprise/weed-rdma"
  GO_WEED_SHA256="$(ssh "$M02_HOST" "sha256sum '$m02_src/enterprise/weed-rdma'" | awk '{print $1}')"
  GO_MOD_SHA256_AFTER="$(ssh "$M02_HOST" "sha256sum '$m02_src/enterprise/go.mod'" | awk '{print $1}')"
  GO_SUM_SHA256_AFTER="$(ssh "$M02_HOST" "sha256sum '$m02_src/enterprise/go.sum'" | awk '{print $1}')"
  RUST_CARGO_LOCK_SHA256_AFTER="$(sha256sum "$m01_src/enterprise/rust/Cargo.lock" | awk '{print $1}')"
  RUST_CARGO_LOCK_STATUS_AFTER="$(git -C "$m01_src" status --short -- enterprise/rust/Cargo.lock | paste -sd ';' -)"
  git -C "$m01_src" diff -- enterprise/rust/Cargo.lock > "$run_dir/enterprise-rust-Cargo.lock.diff" || true
  RUST_CARGO_LOCK_DIFF_SHA256="$(sha256sum "$run_dir/enterprise-rust-Cargo.lock.diff" | awk '{print $1}')"
  test -n "$GO_WEED_SHA256" || { echo "missing Go weed hash" >&2; exit 1; }
  if [ "$VFS_KERNEL_EXCLUDED" != "1" ]; then
    test -n "$KMOD_SHA256" || { echo "missing kernel module hash" >&2; exit 1; }
  fi
  echo "GO_WEED_BIN=$GO_WEED_BIN"
  echo "GO_WEED_SHA256=$GO_WEED_SHA256"
  echo "KMOD_BIN=$KMOD_BIN"
  echo "KMOD_SHA256=$KMOD_SHA256"
  echo "KMOD_UNAME_R=$KMOD_UNAME_R"
  echo "KMOD_VERMAGIC=$KMOD_VERMAGIC"
  echo "VFS_KERNEL_EXCLUDED=$VFS_KERNEL_EXCLUDED"
  echo "VFS_KERNEL_EXCLUSION_REASON=$VFS_KERNEL_EXCLUSION_REASON"
  echo "GO_VERSION=$GO_VERSION"
  echo "GO_MOD_SHA256_BEFORE=$GO_MOD_SHA256_BEFORE"
  echo "GO_MOD_SHA256_AFTER=$GO_MOD_SHA256_AFTER"
  echo "GO_SUM_SHA256_BEFORE=$GO_SUM_SHA256_BEFORE"
  echo "GO_SUM_SHA256_AFTER=$GO_SUM_SHA256_AFTER"
  echo "RUST_CARGO_LOCK_SHA256_BEFORE=$RUST_CARGO_LOCK_SHA256_BEFORE"
  echo "RUST_CARGO_LOCK_SHA256_AFTER=$RUST_CARGO_LOCK_SHA256_AFTER"
  echo "RUST_CARGO_LOCK_DIFF_SHA256=$RUST_CARGO_LOCK_DIFF_SHA256"
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

  CLEANUP_M01_SCRIPT="$m01_src/$gate/teardown.sh"
  CLEANUP_M02_SCRIPT="$m02_src/$gate/teardown.sh"
  trap cleanup_lab EXIT

  local dc_env=""
  if [ "$ENABLE_DC" = "1" ]; then
    dc_env="ENABLE_DC=1 SWFS_RDMA_DC_INITIATORS=$DC_INITIATORS"
  fi

  test -n "$GO_WEED_BIN" || { echo "GO_WEED_BIN missing; build_unified_gate must run before startup" >&2; exit 1; }
  M02_VOLBIN="$m02_src/enterprise/seaweed-volume/target/release/weed-volume"
  ssh "$M02_HOST" "$dc_env RUN='$M02_RUN_DIR' MONO='$m02_src' WEED='$GO_WEED_BIN' VOLBIN='$M02_VOLBIN' bash '$m02_src/$gate/m02-up.sh'"
  record_m02_volume_identity

  local dc_m01=""
  if [ "$ENABLE_DC" = "1" ]; then
    dc_m01="ENABLE_DC=1"
  fi
  local vfs_m01="SKIP_VFS=1 KMOD=$m01_src/seaweed-vfs/kernel/seaweedvfs.ko"
  echo "UNIFIED_RC_DIAGNOSTIC_SKIP_VFS=1"

  RDMA_PIPES="$RDMA_PIPES" MONO="$m01_src" bash -c "$dc_m01 $vfs_m01 bash '$m01_src/$gate/m01-unified.sh'"
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
    echo "rc_not_found_diagnostic=1"
    echo "rc_not_found_diagnostic_overlay=rc-not-found-diagnostic-overlay.txt"
    echo "rc_not_found_diagnostic_overlay_sha256=$(sha256sum "$run_dir/rc-not-found-diagnostic-overlay.txt" | awk '{print $1}')"
    echo "go_weed_bin=$GO_WEED_BIN"
    echo "go_weed_sha256=$GO_WEED_SHA256"
    echo "go_version=$GO_VERSION"
    echo "kmod_bin=$KMOD_BIN"
    echo "kmod_sha256=$KMOD_SHA256"
    echo "kmod_uname_r=$KMOD_UNAME_R"
    echo "kmod_vermagic=$KMOD_VERMAGIC"
    echo "vfs_kernel_excluded=$VFS_KERNEL_EXCLUDED"
    echo "vfs_kernel_exclusion_reason=$VFS_KERNEL_EXCLUSION_REASON"
    echo "go_mod_sha256_before=$GO_MOD_SHA256_BEFORE"
    echo "go_mod_sha256_after=$GO_MOD_SHA256_AFTER"
    echo "go_sum_sha256_before=$GO_SUM_SHA256_BEFORE"
    echo "go_sum_sha256_after=$GO_SUM_SHA256_AFTER"
    echo "rust_cargo_lock_sha256_before=$RUST_CARGO_LOCK_SHA256_BEFORE"
    echo "rust_cargo_lock_sha256_after=$RUST_CARGO_LOCK_SHA256_AFTER"
    echo "rust_cargo_lock_status_before=$RUST_CARGO_LOCK_STATUS_BEFORE"
    echo "rust_cargo_lock_status_after=$RUST_CARGO_LOCK_STATUS_AFTER"
    echo "rust_cargo_lock_diff=enterprise-rust-Cargo.lock.diff"
    echo "rust_cargo_lock_diff_sha256=$RUST_CARGO_LOCK_DIFF_SHA256"
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
    echo "RDMA_CI_RC_NOT_FOUND_DIAGNOSTIC=1"
    echo "RDMA_CI_RC_NOT_FOUND_DIAGNOSTIC_OVERLAY_SHA256=$(sha256sum "$run_dir/rc-not-found-diagnostic-overlay.txt" | awk '{print $1}')"
    echo "RDMA_CI_GO_WEED_SHA256=$GO_WEED_SHA256"
    echo "RDMA_CI_GO_VERSION=$GO_VERSION"
    echo "RDMA_CI_KMOD_SHA256=$KMOD_SHA256"
    echo "RDMA_CI_KMOD_UNAME_R=$KMOD_UNAME_R"
    echo "RDMA_CI_KMOD_VERMAGIC=$KMOD_VERMAGIC"
    echo "RDMA_CI_VFS_KERNEL_EXCLUDED=$VFS_KERNEL_EXCLUDED"
    echo "RDMA_CI_VFS_KERNEL_EXCLUSION_REASON=$VFS_KERNEL_EXCLUSION_REASON"
    echo "RDMA_CI_GO_MOD_SHA256_BEFORE=$GO_MOD_SHA256_BEFORE"
    echo "RDMA_CI_GO_MOD_SHA256_AFTER=$GO_MOD_SHA256_AFTER"
    echo "RDMA_CI_GO_SUM_SHA256_BEFORE=$GO_SUM_SHA256_BEFORE"
    echo "RDMA_CI_GO_SUM_SHA256_AFTER=$GO_SUM_SHA256_AFTER"
    echo "RDMA_CI_RUST_CARGO_LOCK_SHA256_BEFORE=$RUST_CARGO_LOCK_SHA256_BEFORE"
    echo "RDMA_CI_RUST_CARGO_LOCK_SHA256_AFTER=$RUST_CARGO_LOCK_SHA256_AFTER"
    echo "RDMA_CI_RUST_CARGO_LOCK_DIFF_SHA256=$RUST_CARGO_LOCK_DIFF_SHA256"
    echo "RDMA_CI_M02_EVIDENCE_DIR=m02-evidence"
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
apply_rc_not_found_diagnostics
git -C "$M01_WORKDIR/seaweed-mono" status --short > "$run_dir/mono.status"
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
