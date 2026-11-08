#!/usr/bin/env bash
# Prune Docker build cache and old images only when the filesystem that holds
# Docker's data is genuinely tight. The floor is checked against Docker's data
# root (docker info DockerRootDir), not "/": on a host where Docker's data lives
# on a separate disk, a full "/" cannot be helped by pruning, and checking it
# made every job prune (reclaiming nothing) while racing image pulls and builds
# running beside it.
set -euo pipefail

min_free_gb=${MIN_FREE_GB:-150}

# Anything we cannot read cleanly means "do not prune": a visible warning, and
# the job carries on. Free space is read fresh on every run; nothing is cached.
skip() {
  echo "::warning title=runner-housekeeping::$1; not pruning"
  exit 0
}

if ! root=$(docker info --format '{{.DockerRootDir}}' 2>/dev/null) || [ -z "$root" ]; then
  skip "docker data root unknown (docker info failed)"
fi

if ! df_out=$(df -BG --output=avail "$root" 2>&1); then
  skip "df failed for ${root}"
fi
# Expect a header line and exactly one value such as " 321G".
value=$(printf '%s\n' "$df_out" | tail -n +2)
if ! [[ "$value" =~ ^[[:blank:]]*([0-9]+)G[[:blank:]]*$ ]]; then
  skip "unexpected df output for ${root}: $(printf '%s' "$value" | tr '\n' '|')"
fi
free_gb=${BASH_REMATCH[1]}

echo "free on docker data root ${root}: ${free_gb} GB (floor: ${min_free_gb} GB)"
if [ "$free_gb" -lt "$min_free_gb" ]; then
  echo "below the floor, pruning build cache and images older than 14 days"
  docker builder prune -f --filter 'until=336h' || true
  docker image prune -f --filter 'until=336h' || true
  df -h "$root"
fi
