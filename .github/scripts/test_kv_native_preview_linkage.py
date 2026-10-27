import importlib.util
import json
import pathlib
import unittest
from unittest import mock

SCRIPT = pathlib.Path(__file__).with_name("kv-native-preview-verify.py")
spec = importlib.util.spec_from_file_location("verify", SCRIPT)
verify = importlib.util.module_from_spec(spec)
spec.loader.exec_module(verify)


class LinkageTest(unittest.TestCase):
    def test_approved_system_allowlist(self):
        observed = {"provider.so": sorted(verify.RDMA_SYSTEM | verify.RUNTIME_SYSTEM | {"libc.so.6", "libm.so.6"}),
                    "extension.so": ["provider.so", "libc.so.6"]}
        self.assertEqual(verify.check_needed(observed, {"provider.so", "extension.so"}), observed)
        for name in ["libunknown.so", "libssl.so.1.1", "libresolv.so.2", "libutil.so.1"]:
            with self.subTest(name=name):
                with self.assertRaisesRegex(ValueError, "unexpected external DT_NEEDED"):
                    verify.check_needed({"provider.so": [name]}, {"provider.so"})

    def test_no_bundled_rdma_copies(self):
        for name in ["libibverbs.so.1", "librdmacm-hash.so.1", "libmlx5.so.1.24", "libssl-hash.so.3", "libcrypto.so.3", "libstdc++.so.6", "libgcc_s.so.1"]:
            with self.subTest(name=name):
                with self.assertRaisesRegex(ValueError, "must remain system-provided"):
                    verify.check_needed({}, {name})

    def test_readelf_requires_evidence(self):
        with mock.patch.object(verify.subprocess, "check_output", return_value=" 0x0000000000000001 (NEEDED) Shared library: [libc.so.6]\n"):
            self.assertEqual(verify.read_needed(pathlib.Path("fixture")), ["libc.so.6"])
        with mock.patch.object(verify.subprocess, "check_output", return_value="no dynamic section"):
            with self.assertRaisesRegex(ValueError, "missing DT_NEEDED evidence"):
                verify.read_needed(pathlib.Path("fixture"))


if __name__ == "__main__":
    unittest.main()
