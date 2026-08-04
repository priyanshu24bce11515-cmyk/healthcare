"""Simulated wearable: posts vitals to the ingestion API with realistic daily
rhythms and occasional out-of-range spikes (docs/BLUEPRINT.md Part 6.A).

Usage:
    python scripts/device_simulator.py --patient-id 1 --base-url http://localhost:7071/api
    python scripts/device_simulator.py --patient-id 1 --base-url http://localhost:7071/api --once
"""
import argparse
import base64
import json
import random
import time
from datetime import datetime, timezone

import requests

SPIKE_CHANCE = 0.08


def _principal_header(patient_id: int) -> str:
    """Same x-demo-principal shape the frontend sends (web/src/lib/auth.tsx) —
    ingest_vitals now requires an authenticated patient/provider principal."""
    payload = {"userId": f"demo-patient-{patient_id}", "userRoles": ["patient"], "patientId": patient_id}
    return base64.b64encode(json.dumps(payload).encode()).decode()


def _reading(vital_type: str, spike: bool) -> tuple[float, str]:
    if vital_type == "heartRate":
        return (round(random.uniform(125, 150), 1) if spike else round(random.uniform(62, 82), 1)), "bpm"
    if vital_type == "steps":
        return random.randint(0, 400), "count"  # per-tick increment, not a daily total
    if vital_type == "sleep":
        return round(random.uniform(4.0, 5.5) if spike else random.uniform(6.2, 8.2), 1), "hours"
    if vital_type == "bp_systolic":
        return (random.randint(145, 160) if spike else random.randint(112, 128)), "mmHg"
    if vital_type == "bp_diastolic":
        return (random.randint(92, 100) if spike else random.randint(72, 84)), "mmHg"
    if vital_type == "glucose":
        return (random.randint(185, 220) if spike else random.randint(88, 118)), "mg/dL"
    raise ValueError(vital_type)


def post_vital(base_url: str, patient_id: int, vital_type: str, spike: bool) -> None:
    value, unit = _reading(vital_type, spike)
    payload = {
        "patientId": patient_id,
        "type": vital_type,
        "value": value,
        "unit": unit,
        "recordedAt": datetime.now(timezone.utc).isoformat(),
        "source": "device-simulator",
    }
    headers = {"x-demo-principal": _principal_header(patient_id)}
    resp = requests.post(f"{base_url}/vitals", json=payload, headers=headers, timeout=10)
    tag = "SPIKE" if spike else "normal"
    print(f"[{tag}] {vital_type}={value}{unit} -> HTTP {resp.status_code}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Simulated wearable device feed")
    parser.add_argument("--patient-id", type=int, required=True)
    parser.add_argument("--base-url", default="http://localhost:7071/api")
    parser.add_argument("--interval", type=float, default=15.0, help="Seconds between ticks")
    parser.add_argument("--once", action="store_true", help="Send one reading of each vital type and exit")
    args = parser.parse_args()

    vital_types = ["heartRate", "steps", "sleep", "bp_systolic", "bp_diastolic", "glucose"]

    if args.once:
        for vt in vital_types:
            post_vital(args.base_url, args.patient_id, vt, spike=False)
        return

    print(f"Streaming simulated vitals for patient {args.patient_id} to {args.base_url} every {args.interval}s. Ctrl+C to stop.")
    try:
        while True:
            vt = random.choice(vital_types)
            spike = random.random() < SPIKE_CHANCE
            post_vital(args.base_url, args.patient_id, vt, spike)
            time.sleep(args.interval)
    except KeyboardInterrupt:
        print("\nStopped.")


if __name__ == "__main__":
    main()
