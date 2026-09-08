#!/usr/bin/env bash
#
# ACTIONS_RUNNER_HOOK_JOB_STARTED entry point for the tp01 self-hosted runner.
#
# The runner accepts exactly one JOB_STARTED hook, and we need two things to
# happen before a job runs: refuse work that must not execute here, and record
# the session id so the completed hook can find this job's leftovers. This
# script is that single entry point and it chains them IN ORDER.
#
# ORDER MATTERS AND IS THE WHOLE POINT.
#   1. job-started-guard.sh          may refuse the job; non-zero exit here
#                                    fails the job before any workflow step runs
#   2. job-started-record-session.sh only records state; must never block a job
#
# A refusal must stay a refusal, so the guard's exit status is propagated
# immediately and the recorder is not reached. Running them the other way round,
# or swallowing the guard's status, would turn a security control into a log
# line — which is exactly the failure mode this file exists to prevent.
#
# The recorder's own status is deliberately ignored: it is best-effort state,
# and a job that is allowed to run must not be failed because a file could not
# be written. The completed hook already falls back to its own session id.
#
# INSTALL ROOT-OWNED, like the two scripts it calls, and point the runner's .env
# at THIS file. A job runs as the runner account; if that account could edit
# this file, one job could disable the guard for the next one.

set -uo pipefail

DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
GUARD="${DIR}/job-started-guard.sh"
RECORD="${DIR}/job-started-record-session.sh"

# Fail closed on a missing guard. An absent guard is indistinguishable from a
# removed one, and "the file was not there" is not a reason to run a fork's code
# on this host.
if [ ! -x "$GUARD" ]; then
  echo "gh-runner-hook: $GUARD is missing or not executable; refusing the job" >&2
  printf '::error title=Refused by the tp01 runner hook::the fork guard is not installed\n'
  exit 1
fi

"$GUARD"
status=$?
if [ "$status" -ne 0 ]; then
  exit "$status"
fi

if [ -x "$RECORD" ]; then
  "$RECORD" || echo "gh-runner-hook: session recorder failed; the completed hook will fall back" >&2
else
  echo "gh-runner-hook: $RECORD is missing; the completed hook will fall back to its own session" >&2
fi

exit 0
