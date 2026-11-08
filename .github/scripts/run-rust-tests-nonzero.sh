#!/usr/bin/env bash
# One selected libtest binary must actually pass at least one test.
set -euo pipefail
log=$(mktemp)
trap 'rm -f "$log"' EXIT
"$@" 2>&1 | tee "$log"
if ! awk '/^test result: ok\. [0-9]+ passed; 0 failed;/ { summaries++; passed=$4 }
          END { exit !(summaries == 1 && passed + 0 > 0) }' "$log"; then
  echo '::error::selected Rust test target did not report one nonempty passing run' >&2
  exit 1
fi
