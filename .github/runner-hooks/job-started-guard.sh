#!/usr/bin/env bash
#
# ACTIONS_RUNNER_HOOK_JOB_STARTED guard for the tp01 self-hosted runner.
#
# The runner executes this before every job it accepts. A non-zero exit fails
# the job before any workflow step runs, so this is the last place to refuse
# code that must not execute on the host.
#
# What it refuses:
#   - a pull_request (or pull_request_target) whose head repository is a fork,
#     or is not this repository at all;
#   - a pull_request whose author, or whose triggering actor, is not on the
#     allowlist.
# Everything else - push, schedule, repository_dispatch, workflow_dispatch - is
# allowed, because those can only run code that already reached a branch of this
# repository, and that trust boundary is repository write access.
#
# It FAILS CLOSED. A missing jq, an unreadable or unparseable event payload, or
# a pull_request whose head repository cannot be determined are all refusals,
# not warnings.
#
# THIS IS DEFENCE IN DEPTH, NOT THE PRIMARY CONTROL. The primary control is the
# repository setting "Require approval for all outside collaborators" (Settings
# -> Actions -> General -> Fork pull request workflows), which stops a fork's
# workflow from ever being queued. This hook is what catches the case where that
# setting is relaxed, or where a workflow is later pointed at the self-hosted
# label by mistake. Note the limit that follows from where it runs: once any job
# executes arbitrary code as the runner account, that code can tamper with
# anything the runner account can write. Install this script, its allowlist and
# the runner's .env root-owned so a job cannot disable the guard for the next
# one. See README.md beside this file.
#
# Testable without a runner:
#   GITHUB_EVENT_NAME=pull_request GITHUB_EVENT_PATH=/tmp/event.json \
#   GITHUB_ACTOR=someone GITHUB_REPOSITORY=seaweedfs/artifactory \
#   ./job-started-guard.sh; echo "exit=$?"

set -uo pipefail

# Logins allowed to run pull_request workflows on this runner. Override with a
# file of one login per line ("#" comments and blank lines ignored).
ALLOWLIST_FILE="${RUNNER_GUARD_ALLOWLIST:-/etc/github-runner/allowlist}"
DEFAULT_ALLOWLIST="chrislusf pingqiu"

TAG="gh-runner-guard"

log() {
  # Best effort: journald if it is reachable, always the job log.
  logger -t "$TAG" -- "$*" 2>/dev/null || true
  printf '%s: %s\n' "$TAG" "$*"
}

deny() {
  log "DENY repo=${GITHUB_REPOSITORY:-?} event=${GITHUB_EVENT_NAME:-?} actor=${GITHUB_ACTOR:-?} run=${GITHUB_RUN_ID:-?}: $*"
  printf '::error title=Refused by the tp01 runner guard::%s\n' "$*"
  exit 1
}

allow() {
  log "ALLOW repo=${GITHUB_REPOSITORY:-?} event=${GITHUB_EVENT_NAME:-?} actor=${GITHUB_ACTOR:-?} run=${GITHUB_RUN_ID:-?}: $*"
  exit 0
}

# GitHub logins are case-insensitive, so compare folded.
fold() { printf '%s' "$1" | tr '[:upper:]' '[:lower:]'; }

allowed_logins() {
  if [ -r "$ALLOWLIST_FILE" ]; then
    sed -e 's/#.*//' -e 's/[[:space:]]//g' "$ALLOWLIST_FILE" | grep -v '^$'
  else
    printf '%s\n' $DEFAULT_ALLOWLIST
  fi
}

is_allowed() {
  local want found
  want=$(fold "$1")
  [ -n "$want" ] || return 1
  while IFS= read -r found; do
    [ "$(fold "$found")" = "$want" ] && return 0
  done < <(allowed_logins)
  return 1
}

event="${GITHUB_EVENT_NAME:-}"

case "$event" in
  pull_request|pull_request_target) ;;
  "") deny "GITHUB_EVENT_NAME is unset; refusing rather than guessing" ;;
  *)  allow "event ${event} runs only code already in this repository" ;;
esac

command -v jq >/dev/null 2>&1 || deny "jq is not installed on this runner, so the pull request cannot be checked"

payload="${GITHUB_EVENT_PATH:-}"
[ -n "$payload" ] && [ -r "$payload" ] || deny "GITHUB_EVENT_PATH is unset or unreadable, so the pull request cannot be checked"
jq -e . "$payload" >/dev/null 2>&1 || deny "the event payload is not valid JSON, so the pull request cannot be checked"

head_repo=$(jq -r '.pull_request.head.repo.full_name // empty' "$payload")
base_repo=$(jq -r '.pull_request.base.repo.full_name // empty' "$payload")
head_is_fork=$(jq -r '.pull_request.head.repo.fork // empty' "$payload")
pr_author=$(jq -r '.pull_request.user.login // empty' "$payload")
pr_number=$(jq -r '.pull_request.number // empty' "$payload")

[ -n "$head_repo" ] || deny "PR #${pr_number:-?} has no head repository (deleted fork?); cannot prove it is not a fork"
[ -n "$base_repo" ] || deny "PR #${pr_number:-?} has no base repository; cannot prove it targets this repository"

if [ -n "${GITHUB_REPOSITORY:-}" ] && [ "$(fold "$base_repo")" != "$(fold "$GITHUB_REPOSITORY")" ]; then
  deny "PR #${pr_number:-?} targets ${base_repo}, not ${GITHUB_REPOSITORY}"
fi

if [ "$(fold "$head_repo")" != "$(fold "$base_repo")" ]; then
  deny "PR #${pr_number:-?} comes from ${head_repo}, a fork of ${base_repo}. Fork code does not run on this runner; re-run it on a hosted runner"
fi

if [ "$head_is_fork" = "true" ]; then
  deny "PR #${pr_number:-?} head repository ${head_repo} is marked as a fork"
fi

# Both the author and whoever caused this run must be allowed: a re-run is
# started by the person clicking it, not by the person who opened the PR.
triggering="${GITHUB_TRIGGERING_ACTOR:-${GITHUB_ACTOR:-}}"
for who in "$pr_author" "${GITHUB_ACTOR:-}" "$triggering"; do
  [ -n "$who" ] || deny "PR #${pr_number:-?} has an empty actor field; cannot check it against the allowlist"
  is_allowed "$who" || deny "${who} is not on this runner's allowlist. PR #${pr_number:-?} must run on a hosted runner"
done

allow "PR #${pr_number:-?} from ${head_repo} by ${pr_author}"
