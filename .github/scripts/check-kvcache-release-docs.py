#!/usr/bin/env python3
"""Check the KV cache install docs that every enterprise release publishes (seaweed-mono is private).

  check-kvcache-release-docs.py assets TAG ASSET_NAMES_FILE   (one asset name per line, e.g. from
                                                              `gh release view TAG --json assets --jq '.assets[].name'`)
  check-kvcache-release-docs.py content SKILL_DIR QUICKSTART INDEX   (the staged docs, before upload)

assets: the release carries the skill bundle, the quickstart and the llms.txt index, each with a .sha256.
content: the docs are for customers' own agents, so they must be readable from the release alone:
  - every file is non-empty UTF-8 text with LF line endings and never links the private seaweed-mono repository;
  - SKILL_DIR/SKILL.md starts with standard skill front matter (name, description);
  - a relative Markdown link inside the bundle resolves to a file in the bundle (no ../ escape);
  - the quickstart and the index have no relative links, and the index names every published doc.
Exit 0 when the check passes, else 1 naming each problem.
"""
import pathlib
import posixpath
import re
import sys

BUNDLE = "seaweedfs-kvcache-skill.tar.gz"
QUICKSTART = "KVCACHE-QUICKSTART.md"
INDEX = "llms.txt"
DOCS = (BUNDLE, QUICKSTART, INDEX)
# Any URL form of the private repository: github.com/..., raw.githubusercontent.com/..., codeload, git@github.com:...
PRIVATE = re.compile(r"seaweedfs/seaweed-mono\b")
INLINE_LINK = re.compile(r"\]\(\s*<?([^)\s>]+)>?(?:\s+[\"'(][^)]*)?\)")        # [t](target) or [t](target "title")
REFERENCE_LINK = re.compile(r"^\s{0,3}\[[^\]]+\]:\s*<?([^\s>]+)>?")             # [label]: target "title"
SCHEME = re.compile(r"[a-z][a-z0-9+.-]*:", re.I)


def expected():
    return [n for d in DOCS for n in (d, d + ".sha256")]


def missing(names):
    have = {n.strip() for n in names if n.strip()}
    return [n for n in expected() if n not in have]


def text_problems(name, raw):
    """-> (text or None, problems) for one published text file."""
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError:
        return None, [f"{name}: not UTF-8"]
    found = []
    if not text.strip():
        found.append(f"{name}: empty")
    if "\r" in text:
        found.append(f"{name}: CR line endings")
    for n, line in enumerate(text.splitlines(), 1):
        if PRIVATE.search(line):
            found.append(f"{name}:{n}: links the private seaweed-mono repository")
    return text, found


def links(text):
    """-> [(line number, target)] for every relative link: inline (titled or not) and reference definitions."""
    found = []
    for n, line in enumerate(text.splitlines(), 1):
        targets = INLINE_LINK.findall(line) + REFERENCE_LINK.findall(line)
        found += [(n, t) for t in targets if not SCHEME.match(t) and not t.startswith("#")]
    return found


def standalone_problems(name, raw):
    text, found = text_problems(name, raw)
    if text is not None:
        found += [f"{name}:{n}: relative link {t!r} (not resolvable from a release asset)" for n, t in links(text)]
    return text, found


def bundle_problems(files):
    """files: {posix path inside the bundle: bytes}. Relative links must stay inside and name a bundled file."""
    found = []
    if "SKILL.md" not in files:
        return ["bundle: no SKILL.md at its top level"]
    for path, raw in sorted(files.items()):
        text, bad = text_problems(f"bundle/{path}", raw)
        found += bad
        if text is None:
            continue
        if path == "SKILL.md" and not re.match(r"---\nname: [a-z0-9-]+\ndescription: \S", text):
            found.append("bundle/SKILL.md: does not start with the skill front matter (name, description)")
        for n, target in links(text):
            rel = posixpath.normpath(posixpath.join(posixpath.dirname(path), target.split("#", 1)[0]))
            if rel.startswith("../") or rel == ".." or rel.startswith("/") or rel not in files:
                found.append(f"bundle/{path}:{n}: link {target!r} does not resolve to a file in the bundle")
    return found


def index_problems(raw):
    text, found = standalone_problems(INDEX, raw)
    if text is not None:
        found += [f"{INDEX}: does not name {d}" for d in (BUNDLE, QUICKSTART) if d not in text]
    return found


def content(skill_dir, quickstart, index):
    root = pathlib.Path(skill_dir)
    files = {p.relative_to(root).as_posix(): p.read_bytes() for p in sorted(root.rglob("*")) if p.is_file()}
    return (bundle_problems(files) + standalone_problems(QUICKSTART, pathlib.Path(quickstart).read_bytes())[1]
            + index_problems(pathlib.Path(index).read_bytes()))


if __name__ == "__main__":
    try:
        mode, args = sys.argv[1], sys.argv[2:]
        if mode == "assets":
            tag, path = args
            bad = [f"release {tag} lacks {n}" for n in missing(open(path, encoding="utf-8").read().splitlines())]
        elif mode == "content":
            skill_dir, quickstart, index = args
            bad = content(skill_dir, quickstart, index)
        else:
            raise ValueError(f"unknown mode {mode!r}; expected assets or content")
    except (OSError, ValueError) as e:
        print(f"::error::{e}", file=sys.stderr)
        sys.exit(1)
    for p in bad:
        print(f"::error::KV_DOCS_{'MISSING' if mode == 'assets' else 'INVALID'}: {p}", file=sys.stderr)
    if bad:
        sys.exit(1)
    print("KV cache install docs: OK (" + ", ".join(expected() if mode == "assets" else args) + ")")
