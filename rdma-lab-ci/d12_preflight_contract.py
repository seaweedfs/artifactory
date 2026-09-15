#!/usr/bin/env python3
"""Tests-first contract for D-12 tier-S staging preflight."""
import argparse
import ast
import pathlib
import tempfile

ROOT = pathlib.Path(__file__).resolve().parents[1]
RUNNER = ROOT / "rdma-lab-ci" / "rdma-hw-suite"
MIN_FREE_BYTES = 20 * 1024**3
MAIN_PLAN_OBJECT = "42dd1bb40de5ee8affebf799cbf86da64f54e2f5"
T3_DONOR_OBJECT = "4c896d6c48628df35d1b4b0e18980b9c2d17cd74"
EXPECTED_CASES = {
    "volume.dat_lifecycle", "volume.ec_lifecycle", "volume.ec_shard_read",
    "volume.kv_slab_read", "volume.copy", "volume.main_plan_peer",
    "volume.dc_write_contract", "vfs.rdma_lifecycle", "vfs.rdma_ec_read",
    "vfs.rdma_reregister", "t3.stale_key", "unified.short_rc_dc",
}
ROWS = (
    ("D12.1-low-root", "A/L6", "after-suite", "rdma-hw-suite --preflight-negative-control low-root-space", "preflight_status=fail failed_item=root_free_space case_count=0"),
    ("D12.1-root-still", "A/L6", "after-suite", "rdma-hw-suite --preflight-self-test root-space-ok", "free_bytes>=21474836480 and no failure"),
    ("D12.2-low-workdir", "A/L6", "after-suite", "rdma-hw-suite --preflight-negative-control low-workdir-space", "preflight_status=fail failed_item=workdir_free_space case_count=0"),
    ("D12.2-workdir-still", "A/L6", "after-suite", "rdma-hw-suite --preflight-self-test workdir-space-ok", "free_bytes>=21474836480 and no failure"),
    ("D12.3-missing-object", "A/L6", "after-suite", "rdma-hw-suite --preflight-negative-control missing-case-git-object", "preflight_status=fail names case and object before case 1"),
    ("D12.3-objects-still", "A/L6", "after-suite", "rdma-hw-suite --preflight-self-test git-objects-ok", "all per-case objects resolve on M01 and M02"),
    ("D12.4-banner-moving", "A/L6", "after-suite", "rdma-hw-suite --preflight-self-test root-space-ok", "banner without numeric last non-empty line is RED"),
    ("D12.4-banner-still", "A/L6", "after-suite", "rdma-hw-suite --preflight-self-test root-space-ok", "clean number and banner-prefixed number parse the last non-empty line"),
    ("D12.5-dangling-commit", "A/L6", "after-suite", "rdma-hw-suite --preflight-self-test git-objects-ok", "commit without its tree is RED before staging"),
    ("D12.5-repo-still", "A/L6", "after-suite", "rdma-hw-suite --preflight-self-test git-objects-ok", "workdir-derived bundle repo is fsck-clean and every per-case tree resolves on M01/M02"),
    ("D12.6-stale-origin-moving", "A/L6", "after-suite", "rdma-hw-suite --preflight-self-test stage-checkout-reuse", "existing checkout origin is rebound before fetch or the checkout is quarantined and recloned"),
    ("D12.6-clean-reuse-still", "A/L6", "after-suite", "rdma-hw-suite --preflight-self-test stage-checkout-reuse", "checkout with the declared origin and complete case object closure is reused"),
)


def constants(path):
    tree = ast.parse(path.read_text(encoding="utf-8"))
    out = {}
    for node in tree.body:
        if isinstance(node, ast.Assign) and len(node.targets) == 1 and isinstance(node.targets[0], ast.Name):
            try:
                out[node.targets[0].id] = ast.literal_eval(node.value)
            except (ValueError, TypeError):
                pass
    return out


def case_names(values):
    return {row[0] for row in values.get("CASES", ())}


def errors(path, after_suite=False):
    text = path.read_text(encoding="utf-8")
    values = constants(path)
    found_cases = case_names(values)
    result = []
    if found_cases != EXPECTED_CASES:
        result.append(f"case inventory mismatch missing={sorted(EXPECTED_CASES-found_cases)} extra={sorted(found_cases-EXPECTED_CASES)}")
    if not after_suite:
        return result
    if values.get("MIN_PREFLIGHT_FREE_BYTES") != MIN_FREE_BYTES:
        result.append("MIN_PREFLIGHT_FREE_BYTES must be exactly 20 GiB")
    if values.get("PREFLIGHT_FILESYSTEMS") != {"root": "/", "workdir": "workdir"}:
        result.append("PREFLIGHT_FILESYSTEMS must cover / and the workdir filesystem")
    required = values.get("CASE_REQUIRED_GIT_OBJECTS")
    if not isinstance(required, dict) or set(required) != EXPECTED_CASES:
        result.append("CASE_REQUIRED_GIT_OBJECTS must classify every case exactly")
    else:
        for case, refs in required.items():
            if "product" not in refs:
                result.append(f"{case} does not require the product object")
        if MAIN_PLAN_OBJECT not in required.get("volume.main_plan_peer", ()):
            result.append("volume.main_plan_peer missing pinned object")
        if T3_DONOR_OBJECT not in required.get("t3.stale_key", ()):
            result.append("t3.stale_key missing donor object")
    for token in ("root_free_space", "workdir_free_space", "missing-case-git-object", "case_count\": 0"):
        if token not in text:
            result.append(f"missing fail-closed evidence token {token}")
    for token in ("parse_remote_free_bytes", "banner_before_number=PASS", "banner_without_number=RED"):
        if token not in text:
            result.append(f"missing remote probe parsing control {token}")
    for token in ("staging_repo_path", "staging_repo_fsck", "staging_repo_tree", "dangling_commit=RED"):
        if token not in text:
            result.append(f"missing bundle repo control {token}")
    for token in ("prepare_stage_checkout", "remote", "set-url", "quarantine_checkout", "stage-checkout-reuse", "stale_origin=RED", "clean_reuse=PASS"):
        if token not in text:
            result.append(f"missing staged checkout reuse control {token}")
    return result


def self_test():
    assert not errors(RUNNER)
    source = RUNNER.read_text(encoding="utf-8")
    mapping = {case: ("product",) for case in EXPECTED_CASES}
    mapping["volume.main_plan_peer"] += (MAIN_PLAN_OBJECT,)
    mapping["t3.stale_key"] += (T3_DONOR_OBJECT,)
    future = source + f"\nMIN_PREFLIGHT_FREE_BYTES = {MIN_FREE_BYTES!r}\nPREFLIGHT_FILESYSTEMS = {{'root': '/', 'workdir': 'workdir'}}\nCASE_REQUIRED_GIT_OBJECTS = {mapping!r}\n# root_free_space workdir_free_space missing-case-git-object case_count\": 0\n"
    with tempfile.TemporaryDirectory() as td:
        candidate = pathlib.Path(td) / "runner.py"
        candidate.write_text(future, encoding="utf-8")
        assert not errors(candidate, True), errors(candidate, True)
        missing = dict(mapping)
        missing["volume.main_plan_peer"] = ("product",)
        bad = source + f"\nMIN_PREFLIGHT_FREE_BYTES = {MIN_FREE_BYTES!r}\nPREFLIGHT_FILESYSTEMS = {{'root': '/', 'workdir': 'workdir'}}\nCASE_REQUIRED_GIT_OBJECTS = {missing!r}\n# root_free_space workdir_free_space missing-case-git-object case_count\": 0\n"
        candidate.write_text(bad, encoding="utf-8")
        assert any("main_plan_peer missing" in e for e in errors(candidate, True))
        extra_mapping = dict(mapping)
        extra_mapping["undeclared.case"] = ("product",)
        extra = source + f"\nMIN_PREFLIGHT_FREE_BYTES = {MIN_FREE_BYTES!r}\nPREFLIGHT_FILESYSTEMS = {{'root': '/', 'workdir': 'workdir'}}\nCASE_REQUIRED_GIT_OBJECTS = {extra_mapping!r}\n# root_free_space workdir_free_space missing-case-git-object case_count\": 0\n"
        candidate.write_text(extra, encoding="utf-8")
        assert any("classify every case" in e for e in errors(candidate, True))
    print("D12-PREFLIGHT-SELF-TEST PASS future=PASS missing_object=RED undeclared_case=RED")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--after-suite", action="store_true")
    ap.add_argument("--emit-rows", action="store_true")
    ap.add_argument("--self-test", action="store_true")
    args = ap.parse_args()
    if args.emit_rows:
        for row in ROWS:
            print(" | ".join(row))
        return 0
    if args.self_test:
        self_test()
        return 0
    found = errors(RUNNER, args.after_suite)
    if found:
        print("D12-PREFLIGHT-CONTRACT RED " + "; ".join(found))
        return 1
    print(f"D12-PREFLIGHT-CONTRACT PASS cases={len(EXPECTED_CASES)} stage=S5 cell=A/L6 after_suite={args.after_suite}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
