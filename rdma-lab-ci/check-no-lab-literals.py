#!/usr/bin/env python3
"""Fail if committed files contain local lab endpoint literals."""
import argparse
import pathlib
import re
import subprocess
import sys
import tempfile

ROOT = pathlib.Path(__file__).resolve().parents[1]

PRIVATE_IP = r"(?:10[.][0-9]{1,3}[.][0-9]{1,3}[.][0-9]{1,3}|172[.](?:1[6-9]|2[0-9]|3[01])[.][0-9]{1,3}[.][0-9]{1,3}|192[.]168[.][0-9]{1,3}[.][0-9]{1,3})"
LAB_USER = "test" + "dev"
RDMA_DEVICE_PREFIX = "ro" + "cep"
PRIVATE_KEY_NAME = "id" + "_" + "ed" + "25519"
PATTERNS = [
    ("rfc1918-ipv4", re.compile(PRIVATE_IP)),
    ("ssh-target-private-ip", re.compile(r"\b[A-Za-z_][A-Za-z0-9_.-]*@" + PRIVATE_IP + r"\b")),
    ("ssh-target-lab-user", re.compile(r"\b" + LAB_USER + r"@[A-Za-z0-9_.-]+\b")),
    ("rdma-lab-device", re.compile(r"\b" + RDMA_DEVICE_PREFIX + r"[A-Za-z0-9_.-]*\b")),
    ("lab-private-key", re.compile(r"\b" + PRIVATE_KEY_NAME + r"\b")),
    ("lab-user-home", re.compile(r"/home/[A-Za-z0-9_.-]*(?:" + LAB_USER + r"|m01|m02)[A-Za-z0-9_.-]*\b")),
]

TEXT_SUFFIX_ALLOW = {
    "", ".bash", ".cfg", ".conf", ".env", ".example", ".ini", ".json", ".md",
    ".py", ".rs", ".sh", ".toml", ".txt", ".yaml", ".yml",
}


def tracked_files(root: pathlib.Path):
    out = subprocess.check_output(["git", "-C", str(root), "ls-files", "-z"], text=False)
    for raw in out.split(b"\0"):
        if raw:
            yield root / raw.decode()


def scan_files(paths):
    findings = []
    for path in paths:
        if not path.is_file() or path.suffix not in TEXT_SUFFIX_ALLOW:
            continue
        data = path.read_bytes()
        if b"\0" in data:
            continue
        text = data.decode("utf-8", errors="ignore")
        try:
            rel = path.relative_to(ROOT)
        except ValueError:
            rel = path
        for lineno, line in enumerate(text.splitlines(), 1):
            for name, pattern in PATTERNS:
                if pattern.search(line):
                    findings.append((str(rel), lineno, name, line.strip()))
    return findings


def report(findings):
    for rel, lineno, name, line in findings:
        print(f"{rel}:{lineno}: {name}: {line}")


def synthetic_bad_text():
    private_a = ".".join(["192", "168", "1", "184"])
    private_b = ".".join(["10", "0", "0", "3"])
    user_host = "test" + "dev" + "@" + private_a
    device = "ro" + "cep" + "1s0"
    key = "id" + "_" + "ed" + "25519"
    home = "/home/" + "test" + "dev" + "/.ssh/" + key
    return "\n".join([private_a, private_b, user_host, device, home]) + "\n"


def run_self_test(root: pathlib.Path):
    with tempfile.TemporaryDirectory() as td:
        bad = pathlib.Path(td) / "bad.txt"
        bad.write_text(synthetic_bad_text(), encoding="utf-8", newline="\n")
        bad_findings = scan_files([bad])
        if len(bad_findings) < 5:
            report(bad_findings)
            raise SystemExit("self-test failed: synthetic lab literals were not all detected")
    findings = scan_files(tracked_files(root))
    if findings:
        report(findings)
        raise SystemExit("committed lab literal check failed")
    print("self-test PASS and committed lab literal check PASS")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--self-test", action="store_true", help="prove detection on synthesized literals, then scan the repo")
    args = ap.parse_args()
    if args.self_test:
        run_self_test(ROOT)
        return
    findings = scan_files(tracked_files(ROOT))
    if findings:
        report(findings)
        raise SystemExit(1)
    print("committed lab literal check PASS")


if __name__ == "__main__":
    main()
