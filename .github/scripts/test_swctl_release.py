#!/usr/bin/env python3
"""Check that swctl ships with the same source/tag/commit as enterprise weed."""
from pathlib import Path
import unittest

import yaml


class SwctlRelease(unittest.TestCase):
    def test_linux_assets_use_enterprise_release_binding(self):
        workflow = yaml.safe_load((Path(__file__).resolve().parents[1] / 'workflows/release.yml').read_text())
        job = workflow['jobs']['build-release-binaries']
        steps = job['steps']
        swctl = [s for s in steps if s.get('with', {}).get('binary_name') == 'swctl']
        self.assertEqual(len(swctl), 1, 'enterprise release has no swctl asset step')
        step = swctl[0]
        self.assertEqual(step['if'], "${{ matrix.goos == 'linux' && (matrix.goarch == 'amd64' || matrix.goarch == 'arm64') }}")
        settings = step['with']
        weed = next(s['with'] for s in steps if s.get('name') == 'Go Release Binaries Normal Volume Size')
        self.assertEqual(step['uses'], next(s['uses'] for s in steps if s.get('name') == 'Go Release Binaries Normal Volume Size'))
        for key in ['release_tag', 'ldflags', 'pre_command', 'build_flags']:
            self.assertEqual(settings[key], weed[key], key)
        self.assertEqual(settings['project_path'], 'seaweedfs-enterprise-source/enterprise/cmd/swctl')
        self.assertEqual(settings['asset_name'], 'swctl-enterprise-${{ matrix.goos }}_${{ matrix.goarch }}')


if __name__ == '__main__':
    unittest.main()
