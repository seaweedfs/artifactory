#!/usr/bin/env bash
set -euo pipefail

if [[ $# -lt 1 ]]; then
  echo "usage: scripts/qa-assert.sh <bundle-dir> --ref <requested-ref> [--profile file] [--expect key>=value]" >&2
  exit 2
fi

PYTHON_BIN="${PYTHON:-}"
if [[ -z "$PYTHON_BIN" ]]; then
  if command -v python3 >/dev/null 2>&1; then
    PYTHON_BIN=python3
  elif command -v python >/dev/null 2>&1; then
    PYTHON_BIN=python
  else
    echo "python3 or python is required" >&2
    exit 2
  fi
fi

"$PYTHON_BIN" - "$@" <<'PY'
import argparse
import json
import operator
import pathlib
import re
import sys


def fail(message):
    print(f"QA_BUNDLE_ASSERT_FAIL: {message}", file=sys.stderr)
    sys.exit(1)


def load_json(path):
    try:
        with path.open("r", encoding="utf-8") as f:
            return json.load(f)
    except FileNotFoundError:
        fail(f"missing {path.name} in {path.parent}")
    except json.JSONDecodeError as exc:
        fail(f"invalid JSON in {path}: {exc}")


def read_profile(path):
    expectations = []
    with pathlib.Path(path).open("r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            expectations.append(line)
    return expectations


def parse_expectation(text):
    match = re.match(r"^\s*([A-Za-z0-9_.:-]+)\s*(>=|<=|==|=|>|<)\s*(.+?)\s*$", text)
    if not match:
        fail(f"bad expectation {text!r}; use key=value, key>=number, key>number, key<=number, or key<number")
    key, op, value = match.groups()
    return key, op, value


def lookup(vars_doc, key):
    if key in vars_doc:
        return vars_doc[key]
    if not key.startswith("__") and "__" + key in vars_doc:
        return vars_doc["__" + key]
    return None


def as_number(value, key):
    try:
        return float(value)
    except (TypeError, ValueError):
        fail(f"{key}={value!r} is not numeric")


def check_expectation(vars_doc, text):
    key, op, expected = parse_expectation(text)
    actual = lookup(vars_doc, key)
    if actual is None:
        fail(f"missing expected key {key}")
    if op in ("=", "=="):
        if str(actual) != expected:
            fail(f"{key}={actual!r}, want {expected!r}")
        return f"{key}={actual}"
    ops = {
        ">": operator.gt,
        ">=": operator.ge,
        "<": operator.lt,
        "<=": operator.le,
    }
    actual_n = as_number(actual, key)
    expected_n = as_number(expected, key)
    if not ops[op](actual_n, expected_n):
        fail(f"{key}={actual_n:g}, want {op}{expected_n:g}")
    return f"{key}={actual_n:g}"


def truthy(value):
    return str(value).strip().lower() in {"1", "true", "yes", "pass", "passed", "ok"}


def pick(vars_doc, *keys):
    for key in keys:
        if key in vars_doc and str(vars_doc[key]) != "":
            return vars_doc[key]
    return ""


parser = argparse.ArgumentParser()
parser.add_argument("bundle")
parser.add_argument("--ref", dest="requested_ref", default="")
parser.add_argument("--profile", action="append", default=[])
parser.add_argument("--expect", action="append", default=[])
parser.add_argument("--allow-missing-status", action="store_true")
args = parser.parse_args()

bundle = pathlib.Path(args.bundle)
if not bundle.is_dir():
    fail(f"bundle directory not found: {bundle}")

for required in ("result.json", "manifest.json", "result.html", "scenario.yaml"):
    if not (bundle / required).exists():
        fail(f"missing {required}")

result = load_json(bundle / "result.json")
status = {}
status_path = bundle / "status.json"
if status_path.exists():
    status = load_json(status_path)
elif not args.allow_missing_status:
    fail("missing status.json")

result_status = str(result.get("status", "")).lower()
if result_status not in {"pass", "passed"}:
    fail(f"result.status={result.get('status')!r}, want PASS")

if status:
    state = str(status.get("state", "")).lower()
    if state not in {"pass", "passed"}:
        fail(f"status.state={status.get('state')!r}, want pass")

vars_doc = result.get("vars") or {}
if not isinstance(vars_doc, dict):
    fail("result.json vars is not an object")

product = pick(vars_doc, "__product")
if not product:
    for candidate in ("rdma", "block", "s3", "vfs"):
        if any(k.startswith(f"__{candidate}_") for k in vars_doc):
            product = candidate
            break
if not product:
    fail("missing __product and no product-prefixed vars found")
product = str(product)

gate_pass = pick(vars_doc, "__gate_pass", f"__{product}_gate_pass")
if not truthy(gate_pass):
    fail(f"gate pass key is not true for product={product}: {gate_pass!r}")

tested_ref = pick(vars_doc, "__tested_ref", f"__{product}_tested_ref", f"__{product}_mono_ref", f"__{product}_ref")
tested_sha = pick(vars_doc, "__tested_sha", f"__{product}_tested_sha", f"__{product}_mono_sha", f"__{product}_sha")
lab_run_id = pick(vars_doc, "__lab_run_id", f"__{product}_lab_run_id", f"__{product}_run_id")

if args.requested_ref:
    if not tested_ref:
        fail("missing __tested_ref")
    if str(tested_ref) != args.requested_ref:
        fail(f"tested_ref={tested_ref!r}, want {args.requested_ref!r}")
if not tested_sha:
    fail("missing __tested_sha")
if not lab_run_id:
    fail("missing __lab_run_id")

expectations = []
for profile in args.profile:
    expectations.extend(read_profile(profile))
expectations.extend(args.expect)

checked = []
for exp in expectations:
    checked.append(check_expectation(vars_doc, exp))

print("QA_BUNDLE_ASSERT_OK")
print(f"product={product}")
print(f"tested_ref={tested_ref}")
print(f"tested_sha={tested_sha}")
print(f"lab_run_id={lab_run_id}")
for row in checked:
    print(row)
PY
