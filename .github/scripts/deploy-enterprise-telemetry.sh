#!/usr/bin/env bash
set -euo pipefail

upload="$1"
revision="$2"
[[ "$(id -un)" == opc && "$HOME" == /home/opc ]]
[[ "$upload" =~ ^/home/opc/telemetry-upload\.[A-Za-z0-9]+$ && "$revision" =~ ^[a-f0-9]{40}$ ]]
umask 077

legacy="$HOME/seaweedfs-telemetry/data/telemetry-state.json"
root="$HOME/seaweedfs-telemetry/enterprise"
service=telemetry-enterprise.service
unit="/etc/systemd/system/$service"
binary="$root/bin/telemetry-server"
versioned="$binary.$revision"
mkdir -p "$root/bin" "$root/data"
unit_temp="$(mktemp "$root/unit.XXXXXXXX")"
trap 'rm -f -- "$unit_temp" "$upload"' EXIT

if [[ ! -s "$root/data/telemetry-state.sqlite" && ! -s "$legacy" ]]; then
    echo "Legacy telemetry snapshot is missing"
    exit 1
fi

cat > "$unit_temp" <<UNIT
[Unit]
Description=SeaweedFS Enterprise Telemetry Server
After=network.target

[Service]
Type=simple
User=opc
WorkingDirectory=$root
ExecStart=$binary -listen-host=127.0.0.1 -port=8354 -db-file=$root/data/telemetry-state.sqlite -state-file=$legacy
Restart=on-failure
RestartSec=5

[Install]
WantedBy=multi-user.target
UNIT

if [[ -e "$unit" ]]; then
    cmp -s "$unit" "$unit_temp" || { echo "Unexpected existing staging unit"; exit 1; }
else
    sudo -n install -m 0644 "$unit_temp" "$unit"
fi
sudo -n systemctl daemon-reload

previous="$(readlink "$binary" || true)"
if [[ -e "$binary" && -z "$previous" ]]; then
    echo "Existing staging binary is not a release symlink"
    exit 1
fi
install -m 0755 "$upload" "$versioned"
switch_release() {
    local target="$1" pending="$binary.pending.$$"
    ln -s "$target" "$pending"
    mv -Tf "$pending" "$binary"
}
switch_release "$versioned"
sudo -n systemctl restart "$service"

ready=false
for ((attempt = 0; attempt < 45; attempt++)); do
    if curl -fs --max-time 3 http://127.0.0.1:8354/health >/dev/null; then
        ready=true
        break
    fi
    sleep 2
done
if [[ "$ready" != true ]]; then
    if [[ -n "$previous" ]]; then
        switch_release "$previous"
        sudo -n systemctl restart "$service"
    else
        sudo -n systemctl stop "$service"
    fi
    echo "Enterprise telemetry failed its health check"
    exit 1
fi

# Compare on oracle1. Production telemetry records stay on the host, and the
# public job log receives only aggregate verification results.
python3 - <<'PY'
import hashlib
import json
import time
from urllib.parse import urlencode
from urllib.request import urlopen

legacy = 'http://127.0.0.1:8353'
stage = 'http://127.0.0.1:8354'

def fetch(base, path):
    with urlopen(base + path, timeout=40) as response:
        return response.read()

def get(base, path):
    return json.loads(fetch(base, path))

old_html = fetch(legacy, '/dashboard')
if old_html != fetch(stage, '/dashboard'):
    raise SystemExit('Dashboard HTML differs')
print('Dashboard HTML matches, SHA-256 {}'.format(hashlib.sha256(old_html).hexdigest()))

old_stats = get(legacy, '/api/stats')
new_stats = get(stage, '/api/stats')
for stats in (old_stats, new_stats):
    if not all(k in stats for k in ('active_instances', 'total_instances', 'confirmed_instances', 'versions')):
        raise SystemExit('Unexpected stats response')
print('Live and staged cluster counts:', [(s['total_instances'], s['active_instances'], s['confirmed_instances']) for s in (old_stats, new_stats)])

paths = (
    '/api/metrics?days=30',
    '/api/cluster-sizes?days=30&limit=20',
    '/api/versions?days=30&limit=8',
)
for path in paths:
    old = get(legacy, path)
    new = get(stage, path)
    if not old.get('dates') or old['dates'] != new.get('dates'):
        raise SystemExit('Chart date axes differ for ' + path.partition('?')[0])
    print('{} returned {} dates on both ports, current data {}'.format(
        path.partition('?')[0], len(new['dates']), 'matches' if old == new else 'differs'))
    if path.startswith('/api/cluster-sizes'):
        clusters = [cluster['cluster_id'] for cluster in new['clusters'][:8]]

today = int(time.time()) // 86400
for cluster in clusters:
    path = '/api/history?' + urlencode({'cluster_id': cluster, 'days': 14})
    def completed(host):
        return [s for s in get(host, path)['samples'] if today - 10 <= s['ts'] // 86400 < today]
    old, new = completed(legacy), completed(stage)
    if not old or old != new:
        raise SystemExit('Completed-day history differs for a sampled cluster')
if not clusters:
    raise SystemExit('No clusters available to compare')
print('Completed-day history matches for {} clusters'.format(len(clusters)))
PY

sudo -n systemctl enable "$service"
printf 'Staged SQLite database: %s bytes\n' "$(stat -c %s "$root/data/telemetry-state.sqlite")"
