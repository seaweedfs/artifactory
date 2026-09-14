#!/usr/bin/env bash
set -u
export PATH=/opt/work/codex02-coverage-tools/bin:/home/testdev/.cargo/bin:/usr/local/go/bin:/usr/local/bin:/usr/bin:/bin
export CARGO_BUILD_JOBS=2 CARGO_TARGET_DIR=/opt/work/codex02-coverage-target
root="${SWEEP_ROOT:?}/coverage-retained"
mkdir -p "$root"
exec 9>/mnt/smb/work/share/testops/locks/rdma-lab.lock
flock -n 9 || { echo LAB_LOCK_BUSY; exit 3; }
printf 'START codex02 integration coverage ac7f4e0f0 %s\n' "$(date -u +%FT%TZ)" >> /mnt/smb/work/share/testops/WHO-IS-RUNNING
trap 'printf "END codex02 integration coverage ac7f4e0f0 %s\n" "$(date -u +%FT%TZ)" >> /mnt/smb/work/share/testops/WHO-IS-RUNNING' EXIT
cp /opt/work/codex02-sweep2-product/enterprise/rust/Cargo.lock "$root/workspace.lock.before"
cp /opt/work/codex02-sweep2-product/enterprise/seaweed-volume/Cargo.lock "$root/volume.lock.before"
finish_coverage() {
 cp /opt/work/codex02-sweep2-product/enterprise/rust/Cargo.lock "$root/workspace.lock.resolved"
 cp /opt/work/codex02-sweep2-product/enterprise/seaweed-volume/Cargo.lock "$root/volume.lock.resolved"
 cp "$root/workspace.lock.before" /opt/work/codex02-sweep2-product/enterprise/rust/Cargo.lock
 cp "$root/volume.lock.before" /opt/work/codex02-sweep2-product/enterprise/seaweed-volume/Cargo.lock
 printf 'END codex02 integration coverage %s\n' "$(date -u +%FT%TZ)" >> /mnt/smb/work/share/testops/WHO-IS-RUNNING
}
trap finish_coverage EXIT
archive_profile() {
 profile_name="$1"
 artifact="$root/$profile_name-artifacts"
 mkdir -p "$artifact"
 find "$CARGO_TARGET_DIR" -type f \( -name '*.profraw' -o -name '*.profdata' \) -exec cp --parents '{}' "$artifact" \;
 python3 /opt/work/codex02-retain-coverage-profile.py "$root" "$profile_name" || exit 2
 find "$artifact" -type f -exec sha256sum '{}' \; > "$root/$profile_name-artifacts.sha256"
 cargo tree -e features "${extra[@]}" >"$root/$profile_name-resolved-tree.txt" 2>&1
}
{ rustc -Vv; cargo -V; cargo llvm-cov --version; date -u +%FT%TZ; uname -a; } > "$root/toolchain.txt"
cd /opt/work/codex02-sweep2-product/enterprise/rust
for profile in default rdma; do
 extra=()
 if [ "$profile" = rdma ]; then extra=(--features seaweedfs-sw-rdma/mlx5-dc); fi
 timeout --signal=TERM --kill-after=30s 30m cargo llvm-cov --workspace --lib "${extra[@]}" --json --output-path "$root/workspace-$profile.json" >"$root/workspace-$profile.log" 2>&1
 printf 'workspace-%s exit=%s\n' "$profile" "$?" | tee -a "$root/exits.txt"
 archive_profile "workspace-$profile"
done
cd /opt/work/codex02-sweep2-product/enterprise/seaweed-volume
for profile in default rdma rdma-dc,kvcache; do
 extra=()
 if [ "$profile" != default ]; then extra=(--features "$profile"); fi
 timeout --signal=TERM --kill-after=30s 30m cargo llvm-cov --lib "${extra[@]}" --json --output-path "$root/volume-$profile.json" >"$root/volume-$profile.log" 2>&1
 printf 'volume-%s exit=%s\n' "$profile" "$?" | tee -a "$root/exits.txt"
 archive_profile "volume-$profile"
done