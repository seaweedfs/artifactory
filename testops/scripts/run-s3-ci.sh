#!/usr/bin/env bash
set -euo pipefail

# M01/TestOps CI entry for the S3 smoke gate. Self-contained in this repo (like
# run-rdma-ci.sh / run-vfs-ci.sh): scenario + runner live here. The generic
# testops-ci-worker.sh sources the queued request .env then calls this.

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

SCENARIO="${TESTOPS_S3_SCENARIO:-scenarios/s3-smoke-chain.yaml}"
RESULTS_DIR="${TESTOPS_RESULTS_DIR:-/mnt/smb/work/share/testops/results/s3-ci}"
REF="${TESTOPS_MONO_REF:-${TESTOPS_REF:-master}}"   # seaweedfs (weed) ref built for the gate
SSH_KEY="${TESTOPS_SSH_KEY:-/home/testdev/.ssh/id_ed25519}"
RUN_BY="${TESTOPS_RUN_BY:-${USER:-testops-ci}}"
TEAM="${TESTOPS_TEAM:-storage}"
PROJECT="${TESTOPS_PROJECT:-s3-ci}"
TEST_ID="${TESTOPS_TEST_ID:-s3-smoke}"

if [[ -x ./bin/sweeds3 ]]; then
  RUNNER=(./bin/sweeds3)
else
  RUNNER=(go run ./cmd/sweeds3)
fi

mkdir -p "$RESULTS_DIR"
log_file="$(mktemp -t s3-ci.XXXXXX.log)"

echo "S3 smoke CI gate"
echo "  scenario:    $SCENARIO"
echo "  results_dir: $RESULTS_DIR"
echo "  ref:         $REF"
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
  if ! bash scripts/qa-assert.sh "$bundle_dir" --ref "$REF" --profile docs/qa-profiles/s3.expect; then
    echo "ERROR: qa-assert failed for $bundle_dir" >&2
    exit 1
  fi
fi

if [[ "$run_status" -ne 0 ]]; then
  echo "ERROR: runner exited with status $run_status" >&2
  exit "$run_status"
fi

echo "S3 CI PASS"
echo "  bundle: $bundle_dir"
echo "  dashboard: http://192.168.1.181:9099/?project=s3-ci"
