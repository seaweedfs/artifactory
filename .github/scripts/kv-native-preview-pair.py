#!/usr/bin/env python3
"""Checked source/digest selection; tag names alone never establish a pair."""
import hashlib
import json
import pathlib
import re
import sys
import tarfile

SERVER = "weed-volume-enterprise_rdma_large_disk_linux_amd64.tar.gz"
OBJECT = "sw-rdma-object-service-enterprise_linux_amd64.tar.gz"
PREVIEW = "seaweedkv-lmcache-native-preview_cp312_linux_amd64.tar.gz"
NAMES = (SERVER, OBJECT, PREVIEW)
# The standalone wheel published beside the bundle. Its bytes must be the ones the paired preview bundle's
# BUILD-MANIFEST.json lists, so a replaced or stale standalone wheel (even with its own valid .sha256) never pairs.
WHEEL = re.compile(r"seaweedkv_native-[0-9]+\.[0-9]+\.[0-9]+-cp312-cp312-linux_x86_64\.whl")


def digest(path):
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def source(path, preview=False):
    with tarfile.open(path, "r:gz") as archive:
        name = "BUILD-MANIFEST.json" if preview else "source-commit.txt"
        members = [m for m in archive.getmembers() if m.name.removeprefix("./") == name]
        if len(members) != 1 or not members[0].isfile() or members[0].size > 1024*1024:
            raise ValueError("missing/oversized source receipt: " + path.name)
        raw = archive.extractfile(members[0]).read()
        return json.loads(raw)["mono_commit"] if preview else raw.decode().strip()


def standalone(directory):
    """-> (name, sha256) of the one standalone wheel, bound to the preview BUILD-MANIFEST.json wheels entry."""
    found = sorted(p.name for p in directory.iterdir() if WHEEL.fullmatch(p.name))
    if len(found) != 1:
        raise ValueError(f"expected exactly one standalone seaweedkv_native wheel, found {found}")
    name = found[0]
    with tarfile.open(directory / PREVIEW, "r:gz") as archive:
        members = [m for m in archive.getmembers() if m.name.removeprefix("./") == "BUILD-MANIFEST.json"]
        if len(members) != 1 or not members[0].isfile() or members[0].size > 1024*1024:
            raise ValueError("missing/oversized source receipt: " + PREVIEW)
        listed = [w for w in json.loads(archive.extractfile(members[0]).read()).get("wheels", [])
                  if w.get("name") == name]
    if len(listed) != 1 or not re.fullmatch(r"[0-9a-f]{64}", str(listed[0].get("sha256"))):
        raise ValueError("standalone wheel not listed once in the preview BUILD-MANIFEST: " + name)
    want = listed[0]["sha256"]
    if digest(directory / name) != want:
        raise ValueError("standalone wheel bytes differ from the preview BUILD-MANIFEST: " + name)
    sidecar = (directory / (name + ".sha256")).read_text(encoding="utf-8").split()
    if not sidecar or sidecar[0] != want:
        raise ValueError("standalone wheel .sha256 differs from the preview BUILD-MANIFEST: " + name)
    return name, want


def create(directory, sha, expected_servers, wheel=False):
    if not re.fullmatch(r"[0-9a-f]{40}", sha) or set(expected_servers) != {SERVER, OBJECT}:
        raise ValueError("invalid source/server digest contract")
    assets = {}
    for name in NAMES:
        path = directory / name
        if source(path, name == PREVIEW) != sha:
            raise ValueError("source pairing mismatch: " + name)
        assets[name] = digest(path)
        if name != PREVIEW and assets[name] != expected_servers[name]:
            raise ValueError("server digest mismatch: " + name)
    manifest = {"schema": "seaweedkv-preview-pair/v1", "mono_commit": sha, "assets": assets}
    if wheel:
        name, wheel_sha = standalone(directory)
        manifest["wheel"] = {"name": name, "sha256": wheel_sha}
    return manifest


def verify(directory, manifest, wheel=False):
    if manifest.get("schema") != "seaweedkv-preview-pair/v1" or set(manifest.get("assets", {})) != set(NAMES):
        raise ValueError("incomplete pairing manifest")
    for name, expected in manifest["assets"].items():
        path = directory / name
        if digest(path) != expected or source(path, name == PREVIEW) != manifest["mono_commit"]:
            raise ValueError("publication pairing mismatch: " + name)
    if wheel and "wheel" not in manifest:
        raise ValueError("pairing manifest does not pin a standalone wheel")
    if "wheel" in manifest:   # a pinned wheel is always checked
        if standalone(directory) != (manifest["wheel"].get("name"), manifest["wheel"].get("sha256")):
            raise ValueError("publication pairing mismatch: standalone wheel")


if __name__ == "__main__":
    mode, root, *args = sys.argv[1:]
    directory = pathlib.Path(root)
    with_wheel = "--wheel" in args   # create: pin the standalone wheel; verify: require a pinned, matching one
    args = [a for a in args if a != "--wheel"]
    if mode == "create":
        manifest = create(directory, args[0], json.loads(args[1]), wheel=with_wheel)
        verify(directory, manifest, wheel=with_wheel)
        (directory / "seaweedkv-preview-pair.json").write_text(json.dumps(manifest, indent=2) + "\n")
    elif mode == "verify":
        verify(directory, json.loads(pathlib.Path(args[0]).read_text()), wheel=with_wheel)
    else:
        raise ValueError("expected create or verify")
