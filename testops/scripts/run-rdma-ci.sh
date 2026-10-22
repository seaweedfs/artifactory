#!/usr/bin/env bash
set -euo pipefail

# Minimal M01/TestOps CI entry for the unified RDMA lab gate.
#
# This keeps the team-facing contract stable before the full controller exists:
# - one command;
# - dashboard-visible shared result bundle;
# - required metadata;
# - post-run bundle validation.

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

SCENARIO="${TESTOPS_RDMA_SCENARIO:-scenarios/rdma-unified-lab-gate.yaml}"
RESULTS_DIR="${TESTOPS_RESULTS_DIR:-/mnt/smb/work/share/testops/results/rdma-ci}"
MONO_REF="${TESTOPS_MONO_REF:-${MONO_REF:-main}}"
MONO_REPO="${TESTOPS_MONO_REPO:-git@github.com:seaweedfs/seaweed-mono.git}"
SSH_KEY="${TESTOPS_SSH_KEY:-/home/testdev/.ssh/id_ed25519}"
RUN_BY="${TESTOPS_RUN_BY:-${USER:-testops-ci}}"
TEAM="${TESTOPS_TEAM:-rdma}"
PROJECT="${TESTOPS_PROJECT:-rdma-ci}"
TEST_ID="${TESTOPS_TEST_ID:-rdma-unified-lab-gate}"
BRANCH="${TESTOPS_BRANCH:-$MONO_REF}"
COMMIT="${TESTOPS_COMMIT:-$MONO_REF}"

if [[ -x ./bin/sweedrdma ]]; then
  RUNNER=(./bin/sweedrdma)
else
  RUNNER=(go run ./cmd/sweedrdma)
fi

mkdir -p "$RESULTS_DIR"
log_file="$(mktemp -t rdma-ci.XXXXXX.log)"

echo "RDMA CI gate"
echo "  scenario:    $SCENARIO"
echo "  results_dir: $RESULTS_DIR"
echo "  mono_repo:   $MONO_REPO"
echo "  mono_ref:    $MONO_REF"
echo "  ssh_key:     $SSH_KEY"
echo "  run_by:      $RUN_BY"

set +e
"${RUNNER[@]}" run \
  -results-dir "$RESULTS_DIR" \
  -env "mono_repo=$MONO_REPO" \
  -env "mono_ref=$MONO_REF" \
  -env "ssh_key=$SSH_KEY" \
  -meta "project=$PROJECT" \
  -meta "team=$TEAM" \
  -meta "run_by=$RUN_BY" \
  -meta "test_id=$TEST_ID" \
  -meta "branch=$BRANCH" \
  -meta "commit=$COMMIT" \
  "$SCENARIO" 2>&1 | tee "$log_file"
run_status=${PIPESTATUS[0]}
set -e

bundle_dir="$(sed -n 's/.*run bundle: //p' "$log_file" | tail -n 1 | tr -d '\r')"
if [[ -z "$bundle_dir" ]]; then
  echo "ERROR: unable to find run bundle path in runner output" >&2
  echo "log: $log_file" >&2
  exit 1
fi

echo "Validating bundle: $bundle_dir"
if ! "${RUNNER[@]}" validate-bundle \
  -require-pass \
  -require-timing \
  -expect-scenario rdma-unified-lab-gate \
  "$bundle_dir"; then
  echo "ERROR: bundle validation failed: $bundle_dir" >&2
  exit 1
fi

if [[ "$run_status" -ne 0 ]]; then
  echo "ERROR: runner exited with status $run_status" >&2
  exit "$run_status"
fi

echo "RDMA CI PASS"
echo "  bundle: $bundle_dir"
echo "  dashboard: http://192.168.1.181:9099/"
