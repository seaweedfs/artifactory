#!/usr/bin/env python3
"""Add identical build-tool/source provenance to both wheels, updating RECORD."""
import importlib.metadata
import json
import pathlib
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
    info = {
        "schema": "seaweedkv-wheel-build-info/v1",
        "mono_commit": subprocess.check_output(["git", "-C", str(mono), "rev-parse", "HEAD"], text=True).strip(),
        "lmcache_headers_commit": subprocess.check_output(["git", "-C", str(lmcache), "rev-parse", "HEAD"], text=True).strip(),
        "python": sys.version,
        "python_abi": pins["python_abi"],
        "build_tools": {name: f"{name}=={version}" for name, version in tools.items()},
        "runtime": {"lmcache": pins["lmcache_runtime"], **pins["runtime"]},
        "supported_transport": "rc",
        "runtime_validation": pins["runtime_validation"],
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
