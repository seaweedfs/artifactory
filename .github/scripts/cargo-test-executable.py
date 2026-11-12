#!/usr/bin/env python3
"""Select one test executable from this build's Cargo JSON, without layout guesses."""
import json
import os
from pathlib import Path
import sys


def main():
    manifest, target = sys.argv[1:]
    matches = []
    for line in Path(manifest).read_text().splitlines():
        event = json.loads(line)
        if (event.get('reason') == 'compiler-artifact'
                and event.get('target', {}).get('name') == target
                and 'test' in event.get('target', {}).get('kind', [])
                and event.get('profile', {}).get('test') is True
                and event.get('executable') is not None):
            matches.append(event['executable'])
    if len(matches) != 1 or not isinstance(matches[0], str):
        raise ValueError(f'expected exactly one Cargo test executable for {target}, got {len(matches)}')
    binary = Path(matches[0])
    if not binary.is_absolute() or not binary.is_file() or not os.access(binary, os.X_OK):
        raise ValueError(f'Cargo test executable is unavailable for {target}')
    print(binary)


if __name__ == '__main__':
    try:
        main()
    except (OSError, ValueError, TypeError, KeyError) as error:
        print(f'::error::{error}', file=sys.stderr)
        sys.exit(1)
