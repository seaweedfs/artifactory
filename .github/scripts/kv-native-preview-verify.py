#!/usr/bin/env python3
"""Source-free wheel import/link smoke. Never instantiate a connector or NIC."""
import ctypes
import hashlib
import importlib
import importlib.metadata
import json
import pathlib
import platform
import subprocess
import sys
import zipfile


# The release decision permits only these external non-glibc libraries.
RDMA_SYSTEM = {"libibverbs.so.1", "librdmacm.so.1", "libmlx5.so.1"}
GLIBC_SYSTEM = {"libc.so.6", "libm.so.6", "libdl.so.2", "libpthread.so.0",
                "librt.so.1",
                "ld-linux-x86-64.so.2"}

RUNTIME_SYSTEM = {"libstdc++.so.6", "libgcc_s.so.1", "libssl.so.3", "libcrypto.so.3"}


def check_needed(observed, bundled):
    # System libraries must not be hidden under hashed or versioned names.
    for name in bundled:
        if any(name.startswith(prefix) for prefix in ("libibverbs", "librdmacm", "libmlx5", "libstdc++", "libgcc_s", "libssl", "libcrypto")):
            raise ValueError("library must remain system-provided: " + name)
    unexpected = {name for needed in observed.values() for name in needed
                  if name not in RDMA_SYSTEM | GLIBC_SYSTEM | RUNTIME_SYSTEM | {"libseaweedfs_sw_rdma_nixl.so"}}
    if unexpected:
        raise ValueError("unexpected external DT_NEEDED: " + ", ".join(sorted(unexpected)))
    return observed


def check_wheel_libraries(archive):
    libraries = {pathlib.PurePosixPath(n).name for n in archive.namelist()
                 if ".so" in pathlib.PurePosixPath(n).name}
    check_needed({}, libraries)


def read_needed(path):
    import os
    import re
    text = subprocess.check_output(["readelf", "--dynamic", str(path)],
                                   env={**os.environ, "LC_ALL": "C"}, text=True)
    needed = re.findall(r"\(NEEDED\).*Shared library: \[([^\]]+)\]", text)
    if not needed:
        raise ValueError("missing DT_NEEDED evidence: " + str(path))
    return needed


def resolve_dependencies(path, direct):
    import re
    text = subprocess.check_output(["ldd", str(path)], text=True, stderr=subprocess.STDOUT)
    if "not found" in text or not text.strip():
        raise ValueError("unresolved dependency: " + str(path))
    # All direct names must have a resolved row. Transitive system rows are
    # recorded verbatim, not subjected to the DIRECT SONAME allowlist.
    names = {line.strip().split()[0] for line in text.splitlines() if line.strip()}
    if any(name not in names and not any(n.endswith("/" + name) for n in names) for name in direct):
        raise ValueError("missing direct resolution: " + str(path))
    if not re.search(r"(?:=> /|^\s*/)", text, re.MULTILINE):
        raise ValueError("missing resolved paths: " + str(path))
    return text


def validate_info(info):
    required = {"status": "PREVIEW", "platform_tag": "linux_x86_64",
                "distribution": "GitHub release asset only; not PyPI",
                "supported_transport": "rc"}
    if any(info.get(k) != v for k, v in required.items()):
        raise ValueError("missing/changed preview policy")
    if info.get("runtime_validation") != "UNVERIFIED pending planned correctness rerun":
        raise ValueError("missing/changed UNVERIFIED runtime status")
    prereq = info.get("system_prerequisites", {})
    if (not isinstance(prereq, dict) or prereq.get("glibc_min") != "2.39" or not prereq.get("rdma_core_min")
        or prereq.get("platform") != "Ubuntu 24.04-class Linux x86_64"
        or not {"libstdc++6", "libgcc-s1", "libssl3t64", "libc6"}.issubset(prereq.get("ubuntu_packages_required", []))
        or not all(prereq.get("ubuntu_packages_min", {}).get(n) for n in ("libibverbs1", "librdmacm1t64", "ibverbs-providers"))):
        raise ValueError("missing/changed system prerequisites")


def verify(mono, lmcache, out):
    # Locate installed files without importing either package: inspect before load.
    prefix = pathlib.Path(sys.prefix).resolve()
    dist = importlib.metadata.distribution("seaweedkv-native")
    files = [pathlib.Path(dist.locate_file(f)).resolve() for f in dist.files]
    provider, = [p for p in files if p.name == "libseaweedfs_sw_rdma_nixl.so"]
    extension, = [p for p in files if p.name.startswith("_native.") and p.suffix == ".so"]
    assert provider.is_relative_to(prefix) and extension.is_relative_to(prefix)
    wheels = sorted((out/"wheels").glob("*.whl"))
    assert len(wheels) == 2
    for wheel in wheels:
        with zipfile.ZipFile(wheel) as archive:
            check_wheel_libraries(archive)
    observed = {p.name: read_needed(p) for p in (provider, extension)}
    (out/"dt-needed.json").write_text(json.dumps(observed, indent=2)+"\n")
    check_needed(observed, {provider.name, extension.name})
    resolved = {p.name: resolve_dependencies(p, observed[p.name]) for p in (provider, extension)}
    (out/"resolved-dependencies.json").write_text(json.dumps(resolved, indent=2)+"\n")
    (out/"provider-ldd.txt").write_text(resolved[provider.name])
    for name in ("seaweedkv-native", "seaweedkv-lmcache"):
        installed = importlib.metadata.distribution(name)
        member, = [f for f in installed.files if str(f).endswith(".dist-info/BUILD_INFO")]
        raw = pathlib.Path(installed.locate_file(member)).read_bytes()
        validate_info(json.loads(raw))
        assert raw == (out/"BUILD_INFO").read_bytes()
    import seaweedkv_lmcache
    import seaweedkv_native
    native = importlib.import_module("seaweedkv_native._native")
    from seaweedkv_lmcache._native import library_transports
    for module in (seaweedkv_lmcache, seaweedkv_native, native):
        assert pathlib.Path(module.__file__).resolve().is_relative_to(prefix), module.__file__
    ctypes.CDLL(str(provider))
    assert "rc" in library_transports(provider)

    def digest(path):
        return hashlib.file_digest(path.open("rb"), "sha256").hexdigest()

    wheels = sorted((out/"wheels").glob("*.whl"))
    assert len(wheels) == 2
    for wheel in wheels:
        with zipfile.ZipFile(wheel) as archive:
            names = archive.namelist()
            build_infos = [n for n in names if n.endswith(".dist-info/BUILD_INFO")]
            assert len(build_infos) == 1
            assert archive.read(build_infos[0]) == (out/"BUILD_INFO").read_bytes()
            if wheel.name.startswith("seaweedkv_native-"):
                shared_objects = {pathlib.PurePosixPath(n).name for n in names
                                  if ".so" in pathlib.PurePosixPath(n).name}
                check_needed({}, shared_objects)
                assert "-cp312-cp312-linux_x86_64.whl" in wheel.name
                assert sum(n.endswith("libseaweedfs_sw_rdma_nixl.so") for n in names) == 1
                assert sum(n.endswith(".so") and "/_native." in n for n in names) == 1
                bundled = archive.read("seaweedkv_native/libseaweedfs_sw_rdma_nixl.so")
                assert hashlib.sha256(bundled).hexdigest() == digest(provider)
            else:
                assert wheel.name.startswith("seaweedkv_lmcache-") and wheel.name.endswith("-py3-none-any.whl")
    manifest = {
        "schema": "seaweedkv-native-preview-manifest/v1",
        "mono_commit": subprocess.check_output(["git","-C",str(mono),"rev-parse","HEAD"],text=True).strip(),
        "pins": json.loads((out/"preview-pins.json").read_text()),
        "python": sys.version,
        "platform": platform.platform(),
        "rustc": subprocess.check_output(["rustc","--version"],text=True).strip(),
        "wheels": [{"name": p.name,"bytes": p.stat().st_size,"sha256": digest(p)} for p in wheels],
        "dt_needed": observed,
        "resolved_dependencies": resolved,
        "native_provider_sha256": digest(provider),
        "extension_sha256": digest(pathlib.Path(native.__file__)),
        "lmcache_headers": {str(p.relative_to(lmcache)): digest(p) for p in sorted((lmcache/"csrc/storage_backends").glob("*.h"))},
        "installed_versions": {n: importlib.metadata.version(n) for n in ("seaweedkv-lmcache","seaweedkv-native")},
        "smoke": "source-free imports, bundled-library load and RC feature query; no transport opened",
    }
    (out/"BUILD-MANIFEST.json").write_text(json.dumps(manifest,indent=2)+"\n")
    print(json.dumps(manifest,indent=2))


if __name__ == "__main__":
    verify(*map(pathlib.Path, sys.argv[1:]))
