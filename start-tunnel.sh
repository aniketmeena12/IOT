#!/bin/bash
# Stable Cloudflare tunnel with auto-restart on failure

TUNNEL_LOG="/tmp/cloudflare-tunnel.log"
PID_FILE="/tmp/cloudflared.pid"
URL_FILE="/tmp/cloudflared-url.txt"
RETRY_DELAY=5

cleanup() {
    echo ""
    echo "Stopping tunnel..."
    if [ -f "$PID_FILE" ]; then
        kill "$(cat "$PID_FILE")" 2>/dev/null
        rm -f "$PID_FILE"
    fi
    rm -f "$URL_FILE"
    exit 0
}

trap cleanup SIGINT SIGTERM

start_tunnel() {
    # Kill old process if any
    if [ -f "$PID_FILE" ]; then
        old_pid=$(cat "$PID_FILE" 2>/dev/null)
        kill "$old_pid" 2>/dev/null
        rm -f "$PID_FILE"
    fi

    > "$TUNNEL_LOG"

    echo "[$(date '+%H:%M:%S')] Starting Cloudflare tunnel..."
    cloudflared tunnel --url http://localhost:8086 > "$TUNNEL_LOG" 2>&1 &
    PID=$!
    echo $PID > "$PID_FILE"

    # Wait up to 30 seconds for tunnel URL
    for i in {1..30}; do
        sleep 1
        TUNNEL_URL=$(grep -oP 'https://[a-z0-9-]+\.trycloudflare\.com' "$TUNNEL_LOG" | head -1)
        if [ -n "$TUNNEL_URL" ]; then
            echo "$TUNNEL_URL" > "$URL_FILE"
            echo "[$(date '+%H:%M:%S')] Tunnel ready: $TUNNEL_URL"
            echo ""
            echo "  Update your .grafana_cloud_config with:"
            echo "  INFLUXDB_PUBLIC_URL=\"$TUNNEL_URL\""
            return 0
        fi
        echo "  Waiting... ($i/30)"
    done

    echo "[$(date '+%H:%M:%S')] WARNING: Tunnel URL not detected — check $TUNNEL_LOG"
    return 1
}

# Main loop — restart tunnel if it dies
echo "======================================"
echo " Cloudflare Tunnel (auto-restart mode)"
echo "======================================"
echo ""

while true; do
    start_tunnel

    # Wait for the cloudflared process to exit
    if [ -f "$PID_FILE" ]; then
        wait "$(cat "$PID_FILE")" 2>/dev/null
    fi

    echo "[$(date '+%H:%M:%S')] Tunnel exited. Restarting in ${RETRY_DELAY}s... (Ctrl+C to stop)"
    sleep "$RETRY_DELAY"
done
