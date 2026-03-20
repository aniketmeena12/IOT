#!/usr/bin/env python3
"""
Printing Machine Pulse Simulator — Arduino M18 Compatible
==========================================================
Simulates an Arduino M18 inductive proximity sensor mounted on a printing
machine. Each sheet/product passing the sensor emits one NPN digital pulse.

Real M18 behaviour this simulator replicates:
  - Switching frequency up to 2000 Hz (min gap ~0.5 ms between pulses)
  - Typical printing machine: 1–10 sheets/sec (100–1000 ms per sheet)
  - A "job" = fixed number of sheets (one print run / batch)
  - Between jobs: machine pauses for operator to load next stack (~3–10 sec)
  - Occasional missed pulses (~1%) simulating sensor blind-spot or debounce
  - Occasional double-pulses (~0.5%) simulating mechanical vibration

Each pulse is written to InfluxDB with fields that support all four pipeline
goals: batch counting, cycle speed, dashboard, and alerting.

Measurement: print_pulses
Tags:
    machine         e.g. "printer_01"
    job_id          e.g. "job_00042"  (new id per print run)
    shift           "morning" / "afternoon" / "night"

Fields:
    pulse           int  = 1  (always; used for counting)
    sheet_number    int        sheet index within this job (1-based)
    job_number      int        global job counter since simulator start
    cycle_ms        float      ms since previous pulse (cycle time)
    sheets_per_min  float      instantaneous throughput
    job_total_so_far int       sheets printed in current job so far
    is_missed       int  0/1   1 = simulated missed pulse (gap event written)
    is_double       int  0/1   1 = simulated vibration double-pulse

Environment variables (with defaults):
    INFLUXDB_URL            http://localhost:8086
    INFLUXDB_TOKEN          my-super-secret-token
    INFLUXDB_ORG            spring_factory
    INFLUXDB_BUCKET         spring_data

    MACHINE_NAME            printer_01

    # Printing speed: sheets per second (M18 max is 2000 Hz; realistic: 1-10)
    SHEETS_PER_SEC_MIN      2.0       (min throughput during a job)
    SHEETS_PER_SEC_MAX      8.0       (max throughput during a job)
    SPEED_JITTER_PCT        5.0       (±% speed variation per sheet)

    # Job size: sheets per print run
    SHEETS_PER_JOB_MIN      50
    SHEETS_PER_JOB_MAX      200

    # Pause between jobs (operator loads next stack)
    JOB_PAUSE_SEC_MIN       3.0
    JOB_PAUSE_SEC_MAX       10.0

    # Fault simulation
    MISSED_PULSE_PROB       0.01      (1%  chance a sheet produces no pulse)
    DOUBLE_PULSE_PROB       0.005     (0.5% chance vibration causes 2 pulses)
"""

import os
import time
import random
import signal
import sys
from datetime import datetime, timezone

from influxdb_client import InfluxDBClient, Point, WritePrecision
from influxdb_client.client.write_api import SYNCHRONOUS

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------
INFLUXDB_URL    = os.getenv("INFLUXDB_URL",    "http://localhost:8086")
INFLUXDB_TOKEN  = os.getenv("INFLUXDB_TOKEN",  "my-super-secret-token")
INFLUXDB_ORG    = os.getenv("INFLUXDB_ORG",    "spring_factory")
INFLUXDB_BUCKET = os.getenv("INFLUXDB_BUCKET", "spring_data")

MACHINE_NAME = os.getenv("MACHINE_NAME", "printer_01")

SHEETS_PER_SEC_MIN  = float(os.getenv("SHEETS_PER_SEC_MIN", "2.0"))
SHEETS_PER_SEC_MAX  = float(os.getenv("SHEETS_PER_SEC_MAX", "8.0"))
SPEED_JITTER_PCT    = float(os.getenv("SPEED_JITTER_PCT",   "5.0"))

SHEETS_PER_JOB_MIN  = int(os.getenv("SHEETS_PER_JOB_MIN", "50"))
SHEETS_PER_JOB_MAX  = int(os.getenv("SHEETS_PER_JOB_MAX", "200"))

JOB_PAUSE_SEC_MIN   = float(os.getenv("JOB_PAUSE_SEC_MIN", "3.0"))
JOB_PAUSE_SEC_MAX   = float(os.getenv("JOB_PAUSE_SEC_MAX", "10.0"))

MISSED_PULSE_PROB   = float(os.getenv("MISSED_PULSE_PROB", "0.01"))
DOUBLE_PULSE_PROB   = float(os.getenv("DOUBLE_PULSE_PROB", "0.005"))

# ---------------------------------------------------------------------------
# Shift helper
# ---------------------------------------------------------------------------
def current_shift() -> str:
    hour = datetime.now().hour
    if 6 <= hour < 14:
        return "morning"
    elif 14 <= hour < 22:
        return "afternoon"
    return "night"

# ---------------------------------------------------------------------------
# Graceful shutdown
# ---------------------------------------------------------------------------
running = True

def _shutdown(signum, frame):
    global running
    print(f"\n[Simulator] Signal {signum} — shutting down gracefully...")
    running = False

signal.signal(signal.SIGINT,  _shutdown)
signal.signal(signal.SIGTERM, _shutdown)

# ---------------------------------------------------------------------------
# Write one pulse point to InfluxDB
# ---------------------------------------------------------------------------
def write_pulse(
    write_api,
    job_number: int,
    job_id: str,
    sheet_number: int,
    job_total_so_far: int,
    cycle_ms: float,
    sheets_per_min: float,
    is_missed: int = 0,
    is_double: int = 0,
):
    now = datetime.now(timezone.utc)
    point = (
        Point("print_pulses")
        .tag("machine", MACHINE_NAME)
        .tag("job_id", job_id)
        .tag("shift", current_shift())
        .field("pulse",           1)
        .field("sheet_number",    sheet_number)
        .field("job_number",      job_number)
        .field("cycle_ms",        round(cycle_ms, 3))
        .field("sheets_per_min",  round(sheets_per_min, 2))
        .field("job_total_so_far",job_total_so_far)
        .field("is_missed",       is_missed)
        .field("is_double",       is_double)
        .time(now, WritePrecision.NS)
    )
    write_api.write(bucket=INFLUXDB_BUCKET, record=point)

# ---------------------------------------------------------------------------
# Main loop
# ---------------------------------------------------------------------------
def main():
    print(f"[Simulator] M18 Printing Machine Pulse Simulator")
    print(f"[Simulator] Machine : {MACHINE_NAME}")
    print(f"[Simulator] Speed   : {SHEETS_PER_SEC_MIN}–{SHEETS_PER_SEC_MAX} sheets/sec")
    print(f"[Simulator] Job size: {SHEETS_PER_JOB_MIN}–{SHEETS_PER_JOB_MAX} sheets/job")
    print(f"[Simulator] Faults  : missed={MISSED_PULSE_PROB*100:.1f}%  double={DOUBLE_PULSE_PROB*100:.2f}%")
    print(f"[Simulator] InfluxDB: {INFLUXDB_URL}")

    client    = InfluxDBClient(url=INFLUXDB_URL, token=INFLUXDB_TOKEN, org=INFLUXDB_ORG)
    write_api = client.write_api(write_options=SYNCHRONOUS)

    # Wait for InfluxDB to be ready
    for attempt in range(15):
        try:
            health = client.health()
            if health.status == "pass":
                print("[Simulator] InfluxDB is healthy — starting simulation.")
                break
        except Exception as exc:
            print(f"[Simulator] Waiting for InfluxDB (attempt {attempt+1}/15): {exc}")
            time.sleep(2)
    else:
        print("[Simulator] WARNING: Could not confirm InfluxDB health — proceeding anyway.")

    job_number    = 0
    global_sheet  = 0          # total sheets ever written (for rate calcs)
    last_pulse_ts = time.perf_counter()

    while running:
        # ── New job ──────────────────────────────────────────────────────────
        job_number  += 1
        total_sheets = random.randint(SHEETS_PER_JOB_MIN, SHEETS_PER_JOB_MAX)
        job_id       = f"job_{job_number:05d}"

        # Pick a base speed for this job (stays roughly constant per job,
        # mimicking a fixed machine speed setting)
        base_speed_sps = random.uniform(SHEETS_PER_SEC_MIN, SHEETS_PER_SEC_MAX)
        base_gap_sec   = 1.0 / base_speed_sps

        print(f"\n[Simulator] ── Job #{job_number} ({job_id}) ──")
        print(f"[Simulator]    Sheets   : {total_sheets}")
        print(f"[Simulator]    Speed    : {base_speed_sps:.1f} sheets/sec  "
              f"({base_speed_sps*60:.0f}/min)")
        print(f"[Simulator]    Shift    : {current_shift()}")

        sheet_in_job = 0   # how many pulses written for this job so far

        for sheet_idx in range(total_sheets):
            if not running:
                break

            # ── Per-sheet jitter (±SPEED_JITTER_PCT %) ───────────────────
            jitter_factor = 1.0 + random.uniform(
                -SPEED_JITTER_PCT / 100.0,
                +SPEED_JITTER_PCT / 100.0
            )
            gap_sec = max(base_gap_sec * jitter_factor, 0.0005)  # M18 min: 0.5 ms

            # Sleep for this sheet's cycle time
            time.sleep(gap_sec)

            now_ts    = time.perf_counter()
            cycle_ms  = (now_ts - last_pulse_ts) * 1000.0
            last_pulse_ts = now_ts
            spm       = 60_000.0 / cycle_ms if cycle_ms > 0 else 0.0

            global_sheet += 1
            sheet_in_job += 1

            # ── Fault simulation ──────────────────────────────────────────
            # IMPORTANT: double-pulse is checked FIRST because
            # DOUBLE_PULSE_PROB < MISSED_PULSE_PROB — any value that would
            # trigger a double would also trigger a missed if checked second.

            # Double pulse: mechanical vibration causes two pulses per sheet
            if random.random() < DOUBLE_PULSE_PROB:
                print(f"[Simulator]    ⚠ Sheet {sheet_idx+1}: DOUBLE pulse simulated")
                # Write first pulse normally
                write_pulse(
                    write_api, job_number, job_id,
                    sheet_number     = sheet_idx + 1,
                    job_total_so_far = sheet_in_job,
                    cycle_ms         = cycle_ms,
                    sheets_per_min   = spm,
                    is_missed        = 0,
                    is_double        = 0,
                )
                # Write second pulse 2–5 ms later (vibration echo)
                echo_delay = random.uniform(0.002, 0.005)
                time.sleep(echo_delay)
                write_pulse(
                    write_api, job_number, job_id,
                    sheet_number     = sheet_idx + 1,   # same sheet number
                    job_total_so_far = sheet_in_job,
                    cycle_ms         = echo_delay * 1000,
                    sheets_per_min   = 60_000 / (echo_delay * 1000),
                    is_missed        = 0,
                    is_double        = 1,               # flag for downstream filter
                )
                last_pulse_ts = time.perf_counter()
                continue

            # Missed pulse: sensor blind-spot / debounce.
            # Sheet passes but no pulse written — downstream sees an
            # anomalously long cycle_ms gap on the next pulse.
            if random.random() < MISSED_PULSE_PROB:
                print(f"[Simulator]    ⚠ Sheet {sheet_idx+1}: MISSED pulse simulated")
                continue  # deliberately no write

            # Normal pulse
            write_pulse(
                write_api, job_number, job_id,
                sheet_number     = sheet_idx + 1,
                job_total_so_far = sheet_in_job,
                cycle_ms         = cycle_ms,
                sheets_per_min   = spm,
            )

        if not running:
            break

        # ── Job complete — operator pause ─────────────────────────────────
        pause_sec = random.uniform(JOB_PAUSE_SEC_MIN, JOB_PAUSE_SEC_MAX)
        print(f"[Simulator]    Job #{job_number} done — {sheet_in_job} pulses written")
        print(f"[Simulator]    Operator pause: {pause_sec:.1f} sec (loading next stack)")

        # Sleep in 100 ms chunks so Ctrl+C is responsive during long pauses
        pause_end = time.perf_counter() + pause_sec
        while running and time.perf_counter() < pause_end:
            time.sleep(0.1)

        last_pulse_ts = time.perf_counter()   # reset after pause

    write_api.close()
    client.close()
    print("[Simulator] Shutdown complete.")

# ---------------------------------------------------------------------------
if __name__ == "__main__":
    main()