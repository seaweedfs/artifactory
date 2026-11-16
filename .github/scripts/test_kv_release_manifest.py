"""Tests for kv-release-manifest.py and the build-swctl-kv-release-linux-amd64 release job.

RED first: release 4.53 (gh release view 4.53) publishes no swctl KV release directory, so the assets check fails;
and a manifest with placeholder hashes (docs' manifest.example.json shape) is refused, as swctl install refuses it.
Run: python3 -m unittest test_kv_release_manifest
"""
import hashlib
import importlib.util
import json
import os
import pathlib
import subprocess
import sys
import tempfile
import unittest
import zipfile

import yaml

HERE = pathlib.Path(__file__).parent
SCRIPT = HERE / "kv-release-manifest.py"
spec = importlib.util.spec_from_file_location("kv_release", SCRIPT)
rel = importlib.util.module_from_spec(spec)
spec.loader.exec_module(rel)
CONSTANTS = json.loads((HERE.parent / "swctl-kv-release.json").read_text())
LIB = CONSTANTS["native_lib"]
SRC = "a" * 40
RELEASE_4_53 = ["weed-enterprise-linux_amd64.tar.gz", "weed-volume-enterprise_rdma_large_disk_linux_amd64.tar.gz",
                "seaweedkv-lmcache-native-preview_cp312_linux_amd64.tar.gz", "seaweedkv-preview-pair.json"]


def release_dir(root, lib=b"native provider bytes", lmcache=None):
    """A release directory shaped like the job's: two binaries, the .so, and both wheels."""
    (root / "weed").write_bytes(b"weed binary")
    (root / "weed-volume").write_bytes(b"weed-volume binary")
    (root / LIB).write_bytes(lib)
    with zipfile.ZipFile(root / "seaweedkv_native-4.54.0-cp312-cp312-linux_x86_64.whl", "w") as z:
        z.writestr(f"seaweedkv_native/{LIB}", b"native provider bytes")
        z.writestr("seaweedkv_native/__init__.py", b"")
    pin = dict(CONSTANTS["lmcache_wheel"])
    data = lmcache if lmcache is not None else b"lmcache wheel bytes"
    (root / pin["name"]).write_bytes(data)
    consts = dict(CONSTANTS, lmcache_wheel=dict(pin, sha256=hashlib.sha256(b"lmcache wheel bytes").hexdigest()))
    return consts


def build(root, **kw):
    consts = release_dir(root, **kw)
    return rel.create(root, "4.54", "4.54.0", SRC, "go1.26.6", "3e11e108", consts)


class Assets(unittest.TestCase):
    def test_red_release_4_53_has_no_release_directory(self):
        with tempfile.TemporaryDirectory() as d:
            names = pathlib.Path(d, "names")
            names.write_text("\n".join(RELEASE_4_53) + "\n")
            r = subprocess.run([sys.executable, str(SCRIPT), "assets", "4.53", str(names)], capture_output=True, text=True)
            self.assertEqual(r.returncode, 1)
            for n in rel.expected_assets():
                self.assertIn(f"KV_RELEASE_MISSING: release 4.53 lacks {n}", r.stderr)
            names.write_text("\n".join(RELEASE_4_53 + rel.expected_assets()) + "\n")
            r = subprocess.run([sys.executable, str(SCRIPT), "assets", "4.54", str(names)], capture_output=True, text=True)
            self.assertEqual(r.returncode, 0, r.stderr)


class KernelWindow(unittest.TestCase):
    """The supported kernel window: Ubuntu 24.04 with 6.8 <= kernel < 7.1 (7.0.x supported)."""
    VECTORS = {"7.0.0-1017-azure": True, "6.8.0-1064-nvidia": True, "6.8": True, "7.0.9": True,
               "7.1.0": False, "7.1.0-1001-azure": False, "6.7.12-generic": False, "": False, "garbage": False}

    def test_release_constants_window(self):
        (os_entry,) = CONSTANTS["supported_os"]
        self.assertEqual((os_entry["ID"], os_entry["VERSION_ID"]), ("ubuntu", "24.04"))
        self.assertEqual(os_entry["kernel"], {"min": "6.8", "max_exclusive": "7.1"})

    def test_vectors_against_the_release_window(self):
        w = CONSTANTS["supported_os"][0]["kernel"]
        for release, want in self.VECTORS.items():
            self.assertEqual(rel.kernel_in_window(release, w), want, release)

    def test_red_the_old_7_0_window_refused_the_azure_kernel(self):
        self.assertFalse(rel.kernel_in_window("7.0.0-1017-azure", {"min": "6.8", "max_exclusive": "7.0"}))

    def test_numeric_ordering_6_10_is_above_6_8(self):
        self.assertTrue(rel.kernel_in_window("6.10.0-1-generic", {"min": "6.8", "max_exclusive": "7.1"}))
        self.assertFalse(rel.kernel_in_window("6.8.0-1064-nvidia", {"min": "6.10", "max_exclusive": "7.1"}))

    def test_bounds_are_strict_but_uname_keeps_its_suffix(self):
        self.assertTrue(rel.kernel_in_window("7.0.0-1017-azure", {"min": "6.8", "max_exclusive": "7.1"}))
        for bad in ("6..8", "6.8.", " 6.8", "6.8-rc1", "", 6.8, None):
            self.assertFalse(rel.kernel_in_window("7.0.0", {"min": bad, "max_exclusive": "7.1"}), repr(bad))
        self.assertFalse(rel.kernel_in_window("7.0.0", ["6.8", "7.1"]))

    def test_wrong_supported_os_types_are_refused_cleanly(self):
        for supported in ("ubuntu", [["ubuntu"]], [{"ID": "ubuntu", "kernel": ["6.8", "7.1"]}], [{"ID": "ubuntu"}]):
            with tempfile.TemporaryDirectory() as d:
                root = pathlib.Path(d)
                m = build(root)
                m["supported_os"] = supported
                problems = rel.verify(root, json.dumps(m).encode())   # no traceback: a clean refusal
                self.assertTrue(any("supported_os" in p for p in problems), supported)

    def test_malformed_windows_are_refused_by_verify(self):
        for window in ({"min": "7.1", "max_exclusive": "6.8"}, {"min": "7.0", "max_exclusive": "7.0"},
                       {"min": "x", "max_exclusive": "7.1"}, {"min": "6..8", "max_exclusive": "7.1"},
                       {"min": "6.8", "max_exclusive": "7.1-rc1"}, {"min": 6.8, "max_exclusive": "7.1"},
                       {"max_exclusive": "7.1"}):
            with tempfile.TemporaryDirectory() as d:
                root = pathlib.Path(d)
                m = build(root)
                self.assertEqual(rel.verify(root, json.dumps(m).encode()), [])   # the real window passes
                m["supported_os"] = [dict(m["supported_os"][0], kernel=window)]  # a copy: CONSTANTS stays untouched
                self.assertTrue(any("kernel" in p for p in rel.verify(root, json.dumps(m).encode())), window)


class Manifest(unittest.TestCase):
    def test_red_placeholder_manifest_is_refused(self):
        with tempfile.TemporaryDirectory() as d:
            root = pathlib.Path(d)
            m = build(root)
            m["artifacts"][0]["sha256"] = "0" * 64
            m["artifacts"][1]["version"] = "EXAMPLE"
            bad = rel.verify(root, json.dumps(m).encode())
            self.assertIn("manifest.json holds a placeholder (EXAMPLE or an all-zero sha256)", bad)
            self.assertIn("weed sha256 differs from the manifest", bad)

    def test_green_real_hashes_and_shape(self):
        with tempfile.TemporaryDirectory() as d:
            root = pathlib.Path(d)
            m = build(root)
            self.assertEqual(rel.verify(root, json.dumps(m).encode()), [])
            arts = {a["name"]: a for a in m["artifacts"]}
            self.assertEqual(list(arts), ["weed", "weed-volume", "seaweedkv_native", "lmcache"])
            self.assertEqual(arts["weed"]["sha256"], hashlib.sha256(b"weed binary").hexdigest())
            self.assertEqual(arts["seaweedkv_native"]["python_abi"], "cp312-linux_x86_64")
            self.assertEqual(arts["seaweedkv_native"]["native_lib"],
                             {"name": LIB, "sha256": hashlib.sha256(b"native provider bytes").hexdigest()})
            self.assertEqual(arts["weed-volume"]["features"], ["rdma", "rdma-dc", "kvcache"])
            self.assertEqual((m["bundle"], m["source"]["commit"]), ("kv-4.54", SRC))
            self.assertEqual(m["upgrade_edges_ref"], "docs/KVCACHE-UPGRADE-MATRIX.yaml@aaaaaaaa")

    def test_native_lib_must_equal_the_copy_in_the_wheel(self):
        with tempfile.TemporaryDirectory() as d:
            with self.assertRaisesRegex(ValueError, "differs from the copy inside"):
                build(pathlib.Path(d), lib=b"another provider")

    def test_lmcache_wheel_must_match_its_pin(self):
        with tempfile.TemporaryDirectory() as d:
            with self.assertRaisesRegex(ValueError, "sha256 differs from the pinned"):
                build(pathlib.Path(d), lmcache=b"tampered wheel")

    def test_swctl_directory_rules(self):
        with tempfile.TemporaryDirectory() as d:
            root = pathlib.Path(d)
            raw = json.dumps(build(root)).encode()
            (root / "weed-volume").write_bytes(b"replaced")                       # binary changed after the manifest
            self.assertEqual(rel.verify(root, raw), ["weed-volume sha256 differs from the manifest"])
            (root / "weed-volume").write_bytes(b"weed-volume binary")
            dup = root / "copy-of-lmcache.whl"                                  # two wheels with one digest
            dup.write_bytes(b"lmcache wheel bytes")
            self.assertIn("share one sha256", rel.verify(root, raw)[0])
            dup.unlink()
            (root / LIB).unlink()
            self.assertIn(f"{LIB} is missing", rel.verify(root, raw)[0])
            self.assertEqual(rel.verify(root, raw.replace(b'"profile": "kv", ', b'"profile": "kv", "profile": "kv", ')),
                             ["manifest.json: duplicate JSON keys"])

    def test_cli_create_writes_and_verifies(self):
        with tempfile.TemporaryDirectory() as d:
            root = pathlib.Path(d, "release")
            root.mkdir()
            consts = release_dir(root)
            cpath = pathlib.Path(d, "constants.json")
            cpath.write_text(json.dumps(consts))
            argv = [sys.executable, str(SCRIPT), "create", str(root), "4.54", "4.54.0", SRC, "go1.26.6", "3e11e108"]
            r = subprocess.run(argv + [str(cpath)], capture_output=True, text=True)
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertEqual(json.loads((root / "manifest.json").read_text())["schema"], "swctl.release-manifest/v1")
            (root / "weed").unlink()
            r = subprocess.run([sys.executable, str(SCRIPT), "verify", str(root)], capture_output=True, text=True)
            self.assertEqual(r.returncode, 1)
            self.assertIn("KV_RELEASE_INVALID: weed is missing", r.stderr)


class CheckCluster(unittest.TestCase):
    def test_fills_every_value_and_refuses_an_unknown_one(self):
        with tempfile.TemporaryDirectory() as d:
            root = pathlib.Path(d)
            (root / "manifest.json").write_text("{}")
            tpl = root / "cluster.yaml"
            tpl.write_text("# Replace every ${...} here\nname: ${CLUSTER}\nmanifest_sha256: \"${MANIFEST_SHA256}\"\n"
                           "key: controller-file:${WORK}/read-jwt.key\n")
            text = rel.check_cluster(tpl, root, root)
            self.assertIn("name: kv-release-check", text)
            self.assertIn(hashlib.sha256(b"{}").hexdigest(), text)
            self.assertEqual(len((root / "read-jwt.key").read_text()), 64)
            tpl.write_text("name: ${CLUSTER}\nnew_key: ${NOT_A_KNOWN_VALUE}\n")
            with self.assertRaisesRegex(ValueError, "unfilled values"):
                rel.check_cluster(tpl, root, root)


class Job(unittest.TestCase):
    def setUp(self):
        wf = yaml.safe_load((HERE.parent / "workflows/rust_volume_release.yml").read_text(encoding="utf-8"))
        self.job = wf["jobs"]["build-swctl-kv-release-linux-amd64"]
        self.text = "\n".join(str(s.get("run", "")) for s in self.job["steps"])

    def test_inputs_come_from_this_release_and_its_source_commit(self):
        self.assertEqual(set(self.job["needs"]) >= {"build-rust-rdma-enterprise-linux-amd64",
                                                    "build-kv-native-preview-linux-amd64"}, True)
        self.assertIn('checkout --detach "$SOURCE_SHA"', self.text)
        self.assertIn("kv-native-preview-pair.py verify . seaweedkv-preview-pair.json --wheel", self.text)
        self.assertIn("-tags 5BytesOffset", self.text)
        self.assertIn("--require-hashes", self.text)

    def test_checks_with_real_swctl_then_uploads_and_rechecks(self):
        self.assertIn("kv-release-manifest.py create", self.text)
        self.assertIn("swctl-check plan install", self.text)
        self.assertLess(self.text.index("swctl-check plan install"), self.text.index("gzip -n"))
        self.assertIn("kv-release-manifest.py assets", self.text)
        upload = next(s for s in self.job["steps"] if "action-gh-release" in s.get("uses", ""))
        self.assertTrue(upload["with"]["fail_on_unmatched_files"])
        self.assertEqual(upload["with"]["files"].split(), rel.expected_assets())


if __name__ == "__main__":
    unittest.main()
