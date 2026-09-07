#!/usr/bin/env bash
#
# ACTIONS_RUNNER_HOOK_JOB_STARTED companion to job-completed-reap.sh.
#
# Records the session id the job is about to run in, so the completed hook can
# find that job's leftovers afterwards by session rather than by name.
#
# Why a session id and not a parent pid: by the time the completed hook runs,
# the job's own processes have exited and anything they leaked has been
# reparented to init, so the parent chain is gone. The session id survives that
# reparenting, which makes it the one identifier that still ties an orphan back
# to the job that created it.
#
# This is deliberately separate from job-started-guard.sh (the fork guard). That
# one refuses work and must fail closed; this one only records state and must
# never block a job. If the runner is configured with a single JOB_STARTED hook,
# point it at a wrapper that runs the guard first and this second, so a refusal
# still refuses.
#
# Install root-owned, like the guard, so a job cannot stop the next job's
# cleanup from finding it.

set -uo pipefail

TAG="gh-runner-reap"
STATE_DIR="${RUNNER_REAP_STATE:-/run/github-runner}"
SID_FILE="${STATE_DIR}/job.sid"

mkdir -p "$STATE_DIR" 2>/dev/null || true

SID="$(ps -o sid= -p $$ 2>/dev/null | tr -d ' ')"
if [ -n "$SID" ]; then
  printf '%s\n' "$SID" > "$SID_FILE" 2>/dev/null \
    && echo "[$TAG] recorded job session $SID for ${GITHUB_JOB:-<job>} (${GITHUB_RUN_ID:-?})" \
    || echo "[$TAG] WARNING: could not write $SID_FILE; the completed hook will fall back to its own session"
else
  echo "[$TAG] WARNING: could not read own session id"
fi

# Never block a job.
exit 0
