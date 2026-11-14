#!/usr/bin/env python3
"""Build and check the swctl KV release directory: manifest.json (swctl.release-manifest/v1) and the files it binds.

  kv-release-manifest.py create DIR TAG VERSION SOURCE_SHA GO_VERSION LMCACHE_COMMIT CONSTANTS_JSON
  kv-release-manifest.py verify DIR
  kv-release-manifest.py assets TAG ASSET_NAMES_FILE
  kv-release-manifest.py extract-lib WHEEL LIB OUT              (the native library's bytes from inside the wheel)
  kv-release-manifest.py check-cluster TEMPLATE DIR CHECK_DIR   (prints the cluster template filled for a check)

DIR holds weed, weed-volume, the native library and the seaweedkv_native and lmcache wheels. create writes
DIR/manifest.json with their real sha256 and then verifies it. verify applies the rules swctl install uses to read a
release directory (enterprise/internal/swctl/manifest.go, lifecycle_artifacts.go): each artifact name exactly once,
binaries at their exact names, wheels matched by sha256 (at most 128, no two alike), the native library at its name,
all regular files, and no placeholder ("EXAMPLE" or all-zero) value. It also requires the native library to be the
same bytes as the copy inside the seaweedkv_native wheel. assets checks a release carries the bundle and manifest.
Named errors: KV_RELEASE_INPUT, KV_RELEASE_INVALID, KV_RELEASE_MISSING. Exit 0 on success, else 1.
"""
import hashlib
import json
import pathlib
import re
import sys
import zipfile

BUNDLE = "swctl-kv-release_linux_amd64.tar.gz"
MANIFEST_ASSET = "swctl-kv-release_linux_amd64.manifest.json"
SHA = re.compile(r"[0-9a-f]{64}")
VERSION = re.compile(r"[0-9]+(\.[0-9]+){1,3}([.+-][0-9A-Za-z.]+)?")
NATIVE_WHEEL = re.compile(r"seaweedkv_native-([0-9][^-]*)-(cp3[0-9]+)-(cp3[0-9]+)-([a-z0-9_]+)\.whl")
MAX_WHEELS = 128


def digest(path):
    h = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def expected_assets():
    return [n for d in (BUNDLE, MANIFEST_ASSET) for n in (d, d + ".sha256")]


def wheel_member(wheel, lib):
    """-> bytes of <package>/<lib> inside the wheel (exactly one such member)."""
    with zipfile.ZipFile(wheel) as z:
        names = [n for n in z.namelist() if n.rsplit("/", 1)[-1] == lib]
        if len(names) != 1:
            raise ValueError(f"KV_RELEASE_INPUT: {wheel.name} holds {len(names)} copies of {lib}, expected 1")
        return z.read(names[0])


def regular(path):
    if not path.is_file() or path.is_symlink():
        raise ValueError(f"KV_RELEASE_INPUT: {path.name} is missing or not a regular file")
    return path


def create(directory, tag, version, source_sha, go_version, lmcache_commit, constants):
    if not VERSION.fullmatch(version) or not re.fullmatch(r"[0-9a-f]{40}", source_sha):
        raise ValueError("KV_RELEASE_INPUT: version must be MAJOR.MINOR.PATCH and source a 40-hex commit")
    if not re.fullmatch(r"go[0-9]+\.[0-9]+(\.[0-9]+)?", go_version) or not re.fullmatch(r"[0-9a-f]{7,40}", lmcache_commit):
        raise ValueError("KV_RELEASE_INPUT: bad go version or LMCache header commit")
    native = sorted(p for p in directory.iterdir() if NATIVE_WHEEL.fullmatch(p.name))
    if len(native) != 1:
        raise ValueError(f"KV_RELEASE_INPUT: expected one seaweedkv_native wheel, found {[p.name for p in native]}")
    m = NATIVE_WHEEL.fullmatch(native[0].name)
    if m.group(1) != version:
        raise ValueError(f"KV_RELEASE_INPUT: {native[0].name} is not version {version}")
    lib = regular(directory / constants["native_lib"])
    if wheel_member(native[0], lib.name) != lib.read_bytes():
        raise ValueError(f"KV_RELEASE_INPUT: {lib.name} differs from the copy inside {native[0].name}")
    pin = constants["lmcache_wheel"]
    lmcache = regular(directory / pin["name"])
    if digest(lmcache) != pin["sha256"]:
        raise ValueError(f"KV_RELEASE_INPUT: {lmcache.name} sha256 differs from the pinned {pin['sha256']}")
    artifacts = [
        {"name": "weed", "kind": "binary", "edition": "enterprise", "version": version, "go_version": go_version,
         "build_tags": list(constants["weed_build_tags"]), "sha256": digest(regular(directory / "weed"))},
        {"name": "weed-volume", "kind": "binary", "version": version,
         "features": list(constants["weed_volume_features"]), "sha256": digest(regular(directory / "weed-volume"))},
        {"name": "seaweedkv_native", "kind": "python-wheel", "version": version,
         "python_abi": f"{m.group(2)}-{m.group(4)}", "sha256": digest(native[0]),
         "native_lib": {"name": lib.name, "sha256": digest(lib)}},
        {"name": "lmcache", "kind": "python-wheel", "version": pin["version"], "sha256": pin["sha256"],
         "c_header_revision": lmcache_commit},
    ]
    return {
        "schema": "swctl.release-manifest/v1",
        "bundle": constants["bundle_prefix"] + tag,
        "profile": "kv",
        "ga_criteria_revision": constants["ga_criteria_revision"],
        "source": {"repo": "seaweedfs/seaweed-mono", "commit": source_sha},
        "artifacts": artifacts,
        "supported_os": constants["supported_os"],
        "upgrade_edges_ref": f"{constants['upgrade_edges_ref']}@{source_sha[:8]}",
        "note": f"SeaweedFS enterprise release {tag}: every sha256 is the published file's real digest.",
    }


def verify(directory, raw=None):
    """-> list of problems; [] when swctl's release-directory rules hold for DIR/manifest.json."""
    raw = (directory / "manifest.json").read_bytes() if raw is None else raw
    found = []
    if len(raw) > 64 * 1024:
        return ["manifest.json is over 64 KiB"]
    try:
        manifest = json.loads(raw, object_pairs_hook=_no_duplicates)
    except ValueError as e:
        return [f"manifest.json: {e}"]
    if b'"EXAMPLE"' in raw or b"0" * 64 in raw:
        found.append("manifest.json holds a placeholder (EXAMPLE or an all-zero sha256)")
    for key in ("schema", "bundle", "profile", "ga_criteria_revision", "source", "artifacts", "supported_os",
                "upgrade_edges_ref", "note"):
        if not manifest.get(key):
            found.append(f"manifest.json lacks {key}")
    if manifest.get("schema") != "swctl.release-manifest/v1" or manifest.get("profile") != "kv":
        found.append("manifest.json is not a swctl.release-manifest/v1 kv manifest")
    arts = manifest.get("artifacts") or []
    by_name = {}
    for a in arts:
        by_name.setdefault(a.get("name"), []).append(a)
    for name in ("weed", "weed-volume", "seaweedkv_native", "lmcache"):
        if len(by_name.get(name, [])) != 1:
            found.append(f"artifact {name} appears {len(by_name.get(name, []))} times, expected once")
    if set(by_name) - {"weed", "weed-volume", "seaweedkv_native", "lmcache"}:
        found.append(f"unknown artifacts {sorted(set(by_name) - {'weed', 'weed-volume', 'seaweedkv_native', 'lmcache'})}")
    wheels = sorted(p for p in directory.glob("*.whl"))
    if len(wheels) > MAX_WHEELS:
        found.append(f"{len(wheels)} wheels, more than {MAX_WHEELS}")
    wheel_by_sha = {}
    for w in wheels:
        wheel_by_sha.setdefault(digest(w), []).append(w.name)
    for sha, names in wheel_by_sha.items():
        if len(names) > 1:
            found.append(f"wheels {names} share one sha256")
    for a in arts:
        sha = a.get("sha256", "")
        if not SHA.fullmatch(sha):
            found.append(f"artifact {a.get('name')}: sha256 is not 64 hex")
            continue
        if a.get("kind") == "binary":
            path = directory / a["name"]
            if not path.is_file() or path.is_symlink():
                found.append(f"{a['name']} is missing or not a regular file")
            elif digest(path) != sha:
                found.append(f"{a['name']} sha256 differs from the manifest")
        elif sha not in wheel_by_sha:
            found.append(f"no wheel in the directory has {a.get('name')}'s sha256")
    lib = (by_name.get("seaweedkv_native") or [{}])[0].get("native_lib") or {}
    if not re.fullmatch(r"lib[A-Za-z0-9_]+\.so", str(lib.get("name"))) or not SHA.fullmatch(str(lib.get("sha256"))):
        found.append("seaweedkv_native.native_lib needs a lib*.so name and a sha256")
    else:
        path = directory / lib["name"]
        if not path.is_file() or path.is_symlink() or digest(path) != lib["sha256"]:
            found.append(f"{lib['name']} is missing, not a regular file, or differs from the manifest")
    return found


def check_cluster(template, release_dir, check_dir):
    """-> the published cluster template filled with throwaway values for a no-host `swctl plan install` check.

    Writes the read JWT key it refers to into check_dir. Every ${VAR} outside comments must be filled."""
    secret = check_dir / "read-jwt.key"
    secret.write_text(hashlib.sha256(str(check_dir).encode() + b"jwt").hexdigest())
    secret.chmod(0o600)
    values = {"CLUSTER": "kv-release-check", "RELEASE_DIR": str(release_dir),
              "MANIFEST_SHA256": digest(release_dir / "manifest.json"), "SSH_USER": "check",
              "SSH_KEY": str(check_dir / "key"), "TARGET_ADDR": "192.0.2.10", "HOST_KEY_SHA256": "SHA256:" + "A" * 43,
              "RDMA_DEVICE": "rxe0", "RDMA_NETDEV": "eth0", "RDMA_GID_INDEX": "1", "DATA_ADDR": "192.0.2.10",
              "WORK": str(check_dir), "BASE_PYTHON": "/usr/bin/python3"}
    text = re.sub(r"\$\{([A-Z0-9_]+)\}", lambda m: values.get(m.group(1), m.group(0)), template.read_text())
    left = [line for line in text.splitlines() if "${" in line.split("#", 1)[0]]
    if left:
        raise ValueError(f"KV_RELEASE_INPUT: the cluster template has unfilled values: {left}")
    return text


def _no_duplicates(pairs):
    keys = [k for k, _ in pairs]
    if len(keys) != len(set(keys)):
        raise ValueError("duplicate JSON keys")
    return dict(pairs)


if __name__ == "__main__":
    try:
        mode, args = sys.argv[1], sys.argv[2:]
        if mode == "create":
            d, tag, version, sha, go, lmc, consts = args
            directory = pathlib.Path(d)
            manifest = create(directory, tag, version, sha, go, lmc, json.loads(pathlib.Path(consts).read_text()))
            (directory / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
            bad = verify(directory)
        elif mode == "verify":
            (d,) = args
            bad = verify(pathlib.Path(d))
        elif mode == "assets":
            tag, path = args
            have = {n.strip() for n in open(path, encoding="utf-8").read().splitlines() if n.strip()}
            bad = [f"release {tag} lacks {n}" for n in expected_assets() if n not in have]
        elif mode == "extract-lib":
            wheel, lib, out = args
            pathlib.Path(out).write_bytes(wheel_member(pathlib.Path(wheel), lib))
            bad = []
        elif mode == "check-cluster":
            template, release, check = args
            sys.stdout.write(check_cluster(pathlib.Path(template), pathlib.Path(release), pathlib.Path(check)))
            sys.exit(0)
        else:
            raise ValueError(f"unknown mode {mode!r}; expected create, verify, assets, extract-lib or check-cluster")
    except (OSError, ValueError, KeyError, zipfile.BadZipFile) as e:
        msg = str(e)
        print(f"::error::{msg if msg.startswith('KV_RELEASE_') else 'KV_RELEASE_INPUT: ' + msg}", file=sys.stderr)
        sys.exit(1)
    for p in bad:
        print(f"::error::KV_RELEASE_{'MISSING' if mode == 'assets' else 'INVALID'}: {p}", file=sys.stderr)
    if bad:
        sys.exit(1)
    print(f"swctl KV release: OK ({mode})")
