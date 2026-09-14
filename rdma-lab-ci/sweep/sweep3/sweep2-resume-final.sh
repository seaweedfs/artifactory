#!/usr/bin/env bash
set -eu
set -a
source /opt/work/lab.env
set +a
export PATH=/opt/work/gate561-venv/bin:/home/testdev/.cargo/bin:/usr/local/go/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin
export TESTOPS_ACTIVITY_LOG=/mnt/smb/work/share/testops/WHO-IS-RUNNING TESTOPS_LOCK_FILE=/mnt/smb/work/share/testops/locks/rdma-lab.lock
run=${SWEEP_ROOT:?}
phase() {
 printf 'BEGIN %s %s\n' "$1" "$(date -u +%FT%TZ)"
 local name="$1"; shift
 "$@" >"$run/$name-console.log" 2>&1
 printf 'END %s %s\n' "$name" "$(date -u +%FT%TZ)"
}
phase t1-diagnostic /usr/bin/python3 /opt/work/codex02-sweep3-tools/sweep2-t1-diagnostic.py
phase disk-contained /usr/bin/python3 /opt/work/codex02-sweep3-tools/sweep2-disk-contained.py
phase privileged551-configured /usr/bin/python3 /opt/work/codex02-sweep3-tools/sweep2-551-configured.py
phase coverage bash /opt/work/codex02-sweep3-tools/sweep2-coverage.sh
phase paired /usr/bin/python3 /opt/work/codex02-sweep3-tools/sweep2-paired.py
printf 'SWEEP_EXECUTIONS_FINISHED %s\n' "$(date -u +%FT%TZ)"