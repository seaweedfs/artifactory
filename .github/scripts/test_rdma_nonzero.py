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
ONE_PASS = 'test result: ok. 1 passed; 0 failed; 0 ignored; 0 measured; 0 filtered out; finished in 0.01s'
PASS_IGNORED = 'test result: ok. 4 passed; 0 failed; 3 ignored; 0 measured; 0 filtered out; finished in 0.02s'
FAILED = 'test result: FAILED. 0 passed; 1 failed; 0 ignored; 0 measured; 0 filtered out; finished in 0.01s'
BAD_OK = 'test result: ok. 1 passed; 1 failed; 0 ignored; 0 measured; 0 filtered out; finished in 0.01s'


class SelectedRustTargets(unittest.TestCase):
    def setUp(self):
        jobs = yaml.safe_load(WORKFLOW.read_text())['jobs']
        self.steps = [s for j in jobs.values() for s in j['steps']]

    def step(self, prefix):
        matches = [s['run'] for s in self.steps if s.get('name', '').startswith(prefix)]
        self.assertEqual(len(matches), 1, prefix)
        return matches[0]

    def invoke(self, body, summary, status=0, targets=None, zero_target=''):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            volume = root / 'seaweedfs-source/enterprise/seaweed-volume'
            (volume / 'tests').mkdir(parents=True)
            for component in ['sw-rdma-vfs', 'sw-rdma-loader', 'sw-rdma-object']:
                tests = root / 'seaweedfs-source/enterprise/rust' / component / 'tests'
                tests.mkdir(parents=True)
                (tests / 'rdma_fixture_e2e.rs').write_text('// summary discovery fixture\n')
            targets = targets or ['rdma_selected_e2e']
            for target in targets:
                (volume / ('tests/' + target + '.rs')).write_text('// fixture\n')
            deps = volume / 'target/debug/deps'
            deps.mkdir(parents=True)
            for target in targets:
                binary = deps / (target + '-fixture')
                binary.write_text('#!/bin/bash\ntest "$RXE_IP" = 192.0.2.10 || exit 92\necho "${0##*/}" >> "$BINARY_CALLS"\nif [ "${0##*/}" = "$ZERO_TARGET-fixture" ]; then echo "test result: ok. 0 passed; 0 failed; 0 ignored; 0 measured; 0 filtered out; finished in 0.01s"; else printf "%s\\n" "$FAKE_SUMMARY"; fi\nexit "$FAKE_EXIT"\n')
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
            cargo.write_text('#!/usr/bin/python3\nimport json,os,sys\ntarget=sys.argv[sys.argv.index("--test")+1] if "--test" in sys.argv else ""\nassert ("rdma,test-support" if target=="rdma_inventory_resolve" else "rdma,kvcache,test-support") in sys.argv,sys.argv\nassert "rdma-dc" not in ",".join(sys.argv),sys.argv\nopen(os.environ["CARGO_ARGS"],"w").write(json.dumps(sys.argv[1:]))\nif "--message-format=json" in sys.argv:\n assert target in ("rdma_inventory_startup","rdma_inventory_resolve"),sys.argv\n print(json.dumps({"reason":"compiler-artifact","target":{"name":target},"profile":{"test":True},"executable":os.environ["FAKE_BINARY"]}))\n')
            cargo.chmod(0o755)
            env = dict(os.environ, PATH=str(fake) + ':/usr/bin:/bin', GITHUB_WORKSPACE=td,
                       RUNNER_ENVIRONMENT='self-hosted', RXE_IP='192.0.2.10',
                       RUST_BACKTRACE='1', FAKE_BINARY=str(binary),
                       FAKE_SUMMARY=summary, FAKE_EXIT=str(status),
                       ZERO_TARGET=zero_target, BINARY_CALLS=str(root / 'binary-calls'),
                       CARGO_ARGS=str(root / 'cargo-args'), GITHUB_STEP_SUMMARY=str(root / 'summary'))
            # Memlock is preprovisioned by the runner, outside this caller test.
            env['BASH_FUNC_ulimit%%'] = '() { return 0; }'
            result = subprocess.run(['bash', '-e', '-o', 'pipefail', '-c', body],
                                    cwd=root, env=env, capture_output=True, text=True, timeout=10)
            result.cargo_args = json.loads((root / 'cargo-args').read_text()) if (root / 'cargo-args').exists() else []
            result.binary_calls = (root / 'binary-calls').read_text().splitlines() if (root / 'binary-calls').exists() else []
            result.step_summary = (root / 'summary').read_text() if (root / 'summary').exists() else ''
            return result

    def test_exact_dc_compaction_exclusion_in_both_discovery_paths(self):
        targets = ['rdma_dc_compaction_repro_e2e', 'rdma_dc_write_contract_e2e',
                   'rdma_dc_compaction_repro_extra_e2e', 'rdma_startup_e2e']
        expected = set(targets) - {'rdma_dc_compaction_repro_e2e'}
        build = self.invoke(self.step('Build shipped volume server RDMA e2e'), ONE_PASS, targets=targets)
        with self.subTest(discovery='build'):
            self.assertEqual(build.returncode, 0, build.stdout + build.stderr)
            selected = {build.cargo_args[i+1] for i, value in enumerate(build.cargo_args) if value == '--test'}
            self.assertEqual(selected, expected)
        run = self.invoke(self.step('Run shipped volume server RDMA e2e'), ONE_PASS,
                          targets=targets, zero_target='rdma_dc_compaction_repro_e2e')
        with self.subTest(discovery='run'):
            self.assertEqual(run.returncode, 0, run.stdout + run.stderr)
            self.assertEqual(set(run.binary_calls), {target + '-fixture' for target in expected})
        retained_guard = self.invoke(self.step('Run shipped volume server RDMA e2e'), ONE_PASS,
                                     targets=targets, zero_target='rdma_dc_write_contract_e2e')
        self.assertNotEqual(retained_guard.returncode, 0, retained_guard.stdout + retained_guard.stderr)

    def test_dc_compaction_not_run_is_visible_in_summary(self):
        q = self.invoke(self.step('Test summary'), ONE_PASS,
                        targets=['rdma_dc_compaction_repro_e2e', 'rdma_dc_write_contract_e2e', 'rdma_startup_e2e'])
        self.assertEqual(q.returncode, 0, q.stdout + q.stderr)
        self.assertIn('NOT_RUN rdma_dc_compaction_repro_e2e', q.step_summary)
        self.assertIn('mlx5 DC', q.step_summary)
        selected = next(line for line in q.step_summary.splitlines() if line.startswith('- seaweed-volume ('))
        self.assertNotIn('rdma_dc_compaction_repro_e2e', selected)
        self.assertIn('rdma_dc_write_contract_e2e', selected)
        self.assertIn('rdma_startup_e2e', selected)
        self.assertIn('rdma_inventory_startup', selected)

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
                       ONE_PASS, True)

    def test_summary_candidates_are_unambiguous_in_both_callers(self):
        for prefix in ['Run shipped volume server RDMA e2e', 'Shipped volume inventory startup']:
            body = self.step(prefix)
            with self.subTest(caller=prefix, positive='passing-with-ignored'):
                self.check_run(body, PASS_IGNORED, True)
            for second in [FAILED, BAD_OK, 'test result: unreadable']:
                for order in [(ONE_PASS, second), (second, ONE_PASS)]:
                    with self.subTest(caller=prefix, summaries=order):
                        self.check_run(body, '\n'.join(order), False)
            with self.subTest(caller=prefix, same_line=True):
                self.check_run(body, ONE_PASS + ' ' + FAILED, False)
            with self.subTest(caller=prefix, invalid_suffix=True):
                self.check_run(body, ONE_PASS + ' unexpected', False)

    def test_inventory_workflow_caller(self):
        body = self.step('Shipped volume inventory startup')
        for summary, status, expected in [
            (PASS_IGNORED, 0, True),
            ('test result: ok. 0 passed; 0 failed; 0 ignored;', 0, False),
            ('test result: ok. 0 passed; 0 failed; 4 ignored;', 0, False),
            ('no summary', 0, False),
            (ONE_PASS + '\n' + ONE_PASS, 0, False),
            (ONE_PASS, 23, False),
            (FAILED, 101, False),
        ]:
            with self.subTest(summary=summary, status=status):
                self.check_run(body, summary, expected, status)

    def test_inventory_resolve_selected_guarded_caller(self):
        body = self.step('Shipped volume inventory resolve')
        for summary, status, expected in [
            (PASS_IGNORED, 0, True),
            ('test result: ok. 0 passed; 0 failed; 0 ignored;', 0, False),
            ('no summary', 0, False),
            (ONE_PASS + '\n' + FAILED, 0, False),
            (FAILED + '\n' + ONE_PASS, 0, False),
            (ONE_PASS, 23, False),
        ]:
            with self.subTest(summary=summary, status=status):
                q = self.invoke(body, summary, status, targets=['rdma_inventory_resolve'])
                self.assertEqual(q.returncode == 0, expected, q.stdout + q.stderr)
                if expected:
                    i = q.cargo_args.index('--test')
                    self.assertEqual(q.cargo_args[i + 1], 'rdma_inventory_resolve')
                    self.assertEqual(q.cargo_args[q.cargo_args.index('--features') + 1], 'rdma,test-support')
                    self.assertEqual(q.binary_calls, ['rdma_inventory_resolve-fixture'])


if __name__ == '__main__':
    unittest.main()
