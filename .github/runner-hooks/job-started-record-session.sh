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
# Per instance, never shared. With two runner instances on one host a single
# state dir means the second instance's JOB_STARTED overwrites the first's
# job.sid and job.prefix, and the first instance's reaper then cleans up using
# the SECOND instance's identifiers -- killing a live job's processes and
# containers. The instance index comes from the runner's own .env.
STATE_DIR="${RUNNER_REAP_STATE:-/run/github-runner/${RUNNER_INSTANCE:-0}}"
SID_FILE="${STATE_DIR}/job.sid"
START_FILE="${STATE_DIR}/job.start"
PREFIX_FILE="${STATE_DIR}/job.prefix"

mkdir -p "$STATE_DIR" 2>/dev/null || true

# Job start time, so the completed hook can tell containers this job created
# from ones that belong to the host. Recorded before anything else runs.
date +%s > "$START_FILE" 2>/dev/null || true

# A name every container this job creates will carry, so the completed hook can
# tell THIS job's containers from another concurrent job's.
#
# Creation time alone cannot do that. With one runner instance it is a fine
# proxy -- nothing else on the host starts containers mid-job -- but with two
# instances on one machine, "created after my job started" also matches the
# other instance's containers, which are running someone else's tests right
# now. Removing those turns a passing job red for a reason its own log never
# explains, and a wrong red build costs far more than a leaked container.
#
# The instance is part of the prefix so two instances never collide even on the
# same run id, and the run and job ids make it unique per job.
JOB_PREFIX="ci-${RUNNER_INSTANCE:-0}-${GITHUB_RUN_ID:-0}-${GITHUB_JOB:-job}"
# Lowercased and reduced to [a-z0-9_-]: COMPOSE_PROJECT_NAME is validated by
# compose and rejected if it contains anything else, and a job name like
# "S3 tests (shard 1)" contains plenty. A hook that fails the job it was meant
# to protect is worse than no hook.
JOB_PREFIX="$(printf '%s' "$JOB_PREFIX" | tr 'A-Z' 'a-z' | tr -c 'a-z0-9_-' '-' | cut -c1-60)"
printf '%s' "$JOB_PREFIX" > "$PREFIX_FILE" 2>/dev/null || true

# Compose names every container and network after the project, so setting this
# is what actually puts the prefix on the containers. Written to GITHUB_ENV so
# every step of the job inherits it; a suite that calls `docker run` directly
# still has it available to use in --name.
if [ -n "${GITHUB_ENV:-}" ] && [ -w "${GITHUB_ENV}" ]; then
  {
    printf 'COMPOSE_PROJECT_NAME=%s
' "$JOB_PREFIX"
    printf 'CI_JOB_PREFIX=%s
' "$JOB_PREFIX"
  } >> "$GITHUB_ENV" 2>/dev/null || true
fi
echo "[$TAG] this job's container prefix is $JOB_PREFIX"

# Per-instance port band, for suites that bind fixed host ports (SeaweedFS
# master/volume/filer/S3). Two runner instances on one host means two jobs can
# be genuinely simultaneous -- one per instance -- and a literal port that
# worked with a single runner now collides. Exported only when RUNNER_INSTANCE
# is set, which is true only on tp01 (hosted runners never run this hook at
# all, so CI_PORT_BASE is simply absent there and every suite keeps its
# literal defaults -- "hosted unaffected" is automatic, not a branch to get
# wrong).
#
# Base 10000, banded by 1000 per instance: instance 0 -> 10000-10999, instance
# 1 -> 11000-11999. Both bands sit below the ephemeral port range on this host
# (32768+, checked on tp01, worth re-checking on any kernel change) and above
# every literal port these suites use today (all under 10000), so a suite that
# has NOT been converted yet cannot collide with one that has.
#
# Fixed offsets within the band, not one port per suite: every suite that
# starts a master+volume+filer(+S3) SeaweedFS cluster wants the same four
# roles, so one convention serves all three suites in scope (kafka-tests,
# s3-go-tests, s3tests) without per-suite bookkeeping. gRPC ports are NOT
# listed here -- SeaweedFS derives them automatically as port+10000, which a
# suite gets for free from setting CI_MASTER_PORT/CI_VOLUME_PORT and needs no
# separate variable.
if [ -n "${RUNNER_INSTANCE:-}" ] && [ -n "${GITHUB_ENV:-}" ] && [ -w "${GITHUB_ENV}" ]; then
  CI_PORT_BASE=$((10000 + RUNNER_INSTANCE * 1000))
  {
    printf 'CI_INSTANCE=%s\n' "$RUNNER_INSTANCE"
    printf 'CI_PORT_BASE=%s\n' "$CI_PORT_BASE"
    printf 'CI_MASTER_PORT=%s\n' "$((CI_PORT_BASE + 10))"
    printf 'CI_VOLUME_PORT=%s\n' "$((CI_PORT_BASE + 20))"
    printf 'CI_FILER_PORT=%s\n' "$((CI_PORT_BASE + 30))"
    printf 'CI_S3_PORT=%s\n' "$((CI_PORT_BASE + 40))"
  } >> "$GITHUB_ENV" 2>/dev/null || true
  echo "[$TAG] this job's port band is instance $RUNNER_INSTANCE, base $CI_PORT_BASE"
fi

# CPU allocation for build/test parallelism, when this instance has a fixed
# core share (CI_CORES, set in the runner's own .env alongside RUNNER_INSTANCE
# -- absent by default, so a build tool falls back to its own default of "all
# visible CPUs", exactly today's behaviour, when no partition is configured).
#
# Without this, GOMAXPROCS and CARGO_BUILD_JOBS default to the FULL host core
# count regardless of how many instances are running, so two build-heavy jobs
# each try to use all 16 threads at once -- oversubscription, not sharing,
# and the direct cause of the master-startup timeouts seen under concurrent
# kafka-tests instances. Exported only when CI_CORES is set, so an unpartitioned
# host (CI_CORES unset) sees no change from this block at all.
if [ -n "${CI_CORES:-}" ] && [ -n "${GITHUB_ENV:-}" ] && [ -w "${GITHUB_ENV}" ]; then
  {
    printf 'GOMAXPROCS=%s\n' "$CI_CORES"
    printf 'CARGO_BUILD_JOBS=%s\n' "$CI_CORES"
  } >> "$GITHUB_ENV" 2>/dev/null || true
  echo "[$TAG] this job's CPU allocation is $CI_CORES core(s) (GOMAXPROCS/CARGO_BUILD_JOBS)"
fi

# Per-instance "what is this instance doing right now" for the dashboard host
# strip. Written here rather than sourced from GitHub, because a fine-grained
# PAT owned by an outside collaborator cannot be granted repository
# Administration on an org repo at all -- there is no token that can read
# GitHub's own runner-status API from this host. tp01 telling the truth about
# itself is the only source that exists.
CURRENT_FILE="${STATE_DIR}/current.json"
# json.dumps handles quoting correctly in one place instead of a hand-rolled
# sed escape, which is exactly the kind of thing that looks right and silently
# breaks on the first job name or branch that contains a character it didn't
# expect.
CURRENT_FILE="$CURRENT_FILE" RUNNER_INSTANCE="${RUNNER_INSTANCE:-0}" GITHUB_JOB="${GITHUB_JOB:-}" \
GITHUB_WORKFLOW="${GITHUB_WORKFLOW:-}" GITHUB_RUN_ID="${GITHUB_RUN_ID:-}" \
python3 -c '
import json, os, time
json.dump({
    "instance": int(os.environ.get("RUNNER_INSTANCE", "0")),
    "job": os.environ.get("GITHUB_JOB", ""),
    "workflow": os.environ.get("GITHUB_WORKFLOW", ""),
    "run_id": os.environ.get("GITHUB_RUN_ID", ""),
    "started_epoch": int(time.time()),
}, open(os.environ["CURRENT_FILE"], "w"))
' 2>/dev/null || true

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
