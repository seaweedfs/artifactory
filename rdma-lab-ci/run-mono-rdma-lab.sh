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
SKIP_VFS="${SKIP_VFS:-0}"
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
RDMA_CI_SHARE_BUNDLE_DIR="${RDMA_CI_SHARE_BUNDLE_DIR:-${TESTOPS_RESULT_DIR:-${RESULTS_DIR:-}}}"
RDMA_CI_SHARE_RUN_DIR_NAME="${RDMA_CI_SHARE_RUN_DIR_NAME:-rdma-lab-run-dir}"
RDMA_CI_SHARE_BUNDLE_EXPORTED="0"

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
  if m02_ssh "test -d '$M02_RUN_DIR'"; then
    mkdir -p "$out/run"
    if m02_ssh "tar -C '$M02_RUN_DIR' -cf - logs master.pid filer.pid volume.pid volume.identity 2>/dev/null" > "$out/run-state.tar"; then
      tar -C "$out/run" -xf "$out/run-state.tar" 2>/dev/null || true
      find "$out/run" -type f -print0 | xargs -0r sha256sum > "$out/run-state.sha256"
    else
      echo "failed to capture $M02_RUN_DIR run logs/pids" > "$out/run-state.capture_failed"
    fi
  else
    echo "missing $M02_RUN_DIR" > "$out/run-state.missing"
  fi
  if [ -n "$M02_VOLBIN" ]; then
    m02_ssh "sha256sum '$M02_VOLBIN' 2>&1" > "$out/volume-binary.sha256"       || echo "volume binary hash capture failed" > "$out/volume-binary.capture_failed"
  fi
  if ! m02_ssh "RUN='$M02_RUN_DIR' VOLBIN='$M02_VOLBIN' bash -s" > "$out/m02-runtime-snapshot.tar" <<'REMOTE'
set +e
out=$(mktemp -d /tmp/rdma-m02-runtime-snapshot.XXXXXX) || exit 0
pid=$(cat "$RUN/volume.pid" 2>/dev/null)
echo "run=$RUN" > "$out/meta.txt"
echo "volbin=$VOLBIN" >> "$out/meta.txt"
echo "pid=${pid:-}" >> "$out/meta.txt"
if [ -n "${pid:-}" ]; then
  ps -fp "$pid" > "$out/volume.ps" 2>&1
  for f in stat wchan cmdline; do
    cat "/proc/$pid/$f" > "$out/volume.proc.$f" 2>&1
  done
fi
ss -tanp > "$out/ss-tanp.txt" 2>&1
ss -ltnp > "$out/ss-ltnp.txt" 2>&1
for port in 7532 7534 7535 8105 18105 9106; do
  (ss -tanp 2>&1 | grep ":$port" || true) > "$out/ss-port-$port.txt"
done
if command -v rdma >/dev/null 2>&1; then
  rdma link > "$out/rdma-link.txt" 2>&1
  rdma res show qp > "$out/rdma-res-qp.txt" 2>&1
  rdma res show cq > "$out/rdma-res-cq.txt" 2>&1
  rdma res show mr > "$out/rdma-res-mr.txt" 2>&1
else
  echo rdma_not_found > "$out/rdma.unavailable"
fi
if command -v ibv_devinfo >/dev/null 2>&1; then
  ibv_devinfo > "$out/ibv-devinfo.txt" 2>&1
else
  echo ibv_devinfo_not_found > "$out/ibv-devinfo.unavailable"
fi
tar -C "$out" -cf - .
rm -rf "$out"
REMOTE
  then
    echo "runtime snapshot capture ssh timeout/failure" > "$out/m02-runtime-snapshot.capture_failed"
  fi
  mkdir -p "$out/m02-runtime-snapshot"
  tar -C "$out/m02-runtime-snapshot" -xf "$out/m02-runtime-snapshot.tar" 2>/dev/null || true
  find "$out/m02-runtime-snapshot" -type f -print0 | xargs -0r sha256sum > "$out/m02-runtime-snapshot.sha256"
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

export_share_bundle() {
  [ "$RDMA_CI_SHARE_BUNDLE_EXPORTED" = "0" ] || return 0
  RDMA_CI_SHARE_BUNDLE_EXPORTED=1
  if [ -z "$RDMA_CI_SHARE_BUNDLE_DIR" ]; then
    echo "RDMA_CI_SHARE_BUNDLE_EXPORT_SKIPPED reason=no_destination" > "$run_dir/share-bundle-export.skipped"
    return 0
  fi
  local dest="$RDMA_CI_SHARE_BUNDLE_DIR/$RDMA_CI_SHARE_RUN_DIR_NAME"
  mkdir -p "$dest" 2>"$run_dir/share-bundle-export.err" || {
    echo "RDMA_CI_SHARE_BUNDLE_EXPORT_FAIL mkdir dest=$dest" > "$run_dir/share-bundle-export.failed"
    return 0
  }
  case "$(readlink -f "$dest" 2>/dev/null)" in
    "$(readlink -f "$run_dir" 2>/dev/null)"|"$(readlink -f "$run_dir" 2>/dev/null)"/*)
      echo "RDMA_CI_SHARE_BUNDLE_EXPORT_SKIPPED reason=destination_inside_run_dir dest=$dest" > "$run_dir/share-bundle-export.skipped"
      return 0
      ;;
  esac
  if rsync -r --inplace --no-times --omit-dir-times --no-perms --no-owner --no-group --exclude "$RDMA_CI_SHARE_RUN_DIR_NAME" "$run_dir/" "$dest/" >>"$run_dir/share-bundle-export.log" 2>>"$run_dir/share-bundle-export.err"; then
    {
      echo "RDMA_CI_SHARE_BUNDLE_EXPORT_OK dest=$dest"
      date -u '+exported_at=%Y-%m-%dT%H:%M:%SZ'
      find "$dest" -type f -print0 | xargs -0r sha256sum
    } > "$run_dir/share-bundle-export.sha256"
    cp "$run_dir/share-bundle-export.sha256" "$dest/share-bundle-export.sha256" 2>/dev/null || true
  else
    echo "RDMA_CI_SHARE_BUNDLE_EXPORT_FAIL dest=$dest" > "$run_dir/share-bundle-export.failed"
  fi
}

cleanup_lab() {
  set +e
  collect_m02_evidence
  export_share_bundle
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
  echo "== prepare RC NOT_FOUND runner-only diagnostic =="
  cat > "$run_dir/object-bench-diagnostic-wrapper.sh" <<'DIAG'
#!/bin/bash
set -euo pipefail
out="${DIAG_DIR:?}/object-bench-first.log"
status_file="${DIAG_DIR:?}/object-bench-status"
args=("$@")
path=""; object_mib=""; chunk_mib="4"
while [ "$#" -gt 0 ]; do
  case "$1" in
    --path) path=$2; shift 2 ;;
    --object-mib) object_mib=$2; shift 2 ;;
    --chunk-mib) chunk_mib=$2; shift 2 ;;
    *) shift ;;
  esac
done
mkdir -p "$DIAG_DIR"
rm -f "$out" "$status_file"
run_diagnostics() {
  set +e
  local first_line="$1" fail_ms fid vid fail_off fail_len read_mib now_ms elapsed_ms
  fail_ms=$(printf '%s\n' "$first_line" | sed -n 's/^diag_epoch_ms=\([0-9]*\).*/\1/p')
  fid=$(printf '%s\n' "$first_line" | sed -n 's/.* fid=\([^ ]*\) .*/\1/p')
  fail_off=$(printf '%s\n' "$first_line" | sed -n 's/.* offset=\([0-9]*\) .*/\1/p')
  fail_len=$(printf '%s\n' "$first_line" | sed -n 's/.* length=\([0-9][0-9]*\)\([[:space:]].*\)\{0,1\}$/\1/p')
  vid=${fid%%,*}
  echo "UNIFIED_OBJECT_BENCH_FIRST_NOT_FOUND path=$path fid=$fid offset=${fail_off:-unknown} length=${fail_len:-unknown} line=$first_line"
  if [ -z "$fid" ] || [ -z "$vid" ] || [ "$vid" = "$fid" ]; then
    echo "UNIFIED_OBJECT_BENCH_DIAGNOSTIC_NO_FID line=$first_line"
    return 0
  fi
  local http_out="$DIAG_DIR/object-bench-diagnostic-http-body.bin" http_err="$DIAG_DIR/object-bench-diagnostic-http.err"
  : > "$http_out"
  local http_code
  http_code=$(timeout 10 curl -sS -o "$http_out" -w '%{http_code}' "http://$MASTER_IP:${VOL_HTTP:-8105}/$fid" 2>"$http_err")
  local http_rc=$? http_len http_sha
  http_len=$(wc -c < "$http_out" 2>/dev/null | tr -d ' '); http_len=${http_len:-0}
  http_sha=$(sha256sum "$http_out" 2>/dev/null | awk '{print $1}'); http_sha=${http_sha:-missing}
  now_ms=$(date +%s%3N); elapsed_ms=$((now_ms - fail_ms))
  echo "UNIFIED_OBJECT_BENCH_DIAG_HTTP fid=$fid status=${http_code:-NA} curl_exit=$http_rc bytes=$http_len sha256=$http_sha seed_compare=UNBOUND:no_proven_seed_chunk_range elapsed_ms=$elapsed_ms"
  local lookup_out="$DIAG_DIR/object-bench-diagnostic-volume-lookup.json" lookup_err="$DIAG_DIR/object-bench-diagnostic-volume-lookup.err"
  : > "$lookup_out"
  timeout 10 curl -sS "http://$MASTER_IP:9755/dir/lookup?volumeId=$vid" -o "$lookup_out" 2>"$lookup_err"
  local lookup_rc=$? lookup_sha
  lookup_sha=$(sha256sum "$lookup_out" 2>/dev/null | awk '{print $1}'); lookup_sha=${lookup_sha:-missing}
  now_ms=$(date +%s%3N); elapsed_ms=$((now_ms - fail_ms))
  echo "UNIFIED_OBJECT_BENCH_DIAG_LOOKUP fid=$fid volume_id=$vid curl_exit=$lookup_rc elapsed_ms=$elapsed_ms log=$lookup_out sha256=$lookup_sha"
  local reread_out="$DIAG_DIR/object-bench-diagnostic-rdma-reread.log"
  if [ "${fail_off:-}" = "0" ] && [ -n "${fail_len:-}" ] && [ $((fail_len % 1048576)) -eq 0 ]; then
    read_mib=$((fail_len / 1048576))
    timeout 30 "$PUSHBENCH" "$RDMA" "$CTRL" "$fid" "$read_mib" "$read_mib" 1 1 "$RDMA_PIPES" 1 2>&1 | tee "$reread_out"
    local reread_rc=${PIPESTATUS[0]}
    now_ms=$(date +%s%3N); elapsed_ms=$((now_ms - fail_ms))
    echo "UNIFIED_OBJECT_BENCH_DIAG_RDMA_REREAD fid=$fid bytes=$fail_len exit=$reread_rc elapsed_ms=$elapsed_ms log=$reread_out"
  else
    now_ms=$(date +%s%3N); elapsed_ms=$((now_ms - fail_ms))
    echo "UNIFIED_OBJECT_BENCH_DIAG_RDMA_REREAD_SKIPPED fid=$fid offset=${fail_off:-unknown} length=${fail_len:-unknown} reason=unsupported_failed_range elapsed_ms=$elapsed_ms"
  fi
  return 0
}
diag_pid=""
{
  set +e
  "$REAL_BENCHBIN" "${args[@]}"
  echo "$?" > "$status_file"
} 2>&1 | {
  while IFS= read -r line; do
    ms=$(date +%s%3N); ts=$(date -u +%Y-%m-%dT%H:%M:%S.%NZ)
    tagged="diag_epoch_ms=$ms diag_ts=$ts $line"
    printf '%s\n' "$tagged" | tee -a "$out"
    if [ -z "$diag_pid" ] && printf '%s\n' "$line" | grep -q 'push-read response status 1'; then
      run_diagnostics "$tagged" & diag_pid=$!
    fi
  done
  [ -n "$diag_pid" ] && wait "$diag_pid" || true
}
rc=$(cat "$status_file" 2>/dev/null || echo 1)
exit "$rc"

DIAG
  mkdir -p "$run_dir/object-bench-diagnostics"
  chmod +x "$run_dir/object-bench-diagnostic-wrapper.sh"
  sha256sum "$run_dir/object-bench-diagnostic-wrapper.sh"
}

apply_dc_push_capture() {
  echo "== prepare DC push evidence capture wrapper =="
  cat > "$run_dir/s3-loader-capture-wrapper.sh" <<'DIAG'
#!/bin/bash
set -euo pipefail
args=("$@")
backend=""
direction=""
for ((i=0; i<${#args[@]}; i++)); do
  case "${args[$i]}" in
    --backend) backend="${args[$((i+1))]:-}" ;;
    --direction) direction="${args[$((i+1))]:-}" ;;
  esac
done
if [ "${args[0]:-}" = "proxy-get-bench" ] && [ "$backend" = "dc" ] && [ "$direction" = "push" ]; then
  mkdir -p "${DC_CAPTURE_DIR:?}"
  idx_file="$DC_CAPTURE_DIR/.dc-push-index"
  if [ -f "$idx_file" ]; then idx=$(cat "$idx_file"); else idx=0; fi
  idx=$((idx + 1)); echo "$idx" > "$idx_file"
  prefix="$DC_CAPTURE_DIR/dc-push-$idx"
  {
    printf 'REAL_S3LOADER=%q' "$REAL_S3LOADER"
    for arg in "${args[@]}"; do printf ' %q' "$arg"; done
    printf '\n'
  } > "$prefix.command"
  set +e
  "$REAL_S3LOADER" "${args[@]}" > "$prefix.stdout" 2> "$prefix.stderr"
  rc=$?
  set -e
  echo "$rc" > "$prefix.rc"
  sha256sum "$prefix.command" "$prefix.stdout" "$prefix.stderr" "$prefix.rc" > "$prefix.sha256" 2>/dev/null || true
  cat "$prefix.stdout"
  cat "$prefix.stderr" >&2
  exit "$rc"
fi
exec "$REAL_S3LOADER" "${args[@]}"
DIAG
  mkdir -p "$run_dir/dc-push-capture"
  chmod +x "$run_dir/s3-loader-capture-wrapper.sh"
  sha256sum "$run_dir/s3-loader-capture-wrapper.sh"
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

  bash -lc "source ~/.cargo/env 2>/dev/null || true; cd '$m01_src/enterprise/rust' && cargo build --release -p seaweedfs-sw-rdma-object --features '$object_features' --bin sw-rdma-object-put --bin sw-rdma-object-get --bin sw-rdma-s3-loader && cargo build --release -p seaweedkv-tools --features '$object_features' --bin sw-rdma-object-bench --bin sw-rdma-push-client-bench --bin seaweedfs-sw-rdma-kvcache && cargo build --release -p seaweedfs-sw-rdma-vfs --features daemon --bin sw-rdma-kd"
  bash -lc "source ~/.cargo/env 2>/dev/null || true; cd '$m01_src/seaweed-vfs' && cargo build --release -p sw-kd --bin sw-kd"
  KMOD_UNAME_R="$(uname -r)"
  echo "VFS_KERNEL_TREE_CLEAN_START path=$m01_src/seaweed-vfs/kernel"
  git -C "$m01_src" clean -ffdx -n -- seaweed-vfs/kernel | tee "$run_dir/vfs-kernel-clean.txt"
  git -C "$m01_src" clean -ffdx -- seaweed-vfs/kernel
  echo "VFS_KERNEL_TREE_CLEAN_DONE path=$m01_src/seaweed-vfs/kernel log=vfs-kernel-clean.txt"
  if bash -lc "cd '$m01_src/seaweed-vfs/kernel' && make"; then
    KMOD_BIN="$m01_src/seaweed-vfs/kernel/seaweedvfs.ko"
    test -f "$KMOD_BIN" || { echo "kernel module build did not produce $KMOD_BIN" >&2; exit 1; }
    KMOD_SHA256="$(sha256sum "$KMOD_BIN" | awk '{print $1}')"
    KMOD_VERMAGIC="$(modinfo -F vermagic "$KMOD_BIN")"
  else
    if [ "$SKIP_VFS" = "1" ]; then
      VFS_KERNEL_EXCLUDED="1"
      VFS_KERNEL_EXCLUSION_REASON="seaweedvfs.ko_build_failed_for_${KMOD_UNAME_R}"
    else
      echo "kernel module build failed for ${KMOD_UNAME_R} with SKIP_VFS=0" >&2
      exit 1
    fi
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
  case "$SKIP_VFS" in
    0|1) ;;
    *) echo "invalid SKIP_VFS=$SKIP_VFS; expected 0 or 1" >&2; exit 2 ;;
  esac
  if [ "$SKIP_VFS" = "1" ]; then
    echo "UNIFIED_VFS_EXPLICITLY_EXCLUDED reason=skip_vfs_request issue=D-3"
  else
    echo "UNIFIED_VFS_REQUESTED_ON"
  fi

  RDMA_PIPES="$RDMA_PIPES" MONO="$m01_src" WORK="/tmp/unified-rdma-gate-m01-run" MASTER_IP="192.168.1.184" RDMA="10.0.0.3:7534" CTRL="10.0.0.3:7535" ENABLE_DC="$ENABLE_DC" SKIP_VFS="$SKIP_VFS" KMOD="$m01_src/seaweed-vfs/kernel/seaweedvfs.ko" REAL_BENCHBIN="$m01_src/enterprise/rust/target/release/sw-rdma-object-bench" BENCHBIN="$run_dir/object-bench-diagnostic-wrapper.sh" DIAG_DIR="$run_dir/object-bench-diagnostics" PUSHBENCH="$m01_src/enterprise/rust/target/release/sw-rdma-push-client-bench" REAL_S3LOADER="$m01_src/enterprise/rust/target/release/sw-rdma-s3-loader" S3LOADER="$run_dir/s3-loader-capture-wrapper.sh" DC_CAPTURE_DIR="$run_dir/dc-push-capture" bash "$m01_src/$gate/m01-unified.sh"
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
    echo "skip_vfs=$SKIP_VFS"
    echo "rc_not_found_diagnostic=1"
    echo "rc_not_found_diagnostic_wrapper=object-bench-diagnostic-wrapper.sh"
    echo "rc_not_found_diagnostic_wrapper_sha256=$(sha256sum "$run_dir/object-bench-diagnostic-wrapper.sh" | awk '{print $1}')"
    echo "dc_push_capture_wrapper=s3-loader-capture-wrapper.sh"
    echo "dc_push_capture_wrapper_sha256=$(sha256sum "$run_dir/s3-loader-capture-wrapper.sh" | awk '{print $1}')"
    echo "dc_push_capture_dir=dc-push-capture"
    echo "share_bundle_dir=$RDMA_CI_SHARE_BUNDLE_DIR"
    echo "share_bundle_run_dir_name=$RDMA_CI_SHARE_RUN_DIR_NAME"
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
    echo "RDMA_CI_SKIP_VFS=$SKIP_VFS"
    if [ "$SKIP_VFS" = "1" ]; then
      echo "RDMA_CI_VFS_EXCLUDED=1"
      echo "RDMA_CI_VFS_EXCLUSION_REASON=skip_vfs_request"
      echo "RDMA_CI_VFS_EXCLUSION_ISSUE=D-3"
    else
      echo "RDMA_CI_VFS_EXCLUDED=0"
      echo "RDMA_CI_VFS_EXCLUSION_REASON="
      echo "RDMA_CI_VFS_EXCLUSION_ISSUE="
    fi
    echo "RDMA_CI_LOADER_ROWS=$loader_rows"
    echo "RDMA_CI_RC_NOT_FOUND_DIAGNOSTIC=1"
    echo "RDMA_CI_RC_NOT_FOUND_DIAGNOSTIC_WRAPPER_SHA256=$(sha256sum "$run_dir/object-bench-diagnostic-wrapper.sh" | awk '{print $1}')"
    echo "RDMA_CI_DC_PUSH_CAPTURE_WRAPPER_SHA256=$(sha256sum "$run_dir/s3-loader-capture-wrapper.sh" | awk '{print $1}')"
    echo "RDMA_CI_DC_PUSH_CAPTURE_DIR=dc-push-capture"
    echo "RDMA_CI_SHARE_BUNDLE_DIR=$RDMA_CI_SHARE_BUNDLE_DIR"
    echo "RDMA_CI_SHARE_RUN_DIR_NAME=$RDMA_CI_SHARE_RUN_DIR_NAME"
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
apply_dc_push_capture
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

if [ -d "$run_dir/dc-push-capture" ]; then
  find "$run_dir/dc-push-capture" -type f -print0 | xargs -0r sha256sum > "$run_dir/dc-push-capture.sha256"
fi
write_provenance
write_summary

echo "RDMA_LAB_CI_PASS"
