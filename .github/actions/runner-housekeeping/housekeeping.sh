#!/usr/bin/env bash
# Prune Docker build cache and old images only when the filesystem that holds
# Docker's data is genuinely tight. The floor is checked against Docker's data
# root (docker info DockerRootDir), not "/": on a host where Docker's data lives
# on a separate disk, a full "/" cannot be helped by pruning, and checking it
# made every job prune (reclaiming nothing) while racing image pulls and builds
# running beside it.
set -euo pipefail

min_free_gb=${MIN_FREE_GB:-150}

if ! root=$(docker info --format '{{.DockerRootDir}}' 2>/dev/null) || [ -z "$root" ]; then
  echo "docker data root unknown (docker info failed); not pruning"
  exit 0
fi

free_gb=$(df -BG --output=avail "$root" | tail -1 | tr -dc '0-9')
if [ -z "$free_gb" ]; then
  echo "could not read free space for ${root}; not pruning"
  exit 0
fi

echo "free on docker data root ${root}: ${free_gb} GB (floor: ${min_free_gb} GB)"
if [ "$free_gb" -lt "$min_free_gb" ]; then
  echo "below the floor, pruning build cache and images older than 14 days"
  docker builder prune -f --filter 'until=336h' || true
  docker image prune -f --filter 'until=336h' || true
  df -h "$root"
fi
