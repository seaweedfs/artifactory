#!/usr/bin/env python3
"""Tests-first contract for the D11.8 parent/head sample producers."""
import argparse
import ast
import pathlib
import subprocess
import sys
import tempfile

ROOT = pathlib.Path(__file__).resolve().parents[3]
STAGER = ROOT / "rdma-lab-ci" / "sweep" / "sweep3" / "stage-d11-paired.py"
PARENT = "f866249ca3bd845cb861b3a6fc65b560188a4770"
HEAD = "b77cf60a927db21d98e51bb68e13d64c1a8d40c3"
COMMANDS = {
    "SWEEP_D11_OBJECT_READ_A_CMD",
    "SWEEP_D11_OBJECT_READ_B_CMD",
    "SWEEP_D11_EC_READ_A_CMD",
    "SWEEP_D11_EC_READ_B_CMD",
}
ROWS = (
    ("D11.8a-stage", "A/L5", "after-stage", "stage-d11-paired.py --workdir /opt/work/<run>",
     "exact parent/head object+EC readers built below workdir; four commands emitted"),
    ("D11.8b-layout-still", "A/L5", "after-stage", "stage-d11-paired.py --self-test",
     "all source,target,binary,command paths share workdir filesystem"),
    ("D11.8c-layout-moving", "A/L5", "after-stage", "stage self-test outside-workdir fixture",
     "outside/missing executable commands fail before measurement"),
)


def constants(tree):
    found = {}
    for node in tree.body:
        if isinstance(node, ast.Assign) and len(node.targets) == 1 and isinstance(node.targets[0], ast.Name):
            try:
                found[node.targets[0].id] = ast.literal_eval(node.value)
            except (ValueError, TypeError):
                pass
    return found


def function(tree, name):
    return next((node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name == name), None)


def calls(node):
    return [item for item in ast.walk(node) if isinstance(item, ast.Call)] if node else []


def dotted(node):
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        return f"{dotted(node.value)}.{node.attr}".lstrip(".")
    return ""


def words(call):
    return {node.value for node in ast.walk(call) if isinstance(node, ast.Constant) and isinstance(node.value, str)}


def errors(path=STAGER, run_self_test=True):
    if not path.is_file():
        return ["missing stage-d11-paired.py"]
    source = path.read_text(encoding="utf-8")
    try:
        tree = ast.parse(source)
    except SyntaxError as error:
        return [f"stager syntax: {error}"]
    values = constants(tree)
    found = []
    if values.get("D11_PARENT_SHA") != PARENT or values.get("D11_HEAD_SHA") != HEAD:
        found.append("exact D11 parent/head SHAs are not pinned")
    if set(values.get("D11_COMMAND_VARS", ())) != COMMANDS:
        found.append("four D11 command variables are not exhaustive")
    if values.get("D11_MIN_FREE_BYTES") != 20 * 1024**3:
        found.append("20 GiB staging floor is not exact")
    layout = function(tree, "validate_layout")
    layout_calls = {dotted(call.func) for call in calls(layout)}
    layout_text = ast.get_source_segment(source, layout) if layout else ""
    if "Path.is_relative_to" not in layout_calls and ".is_relative_to(" not in layout_text:
        found.append("layout does not reject paths outside workdir")
    if "os.stat" not in layout_calls or ".st_dev" not in layout_text:
        found.append("layout does not bind paths to the workdir filesystem")
    build = function(tree, "stage_build")
    build_text = ast.get_source_segment(source, build) if build else ""
    git_calls = [call for call in calls(build) if {"git", "worktree", "add", "--detach"}.issubset(words(call))]
    cargo_calls = [call for call in calls(build) if {"cargo", "build", "--locked"}.issubset(words(call))]
    if len(git_calls) != 1 or not {"D11_PARENT_SHA", "D11_HEAD_SHA"}.issubset({node.id for node in ast.walk(build) if isinstance(node, ast.Name)}):
        found.append("stage_build does not call git worktree add at both pinned refs")
    if len(cargo_calls) != 1 or "CARGO_TARGET_DIR" not in build_text or not {"object_read", "ec_read"}.issubset(words(cargo_calls[0])):
        found.append("stage_build does not call cargo build --locked for both readers")
    if not COMMANDS.issubset({node.value for node in ast.walk(build) if isinstance(node, ast.Constant) and isinstance(node.value, str)}):
        found.append("stage_build does not bind all commands to built paths")
    target = next((node.value for node in ast.walk(build) if isinstance(node, ast.Assign)
                   and any(isinstance(item, ast.Name) and item.id == "target" for item in node.targets)), None)
    if target is None or not any(isinstance(node, ast.Name) and node.id == "workdir" for node in ast.walk(target)):
        found.append("CARGO_TARGET_DIR is not derived from workdir")
    emit = function(tree, "emit_commands")
    emit_text = ast.get_source_segment(source, emit) if emit else ""
    if "D11_COMMAND_VARS" not in emit_text:
        found.append("emit_commands does not iterate the exhaustive command set")
    if "os.access" not in {dotted(call.func) for call in calls(emit)} or "X_OK" not in emit_text:
        found.append("emitted command executables are not checked")
    main_fn = function(tree, "main")
    main_text = ast.get_source_segment(source, main_fn) if main_fn else ""
    if "stage_build(" not in main_text or "emit_commands(" not in main_text or main_text.index("stage_build(") > main_text.index("emit_commands("):
        found.append("main does not stage builds before emitting their commands")
    self_test = function(tree, "self_test")
    self_text = ast.get_source_segment(source, self_test) if self_test else ""
    self_calls = {dotted(call.func) for call in calls(self_test)}
    if not {"validate_layout", "emit_commands"}.issubset(self_calls):
        found.append("self-test does not execute layout and emitted-command checks")
    for token in ("TemporaryDirectory", "outside-workdir", "missing-executable", "D11_STAGE_SELF_TEST PASS"):
        if token not in self_text:
            found.append(f"self-test missing {token}")
    if not found and run_self_test:
        result = subprocess.run([sys.executable, str(path), "--self-test"], text=True, capture_output=True)
        if result.returncode or "D11_STAGE_SELF_TEST PASS" not in result.stdout:
            found.append(f"stager self-test failed rc={result.returncode}: {(result.stdout + result.stderr)[-1000:]}")
    return found


def self_test():
    fixture = '''
import os, pathlib, tempfile
D11_PARENT_SHA="%s"
D11_HEAD_SHA="%s"
D11_COMMAND_VARS=%r
D11_MIN_FREE_BYTES=21474836480
def validate_layout(workdir, paths):
    root=pathlib.Path(workdir).resolve(); device=os.stat(root).st_dev
    return all(pathlib.Path(p).resolve().is_relative_to(root) and os.stat(p).st_dev == device for p in paths)
def run(args, **kwargs): pass
def stage_build(workdir):
    workdir=pathlib.Path(workdir); target=workdir/"target"; built={}
    for label,sha in {"A":D11_PARENT_SHA,"B":D11_HEAD_SHA}.items():
        source=workdir/"source"/label
        run(["git","worktree","add","--detach",str(source),sha])
        run(["cargo","build","--locked","object_read","ec_read"],env={"CARGO_TARGET_DIR":str(target/label)})
    return {"SWEEP_D11_OBJECT_READ_A_CMD":target/"A"/"object_read", "SWEEP_D11_OBJECT_READ_B_CMD":target/"B"/"object_read",
            "SWEEP_D11_EC_READ_A_CMD":target/"A"/"ec_read", "SWEEP_D11_EC_READ_B_CMD":target/"B"/"ec_read"}
def emit_commands(paths):
    values={name:str(paths[name]) for name in D11_COMMAND_VARS}
    assert all(os.access(value, os.X_OK) for value in values.values())
    return values
def self_test():
    with tempfile.TemporaryDirectory() as td:
        root=pathlib.Path(td); commands={}
        for name in D11_COMMAND_VARS:
            path=root/name; path.write_text("x"); path.chmod(0o700); commands[name]=path
        assert validate_layout(root, commands.values()); emit_commands(commands)
        outside=root.parent/(root.name+"-outside-workdir"); outside.write_text("x"); outside.chmod(0o700)
        assert not validate_layout(root, [outside]), "outside-workdir"
        missing=root/"missing-executable"
        bad=dict(commands); bad[next(iter(D11_COMMAND_VARS))]=missing
        try: emit_commands(bad)
        except AssertionError: pass
        else: raise AssertionError("missing-executable")
        outside.unlink()
    print("D11_STAGE_SELF_TEST PASS")
def main():
    built=stage_build(pathlib.Path("/opt/work/d11")); return emit_commands(built)
if __name__ == "__main__": self_test()
''' % (PARENT, HEAD, tuple(sorted(COMMANDS)))
    with tempfile.TemporaryDirectory() as td:
        candidate = pathlib.Path(td) / "stage-d11-paired.py"
        candidate.write_text(fixture, encoding="utf-8")
        assert not errors(candidate), errors(candidate)
        candidate.write_text(fixture.replace("D11_MIN_FREE_BYTES=21474836480", "D11_MIN_FREE_BYTES=1"), encoding="utf-8")
        assert any("20 GiB" in item for item in errors(candidate, False))
        candidate.write_text(fixture.replace("built=stage_build(pathlib.Path(\"/opt/work/d11\")); ", "built={}; "), encoding="utf-8")
        assert any("main does not stage" in item for item in errors(candidate, False))
        candidate.write_text(fixture.replace('target=workdir/"target"', 'target=pathlib.Path("/target")'), encoding="utf-8")
        assert any("derived from workdir" in item for item in errors(candidate, False))
    print("D11-STAGE-CONTRACT-SELF-TEST PASS future=PASS wrong-floor=RED uncalled-stage=RED root-target=RED")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--after-stage", action="store_true")
    parser.add_argument("--self-test", action="store_true")
    parser.add_argument("--emit-rows", action="store_true")
    args = parser.parse_args()
    if args.emit_rows:
        for row in ROWS:
            print(" | ".join(row))
        return 0
    if args.self_test:
        self_test()
        return 0
    found = errors() if args.after_stage else []
    if found:
        print("D11-STAGE-CONTRACT RED " + "; ".join(found))
        return 1
    print(f"D11-STAGE-CONTRACT PASS stage=S2 cell=A/L5 after_stage={args.after_stage}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
