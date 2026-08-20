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

# timeout(1) is coreutils, so it is there on the runners but not everywhere a
# developer might run this. Degrade to retries-only rather than failing every
# command on a host without it.
bound=(timeout "$TIMEOUT")
if ! command -v timeout >/dev/null 2>&1; then
  echo "$op: timeout(1) not found; running unbounded with retries only" >&2
  bound=()
fi

attempt=1
until "${bound[@]+"${bound[@]}"}" sudo DEBIAN_FRONTEND=noninteractive apt-get "${apt_opts[@]}" "$@"; do
  status=$?
  reason="exit $status"
  # 124 is timeout(1)'s own code for "I killed it" -- the case above.
  if [ "$status" = 124 ]; then
    reason="no response within ${TIMEOUT}s"
  fi
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
