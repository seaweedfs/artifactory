#!/usr/bin/env python3
"""Check the checkout we will build, rather than an earlier mutable-main read."""
import pathlib
import re
import sys


def verify(tag, checkout):
    match = re.fullmatch(r"v?([0-9]+)\.([0-9]+)(?:\.[0-9]+)?", tag)
    if not match:
        return f"Tag {tag!r} is nonnumeric; preserving the existing version-check skip."
    path = pathlib.Path(checkout) / "enterprise/weed/util/version/constants.go"
    text = path.read_text(encoding="utf-8")
    version = []
    for field in ("MAJOR_VERSION", "MINOR_VERSION"):
        values = re.findall(r"^\s*" + field + r"\s*=\s*int32\(([0-9]+)\)", text, re.M)
        if len(values) != 1:
            raise ValueError(f"cannot parse a unique {field} in {path}")
        version.append(int(values[0]))
    if tuple(map(int, match.groups()[:2])) != tuple(version):
        raise ValueError(f"release tag {tag} does not match cloned source version {version[0]}.{version[1]:02d} in {path}")
    return f"Release tag {tag} matches cloned source version {version[0]}.{version[1]:02d}."


if __name__ == "__main__":
    try:
        if len(sys.argv) != 3:
            raise ValueError("usage: verify-enterprise-checkout.py TAG CHECKOUT")
        print(verify(sys.argv[1], sys.argv[2]))
    except (ValueError, OSError) as error:
        print(f"::error::{error}", file=sys.stderr)
        sys.exit(1)
