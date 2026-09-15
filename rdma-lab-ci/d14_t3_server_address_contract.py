#!/usr/bin/env python3
"""Tests-first contract for D-14 T3 server address staging."""
import argparse
import ast
import pathlib
import re
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
    for fallback in (
        "args.t3_server_ip = args.t3_server_ip or args.rdma_ip",
        "args.t3_server_ssh = args.t3_server_ssh or args.m02",
    ):
        if fallback in source:
            result.append(f"forbidden T3 server fallback: {fallback}")
    if not re.search(r'\("t3_server_ip",\s*args\.t3_server_ip\)', source):
        result.append("t3_server_ip is not in required_inputs")
    try:
        tree = ast.parse(source)
        validator = next(node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name == "t3_server_ip_error")
        validator_source = ast.get_source_segment(source, validator) or ""
    except (SyntaxError, StopIteration):
        validator_source = ""
    for token in ("ip -o addr show", "ipaddress.ip_address", "remote_text"):
        if token not in validator_source:
            result.append(f"t3_server_ip_error missing real ownership check {token}")
    if "t3_server_ip_error(args.t3_server_ip, args.t3_server_ssh)" not in source:
        result.append("input preflight does not call the ownership validator")
    if '"wrong-t3-server-ip"' not in source or '"t3-server-ip-ok"' not in source:
        result.append("moving/still controls are missing")
    if 'failed_item": "t3_server_ip"' not in source or 'case_count": 0' not in source:
        result.append("wrong-address control is not bound to fail before case 1")
    if '"TESTOPS_T3_SERVER_IP": args.t3_server_ip' not in source:
        result.append("validated T3 server IP is not passed to the nested helper")
    if "os.environ['TESTOPS_T3_SERVER_IP']" not in source and 'os.environ["TESTOPS_T3_SERVER_IP"]' not in source:
        result.append("nested helper does not consume the validated T3 server IP")
    return result


def self_test():
    source = RUNNER.read_text(encoding="utf-8")
    source = source.replace("args.t3_server_ip = args.t3_server_ip or args.rdma_ip", "")
    source = source.replace("args.t3_server_ssh = args.t3_server_ssh or args.m02", "")
    future = source + '''
T3_SERVER_IP_REQUIRED = True
required_inputs = (("t3_server_ip", args.t3_server_ip),)
def t3_server_ip_error(value, host):
    output = remote_text(host, "ip -o addr show")
    wanted = ipaddress.ip_address(value)
    return None if wanted in {ipaddress.ip_address("192.0.2.1")} else "not owned"
result = t3_server_ip_error(args.t3_server_ip, args.t3_server_ssh)
controls = ("wrong-t3-server-ip", "t3-server-ip-ok")
failure = {"failed_item": "t3_server_ip", "case_count": 0}
t3_env = {"TESTOPS_T3_SERVER_IP": args.t3_server_ip}
nested = os.environ["TESTOPS_T3_SERVER_IP"]
'''
    with tempfile.TemporaryDirectory() as td:
        candidate = pathlib.Path(td) / "runner.py"
        candidate.write_text(future, encoding="utf-8")
        assert not errors(candidate, True), errors(candidate, True)
        candidate.write_text(future.replace("T3_SERVER_IP_REQUIRED = True", "T3_SERVER_IP_REQUIRED = False"), encoding="utf-8")
        assert any("must be true" in error for error in errors(candidate, True))
        candidate.write_text(future + "\nargs.t3_server_ip = args.t3_server_ip or args.rdma_ip\n", encoding="utf-8")
        assert any("forbidden" in error for error in errors(candidate, True))
        candidate.write_text(future.replace('output = remote_text(host, "ip -o addr show")', 'output = "marker only"'), encoding="utf-8")
        assert any("real ownership check" in error for error in errors(candidate, True))
    print("D14-SELF-TEST PASS future=PASS wrong_default=RED optional_input=RED marker_only_validator=RED")


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
