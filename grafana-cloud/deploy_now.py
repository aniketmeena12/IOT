#!/usr/bin/env python3
"""Deploy datasource + dashboard to Grafana Cloud — non-interactive."""
import json
import sys
import urllib.request
import urllib.error
import ssl

GRAFANA_URL = "https://fe055024.grafana.net"
API_KEY = "glsa_YXXSW0BwMpmYEgZX6xxWaMnKZo9u3MLT_dd2abd32"
INFLUXDB_URL = "https://gmbh-flags-fuel-use.trycloudflare.com"
DASHBOARD_PATH = "/home/ashok/IOT/grafana/dashboards/spring_machine.json"

ctx = ssl.create_default_context()

def api(method, endpoint, data=None):
    url = f"{GRAFANA_URL}/api{endpoint}"
    headers = {
        "Authorization": f"Bearer {API_KEY}",
        "Content-Type": "application/json",
        "User-Agent": "grafana-deploy-script/1.0",
        "Accept": "application/json",
    }
    body = json.dumps(data).encode() if data else None
    req = urllib.request.Request(url, data=body, headers=headers, method=method)
    try:
        resp = urllib.request.urlopen(req, context=ctx, timeout=15)
        return resp.status, json.loads(resp.read().decode())
    except urllib.error.HTTPError as e:
        raw = e.read().decode()
        try:
            return e.code, json.loads(raw)
        except json.JSONDecodeError:
            return e.code, {"raw": raw}
    except Exception as e:
        return 0, {"error": str(e)}

# ── Step 1: Test connection ──────────────────────────────────────────────
print("=" * 60)
print("[1/4] Testing Grafana Cloud connection...")
code, body = api("GET", "/org")
if code != 200:
    print(f"  ✗ Failed (HTTP {code}): {body}")
    sys.exit(1)
print(f"  ✓ Connected to org: {body.get('name', 'unknown')}")

# ── Step 2: Create/Update datasource ────────────────────────────────────
print("\n[2/4] Creating InfluxDB datasource...")
ds_payload = {
    "name": "InfluxDB-Flux",
    "type": "influxdb",
    "access": "proxy",
    "url": INFLUXDB_URL,
    "jsonData": {
        "version": "Flux",
        "organization": "spring_factory",
        "defaultBucket": "spring_data",
        "tlsSkipVerify": True,
    },
    "secureJsonData": {
        "token": "my-super-secret-token",
    },
    "isDefault": True,
    "editable": True,
}

# Check if exists
code, body = api("GET", "/datasources/name/InfluxDB-Flux")
if code == 200:
    ds_id = body["id"]
    ds_uid = body["uid"]
    print(f"  Datasource already exists (id={ds_id}), updating...")
    code2, body2 = api("PUT", f"/datasources/{ds_id}", ds_payload)
    if code2 == 200:
        ds_uid = body2.get("datasource", {}).get("uid", ds_uid)
        print(f"  ✓ Datasource updated (uid: {ds_uid})")
    else:
        print(f"  ✗ Update failed (HTTP {code2}): {body2}")
        sys.exit(1)
else:
    code2, body2 = api("POST", "/datasources", ds_payload)
    if code2 == 200:
        ds_uid = body2.get("datasource", {}).get("uid", "")
        print(f"  ✓ Datasource created (uid: {ds_uid})")
    else:
        print(f"  ✗ Create failed (HTTP {code2}): {body2}")
        sys.exit(1)

# Re-fetch to get the uid for sure
code, body = api("GET", "/datasources/name/InfluxDB-Flux")
if code == 200:
    ds_uid = body["uid"]
print(f"  Datasource UID: {ds_uid}")

# ── Step 3: Test datasource connectivity ─────────────────────────────────
print("\n[3/4] Testing datasource connectivity...")
code, body = api("GET", f"/datasources/uid/{ds_uid}/health")
print(f"  Health check: HTTP {code} → {body}")

# ── Step 4: Import dashboard ────────────────────────────────────────────
print("\n[4/4] Importing dashboard...")
with open(DASHBOARD_PATH) as f:
    dash = json.load(f)

# Prepare for import
dash.pop("id", None)
dash["version"] = None

# Patch all datasource UIDs
def patch_ds(obj):
    if isinstance(obj, dict):
        if "datasource" in obj and isinstance(obj["datasource"], dict):
            if obj["datasource"].get("uid", "") == "":
                obj["datasource"]["uid"] = ds_uid
        for v in obj.values():
            patch_ds(v)
    elif isinstance(obj, list):
        for item in obj:
            patch_ds(item)

patch_ds(dash)

import_payload = {
    "dashboard": dash,
    "overwrite": True,
    "message": "Deployed via deploy script",
}

code, body = api("POST", "/dashboards/db", import_payload)
if code == 200:
    dash_url = body.get("url", "")
    print(f"  ✓ Dashboard imported!")
    print()
    print("=" * 60)
    print("  🎉 DEPLOYMENT COMPLETE!")
    print("=" * 60)
    print()
    print(f"  Dashboard URL:")
    print(f"  {GRAFANA_URL}{dash_url}?refresh=5s")
    print()
    print(f"  InfluxDB tunnel: {INFLUXDB_URL}")
    print(f"  ⚠ Keep the tunnel running!")
    print()
else:
    print(f"  ✗ Import failed (HTTP {code}): {json.dumps(body, indent=2)}")
    sys.exit(1)
