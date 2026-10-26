import pathlib
import subprocess
import sys
import tempfile
import unittest

SCRIPT = pathlib.Path(__file__).with_name("verify-enterprise-checkout.py")


class CheckoutVersionTest(unittest.TestCase):
    def test_actual_checkout_controls(self):
        with tempfile.TemporaryDirectory() as directory:
            constants = pathlib.Path(directory) / "enterprise/weed/util/version/constants.go"
            constants.parent.mkdir(parents=True)
            constants.write_text("const (\n MAJOR_VERSION = int32(4)\n MINOR_VERSION = int32(51)\n)\n")
            def run(tag):
                return subprocess.run([sys.executable, str(SCRIPT), tag, directory], capture_output=True, text=True)
            for tag in ("4.51", "v4.51.2", "04.051"):
                self.assertEqual(run(tag).returncode, 0)
            # Earlier gate saw 4.51; mutable main advanced before this checkout.
            constants.write_text(constants.read_text().replace("int32(51)", "int32(52)"))
            result = run("4.51")
            self.assertEqual(result.returncode, 1)
            self.assertIn("cloned source version 4.52", result.stderr)
            self.assertEqual(run("4.52").returncode, 0)
            constants.write_text("not version constants")
            self.assertEqual(run("4.52").returncode, 1)
            constants.unlink()
            self.assertEqual(run("4.52").returncode, 1)
            self.assertEqual(run("latest").returncode, 0)


if __name__ == "__main__":
    unittest.main()
