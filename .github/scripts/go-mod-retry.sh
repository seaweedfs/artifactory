#!/usr/bin/env bash
#
# Download Go modules for the current module, bounded and retried, so a module
# proxy hiccup costs a minute instead of a job.
#
#   .github/scripts/go-mod-retry.sh            # run in the module directory
#
# The failure this exists for: proxy.golang.org dropped one zip mid-stream
#   golang.org/x/sys@v0.48.0: read "https://proxy.golang.org/golang.org/x/sys/
#   @v/v0.48.0.zip": stream error: stream ID ...; INTERNAL_ERROR
# and `go build` failed the job (seaweed-mono EC run 39689258306, 2026-10-21).
# GOPROXY's ",direct" fallback does not help: it applies to 404/410 answers,
# not to a stream reset. A retry does, because the next fetch is a new stream.
#
# Run it as its own step before `go build` / `go test`. The build then finds
# every module in the cache and makes no network fetch of its own.

set -uo pipefail

ATTEMPTS="${GO_MOD_RETRY_ATTEMPTS:-4}"
# One attempt downloads the whole module graph; generous for a cold cache,
# short enough that a dead proxy is caught inside the step, not by the job.
TIMEOUT="${GO_MOD_RETRY_TIMEOUT:-600}"

# Reject values that would remove the bounds: a non-integer ATTEMPTS makes the
# terminal test error out, and the loop would then retry forever.
case "$ATTEMPTS" in ''|*[!0-9]*) echo "go-mod-retry: GO_MOD_RETRY_ATTEMPTS must be a positive integer" >&2; exit 2 ;; esac
case "$TIMEOUT" in ''|*[!0-9]*) echo "go-mod-retry: GO_MOD_RETRY_TIMEOUT must be a positive integer" >&2; exit 2 ;; esac
if [ "$ATTEMPTS" -lt 1 ] || [ "$TIMEOUT" -lt 1 ]; then
  echo "go-mod-retry: attempts and timeout must be at least 1" >&2
  exit 2
fi

attempt=1
while :; do
  # Take the status directly: after an `if cmd; then ...; fi` with no branch
  # taken, $? is the if statement's 0, which would report a failure as success.
  timeout --kill-after=30 "$TIMEOUT" go mod download
  rc=$?
  if [ "$rc" -eq 0 ]; then
    exit 0
  fi
  if [ "$attempt" -ge "$ATTEMPTS" ]; then
    echo "go-mod-retry: go mod download failed $attempt time(s), last exit $rc" >&2
    exit "$rc"
  fi
  delay=$((attempt * 10))
  echo "go-mod-retry: attempt $attempt failed (exit $rc); retrying in ${delay}s" >&2
  sleep "$delay"
  attempt=$((attempt + 1))
done
