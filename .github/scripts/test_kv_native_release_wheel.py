"""Tests for kv-native-wheel-version.py and check-kv-native-release-wheel.py.

RED first: the asset list actually published for release 4.53 (gh release view 4.53) has the wheel only inside the
preview tarball, as 0.1.0, so the standalone-wheel check fails on it. Run: python3 -m unittest test_kv_native_release_wheel
"""
import importlib.util
import pathlib
import subprocess
import sys
import tempfile
import unittest

HERE = pathlib.Path(__file__).parent


def load(name, file):
    spec = importlib.util.spec_from_file_location(name, HERE / file)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


version = load("wheel_version", "kv-native-wheel-version.py")
check = load("release_wheel", "check-kv-native-release-wheel.py")

# The KV-related asset names of the published 4.53 release (2026-11-13), plus two ordinary ones.
RELEASE_4_53 = [
    "weed-enterprise-linux_amd64.tar.gz",
    "weed-volume-enterprise_rdma_large_disk_linux_amd64.tar.gz",
    "seaweedkv-lmcache-native-preview_cp312_linux_amd64.tar.gz",
    "seaweedkv-lmcache-native-preview_cp312_linux_amd64.tar.gz.md5",
    "seaweedkv-lmcache-native-preview_cp312_linux_amd64.tar.gz.sha256",
    "seaweedkv-preview-pair.json",
]


def checkout(constants):
    d = tempfile.mkdtemp()
    p = pathlib.Path(d, "enterprise/weed/util/version")
    p.mkdir(parents=True)
    if constants is not None:
        (p / "constants.go").write_text(constants)
    return d


GOOD = "package version\n\nconst (\n\tMAJOR_VERSION  = int32(4)\n\tMINOR_VERSION  = int32(54)\n)\n"


class ReleaseWheelCheck(unittest.TestCase):
    def test_red_release_4_53_has_no_standalone_wheel(self):
        self.assertEqual(check.missing("4.53", RELEASE_4_53),
                         ["seaweedkv_native-4.53.0-cp312-cp312-linux_x86_64.whl",
                          "seaweedkv_native-4.53.0-cp312-cp312-linux_x86_64.whl.sha256"])

    def test_a_wheel_at_the_old_hand_kept_version_does_not_count(self):
        names = RELEASE_4_53 + ["seaweedkv_native-0.1.0-cp312-cp312-linux_x86_64.whl",
                                "seaweedkv_native-0.1.0-cp312-cp312-linux_x86_64.whl.sha256"]
        self.assertEqual(len(check.missing("4.54", names)), 2)

    def test_green_new_release_shape(self):
        names = RELEASE_4_53 + ["seaweedkv_native-4.54.0-cp312-cp312-linux_x86_64.whl",
                                "seaweedkv_native-4.54.0-cp312-cp312-linux_x86_64.whl.sha256"]
        self.assertEqual(check.missing("4.54", names), [])

    def test_wheel_without_its_sha256_is_incomplete(self):
        names = ["seaweedkv_native-4.54.0-cp312-cp312-linux_x86_64.whl"]
        self.assertEqual(check.missing("4.54", names), ["seaweedkv_native-4.54.0-cp312-cp312-linux_x86_64.whl.sha256"])

    def test_cli_exit_codes(self):
        f = tempfile.NamedTemporaryFile("w", suffix=".txt", delete=False)
        f.write("\n".join(RELEASE_4_53)); f.close()
        r = subprocess.run([sys.executable, str(HERE / "check-kv-native-release-wheel.py"), "4.53", f.name],
                           capture_output=True, text=True)
        self.assertEqual(r.returncode, 1)
        self.assertIn("lacks the standalone seaweedkv-native wheel", r.stderr)


class WheelVersion(unittest.TestCase):
    def test_minor_tag(self):
        self.assertEqual(version.wheel_version("4.54", checkout(GOOD)), "4.54.0")

    def test_patch_tag(self):
        self.assertEqual(version.wheel_version("4.54.2", checkout(GOOD)), "4.54.2")

    def test_v_prefix(self):
        self.assertEqual(version.wheel_version("v4.54", checkout(GOOD)), "4.54.0")

    def assert_named(self, tag, co, code):
        with self.assertRaises(version.VersionError) as cm:
            version.wheel_version(tag, co)
        self.assertTrue(str(cm.exception).startswith(code), str(cm.exception))

    def test_nonnumeric_tag_is_an_error_never_a_fallback(self):
        self.assert_named("latest", checkout(GOOD), "KV_WHEEL_VERSION_TAG")

    def test_tag_constants_mismatch(self):
        self.assert_named("4.55", checkout(GOOD), "KV_WHEEL_VERSION_MISMATCH")

    def test_constants_unparsable(self):
        self.assert_named("4.54", checkout("package version\nconst MAJOR_VERSION = 4\n"), "KV_WHEEL_VERSION_UNPARSABLE")

    def test_constants_missing(self):
        self.assert_named("4.54", checkout(None), "KV_WHEEL_VERSION_UNPARSABLE")

    def test_cli_names_the_error_and_prints_no_version(self):
        r = subprocess.run([sys.executable, str(HERE / "kv-native-wheel-version.py"), "latest", checkout(GOOD)],
                           capture_output=True, text=True)
        self.assertEqual((r.returncode, r.stdout), (1, ""))
        self.assertIn("::error::KV_WHEEL_VERSION_TAG", r.stderr)


if __name__ == "__main__":
    unittest.main()
