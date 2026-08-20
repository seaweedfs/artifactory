#!/usr/bin/env bash
#
# Run an apt-get command, bounded and retried, so a mirror outage costs a
# minute instead of a job.
#
#   .github/scripts/apt-retry.sh update
#   .github/scripts/apt-retry.sh install -y libfuse3-dev
#
# The failure this exists for is not an error exit -- it is a HANG. When
# azure.archive.ubuntu.com stops answering, apt sits in its own retry loop
# printing "Ign:" lines against every index in sources.list, and the job runs
# until something kills it: four runs on seaweed-mono#410 were cancelled that
# way after one to two and a half hours, having produced nothing.
#
# So the timeouts do the work here and the retries are the backstop, which is
# the opposite of git-retry.sh. apt is told to give up on an unresponsive
# connection in seconds rather than its default two minutes, and each attempt
# is bounded from outside in case it finds a way to hang anyway.
#
# Wrap `update` and `install` separately -- an install whose index is stale
# fails fast and clearly, and re-running `update` on its own retry schedule is
# what actually fixes that.

set -uo pipefail

ATTEMPTS="${APT_RETRY_ATTEMPTS:-4}"
# Generous enough for a real install over a slow link, short enough that a
# dead mirror is caught inside one job step rather than by the job timeout.
TIMEOUT="${APT_RETRY_TIMEOUT:-300}"
# How long apt gets to honour TERM before it is killed outright. See the
# --kill-after note below for why the escalation is wanted at all.
KILL_GRACE="${APT_RETRY_KILL_AFTER:-30}"

# Reject settings that would quietly remove the bounds this script exists to
# apply. Two of them fail badly rather than obviously:
#
#   ATTEMPTS=abc -> `[ 1 -ge abc ]` errors and returns non-zero, so the
#     terminal branch is never taken and the loop retries FOREVER, sleeping
#     longer each time. An unbounded loop is the one outcome this script must
#     not produce.
#   TIMEOUT=0    -> GNU timeout reads 0 as "no limit", so a hang is unbounded
#     again, which is precisely the failure being fixed here.
require_positive_int() {
  case "$2" in
    '' | *[!0-9]*)
      echo "apt-retry: $1 must be a positive integer, got '$2'" >&2
      exit 2
      ;;
  esac
  if [ "$2" -eq 0 ]; then
    echo "apt-retry: $1 must be greater than zero; 0 disables the bound" >&2
    exit 2
  fi
}
require_positive_int APT_RETRY_ATTEMPTS "$ATTEMPTS"
require_positive_int APT_RETRY_TIMEOUT "$TIMEOUT"
require_positive_int APT_RETRY_KILL_AFTER "$KILL_GRACE"

# Acquire::Retries lets apt re-ask a working mirror for a file it dropped;
# the Timeouts are what stop it waiting on one that is gone. Both are per
# connection, so the worst case is bounded by the outer `timeout` regardless.
apt_opts=(
  -o Acquire::Retries=2
  -o Acquire::http::Timeout=20
  -o Acquire::https::Timeout=20
  -o Acquire::ftp::Timeout=20
)

op="apt-get ${1:-}"

# --kill-after escalates to KILL for a command that ignores or delays TERM;
# without it the per-attempt bound is advisory. Killing apt mid-install can
# leave dpkg wanting a --configure, which would matter on a real machine and
# does not on a throwaway runner -- and we only reach it after apt has already
# spent TIMEOUT seconds unresponsive and then ignored TERM for KILL_GRACE more.
#
# timeout(1) is coreutils, so it is on the runners but not everywhere a
# developer might run this, and not every build of it has --kill-after. Probe
# once and degrade in two steps rather than failing every command outright.
bound=(timeout --kill-after="$KILL_GRACE" "$TIMEOUT")
if ! command -v timeout >/dev/null 2>&1; then
  echo "$op: timeout(1) not found; running unbounded with retries only" >&2
  bound=()
elif ! timeout --kill-after=1 1 true >/dev/null 2>&1; then
  echo "$op: timeout(1) has no --kill-after; bounding with TERM only" >&2
  bound=(timeout "$TIMEOUT")
fi

attempt=1
until "${bound[@]+"${bound[@]}"}" sudo DEBIAN_FRONTEND=noninteractive apt-get "${apt_opts[@]}" "$@"; do
  status=$?
  reason="exit $status"
  case "$status" in
    # timeout(1)'s own code for "I terminated it" -- the hang case above.
    124) reason="no response within ${TIMEOUT}s" ;;
    # 128+9: TERM was ignored and --kill-after escalated to KILL.
    137) reason="ignored TERM, killed ${KILL_GRACE}s after the ${TIMEOUT}s bound" ;;
  esac
  if [ "$attempt" -ge "$ATTEMPTS" ]; then
    plural=s
    [ "$ATTEMPTS" = 1 ] && plural=
    echo "$op: failed after $ATTEMPTS attempt$plural ($reason)" >&2
    exit 1
  fi
  echo "$op: failed (attempt $attempt of $ATTEMPTS, $reason); retrying in $((attempt * 10))s" >&2
  sleep $((attempt * 10))
  attempt=$((attempt + 1))
done
