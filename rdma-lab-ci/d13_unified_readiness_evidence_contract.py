#!/usr/bin/env python3
"""Tests-first contract for D-13 unified readiness and failure evidence."""
import argparse
import pathlib
import re
import tempfile

RUNNER = pathlib.Path(__file__).with_name("run-mono-rdma-lab.sh")


def function(text, name):
    match = re.search(rf"(?m)^{re.escape(name)}\(\) \{{\s*$", text)
    if match is None:
        return ""
    following = re.search(r"(?m)^[a-z][a-z0-9_]*\(\) \{\s*$", text[match.end():])
    end = match.end() + following.start() if following else len(text)
    return text[match.start():end]


def errors(path=RUNNER):
    text = path.read_text(encoding="utf-8")
    run = function(text, "run_unified_gate")
    wait = function(text, "wait_for_writable_volume")
    capture = function(text, "capture_unified_logs")
    cleanup = function(text, "cleanup_lab")
    found = []
    if not wait or "/dir/assign?replication=000" not in wait or "UNIFIED_WRITABLE_VOLUME_READY" not in wait:
        found.append("readiness must require a successful /dir/assign probe")
    positions = [run.rfind("m02-up.sh"), run.find("wait_for_writable_volume"), run.rfind("m01-unified.sh")]
    if not run or any(pos < 0 for pos in positions) or positions != sorted(positions):
        found.append("assign readiness must run after m02-up and before the first PUT")
    required_logs = ("master.log", "weed-volume.log", "filer.log", "m01", "m02")
    if not capture or any(token not in capture for token in required_logs):
        found.append("capture must name master/volume/filer and both host evidence roots")
    cleanup_order = [cleanup.find(token) for token in ("capture_unified_logs", "CLEANUP_M01_SCRIPT", "CLEANUP_M02_SCRIPT")]
    if not cleanup or any(pos < 0 for pos in cleanup_order) or cleanup_order != sorted(cleanup_order):
        found.append("cleanup must capture evidence before either teardown")
    trap = run.find("trap cleanup_lab EXIT")
    launch = run.rfind("m02-up.sh")
    if trap < 0 or launch < 0 or trap > launch:
        found.append("failure capture trap must be armed before component startup")
    for token in ("evidence.sha256", "logs_captured=true", "logs_captured=false", "m01/client.log",
                  "m01/loader.log"):
        if token not in capture:
            found.append(f"fail-closed capture missing {token}")
    self_test = function(text, "d13_self_test")
    for token in ("missing-fid accepted", "master.log", "weed-volume.log", "filer.log", "loader.log",
                  "absent-m01 accepted", "failed-m02-transfer accepted", "success_capture_failure_exit=7",
                  "failed_body_status_preserved=23", "teardowns=both", "required_binaries=PASS",
                  "missing_binary=seaweedfs-sw-rdma-kvcache:RED", "missing_module=seaweedvfs.ko:RED",
                  "stale_module=vermagic:RED",
                  "D13_SELF_TEST PASS"):
        if token not in self_test:
            found.append(f"runtime self-test missing {token}")
    build = function(text, "build_unified_gate")
    required = function(text, "unified_required_binaries")
    verify = function(text, "verify_unified_binaries")
    if "-p seaweedkv-tools" not in build or "--bin seaweedfs-sw-rdma-kvcache" not in build:
        found.append("launcher does not build the seaweedkv-tools kvcache binary target")
    if 'make -C "$m01_src/seaweed-vfs/kernel"' not in build:
        found.append("launcher does not build the checked-out seaweedvfs kernel module")
    for name in ("sw-rdma-object-put", "sw-rdma-object-get", "sw-rdma-object-bench", "sw-rdma-s3-loader",
                 "seaweedfs-sw-rdma-kvcache", "sw-rdma-kd", "sw-kd"):
        if name not in required:
            found.append(f"required binary manifest missing {name}")
    if "UNIFIED_PREFLIGHT_MISSING_BINARY" not in verify or "[ ! -x" not in verify:
        found.append("required binary preflight is not fail-closed")
    if "UNIFIED_PREFLIGHT_MISSING_ARTIFACT" not in verify or "[ ! -f" not in verify:
        found.append("required file artifact preflight is not fail-closed")
    if "modinfo -F vermagic" not in verify or "uname -r" not in verify or \
            "UNIFIED_PREFLIGHT_STALE_ARTIFACT" not in verify:
        found.append("kernel module vermagic preflight is not fail-closed")
    if "seaweed-vfs/kernel/seaweedvfs.ko" not in text:
        found.append("required file artifact manifest is missing seaweedvfs.ko")
    verify_call = text.rfind('verify_unified_binaries "$M01_WORKDIR/seaweed-mono"')
    case_call = text.rfind('case "$PROFILE" in')
    if verify_call < 0 or case_call < 0 or verify_call > case_call:
        found.append("required binary preflight must run before unified readiness")
    return found


def self_test():
    future = r'''#!/usr/bin/env bash
wait_for_writable_volume() {
  ssh host "curl -sf 'http://127.0.0.1:9755/dir/assign?replication=000'"
  echo UNIFIED_WRITABLE_VOLUME_READY
}
capture_unified_logs() {
  mkdir m01 m02
  test -f master.log; test -f weed-volume.log; test -f filer.log
  test -s m01/client.log; test -s m01/loader.log
  false && echo logs_captured=false
  sha256sum master.log > evidence.sha256
  echo logs_captured=true
}
cleanup_lab() {
  capture_unified_logs
  bash "$CLEANUP_M01_SCRIPT"
  ssh host "$CLEANUP_M02_SCRIPT"
}
run_unified_gate() {
  trap cleanup_lab EXIT
  ssh host m02-up.sh
  wait_for_writable_volume
  bash m01-unified.sh
}
d13_self_test() {
  false && echo 'missing-fid accepted'
  false && echo 'absent-m01 accepted'
  false && echo 'failed-m02-transfer accepted'
  test master.log weed-volume.log filer.log loader.log success_capture_failure_exit=7
  test failed_body_status_preserved=23 teardowns=both required_binaries=PASS
  test missing_binary=seaweedfs-sw-rdma-kvcache:RED missing_module=seaweedvfs.ko:RED stale_module=vermagic:RED
  echo D13_SELF_TEST PASS
}
build_unified_gate() {
  cargo build -p seaweedkv-tools --bin seaweedfs-sw-rdma-kvcache
  make -C "$m01_src/seaweed-vfs/kernel"
}
unified_required_binaries() {
  echo sw-rdma-object-put sw-rdma-object-get sw-rdma-object-bench sw-rdma-s3-loader
  echo seaweedfs-sw-rdma-kvcache sw-rdma-kd sw-kd
}
verify_unified_binaries() {
  [ ! -x missing ] && echo UNIFIED_PREFLIGHT_MISSING_BINARY
  [ ! -f module ] && echo UNIFIED_PREFLIGHT_MISSING_ARTIFACT
  modinfo -F vermagic module; uname -r; echo UNIFIED_PREFLIGHT_STALE_ARTIFACT
}
unified_required_files() { echo seaweed-vfs/kernel/seaweedvfs.ko; }
verify_unified_binaries "$M01_WORKDIR/seaweed-mono"
case "$PROFILE" in unified) true ;; esac
'''
    with tempfile.TemporaryDirectory() as td:
        path = pathlib.Path(td) / "runner.sh"
        path.write_text(future, encoding="utf-8")
        assert not errors(path), errors(path)
        path.write_text(future.replace("  wait_for_writable_volume\n", ""), encoding="utf-8")
        assert any("before the first PUT" in item for item in errors(path))
        path.write_text(future.replace("  capture_unified_logs\n", ""), encoding="utf-8")
        assert any("before either teardown" in item for item in errors(path))
        path.write_text(future.replace("--bin seaweedfs-sw-rdma-kvcache", "--lib"), encoding="utf-8")
        assert any("kvcache binary target" in item for item in errors(path))
        path.write_text(future.replace("  trap cleanup_lab EXIT\n", "").replace(
            "  bash m01-unified.sh", "  bash m01-unified.sh\n  trap cleanup_lab EXIT"), encoding="utf-8")
        assert any("before component startup" in item for item in errors(path))
    print("D13-SELF-TEST PASS future=PASS no_assign=RED capture_after_teardown=RED late_trap=RED")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--self-test", action="store_true")
    parser.add_argument("--after-suite", action="store_true")
    args = parser.parse_args()
    if args.self_test:
        self_test()
        return 0
    found = errors() if args.after_suite else []
    if found:
        print("D13-CONTRACT RED " + "; ".join(found))
        return 1
    print(f"D13-CONTRACT PASS stage=S5 cell=B/L6 after_suite={args.after_suite}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
