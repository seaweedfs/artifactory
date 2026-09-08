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
# Per instance, never shared. With two runner instances on one host a single
# state dir means the second instance's JOB_STARTED overwrites the first's
# job.sid and job.prefix, and the first instance's reaper then cleans up using
# the SECOND instance's identifiers -- killing a live job's processes and
# containers. The instance index comes from the runner's own .env.
STATE_DIR="${RUNNER_REAP_STATE:-/run/github-runner/${RUNNER_INSTANCE:-0}}"
SID_FILE="${STATE_DIR}/job.sid"
START_FILE="${STATE_DIR}/job.start"
PREFIX_FILE="${STATE_DIR}/job.prefix"
DRYRUN="${RUNNER_REAP_DRYRUN:-0}"
GRACE_SECONDS="${RUNNER_REAP_GRACE:-5}"

# Fixed ports the suites bind. Checked, not owned: the sweep only acts on a
# listener that belongs to the runner account.
PORTS="${RUNNER_REAP_PORTS:-9333 19333 8888 18888 8333 8084 18084 9533 8004 26777 16777}"

# stderr, not stdout: sweep_ports returns its result through stdout via command
# substitution, so a log line written to stdout would be captured INTO the port
# list and reported back as if it were a port number. The runner captures both
# streams into the job log either way.
log() { echo "[$TAG] $*" >&2; }

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

port_listening() {
  # Is anything listening at all? This works unprivileged.
  [ -n "$(ss -lntH "sport = :$1" 2>/dev/null | head -1)" ]
}

port_holder_pid() {
  # ss prints users:(("comm",pid=N,fd=M)) -- but ONLY for sockets we own. As an
  # unprivileged user a root-owned listener shows the LISTEN line with no pid
  # at all. Treating "no pid" as "no listener" is a false negative, and it is
  # not hypothetical: a leaked container held 9333, 8888 and 8333 for six hours
  # while this hook reported every watched port free, because docker-proxy runs
  # as root and the pid field was simply absent.
  ss -lntpH "sport = :$1" 2>/dev/null | grep -oE 'pid=[0-9]+' | head -1 | cut -d= -f2
}

sweep_ports() {
  local still=""
  for port in $PORTS; do
    port_listening "$port" || continue
    local pid
    pid="$(port_holder_pid "$port")"
    if [ -z "$pid" ]; then
      # Listening, but the owner is invisible to us: another account, almost
      # always a container's docker-proxy. Report it precisely rather than
      # silently calling the port free.
      local owner_hint
      owner_hint="$(docker ps --format '{{.ID}} {{.Image}} {{.Ports}}' 2>/dev/null                     | grep -F ":$port->" | head -1)"
      if [ -n "$owner_hint" ]; then
        log "port $port HELD by a container: $owner_hint - see the container sweep below"
      else
        log "port $port HELD by another account (pid not visible to $RUNNER_USER) - cannot clear it here"
      fi
      still="$still $port"
      continue
    fi
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

# Containers the job left behind. THIS is the leak that actually bites. A
# compose stack whose job was cancelled never runs `down`, and its published
# ports stay bound by docker-proxy. dockerd owns those processes, so no amount
# of session reaping touches them: two containers from one cancelled job held
# 9333, 8888 and 8333 for six hours and failed every port-binding suite after
# it.
#
# Attribution is by THIS JOB'S PREFIX first and creation time second. The
# JOB_STARTED hook stamps every container the job creates with a name unique to
# the job (via COMPOSE_PROJECT_NAME); the reaper removes only containers
# carrying it, and only ones newer than the job's start.
#
# Creation time alone is not enough and stops being safe the moment a second
# runner instance shares the host: "created after my job started" then also
# matches the OTHER instance's containers, which are running its tests right
# now. Removing those turns a passing job red for a reason its own log never
# explains. Without a prefix this hook removes nothing at all -- a leaked
# container is the next job's port conflict, which is the cheaper failure.
if command -v docker >/dev/null 2>&1; then
  started_at=""
  [ -r "$START_FILE" ] && started_at="$(cat "$START_FILE" 2>/dev/null)"
  job_prefix=""
  [ -r "$PREFIX_FILE" ] && job_prefix="$(cat "$PREFIX_FILE" 2>/dev/null)"

  if [ -z "$started_at" ]; then
    log "no job start timestamp; not removing any container (cannot tell ours from the host's)"
  elif [ -z "$job_prefix" ]; then
    # Fail SAFE, not thorough. Without the prefix the only test left is creation
    # time, and on a host running two runner instances that test also matches
    # the other instance's live containers. Leaking a container costs the next
    # job on those ports; killing another instance's containers costs a red
    # build on a job that passed, and nothing in that job's log explains it.
    log "WARNING: no job prefix recorded; NOT removing any container. A leftover here becomes the next job's port conflict, which is the cheaper failure."
  else
    for cid in $(docker ps -q 2>/dev/null); do
      created="$(docker inspect -f '{{.Created}}' "$cid" 2>/dev/null)"
      [ -z "$created" ] && continue
      c_epoch="$(date -d "$created" +%s 2>/dev/null || echo 0)"

      # Both tests must pass: the container must carry THIS job's prefix (which
      # is what makes it ours rather than a concurrent job's) and it must be
      # newer than this job's start (which catches a stale container from an
      # earlier run that happened to reuse the name).
      name="$(docker inspect -f '{{.Name}}' "$cid" 2>/dev/null | sed 's|^/||')"
      project="$(docker inspect -f '{{index .Config.Labels "com.docker.compose.project"}}' "$cid" 2>/dev/null)"
      mine=0
      case "$name" in "$job_prefix"*) mine=1 ;; esac
      [ "$project" = "$job_prefix" ] && mine=1

      if [ "$mine" != "1" ]; then
        log "container $cid ($name) is not this job's ($job_prefix) - leaving it alone"
        continue
      fi
      if [ "$c_epoch" -lt "$started_at" ]; then
        log "container $cid ($name) carries our prefix but predates this job - leaving it alone"
        continue
      fi

      info="$(docker inspect -f '{{.Name}} {{.Config.Image}}' "$cid" 2>/dev/null)"
      if [ "$DRYRUN" = "1" ]; then
        log "DRYRUN would remove container $cid ($info) created by this job"
      else
        log "removing container $cid ($info) - created by this job and still running"
        docker rm -f "$cid" >/dev/null 2>&1 || log "  could not remove $cid"
      fi
    done

    # Compose leaves its network behind even when every container is gone.
    for net in $(docker network ls --format '{{.Name}}' 2>/dev/null | grep -F "$job_prefix" || true); do
      if [ "$DRYRUN" = "1" ]; then
        log "DRYRUN would remove network $net"
      else
        docker network rm "$net" >/dev/null 2>&1 && log "removed network $net" || true
      fi
    done
  fi
fi

# Ports are checked AFTER the containers are gone: removing them is usually
# what frees the port, so checking first would report a problem we just fixed.
remaining="$(sweep_ports | tr -s ' ')"

if [ -n "${remaining// /}" ]; then
  log "PORTS STILL BOUND after cleanup:${remaining} - the next job on these ports will fail"
else
  log "all watched ports free"
fi

# Mark this instance idle for the dashboard host strip. The last thing this
# hook does, so a strip reading "idle" is trustworthy: everything above it --
# the session reap, the container sweep -- has already finished.
CURRENT_FILE="${STATE_DIR}/current.json"
rm -f "$SID_FILE" "$PREFIX_FILE" "$CURRENT_FILE" 2>/dev/null || true
log "done"
# Always succeed. A job that passed must not be failed by its own cleanup.
exit 0
