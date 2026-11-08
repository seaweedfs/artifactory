#!/usr/bin/env python3
"""Exercise the workflow's real run bodies with bounded fake Cargo/test binaries."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

import yaml

REPO = Path(__file__).resolve().parents[2]
WORKFLOW = REPO / '.github/workflows/rdma-softroce-tests.yml'


class SelectedRustTargets(unittest.TestCase):
    def setUp(self):
        jobs = yaml.safe_load(WORKFLOW.read_text())['jobs']
        self.steps = [s for j in jobs.values() for s in j['steps']]

    def step(self, prefix):
        matches = [s['run'] for s in self.steps if s.get('name', '').startswith(prefix)]
        self.assertEqual(len(matches), 1, prefix)
        return matches[0]

    def invoke(self, body, summary, status=0):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            volume = root / 'seaweedfs-source/enterprise/seaweed-volume'
            (volume / 'tests').mkdir(parents=True)
            (volume / 'tests/rdma_selected_e2e.rs').write_text('// fixture\n')
            deps = volume / 'target/debug/deps'
            deps.mkdir(parents=True)
            binary = deps / 'rdma_selected_e2e-fixture'
            binary.write_text('#!/bin/bash\ntest "$RXE_IP" = 192.0.2.10 || exit 92\nprintf "%s\\n" "$FAKE_SUMMARY"\nexit "$FAKE_EXIT"\n')
            binary.chmod(0o755)
            scripts = root / '.github/scripts'
            scripts.mkdir(parents=True)
            helper = REPO / '.github/scripts/run-rust-tests-nonzero.sh'
            if helper.exists():
                shutil.copyfile(helper, scripts / helper.name)
                (scripts / helper.name).chmod(0o755)
            fake = root / 'bin'
            fake.mkdir()
            cargo = fake / 'cargo'
            cargo.write_text('#!/usr/bin/python3\nimport json,os,sys\nassert "rdma,kvcache,test-support" in sys.argv,sys.argv\nassert "rdma_inventory_startup" in sys.argv,sys.argv\nprint(json.dumps({"reason":"compiler-artifact","target":{"name":"rdma_inventory_startup"},"profile":{"test":True},"executable":os.environ["FAKE_BINARY"]}))\n')
            cargo.chmod(0o755)
            env = dict(os.environ, PATH=str(fake) + ':/usr/bin:/bin', GITHUB_WORKSPACE=td,
                       RUNNER_ENVIRONMENT='self-hosted', RXE_IP='192.0.2.10',
                       RUST_BACKTRACE='1', FAKE_BINARY=str(binary),
                       FAKE_SUMMARY=summary, FAKE_EXIT=str(status))
            # Memlock is preprovisioned by the runner, outside this caller test.
            env['BASH_FUNC_ulimit%%'] = '() { return 0; }'
            return subprocess.run(['bash', '-e', '-o', 'pipefail', '-c', body],
                                  cwd=root, env=env, capture_output=True, text=True, timeout=10)

    def check_run(self, body, summary, expected, status=0):
        q = self.invoke(body, summary, status)
        self.assertEqual(q.returncode == 0, expected, q.stdout + q.stderr)

    def test_volume_features_include_kvcache(self):
        body = self.step('Build shipped volume server RDMA e2e')
        self.assertIn('cargo test --features rdma,kvcache,test-support', body)

    def test_selected_volume_zero_is_failure(self):
        self.check_run(self.step('Run shipped volume server RDMA e2e'),
                       'test result: ok. 0 passed; 0 failed; 0 ignored;', False)

    def test_selected_volume_one_passes(self):
        self.check_run(self.step('Run shipped volume server RDMA e2e'),
                       'test result: ok. 1 passed; 0 failed; 0 ignored;', True)

    def test_inventory_workflow_caller(self):
        body = self.step('Shipped volume inventory startup')
        for summary, status, expected in [
            ('test result: ok. 4 passed; 0 failed; 0 ignored;', 0, True),
            ('test result: ok. 0 passed; 0 failed; 0 ignored;', 0, False),
            ('test result: ok. 0 passed; 0 failed; 4 ignored;', 0, False),
            ('no summary', 0, False),
            ('test result: ok. 1 passed; 0 failed;\ntest result: ok. 1 passed; 0 failed;', 0, False),
            ('test result: ok. 1 passed; 0 failed;', 23, False),
            ('test result: FAILED. 0 passed; 1 failed;', 101, False),
        ]:
            with self.subTest(summary=summary, status=status):
                self.check_run(body, summary, expected, status)


if __name__ == '__main__':
    unittest.main()
