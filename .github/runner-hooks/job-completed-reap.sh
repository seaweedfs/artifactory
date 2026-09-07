#!/usr/bin/env bash
#
# ACTIONS_RUNNER_HOOK_JOB_COMPLETED reaper for the tp01 self-hosted runner.
#
# The runner executes this after every job, before it accepts the next one. Its
# job is to leave the host as close to "no job ran here" as a persistent machine
# can get.
#
# WHY THIS EXISTS. On a GitHub-hosted runner every job gets a fresh VM, so a
# process that outlives its job dies with the machine and nothing is inherited.
# On a persistent runner it survives, keeps its listening sockets, and the NEXT
# job fails to bind them. That is not theoretical: `s3tests` failed on this host
# with "Some ports are still in use after aggressive cleanup!" while the suite
# itself was fine, and the failure landed on a job that had done nothing wrong.
#
# HOW IT FINDS PROCESSES: by session id, never by name.
#   The companion JOB_STARTED hook records this job's session id. Every process
#   the job spawns inherits it, and — unlike a parent pid — it SURVIVES the
#   parent exiting, which is exactly the case we are cleaning up after: an
#   orphan reparents to init but keeps its session.
#   Matching on a name pattern (`pkill -f weed`) would be wrong twice over: it
#   would miss anything renamed, and it would kill processes belonging to
#   somebody else on a shared host. This host runs other things.
#
# The port sweep is the backstop for a process that escaped its session with
# setsid(). It resolves the port to a PID and checks that PID belongs to the
# runner account before touching it — still pid-based, never a name match, and
# never anything that is not ours.
#
# It NEVER fails the job: a job that already passed must not be marked failed by
# its own cleanup. Everything here is best-effort and loud.
#
# INSTALL ROOT-OWNED. A job runs as the runner account, so if that account could
# edit this script, one job could disable the cleanup that protects the next.
# See README.md beside this file.
#
# Testable without a runner:
#   RUNNER_REAP_STATE=/tmp/reap RUNNER_REAP_DRYRUN=1 ./job-completed-reap.sh

set -uo pipefail

TAG="gh-runner-reap"
STATE_DIR="${RUNNER_REAP_STATE:-/run/github-runner}"
SID_FILE="${STATE_DIR}/job.sid"
DRYRUN="${RUNNER_REAP_DRYRUN:-0}"
GRACE_SECONDS="${RUNNER_REAP_GRACE:-5}"

# Fixed ports the suites bind. Checked, not owned: the sweep only acts on a
# listener that belongs to the runner account.
PORTS="${RUNNER_REAP_PORTS:-9333 19333 8888 18888 8333 8084 18084 9533 8004 26777 16777}"

log() { echo "[$TAG] $*"; }

RUNNER_USER="$(id -un)"
SELF_SID="$(ps -o sid= -p $$ 2>/dev/null | tr -d ' ')"

# Every process between this hook and init. These are the runner's own worker,
# its shell, and this script -- killing any of them takes the runner offline or
# aborts the cleanup halfway. The hook runs INSIDE the session it is cleaning,
# so without this the first thing it would reap is itself.
ancestors() {
  local pid=$$ out=""
  while [ -n "$pid" ] && [ "$pid" != "0" ] && [ "$pid" != "1" ]; do
    out="$out $pid"
    pid="$(ps -o ppid= -p "$pid" 2>/dev/null | tr -d ' ')"
  done
  echo "$out"
}
SELF_CHAIN="$(ancestors)"

is_ancestor() {
  case " $SELF_CHAIN " in *" $1 "*) return 0 ;; esac
  return 1
}

# ---------------------------------------------------------------- session reap

reap_session() {
  local sid="$1" pass="$2" signal="$3" killed=0
  # Every pid in the session except this hook's own session leader chain.
  local pids
  pids="$(ps -eo pid=,sid=,user=,comm= 2>/dev/null \
          | awk -v s="$sid" -v u="$RUNNER_USER" -v me="$$" \
                '$2==s && $3==u && $1!=me {print $1}')"
  [ -z "$pids" ] && return 0

  for pid in $pids; do
    # Never touch this hook's own ancestor chain. The hook runs inside the
    # session it is cleaning, so its shell and the runner's worker are in the
    # same session as the job's leftovers; killing one of those would take the
    # runner offline and the watchdog would start rescuing jobs for no reason.
    if is_ancestor "$pid"; then continue; fi
    local comm
    comm="$(ps -o comm= -p "$pid" 2>/dev/null || true)"
    case "$comm" in
      Runner.Listener|Runner.Worker|runsvc.sh|"") continue ;;
    esac
    local args
    args="$(ps -o args= -p "$pid" 2>/dev/null | cut -c1-100 || true)"
    if [ "$DRYRUN" = "1" ]; then
      log "DRYRUN would $signal pid=$pid comm=$comm args=${args}"
    else
      log "$pass: $signal pid=$pid comm=$comm args=${args}"
      kill "-$signal" "$pid" 2>/dev/null || true
    fi
    killed=$((killed + 1))
  done
  return "$killed"
}

# ---------------------------------------------------------------- port sweep

port_holder_pid() {
  # ss prints users:(("comm",pid=N,fd=M)). Take the pid, not the name.
  ss -lntpH "sport = :$1" 2>/dev/null | grep -oE 'pid=[0-9]+' | head -1 | cut -d= -f2
}

sweep_ports() {
  local still=""
  for port in $PORTS; do
    local pid
    pid="$(port_holder_pid "$port")"
    [ -z "$pid" ] && continue
    local owner comm
    owner="$(ps -o user= -p "$pid" 2>/dev/null | tr -d ' ')"
    comm="$(ps -o comm= -p "$pid" 2>/dev/null | tr -d ' ')"
    if [ "$owner" != "$RUNNER_USER" ]; then
      # Somebody else's service. Report it and leave it alone - this host is
      # not exclusively ours.
      log "port $port held by pid=$pid comm=$comm owner=$owner - NOT ours, left alone"
      still="$still $port"
      continue
    fi
    if [ "$DRYRUN" = "1" ]; then
      log "DRYRUN would kill port $port holder pid=$pid comm=$comm"
    else
      log "port $port still held by our pid=$pid comm=$comm - escaped its session, killing"
      kill -KILL "$pid" 2>/dev/null || true
    fi
  done
  echo "$still"
}

# ---------------------------------------------------------------- main

log "job completed; reaping leftovers for ${GITHUB_JOB:-<unknown job>} (${GITHUB_RUN_ID:-?})"

JOB_SID=""
if [ -r "$SID_FILE" ]; then
  JOB_SID="$(cat "$SID_FILE" 2>/dev/null | tr -d ' ')"
fi
# Fall back to this hook's own session: the runner starts the hook in the same
# session as the job it just finished.
[ -z "$JOB_SID" ] && JOB_SID="$SELF_SID"

if [ -n "$JOB_SID" ]; then
  reap_session "$JOB_SID" "TERM sweep" TERM
  n=$?
  if [ "$n" -gt 0 ]; then
    log "signalled $n process(es) with TERM; waiting ${GRACE_SECONDS}s"
    sleep "$GRACE_SECONDS"
    reap_session "$JOB_SID" "KILL sweep" KILL
    log "killed $? remaining process(es)"
  else
    log "no leftover processes in session $JOB_SID"
  fi
else
  log "WARNING: could not determine the job session id; skipping the session reap"
fi

remaining="$(sweep_ports | tr -s ' ')"

# Docker containers the job left behind. Removed by id, and only ones started
# after this job began, so a long-lived container on the host is not touched.
if command -v docker >/dev/null 2>&1 && [ "$DRYRUN" != "1" ]; then
  since="${RUNNER_REAP_SINCE:-}"
  leftovers="$(docker ps -q 2>/dev/null || true)"
  if [ -n "$leftovers" ]; then
    log "containers still running after the job: $(echo "$leftovers" | tr '\n' ' ')"
    log "leaving them: removing a container the host owns would be worse than a stale one"
  fi
fi

if [ -n "${remaining// /}" ]; then
  log "PORTS STILL BOUND after cleanup:${remaining} - the next job on these ports will fail"
else
  log "all watched ports free"
fi

rm -f "$SID_FILE" 2>/dev/null || true
log "done"
# Always succeed. A job that passed must not be failed by its own cleanup.
exit 0
