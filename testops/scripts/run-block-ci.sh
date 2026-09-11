#!/usr/bin/env bash
set -euo pipefail

# M01/TestOps CI entry for the block first-volume gate. Unlike rdma/vfs, the block
# gate lives in the (private) seaweed_block repo and needs product_root synced to
# m02. To avoid needing that repo on M01, the harness (scenario + scripts + charts)
# is staged on SMB at TESTOPS_BLOCK_HARNESS; m02 (which also mounts SMB) copies it
# into product_root locally. The tested artifact is the published sw-block IMAGE
# (REF = image tag), so no repo build is needed.
#
# Contract: run the gate, print "  bundle: <dir>", accept via qa-assert + block profile.

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

HARNESS="${TESTOPS_BLOCK_HARNESS:-/mnt/smb/work/share/testops/harness/block}"
SCENARIO="${TESTOPS_BLOCK_SCENARIO:-$HARNESS/testops/scenarios/helm-single-node-first-volume-chain.yaml}"
RESULTS_DIR="${TESTOPS_RESULTS_DIR:-/mnt/smb/work/share/testops/results/block-ci}"
PRODUCT_ROOT="${TESTOPS_BLOCK_PRODUCT_ROOT:-/tmp/seaweed_block}"
REF="${TESTOPS_MONO_REF:-${TESTOPS_REF:-sha-28a99ce4f644}}"   # published sw-block image tag
M2="${TESTOPS_M2:-192.168.1.184}"
M2_USER="${TESTOPS_M2_USER:-testdev}"
SSH_KEY="${TESTOPS_SSH_KEY:-/home/testdev/.ssh/id_ed25519}"
IMG="ghcr.io/seaweedfs/seaweed-block:${REF}"
CSI="ghcr.io/seaweedfs/seaweed-block-csi:${REF}"
RUN_BY="${TESTOPS_RUN_BY:-${USER:-testops-ci}}"
TEAM="${TESTOPS_TEAM:-block}"
PROJECT="${TESTOPS_PROJECT:-block-ci}"
TEST_ID="${TESTOPS_TEST_ID:-helm-single-node-first-volume}"

if [[ -x ./bin/swblock ]]; then
  RUNNER=(./bin/swblock)
else
  RUNNER=(go run ./cmd/swblock)
fi

echo "Block CI gate"
echo "  scenario:    $SCENARIO"
echo "  results_dir: $RESULTS_DIR"
echo "  image ref:   $REF"
echo "  run_by:      $RUN_BY"

# Sync the harness into product_root on m02 (m02 mounts SMB -> copies locally).
ssh -i "$SSH_KEY" -o StrictHostKeyChecking=no "$M2_USER@$M2" \
  "rm -rf $PRODUCT_ROOT/scripts $PRODUCT_ROOT/charts; mkdir -p $PRODUCT_ROOT; cp -r $HARNESS/scripts $HARNESS/charts $PRODUCT_ROOT/ && echo product_root-synced"

mkdir -p "$RESULTS_DIR"
log_file="$(mktemp -t block-ci.XXXXXX.log)"

set +e
"${RUNNER[@]}" run \
  -results-dir "$RESULTS_DIR" \
  -env "product_root=$PRODUCT_ROOT" \
  -env "ssh_key=$SSH_KEY" \
  -env "sw_block_ref=$REF" \
  -env "sw_block_commit=${REF#sha-}" \
  -env "sw_block_image=$IMG" \
  -env "sw_block_csi_image=$CSI" \
  -meta "project=$PROJECT" \
  -meta "team=$TEAM" \
  -meta "run_by=$RUN_BY" \
  -meta "test_id=$TEST_ID" \
  -meta "branch=$REF" \
  -meta "commit=$REF" \
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
  if ! bash scripts/qa-assert.sh "$bundle_dir" --ref "$REF" --profile docs/qa-profiles/block.expect; then
    echo "ERROR: qa-assert failed for $bundle_dir" >&2
    exit 1
  fi
fi

if [[ "$run_status" -ne 0 ]]; then
  echo "ERROR: runner exited with status $run_status" >&2
  exit "$run_status"
fi

echo "Block CI PASS"
echo "  bundle: $bundle_dir"
echo "  dashboard: http://192.168.1.181:9099/?project=block-ci"
