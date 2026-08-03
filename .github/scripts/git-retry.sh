#!/usr/bin/env bash
#
# Run a git network command, retrying through transient github 5xx and
# hangups. A blip that kills a clone costs a whole job, and every workflow
# here starts by pulling source down.
#
#   .github/scripts/git-retry.sh clone -b main "$REMOTE" src
#   .github/scripts/git-retry.sh fetch origin "$SHA"
#
# Only wrap commands whose non-zero exit means "it broke". A command that
# answers with its exit code -- git ls-remote --exit-code returns 2 for "no
# such ref" -- would spend the full backoff on every ordinary miss.
#
# Retrying a clone is safe: git removes the directory it created when the
# clone fails. A directory that already existed is left alone, so callers
# that pre-create the target must clear it themselves before each attempt.

set -uo pipefail

ATTEMPTS="${GIT_RETRY_ATTEMPTS:-4}"

# Name the operation in the log by its subcommand, stepping over any leading
# git options. Never log the argv itself -- the remote URL carries a token.
op=git
skip=0
for arg in "$@"; do
  if [ "$skip" = 1 ]; then skip=0; continue; fi
  case "$arg" in
    -C|-c) skip=1 ;;
    -*) ;;
    *) op="git $arg"; break ;;
  esac
done

attempt=1
until git "$@"; do
  if [ "$attempt" -ge "$ATTEMPTS" ]; then
    echo "$op: failed after $ATTEMPTS attempts" >&2
    exit 1
  fi
  echo "$op: failed (attempt $attempt of $ATTEMPTS); retrying in $((attempt * 10))s" >&2
  sleep $((attempt * 10))
  attempt=$((attempt + 1))
done
