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
SCRIPT = HERE / "check-kvcache-release-docs.py"
spec = importlib.util.spec_from_file_location("docs_check", SCRIPT)
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
SKILL = (b"---\nname: seaweedfs-kvcache\ndescription: Install SeaweedFS KV cache.\n---\n\n# KV cache\n"
         b"Codes: [failure codes](reference/failure-codes.md#doctor), template [cluster.yaml](cluster.yaml).\n")
BUNDLE = {"SKILL.md": SKILL, "cluster.yaml": b"apiVersion: seaweedfs.io/cluster/v1\n",
          "reference/failure-codes.md": b"# Codes\nBack to [the skill](../SKILL.md).\n"}
INDEX = b"# SeaweedFS KV cache\nRead first: seaweedfs-kvcache-skill.tar.gz. Humans: KVCACHE-QUICKSTART.md.\n"


def run(*args):
    return subprocess.run([sys.executable, str(SCRIPT), *args], capture_output=True, text=True)


class ReleaseDocsAssets(unittest.TestCase):
    def test_red_release_4_53_has_no_docs(self):
        self.assertEqual(check.missing(RELEASE_4_53), check.expected())

    def test_green_release_with_docs(self):
        self.assertEqual(check.missing(RELEASE_4_53 + check.expected()), [])

    def test_a_doc_without_its_sha256_is_missing(self):
        names = RELEASE_4_53 + [n for n in check.expected() if n != "llms.txt.sha256"]
        self.assertEqual(check.missing(names), ["llms.txt.sha256"])

    def test_cli_names_each_missing_asset(self):
        with tempfile.TemporaryDirectory() as d:
            names = pathlib.Path(d, "names")
            names.write_text("\n".join(RELEASE_4_53) + "\n")
            r = run("assets", "4.53", str(names))
            self.assertEqual(r.returncode, 1)
            self.assertIn("KV_DOCS_MISSING: release 4.53 lacks seaweedfs-kvcache-skill.tar.gz", r.stderr)
            names.write_text("\n".join(RELEASE_4_53 + check.expected()) + "\n")
            self.assertEqual(run("assets", "4.54", str(names)).returncode, 0)


class ReleaseDocsContent(unittest.TestCase):
    def test_green_self_contained_bundle(self):
        self.assertEqual(check.bundle_problems(BUNDLE), [])

    def test_bundle_link_must_resolve_inside_the_bundle(self):
        bad = check.bundle_problems({**BUNDLE, "SKILL.md": SKILL + b"See [guide](../../product-layer/x.md) "
                                     b"and [gone](reference/missing.md).\n"})
        self.assertEqual(len(bad), 2, bad)
        self.assertIn("'../../product-layer/x.md' does not resolve", bad[0])
        self.assertIn("'reference/missing.md' does not resolve", bad[1])

    def test_bundle_needs_skill_front_matter_and_no_private_links(self):
        self.assertEqual(check.bundle_problems({"cluster.yaml": b"x\n"}), ["bundle: no SKILL.md at its top level"])
        self.assertIn("front matter", check.bundle_problems({**BUNDLE, "SKILL.md": b"# no front matter\n"})[0])
        bad = check.bundle_problems({**BUNDLE, "reference/failure-codes.md":
                                     b"https://github.com/seaweedfs/seaweed-mono/blob/main/x.go\n"})
        self.assertEqual(bad, ["bundle/reference/failure-codes.md:1: links the private seaweed-mono repository"])

    def test_review_titled_and_reference_links_are_checked(self):
        # Titled inline links and reference definitions are links too; each must resolve inside the bundle.
        bad = check.bundle_problems({**BUNDLE, "SKILL.md": SKILL + b'See [g](../x.md "Guide") and [r][ref].\n\n'
                                     b"[ref]: ../../product-layer/y.md\n[ok]: reference/failure-codes.md\n"})
        self.assertEqual(len(bad), 2, bad)
        self.assertIn("'../x.md' does not resolve", bad[0])
        self.assertIn("'../../product-layer/y.md' does not resolve", bad[1])
        self.assertIn("relative link", check.standalone_problems("llms.txt", b'[a](SKILL.md "t")\n')[1][0])
        self.assertIn("relative link", check.standalone_problems("llms.txt", b"[a]: SKILL.md\n")[1][0])
        self.assertEqual(check.standalone_problems("llms.txt", b'[a](https://x.test/a "t")\n[b]: https://x.test/b\n')[1], [])

    def test_review_every_private_repo_url_form_is_refused(self):
        for url in (b"https://github.com/seaweedfs/seaweed-mono/blob/main/a.md",
                    b"https://raw.githubusercontent.com/seaweedfs/seaweed-mono/main/a.md",
                    b"https://codeload.github.com/seaweedfs/seaweed-mono/zip/main",
                    b"git@github.com:seaweedfs/seaweed-mono.git"):
            self.assertIn("private seaweed-mono", check.text_problems("x", url + b"\n")[1][0], url)

    def test_text_rules_crlf_empty_encoding(self):
        self.assertIn("CR line endings", check.text_problems("x", b"a\r\nb\n")[1][0])
        self.assertIn("empty", check.text_problems("x", b"\n")[1][0])
        self.assertEqual(check.text_problems("x", b"\xff\xfe"), (None, ["x: not UTF-8"]))

    def test_quickstart_and_index_stand_alone(self):
        self.assertEqual(check.standalone_problems("KVCACHE-QUICKSTART.md", b"# Q\n[releases]"
                                                   b"(https://github.com/seaweedfs/artifactory/releases)\n")[1], [])
        self.assertIn("relative link", check.standalone_problems("KVCACHE-QUICKSTART.md", b"[a](SKILL.md)\n")[1][0])
        self.assertEqual(check.index_problems(INDEX), [])
        self.assertEqual(check.index_problems(b"# only the quickstart: KVCACHE-QUICKSTART.md\n"),
                         ["llms.txt: does not name seaweedfs-kvcache-skill.tar.gz"])

    def test_cli_content_on_staged_files(self):
        with tempfile.TemporaryDirectory() as d:
            root = pathlib.Path(d, "seaweedfs-kvcache")
            for name, raw in BUNDLE.items():
                (root / name).parent.mkdir(parents=True, exist_ok=True)
                (root / name).write_bytes(raw)
            quick, index = pathlib.Path(d, "KVCACHE-QUICKSTART.md"), pathlib.Path(d, "llms.txt")
            quick.write_bytes(b"# Quickstart\n")
            index.write_bytes(INDEX)
            r = run("content", str(root), str(quick), str(index))
            self.assertEqual(r.returncode, 0, r.stderr)
            (root / "reference/failure-codes.md").unlink()
            r = run("content", str(root), str(quick), str(index))
            self.assertEqual(r.returncode, 1)
            self.assertIn("KV_DOCS_INVALID: bundle/SKILL.md:7: link 'reference/failure-codes.md#doctor'", r.stderr)


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

    def test_stages_checks_packs_uploads_and_rechecks_the_release(self):
        for needle in ("docs/skills/seaweedfs-kvcache/SKILL.md", "docs/KVCACHE-QUICKSTART.md", "docs/kvcache-llms.txt",
                       "KV_DOCS_MISSING", "check-kvcache-release-docs.py content",
                       "check-kvcache-release-docs.py assets", "--sort=name", "gzip -n"):
            self.assertIn(needle, self.text)
        self.assertLess(self.text.index("check-kvcache-release-docs.py content"), self.text.index("gzip -n"))
        upload = next(s for s in self.job["steps"] if "action-gh-release" in s.get("uses", ""))
        self.assertTrue(upload["with"]["fail_on_unmatched_files"])
        self.assertEqual(upload["with"]["files"].split(), check.expected())


if __name__ == "__main__":
    unittest.main()
