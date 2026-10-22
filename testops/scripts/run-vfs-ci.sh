#!/usr/bin/env bash
set -euo pipefail

# M01/TestOps CI entry for the VFS cross-access gate. Self-contained in this repo
# (like run-rdma-ci.sh): the scenario + runner live here. The worker
# (testops-ci-worker.sh) sources the queued request .env then calls this.
#
# Contract: run the gate, print "  bundle: <dir>" (the worker greps "bundle: "),
# and accept it with the shared qa-assert.sh + the vfs profile.

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

SCENARIO="${TESTOPS_VFS_SCENARIO:-scenarios/vfs-cross-access-chain.yaml}"
RESULTS_DIR="${TESTOPS_RESULTS_DIR:-/mnt/smb/work/share/testops/results/vfs-ci}"
REF="${TESTOPS_MONO_REF:-${TESTOPS_REF:-master}}"
SSH_KEY="${TESTOPS_SSH_KEY:-/home/testdev/.ssh/id_ed25519}"   # M01 Linux key (not the Windows path in the scenario env)
RUN_BY="${TESTOPS_RUN_BY:-${USER:-testops-ci}}"
TEAM="${TESTOPS_TEAM:-storage}"
PROJECT="${TESTOPS_PROJECT:-vfs-ci}"
TEST_ID="${TESTOPS_TEST_ID:-vfs-cross-access}"

if [[ -x ./bin/sweeds3 ]]; then
  RUNNER=(./bin/sweeds3)
else
  RUNNER=(go run ./cmd/sweeds3)
fi

mkdir -p "$RESULTS_DIR"
log_file="$(mktemp -t vfs-ci.XXXXXX.log)"

echo "VFS cross-access CI gate"
echo "  scenario:    $SCENARIO"
echo "  results_dir: $RESULTS_DIR"
echo "  ref:         $REF"
echo "  ssh_key:     $SSH_KEY"
echo "  run_by:      $RUN_BY"

set +e
"${RUNNER[@]}" run \
  -results-dir "$RESULTS_DIR" \
  -env "ssh_key=$SSH_KEY" \
  -env "weed_ref=$REF" \
  -meta "project=$PROJECT" \
  -meta "team=$TEAM" \
  -meta "run_by=$RUN_BY" \
  -meta "test_id=$TEST_ID" \
  -meta "branch=$REF" \
  "$SCENARIO" 2>&1 | tee "$log_file"
run_status=${PIPESTATUS[0]}
set -e

bundle_dir="$(sed -n 's/.*run bundle: //p' "$log_file" | tail -n 1 | tr -d '\r')"
if [[ -z "$bundle_dir" ]]; then
  echo "ERROR: unable to find run bundle path in runner output" >&2
  exit 1
fi

echo "Accepting bundle: $bundle_dir"
if [[ -f "$bundle_dir/result.json" ]]; then
  if ! bash scripts/qa-assert.sh "$bundle_dir" --ref "$REF" --profile docs/qa-profiles/vfs.expect; then
    echo "ERROR: qa-assert failed for $bundle_dir" >&2
    exit 1
  fi
fi

if [[ "$run_status" -ne 0 ]]; then
  echo "ERROR: runner exited with status $run_status" >&2
  exit "$run_status"
fi

echo "VFS CI PASS"
echo "  bundle: $bundle_dir"
echo "  dashboard: http://192.168.1.181:9099/?project=vfs-ci"
