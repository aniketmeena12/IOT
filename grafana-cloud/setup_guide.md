# 🌐 Grafana Cloud Setup Guide

## Architecture

```
┌─────────────────────┐         ┌──────────────────────────┐
│  Your Local Machine  │         │     Grafana Cloud        │
│                      │         │                          │
│  ┌─────────────────┐ │  Tunnel │  ┌────────────────────┐  │
│  │  Pulse Simulator│ │ ──────► │  │ InfluxDB Datasource│  │
│  └────────┬────────┘ │         │  └────────┬───────────┘  │
│           │          │         │           │              │
│  ┌────────▼────────┐ │ ◄──────┤  ┌────────▼───────────┐  │
│  │    InfluxDB     │ │         │  │    Dashboard        │  │
│  └─────────────────┘ │         │  │  (10 panels, live)  │  │
│                      │         │  └────────────────────┘  │
└─────────────────────┘         └──────────────────────────┘
```

## Prerequisites

- A **Grafana Cloud** account (free tier at https://grafana.com/products/cloud/)
- Docker running locally with your existing setup

---

## Step 1: Create Grafana Cloud Account

1. Go to **https://grafana.com/auth/sign-up/create-user**
2. Sign up (free tier includes 10k metrics, 50GB logs, 14-day retention)
3. Note down your:
   - **Stack URL**: `https://<your-stack>.grafana.net`
   - **Username** (usually your email)
   - **Cloud API Key**: Create one at `https://grafana.com/orgs/<your-org>/api-keys`
     - Role: **Admin** (needed to create datasources & dashboards)

---

## Step 2: Expose InfluxDB to the Internet

Your local InfluxDB must be reachable by Grafana Cloud. Choose **one** method:

### Option A: Cloudflare Tunnel (Recommended — Free, Secure, No Port Forwarding)

```bash
# Run setup script
cd /home/ashok/IOT/grafana-cloud
bash expose_influxdb.sh
```

### Option B: ngrok (Quick Testing)

```bash
ngrok http 8086
# Note the https://xxxx.ngrok-free.app URL
```

### Option C: Public Server / Cloud VM

If your InfluxDB is on a VPS with a public IP, just use `http://<public-ip>:8086`.

---

## Step 3: Deploy to Grafana Cloud

Run the deployment script:

```bash
cd /home/ashok/IOT/grafana-cloud
bash deploy_to_grafana_cloud.sh
```

You'll be prompted for:
1. Your Grafana Cloud stack URL
2. Your API key
3. Your InfluxDB public URL (from Step 2)

The script will:
- Create the InfluxDB Flux datasource in Grafana Cloud
- Import the full dashboard with all 10 panels

---

## Step 4: Verify

1. Open your Grafana Cloud dashboard URL (printed by the script)
2. All 10 panels should be live with streaming data
3. Dashboard auto-refreshes every 5 seconds

---

## Troubleshooting

| Issue | Fix |
|-------|-----|
| "No data" on panels | Check tunnel is running and InfluxDB is accessible from internet |
| Datasource error | Verify the InfluxDB URL, token, and org in Grafana Cloud datasource settings |
| Tunnel disconnected | Restart `cloudflared` or `ngrok` |
| Dashboard not loading | Re-run `deploy_to_grafana_cloud.sh` |

---

## Security Notes

⚠️ **Important**: Your InfluxDB token (`my-super-secret-token`) is used in the datasource config.
For production:
1. Create a **read-only** InfluxDB token for Grafana Cloud
2. Restrict the tunnel to only allow Grafana Cloud IPs
3. Enable InfluxDB TLS
