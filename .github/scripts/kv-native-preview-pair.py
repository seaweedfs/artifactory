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


def create(directory, sha, expected_servers):
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
    return {"schema": "seaweedkv-preview-pair/v1", "mono_commit": sha, "assets": assets}


def verify(directory, manifest):
    if manifest.get("schema") != "seaweedkv-preview-pair/v1" or set(manifest.get("assets", {})) != set(NAMES):
        raise ValueError("incomplete pairing manifest")
    for name, expected in manifest["assets"].items():
        path = directory / name
        if digest(path) != expected or source(path, name == PREVIEW) != manifest["mono_commit"]:
            raise ValueError("publication pairing mismatch: " + name)


if __name__ == "__main__":
    mode, root, *args = sys.argv[1:]
    directory = pathlib.Path(root)
    if mode == "create":
        manifest = create(directory, args[0], json.loads(args[1]))
        verify(directory, manifest)
        (directory / "seaweedkv-preview-pair.json").write_text(json.dumps(manifest, indent=2) + "\n")
    elif mode == "verify":
        verify(directory, json.loads(pathlib.Path(args[0]).read_text()))
    else:
        raise ValueError("expected create or verify")
