#!/usr/bin/env python3
"""Add identical build-tool/source provenance to both wheels, updating RECORD."""
import importlib.metadata
import json
import pathlib
import platform
import subprocess
import sys
import tempfile


def stamp(mono, lmcache, out):
    pins = json.loads((out / "preview-pins.json").read_text())
    tools = {name: importlib.metadata.version(name) for name in
             ("pip", "setuptools", "wheel", "pybind11", "cmake")}
    for name, version in tools.items():
        if version != pins[name]:
            raise ValueError(f"{name}=={version}, expected {pins[name]}")
    for name, args in (("rustc", ["--version"]), ("cargo", ["--version"]),
                       ("gcc", ["-dumpfullversion"])):
        output = subprocess.check_output([name, *args], text=True).strip()
        version = output if name == "gcc" else output.split()[1]
        expected = pins["rust"] if name == "rustc" else pins[name]
        if version != expected:
            raise ValueError(f"{name}=={version}, expected {expected}")
        tools[name] = version
    packages = {name: subprocess.check_output(
        ["dpkg-query", "-W", "-f=${Version}", name], text=True).strip()
        for name in ("libibverbs1", "librdmacm1t64", "ibverbs-providers")}
    if any(not version for version in packages.values()):
        raise ValueError("missing system rdma-core package version")
    upstream = {version.split(":")[-1].split("-")[0] for version in packages.values()}
    if len(upstream) != 1:
        raise ValueError("rdma-core packages disagree on upstream version")
    if platform.libc_ver() != ("glibc", "2.39"):
        raise ValueError("preview builder must match Ubuntu24.04 glibc 2.39 floor")
    info = {
        "schema": "seaweedkv-wheel-build-info/v1",
        "status": "PREVIEW",
        "platform_tag": "linux_x86_64",
        "distribution": "GitHub release asset only; not PyPI",
        "mono_commit": subprocess.check_output(["git", "-C", str(mono), "rev-parse", "HEAD"], text=True).strip(),
        "lmcache_headers_commit": subprocess.check_output(["git", "-C", str(lmcache), "rev-parse", "HEAD"], text=True).strip(),
        "python": sys.version,
        "python_abi": pins["python_abi"],
        "build_tools": {name: f"{name}=={version}" for name, version in tools.items()},
        "runtime": {"lmcache": pins["lmcache_runtime"], **pins["runtime"]},
        "supported_transport": "rc",
        "runtime_validation": pins["runtime_validation"],
        "system_prerequisites": {
            "platform": "Ubuntu 24.04-class Linux x86_64",
            "glibc_min": "2.39",
            "rdma_core_min": upstream.pop(),
            "ubuntu_packages_min": packages,
            "external_rdma_libraries": ["libibverbs.so.1", "librdmacm.so.1", "libmlx5.so.1"],
            "ubuntu_packages_required": ["libstdc++6", "libgcc-s1", "libssl3t64", "libc6"],
            "external_runtime_libraries": ["libstdc++.so.6", "libgcc_s.so.1", "libssl.so.3", "libcrypto.so.3"],
            "runtime_bundling": "forbidden; keep OpenSSL and compiler runtimes system-provided",
            "rdma_bundling": "forbidden; use the host rdma-core/provider installation",
        },
    }
    encoded = json.dumps(info, indent=2) + "\n"
    for wheel in sorted((out / "wheels").glob("*.whl")):
        with tempfile.TemporaryDirectory(dir=out) as tmp:
            subprocess.run([sys.executable, "-m", "wheel", "unpack", str(wheel), "-d", tmp], check=True)
            roots = list(pathlib.Path(tmp).iterdir())
            if len(roots) != 1:
                raise ValueError("wheel unpack did not produce one root")
            metadata = list(roots[0].glob("*.dist-info"))
            if len(metadata) != 1:
                raise ValueError("wheel must have exactly one dist-info")
            (metadata[0] / "BUILD_INFO").write_text(encoded)
            subprocess.run([sys.executable, "-m", "wheel", "pack", str(roots[0]), "-d", str(out / "wheels")], check=True)
    (out / "BUILD_INFO").write_text(encoded)


if __name__ == "__main__":
    stamp(*map(pathlib.Path, sys.argv[1:]))
