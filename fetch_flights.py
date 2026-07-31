"""
fetch_flights.py — runs inside the GitHub Action on a schedule.

Queries a small, quota-conscious set of flight numbers each run and appends
results to data/flights_history.jsonl (one JSON record per line, never
overwritten, so history accumulates over time) and overwrites
data/latest.json with just this run's results for convenience.

QUOTA MATH: free Aviationstack tier = 100 requests/month. If this runs daily
(30 runs/month), keep FLIGHT_NUMBERS_PER_RUN small enough that
FLIGHT_NUMBERS_PER_RUN * 30 stays under 100 — 3 per run (90/month) is a safe
default. Adjust ROTATION below to control which flights get checked and how
often; it cycles through the full known roster a few at a time so the whole
fleet gets sampled over time instead of ignoring most of it forever.
"""

import json
import os
import urllib.request
import urllib.parse
from datetime import datetime, timezone

API_KEY = os.environ["AVIATIONSTACK_API_KEY"]

# Full known Delta A350 flight-number roster (from the FlightRadar24-derived
# rotation study). The rotation below cycles through this a few at a time.
FULL_ROSTER = [
    "DL7", "DL8", "DL11", "DL12", "DL26", "DL27", "DL38", "DL39", "DL40", "DL41",
    "DL68", "DL69", "DL70", "DL71", "DL82", "DL83", "DL88", "DL89", "DL95", "DL96",
    "DL120", "DL121", "DL132", "DL133", "DL136", "DL137", "DL146", "DL147",
    "DL158", "DL159", "DL160", "DL161", "DL166", "DL167", "DL170", "DL171",
    "DL172", "DL173", "DL188", "DL189", "DL196", "DL197", "DL200", "DL201",
    "DL210", "DL211", "DL274", "DL275", "DL280", "DL281", "DL290", "DL291",
    "DL327", "DL388", "DL389", "DL763",
]

FLIGHT_NUMBERS_PER_RUN = 3  # keep small — see quota math in the docstring above

DATA_DIR = "data"
HISTORY_PATH = os.path.join(DATA_DIR, "flights_history.jsonl")
LATEST_PATH = os.path.join(DATA_DIR, "latest.json")
STATE_PATH = os.path.join(DATA_DIR, "rotation_state.json")


def load_rotation_index():
    if os.path.exists(STATE_PATH):
        with open(STATE_PATH) as f:
            return json.load(f).get("next_index", 0)
    return 0


def save_rotation_index(idx):
    with open(STATE_PATH, "w") as f:
        json.dump({"next_index": idx}, f)


def fetch_flight(flight_iata):
    params = urllib.parse.urlencode({
        "access_key": API_KEY,
        "flight_iata": flight_iata,
    })
    url = f"https://api.aviationstack.com/v1/flights?{params}"
    with urllib.request.urlopen(url, timeout=30) as resp:
        return json.loads(resp.read().decode())


def main():
    os.makedirs(DATA_DIR, exist_ok=True)

    start = load_rotation_index()
    batch = [FULL_ROSTER[(start + i) % len(FULL_ROSTER)] for i in range(FLIGHT_NUMBERS_PER_RUN)]
    save_rotation_index((start + FLIGHT_NUMBERS_PER_RUN) % len(FULL_ROSTER))

    fetch_ts = datetime.now(timezone.utc).isoformat()
    latest_results = []

    for flight_iata in batch:
        try:
            result = fetch_flight(flight_iata)
            records = result.get("data", [])
        except Exception as e:
            records = []
            print(f"WARNING: fetch failed for {flight_iata}: {e}")

        for rec in records:
            rec["_fetch_timestamp_utc"] = fetch_ts
            rec["_fetch_flight_iata_queried"] = flight_iata
            latest_results.append(rec)
            with open(HISTORY_PATH, "a") as f:
                f.write(json.dumps(rec) + "\n")

    with open(LATEST_PATH, "w") as f:
        json.dump({
            "fetch_timestamp_utc": fetch_ts,
            "flight_numbers_queried": batch,
            "records": latest_results,
        }, f, indent=2)

    print(f"Queried {batch}, got {len(latest_results)} records, appended to {HISTORY_PATH}")


if __name__ == "__main__":
    main()
