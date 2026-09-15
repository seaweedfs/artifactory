#!/usr/bin/env python3
"""Tests-first contract for D-14 T3 server address staging."""
import argparse
import ast
import pathlib
import tempfile

ROOT = pathlib.Path(__file__).resolve().parents[1]
RUNNER = ROOT / "rdma-lab-ci" / "rdma-hw-suite"
HELPER = ROOT / "rdma-lab-ci" / "sweep" / "sweep3" / "sweep2-t3.py"
ROWS = (
    ("D14.1-wrong-address", "B/L6", "after-suite",
     "rdma-hw-suite --preflight-negative-control wrong-t3-server-ip",
     "preflight_status=fail failed_item=t3_server_ip case_count=0"),
    ("D14.2-explicit-local-address", "B/L6", "after-suite",
     "rdma-hw-suite --preflight-self-test t3-server-ip-ok",
     "named T3 host owns the explicit address and nested T3 consumes it"),
)


def constants(path):
    found = {}
    for node in ast.parse(path.read_text(encoding="utf-8")).body:
        if isinstance(node, ast.Assign) and len(node.targets) == 1 and isinstance(node.targets[0], ast.Name):
            try:
                found[node.targets[0].id] = ast.literal_eval(node.value)
            except (ValueError, TypeError):
                pass
    return found


def function(tree, name):
    return next((node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name == name), None)


def dotted(node):
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        return f"{dotted(node.value)}.{node.attr}".lstrip(".")
    return ""


def call_name(node):
    return dotted(node.func) if isinstance(node, ast.Call) else ""


def args_attr(node, name):
    return isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name) and node.value.id == "args" and node.attr == name


def assigned_name(value, root):
    for node in ast.walk(root):
        if isinstance(node, ast.Assign) and node.value is value and len(node.targets) == 1 and isinstance(node.targets[0], ast.Name):
            return node.targets[0].id
    return None


def validator_error(tree):
    func = function(tree, "t3_server_ip_error")
    if func is None or len(func.args.args) < 2:
        return "missing t3_server_ip_error ownership validator"
    ip_arg, host_arg = (arg.arg for arg in func.args.args[:2])
    remote = [node for node in ast.walk(func) if isinstance(node, ast.Call) and call_name(node) == "remote_text"]
    if len(remote) != 1 or len(remote[0].args) < 2:
        return "validator must make one remote_text address query"
    query = remote[0]
    if not isinstance(query.args[0], ast.Name) or query.args[0].id != host_arg:
        return "validator does not query the named T3 host"
    if not isinstance(query.args[1], ast.Constant) or query.args[1].value != "ip -o addr show":
        return "validator query is not ip -o addr show"
    output = assigned_name(query, func)
    split_remote = output and any(
        isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr == "splitlines"
        and isinstance(node.func.value, ast.Name) and node.func.value.id == output for node in ast.walk(func))
    wanted = set()
    owned = set()
    for node in ast.walk(func):
        if not isinstance(node, ast.Call) or call_name(node) != "ipaddress.ip_address" or len(node.args) != 1:
            continue
        target = assigned_name(node, func)
        if isinstance(node.args[0], ast.Name) and node.args[0].id == ip_arg and target:
            wanted.add(target)
        elif not isinstance(node.args[0], ast.Constant):
            for parent in ast.walk(func):
                if isinstance(parent, ast.Call) and node in ast.walk(parent) and isinstance(parent.func, ast.Attribute) \
                        and parent.func.attr in {"add", "append"} and isinstance(parent.func.value, ast.Name):
                    owned.add(parent.func.value.id)
    compared = any(
        isinstance(node, ast.Compare) and isinstance(node.left, ast.Name) and node.left.id in wanted
        and any(isinstance(op, ast.In) for op in node.ops)
        and any(isinstance(item, ast.Name) and item.id in owned for item in node.comparators)
        for node in ast.walk(func))
    return None if split_remote and owned and compared else "ownership is not derived from parsed remote ip -o addr output"


def preflight_error(tree):
    func = function(tree, "input_preflight")
    if func is None:
        return "input_preflight missing"
    required = any(
        isinstance(node, ast.Tuple) and len(node.elts) == 2 and isinstance(node.elts[0], ast.Constant)
        and node.elts[0].value == "t3_server_ip" and args_attr(node.elts[1], "t3_server_ip")
        for node in ast.walk(func))
    calls = [node for node in ast.walk(func) if isinstance(node, ast.Call) and call_name(node) == "t3_server_ip_error"]
    if not required:
        return "t3_server_ip is not required inside input_preflight"
    if len(calls) != 1 or len(calls[0].args) < 2 or not args_attr(calls[0].args[0], "t3_server_ip") \
            or not args_attr(calls[0].args[1], "t3_server_ssh"):
        return "ownership validator is detached from input_preflight"
    error = assigned_name(calls[0], func)
    bound = error and any(
        isinstance(node, ast.If) and isinstance(node.test, ast.Name) and node.test.id == error
        and any(isinstance(child, ast.Return) and isinstance(child.value, ast.Call)
                and call_name(child.value) == "fail" and len(child.value.args) >= 2
                and isinstance(child.value.args[0], ast.Constant) and child.value.args[0].value == "t3_server_ip"
                and isinstance(child.value.args[1], ast.Name) and child.value.args[1].id == error
                for child in node.body) for node in ast.walk(func))
    return None if bound else "validator failure is not returned from input_preflight as failed_item=t3_server_ip"


def nested_error(runner, helper):
    passed = any(
        isinstance(node, ast.Dict) and any(
            isinstance(key, ast.Constant) and key.value == "TESTOPS_T3_SERVER_IP" and args_attr(value, "t3_server_ip")
            for key, value in zip(node.keys, node.values)) for node in ast.walk(runner))
    env_names = set()
    for node in ast.walk(helper):
        if isinstance(node, ast.Subscript) and dotted(node.value) == "os.environ" \
                and isinstance(node.slice, ast.Constant) and node.slice.value == "TESTOPS_T3_SERVER_IP":
            name = assigned_name(node, helper)
            if name:
                env_names.add(name)
    written = any(
        isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr == "write_text"
        and any(isinstance(child, ast.Name) and child.id in env_names for arg in node.args for child in ast.walk(arg))
        for node in ast.walk(helper))
    attached = any(
        isinstance(node, ast.keyword) and node.arg == "server_env_file"
        and any(isinstance(child, ast.Name) and child.id == "server_env" for child in ast.walk(node.value))
        for node in ast.walk(helper))
    return None if passed and env_names and written and attached else "validated value is detached from the nested T3 command environment"


def errors(path, after_suite=False, helper_path=HELPER):
    if not after_suite:
        return []
    source = path.read_text(encoding="utf-8")
    result = []
    if constants(path).get("T3_SERVER_IP_REQUIRED") is not True:
        result.append("T3_SERVER_IP_REQUIRED must be true")
    for fallback in ("args.t3_server_ip = args.t3_server_ip or args.rdma_ip",
                     "args.t3_server_ssh = args.t3_server_ssh or args.m02"):
        if fallback in source:
            result.append("forbidden fallback " + fallback)
    try:
        runner = ast.parse(source)
        helper = ast.parse(helper_path.read_text(encoding="utf-8"))
    except (OSError, SyntaxError) as error:
        return result + [f"cannot parse suite/helper: {error}"]
    result += [item for item in (validator_error(runner), preflight_error(runner), nested_error(runner, helper)) if item]
    if '"wrong-t3-server-ip"' not in source or '"t3-server-ip-ok"' not in source:
        result.append("moving/still controls missing")
    if 'failed_item": "t3_server_ip"' not in source or 'case_count": 0' not in source:
        result.append("wrong-address control is not fail-before-case-1")
    return result


def self_test():
    future = '''
T3_SERVER_IP_REQUIRED = True
def t3_server_ip_error(value, host):
    output = remote_text(host, "ip -o addr show")
    wanted = ipaddress.ip_address(value)
    owned = set()
    for line in output.splitlines():
        address = line.split()[3].split("/")[0]
        owned.add(ipaddress.ip_address(address))
    return None if wanted in owned else "not owned"
def input_preflight(args, result_dir):
    required_inputs = (("t3_server_ip", args.t3_server_ip),)
    error = t3_server_ip_error(args.t3_server_ip, args.t3_server_ssh)
    if error: return fail("t3_server_ip", error)
controls = ("wrong-t3-server-ip", "t3-server-ip-ok")
failure = {"failed_item": "t3_server_ip", "case_count": 0}
t3_env = {"TESTOPS_T3_SERVER_IP": args.t3_server_ip}
'''
    helper = '''
t3_server_ip = os.environ["TESTOPS_T3_SERVER_IP"]
server_env = Path("server.env")
server_env.write_text(f"TESTOPS_T3_SERVER_IP={t3_server_ip}\\n")
scenario = dict(server_env_file=str(server_env))
'''
    with tempfile.TemporaryDirectory() as td:
        runner_path, helper_path = pathlib.Path(td) / "runner.py", pathlib.Path(td) / "helper.py"
        helper_path.write_text(helper, encoding="utf-8")
        runner_path.write_text(future, encoding="utf-8")
        assert not errors(runner_path, True, helper_path), errors(runner_path, True, helper_path)
        unused = future.replace('output = remote_text(host, "ip -o addr show")',
                                'unused = remote_text(host, "ip -o addr show")\n    output = "1: lo inet 192.0.2.1/32"')
        runner_path.write_text(unused, encoding="utf-8")
        assert any("parsed remote" in item for item in errors(runner_path, True, helper_path))
        detached = future.replace("    error = t3_server_ip_error(args.t3_server_ip, args.t3_server_ssh)\n", "") \
            + "\nerror = t3_server_ip_error(args.t3_server_ip, args.t3_server_ssh)\n"
        runner_path.write_text(detached, encoding="utf-8")
        helper_path.write_text(helper.replace("scenario = dict(server_env_file=str(server_env))", "detached = str(server_env)"), encoding="utf-8")
        failures = errors(runner_path, True, helper_path)
        assert any("input_preflight" in item for item in failures)
        assert any("nested T3" in item for item in failures)
        helper_path.write_text(helper, encoding="utf-8")
        runner_path.write_text(future.replace('if error: return fail("t3_server_ip", error)',
                                               'if error: fail("t3_server_ip", error)'), encoding="utf-8")
        assert any("not returned" in item for item in errors(runner_path, True, helper_path))
        nested = future.replace('if error: return fail("t3_server_ip", error)',
                                'if error:\n        if False: return fail("t3_server_ip", error)')
        runner_path.write_text(nested, encoding="utf-8")
        assert any("not returned" in item for item in errors(runner_path, True, helper_path))
    print("D14-SELF-TEST PASS future=PASS unused_remote_output=RED detached_call_env=RED nonreturning_fail=RED nested_fail=RED")


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
