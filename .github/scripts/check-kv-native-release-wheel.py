#!/usr/bin/env python3
"""Check that a release carries the seaweedkv-native wheel as a standalone asset at the release's version.

  check-kv-native-release-wheel.py TAG ASSET_NAMES_FILE     (one asset name per line, e.g. from
                                                              `gh release view TAG --json assets --jq '.assets[].name'`)

Required: seaweedkv_native-<MAJOR.MINOR.PATCH>-cp312-cp312-linux_x86_64.whl and its .sha256, where the version is the
tag's (4.54 -> 4.54.0). A wheel at any other version (e.g. the old hand-kept 0.1.0) does not count. Exit 0 when both
are present, else 1 naming what is missing.
"""
import re
import sys


def expected(tag):
    m = re.fullmatch(r"v?([0-9]+)\.([0-9]+)(?:\.([0-9]+))?", tag)
    if not m:
        raise ValueError(f"release tag {tag!r} is not MAJOR.MINOR[.PATCH]")
    major, minor, patch = (int(g) if g is not None else 0 for g in m.groups())
    wheel = f"seaweedkv_native-{major}.{minor}.{patch}-cp312-cp312-linux_x86_64.whl"
    return [wheel, wheel + ".sha256"]


def missing(tag, names):
    have = {n.strip() for n in names if n.strip()}
    return [n for n in expected(tag) if n not in have]


if __name__ == "__main__":
    try:
        tag, path = sys.argv[1], sys.argv[2]
        gone = missing(tag, open(path, encoding="utf-8").read().splitlines())
    except (IndexError, OSError, ValueError) as e:
        print(f"::error::{e}", file=sys.stderr)
        sys.exit(1)
    if gone:
        print(f"::error::release {tag} lacks the standalone seaweedkv-native wheel asset(s): {', '.join(gone)}",
              file=sys.stderr)
        sys.exit(1)
    print(f"release {tag} carries {', '.join(expected(tag))}")
