#!/usr/bin/env bash
set -euo pipefail
product=${1:?product checkout required}
pin=${2:?full product sha required}
[[ $pin =~ ^[0-9a-f]{40}$ ]] || { echo 'CONTRACT-PREFLIGHT invalid_product_sha'; exit 2; }
actual=$(git -C "$product" rev-parse HEAD)
[[ $actual == "$pin" ]] || { echo "CONTRACT-PREFLIGHT product_mismatch expected=$pin actual=$actual"; exit 2; }
cd "$product"
# A product ref that carries NEITHER contract (any non-RDMA branch) is not in
# scope: report it and pass. A ref that carries only ONE of them is a broken
# RDMA tree and still fails closed below.
specs=(s4_error_classification_contract.py lease_explicit_finish_contract.py)
present=0
for script in "${specs[@]}"; do [[ -f enterprise/rust/tests/$script ]] && present=$((present+1)); done
if [[ $present == 0 ]]; then
  echo "CONTRACT-PACK product_sha=$pin status=NOT_APPLICABLE reason=no_contract_scripts_in_product"
  exit 0
fi
failed=0
for spec in 's4_error_classification_contract.py:S4-ERROR-CLASSIFICATION-SELF-TEST PASS' 'lease_explicit_finish_contract.py:LEF-SELF-TEST PASS'; do
  script=${spec%%:*}; witness=${spec#*:}
  echo "CONTRACT-SELF-TEST product_sha=$pin script=$script"
  output=$(mktemp)
  rc=0
  timeout 180s python3 "enterprise/rust/tests/$script" --self-test >"$output" 2>&1 || rc=$?
  cat "$output"
  if [[ $rc == 0 ]] && grep -Fq "$witness" "$output"; then
    echo "CONTRACT-VERDICT script=$script status=PASS rc=0"
  else
    echo "CONTRACT-VERDICT script=$script status=FAIL rc=$rc"
    failed=1
  fi
  rm "$output"
done
echo "CONTRACT-PACK product_sha=$pin failed=$failed"
exit "$failed"
