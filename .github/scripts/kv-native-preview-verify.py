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

mono, lmcache, out = map(pathlib.Path, sys.argv[1:])
import seaweedkv_lmcache
import seaweedkv_native
native = importlib.import_module("seaweedkv_native._native")
from seaweedkv_lmcache._native import library_transports

prefix = pathlib.Path(sys.prefix).resolve()
for module in (seaweedkv_lmcache, seaweedkv_native, native):
    assert pathlib.Path(module.__file__).resolve().is_relative_to(prefix), module.__file__
provider = pathlib.Path(native.__file__).with_name("libseaweedfs_sw_rdma_nixl.so")
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
    "native_provider_sha256": digest(provider),
    "extension_sha256": digest(pathlib.Path(native.__file__)),
    "lmcache_headers": {str(p.relative_to(lmcache)): digest(p) for p in sorted((lmcache/"csrc/storage_backends").glob("*.h"))},
    "installed_versions": {n: importlib.metadata.version(n) for n in ("seaweedkv-lmcache","seaweedkv-native")},
    "smoke": "source-free imports, bundled-library load and RC feature query; no transport opened",
}
(out/"BUILD-MANIFEST.json").write_text(json.dumps(manifest,indent=2)+"\n")
print(json.dumps(manifest,indent=2))
