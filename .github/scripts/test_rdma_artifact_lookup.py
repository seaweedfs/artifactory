#!/usr/bin/env python3
"""Run the shipping workflow bodies against Cargo's reported executable paths."""
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


class CargoArtifactLookup(unittest.TestCase):
    def invoke(self, family, layout='build', records=1, status=0):
        steps = yaml.safe_load(WORKFLOW.read_text())['jobs']['rdma-softroce-e2e']['steps']
        prefixes = {
            'vfs': ('Run RDMA e2e over', 'sw-rdma-vfs', 'rdma_lifecycle_e2e', 'vfs-e2e.json'),
            'object': ('Run object/loader RDMA', 'sw-rdma-object', 'rdma_object_e2e', 'sw-rdma-object-e2e.json'),
            'volume': ('Run shipped volume server RDMA', '', 'rdma_volume_e2e', 'volume-e2e.json'),
        }
        prefix, component, target, manifest = prefixes[family]
        body = next(s['run'] for s in steps if s.get('name', '').startswith(prefix))
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            workspace = root / 'seaweedfs-source/enterprise' / ('seaweed-volume' if family == 'volume' else 'rust')
            for part in ['sw-rdma-vfs', 'sw-rdma-loader', 'sw-rdma-object']:
                (root / 'seaweedfs-source/enterprise/rust' / part / 'tests').mkdir(parents=True)
            tests = workspace / component / 'tests'
            tests.mkdir(parents=True, exist_ok=True)
            (tests / (target + '.rs')).write_text('// discovery fixture\n')
            executable = workspace / ('target/debug/build/pkg/hash/out' if layout == 'build' else 'target/debug/deps') / (target + '-hash')
            executable.parent.mkdir(parents=True)
            executable.write_text('#!/bin/bash\necho invoked > "$CALLS"\necho "test result: ok. 1 passed; 0 failed; 0 ignored; 0 measured; 0 filtered out; finished in 0.01s"\nexit "$TEST_STATUS"\n')
            executable.chmod(0o755)
            artifact = {'reason': 'compiler-artifact', 'target': {'name': target, 'kind': ['test']},
                        'profile': {'test': True}, 'executable': str(executable)}
            (workspace / manifest).write_text(''.join(json.dumps(artifact) + '\n' for _ in range(records)))
            scripts = root / '.github/scripts'
            shutil.copytree(REPO / '.github/scripts', scripts)
            for script in scripts.glob('*.sh'):
                script.write_text(script.read_text())
                script.chmod(0o755)
            env = dict(os.environ, GITHUB_WORKSPACE=td, RUNNER_ENVIRONMENT='self-hosted',
                       RXE_IP='192.0.2.10', RUST_BACKTRACE='1', CALLS=str(root / 'calls'), TEST_STATUS=str(status))
            env['BASH_FUNC_ulimit%%'] = '() { return 0; }'
            result = subprocess.run(['bash', '-e', '-o', 'pipefail', '-c', body], cwd=root,
                                    env=env, capture_output=True, text=True, timeout=10)
            return result, (root / 'calls').exists()

    def test_shipping_callers_use_reported_path(self):
        for family in ['vfs', 'object', 'volume']:
            for layout in ['build', 'deps']:
                with self.subTest(family=family, layout=layout):
                    result, called = self.invoke(family, layout)
                    self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                    self.assertTrue(called)

    def test_missing_or_duplicate_artifact_refuses_dispatch(self):
        for family in ['vfs', 'object', 'volume']:
            for records in [0, 2]:
                with self.subTest(family=family, records=records):
                    result, called = self.invoke(family, records=records)
                    self.assertNotEqual(result.returncode, 0)
                    self.assertFalse(called)

    def test_cargo_json_builds_keep_rendered_diagnostics(self):
        text = WORKFLOW.read_text()
        self.assertIn('--message-format=json-render-diagnostics', text)
        self.assertNotRegex(text, r'--message-format=json(?!-render-diagnostics)')

    def test_binary_failure_is_preserved(self):
        for family in ['vfs', 'object', 'volume']:
            with self.subTest(family=family):
                result, called = self.invoke(family, status=23)
                self.assertNotEqual(result.returncode, 0)
                self.assertTrue(called)


if __name__ == '__main__':
    unittest.main()
