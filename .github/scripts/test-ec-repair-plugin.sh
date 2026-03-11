#!/bin/bash

# EC Repair Plugin Worker Integration Test
#
# Tests the ec_repair plugin worker end-to-end:
# 1. Start weed mini (master + filer + volume + admin) + 4 standalone volume servers
# 2. Upload data to fill a volume, then EC-encode it
# 3. Simulate shard loss by stopping a volume server and deleting shard files
# 4. Trigger ec_repair detection + execution via admin plugin API
# 5. Verify all shards are restored and no temp files leaked to /tmp

set -euo pipefail

WEED_BINARY="${WEED_BINARY:-weed}"
DATA_DIR="${WEED_DATA_DIR:-/tmp/ec-repair-test-$$}"
MASTER_IP="127.0.0.1"
MASTER_PORT=9333
VOLUME_PORT_START=9340
ADMIN_PORT=0
NUM_EXTRA_VOLUMES=4
VOLUME_SIZE_LIMIT_MB=15

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m'

pass() { echo -e "${GREEN}[PASS]${NC} $1"; }
fail() { echo -e "${RED}[FAIL]${NC} $1"; EXIT_CODE=1; }
info() { echo -e "${YELLOW}[INFO]${NC} $1"; }

EXIT_CODE=0

dump_logs() {
    echo ""
    echo "======== SERVER LOGS ========"
    for logfile in "${DATA_DIR}"/*.log; do
        [ -f "$logfile" ] || continue
        echo ""
        echo "──── $(basename "$logfile") ────"
        tail -100 "$logfile"
    done
    echo "======== END LOGS ========"
    echo ""
}

cleanup() {
    local exit_code=$?
    info "Cleaning up..."
    pkill -f "${DATA_DIR}" 2>/dev/null || true
    sleep 2
    pkill -9 -f "${DATA_DIR}" 2>/dev/null || true
    if [ "$exit_code" -ne 0 ] || [ "$EXIT_CODE" -ne 0 ]; then
        dump_logs
        # preserve logs for artifact upload; only delete non-log data
        find "${DATA_DIR}" -type f ! -name '*.log' -delete 2>/dev/null || true
    else
        rm -rf "${DATA_DIR}"
    fi
}
trap cleanup EXIT

wait_for_http() {
    local name=$1 url=$2 max=${3:-30}
    for i in $(seq 1 "$max"); do
        if curl -sf "$url" > /dev/null 2>&1; then
            return 0
        fi
        sleep 1
    done
    echo "Timed out waiting for $name at $url"
    return 1
}

wait_for_port() {
    local name=$1 host=$2 port=$3 max=${4:-30}
    for i in $(seq 1 "$max"); do
        if nc -z "$host" "$port" 2>/dev/null; then
            return 0
        fi
        sleep 1
    done
    echo "Timed out waiting for $name on $host:$port"
    return 1
}

# ── Setup ──────────────────────────────────────────────────────────────

info "Creating data directories"
mkdir -p "${DATA_DIR}/mini"
for i in $(seq 1 $NUM_EXTRA_VOLUMES); do
    mkdir -p "${DATA_DIR}/vol${i}"
done

info "Starting weed mini"
$WEED_BINARY mini \
    -dir="${DATA_DIR}/mini" \
    -master.volumeSizeLimitMB=$VOLUME_SIZE_LIMIT_MB \
    -volume.port=$VOLUME_PORT_START \
    -s3=false -webdav=false -admin.ui=true \
    -ip=$MASTER_IP -ip.bind=0.0.0.0 \
    > "${DATA_DIR}/mini.log" 2>&1 &
echo $! > "${DATA_DIR}/mini.pid"

wait_for_http "Master" "http://${MASTER_IP}:${MASTER_PORT}/cluster/status" 30

# Detect admin port from logs
for attempt in $(seq 1 20); do
    ADMIN_PORT=$(grep -o "Admin server is ready at http://[^:]*:\([0-9]*\)" "${DATA_DIR}/mini.log" | grep -o '[0-9]*$' || true)
    if [ -n "$ADMIN_PORT" ]; then
        break
    fi
    sleep 1
done

if [ -z "$ADMIN_PORT" ] || [ "$ADMIN_PORT" = "0" ]; then
    fail "Could not detect admin port from mini logs"
    cat "${DATA_DIR}/mini.log"
    exit 1
fi

wait_for_http "Admin" "http://${MASTER_IP}:${ADMIN_PORT}/health" 15
info "Admin server ready on port $ADMIN_PORT"

# Wait for plugin worker to register
for attempt in $(seq 1 30); do
    JOB_TYPES=$(curl -sf "http://${MASTER_IP}:${ADMIN_PORT}/api/plugin/job-types" || echo "[]")
    if echo "$JOB_TYPES" | grep -q "ec_repair"; then
        break
    fi
    sleep 1
done

if ! echo "$JOB_TYPES" | grep -q "ec_repair"; then
    fail "ec_repair job type not registered with admin"
    exit 1
fi
info "ec_repair plugin worker registered"

info "Starting $NUM_EXTRA_VOLUMES additional volume servers"
for i in $(seq 1 $NUM_EXTRA_VOLUMES); do
    PORT=$((VOLUME_PORT_START + i))
    $WEED_BINARY volume \
        -dir="${DATA_DIR}/vol${i}" \
        -port=${PORT} \
        -master="${MASTER_IP}:${MASTER_PORT}" \
        -max=10 \
        -ip=$MASTER_IP \
        > "${DATA_DIR}/vol${i}.log" 2>&1 &
    echo $! > "${DATA_DIR}/vol${i}.pid"
done

# Wait for all volume servers
for i in $(seq 1 $NUM_EXTRA_VOLUMES); do
    PORT=$((VOLUME_PORT_START + i))
    if ! wait_for_http "Volume $i" "http://${MASTER_IP}:${PORT}/status" 30; then
        fail "Volume server $i failed to start"
        cat "${DATA_DIR}/vol${i}.log"
        exit 1
    fi
done

# Wait for volume servers to register with master
sleep 10
REGISTERED=$(curl -sf "http://${MASTER_IP}:${MASTER_PORT}/vol/status" | python3 -c "
import sys, json
data = json.load(sys.stdin)
dcs = data['Volumes']['DataCenters']
count = 0
for dc in dcs.values():
    for rack in dc.values():
        count += len(rack)
print(count)
" 2>/dev/null || echo "0")

EXPECTED=$((1 + NUM_EXTRA_VOLUMES))
if [ "$REGISTERED" -lt "$EXPECTED" ]; then
    fail "Expected $EXPECTED volume servers, got $REGISTERED"
    exit 1
fi
pass "All $REGISTERED volume servers registered"

# ── Upload data and EC-encode ──────────────────────────────────────────

info "Generating 10MB test file"
dd if=/dev/urandom of="${DATA_DIR}/testfile" bs=1M count=10 2>/dev/null

info "Uploading files to collection 'ectest'"
for i in $(seq 1 12); do
    ASSIGN=""
    for attempt in $(seq 1 5); do
        ASSIGN=$(curl -sf "http://${MASTER_IP}:${MASTER_PORT}/dir/assign?collection=ectest" 2>/dev/null) && break
        info "Assign attempt $attempt failed, retrying..."
        sleep 3
    done
    if [ -z "$ASSIGN" ]; then
        fail "Failed to assign volume for upload $i after 5 attempts"
        exit 1
    fi
    FID=$(echo "$ASSIGN" | python3 -c "import sys,json; print(json.load(sys.stdin)['fid'])")
    URL=$(echo "$ASSIGN" | python3 -c "import sys,json; print(json.load(sys.stdin)['url'])")
    curl -sf --retry 3 --retry-delay 2 --retry-all-errors \
        -F file=@"${DATA_DIR}/testfile" "http://${URL}/${FID}" > /dev/null
done
info "Uploaded 12 files (~120MB total)"

# Find any ectest volume with data (pick the largest)
ECVOL=$(curl -sf "http://${MASTER_IP}:${MASTER_PORT}/vol/status" | python3 -c "
import sys, json
data = json.load(sys.stdin)
dcs = data['Volumes']['DataCenters']
best_id, best_size = None, 0
for dc in dcs.values():
    for rack in dc.values():
        for node_url, vols in rack.items():
            if vols is None:
                continue
            for v in vols:
                if v.get('Collection') == 'ectest' and v.get('Size', 0) > best_size:
                    best_id, best_size = v['Id'], v['Size']
if best_id is not None:
    print(best_id)
else:
    sys.exit(1)
" 2>/dev/null || echo "")

if [ -z "$ECVOL" ]; then
    fail "No ectest volumes found"
    exit 1
fi
info "EC-encoding volume $ECVOL"

echo "lock; ec.encode -collection=ectest -volumeId=${ECVOL} -force; unlock" | \
    $WEED_BINARY shell -master="${MASTER_IP}:${MASTER_PORT}" > "${DATA_DIR}/ec-encode.log" 2>&1

# Verify EC shards exist
EC_NODES=$(echo 'volume.list' | $WEED_BINARY shell -master="${MASTER_IP}:${MASTER_PORT}" 2>&1 | grep "ec volume id:${ECVOL}" | wc -l)
if [ "$EC_NODES" -lt 2 ]; then
    fail "EC shards not distributed (found on $EC_NODES nodes)"
    exit 1
fi
pass "Volume $ECVOL EC-encoded and distributed across $EC_NODES nodes"

# Count total shards before corruption
SHARDS_BEFORE=$(echo 'volume.list' | $WEED_BINARY shell -master="${MASTER_IP}:${MASTER_PORT}" 2>&1 | \
    grep "ec volume id:${ECVOL}" | grep -o 'shards:\[[^]]*\]' | tr ',' '\n' | wc -w)
info "Total EC shards before corruption: $SHARDS_BEFORE"

# ── Simulate shard loss ────────────────────────────────────────────────

# Find a standalone volume server that has EC shards and kill it
VICTIM_PORT=""
VICTIM_IDX=""
for i in $(seq 1 $NUM_EXTRA_VOLUMES); do
    PORT=$((VOLUME_PORT_START + i))
    EC_FILES=$(ls "${DATA_DIR}/vol${i}"/ectest_${ECVOL}.ec[0-9]* 2>/dev/null | wc -l || echo 0)
    if [ "$EC_FILES" -gt 0 ]; then
        VICTIM_PORT=$PORT
        VICTIM_IDX=$i
        break
    fi
done

if [ -z "$VICTIM_PORT" ]; then
    fail "No standalone volume server has EC shard files"
    exit 1
fi

DELETED_SHARDS=$(ls "${DATA_DIR}/vol${VICTIM_IDX}"/ectest_${ECVOL}.ec[0-9]* 2>/dev/null | wc -l)
info "Simulating shard loss: killing vol${VICTIM_IDX} (port $VICTIM_PORT) and deleting $DELETED_SHARDS shard files"

kill "$(cat "${DATA_DIR}/vol${VICTIM_IDX}.pid")" 2>/dev/null || true
sleep 2

# Delete EC shard data files (keep .ecx/.ecj so the node doesn't re-report old shards)
rm -f "${DATA_DIR}/vol${VICTIM_IDX}"/ectest_${ECVOL}.ec[0-9]*
rm -f "${DATA_DIR}/vol${VICTIM_IDX}"/ectest_${ECVOL}.vif

# Restart the volume server
$WEED_BINARY volume \
    -dir="${DATA_DIR}/vol${VICTIM_IDX}" \
    -port=${VICTIM_PORT} \
    -master="${MASTER_IP}:${MASTER_PORT}" \
    -max=10 \
    -ip=$MASTER_IP \
    > "${DATA_DIR}/vol${VICTIM_IDX}.log" 2>&1 &
echo $! > "${DATA_DIR}/vol${VICTIM_IDX}.pid"

wait_for_http "Volume ${VICTIM_IDX}" "http://${MASTER_IP}:${VICTIM_PORT}/status" 30
sleep 3

pass "Shard loss simulated on vol${VICTIM_IDX}"

# ── Trigger ec_repair via plugin API ───────────────────────────────────

info "Triggering ec_repair detection + execution via admin API"

RUN_RESULT=$(curl -sf -X POST "http://${MASTER_IP}:${ADMIN_PORT}/api/plugin/job-types/ec_repair/run" \
    -H "Content-Type: application/json" \
    -d '{"max_results": 100, "timeout_seconds": 120}')

DETECTED=$(echo "$RUN_RESULT" | python3 -c "import sys,json; print(json.load(sys.stdin).get('detected_count', 0))")
EXECUTED=$(echo "$RUN_RESULT" | python3 -c "import sys,json; print(json.load(sys.stdin).get('executed_count', 0))")
SUCCEEDED=$(echo "$RUN_RESULT" | python3 -c "import sys,json; print(json.load(sys.stdin).get('success_count', 0))")
ERRORS=$(echo "$RUN_RESULT" | python3 -c "import sys,json; print(json.load(sys.stdin).get('error_count', 0))")

if [ "$DETECTED" -lt 1 ]; then
    fail "Detection found 0 repair tasks (expected >= 1)"
    echo "$RUN_RESULT" | python3 -m json.tool
    exit 1
fi
pass "Detection found $DETECTED repair task(s)"

if [ "$SUCCEEDED" -lt 1 ] || [ "$ERRORS" -gt 0 ]; then
    fail "Execution: succeeded=$SUCCEEDED errors=$ERRORS (expected succeeded>=1, errors=0)"
    echo "$RUN_RESULT" | python3 -m json.tool
    exit 1
fi
pass "Execution succeeded: $SUCCEEDED job(s) completed, $ERRORS errors"

# ── Verify repair results ─────────────────────────────────────────────

# Count shards after repair
SHARDS_AFTER=$(echo 'volume.list' | $WEED_BINARY shell -master="${MASTER_IP}:${MASTER_PORT}" 2>&1 | \
    grep "ec volume id:${ECVOL}" | grep -o 'shards:\[[^]]*\]' | tr ',' '\n' | wc -w)

if [ "$SHARDS_AFTER" -lt "$SHARDS_BEFORE" ]; then
    fail "Shards after repair ($SHARDS_AFTER) < shards before corruption ($SHARDS_BEFORE)"
else
    pass "All shards restored: $SHARDS_AFTER shards (was $SHARDS_BEFORE before corruption)"
fi

# Verify no temp files leaked to /tmp
LEAKED=$(ls -d /tmp/ec-repair-${ECVOL}-* 2>/dev/null | wc -l || echo 0)
if [ "$LEAKED" -gt 0 ]; then
    fail "Found $LEAKED leaked ec-repair temp dirs in /tmp (workingDir fix not applied)"
else
    pass "No ec-repair temp files leaked to /tmp"
fi

# Verify a second detection shows no issues
DETECT2=$(curl -sf -X POST "http://${MASTER_IP}:${ADMIN_PORT}/api/plugin/job-types/ec_repair/detect" \
    -H "Content-Type: application/json" \
    -d '{"max_results": 100, "timeout_seconds": 60}')
REMAINING=$(echo "$DETECT2" | python3 -c "import sys,json; print(json.load(sys.stdin).get('total_proposals', -1))")

if [ "$REMAINING" -eq 0 ]; then
    pass "Second detection confirms no remaining issues"
else
    fail "Second detection still found $REMAINING repair proposals"
fi

# ── Summary ────────────────────────────────────────────────────────────

echo ""
if [ "$EXIT_CODE" -eq 0 ]; then
    echo -e "${GREEN}All EC repair plugin worker tests passed${NC}"
else
    echo -e "${RED}Some tests failed${NC}"
fi

exit $EXIT_CODE
