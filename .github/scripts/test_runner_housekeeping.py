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
# FAKE_DF_OUT replaces the whole output verbatim; FAKE_DF_RC sets the exit code.
FAKE_DF = """#!/usr/bin/env bash
echo "df $*" >> "$FAKE_LOG"
if [ -n "${FAKE_DF_OUT:-}" ]; then printf '%s\\n' "$FAKE_DF_OUT"; exit "${FAKE_DF_RC:-0}"; fi
path="${@: -1}"
if [ "$path" = / ]; then free="$FAKE_FREE_SLASH"; else free="$FAKE_FREE_DOCKER"; fi
echo " Avail"
echo " ${free}G"
exit "${FAKE_DF_RC:-0}"
"""


class HousekeepingTest(unittest.TestCase):
    def run_script(self, free_slash, free_docker, docker_root="/data/docker", df_out="", df_rc="0"):
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
                       FAKE_DF_OUT=df_out, FAKE_DF_RC=df_rc, MIN_FREE_GB="150")
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

    def assert_skipped_with_warning(self, result, calls):
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.pruned(calls), [])
        self.assertIn("::warning title=runner-housekeeping::", result.stdout)
        self.assertIn("not pruning", result.stdout)

    def test_docker_info_failure_does_not_prune_or_fail_the_job(self):
        self.assert_skipped_with_warning(*self.run_script(free_slash="10", free_docker="10", docker_root=""))

    def test_unreadable_free_space_does_not_prune_or_fail_the_job(self):
        self.assert_skipped_with_warning(*self.run_script(free_slash="10", free_docker=""))

    def test_df_failure_does_not_prune_or_fail_the_job(self):
        # df exits non-zero but still prints a low value: the exit code must win.
        for rc in ("1", "23"):
            with self.subTest(rc=rc):
                self.assert_skipped_with_warning(*self.run_script(free_slash="10", free_docker="10", df_rc=rc))

    def test_malformed_df_value_does_not_prune(self):
        # Digits buried in junk must not be scraped into a number.
        for out in (" Avail\nunreadable(5)G", " Avail\n 5", " Avail\n 5T", " Avail\n -5G", " Avail\n 5G 6G"):
            with self.subTest(out=out):
                self.assert_skipped_with_warning(*self.run_script(free_slash="10", free_docker="10", df_out=out))

    def test_multiple_df_values_do_not_prune(self):
        self.assert_skipped_with_warning(*self.run_script(
            free_slash="10", free_docker="10", df_out=" Avail\n 5G\n 6G"))

    def test_header_only_df_output_does_not_prune(self):
        self.assert_skipped_with_warning(*self.run_script(free_slash="10", free_docker="10", df_out=" Avail"))


if __name__ == "__main__":
    unittest.main()
