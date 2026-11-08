import os
import pathlib
import stat
import subprocess
import tempfile
import unittest

SCRIPT = pathlib.Path(os.environ.get(
    "HOUSEKEEPING_SCRIPT",
    pathlib.Path(__file__).parents[1] / "actions/runner-housekeeping/housekeeping.sh",
))

# Fake docker: `docker info` prints FAKE_DOCKER_ROOT (or fails when it is empty);
# every other call is appended to FAKE_LOG so the test can see what was pruned.
FAKE_DOCKER = """#!/usr/bin/env bash
echo "docker $*" >> "$FAKE_LOG"
if [ "$1" = info ]; then
  [ -n "${FAKE_DOCKER_ROOT:-}" ] || exit 1
  echo "$FAKE_DOCKER_ROOT"
fi
exit 0
"""

# Fake df: free space depends on the path asked about (the last argument).
FAKE_DF = """#!/usr/bin/env bash
echo "df $*" >> "$FAKE_LOG"
path="${@: -1}"
if [ "$path" = / ]; then free="$FAKE_FREE_SLASH"; else free="$FAKE_FREE_DOCKER"; fi
echo " Avail"
echo " ${free}G"
"""


class HousekeepingTest(unittest.TestCase):
    def run_script(self, free_slash, free_docker, docker_root="/data/docker"):
        with tempfile.TemporaryDirectory() as d:
            bin_dir = pathlib.Path(d) / "bin"
            bin_dir.mkdir()
            for name, body in (("docker", FAKE_DOCKER), ("df", FAKE_DF)):
                p = bin_dir / name
                p.write_text(body)
                p.chmod(p.stat().st_mode | stat.S_IXUSR)
            log = pathlib.Path(d) / "calls.log"
            log.touch()
            env = dict(os.environ,
                       PATH=f"{bin_dir}{os.pathsep}{os.environ['PATH']}",
                       FAKE_LOG=str(log), FAKE_DOCKER_ROOT=docker_root,
                       FAKE_FREE_SLASH=free_slash, FAKE_FREE_DOCKER=free_docker,
                       MIN_FREE_GB="150")
            result = subprocess.run(["bash", str(SCRIPT)], env=env, capture_output=True, text=True)
            return result, log.read_text()

    def pruned(self, calls):
        return [c for c in calls.splitlines() if "prune" in c]

    def test_full_slash_does_not_prune_when_docker_disk_has_room(self):
        # The incident: "/" below the floor, Docker's own disk has plenty.
        result, calls = self.run_script(free_slash="145", free_docker="321")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.pruned(calls), [])
        self.assertIn("df -BG --output=avail /data/docker", calls)

    def test_tight_docker_disk_prunes_both_with_the_14_day_filter(self):
        result, calls = self.run_script(free_slash="500", free_docker="100")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.pruned(calls), [
            "docker builder prune -f --filter until=336h",
            "docker image prune -f --filter until=336h",
        ])

    def test_at_the_floor_does_not_prune(self):
        result, calls = self.run_script(free_slash="10", free_docker="150")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.pruned(calls), [])

    def test_docker_info_failure_does_not_prune_or_fail_the_job(self):
        result, calls = self.run_script(free_slash="10", free_docker="10", docker_root="")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.pruned(calls), [])
        self.assertIn("not pruning", result.stdout)

    def test_unreadable_free_space_does_not_prune_or_fail_the_job(self):
        result, calls = self.run_script(free_slash="10", free_docker="")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.pruned(calls), [])
        self.assertIn("not pruning", result.stdout)


if __name__ == "__main__":
    unittest.main()
