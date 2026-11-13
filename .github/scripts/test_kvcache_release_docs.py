"""Tests for check-kvcache-release-docs.py and the publish-kvcache-docs release job.

RED first: the asset list actually published for release 4.53 (gh release view 4.53) has no install docs, so the
assets check fails on it. Run: python3 -m unittest test_kvcache_release_docs
"""
import importlib.util
import pathlib
import subprocess
import sys
import tempfile
import unittest

import yaml

HERE = pathlib.Path(__file__).parent
spec = importlib.util.spec_from_file_location("docs_check", HERE / "check-kvcache-release-docs.py")
check = importlib.util.module_from_spec(spec)
spec.loader.exec_module(check)

# The KV-related asset names of the published 4.53 release (2026-11-14), plus an ordinary one.
RELEASE_4_53 = [
    "weed-enterprise-linux_amd64.tar.gz",
    "weed-volume-enterprise_rdma_large_disk_linux_amd64.tar.gz",
    "seaweedkv-lmcache-native-preview_cp312_linux_amd64.tar.gz",
    "seaweedkv-lmcache-native-preview_cp312_linux_amd64.tar.gz.sha256",
    "seaweedkv-preview-pair.json",
]
SKILL = b"---\nname: seaweedfs-kvcache\ndescription: Install SeaweedFS KV cache.\n---\n\n# KV cache\n"


class ReleaseDocsAssets(unittest.TestCase):
    def test_red_release_4_53_has_no_docs(self):
        self.assertEqual(check.missing(RELEASE_4_53), check.expected())

    def test_green_release_with_docs(self):
        self.assertEqual(check.missing(RELEASE_4_53 + check.expected()), [])

    def test_a_doc_without_its_sha256_is_missing(self):
        self.assertEqual(check.missing(RELEASE_4_53 + ["kvcache-SKILL.md", "KVCACHE-QUICKSTART.md",
                                                       "KVCACHE-QUICKSTART.md.sha256"]),
                         ["kvcache-SKILL.md.sha256"])

    def test_cli_names_each_missing_asset(self):
        with tempfile.TemporaryDirectory() as d:
            names = pathlib.Path(d, "names")
            names.write_text("\n".join(RELEASE_4_53) + "\n")
            r = subprocess.run([sys.executable, str(HERE / "check-kvcache-release-docs.py"), "assets", "4.53",
                                str(names)], capture_output=True, text=True)
            self.assertEqual(r.returncode, 1)
            self.assertIn("KV_DOCS_MISSING: release 4.53 lacks kvcache-SKILL.md", r.stderr)
            names.write_text("\n".join(RELEASE_4_53 + check.expected()) + "\n")
            r = subprocess.run([sys.executable, str(HERE / "check-kvcache-release-docs.py"), "assets", "4.54",
                                str(names)], capture_output=True, text=True)
            self.assertEqual(r.returncode, 0, r.stderr)


class ReleaseDocsContent(unittest.TestCase):
    def test_green_public_doc(self):
        self.assertEqual(check.problems("kvcache-SKILL.md", SKILL + b"See [steps](#install) and "
                                        b"[releases](https://github.com/seaweedfs/artifactory/releases).\n"), [])

    def test_private_repo_and_relative_links_refused(self):
        bad = check.problems("KVCACHE-QUICKSTART.md", b"# Q\nSee [guide](../product-layer/x.md) and "
                             b"https://github.com/seaweedfs/seaweed-mono/blob/main/README.md\n")
        self.assertEqual(len(bad), 2, bad)
        self.assertIn("private seaweed-mono", bad[0])
        self.assertIn("relative link '../product-layer/x.md'", bad[1])

    def test_skill_front_matter_crlf_empty_and_encoding(self):
        self.assertIn("front matter", check.problems("kvcache-SKILL.md", b"# no front matter\n")[0])
        self.assertIn("CR line endings", check.problems("kvcache-SKILL.md", SKILL.replace(b"\n", b"\r\n"))[0])
        self.assertIn("empty", check.problems("KVCACHE-QUICKSTART.md", b"\n")[0])
        self.assertEqual(check.problems("KVCACHE-QUICKSTART.md", b"\xff\xfe"), ["KVCACHE-QUICKSTART.md: not UTF-8"])


class ReleaseDocsJob(unittest.TestCase):
    def setUp(self):
        wf = yaml.safe_load((HERE.parent / "workflows/rust_volume_release.yml").read_text(encoding="utf-8"))
        self.job = wf["jobs"]["publish-kvcache-docs"]
        self.text = "\n".join(str(s.get("run", "")) for s in self.job["steps"])

    def test_docs_come_from_the_release_source_commit(self):
        self.assertIn("build-rust-rdma-enterprise-linux-amd64", self.job["needs"])
        env = [s.get("env", {}) for s in self.job["steps"]]
        self.assertIn("${{ needs.build-rust-rdma-enterprise-linux-amd64.outputs.mono_sha }}",
                      [e.get("SOURCE_SHA") for e in env])
        self.assertIn('checkout --detach "$SOURCE_SHA"', self.text)
        self.assertIn("verify-enterprise-checkout.py", self.text)

    def test_stages_checks_uploads_and_rechecks_the_release(self):
        self.assertIn("docs/skills/kvcache-preview/SKILL.md:kvcache-SKILL.md", self.text)
        self.assertIn("docs/KVCACHE-QUICKSTART.md:KVCACHE-QUICKSTART.md", self.text)
        self.assertIn("KV_DOCS_MISSING", self.text)
        self.assertIn("check-kvcache-release-docs.py content", self.text)
        self.assertIn("check-kvcache-release-docs.py assets", self.text)
        upload = next(s for s in self.job["steps"] if "action-gh-release" in s.get("uses", ""))
        self.assertTrue(upload["with"]["fail_on_unmatched_files"])
        self.assertEqual(upload["with"]["files"].split(), check.expected())


if __name__ == "__main__":
    unittest.main()
