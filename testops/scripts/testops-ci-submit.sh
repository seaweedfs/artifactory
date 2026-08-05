#!/usr/bin/env bash
set -euo pipefail

# Submit one RDMA CI request to the local controller-lite queue.
#
# This is intentionally file based. It gives the team a safe trigger path before
# there is a web/API controller.

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

QUEUE_DIR="${TESTOPS_QUEUE_DIR:-/mnt/smb/work/share/testops/queue/rdma-ci}"
MONO_REF="${1:-${TESTOPS_MONO_REF:-main}}"
RUN_BY="${TESTOPS_RUN_BY:-${USER:-testops-ci}}"
TEAM="${TESTOPS_TEAM:-rdma}"
PROJECT="${TESTOPS_PROJECT:-rdma-ci}"
TEST_ID="${TESTOPS_TEST_ID:-rdma-unified-lab-gate}"
MONO_REPO="${TESTOPS_MONO_REPO:-git@github.com:seaweedfs/seaweed-mono.git}"
NOW="$(date -u +%Y%m%d-%H%M%S)"
SAFE_REF="$(printf '%s' "$MONO_REF" | tr -c 'A-Za-z0-9._-' '_')"
REQ_ID="${NOW}-${SAFE_REF}-$$"
REQ_PATH="${QUEUE_DIR}/${REQ_ID}.env"

mkdir -p "$QUEUE_DIR"

tmp="$(mktemp "${QUEUE_DIR}/.${REQ_ID}.XXXXXX")"
{
  printf 'REQUEST_ID=%q\n' "$REQ_ID"
  printf 'TESTOPS_MONO_REF=%q\n' "$MONO_REF"
  printf 'TESTOPS_MONO_REPO=%q\n' "$MONO_REPO"
  printf 'TESTOPS_RUN_BY=%q\n' "$RUN_BY"
  printf 'TESTOPS_TEAM=%q\n' "$TEAM"
  printf 'TESTOPS_PROJECT=%q\n' "$PROJECT"
  printf 'TESTOPS_TEST_ID=%q\n' "$TEST_ID"
  printf 'TESTOPS_BRANCH=%q\n' "$MONO_REF"
  printf 'TESTOPS_COMMIT=%q\n' "$MONO_REF"
} >"$tmp"
mv "$tmp" "$REQ_PATH"

echo "submitted: $REQ_ID"
echo "queue:     $REQ_PATH"
