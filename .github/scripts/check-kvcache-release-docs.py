#!/usr/bin/env python3
"""Check the KV cache install docs that every enterprise release publishes (seaweed-mono is private).

  check-kvcache-release-docs.py assets TAG ASSET_NAMES_FILE   (one asset name per line, e.g. from
                                                              `gh release view TAG --json assets --jq '.assets[].name'`)
  check-kvcache-release-docs.py content FILE...               (the staged docs, before upload)

assets: the release must carry kvcache-SKILL.md, KVCACHE-QUICKSTART.md and a .sha256 for each.
content: each doc is non-empty UTF-8 text with LF line endings, the skill starts with its YAML front matter, and no
doc links into the private source repository (a public reader cannot open it): no seaweed-mono URL and no relative
Markdown link. Exit 0 when the check passes, else 1 naming each problem.
"""
import re
import sys

DOCS = ("kvcache-SKILL.md", "KVCACHE-QUICKSTART.md")
PRIVATE = re.compile(r"github\.com/seaweedfs/seaweed-mono\b")
RELATIVE_LINK = re.compile(r"\]\((?![a-z][a-z0-9+.-]*:|#)([^)\s]+)\)", re.I)   # ](path), not ](https:...) or ](#x)


def expected():
    return [n for d in DOCS for n in (d, d + ".sha256")]


def missing(names):
    have = {n.strip() for n in names if n.strip()}
    return [n for n in expected() if n not in have]


def problems(name, raw):
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError:
        return [f"{name}: not UTF-8"]
    found = []
    if not text.strip():
        found.append(f"{name}: empty")
    if "\r" in text:
        found.append(f"{name}: CR line endings")
    if name.endswith("SKILL.md") and not re.match(r"---\nname: [a-z0-9-]+\ndescription: \S", text):
        found.append(f"{name}: does not start with the skill front matter (name, description)")
    for n, line in enumerate(text.splitlines(), 1):
        if PRIVATE.search(line):
            found.append(f"{name}:{n}: links the private seaweed-mono repository")
        for target in RELATIVE_LINK.findall(line):
            found.append(f"{name}:{n}: relative link {target!r} (not resolvable from a release asset)")
    return found


if __name__ == "__main__":
    try:
        mode, args = sys.argv[1], sys.argv[2:]
        if mode == "assets":
            tag, path = args
            bad = [f"release {tag} lacks {n}" for n in missing(open(path, encoding="utf-8").read().splitlines())]
        elif mode == "content":
            if not args:
                raise ValueError("content needs at least one file")
            bad = [p for f in args for p in problems(f.replace("\\", "/").rsplit("/", 1)[-1], open(f, "rb").read())]
        else:
            raise ValueError(f"unknown mode {mode!r}; expected assets or content")
    except (IndexError, OSError, ValueError) as e:
        print(f"::error::{e}", file=sys.stderr)
        sys.exit(1)
    for p in bad:
        print(f"::error::KV_DOCS_{'MISSING' if mode == 'assets' else 'INVALID'}: {p}", file=sys.stderr)
    if bad:
        sys.exit(1)
    print("KV cache install docs: OK (" + ", ".join(expected() if mode == "assets" else args) + ")")
