import importlib.util
import io
import itertools
import json
import pathlib
import tarfile
import tempfile
import unittest

SCRIPT = pathlib.Path(__file__).with_name("kv-native-preview-pair.py")
spec = importlib.util.spec_from_file_location("pair", SCRIPT)
pair = importlib.util.module_from_spec(spec)
spec.loader.exec_module(pair)


class PairTest(unittest.TestCase):
    def asset(self, root, name, sha):
        raw = json.dumps({"mono_commit": sha}).encode() if name == pair.PREVIEW else sha.encode()
        with tarfile.open(root / name, "w:gz") as archive:
            member = tarfile.TarInfo("BUILD-MANIFEST.json" if name == pair.PREVIEW else "./source-commit.txt")
            member.size = len(raw)
            archive.addfile(member, io.BytesIO(raw))

    def test_pair_and_partial_failure(self):
        with tempfile.TemporaryDirectory(dir=SCRIPT.parent) as tmp:
            root = pathlib.Path(tmp)
            sha = "a"*40
            for name in pair.NAMES:
                self.asset(root, name, sha)
            digests = {n: pair.digest(root/n) for n in (pair.SERVER, pair.OBJECT)}
            manifest = pair.create(root, sha, digests)
            pair.verify(root, manifest)
            for name in pair.NAMES:
                self.asset(root, name, "b"*40)
                with self.assertRaises(ValueError):
                    pair.verify(root, manifest)
                with self.assertRaises(ValueError):
                    pair.create(root, sha, digests)
                self.asset(root, name, sha)
            with self.assertRaises(ValueError):
                pair.create(root, sha, {n: "0"*64 for n in digests})
            (root/pair.PREVIEW).unlink()
            with self.assertRaises(FileNotFoundError):
                pair.verify(root, manifest)

    def test_concurrent_overwrite_vs_serialized_runs(self):
        mixed = 0
        for order in itertools.permutations(("As", "Aw", "Bs", "Bw")):
            if order.index("As") > order.index("Aw") or order.index("Bs") > order.index("Bw"):
                continue
            server = wheel = None
            for event in order:
                if event[1] == "s":
                    server = event[0]
                else:
                    wheel = event[0]
            mixed += server != wheel
        self.assertEqual(mixed, 2)  # RED model without workflow serialization.
        for order in [("As", "Aw", "Bs", "Bw"), ("Bs", "Bw", "As", "Aw")]:
            self.assertEqual(order[-2][0], order[-1][0])
        workflow = SCRIPT.parents[1]/"workflows/rust_volume_release.yml"
        text = workflow.read_text(encoding="utf-8")
        self.assertIn("group: rust-enterprise-${{ inputs.tag_name || github.ref_name }}", text)
        self.assertIn("cancel-in-progress: false", text)
        self.assertIn('kv-native-preview-pair.py create . "$SOURCE_SHA" "$SERVER_DIGESTS"', text)


if __name__ == "__main__":
    unittest.main()
