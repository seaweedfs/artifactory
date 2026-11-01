#!/usr/bin/env bash
#
# Tag the seaweed-mono commit an enterprise release was built from, and write
# that release's change log into the artifactory release body.
#
# OSS releases get GitHub's generated "What's Changed" list. Enterprise
# releases build seaweed-mono main into binaries published here, so the list
# has to come from seaweed-mono: each release tags the commit it built as
# enterprise-<version>, and the notes list the seaweed-mono PRs merged between
# the previous enterprise-* tag and this one.
#
# This repository is public and seaweed-mono is private, so the list keeps
# only PR titles: no links (they 404 here) and no authors. PRs that do not
# change the enterprise product (docs, seaweed-vfs, the website) are left out.
# The OSS changes are the OSS release's notes; the section links to them.
#
# Environment:
#   TAG           release tag being published (e.g. 4.52 or 4.52.1)
#   MONO_SHA      the seaweed-mono commit the binaries were built from
#   MONO_TOKEN    token for seaweedfs/seaweed-mono; tagging needs
#                 contents:write, the notes alone need read
#   GH_TOKEN      token that can edit this repository's releases
#   GH_REPO       this repository (owner/name), as Actions sets it
#   BUILD_RESULT  result of the build jobs (default success)
#   PREVIOUS_TAG  optional: seaweed-mono tag to list changes from, instead of
#                 the latest enterprise-* tag below this release
#   DRY_RUN=1     print the notes; create no tag and edit no release

set -euo pipefail

MONO_REPO=seaweedfs/seaweed-mono
OSS_REPO=seaweedfs/seaweedfs
START='<!-- enterprise-release-notes:start -->'
END='<!-- enterprise-release-notes:end -->'

: "${TAG:?}" "${MONO_SHA:?}" "${MONO_TOKEN:?}"
DRY_RUN="${DRY_RUN:-}"

version="${TAG#v}"
if [[ ! "$version" =~ ^[0-9]+\.[0-9]+(\.[0-9]+)?$ ]]; then
  echo "Tag '${TAG}' is not a MAJOR.MINOR[.PATCH] release version; no enterprise notes."
  exit 0
fi
if [[ ! "$MONO_SHA" =~ ^[0-9a-f]{40}$ ]]; then
  echo "::error::MONO_SHA '${MONO_SHA}' is not a full commit sha"
  exit 1
fi
mono_tag="enterprise-${version}"

mono() { GH_TOKEN="$MONO_TOKEN" gh api "$@"; }

# --- tag the built commit in seaweed-mono ----------------------------------
# A re-run of a release rebuilds from the main of that moment and overwrites
# the assets, so the tag follows the binaries that are published.
tagged=
if [ -n "$DRY_RUN" ]; then
  echo "dry run: would tag ${MONO_REPO} ${mono_tag} at ${MONO_SHA}"
else
  current=$(mono "repos/${MONO_REPO}/git/matching-refs/tags/${mono_tag}" \
    --jq ".[] | select(.ref == \"refs/tags/${mono_tag}\") | .object.sha")
  # Failed legs keep their assets from the commit the tag names, so neither
  # moves until the failed jobs are re-run from this commit.
  if [ "${BUILD_RESULT:-success}" != success ] && [ -n "$current" ] && [ "$current" != "$MONO_SHA" ]; then
    echo "::warning::some ${TAG} builds failed and their assets are still from ${current}; leaving ${mono_tag} and the notes as they are. Re-run the failed jobs to finish ${TAG} from ${MONO_SHA}."
    exit 0
  fi
  if [ "$current" = "$MONO_SHA" ]; then
    echo "${MONO_REPO} ${mono_tag} already at ${MONO_SHA}"
    tagged=1
  elif [ -n "$current" ]; then
    echo "::warning::moving ${MONO_REPO} ${mono_tag} from ${current} to ${MONO_SHA}: this run rebuilt ${TAG} from a newer commit"
    mono -X PATCH "repos/${MONO_REPO}/git/refs/tags/${mono_tag}" \
      -f sha="$MONO_SHA" -F force=true >/dev/null && tagged=1
  else
    mono -X POST "repos/${MONO_REPO}/git/refs" \
      -f ref="refs/tags/${mono_tag}" -f sha="$MONO_SHA" >/dev/null && tagged=1
  fi
  # Without the tag the notes below still work (they name the commit), but
  # the next release would have no starting point, so say so loudly.
  if [ -z "$tagged" ]; then
    echo "::warning::could not tag ${MONO_REPO} ${mono_tag}; MONO_TOKEN needs contents:write there. The next release's notes will need PREVIOUS_TAG."
  fi
fi

# --- pick the previous enterprise release -----------------------------------
previous="${PREVIOUS_TAG:-}"
if [ -z "$previous" ]; then
  previous=$(mono --paginate "repos/${MONO_REPO}/git/matching-refs/tags/enterprise-" \
    --jq '.[].ref | ltrimstr("refs/tags/")' |
    { grep -E '^enterprise-[0-9]+\.[0-9]+(\.[0-9]+)?$' || true; } |
    { cat; echo "$mono_tag"; } | sort -uV |
    awk -v cur="$mono_tag" '$0 == cur { print prev; exit } { prev = $0 }')
fi

# --- list the enterprise PRs since then -------------------------------------
# A PR counts when it targets the enterprise branch or changes the enterprise
# product: enterprise/ on main, or the root layout (weed/, seaweed-volume/...)
# that only branches cut from enterprise have.
# Called as an if condition, where set -e does not apply, so a failed lookup
# exits explicitly instead of leaving the PR out.
is_enterprise_pr() {
  local pr=$1 base files
  base=$(mono "repos/${MONO_REPO}/pulls/${pr}" --jq .base.ref) || exit 1
  [ "$base" = enterprise ] && return 0
  # captured first: grep -q quitting early would SIGPIPE gh, and pipefail
  # would then report a match as a miss
  files=$(mono --paginate "repos/${MONO_REPO}/pulls/${pr}/files" --jq '.[].filename') || exit 1
  grep -qE '^(enterprise|weed|seaweed-volume|seaweed-worker|seaweed-common|seaweed-vector|rust|sw-block|docker|k8s)/' <<<"$files"
}

# Plain text for a public page: #123 and @name would link to this
# repository's issues and to people, neither of which is meant.
plain_title() {
  sed -E 's/#([0-9]+)/`#\1`/g; s/@([A-Za-z0-9][A-Za-z0-9-]*)/`@\1`/g'
}

changes=
skipped=0
if [ -n "$previous" ]; then
  generated=$(mono -X POST "repos/${MONO_REPO}/releases/generate-notes" \
    -f tag_name="$mono_tag" -f previous_tag_name="$previous" \
    -f target_commitish="$MONO_SHA" --jq .body)
  line_re="^\* (.+) by @[^ ]+ in https://github\.com/${MONO_REPO}/pull/([0-9]+)$"
  while IFS= read -r line; do
    [[ "$line" =~ $line_re ]] || continue
    title="${BASH_REMATCH[1]}" pr="${BASH_REMATCH[2]}"
    if is_enterprise_pr "$pr"; then
      changes+="* $(plain_title <<<"$title")"$'\n'
    else
      skipped=$((skipped + 1))
    fi
  done <<<"$generated"
fi

# --- compose the section ----------------------------------------------------
oss_line=
if GH_TOKEN="${GH_TOKEN:-$MONO_TOKEN}" gh api "repos/${OSS_REPO}/releases/tags/${version}" --jq .html_url >/dev/null 2>&1; then
  oss_line="Includes everything in [SeaweedFS ${version}](https://github.com/${OSS_REPO}/releases/tag/${version})."
fi

section=$(
  echo "$START"
  echo "## SeaweedFS Enterprise ${version}"
  echo
  echo "Built from seaweed-mono \`${MONO_SHA:0:8}\`. ${oss_line}"
  echo
  if [ -z "$previous" ]; then
    echo "No earlier \`enterprise-*\` tag to list enterprise changes from."
  elif [ -z "$changes" ]; then
    echo "No enterprise changes since ${previous#enterprise-}."
  else
    echo "### Enterprise changes since ${previous#enterprise-}"
    echo
    printf '%s' "$changes"
  fi
  echo "$END"
)

if [ -n "$DRY_RUN" ]; then
  echo "dry run: previous=${previous:-none}, ${skipped} non-enterprise PRs left out"
  echo "----"
  echo "$section"
  exit 0
fi

# --- write it into the release ----------------------------------------------
# Replace an earlier copy of the section and keep anything else the release
# says, so hand-written notes survive a re-run. A body saved from the web
# editor has CRLF line ends.
body=$(gh release view "$TAG" --json body --jq .body)
rest=$(awk -v s="$START" -v e="$END" '{ sub(/\r$/, "") } $0 == s { skip = 1; next } $0 == e { skip = 0; next } !skip' <<<"$body" | sed '/./,$!d')
notes=$(mktemp)
{
  echo "$section"
  if [ -n "${rest//[[:space:]]/}" ]; then
    echo
    echo "$rest"
  fi
} >"$notes"
gh release edit "$TAG" --notes-file "$notes" >/dev/null

{
  echo "### Enterprise release notes"
  echo "- seaweed-mono \`${mono_tag}\` → \`${MONO_SHA}\`${tagged:+ (tagged)}"
  echo "- changes listed since \`${previous:-none}\`; ${skipped} non-enterprise PRs left out"
} >>"${GITHUB_STEP_SUMMARY:-/dev/null}"
echo "Wrote enterprise notes for ${TAG} (since ${previous:-none})."
