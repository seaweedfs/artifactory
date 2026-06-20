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
# Re-running over an existing install upgrades it in place automatically, and
# reloads only what changed:
#   * only the daemon changed  -> restart the daemon under the live mounts; the
#     module completes in-flight requests with -ENOTCONN and the daemon
#     transparently re-attaches (no unmount, just a brief I/O pause).
#   * the kernel module changed -> full reload (unmount, rmmod, modprobe,
#     remount) because a loaded module can't be swapped with users on it.
# Force the choice with SEAWEEDFS_VFS_UPGRADE=1 (always upgrade) or =0 (treat as
# a fresh install).
set -euo pipefail

RELEASE="${SEAWEEDFS_VFS_RELEASE:-vfs-latest}"
BASE_URL="${SEAWEEDFS_VFS_BASE_URL:-https://github.com/seaweedfs/artifactory/releases/download/${RELEASE}}"
FILER="${FILER:-}"
UPGRADE_REQ="${SEAWEEDFS_VFS_UPGRADE:-auto}"

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

# --- upgrade detection + state snapshot ---
# Decide upgrade mode (auto-detect an existing install unless forced) and record
# what we might have to restore. We install the new packages FIRST — that only
# stages the new .ko on disk and replaces the daemon binary, leaving the running
# module and mounts untouched — then compare the module's srcversion to decide
# whether a disruptive module reload is actually needed.
have_install() {
  [ -e /sys/module/seaweedvfs ] && return 0
  awk '$3=="seaweedvfs"{f=1} END{exit !f}' /proc/self/mounts 2>/dev/null && return 0
  [ -d /run/systemd/system ] \
    && systemctl list-units --all --no-legend 'seaweed-vfs*' 2>/dev/null | grep -q . \
    && return 0
  return 1
}
case "$UPGRADE_REQ" in
  1|yes|true)  UPGRADE=1 ;;
  0|no|false)  UPGRADE= ;;
  *)           if have_install; then UPGRADE=1; else UPGRADE=; fi ;;
esac

# srcversion of the currently loaded module (empty if not loaded) — compared to
# the freshly installed .ko after the package swap.
OLD_SRCVERSION=""
[ -r /sys/module/seaweedvfs/srcversion ] && OLD_SRCVERSION=$(cat /sys/module/seaweedvfs/srcversion)

# Snapshot live mounts (source|mountpoint|options) + running daemon units, for
# whichever restore path we take. Keep both orderings up front so we never need
# the non-POSIX `tac`: deepest-first to unmount, shallowest-first to remount.
RESUME_MOUNTS=$(awk '$3=="seaweedvfs"{print length($2)"\t"$1"|"$2"|"$4}' \
  /proc/self/mounts 2>/dev/null | sort -rn | cut -f2)
RESUME_MOUNTS_SHALLOW=$(awk '$3=="seaweedvfs"{print length($2)"\t"$1"|"$2"|"$4}' \
  /proc/self/mounts 2>/dev/null | sort -n | cut -f2)
RESUME_UNITS=""
if [ -d /run/systemd/system ]; then
  # || true: systemctl exits non-zero if it can't reach systemd (e.g. inside a
  # container, or a fresh install with SEAWEEDFS_VFS_UPGRADE=0); under
  # set -euo pipefail that would otherwise abort the whole installer here.
  RESUME_UNITS=$(systemctl list-units --no-legend --state=running \
    'seaweed-vfs*' 2>/dev/null | awk '{print $1}') || true
fi

[ -n "$UPGRADE" ] && echo ">> existing install detected — upgrading in place"

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
  NEW_SRCVERSION=$(modinfo -F srcversion seaweedvfs 2>/dev/null || true)
  if [ -n "$OLD_SRCVERSION" ] && [ "$OLD_SRCVERSION" = "$NEW_SRCVERSION" ]; then
    # --- module unchanged: restart the daemon only, mounts stay up ---
    echo ">> kernel module unchanged (srcversion ${NEW_SRCVERSION}) — restarting daemon only"
    if [ -n "$RESUME_UNITS" ]; then
      for u in $RESUME_UNITS; do
        echo "   systemctl restart $u"
        systemctl restart "$u" || echo ">> warning: could not restart $u"
      done
      echo ">> upgrade complete: daemon v${VERSION} active, mounts preserved"
    elif [ -n "$RESUME_MOUNTS" ]; then
      # Active mounts but no systemd unit: an unmanaged sw-kd is still serving
      # them from the OLD binary, and we don't know how it was launched so we
      # can't restart it. The new binary is staged but NOT live — don't claim
      # success. (A full reload would be no better: it can't start the daemon
      # either, and would leave the remount with nothing serving it.)
      die "active seaweedvfs mounts are served by an unmanaged sw-kd daemon; the new v${VERSION} binary is installed but the running daemon is unchanged. Restart your daemon by hand (or unmount and re-run) to finish the upgrade."
    else
      echo ">> new daemon binary v${VERSION} installed; no running daemon to restart"
    fi
  else
    # --- module changed: full reload (needs zero users on the module) ---
    echo ">> kernel module changed — full reload (brief unmount)"
    while IFS='|' read -r _src mp _opts; do
      [ -n "$mp" ] || continue
      echo "   umount $mp"
      umount "$mp" || umount -l "$mp" || die "could not unmount $mp (open files?)"
    done <<< "$RESUME_MOUNTS"
    for u in $RESUME_UNITS; do
      echo "   systemctl stop $u"
      systemctl stop "$u" || true
    done
    pkill -x sw-kd 2>/dev/null || true          # any daemon not under systemd
    if grep -q '^seaweedvfs\b' /proc/modules 2>/dev/null; then
      echo "   rmmod seaweedvfs"
      rmmod seaweedvfs || die "rmmod failed — module still busy"
    fi
    modprobe seaweedvfs || die "modprobe seaweedvfs failed after upgrade"
    for u in $RESUME_UNITS; do
      echo "   systemctl start $u"
      systemctl start "$u" || echo ">> warning: could not start $u"
    done
    # Shallowest mountpoint first (parents before nested children).
    while IFS='|' read -r src mp opts; do
      [ -n "$mp" ] || continue
      echo "   mount $mp"
      mount "$mp" 2>/dev/null \
        || mount -t seaweedvfs -o "$opts" "$src" "$mp" \
        || echo ">> warning: could not remount $mp — remount it by hand"
    done <<< "$RESUME_MOUNTS_SHALLOW"
    echo ">> upgrade complete: v${VERSION} active on ${KVER}"
  fi
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
