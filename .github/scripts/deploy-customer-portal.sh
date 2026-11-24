#!/usr/bin/env bash
set -euo pipefail

upload="$1"
revision="$2"
[[ "$(id -un)" == opc && "$HOME" == /home/opc ]]
[[ "$upload" =~ ^/home/opc/portal-upload\.[A-Za-z0-9]+$ && "$revision" =~ ^[a-f0-9]{40}$ ]]
umask 077

root="$HOME/seaweedfs-portal"
service=seaweedfs-portal.service
current="$root/current"
database="$root/data/portal.sqlite"
origin=portal.seaweedfs.com
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
            curl -fs --max-time 3 http://127.0.0.1:8355/health >/dev/null; then
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
            echo "Restored the previous portal release" >&2
        else
            echo "Failed to restore the previous portal release" >&2
        fi
    fi
    rm -f -- "$current.pending.$$" "$upload/customer-portal" "$upload/deploy-customer-portal.sh"
    rmdir "$upload"
    exit "$status"
}
trap cleanup EXIT

exec 9>"$root/deploy.lock"
flock -n 9
if ! grep -Eq '^[[:space:]]*proxy_pass http://127\.0\.0\.1:8355;' /etc/nginx/conf.d/portal.conf; then
    echo "Production nginx must point at 127.0.0.1:8355" >&2
    exit 1
fi
if ! previous="$(readlink -e "$current")" ||
    [[ "$previous" != "$root"/releases/* || ! -x "$previous/start.sh" ]]; then
    echo "No previous portal release is available" >&2
    exit 1
fi
if [[ ! -f "$database" || ! -s "$database" ]]; then
    echo "Production portal database is missing or empty" >&2
    exit 1
fi

candidate="$(mktemp -d "$root/releases/$revision.XXXXXXXX")"
install -m 0755 "$upload/customer-portal" "$candidate/customer-portal"
cat > "$candidate/start.sh" <<'START'
#!/usr/bin/env bash
set -euo pipefail
release="$(dirname "$(readlink -f "$0")")"
exec "$release/customer-portal"
START
chmod 0700 "$candidate/start.sh"

switch_release "$candidate"
switched=true
sudo -n systemctl restart "$service"
if ! wait_for_health; then
    echo "Customer portal failed its health check" >&2
    exit 1
fi
curl -fsS --max-time 15 \
    --cacert /etc/nginx/certs/portal.crt \
    --resolve "$origin:443:127.0.0.1" "https://$origin/login" >/dev/null

accepted=true
printf 'Deployed customer portal %s to port 8355 using the existing SQLite database\n' "$revision"
