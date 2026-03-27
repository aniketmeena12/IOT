#!/usr/bin/env bash
# =============================================================================
# deploy_to_grafana_cloud.sh
# Creates InfluxDB datasource + imports dashboard to Grafana Cloud
# =============================================================================
set -euo pipefail

CYAN='\033[0;36m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
BOLD='\033[1m'
NC='\033[0m'

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(dirname "${SCRIPT_DIR}")"
DASHBOARDS_DIR="${PROJECT_DIR}/grafana/dashboards"
CONFIG_FILE="${SCRIPT_DIR}/.grafana_cloud_config"

echo -e "${CYAN}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
echo -e "${CYAN}  Deploy to Grafana Cloud${NC}"
echo -e "${CYAN}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
echo ""

# ── Load or collect config ───────────────────────────────────────────────────
if [[ -f "${CONFIG_FILE}" ]]; then
    echo -e "${YELLOW}Found saved config. Loading...${NC}"
    source "${CONFIG_FILE}"
    echo -e "  Stack URL:    ${GREEN}${GRAFANA_CLOUD_URL}${NC}"
    echo -e "  InfluxDB URL: ${GREEN}${INFLUXDB_PUBLIC_URL}${NC}"
    echo ""
    read -rp "Use saved config? (Y/n): " USE_SAVED
    if [[ "${USE_SAVED,,}" == "n" ]]; then
        rm -f "${CONFIG_FILE}"
        unset GRAFANA_CLOUD_URL GRAFANA_CLOUD_API_KEY INFLUXDB_PUBLIC_URL
    fi
fi

if [[ -z "${GRAFANA_CLOUD_URL:-}" ]]; then
    echo -e "${BOLD}Enter your Grafana Cloud details:${NC}"
    echo ""

    echo -e "  ${CYAN}Stack URL${NC} (e.g. https://myorg.grafana.net)"
    read -rp "  → " GRAFANA_CLOUD_URL
    # Remove trailing slash
    GRAFANA_CLOUD_URL="${GRAFANA_CLOUD_URL%/}"

    echo ""
    echo -e "  ${CYAN}Service Account Token or API Key${NC}"
    echo -e "  Create one at: ${GRAFANA_CLOUD_URL}/org/serviceaccounts"
    echo -e "  Role: ${YELLOW}Admin${NC} (needed to create datasources & import dashboards)"
    read -rsp "  → " GRAFANA_CLOUD_API_KEY
    echo ""

    echo ""
    echo -e "  ${CYAN}InfluxDB Public URL${NC} (from tunnel — e.g. https://xxx.trycloudflare.com)"
    read -rp "  → " INFLUXDB_PUBLIC_URL
    INFLUXDB_PUBLIC_URL="${INFLUXDB_PUBLIC_URL%/}"

    # Save config for re-runs
    cat > "${CONFIG_FILE}" <<EOF
GRAFANA_CLOUD_URL="${GRAFANA_CLOUD_URL}"
GRAFANA_CLOUD_API_KEY="${GRAFANA_CLOUD_API_KEY}"
INFLUXDB_PUBLIC_URL="${INFLUXDB_PUBLIC_URL}"
EOF
    chmod 600 "${CONFIG_FILE}"
    echo -e "${GREEN}  ✓ Config saved to ${CONFIG_FILE}${NC}"
fi

echo ""

# ── Helper: API call ─────────────────────────────────────────────────────────
grafana_api() {
    local method="$1"
    local endpoint="$2"
    local data="${3:-}"

    local args=(
        -s -w "\n%{http_code}"
        -X "${method}"
        -H "Authorization: Bearer ${GRAFANA_CLOUD_API_KEY}"
        -H "Content-Type: application/json"
    )
    [[ -n "${data}" ]] && args+=(-d "${data}")
    args+=("${GRAFANA_CLOUD_URL}/api${endpoint}")

    curl "${args[@]}"
}

# ── Step 1: Test connection ──────────────────────────────────────────────────
echo -e "${CYAN}[1/4] Testing Grafana Cloud connection...${NC}"
RESPONSE=$(grafana_api GET "/org")
HTTP_CODE=$(echo "${RESPONSE}" | tail -1)
BODY=$(echo "${RESPONSE}" | sed '$d')

if [[ "${HTTP_CODE}" != "200" ]]; then
    echo -e "${RED}  ✗ Failed to connect to Grafana Cloud (HTTP ${HTTP_CODE})${NC}"
    echo -e "${RED}  Response: ${BODY}${NC}"
    echo ""
    echo "  Check your Stack URL and API Key."
    exit 1
fi

ORG_NAME=$(echo "${BODY}" | python3 -c "import sys,json; print(json.load(sys.stdin).get('name','unknown'))" 2>/dev/null || echo "unknown")
echo -e "${GREEN}  ✓ Connected to org: ${ORG_NAME}${NC}"

# ── Step 2: Test InfluxDB reachability from here ─────────────────────────────
echo ""
echo -e "${CYAN}[2/4] Testing InfluxDB tunnel (${INFLUXDB_PUBLIC_URL})...${NC}"
HEALTH=$(curl -s --max-time 10 "${INFLUXDB_PUBLIC_URL}/health" 2>/dev/null || echo '{"status":"fail"}')
if echo "${HEALTH}" | grep -q '"status":"pass"'; then
    echo -e "${GREEN}  ✓ InfluxDB reachable via tunnel${NC}"
else
    echo -e "${YELLOW}  ⚠ Could not reach InfluxDB at ${INFLUXDB_PUBLIC_URL}${NC}"
    echo -e "${YELLOW}    (This is OK if Grafana Cloud can reach it even though your local machine can't via the public URL)${NC}"
    echo -e "${YELLOW}    Continuing anyway...${NC}"
fi

# ── Step 3: Create/Update InfluxDB Datasource ───────────────────────────────
echo ""
echo -e "${CYAN}[3/4] Creating InfluxDB datasource in Grafana Cloud...${NC}"

DS_PAYLOAD=$(cat <<EOJSON
{
  "name": "InfluxDB-Flux",
  "type": "influxdb",
  "access": "proxy",
  "url": "${INFLUXDB_PUBLIC_URL}",
  "jsonData": {
    "version": "Flux",
    "organization": "spring_factory",
    "defaultBucket": "spring_data",
    "tlsSkipVerify": true
  },
  "secureJsonData": {
    "token": "my-super-secret-token"
  },
  "isDefault": true,
  "editable": true
}
EOJSON
)

# Check if datasource already exists
DS_CHECK=$(grafana_api GET "/datasources/name/InfluxDB-Flux")
DS_CHECK_CODE=$(echo "${DS_CHECK}" | tail -1)

if [[ "${DS_CHECK_CODE}" == "200" ]]; then
    # Update existing
    DS_ID=$(echo "${DS_CHECK}" | sed '$d' | python3 -c "import sys,json; print(json.load(sys.stdin)['id'])" 2>/dev/null)
    DS_RESPONSE=$(grafana_api PUT "/datasources/${DS_ID}" "${DS_PAYLOAD}")
    DS_CODE=$(echo "${DS_RESPONSE}" | tail -1)
    if [[ "${DS_CODE}" == "200" ]]; then
        echo -e "${GREEN}  ✓ Datasource updated (id: ${DS_ID})${NC}"
    else
        echo -e "${RED}  ✗ Failed to update datasource (HTTP ${DS_CODE})${NC}"
        echo "${DS_RESPONSE}" | sed '$d'
        exit 1
    fi
else
    # Create new
    DS_RESPONSE=$(grafana_api POST "/datasources" "${DS_PAYLOAD}")
    DS_CODE=$(echo "${DS_RESPONSE}" | tail -1)
    if [[ "${DS_CODE}" == "200" || "${DS_CODE}" == "409" ]]; then
        echo -e "${GREEN}  ✓ Datasource created${NC}"
    else
        echo -e "${RED}  ✗ Failed to create datasource (HTTP ${DS_CODE})${NC}"
        echo "${DS_RESPONSE}" | sed '$d'
        exit 1
    fi
fi

# Get the datasource UID so we can wire it into the dashboard
DS_INFO=$(grafana_api GET "/datasources/name/InfluxDB-Flux")
DS_UID=$(echo "${DS_INFO}" | sed '$d' | python3 -c "import sys,json; print(json.load(sys.stdin)['uid'])" 2>/dev/null)
echo -e "  Datasource UID: ${CYAN}${DS_UID}${NC}"

# ── Step 4: Import Dashboards ──────────────────────────────────────────────
echo ""
echo -e "${CYAN}[4/4] Importing dashboards...${NC}"

DASHBOARD_COUNT=0
DASHBOARD_URLS=""

for DASHBOARD_JSON in "${DASHBOARDS_DIR}"/*.json; do
    DASHBOARD_NAME=$(basename "${DASHBOARD_JSON}" .json)
    
    if [[ ! -f "${DASHBOARD_JSON}" ]]; then
        echo -e "${YELLOW}  ⊘ No dashboards found in ${DASHBOARDS_DIR}${NC}"
        continue
    fi

    # Read and patch the dashboard JSON:
    # 1. Replace all empty datasource UIDs with the real one
    # 2. Remove id field (let Grafana Cloud assign one)
    # 3. Set version to null for fresh import
    PATCHED_DASHBOARD=$(python3 <<PYEOF
import json, sys

with open("${DASHBOARD_JSON}", "r") as f:
    dash = json.load(f)

# Remove internal id — Grafana Cloud assigns its own
dash.pop("id", None)
dash["version"] = None

# Recursively replace all datasource uid="" with the real UID
def patch_ds(obj):
    if isinstance(obj, dict):
        if "datasource" in obj and isinstance(obj["datasource"], dict):
            if obj["datasource"].get("uid", "") == "":
                obj["datasource"]["uid"] = "${DS_UID}"
        for v in obj.values():
            patch_ds(v)
    elif isinstance(obj, list):
        for item in obj:
            patch_ds(item)

patch_ds(dash)

# Wrap in import payload
payload = {
    "dashboard": dash,
    "overwrite": True,
    "message": "Deployed from local setup via deploy_to_grafana_cloud.sh"
}

print(json.dumps(payload))
PYEOF
)

    IMPORT_RESPONSE=$(grafana_api POST "/dashboards/db" "${PATCHED_DASHBOARD}")
    IMPORT_CODE=$(echo "${IMPORT_RESPONSE}" | tail -1)
    IMPORT_BODY=$(echo "${IMPORT_RESPONSE}" | sed '$d')

    if [[ "${IMPORT_CODE}" == "200" ]]; then
        DASH_URL=$(echo "${IMPORT_BODY}" | python3 -c "import sys,json; print(json.load(sys.stdin).get('url',''))" 2>/dev/null)
        echo -e "${GREEN}  ✓ ${DASHBOARD_NAME}${NC}"
        DASHBOARD_URLS="${DASHBOARD_URLS}${GRAFANA_CLOUD_URL}${DASH_URL}?refresh=5s\n"
        ((DASHBOARD_COUNT++))
    else
        echo -e "${RED}  ✗ Failed to import ${DASHBOARD_NAME} (HTTP ${IMPORT_CODE})${NC}"
        echo "${IMPORT_BODY}"
        exit 1
    fi
done

echo ""
echo -e "${GREEN}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
echo -e "${GREEN}  🎉 Deployment Complete!${NC}"
echo -e "${GREEN}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
echo ""
echo -e "  ${BOLD}Dashboards (${DASHBOARD_COUNT}):${NC}"
echo -e "${DASHBOARD_URLS}" | sed 's/^/  /'
echo ""
echo -e "  ${BOLD}What's deployed:${NC}"
echo -e "  • InfluxDB Flux datasource → ${INFLUXDB_PUBLIC_URL}"
echo -e "  • ${DASHBOARD_COUNT} live dashboards (auto-refresh 5s)"
echo ""
echo -e "  ${YELLOW}⚠ Keep your tunnel running so Grafana Cloud can reach InfluxDB!${NC}"
