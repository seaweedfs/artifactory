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
                  if name not in RDMA_SYSTEM | GLIBC_SYSTEM | RUNTIME_SYSTEM | set(bundled)}
    if unexpected:
        raise ValueError("unexpected external DT_NEEDED: " + ", ".join(sorted(unexpected)))
    return observed


def read_needed(path):
    import os
    import re
    text = subprocess.check_output(["readelf", "--dynamic", str(path)],
                                   env={**os.environ, "LC_ALL": "C"}, text=True)
    needed = re.findall(r"\(NEEDED\).*Shared library: \[([^\]]+)\]", text)
    if not needed:
        raise ValueError("missing DT_NEEDED evidence: " + str(path))
    return needed


def verify(mono, lmcache, out):
    import seaweedkv_lmcache
    import seaweedkv_native
    native = importlib.import_module("seaweedkv_native._native")
    from seaweedkv_lmcache._native import library_transports

    prefix = pathlib.Path(sys.prefix).resolve()
    for module in (seaweedkv_lmcache, seaweedkv_native, native):
        assert pathlib.Path(module.__file__).resolve().is_relative_to(prefix), module.__file__
    provider = pathlib.Path(native.__file__).with_name("libseaweedfs_sw_rdma_nixl.so")
    observed = {p.name: read_needed(p) for p in (provider, pathlib.Path(native.__file__))}
    (out/"dt-needed.json").write_text(json.dumps(observed, indent=2)+"\n")
    check_needed(observed, {provider.name, pathlib.Path(native.__file__).name})
    ctypes.CDLL(str(provider))
    assert "rc" in library_transports(provider)
    ldd = subprocess.run(["ldd", str(provider)], capture_output=True, text=True, check=True).stdout
    assert "not found" not in ldd
    for lib in ("libibverbs.so", "librdmacm.so", "libmlx5.so"):
        assert lib in ldd, lib
    (out/"provider-ldd.txt").write_text(ldd)

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
