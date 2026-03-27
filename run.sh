#!/bin/bash
# =============================================================================
# run.sh — Full deploy: Docker stack + Cloudflare tunnel + Grafana Cloud
# Usage:  ./run.sh
# =============================================================================
set -euo pipefail

CYAN='\033[0;36m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'
RED='\033[0;31m'; BOLD='\033[1m'; NC='\033[0m'

PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CONFIG_FILE="${PROJECT_DIR}/grafana-cloud/.grafana_cloud_config"
TUNNEL_LOG="/tmp/cloudflare-tunnel.log"
PID_FILE="/tmp/cloudflared.pid"
URL_FILE="/tmp/cloudflared-url.txt"

log()  { echo -e "${CYAN}[$(date '+%H:%M:%S')]${NC} $*"; }
ok()   { echo -e "${GREEN}  ✓${NC} $*"; }
warn() { echo -e "${YELLOW}  ⚠${NC} $*"; }
die()  { echo -e "${RED}  ✗ ERROR:${NC} $*"; exit 1; }

# ── Cleanup on exit ──────────────────────────────────────────────────────────
tunnel_pid=""
cleanup() {
    echo ""
    log "Shutting down..."
    [[ -n "$tunnel_pid" ]] && kill "$tunnel_pid" 2>/dev/null && rm -f "$PID_FILE" "$URL_FILE"
    log "Tunnel stopped. Docker stack still running. To stop: docker compose down"
}
trap cleanup EXIT INT TERM

# ── Step 0: Check docker access ──────────────────────────────────────────────
echo -e "${CYAN}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
echo -e "${CYAN}  IOT Deploy: Docker + Tunnel + Grafana Cloud${NC}"
echo -e "${CYAN}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
echo ""

log "Checking Docker access..."
if ! docker info >/dev/null 2>&1; then
    warn "Cannot access Docker. Trying with sudo..."
    if sudo -n docker info >/dev/null 2>&1; then
        DOCKER="sudo docker"
        COMPOSE="sudo docker compose"
    else
        die "Docker not accessible. Run: sudo usermod -aG docker \$USER && newgrp docker"
    fi
else
    DOCKER="docker"
    COMPOSE="docker compose"
fi
ok "Docker accessible"

# ── Step 1: Start Docker stack ───────────────────────────────────────────────
echo ""
log "[1/3] Starting Docker stack (influxdb + grafana + simulators)..."
cd "${PROJECT_DIR}"

$COMPOSE up -d --build 2>&1 | grep -E "Creating|Starting|Building|✓|done|error" || true

# Wait for InfluxDB to be healthy
log "Waiting for InfluxDB to be healthy..."
for i in {1..30}; do
    if $DOCKER exec spring_influxdb influx ping >/dev/null 2>&1; then
        ok "InfluxDB healthy"
        break
    fi
    [[ $i -eq 30 ]] && die "InfluxDB did not become healthy in time"
    sleep 2
    echo -n "."
done
echo ""

# ── Step 2: Start Cloudflare tunnel ─────────────────────────────────────────
echo ""
log "[2/3] Starting Cloudflare tunnel for InfluxDB (port 8086)..."

if ! command -v cloudflared &>/dev/null; then
    warn "cloudflared not found. Installing..."
    if command -v apt-get &>/dev/null; then
        curl -fsSL https://pkg.cloudflare.com/cloudflare-main.gpg | sudo tee /usr/share/keyrings/cloudflare-main.gpg >/dev/null
        echo "deb [signed-by=/usr/share/keyrings/cloudflare-main.gpg] https://pkg.cloudflare.com/cloudflared $(lsb_release -cs) main" \
            | sudo tee /etc/apt/sources.list.d/cloudflared.list
        sudo apt-get update -q && sudo apt-get install -y cloudflared
    else
        die "Cannot install cloudflared. Please install manually: https://developers.cloudflare.com/cloudflare-one/connections/connect-networks/downloads/"
    fi
fi

# Kill any existing tunnel
if [[ -f "$PID_FILE" ]]; then
    kill "$(cat "$PID_FILE")" 2>/dev/null || true
fi
rm -f "$PID_FILE" "$URL_FILE"
> "$TUNNEL_LOG"

cloudflared tunnel --url http://localhost:8086 >"$TUNNEL_LOG" 2>&1 &
tunnel_pid=$!
echo $tunnel_pid > "$PID_FILE"

# Wait for URL (up to 40s)
TUNNEL_URL=""
for i in {1..40}; do
    sleep 1
    TUNNEL_URL=$(grep -oP 'https://[a-z0-9-]+\.trycloudflare\.com' "$TUNNEL_LOG" 2>/dev/null | head -1 || true)
    if [[ -n "$TUNNEL_URL" ]]; then
        echo "$TUNNEL_URL" > "$URL_FILE"
        ok "Tunnel ready: ${BOLD}${TUNNEL_URL}${NC}"
        break
    fi
    echo -n "."
done
echo ""

[[ -z "$TUNNEL_URL" ]] && die "Tunnel URL not detected after 40s. Check: $TUNNEL_LOG"

# ── Step 3: Deploy to Grafana Cloud ─────────────────────────────────────────
echo ""
log "[3/3] Deploying datasource + dashboards to Grafana Cloud..."

# Load saved Grafana config
[[ -f "$CONFIG_FILE" ]] || die "No Grafana Cloud config found at $CONFIG_FILE"
source "$CONFIG_FILE"

[[ -z "${GRAFANA_CLOUD_URL:-}"    ]] && die "GRAFANA_CLOUD_URL missing from config"
[[ -z "${GRAFANA_CLOUD_API_KEY:-}" ]] && die "GRAFANA_CLOUD_API_KEY missing from config"

# Update config with fresh tunnel URL
sed -i "s|INFLUXDB_PUBLIC_URL=.*|INFLUXDB_PUBLIC_URL=\"${TUNNEL_URL}\"|" "$CONFIG_FILE"
INFLUXDB_PUBLIC_URL="$TUNNEL_URL"
ok "Config updated with new tunnel URL"

grafana_api() {
    local method="$1" endpoint="$2" data="${3:-}"
    local args=(-s -w "\n%{http_code}" -X "$method"
        -H "Authorization: Bearer ${GRAFANA_CLOUD_API_KEY}"
        -H "Content-Type: application/json")
    [[ -n "$data" ]] && args+=(-d "$data")
    args+=("${GRAFANA_CLOUD_URL}/api${endpoint}")
    curl "${args[@]}"
}

# Test connection
RESP=$(grafana_api GET "/org")
CODE=$(echo "$RESP" | tail -1)
[[ "$CODE" != "200" ]] && die "Cannot connect to Grafana Cloud (HTTP $CODE). Check API key."
ORG=$(echo "$RESP" | sed '$d' | python3 -c "import sys,json; print(json.load(sys.stdin).get('name','?'))" 2>/dev/null)
ok "Grafana Cloud connected: ${ORG}"

# Create/update InfluxDB datasource
DS_PAYLOAD=$(cat <<EOJSON
{
  "name": "InfluxDB-Flux",
  "type": "influxdb",
  "access": "proxy",
  "url": "${INFLUXDB_PUBLIC_URL}",
  "jsonData": { "version": "Flux", "organization": "spring_factory", "defaultBucket": "spring_data", "tlsSkipVerify": true },
  "secureJsonData": { "token": "my-super-secret-token" },
  "isDefault": true,
  "editable": true
}
EOJSON
)

DS_CHECK=$(grafana_api GET "/datasources/name/InfluxDB-Flux")
DS_CODE=$(echo "$DS_CHECK" | tail -1)

if [[ "$DS_CODE" == "200" ]]; then
    DS_ID=$(echo "$DS_CHECK" | sed '$d' | python3 -c "import sys,json; print(json.load(sys.stdin)['id'])")
    DS_RESP=$(grafana_api PUT "/datasources/${DS_ID}" "$DS_PAYLOAD")
    [[ "$(echo "$DS_RESP" | tail -1)" == "200" ]] && ok "Datasource updated (id: ${DS_ID})" || die "Failed to update datasource"
else
    DS_RESP=$(grafana_api POST "/datasources" "$DS_PAYLOAD")
    DSCODE=$(echo "$DS_RESP" | tail -1)
    [[ "$DSCODE" == "200" || "$DSCODE" == "409" ]] && ok "Datasource created" || die "Failed to create datasource (HTTP $DSCODE)"
fi

# Get datasource UID
DS_INFO=$(grafana_api GET "/datasources/name/InfluxDB-Flux")
DS_UID=$(echo "$DS_INFO" | sed '$d' | python3 -c "import sys,json; print(json.load(sys.stdin)['uid'])")
ok "Datasource UID: ${DS_UID}"

# Import all dashboards
DASHBOARD_URLS=""
COUNT=0
DASHBOARDS_DIR="${PROJECT_DIR}/grafana/dashboards"

for DASH_FILE in "${DASHBOARDS_DIR}"/*.json; do
    DASH_NAME=$(basename "$DASH_FILE" .json)

    PATCHED=$(python3 <<PYEOF
import json, sys

with open("${DASH_FILE}") as f:
    dash = json.load(f)

dash.pop("id", None)
dash["version"] = None

def patch(obj):
    if isinstance(obj, dict):
        if "datasource" in obj and isinstance(obj["datasource"], dict):
            uid = obj["datasource"].get("uid", "")
            if uid in ("", "influxdb-uid"):
                obj["datasource"]["uid"] = "${DS_UID}"
        for v in obj.values():
            patch(v)
    elif isinstance(obj, list):
        for item in obj:
            patch(item)

patch(dash)
print(json.dumps({"dashboard": dash, "overwrite": True, "message": "Auto-deployed via run.sh"}))
PYEOF
)

    IMP_RESP=$(grafana_api POST "/dashboards/db" "$PATCHED")
    IMP_CODE=$(echo "$IMP_RESP" | tail -1)
    IMP_BODY=$(echo "$IMP_RESP" | sed '$d')

    if [[ "$IMP_CODE" == "200" ]]; then
        DASH_URL=$(echo "$IMP_BODY" | python3 -c "import sys,json; print(json.load(sys.stdin).get('url',''))" 2>/dev/null)
        ok "Dashboard imported: ${DASH_NAME}"
        DASHBOARD_URLS="${DASHBOARD_URLS}  ${GRAFANA_CLOUD_URL}${DASH_URL}?refresh=5s\n"
        ((COUNT++))
    else
        warn "Failed to import ${DASH_NAME} (HTTP ${IMP_CODE}): $(echo "$IMP_BODY" | head -c 200)"
    fi
done

# ── Done ─────────────────────────────────────────────────────────────────────
echo ""
echo -e "${GREEN}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
echo -e "${GREEN}  Deployment Complete! (${COUNT} dashboards)${NC}"
echo -e "${GREEN}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
echo ""
echo -e "  ${BOLD}Live dashboards:${NC}"
echo -e "$DASHBOARD_URLS"
echo -e "  ${BOLD}Tunnel URL:${NC} ${TUNNEL_URL}"
echo -e "  ${BOLD}Local Grafana:${NC} http://localhost:3001  (admin/admin)"
echo ""
echo -e "  ${YELLOW}Keep this terminal open — tunnel stays alive with auto-restart.${NC}"
echo ""

# ── Keep tunnel alive (auto-restart loop) ───────────────────────────────────
log "Tunnel running (PID: ${tunnel_pid}). Auto-restarting on failure. Ctrl+C to stop."

while true; do
    if ! kill -0 "$tunnel_pid" 2>/dev/null; then
        warn "Tunnel died. Restarting in 5s..."
        sleep 5
        > "$TUNNEL_LOG"
        cloudflared tunnel --url http://localhost:8086 >"$TUNNEL_LOG" 2>&1 &
        tunnel_pid=$!
        echo $tunnel_pid > "$PID_FILE"

        NEW_URL=""
        for i in {1..40}; do
            sleep 1
            NEW_URL=$(grep -oP 'https://[a-z0-9-]+\.trycloudflare\.com' "$TUNNEL_LOG" 2>/dev/null | head -1 || true)
            [[ -n "$NEW_URL" ]] && break
        done

        if [[ -n "$NEW_URL" && "$NEW_URL" != "$TUNNEL_URL" ]]; then
            TUNNEL_URL="$NEW_URL"
            echo "$TUNNEL_URL" > "$URL_FILE"
            warn "New tunnel URL: ${BOLD}${TUNNEL_URL}${NC}"
            warn "Re-deploying datasource with new URL..."

            # Update datasource URL in Grafana Cloud
            sed -i "s|INFLUXDB_PUBLIC_URL=.*|INFLUXDB_PUBLIC_URL=\"${TUNNEL_URL}\"|" "$CONFIG_FILE"
            DS_PAYLOAD2=$(echo "$DS_PAYLOAD" | python3 -c "
import sys, json
p = json.load(sys.stdin)
p['url'] = '${TUNNEL_URL}'
print(json.dumps(p))
")
            DS_INFO2=$(grafana_api GET "/datasources/name/InfluxDB-Flux")
            DS_ID2=$(echo "$DS_INFO2" | sed '$d' | python3 -c "import sys,json; print(json.load(sys.stdin)['id'])" 2>/dev/null || echo "")
            if [[ -n "$DS_ID2" ]]; then
                grafana_api PUT "/datasources/${DS_ID2}" "$DS_PAYLOAD2" >/dev/null
                ok "Grafana Cloud datasource updated with new tunnel URL"
            fi
        fi
    fi
    sleep 10
done
