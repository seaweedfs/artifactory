import importlib.util
import json
import pathlib
import tempfile
import unittest
from unittest import mock
from wheel.wheelfile import WheelFile

SCRIPT = pathlib.Path(__file__).with_name("kv-native-preview-stamp.py")
spec = importlib.util.spec_from_file_location("stamp", SCRIPT)
stamp = importlib.util.module_from_spec(spec)
spec.loader.exec_module(stamp)
PINS = json.loads(SCRIPT.parents[1].joinpath("kv-native-preview-pins.json").read_text())


class StampTest(unittest.TestCase):
    def test_both_wheels_have_build_info_and_valid_record(self):
        # Keep fixture writes in this agent's local tree, not a shared temp root.
        with tempfile.TemporaryDirectory(dir=SCRIPT.parent) as directory:
            out = pathlib.Path(directory)
            wheels = out / "wheels"
            wheels.mkdir()
            for name, tag in (("seaweedkv_lmcache", "py3-none-any"),
                              ("seaweedkv_native", "cp312-cp312-linux_x86_64")):
                with WheelFile(str(wheels / f"{name}-0.1.0-{tag}.whl"), "w") as archive:
                    archive.writestr(name + "/__init__.py", "# fixture\n")
                    archive.writestr(name + "-0.1.0.dist-info/METADATA", f"Metadata-Version: 2.1\nName: {name}\nVersion: 0.1.0\n")
                    archive.writestr(name + "-0.1.0.dist-info/WHEEL", "Wheel-Version: 1.0\nGenerator: fixture\nRoot-Is-Purelib: true\nTag: " + tag + "\n")
            (out / "preview-pins.json").write_text(json.dumps(PINS))
            before = {p.name: p.read_bytes() for p in wheels.iterdir()}
            self.assertTrue(all(b"BUILD_INFO" not in b for b in before.values()))

            def output(argv, **kwargs):
                if argv[0] == "git":
                    return ("mono-fixture" if argv[2] == "mono" else PINS["lmcache_commit"]) + "\n"
                if argv[0] == "dpkg-query":
                    return "50.0-2ubuntu0.2"
                if argv[0] == "gcc":
                    return PINS["gcc"] + "\n"
                return argv[0] + " " + PINS["rust" if argv[0] == "rustc" else argv[0]] + " fixture\n"

            with mock.patch.object(stamp.importlib.metadata, "version", side_effect=lambda n: PINS[n]), \
                 mock.patch.object(stamp.subprocess, "check_output", side_effect=output), \
                 mock.patch.object(stamp.platform, "libc_ver", return_value=("glibc", "2.39")):
                stamp.stamp(pathlib.Path("mono"), pathlib.Path("lmcache"), out)
            expected = (out / "BUILD_INFO").read_bytes()
            for wheel in wheels.iterdir():
                with WheelFile(str(wheel)) as archive:
                    # WheelFile checks RECORD's content hashes on each read.
                    for name in archive.namelist():
                        archive.read(name)
                    member, = (n for n in archive.namelist() if n.endswith(".dist-info/BUILD_INFO"))
                    self.assertEqual(archive.read(member), expected)
            info = json.loads(expected)
            self.assertEqual(info["mono_commit"], "mono-fixture")
            self.assertEqual(info["lmcache_headers_commit"], PINS["lmcache_commit"])
            self.assertEqual(info["supported_transport"], "rc")
            self.assertIn("UNVERIFIED", info["runtime_validation"])
            self.assertEqual(info["system_prerequisites"]["glibc_min"], "2.39")
            self.assertEqual(info["system_prerequisites"]["rdma_core_min"], "50.0")
            self.assertEqual(len(info["system_prerequisites"]["ubuntu_packages_min"]), 3)
            self.assertEqual(info["system_prerequisites"]["ubuntu_packages_required"], ["libstdc++6", "libgcc-s1", "libssl3t64", "libc6"])
            self.assertEqual(len(info["system_prerequisites"]["external_runtime_libraries"]), 4)
            for name in ("setuptools", "wheel", "pybind11", "cmake", "rustc", "cargo", "gcc"):
                self.assertTrue(info["build_tools"][name].startswith(name + "=="))
            for libc in [("glibc", "2.38"), ("glibc", "2.40"), ("", "")]:
                with mock.patch.object(stamp.importlib.metadata, "version", side_effect=lambda n: PINS[n]), \
                     mock.patch.object(stamp.subprocess, "check_output", side_effect=output), \
                     mock.patch.object(stamp.platform, "libc_ver", return_value=libc):
                    with self.assertRaisesRegex(ValueError, "glibc 2.39 floor"):
                        stamp.stamp(pathlib.Path("mono"), pathlib.Path("lmcache"), out)
            # A wrong tool pin refuses before any wheel is rewritten.
            unchanged = {p.name: p.read_bytes() for p in wheels.iterdir()}
            with mock.patch.object(stamp.importlib.metadata, "version", return_value="wrong"):
                with self.assertRaises(ValueError):
                    stamp.stamp(pathlib.Path("mono"), pathlib.Path("lmcache"), out)
            self.assertEqual(unchanged, {p.name: p.read_bytes() for p in wheels.iterdir()})


if __name__ == "__main__":
    unittest.main()
