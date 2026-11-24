#!/usr/bin/env bash
set -euo pipefail

upload="$1"
revision="$2"
[[ "$(id -un)" == opc && "$HOME" == /home/opc ]]
[[ "$upload" =~ ^/home/opc/telemetry-upload\.[A-Za-z0-9]+$ && "$revision" =~ ^[a-f0-9]{40}$ ]]
umask 077

root="$HOME/seaweedfs-telemetry/enterprise"
service=telemetry-enterprise.service
current="$root/current"
database="$root/data/telemetry-state.sqlite"
origin=telemetry.seaweedfs.com
previous=""
switched=false
accepted=false

switch_release() {
    local temporary="$current.pending.$$"
    ln -s "$1" "$temporary" && mv -Tf "$temporary" "$current"
}

wait_for_health() {
    local attempt
    for ((attempt = 0; attempt < 30; attempt++)); do
        if systemctl is-active --quiet "$service" &&
            curl -fs --max-time 3 http://127.0.0.1:8354/health >/dev/null; then
            return 0
        fi
        sleep 2
    done
    return 1
}

cleanup() {
    local status=$?
    trap - EXIT
    set +e
    if [[ "$switched" == true && "$accepted" != true ]]; then
        if switch_release "$previous" && sudo -n systemctl restart "$service" && wait_for_health; then
            echo "Restored the previous telemetry release" >&2
        else
            echo "Failed to restore the previous telemetry release" >&2
        fi
    fi
    rm -f -- "$current.pending.$$" "$upload/telemetry-server" "$upload/deploy-enterprise-telemetry.sh"
    rmdir "$upload"
    exit "$status"
}
trap cleanup EXIT

exec 9>"$root/deploy.lock"
flock -n 9
if ! grep -Eq '^[[:space:]]*proxy_pass http://127\.0\.0\.1:8354;' /etc/nginx/conf.d/telemetry.conf; then
    echo "Production nginx must point at 127.0.0.1:8354" >&2
    exit 1
fi
if ! previous="$(readlink -e "$current")" ||
    [[ "$previous" != "$root"/releases/* || ! -x "$previous/start.sh" ]]; then
    echo "No previous telemetry release is available" >&2
    exit 1
fi

# Adopt the live database from the cutover release in place. Its directory must
# be retained while it is the target of this link, including its SQLite WAL.
mkdir -p "$root/data"
if [[ ! -e "$database" && ! -L "$database" ]]; then
    if [[ ! -f "$previous/telemetry-state.sqlite" || ! -s "$previous/telemetry-state.sqlite" ]]; then
        echo "No existing telemetry database to adopt" >&2
        exit 1
    fi
    ln -s "$previous/telemetry-state.sqlite" "$database"
fi
if [[ ! -f "$database" || ! -s "$database" ]]; then
    echo "Production telemetry database is missing or empty" >&2
    exit 1
fi

candidate="$(mktemp -d "$root/releases/$revision.XXXXXXXX")"
install -m 0755 "$upload/telemetry-server" "$candidate/telemetry-server"
cat > "$candidate/start.sh" <<'START'
#!/usr/bin/env bash
set -euo pipefail
release="$(dirname "$(readlink -f "$0")")"
database="$(readlink -e "$release/../../data/telemetry-state.sqlite")"
[[ -f "$database" && -s "$database" ]]
exec "$release/telemetry-server" -listen-host=127.0.0.1 -port=8354 -db-file="$database" -state-file=
START
chmod 0700 "$candidate/start.sh"

switch_release "$candidate"
switched=true
sudo -n systemctl restart "$service"
if ! wait_for_health; then
    echo "Enterprise telemetry failed its health check" >&2
    exit 1
fi
curl -fsS --max-time 15 \
    --cacert /etc/nginx/certs/telemetry.crt \
    --resolve "$origin:443:127.0.0.1" "https://$origin/health" >/dev/null

accepted=true
printf 'Deployed enterprise telemetry %s to port 8354 using the existing SQLite database\n' "$revision"
