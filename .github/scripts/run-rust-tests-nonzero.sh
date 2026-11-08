#!/usr/bin/env bash
# One selected libtest binary must actually pass at least one test.
set -euo pipefail
log=$(mktemp)
trap 'rm -f "$log"' EXIT
"$@" 2>&1 | tee "$log"
if ! awk '{ candidates += gsub(/test result:/, "&") }
          /^test result: ok\. [0-9]+ passed; 0 failed; [0-9]+ ignored; [0-9]+ measured; [0-9]+ filtered out; finished in [0-9]+([.][0-9]+)?s$/ { valid++; passed=$4 }
          END { exit !(candidates == 1 && valid == 1 && passed + 0 > 0) }' "$log"; then
  echo '::error::selected Rust test target did not report one nonempty passing run' >&2
  exit 1
fi
