#!/usr/bin/env bash
# SeaweedFS kernel VFS — one-shot installer. Run as root:
#
#   curl -fsSL https://raw.githubusercontent.com/seaweedfs/artifactory/main/seaweed-vfs/install.sh | sudo bash
#   # or, to also point at a filer, start the daemon and be ready to mount:
#   curl -fsSL https://raw.githubusercontent.com/seaweedfs/artifactory/main/seaweed-vfs/install.sh | sudo FILER=10.0.0.1:18888 bash
#
# It installs prerequisites, fetches the two packages (the GPL module + the
# closed-source daemon) from this repo's release, and DKMS builds the module for
# THIS kernel. Override: SEAWEEDFS_VFS_RELEASE (default: vfs-latest),
# SEAWEEDFS_VFS_BASE_URL, SEAWEEDFS_VFS_KMOD=1 (use a precompiled module
# instead of DKMS — no toolchain).
#
# Upgrade an existing install in place with SEAWEEDFS_VFS_UPGRADE=1: it unmounts
# the seaweedvfs mounts, stops the daemon, swaps the packages, reloads the new
# module, then restarts the daemon and remounts — a brief mount blip, no reboot:
#   curl -fsSL .../install.sh | sudo SEAWEEDFS_VFS_UPGRADE=1 bash
set -euo pipefail

RELEASE="${SEAWEEDFS_VFS_RELEASE:-vfs-latest}"
BASE_URL="${SEAWEEDFS_VFS_BASE_URL:-https://github.com/seaweedfs/artifactory/releases/download/${RELEASE}}"
FILER="${FILER:-}"
UPGRADE="${SEAWEEDFS_VFS_UPGRADE:-}"

die() { echo "install.sh: error: $*" >&2; exit 1; }
[ "$(id -u)" = 0 ] || die "run as root (sudo)"
command -v curl >/dev/null || die "curl is required"

# Detect the package version from the release assets (the dkms deb name embeds it).
VERSION=$(curl -fsSL "https://api.github.com/repos/seaweedfs/artifactory/releases/tags/${RELEASE}" \
  | grep -o '"seaweedfs-vfs-dkms_[0-9][0-9.]*_all\.deb"' \
  | grep -o '[0-9][0-9.]*' | head -1) || true
[ -n "$VERSION" ] || die "could not detect package version from release ${RELEASE}"

case "$(uname -m)" in
  x86_64|amd64) DEB_ARCH=amd64; RPM_ARCH=x86_64 ;;
  aarch64|arm64) DEB_ARCH=arm64; RPM_ARCH=aarch64 ;;
  *) die "unsupported architecture: $(uname -m)" ;;
esac
KVER="$(uname -r)"

if   command -v apt-get >/dev/null; then PM=apt
elif command -v dnf     >/dev/null; then PM=dnf
elif command -v yum     >/dev/null; then PM=yum
elif command -v zypper  >/dev/null; then PM=zypper
else die "no supported package manager (apt/dnf/yum/zypper)"; fi

fetch() { curl -fsSL "$1" -o "$2" || die "download failed: $1"; }
tmp="$(mktemp -d)"; trap 'rm -rf "$tmp"' EXIT

# --- upgrade: quiesce the running install before swapping the module ---
# Reloading a kernel module needs zero users, so unmount the seaweedvfs mounts
# and stop the daemon first, then rmmod. We record source|mountpoint|options and
# the running units so the resume step can restore them after the new .ko lands.
RESUME_MOUNTS=""
RESUME_UNITS=""
if [ -n "$UPGRADE" ]; then
  echo ">> upgrade: quiescing the running install (unmount, stop daemon, rmmod)"
  # Deepest mountpoint first, so nested paths unmount before their parents.
  RESUME_MOUNTS=$(awk '$3=="seaweedvfs"{print length($2)"\t"$1"|"$2"|"$4}' \
    /proc/self/mounts | sort -rn | cut -f2)
  while IFS='|' read -r _src mp _opts; do
    [ -n "$mp" ] || continue
    echo "   umount $mp"
    umount "$mp" || umount -l "$mp" || die "could not unmount $mp (open files?)"
  done <<< "$RESUME_MOUNTS"
  if [ -d /run/systemd/system ]; then
    RESUME_UNITS=$(systemctl list-units --no-legend --state=running \
      'seaweed-vfs*' 2>/dev/null | awk '{print $1}')
    for u in $RESUME_UNITS; do
      echo "   systemctl stop $u"
      systemctl stop "$u" || true
    done
  fi
  pkill -x sw-kd 2>/dev/null || true          # any daemon not under systemd
  if lsmod | grep -q '^seaweedvfs\b'; then
    echo "   rmmod seaweedvfs"
    rmmod seaweedvfs || die "rmmod failed — module still busy"
  fi
fi

# Module source: DKMS (rebuilds per kernel; needs a toolchain) by default, or a
# precompiled .ko for THIS exact kernel when SEAWEEDFS_VFS_KMOD=1 (fleets /
# hardened hosts — no compiler or headers needed, but a package must exist for
# this kernel).
KMOD="${SEAWEEDFS_VFS_KMOD:-}"
if [ -n "$KMOD" ]; then
  echo ">> installing precompiled module for kernel ${KVER} (no toolchain)"
else
  echo ">> installing DKMS prerequisites (toolchain + kernel headers for ${KVER})"
fi
echo ">> packages from ${BASE_URL}"

case "$PM" in
  apt)
    apt-get update -qq || echo ">> warning: apt-get update failed, continuing..."
    if [ -n "$KMOD" ]; then
      apt-get install -y psmisc
      fetch "${BASE_URL}/seaweedfs-vfs-kmod-${KVER}_${DEB_ARCH}.deb" "$tmp/mod.deb"
    else
      apt-get install -y dkms make gcc psmisc "linux-headers-${KVER}" \
        || apt-get install -y dkms make gcc psmisc linux-headers-generic \
        || die "could not install kernel headers for ${KVER}"
      fetch "${BASE_URL}/seaweedfs-vfs-dkms_${VERSION}_all.deb" "$tmp/mod.deb"
    fi
    fetch "${BASE_URL}/seaweedfs-vfs_${VERSION}_${DEB_ARCH}.deb" "$tmp/daemon.deb"
    apt-get install -y "$tmp/mod.deb" "$tmp/daemon.deb"
    ;;
  dnf|yum)
    if [ -n "$KMOD" ]; then
      $PM install -y psmisc
      fetch "${BASE_URL}/seaweedfs-vfs-kmod-${KVER}.${RPM_ARCH}.rpm" "$tmp/mod.rpm"
    else
      $PM install -y dkms make gcc psmisc "kernel-devel-${KVER}" \
        || $PM install -y dkms make gcc psmisc kernel-devel \
        || die "could not install kernel-devel for ${KVER}"
      fetch "${BASE_URL}/seaweedfs-vfs-dkms-${VERSION}.noarch.rpm" "$tmp/mod.rpm"
    fi
    fetch "${BASE_URL}/seaweedfs-vfs-${VERSION}.${RPM_ARCH}.rpm" "$tmp/daemon.rpm"
    $PM install -y "$tmp/mod.rpm" "$tmp/daemon.rpm"
    ;;
  zypper)
    if [ -n "$KMOD" ]; then
      zypper --non-interactive install psmisc
      fetch "${BASE_URL}/seaweedfs-vfs-kmod-${KVER}.${RPM_ARCH}.rpm" "$tmp/mod.rpm"
    else
      zypper --non-interactive install dkms make gcc psmisc kernel-default-devel \
        || die "could not install kernel-default-devel"
      fetch "${BASE_URL}/seaweedfs-vfs-dkms-${VERSION}.noarch.rpm" "$tmp/mod.rpm"
    fi
    fetch "${BASE_URL}/seaweedfs-vfs-${VERSION}.${RPM_ARCH}.rpm" "$tmp/daemon.rpm"
    zypper --non-interactive install --allow-unsigned-rpm "$tmp/mod.rpm" "$tmp/daemon.rpm"
    ;;
esac

modinfo seaweedvfs >/dev/null 2>&1 || die "module not available — $(
  [ -n "$KMOD" ] && echo "no precompiled package for kernel ${KVER}?" \
                 || echo "check 'dkms status' and that headers for ${KVER} are present")"
echo ">> seaweedvfs module v$(modinfo -F version seaweedvfs 2>/dev/null) ready for ${KVER}"

if [ -n "$UPGRADE" ]; then
  # --- upgrade: reload the new module, then restore daemon + mounts ---
  echo ">> upgrade: reloading module and restoring daemon + mounts"
  modprobe seaweedvfs || die "modprobe seaweedvfs failed after upgrade"
  for u in $RESUME_UNITS; do
    echo "   systemctl start $u"
    systemctl start "$u" || echo ">> warning: could not start $u"
  done
  # Shallowest mountpoint first (reverse of the unmount order).
  while IFS='|' read -r src mp opts; do
    [ -n "$mp" ] || continue
    echo "   mount $mp"
    mount "$mp" 2>/dev/null \
      || mount -t seaweedvfs -o "$opts" "$src" "$mp" \
      || echo ">> warning: could not remount $mp — remount it by hand"
  done <<< "$(echo "$RESUME_MOUNTS" | tac)"
  echo ">> upgrade complete: v${VERSION} active on ${KVER}"
elif [ -n "$FILER" ]; then
  echo ">> configuring filer ${FILER} and starting the daemon"
  mkdir -p /etc/seaweedfs-vfs
  touch /etc/seaweedfs-vfs/config
  # awk with a literal -v value (sed would mangle & or \ in the filer address).
  awk -v new="FILER=${FILER}" '
    /^FILER=/ { print new; found=1; next }
    { print }
    END { if (!found) print new }
  ' /etc/seaweedfs-vfs/config > "$tmp/config" && cp "$tmp/config" /etc/seaweedfs-vfs/config
  systemctl enable seaweed-vfs.service || true
  if [ -d /run/systemd/system ]; then
    systemctl start seaweed-vfs.service \
      || echo ">> warning: could not start seaweed-vfs.service (is the filer reachable?)"
  fi
  echo ">> Mount with:  mkdir -p /mnt/seaweed && mount -t seaweedvfs none /mnt/seaweed"
else
  echo ">> next: set FILER in /etc/seaweedfs-vfs/config, then"
  echo "   systemctl enable --now seaweed-vfs.service && mount -t seaweedvfs none /mnt/seaweed"
fi

if command -v mokutil >/dev/null 2>&1 && mokutil --sb-state 2>/dev/null | grep -qi enabled; then
  echo ">> NOTE: Secure Boot is ON — the module must be signed and its key enrolled"
  echo "   (DKMS signs with a local MOK key; mokutil --import <key>; reboot) to load."
fi
echo ">> done."
