#!/usr/bin/env python3
"""Tests-first contract for D-14 T3 server address staging."""
import argparse
import ast
import pathlib
import tempfile

ROOT = pathlib.Path(__file__).resolve().parents[1]
RUNNER = ROOT / "rdma-lab-ci" / "rdma-hw-suite"
ROWS = (
    (
        "D14.1-wrong-address",
        "B/L6",
        "after-suite",
        "rdma-hw-suite --preflight-negative-control wrong-t3-server-ip",
        "preflight_status=fail failed_item=t3_server_ip case_count=0",
    ),
    (
        "D14.2-explicit-local-address",
        "B/L6",
        "after-suite",
        "rdma-hw-suite --preflight-self-test t3-server-ip-ok",
        "explicit T3 server IP belongs to the local T3 host and remains unchanged",
    ),
)


def constants(path):
    tree = ast.parse(path.read_text(encoding="utf-8"))
    found = {}
    for node in tree.body:
        if isinstance(node, ast.Assign) and len(node.targets) == 1 and isinstance(node.targets[0], ast.Name):
            try:
                found[node.targets[0].id] = ast.literal_eval(node.value)
            except (ValueError, TypeError):
                pass
    return found


def errors(path, after_suite=False):
    source = path.read_text(encoding="utf-8")
    if not after_suite:
        return []
    values = constants(path)
    result = []
    if values.get("T3_SERVER_IP_REQUIRED") is not True:
        result.append("T3_SERVER_IP_REQUIRED must be true")
    if "args.t3_server_ip = args.t3_server_ip or args.rdma_ip" in source:
        result.append("T3 server IP still defaults to the M02 RDMA IP")
    for token in (
        "t3_server_ip_error",
        "wrong-t3-server-ip",
        "t3-server-ip-ok",
        'failed_item": "t3_server_ip"',
        'case_count": 0',
    ):
        if token not in source:
            result.append(f"missing D-14 evidence token {token}")
    return result


def self_test():
    source = RUNNER.read_text(encoding="utf-8")
    future = source.replace("args.t3_server_ip = args.t3_server_ip or args.rdma_ip", "")
    future += (
        "\nT3_SERVER_IP_REQUIRED = True\n"
        "# t3_server_ip_error wrong-t3-server-ip t3-server-ip-ok\n"
        "# preflight_status fail failed_item\": \"t3_server_ip\" case_count\": 0\n"
    )
    with tempfile.TemporaryDirectory() as td:
        candidate = pathlib.Path(td) / "runner.py"
        candidate.write_text(future, encoding="utf-8")
        assert not errors(candidate, True), errors(candidate, True)
        candidate.write_text(future.replace("T3_SERVER_IP_REQUIRED = True", "T3_SERVER_IP_REQUIRED = False"), encoding="utf-8")
        assert any("must be true" in error for error in errors(candidate, True))
        candidate.write_text(future + "\nargs.t3_server_ip = args.t3_server_ip or args.rdma_ip\n", encoding="utf-8")
        assert any("M02 RDMA IP" in error for error in errors(candidate, True))
    print("D14-SELF-TEST PASS future=PASS wrong_default=RED optional_input=RED")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--after-suite", action="store_true")
    parser.add_argument("--emit-rows", action="store_true")
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args()
    if args.emit_rows:
        for row in ROWS:
            print(" | ".join(row))
        return 0
    if args.self_test:
        self_test()
        return 0
    found = errors(RUNNER, args.after_suite)
    if found:
        print("D14-CONTRACT RED " + "; ".join(found))
        return 1
    print(f"D14-CONTRACT PASS stage=S5 cell=B/L6 after_suite={args.after_suite}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
