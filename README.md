# Production Machine IoT - Real-Time Pulse Monitoring

A complete, Docker-based system that **simulates** a production machine sensor, **streams** pulse data into InfluxDB, and displays **live analytics** on a Grafana dashboard.

---

## Architecture

```
┌─────────────────────┐       ┌──────────────┐       ┌──────────────┐
│  Pulse Simulator    │──────▶│   InfluxDB   │◀──────│   Grafana    │
│  (Python)           │ write │   (2.7)      │ query │   (10.4)     │
│                     │       │              │       │              │
│  Mimics sensor      │       │  Bucket:     │       │  Live dash   │
│  pulses from a      │       │  spring_data │       │  with 5s     │
│  production machine │       │              │       │  auto-refresh│
└─────────────────────┘       └──────────────┘       └──────────────┘
```

## Startup Guide

This section walks you through getting the project running on a fresh machine. It covers both a full local deployment (InfluxDB + Grafana + simulator) and a Grafana Cloud deployment (InfluxDB local + Grafana hosted).

Prerequisites (all platforms)
- Git
- Docker & Docker Compose (v2)
- Python 3.8+ (only required for auxiliary scripts, e.g., deployment and simulator build)

If you need platform-specific Docker install instructions, follow Docker's guides: https://docs.docker.com/get-docker/


1) Quick checkout

```bash
# clone the repo to your home or desired folder
git clone <your-repo-url> ~/IOT
cd ~/IOT
```


A. Local (all-in-one) — recommended for demos

This runs InfluxDB, Grafana (local), and the pulse simulator in Docker containers.

Steps:

```bash
# build and run full local stack (InfluxDB + Grafana + simulator)
docker compose up --build -d

# watch logs (optional)
docker compose logs -f
```

Verify services:
- Grafana: http://localhost:3001 — login: admin / admin
- InfluxDB: http://localhost:8086 — login: admin / admin12345

Quick checks:
```bash
# list containers and status
docker ps --format "{{.Names}}\t{{.Status}}"

# check InfluxDB health (returns JSON)
curl -s http://localhost:8086/health | jq .
```

Stopping and cleanup:
```bash
# stop
docker compose down
# stop and remove volumes / persistent data
docker compose down -v
```

Notes:
- If you change timing parameters, edit the environment variables in `docker-compose.yml`.
- Dashboard auto-refresh is set to 5s; give the simulator ~10s after startup to accumulate data.


B. Grafana Cloud (recommended for sharing with clients)

This mode keeps InfluxDB running locally but hosts Grafana on Grafana Cloud.

High-level steps:
1. Start only InfluxDB + simulator locally
2. Expose InfluxDB to the internet (secure tunnel)
3. Create a Grafana Cloud account + service account token
4. Run the deploy script to create the Flux datasource and import the dashboard

Commands:

```bash
# start only InfluxDB + simulator (no local Grafana)
docker compose -f docker-compose.cloud.yml up --build -d

# open a new terminal to create the tunnel
cd grafana-cloud
bash expose_influxdb.sh
# choose Cloudflare Tunnel (recommended) or ngrok — copy the public URL it prints
```

In Grafana Cloud (web UI):
- Create an Organization / Stack (if you haven't already)
- Go to Administration → Users and access → Service accounts → Add service account
  - Name: `iot-deploy` (or similar)
  - Role: Admin (required for automated import)
  - Add service account token → Copy the token (shown only once)

Then run the deploy script locally (in another terminal):

```bash
cd ~/IOT/grafana-cloud
bash deploy_to_grafana_cloud.sh
# follow prompts: provide your Grafana Cloud stack URL (https://<your-stack>.grafana.net), the service-account token, and the InfluxDB public URL from the tunnel
```

What the deploy script does:
- Creates/updates an InfluxDB (Flux) datasource in Grafana Cloud (using the token you provide)
- Imports the pre-built dashboard JSON with all 10 panels

Important caveats:
- Keep the tunnel running. If the tunnel stops, Grafana Cloud can't reach your local InfluxDB and panels will show "No data".
- Quick (free) Cloudflare tunnels rotate URLs; for a persistent setup consider creating a named Cloudflare Tunnel using a Cloudflare account or host InfluxDB on a public VM.
- For production, create a read-only InfluxDB token with limited permissions and use that in Grafana Cloud datasource instead of the admin token.


2) Common troubleshooting & tips

- "No data" in Grafana panels:
  - Ensure InfluxDB health endpoint returns status: pass
  - Confirm the tunnel URL is reachable from the machine running the deploy script (curl <tunnel-url>/health)
  - Check Grafana Cloud datasource health in the Grafana UI (Data sources → InfluxDB-Flux)

- Container errors:
  - `docker compose logs <service>` to inspect logs
  - Remove volumes if corrupted: `docker compose down -v` (data loss)

- When tunnel URL changes (Cloudflare quick tunnels or ngrok free):
  - Re-run `bash deploy_to_grafana_cloud.sh` with the new tunnel URL so Grafana Cloud datasource points to the correct address


3) Useful commands summary

```bash
# start full local stack
docker compose up --build -d

# start cloud-mode (InfluxDB + simulator only)
docker compose -f docker-compose.cloud.yml up -d

# stop
docker compose down

# show logs
docker compose logs -f

# check InfluxDB health
curl -s http://localhost:8086/health | jq .

# run cloud deploy script (after creating tunnel + service account token)
cd grafana-cloud
bash deploy_to_grafana_cloud.sh
```


4) Security recommendations

- Never commit secrets (tokens) into git. Use environment variables or secret stores.
- Create least-privilege InfluxDB tokens for Grafana (read-only) instead of admin tokens.
- Use TLS and firewall rules where possible; restrict access to the InfluxDB tunnel to Grafana Cloud IPs when using a persistent tunnel.


5) Further reading
- Grafana Cloud docs: https://grafana.com/docs/grafana-cloud/
- Cloudflare Tunnel: https://developers.cloudflare.com/cloudflare-one/connections/connect-apps/
- InfluxDB docs: https://docs.influxdata.com/influxdb/v2.7/


---

## Dashboard Panels

| Panel | Description |
|-------|-------------|
| **Live Pulse Stream** | Raw pulse arrivals as dots on a timeline |
| **Intra-Series Gap** | Bar chart of time gaps between pulses within each product (< 200 ms) |
| **Inter-Series Gap** | Bar chart of material feed gaps between products (≥ 200 ms) |
| **Avg Intra-Series Gap** | Stat: rolling average pulse gap over last 5 min |
| **Avg Inter-Series Gap** | Stat: rolling average material feed time over last 5 min |
| **Total Products Produced** | Count of products made in last 5 min |
| **Total Pulses** | Count of all pulses in last 5 min |
| **Pulses Per Product** | Bar chart showing how many pulses each product received |
| **Gap Distribution** | Line chart of all consecutive gaps — low = pulses, spikes = material feed |

---

## Configuration

All timing parameters are set via environment variables in `docker-compose.yml`:

| Variable | Default | Description |
|----------|---------|-------------|
| `INTRA_PULSE_GAP` | 0.05 s | Time between pulses within a product (50 ms) |
| `INTRA_PULSE_JITTER` | 0.008 s | Random jitter ± on pulse gap |
| `INTER_SERIES_GAP` | 0.5 s | Material feed time between products (500 ms) |
| `INTER_SERIES_JITTER` | 0.08 s | Random jitter ± on feed gap |
| `PULSES_PER_PRODUCT_MIN` | 8 | Minimum pulses per product |
| `PULSES_PER_PRODUCT_MAX` | 15 | Maximum pulses per product |

### Gap Threshold

The Grafana queries use **200 ms** as the threshold to separate intra-series from inter-series gaps. If you change the timing parameters significantly, update the `elapsed < 200` / `elapsed >= 200` filters in the dashboard queries.

---

## File Structure

```
IOT/
├── docker-compose.yml              # Full local stack (InfluxDB + Grafana + Simulator)
├── docker-compose.cloud.yml        # Cloud mode (InfluxDB + Simulator only, no local Grafana)
├── Dockerfile                      # Builds the Python simulator
├── requirements.txt                # Python dependencies
├── pulse_simulator.py              # Simulates production machine sensor
├── spring_pulse_analysis.sql       # Standalone InfluxDB SQL queries
├── README.md                       # This file
├── grafana-cloud/
│   ├── setup_guide.md              # Detailed Grafana Cloud setup guide
│   ├── expose_influxdb.sh          # Expose local InfluxDB via tunnel
│   └── deploy_to_grafana_cloud.sh  # Deploy datasource + dashboard to Grafana Cloud
└── grafana/
    ├── provisioning/
    │   ├── datasources/
    │   │   └── influxdb.yml        # Auto-configures InfluxDB datasource
    │   └── dashboards/
    │       └── dashboards.yml      # Tells Grafana where to find dashboards
    └── dashboards/
        └── spring_machine.json     # The pre-built dashboard
```

---

## ☁️ Grafana Cloud Deployment

Host your dashboard on **Grafana Cloud** instead of running Grafana locally.

### Quick Steps

```bash
# 1. Start only InfluxDB + Simulator (no local Grafana)
docker compose -f docker-compose.cloud.yml up --build -d

# 2. Expose InfluxDB to the internet (in a separate terminal)
cd grafana-cloud
bash expose_influxdb.sh

# 3. Deploy datasource + dashboard to Grafana Cloud (in another terminal)
cd grafana-cloud
bash deploy_to_grafana_cloud.sh
```

You'll need:
- A free **Grafana Cloud** account (https://grafana.com/products/cloud/)
- Your Grafana Cloud **Stack URL** (e.g. `https://myorg.grafana.net`)
- A **Service Account Token** with Admin role

See [`grafana-cloud/setup_guide.md`](grafana-cloud/setup_guide.md) for the detailed guide.

---

## Connecting a Real Sensor

To replace the simulator with a real sensor:

1. Remove or stop the `pulse_simulator` service in `docker-compose.yml`
2. From your sensor / edge device, write points to InfluxDB using the same schema:

```
Measurement: spring_pulses
Tags:        machine=machine_01
Fields:      pulse=1, turn_number=<int>, series_number=<int>
Timestamp:   nanosecond precision
```

You can use the InfluxDB line protocol, the HTTP API, or any InfluxDB client library (Python, C, Arduino, etc.).

---

## Troubleshooting

| Issue | Solution |
|-------|----------|
| Dashboard shows "No data" | Wait 10-15 seconds for data to accumulate, check time range is "Last 15 minutes" |
| Containers won't start | Run `docker compose logs` to see errors |
| InfluxDB health check fails | Ensure port 8086 is not already in use |
| Grafana can't connect to InfluxDB | Check that both containers are on the same Docker network (default with compose) |
