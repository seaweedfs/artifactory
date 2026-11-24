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
current="$root/current"
mkdir -p "$root/releases"
unit_temp="$(mktemp "$root/unit.XXXXXXXX")"

switch_release() {
    local target="$1" pending="$current.pending.$$"
    ln -s "$target" "$pending" && mv -Tf "$pending" "$current"
}

previous=""
switched=false
accepted=false
cleanup() {
    status=$?
    trap - EXIT
    set +e
    if [[ "$switched" == true && "$accepted" != true ]]; then
        if [[ -n "$previous" ]]; then
            if ! switch_release "$previous" || ! sudo -n systemctl restart "$service"; then
                echo "Failed to restore the previous staging release" >&2
            fi
        else
            sudo -n systemctl stop "$service"
            sudo -n systemctl disable "$service"
            rm -f -- "$current"
        fi
    fi
    rm -f -- "$unit_temp" "$upload/telemetry-server" "$upload/deploy-enterprise-telemetry.sh" \
        "$upload/compare-enterprise-telemetry.py"
    rmdir "$upload"
    exit "$status"
}
trap cleanup EXIT

exec 9>"$root/deploy.lock"
flock -n 9
previous="$(readlink "$current" || true)"
if ! grep -Eq '^[[:space:]]*proxy_pass http://(localhost|127\.0\.0\.1):8353;' /etc/nginx/conf.d/telemetry.conf; then
    echo "This workflow stages releases only while nginx points to port 8353"
    exit 1
fi
if [[ ! -s "$legacy" ]]; then
    echo "Legacy telemetry snapshot is missing"
    exit 1
fi
if [[ -e "$current" && -z "$previous" ]]; then
    echo "Unexpected staging release path"
    exit 1
fi

candidate="$(mktemp -d "$root/releases/$revision.XXXXXXXX")"
install -m 0755 "$upload/telemetry-server" "$candidate/telemetry-server"
cp -- "$legacy" "$candidate/telemetry-state.json"
cat > "$candidate/start.sh" <<'START'
#!/usr/bin/env bash
set -euo pipefail
release="$(dirname "$(readlink -f "$0")")"
exec "$release/telemetry-server" -listen-host=127.0.0.1 -port=8354 \
    -db-file="$release/telemetry-state.sqlite" -state-file="$release/telemetry-state.json"
START
chmod 0700 "$candidate/start.sh"

cat > "$unit_temp" <<UNIT
[Unit]
Description=SeaweedFS Enterprise Telemetry Server
After=network.target

[Service]
Type=simple
User=opc
WorkingDirectory=$root
ExecStart=$current/start.sh
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

switch_release "$candidate"
switched=true
sudo -n systemctl restart "$service"

ready=false
for ((attempt = 0; attempt < 45; attempt++)); do
    if sudo -n systemctl is-active --quiet "$service" &&
        curl -fs --max-time 3 http://127.0.0.1:8354/health >/dev/null; then
        ready=true
        break
    fi
    sleep 2
done
if [[ "$ready" != true ]]; then
    echo "Enterprise telemetry failed its health check"
    exit 1
fi

# Compare on oracle1 and emit only aggregate results to the public job log.
python3 "$upload/compare-enterprise-telemetry.py"
sudo -n systemctl enable "$service"
accepted=true
printf 'Staged SQLite database: %s bytes\n' "$(stat -c %s "$candidate/telemetry-state.sqlite")"
