import hashlib
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

    W = "seaweedkv_native-4.54.0-cp312-cp312-linux_x86_64.whl"

    def wheel_release(self, root, sha, good=b"verified wheel bytes"):
        """Three paired assets whose preview BUILD-MANIFEST lists the standalone wheel, plus that wheel + .sha256."""
        for name in (pair.SERVER, pair.OBJECT):
            self.asset(root, name, sha)
        raw = json.dumps({"mono_commit": sha, "wheels": [
            {"name": self.W, "sha256": hashlib.sha256(good).hexdigest()}]}).encode()
        with tarfile.open(root / pair.PREVIEW, "w:gz") as archive:
            member = tarfile.TarInfo("BUILD-MANIFEST.json")
            member.size = len(raw)
            archive.addfile(member, io.BytesIO(raw))
        self.put_wheel(root, good)
        return {n: pair.digest(root/n) for n in (pair.SERVER, pair.OBJECT)}

    def put_wheel(self, root, data, sidecar=None):
        (root/self.W).write_bytes(data)
        (root/(self.W + ".sha256")).write_text(f"{sidecar or hashlib.sha256(data).hexdigest()}  {self.W}\n")

    def test_standalone_wheel_bound_to_the_preview_manifest(self):
        with tempfile.TemporaryDirectory(dir=SCRIPT.parent) as tmp:
            root = pathlib.Path(tmp)
            sha = "a"*40
            digests = self.wheel_release(root, sha)
            manifest = pair.create(root, sha, digests, wheel=True)            # green: pinned and verified
            self.assertEqual(manifest["wheel"]["name"], self.W)
            pair.verify(root, manifest, wheel=True)
            # RED (review repro): the standalone wheel replaced by other bytes WITH their own valid .sha256
            self.put_wheel(root, b"other wheel bytes")
            with self.assertRaisesRegex(ValueError, "bytes differ from the preview BUILD-MANIFEST"):
                pair.verify(root, manifest, wheel=True)
            with self.assertRaisesRegex(ValueError, "bytes differ"):
                pair.create(root, sha, digests, wheel=True)
            # the right bytes but a stale/forged .sha256 sidecar
            self.put_wheel(root, b"verified wheel bytes", sidecar="0"*64)
            with self.assertRaisesRegex(ValueError, ".sha256 differs"):
                pair.verify(root, manifest, wheel=True)
            self.put_wheel(root, b"verified wheel bytes")
            pair.verify(root, manifest, wheel=True)                           # restored: green again
            # a second standalone wheel, or none, is refused
            other = root/"seaweedkv_native-4.53.0-cp312-cp312-linux_x86_64.whl"
            other.write_bytes(b"x")
            with self.assertRaisesRegex(ValueError, "exactly one standalone"):
                pair.verify(root, manifest, wheel=True)
            other.unlink()
            (root/self.W).unlink()
            with self.assertRaisesRegex(ValueError, "exactly one standalone"):
                pair.verify(root, manifest, wheel=True)

    def test_wheel_selection_requires_a_pinned_wheel(self):
        with tempfile.TemporaryDirectory(dir=SCRIPT.parent) as tmp:
            root = pathlib.Path(tmp)
            sha = "a"*40
            digests = self.wheel_release(root, sha)
            old = pair.create(root, sha, digests)                             # a manifest from before the wheel
            pair.verify(root, old)                                            # still valid for the three assets
            with self.assertRaisesRegex(ValueError, "does not pin a standalone wheel"):
                pair.verify(root, old, wheel=True)                            # but never selects a standalone wheel

    def test_workflow_pins_the_wheel_when_pairing(self):
        text = (SCRIPT.parents[1]/"workflows/rust_volume_release.yml").read_text(encoding="utf-8")
        self.assertIn('kv-native-preview-pair.py create . "$SOURCE_SHA" "$SERVER_DIGESTS" --wheel', text)

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
