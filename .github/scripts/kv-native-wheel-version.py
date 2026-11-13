#!/usr/bin/env python3
"""Print the seaweedkv-native wheel version for a release: the enterprise version of the checkout being built.

  kv-native-wheel-version.py TAG CHECKOUT  ->  MAJOR.MINOR.PATCH   (e.g. 4.54 -> 4.54.0, 4.54.2 -> 4.54.2)

MAJOR/MINOR come from the checkout's enterprise/weed/util/version/constants.go, parsed exactly as
verify-enterprise-checkout.py does, and must match the tag; PATCH is the tag's patch number (0 if absent). There is no
fallback: a tag that is not MAJOR.MINOR[.PATCH], an unparsable constants.go or a tag/constants mismatch exits 1 with
a named ::error:: (KV_WHEEL_VERSION_TAG / _UNPARSABLE / _MISMATCH), and the release job stops before building.
"""
import importlib.util
import pathlib
import re
import sys

_spec = importlib.util.spec_from_file_location(
    "verify_checkout", pathlib.Path(__file__).with_name("verify-enterprise-checkout.py"))
verify_checkout = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(verify_checkout)


class VersionError(Exception):
    pass


def wheel_version(tag, checkout):
    m = re.fullmatch(r"v?([0-9]+)\.([0-9]+)(?:\.([0-9]+))?", tag)
    if not m:
        raise VersionError(f"KV_WHEEL_VERSION_TAG: release tag {tag!r} is not MAJOR.MINOR[.PATCH]; "
                           "the wheel version is never guessed")
    try:
        verify_checkout.verify(tag, checkout)   # the same constants.go parse and tag check as the build legs
    except OSError as e:
        raise VersionError(f"KV_WHEEL_VERSION_UNPARSABLE: cannot read constants.go in {checkout}: {e}")
    except ValueError as e:
        code = "MISMATCH" if "does not match" in str(e) else "UNPARSABLE"
        raise VersionError(f"KV_WHEEL_VERSION_{code}: {e}")
    major, minor, patch = (int(g) if g is not None else 0 for g in m.groups())
    return f"{major}.{minor}.{patch}"


if __name__ == "__main__":
    try:
        if len(sys.argv) != 3:
            raise VersionError("KV_WHEEL_VERSION_TAG: usage: kv-native-wheel-version.py TAG CHECKOUT")
        print(wheel_version(sys.argv[1], sys.argv[2]))
    except VersionError as e:
        print(f"::error::{e}", file=sys.stderr)
        sys.exit(1)
