#!/usr/bin/env python3
"""
Product Machine Pulse Simulator
================================
Simulates a production machine sensor that emits one pulse per operation cycle.

* Pulses within a series (one product) are closely spaced (~50 ms ± jitter).
* Between series (material feed for next product) there is a larger gap (~500 ms ± jitter).
* Each pulse is written to InfluxDB as a point in the "spring_pulses" measurement.

Environment variables (with defaults):
    INFLUXDB_URL            http://localhost:8086
    INFLUXDB_TOKEN          my-super-secret-token
    INFLUXDB_ORG            spring_factory
    INFLUXDB_BUCKET         spring_data
    INTRA_PULSE_GAP         0.05        (seconds)
    INTRA_PULSE_JITTER      0.008       (seconds)
    INTER_SERIES_GAP        0.5         (seconds)
    INTER_SERIES_JITTER     0.08        (seconds)
    PULSES_PER_PRODUCT_MIN  8
    PULSES_PER_PRODUCT_MAX  15
"""

import os
import time
import random
import signal
import sys
from datetime import datetime, timezone

from influxdb_client import InfluxDBClient, Point, WritePrecision
from influxdb_client.client.write_api import ASYNCHRONOUS

# ---------------------------------------------------------------------------
# Configuration from environment
# ---------------------------------------------------------------------------
INFLUXDB_URL   = os.getenv("INFLUXDB_URL",   "http://localhost:8086")
INFLUXDB_TOKEN = os.getenv("INFLUXDB_TOKEN", "my-super-secret-token")
INFLUXDB_ORG   = os.getenv("INFLUXDB_ORG",   "spring_factory")
INFLUXDB_BUCKET = os.getenv("INFLUXDB_BUCKET", "spring_data")

INTRA_PULSE_GAP      = float(os.getenv("INTRA_PULSE_GAP",      "0.05"))
INTRA_PULSE_JITTER   = float(os.getenv("INTRA_PULSE_JITTER",   "0.008"))
INTER_SERIES_GAP     = float(os.getenv("INTER_SERIES_GAP",     "0.5"))
INTER_SERIES_JITTER  = float(os.getenv("INTER_SERIES_JITTER",  "0.08"))
PULSES_PER_PRODUCT_MIN = int(os.getenv("PULSES_PER_PRODUCT_MIN", "8"))
PULSES_PER_PRODUCT_MAX = int(os.getenv("PULSES_PER_PRODUCT_MAX", "15"))

# ---------------------------------------------------------------------------
# Graceful shutdown
# ---------------------------------------------------------------------------
running = True

def _shutdown(signum, frame):
    global running
    print(f"\n[Simulator] Received signal {signum}, shutting down...")
    running = False

signal.signal(signal.SIGINT,  _shutdown)
signal.signal(signal.SIGTERM, _shutdown)

# ---------------------------------------------------------------------------
# Main loop
# ---------------------------------------------------------------------------
def main():
    print("[Simulator] Connecting to InfluxDB at", INFLUXDB_URL)
    client = InfluxDBClient(url=INFLUXDB_URL, token=INFLUXDB_TOKEN, org=INFLUXDB_ORG)
    write_api = client.write_api(write_options=ASYNCHRONOUS)

    # Wait briefly for InfluxDB to be fully ready
    for attempt in range(10):
        try:
            health = client.health()
            if health.status == "pass":
                print("[Simulator] InfluxDB is healthy.")
                break
        except Exception as e:
            print(f"[Simulator] Waiting for InfluxDB... ({e})")
            time.sleep(2)
    else:
        print("[Simulator] WARNING: Could not confirm InfluxDB health, proceeding anyway.")

    series_count = 0

    while running:
        series_count += 1
        num_pulses = random.randint(PULSES_PER_PRODUCT_MIN, PULSES_PER_PRODUCT_MAX)
        print(f"[Simulator] Product #{series_count}: {num_pulses} pulses")

        for pulse_idx in range(num_pulses):
            if not running:
                break

            # Write a pulse point
            now = datetime.now(timezone.utc)
            point = (
                Point("spring_pulses")
                .tag("machine", "machine_01")
                .tag("series", str(series_count))
                .field("pulse", 1)
                .field("turn_number", pulse_idx + 1)
                .field("series_number", series_count)
                .time(now, WritePrecision.NS)
            )
            write_api.write(bucket=INFLUXDB_BUCKET, record=point)

            # Intra-series delay (between pulses of the same product)
            if pulse_idx < num_pulses - 1:
                gap = INTRA_PULSE_GAP + random.uniform(-INTRA_PULSE_JITTER, INTRA_PULSE_JITTER)
                gap = max(gap, 0.005)  # never negative or near-zero
                time.sleep(gap)

        if not running:
            break

        # Inter-series delay (material feed for next product)
        inter_gap = INTER_SERIES_GAP + random.uniform(-INTER_SERIES_JITTER, INTER_SERIES_JITTER)
        inter_gap = max(inter_gap, 0.1)
        print(f"[Simulator] Material feed pause: {inter_gap*1000:.1f} ms")
        time.sleep(inter_gap)

    write_api.close()
    client.close()
    print("[Simulator] Shutdown complete.")


if __name__ == "__main__":
    main()
