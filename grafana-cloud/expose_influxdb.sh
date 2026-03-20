#!/usr/bin/env bash
# =============================================================================
# expose_influxdb.sh — Expose local InfluxDB via Cloudflare Tunnel
# =============================================================================
set -euo pipefail

CYAN='\033[0;36m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
NC='\033[0m'

echo -e "${CYAN}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
echo -e "${CYAN}  Expose Local InfluxDB to the Internet${NC}"
echo -e "${CYAN}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
echo ""

# ── Check if InfluxDB is running ─────────────────────────────────────────────
echo -e "${CYAN}[1/3] Checking InfluxDB...${NC}"
if curl -s http://localhost:8086/health | grep -q '"status":"pass"'; then
    echo -e "${GREEN}  ✓ InfluxDB is running on port 8086${NC}"
else
    echo -e "${RED}  ✗ InfluxDB is not running! Start it first:${NC}"
    echo "    cd /home/ashok/IOT && docker compose up -d influxdb"
    exit 1
fi

# ── Detect or install tunnel tool ────────────────────────────────────────────
echo ""
echo -e "${CYAN}[2/3] Setting up tunnel...${NC}"
echo ""
echo "Choose your tunnel method:"
echo "  1) Cloudflare Tunnel (cloudflared) — Recommended, free, persistent"
echo "  2) ngrok — Quick and easy for testing"
echo ""
read -rp "Enter choice (1 or 2): " TUNNEL_CHOICE

case "${TUNNEL_CHOICE}" in
    1)
        # ── Cloudflare Tunnel ────────────────────────────────────────────
        if ! command -v cloudflared &>/dev/null; then
            echo -e "${YELLOW}  Installing cloudflared...${NC}"
            # Linux amd64
            curl -fsSL https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-linux-amd64 -o /tmp/cloudflared
            chmod +x /tmp/cloudflared
            sudo mv /tmp/cloudflared /usr/local/bin/cloudflared
            echo -e "${GREEN}  ✓ cloudflared installed${NC}"
        else
            echo -e "${GREEN}  ✓ cloudflared already installed${NC}"
        fi

        echo ""
        echo -e "${CYAN}[3/3] Starting Cloudflare Quick Tunnel...${NC}"
        echo -e "${YELLOW}  This creates a free temporary tunnel (no Cloudflare account needed).${NC}"
        echo -e "${YELLOW}  For a permanent tunnel, use: cloudflared tunnel create influxdb${NC}"
        echo ""
        echo -e "${GREEN}  ──────────────────────────────────────────────${NC}"
        echo -e "${GREEN}  Look for the URL line below like:${NC}"
        echo -e "${GREEN}    https://xxxxx-xxxx-xxxx.trycloudflare.com${NC}"
        echo -e "${GREEN}  Copy that URL — you'll need it for the deploy script!${NC}"
        echo -e "${GREEN}  ──────────────────────────────────────────────${NC}"
        echo ""
        echo -e "${YELLOW}  Press Ctrl+C to stop the tunnel.${NC}"
        echo ""

        cloudflared tunnel --url http://localhost:8086
        ;;

    2)
        # ── ngrok ────────────────────────────────────────────────────────
        if ! command -v ngrok &>/dev/null; then
            echo -e "${YELLOW}  Installing ngrok...${NC}"
            curl -fsSL https://ngrok-agent.s3.amazonaws.com/ngrok-v3-stable-linux-amd64.tgz | tar xzf - -C /tmp
            sudo mv /tmp/ngrok /usr/local/bin/ngrok
            echo -e "${GREEN}  ✓ ngrok installed${NC}"
            echo ""
            echo -e "${YELLOW}  NOTE: ngrok requires a free account. Sign up at https://ngrok.com${NC}"
            read -rp "  Enter your ngrok authtoken: " NGROK_TOKEN
            ngrok config add-authtoken "${NGROK_TOKEN}"
        else
            echo -e "${GREEN}  ✓ ngrok already installed${NC}"
        fi

        echo ""
        echo -e "${CYAN}[3/3] Starting ngrok tunnel...${NC}"
        echo -e "${GREEN}  ──────────────────────────────────────────────${NC}"
        echo -e "${GREEN}  Look for the Forwarding line like:${NC}"
        echo -e "${GREEN}    https://xxxx-xxxx.ngrok-free.app → http://localhost:8086${NC}"
        echo -e "${GREEN}  Copy that URL — you'll need it for the deploy script!${NC}"
        echo -e "${GREEN}  ──────────────────────────────────────────────${NC}"
        echo ""

        ngrok http 8086
        ;;

    *)
        echo -e "${RED}Invalid choice. Exiting.${NC}"
        exit 1
        ;;
esac
