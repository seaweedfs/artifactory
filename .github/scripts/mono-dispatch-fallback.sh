#!/usr/bin/env bash
# Fallback for seaweed-mono's "Notify artifactory CI" workflow.
#
# While seaweed-mono cannot start GitHub-hosted jobs, its notify workflow never
# runs, so no mono PR or main push reaches the artifactory suites. This script
# finds mono heads that never reached artifactory and sends the same
# repository_dispatch that mono's notify workflow would have sent:
#   enterprise/**  -> mono-push / mono-merge-request
#   seaweed-vfs/** -> mono-vfs-push / mono-vfs-merge-request
#   client_payload  = {ref, sha, pr}
#
# Candidates: seaweed-mono main head, and open non-draft PRs whose head branch
# lives in seaweed-mono (fork PRs cannot be cloned by artifactory).
#
# A head is "already handled" when any of these exist, so a SHA is never
# dispatched twice in a loop:
#   - an artifactory repository_dispatch run whose title carries mono:<sha>
#     (covers runs still in progress and runs started by mono's own notify);
#   - a commit status on the mono SHA whose context starts with "artifactory / "
#     (reported results, and this script's own marker), except this script's
#     marker in state "error", which means the dispatch call itself failed.
#
# Env:
#   MONO_TOKEN      read mono contents/pulls/statuses; write mono statuses
#   DISPATCH_TOKEN  create repository_dispatch on ART_REPO; read its runs
#   ART_REPO        default seaweedfs/artifactory
#   MONO_REPO       default seaweedfs/seaweed-mono
#   MAX_DISPATCH    upper bound of heads dispatched per run (default 6)
#   DRY_RUN         "true" prints decisions and sends nothing
set -euo pipefail

: "${MONO_TOKEN:?MONO_TOKEN is required}"
: "${DISPATCH_TOKEN:?DISPATCH_TOKEN is required}"
ART_REPO="${ART_REPO:-seaweedfs/artifactory}"
MONO_REPO="${MONO_REPO:-seaweedfs/seaweed-mono}"
MAX_DISPATCH="${MAX_DISPATCH:-6}"
DRY_RUN="${DRY_RUN:-false}"
MARK_CONTEXT="artifactory / dispatch-fallback"
MAIN_LOOKBACK=30

mono_api() { GH_TOKEN="$MONO_TOKEN" gh api -H "X-GitHub-Api-Version: 2022-11-28" "$@"; }
art_api()  { GH_TOKEN="$DISPATCH_TOKEN" gh api -H "X-GitHub-Api-Version: 2022-11-28" "$@"; }

# SHAs that already have an artifactory dispatch run (any state). A failed
# listing fails the run: without it this dedupe layer would silently vanish.
if ! run_titles=$(art_api --paginate -X GET "repos/${ART_REPO}/actions/runs" \
    -f event=repository_dispatch -f per_page=100 --jq '.workflow_runs[].display_title'); then
  echo "cannot list ${ART_REPO} dispatch runs; refusing to dispatch without dedupe" >&2
  exit 1
fi
seen_runs=$(printf '%s\n' "$run_titles" | { grep -oE 'mono:[0-9a-f]{40}' || true; } | cut -d: -f2 | sort -u)

# Returns 0 handled (prints why), 1 not handled, 2 when the statuses cannot be
# read. Callers treat 2 as fatal: an unreadable status must never look unhandled.
handled() {
  local sha=$1 n
  # A here-string, not a pipe: under pipefail, grep -q exiting on a match
  # near the top of a long list SIGPIPEs printf and the test reads as a miss.
  if grep -qxF -- "$sha" <<<"$seen_runs"; then
    echo "run"; return 0
  fi
  if ! n=$(mono_api "repos/${MONO_REPO}/commits/${sha}/statuses?per_page=100" \
      --jq "[.[] | select((.context | startswith(\"artifactory / \"))
                          and ((.context == \"${MARK_CONTEXT}\" and .state == \"error\") | not))] | length"); then
    echo "cannot read ${MONO_REPO} statuses for ${sha}" >&2
    return 2
  fi
  if [ "${n:-0}" -gt 0 ]; then
    echo "status"; return 0
  fi
  return 1
}

# Prints "enterprise vfs" flags for the changes base...head; fails open.
scope() {
  local base=$1 head=$2 files enterprise vfs
  if [ -z "$base" ]; then echo "true true"; return; fi
  if ! files=$(mono_api "repos/${MONO_REPO}/compare/${base}...${head}" \
      --jq 'if (.files | length) >= 300 then "TRUNCATED" else .files[].filename end'); then
    echo "true true"; return
  fi
  if grep -qx TRUNCATED <<<"$files"; then echo "true true"; return; fi
  enterprise=false; vfs=false
  grep -q '^enterprise/'  <<<"$files" && enterprise=true
  grep -q '^seaweed-vfs/' <<<"$files" && vfs=true
  echo "$enterprise $vfs"
}

mark() {
  local sha=$1 state=$2 desc=$3
  [ "$DRY_RUN" = "true" ] && return 0
  mono_api -X POST "repos/${MONO_REPO}/statuses/${sha}" \
    -f state="$state" -f context="$MARK_CONTEXT" -f description="$desc" >/dev/null
}

send() {
  local event=$1 ref=$2 sha=$3 pr=$4
  echo "  dispatch ${event} ref=${ref} sha=${sha} pr=${pr:-none}"
  [ "$DRY_RUN" = "true" ] && return 0
  art_api -X POST "repos/${ART_REPO}/dispatches" --input - >/dev/null <<EOF
{"event_type":"${event}","client_payload":{"ref":"${ref}","sha":"${sha}","pr":"${pr}"}}
EOF
}

dispatched=0
consider() {
  local kind=$1 ref=$2 sha=$3 base=$4 pr=$5 why flags enterprise vfs push_ev vfs_ev
  local rc=0
  why=$(handled "$sha") || rc=$?
  if [ "$rc" -eq 2 ]; then exit 1; fi
  if [ "$rc" -eq 0 ]; then
    echo "skip ${kind} ${ref}@${sha:0:12}: already handled (${why})"
    return
  fi
  read -r enterprise vfs <<<"$(scope "$base" "$sha")"
  if [ "$enterprise" = false ] && [ "$vfs" = false ]; then
    echo "skip ${kind} ${ref}@${sha:0:12}: no enterprise/ or seaweed-vfs/ change"
    return
  fi
  if [ "$dispatched" -ge "$MAX_DISPATCH" ]; then
    echo "defer ${kind} ${ref}@${sha:0:12}: per-run cap ${MAX_DISPATCH} reached"
    return
  fi
  if [ "$kind" = pr ]; then push_ev=mono-merge-request; vfs_ev=mono-vfs-merge-request
  else push_ev=mono-push; vfs_ev=mono-vfs-push; fi
  echo "dispatch ${kind} ${ref}@${sha:0:12} enterprise=${enterprise} vfs=${vfs}"
  mark "$sha" pending "dispatching to artifactory (fallback)"
  if { [ "$enterprise" = false ] || send "$push_ev" "$ref" "$sha" "$pr"; } &&
     { [ "$vfs" = false ] || send "$vfs_ev" "$ref" "$sha" "$pr"; }; then
    mark "$sha" success "dispatched to artifactory (fallback)"
  else
    mark "$sha" error "dispatch call failed; will retry next run"
    echo "  dispatch failed for ${sha:0:12}" >&2
  fi
  dispatched=$((dispatched + 1))
}

# main head; base = newest earlier main commit that reached artifactory, else fail open.
main_shas=$(mono_api "repos/${MONO_REPO}/commits?sha=main&per_page=${MAIN_LOOKBACK}" --jq '.[].sha')
main_head=$(printf '%s\n' "$main_shas" | head -n1)
main_base=""
for s in $(printf '%s\n' "$main_shas" | tail -n +2); do
  rc=0; handled "$s" >/dev/null || rc=$?
  if [ "$rc" -eq 2 ]; then exit 1; fi
  if [ "$rc" -eq 0 ]; then main_base=$s; break; fi
done
consider main main "$main_head" "$main_base" ""

# open, non-draft, same-repo PRs; base = the PR's base commit.
# Read the list into a variable first: a failed listing (for example a token
# without pull-request read access) must fail this run, not be skipped silently.
if ! prs=$(mono_api --paginate "repos/${MONO_REPO}/pulls?state=open&per_page=100" \
    --jq ".[] | select(.draft == false and .head.repo.full_name == \"${MONO_REPO}\")
          | [.number, .head.ref, .head.sha, .base.sha] | @tsv"); then
  echo "cannot list ${MONO_REPO} pull requests; MONO_TOKEN needs pull-request read access" >&2
  exit 1
fi
# (a here-string, not a pipe, so the per-run cap counter is shared)
while IFS=$'\t' read -r number ref sha base; do
  [ -n "$number" ] || continue
  consider pr "$ref" "$sha" "$base" "$number"
done <<<"$prs"

echo "done (dry_run=${DRY_RUN})"
