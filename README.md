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

---

## How It Works

### System Overview

The IoT system monitors production machine operations by analyzing pulse data from industrial sensors. Here's what happens under the hood:

1. **Pulse Generation** — A sensor mounted on the machine (or simulated) emits pulses as products pass through
2. **Data Collection** — Each pulse is written to InfluxDB with metadata: machine ID, series number, timestamp
3. **Gap Analysis** — Gaps between consecutive pulses reveal process information:
   - **Small gaps (< 200 ms)**: pulses within a single product (*intra-series*)
   - **Large gaps (≥ 200 ms)**: material feed time between products (*inter-series*)
4. **Real-Time Dashboard** — Grafana queries InfluxDB every 5 seconds to display:
   - Live pulse stream
   - Pulse distribution and cycle speeds
   - Gap statistics for process optimization
   - Product count and throughput metrics

### Pulse Timing Logic

```
Timeline of a production run:

Product A pulses:           ▪ ▪ ▪ ▪ ▪ ▪ ▪ ▪  (50 ms gaps, ± jitter)
                            [~400 ms total]

Material feed gap:          ════════════════════ (500 ms)

Product B pulses:                            ▪ ▪ ▪ ▪ ▪ ▪ ▪  (50 ms gaps)
                                            [~350 ms total]

                    Gap < 200 ms = intra-series (part of same product)
                    Gap ≥ 200 ms = inter-series (new product)
```

**Key Parameters** (configurable via `docker-compose.yml`):

| Parameter | Role |
|-----------|------|
| `INTRA_PULSE_GAP` | Base time between pulses within a product (typical: 50 ms) |
| `INTRA_PULSE_JITTER` | Random ± variation to add realism (typical: ±8 ms) |
| `INTER_SERIES_GAP` | Material feed time between products (typical: 500 ms) |
| `INTER_SERIES_JITTER` | Random ± variation on feed time (typical: ±80 ms) |
| `PULSES_PER_PRODUCT_MIN/MAX` | How many sensor hits per product (typical: 8–15) |

### InfluxDB Data Schema

Every pulse is stored in InfluxDB with this structure:

```
Measurement: spring_pulses
├─ Tags (indexed, used for filtering):
│  ├─ machine       — e.g., "machine_01", "m18_printer_01"
│  ├─ series        — batch/product number (e.g., "1", "2", "3")
│  └─ shift         — "morning", "afternoon", "night"
│
└─ Fields (numeric data):
   ├─ pulse              — always 1 (used for counting events)
   ├─ turn_number        — pulse index within the product (1-based)
   ├─ series_number      — which product/series this pulse belongs to
   ├─ cycle_ms           — milliseconds since previous pulse
   ├─ sheets_per_min     — instantaneous throughput (for M18 simulator)
   └─ job_total_so_far   — cumulative pulses in current job

Timestamp: nanosecond precision (UTC)
```

### Gap Detection Algorithm (in Grafana)

The dashboard uses Flux queries to detect and analyze gaps:

```flux
// Detect gaps between consecutive pulses
|> calculateElapsed()      // elapsed = current_time - previous_time
|> filter(fn: (r) => r.elapsed > 0)

// Separate intra-series from inter-series
// Gap threshold = 200 ms (configurable)
|> map(fn: (r) => ({
    r with 
    gap_type = if r.elapsed < 200 then "intra" else "inter"
  }))

// Calculate rolling averages (last 5 minutes)
|> aggregateWindow(every: 5m, fn: mean)
```

---

## Available Simulators

### 1. **pulse_simulator.py** (Default — Spring Production Machine)

**Purpose**: Simulates a generic spring production machine with realistic pulse patterns.

**Features**:
- Configurable product batches with random pulse counts (8–15 pulses/product)
- Realistic jitter on pulse timing and material feed gaps
- Simulates operational pauses between batches
- Writes to InfluxDB with `series_number` and `turn_number` tracking

**Start Locally**:
```bash
docker compose up --build -d
```

**Configuration**:
```yaml
INTRA_PULSE_GAP: "0.05"      # 50 ms between pulses
INTRA_PULSE_JITTER: "0.008"  # ±8 ms jitter
INTER_SERIES_GAP: "0.5"      # 500 ms between products
INTER_SERIES_JITTER: "0.08"  # ±80 ms jitter
PULSES_PER_PRODUCT_MIN: "8"
PULSES_PER_PRODUCT_MAX: "15"
```

---

### 2. **pulse_simulator_m18.py** (ARINO M18 Printer)

**Purpose**: Simulates an ARINO M18 inductive proximity sensor on a printing machine.

**Features**:
- Models a real M18 NPN digital pulse sensor (up to 2000 Hz switching)
- Printing machine behavior: 1–10 sheets/sec, jobs of 50–200 sheets
- Realistic fault injection: missed pulses (~1%), double pulses from vibration (~0.5%)
- Per-sheet cycle time tracking and throughput calculation
- Operator pause simulation between print jobs

**Start Locally** (standalone):
```bash
python3 pulse_simulator_m18.py
```

**Configuration**:
```bash
export SHEETS_PER_SEC_MIN=2.0         # min throughput
export SHEETS_PER_SEC_MAX=8.0         # max throughput
export SPEED_JITTER_PCT=5.0           # ±% speed variation
export SHEETS_PER_JOB_MIN=50
export SHEETS_PER_JOB_MAX=200
export JOB_PAUSE_SEC_MIN=3.0          # pause between jobs
export JOB_PAUSE_SEC_MAX=10.0
export MISSED_PULSE_PROB=0.01         # 1% missed pulses
export DOUBLE_PULSE_PROB=0.005        # 0.5% double pulses
```

**Data Fields**:
- `sheet_number` — index within job (1-based)
- `job_number` — global job counter
- `cycle_ms` — milliseconds since previous pulse
- `sheets_per_min` — calculated throughput
- `job_total_so_far` — cumulative pulses in current job

---

### 3. **pulse_simulator_new.py** (Enhanced Generic Simulator)

**Purpose**: Extended pulse simulator with additional industrial scenarios.

**Features**:
- Multiple machine types (stamping, assembly, packaging)
- Advanced fault modes (sensor dropout, double pulses, pattern disruption)
- Batch/campaign tracking (multiple products per batch)
- InfluxDB connection with automatic retry logic

**Start Locally** (standalone):
```bash
python3 pulse_simulator_new.py
```

---

## Smart Heating Controller System

The `heating_simulation.py` module provides a **separate IoT system** for monitoring corrugated manufacturing machine heaters with **fault detection and failsafe logic**.

### Features

#### ✅ Three Sensor Operating Modes

| Mode | Behavior | Use Case |
|------|----------|----------|
| **SIMULATION** | No real sensor registered → full physics model runs → normal operation | Testing, development |
| **REAL** | Real sensor reads successfully → use live data → PID controls heater | Production with working sensor |
| **FAULT** | Real sensor fails → 🚨 alarm raised → heater emergency OFF → PID suspended | Production with sensor failure handling |

#### ⚠️ Fault Handling (Failsafe Design)

When a real sensor fails:
1. **Immediate**: Heater set to 0% (emergency off — no silent overheating)
2. **Alarm raised**: Logged with timestamp, fault count, and description
3. **PID suspended**: No control attempted on bad data
4. **Auto-retry**: Sensor re-tested every 30 steps
5. **Recovery**: When sensor works again, alarm clears and PID resumes

This prevents dangerous silent failures where the system silently substitutes simulated data while a real sensor is broken.

### Supported Real Sensors

#### Modbus TCP (Siemens S7, Allen-Bradley, Schneider)
```python
ModbusTCPSensor(
    host="192.168.1.10",    # PLC IP
    port=502,               # standard Modbus TCP port
    register=100,           # holding register address
    unit_id=1,
    scale=0.1               # raw_value * scale = temperature
)
```

#### Modbus RTU over RS485 (Eurotherm, RKC, Autonics)
```python
ModbusRTUSensor(
    port="/dev/ttyUSB0",    # serial port
    slave_id=1,             # Modbus device address
    register=0,             # register number
    baudrate=9600,
    scale=0.1
)
```

#### 4–20 mA Analog via ADS1115 ADC (Raspberry Pi)
```python
AnalogSensor_4_20mA(
    channel=0,              # ADC channel
    eng_min=80.0,           # engineering value at 4 mA
    eng_max=200.0           # engineering value at 20 mA
)
```

#### OPC-UA (Siemens WinCC, Ignition, Wonderware)
```python
OPCUASensor(
    url="opc.tcp://192.168.1.20:4840",
    node_id="ns=2;i=1001"   # OPC-UA node identifier
)
```

#### Serial ASCII (Eurotherm, RKC Controllers)
```python
SerialSensor(
    port="/dev/ttyUSB0",
    baudrate=9600,
    command="T\r"           # command to request temperature
)
```

#### MQTT Broker (Node-RED, AWS IoT)
```python
MQTTSensor(
    broker="192.168.1.5",
    topic="factory/zone1/temp",
    port=1883
)
```

#### CSV Replay (Test with Historical Data)
```python
CSVReplaySensor(
    filepath="plant_data.csv",
    column="temperature"
)
```

### Example: Heating Controller Setup

```python
from heating_simulation import (
    SmartSensorManager, SmartHeatingController,
    SimulatedTempSensor, ModbusTCPSensor
)

# Create sensor manager
manager = SmartSensorManager()

# Register temperature sensor
# 1st arg: simulated (fallback) | 2nd arg: real sensor (optional)
manager.register("temp",
    SimulatedTempSensor(initial_temp=30.0),
    real_sensor=ModbusTCPSensor(host="192.168.1.10", register=100)
)

# Create controller
controller = SmartHeatingController(
    manager=manager,
    base_target=140.0,  # 140°C setpoint
    idle_mode=False
)

# Run simulation
controller.run(max_steps=1000, step_delay=1.0)
```

### PID Control Logic

The embedded PID controller:
- **Auto-suspends** when sensor is in FAULT state (fails safely)
- **Anti-windup integral clamping** to prevent integrator saturation
- **Derivative filtering** to reduce noise sensitivity
- **Output clamping** to 0–100% heater power range

### InfluxDB Integration

The heater controller writes one point per step:

```
Measurement: smart_heater
├─ Tags:
│  ├─ machine         — e.g., "smart_heater_01"
│  ├─ sensor_mode     — "SIMULATION" | "REAL" | "FAULT"
│  └─ state           — "idle", "heating", "fault"
│
└─ Fields:
   ├─ setpoint        — target temperature (°C)
   ├─ measured_temp   — actual temperature (°C)
   ├─ heater_power    — output 0–100 (%)
   ├─ pid_error       — setpoint - measured (°C)
   ├─ humidity        — ambient humidity (%)
   ├─ ambient_temp    — ambient temperature (°C)
   └─ fault_count     — cumulative sensor faults (count)
```

Grafana can alert on `state = "fault"` to notify operators of sensor issues.

---

## Data Schema Details

### Spring Pulses (Pulse Monitoring)

```
Measurement: spring_pulses
Retention: Infinite (or configure in InfluxDB)
```

**Query Example** (Flux):
```flux
from(bucket: "spring_data")
  |> range(start: -5m)
  |> filter(fn: (r) => r._measurement == "spring_pulses")
  |> filter(fn: (r) => r.machine == "machine_01")
  |> stats.count()
```

---

### Smart Heat Data (Heating Controller)

```
Measurement: smart_heater
Retention: Infinite (or configure in InfluxDB)
```

**Alert Example** (Grafana):
```
Alert when: state == "fault" for 1 minute
Action: Send Slack notification → alert #ops-pagerduty
```

---

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
├── docker-compose.yml                           # Full local stack (InfluxDB + Grafana + Simulator)
├── docker-compose.cloud.yml                     # Cloud mode (InfluxDB + Simulator only, no local Grafana)
├── Dockerfile                                   # Builds the Python simulator
├── requirements.txt                             # Python dependencies
│
├── PULSE SIMULATORS
├── pulse_simulator.py                           # Default: generic spring production machine
├── pulse_simulator_m18.py                       # ARINO M18 printing machine with fault injection
├── pulse_simulator_new.py                       # Enhanced simulator with advanced fault modes
│
├── HEATING SYSTEM
├── heating_simulation.py                        # Smart heater controller + multi-sensor support
│                                                # Supports: Modbus TCP/RTU, 4-20mA, OPC-UA, Serial, MQTT
│
├── UTILITIES
├── generate_ppt.py                              # PowerPoint report generator from InfluxDB data
├── spring_pulse_analysis.sql                    # Standalone InfluxDB SQL queries for analysis
│
├── README.md                                    # This file
│
├── grafana-cloud/
│   ├── setup_guide.md                           # Detailed Grafana Cloud deployment guide
│   ├── expose_influxdb.sh                       # Expose local InfluxDB via Cloudflare Tunnel / ngrok
│   ├── deploy_to_grafana_cloud.sh               # Deploy datasource + dashboard to Grafana Cloud
│   └── deploy_now.py                            # Alternative Python deployment script
│
└── grafana/
    ├── provisioning/
    │   ├── datasources/
    │   │   └── influxdb.yml                     # Auto-configures InfluxDB datasource (local mode)
    │   └── dashboards/
    │       └── dashboards.yml                   # Tells Grafana where to find dashboard JSON files
    │
    └── dashboards/
        ├── spring_machine.json                  # Pre-built pulse monitoring dashboard (10 panels)
        └── heating_simulation.json              # Pre-built heater controller dashboard (live monitoring)
```

### File Details

| File | Purpose |
|------|---------|
| `pulse_simulator.py` | Main simulator — spring machine with configurable pulses, gaps, jitter |
| `pulse_simulator_m18.py` | M18 printer sensor emulation — sheets/sec throughput, job batches, realistic faults |
| `pulse_simulator_new.py` | Extended simulator — multiple machines, advanced fault injection, campaign tracking |
| `heating_simulation.py` | Heater controller with PID, three-mode sensor logic, real sensor support (Modbus/OPC-UA/MQTT/Serial) |
| `generate_ppt.py` | Generates PowerPoint reports with charts/metrics from InfluxDB historical data |
| `spring_pulse_analysis.sql` | SQL analysis queries for ad-hoc data exploration in InfluxDB |
| `docker-compose.yml` | Orchestrates InfluxDB v2.7, Grafana v10.4, and primary pulse simulator container |
| `docker-compose.cloud.yml` | Cloud-optimized stack (InfluxDB + simulator only, no local Grafana) for Grafana Cloud hosting |
| `Dockerfile` | Python runtime for running simulators in containers |
| `requirements.txt` | Python dependencies: InfluxDB client, pymodbus, opcua, paho-mqtt, pandas, etc. |

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

To replace the simulator with a real production machine sensor, you have two options:

### Option A: Stop the Simulator, Start Your Own Producer

**Steps**:

1. **Update `docker-compose.yml`** — comment out the `pulse_simulator` service:
```yaml
# pulse_simulator:
#   build: .
#   depends_on:
#     - influxdb
#   environment:
#     ...
```

2. **Run only InfluxDB**:
```bash
docker compose up influxdb grafana -d
```

3. **From your sensor device / edge computer**, write pulses to InfluxDB using **any method**:
   - **InfluxDB Line Protocol** (TCP port 8086)
   - **HTTP REST API** (easiest for IoT devices)
   - **InfluxDB client library** (Python, Node.js, Go, C, Java, Arduino)
   - **MQTT bridge** (subscribe + write to InfluxDB)

### Data Schema for Real Sensors

Every pulse must have this exact structure in InfluxDB:

```
Measurement: spring_pulses
Tags (indexed):
  machine           (string)  e.g., "machine_01", "conveyor_line_2"
  series            (string)  batch/product ID (e.g., "1001", "batch_A")
  shift             (string)  "morning" | "afternoon" | "night"

Fields (numeric):
  pulse             (int)     always = 1 (used for event counting)
  turn_number       (int)     pulse index within product (1-based)
  series_number     (int)     product/series sequence number
  cycle_ms          (float)   milliseconds since previous pulse
  
Timestamp: nanosecond precision (UTC)
```

### Implementation Examples

#### Python (InfluxDB Client Library)

```python
from influxdb_client import InfluxDBClient, Point, WritePrecision
from datetime import datetime, timezone

# Connect to InfluxDB
client = InfluxDBClient(
    url="http://localhost:8086",
    token="my-super-secret-token",
    org="spring_factory"
)
write_api = client.write_api(write_options=SYNCHRONOUS)

# Write a pulse event
pulse_point = (
    Point("spring_pulses")
    .tag("machine", "machine_01")
    .tag("series", "100")
    .tag("shift", "morning")
    .field("pulse", 1)
    .field("turn_number", 5)
    .field("series_number", 100)
    .field("cycle_ms", 52.3)
    .time(datetime.now(timezone.utc), WritePrecision.NS)
)

write_api.write(bucket="spring_data", record=pulse_point)
```

#### HTTP REST API (cURL / Bash)

```bash
# Set environment
export INFLUX_URL="http://localhost:8086"
export INFLUX_TOKEN="my-super-secret-token"
export INFLUX_ORG="spring_factory"
export INFLUX_BUCKET="spring_data"

# Write using line protocol
curl -X POST "${INFLUX_URL}/api/v1/write" \
  -H "Authorization: Token ${INFLUX_TOKEN}" \
  -d "spring_pulses,machine=machine_01,series=100,shift=morning \
      pulse=1i,turn_number=5i,series_number=100i,cycle_ms=52.3 1709539200000000000"
```

#### Node.js (InfluxDB Client)

```javascript
const { InfluxDB, Point } = require('@influxdata/influxdb-client');

const client = new InfluxDB({
  url: 'http://localhost:8086',
  token: 'my-super-secret-token',
  org: 'spring_factory',
});

const writeApi = client.getWriteApi('spring_factory', 'spring_data', 'ns');

const point = new Point('spring_pulses')
  .tag('machine', 'machine_01')
  .tag('series', '100')
  .tag('shift', 'morning')
  .intField('pulse', 1)
  .intField('turn_number', 5)
  .intField('series_number', 100)
  .floatField('cycle_ms', 52.3)
  .timestamp(Date.now() * 1e6); // nanoseconds

writeApi.writePoint(point);
writeApi.close();
```

#### Arduino / Embedded C

```cpp
#include <WiFi.h>
#include <HTTPClient.h>

// When pulse detected on pin GPIO5
void onPulseDetected() {
  static int pulse_count = 0;
  static unsigned long last_pulse_ms = millis();
  
  unsigned long now = millis();
  float cycle_ms = (now - last_pulse_ms);
  pulse_count++;
  
  // Format as InfluxDB line protocol
  String line_protocol = String("spring_pulses,machine=esp32_sensor,series=") 
    + String(pulse_count / 10)
    + ",shift=morning pulse=1i,turn_number=" + String(pulse_count)
    + "i,series_number=" + String(pulse_count / 10)
    + "i,cycle_ms=" + String(cycle_ms) + "\n";
  
  // Send to InfluxDB
  HTTPClient http;
  http.begin("http://192.168.1.50:8086/api/v1/write?org=spring_factory&bucket=spring_data");
  http.addHeader("Authorization", "Token my-super-secret-token");
  http.POST(line_protocol);
  http.end();
  
  last_pulse_ms = now;
}

void setup() {
  attachInterrupt(digitalPinToInterrupt(5), onPulseDetected, RISING);
}
```

#### MQTT Bridge (Node-RED / Generic Broker)

If your sensor publishes to MQTT, bridge it to InfluxDB:

```bash
# Using mosquitto + telegraf
# 1. Subscribe to MQTT sensor topic
# 2. Transform to InfluxDB line protocol
# 3. Write to InfluxDB

# telegraf.conf
[[inputs.mqtt_consumer]]
  servers = ["mqtt://localhost:1883"]
  topics = ["factory/machine_01/pulse"]

[[outputs.influxdb_v2]]
  urls = ["http://localhost:8086"]
  token = "my-super-secret-token"
  organization = "spring_factory"
  bucket = "spring_data"
```

### Verify Real Sensor Data

Once your sensor is writing to InfluxDB:

```bash
# Query recent pulses from your machine
influx query 'from(bucket:"spring_data") 
  |> range(start:-1h) 
  |> filter(fn:(r) => r._measurement == "spring_pulses" and r.machine == "machine_01")
  |> tail(n:10)'

# Check Grafana dashboard updates
# → http://localhost:3001 → Live Pulse Stream panel should show data
```

### Security Best Practices for Real Sensors

1. **Use read-only tokens** for sensors writing to specific buckets:
```bash
influx auth create \
  --org spring_factory \
  --user sensor_01 \
  --write-bucket spring_data
```

2. **Use TLS/HTTPS** if sensors are remote:
```python
client = InfluxDBClient(
    url="https://influx.example.com:8086",  # HTTPS
    token="...",
    verify_ssl=True
)
```

3. **Never hardcode tokens** — use environment variables:
```bash
export INFLUXDB_TOKEN="$(cat /run/secrets/influx_token)"
python3 my_sensor.py
```

---

## Troubleshooting

| Issue | Solution |
|-------|----------|
| Dashboard shows "No data" | Wait 10-15 seconds for data to accumulate, check time range is "Last 15 minutes" |
| Containers won't start | Run `docker compose logs` to see errors |
| InfluxDB health check fails | Ensure port 8086 is not already in use |
| Grafana can't connect to InfluxDB | Check that both containers are on the same Docker network (default with compose) |

---

## Use Cases & Operational Patterns

### 1. Production Line Monitoring

**Scenario**: Monitor spring production line for anomalies

```bash
# Configuration in docker-compose.yml
PULSES_PER_PRODUCT_MIN: "10"
PULSES_PER_PRODUCT_MAX: "15"
INTRA_PULSE_GAP: "0.045"              # 45 ms (tighter tolerance)
INTER_SERIES_GAP: "0.3"               # 300 ms (faster feed)

# Monitor in Grafana:
# - Watch "Pulses Per Product" for consistency (variance signals mechanical issue)
# - Alert if "Avg Intra-Series Gap" > 60 ms (indicating slower production)
# - Alert if products < 200 pulses/hour (line stoppage)
```

**Alert Rules** (example Grafana alert):
```yaml
Alert: "Product Quality Deviation"
Condition: stddev(pulses_per_product, 5m) > 3
Action: Email ops@company.com
```

### 2. Printing Machine Performance Analysis (M18)

**Scenario**: Track printer throughput, fault patterns, batch efficiency

```bash
# Start M18 simulator
python3 pulse_simulator_m18.py

# Grafana dashboards show:
# - Sheets per minute (throughput) — real-time
# - Job completion time distribution — historical
# - Missed pulse rate (~1%) — fault detection
# - Double pulse rate (~0.5%) — mechanical vibration detection

# Query: Average sheets per job in morning vs. night shift
from(bucket:"spring_data")
  |> range(start:-7d)
  |> filter(fn:(r) => r._measurement == "spring_pulses" and r.machine == "m18_printer_01")
  |> group(by:["shift"])
  |> aggregateWindow(every:1d, fn:mean, createEmpty:false)
```

### 3. Heater Controller Monitoring (Manufacturing)

**Scenario**: Monitor corrugated machine heater with sensor fault detection

```python
from heating_simulation import SmartSensorManager, SmartHeatingController
from heating_simulation import ModbusTCPSensor, SimulatedTempSensor

# Setup: Use real Modbus sensor from PLC, fallback to simulation
manager = SmartSensorManager()
manager.register("zone_1_temp",
    SimulatedTempSensor(initial_temp=30.0),
    real_sensor=ModbusTCPSensor(host="192.168.1.100", register=100)
)

# Run controller
controller = SmartHeatingController(manager, base_target=160.0)
controller.run(max_steps=0)  # infinite

# Grafana alerts:
# - Alert if state = "FAULT" for > 5 min (sensor issue)
# - Alert if measured_temp < setpoint - 20°C (heater malfunction)
# - Alert if heater_power pinned at 100% for > 10 min (runaway)
```

**Safety Guarantees**:
- If sensor fails → heater automatically OFF (0%)
- No silent data substitution → operator notified immediately
- Auto-retry every 30 steps (30 seconds default)

### 4. Multi-Machine Factory Tracking

**Scenario**: Monitor 5 production lines in one facility

```yaml
# Deploy on each machine's local computer:
MACHINE_NAME: "line_1"              # line_1, line_2, ..., line_5
INFLUXDB_URL: "http://central-influx.local:8086"

# Central Grafana aggregates:
query: |
  from(bucket:"spring_data")
    |> range(start:-24h)
    |> group(by:["machine"])
    |> aggregateWindow(every:1h, fn:sum)  # hourly production per line

# Dashboard shows:
# - Production totals by line
# - Efficiency comparison (pulses/hour)
# - Downtime detection (zero pulses for > 30 min)
```

### 5. Historical Analysis & Reporting

**Generate hourly production reports**:
```bash
python3 generate_ppt.py \
  --start "2024-03-20 00:00:00" \
  --end "2024-03-20 23:59:59" \
  --machine "machine_01" \
  --output "production_report_2024-03-20.pptx"
```

**Output includes**:
- Total products produced
- Average cycle time
- Efficiency metrics
- Gap distribution charts
- Hourly throughput trends

---

## Performance Tuning

### For High-Throughput Sensors (1000+ pulses/sec)

```yaml
# Increase InfluxDB batch size
INFLUXDB_BATCH_SIZE: "10000"
INFLUXDB_FLUSH_INTERVAL: "5s"

# Reduce Grafana query range (costs more CPU)
# In dashboard: set time range to "Last 1 hour" instead of "Last 24 hours"
```

### For Low-Bandwidth Environments

```yaml
# Batch multiple pulses in a single write
# (Modify pulse_simulator.py to buffer writes)
WRITE_BATCH_COUNT: "100"        # flush every 100 pulses or 10 seconds
WRITE_FLUSH_INTERVAL_SEC: "10"
```

### Storage Optimization

```bash
# Downsampling policy in InfluxDB: keep raw data 7 days, hourly avg 90 days
influx bucket-schema update --name spring_data --retention=7d

# Retention policy (clean old data)
influx bucket update --name spring_data \
  --retention 2160h          # 90 days
```

---

## Advanced: Custom Metrics

### Add Business Logic on Top of Pulse Data

**Example: Detect production anomalies with ML**

```python
import pandas as pd
from influxdb_client import InfluxDBClient

# Query pulse data
client = InfluxDBClient(url="http://localhost:8086", token="...", org="...")
query = '''
from(bucket:"spring_data")
  |> range(start:-1h)
  |> filter(fn:(r) => r._measurement == "spring_pulses")
  |> pivot(rowKey:["_time"], columnKey:["_field"], valueColumn:"_value")
'''
df = client.query_api().query_data_frame(query)

# Detect anomalies (e.g., cycle time > mean + 3*std)
mean_cycle = df['cycle_ms'].mean()
std_cycle = df['cycle_ms'].std()
anomalies = df[df['cycle_ms'] > mean_cycle + 3*std_cycle]

# Write anomaly flag back to InfluxDB
for idx, anomaly in anomalies.iterrows():
    write_api.write(bucket="spring_data",
        record=Point("anomaly_detection")
            .tag("machine", "machine_01")
            .field("anomaly_score", (anomaly['cycle_ms'] - mean_cycle) / std_cycle)
    )
```

### Integration with External Systems

**Send alerts to Slack**:
```python
import requests

def send_slack_alert(message, channel="#production"):
    requests.post(SLACK_WEBHOOK_URL, json={
        "channel": channel,
        "text": f"🚨 IoT Alert: {message}"
    })

# Trigger from Grafana contact point or custom monitoring script
```

**Send to MQTT for downstream processing**:
```python
import paho.mqtt.client as mqtt

client = mqtt.Client()
client.connect("localhost", 1883, 60)
client.publish("factory/alerts", "Product quality deviation detected")
```
