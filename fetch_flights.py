"""
fetch_flights.py — runs inside the GitHub Action on a schedule.

Queries the FULL known Delta A350 flight-number roster on every run and
appends results to data/flights_history.jsonl (one JSON record per line,
never overwritten, so history accumulates over time), and overwrites
data/latest.json with just this run's results for convenience.

QUOTA MATH (updated for the $49.99/mo "Basic" Aviationstack plan —
10,000 requests/month, confirmed via account dashboard):
  56 known flight numbers x 1 run/day x 30 days  = 1,680 requests/month (17% of quota)
  56 known flight numbers x 2 runs/day x 30 days = 3,360 requests/month (34% of quota)
Even running the workflow manually several extra times a day leaves a wide
safety margin. This replaces the old free-tier throttled rotation (3/run,
2 priority + 1 rotating), which under-covered the roster badly: most flight
numbers only got re-checked once every ~54 days, so a leg seen as
"scheduled" would often never get re-queried to see whether it had actually
departed. Querying everything every run fixes that directly.

MANUAL_FLIGHT_NUMBER modes (set via the workflow_dispatch input):
  - blank  -> full roster sweep (the default now — see quota math above)
  - a single flight number -> e.g. "DL8", queries just that one flight,
    useful for a quick spot-check without waiting on the full sweep
"""

import json
import os
import time
import urllib.request
import urllib.parse
from datetime import datetime, timezone

API_KEY = os.environ["AVIATIONSTACK_API_KEY"]

# Full known Delta A350 flight-number roster (from the FlightRadar24-derived
# rotation study). Every run queries all of these now — see quota math above.
FULL_ROSTER = [
    "DL7", "DL8", "DL11", "DL12", "DL26", "DL27", "DL38", "DL39", "DL40", "DL41",
    "DL68", "DL69", "DL70", "DL71", "DL82", "DL83", "DL88", "DL89", "DL95", "DL96",
    "DL120", "DL121", "DL132", "DL133", "DL136", "DL137", "DL146", "DL147",
    "DL158", "DL159", "DL160", "DL161", "DL166", "DL167", "DL170", "DL171",
    "DL172", "DL173", "DL188", "DL189", "DL196", "DL197", "DL200", "DL201",
    "DL210", "DL211", "DL274", "DL275", "DL280", "DL281", "DL290", "DL291",
    "DL327", "DL388", "DL389", "DL763",
]

DATA_DIR = "data"
HISTORY_PATH = os.path.join(DATA_DIR, "flights_history.jsonl")
LATEST_PATH = os.path.join(DATA_DIR, "latest.json")


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

    manual_flight = os.environ.get("MANUAL_FLIGHT_NUMBER", "").strip().upper()

    if manual_flight in ("", "ALL"):
        # Default: full roster sweep every run — see quota math in the docstring.
        # "ALL" is accepted as an explicit alias for the same behavior, since
        # that was the documented full-sweep trigger before this plan upgrade
        # and the workflow's own UI text still says so.
        batch = FULL_ROSTER
        mode = "full_sweep"
    else:
        # Single-flight spot-check: query just this one, don't touch anything else.
        batch = [manual_flight]
        mode = "manual"

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
            rec["_fetch_mode"] = mode
            latest_results.append(rec)
            with open(HISTORY_PATH, "a") as f:
                f.write(json.dumps(rec) + "\n")

        if mode == "full_sweep":
            time.sleep(0.3)  # light pacing across a 56-request sweep

    with open(LATEST_PATH, "w") as f:
        json.dump({
            "fetch_timestamp_utc": fetch_ts,
            "fetch_mode": mode,
            "flight_numbers_queried": batch,
            "records": latest_results,
        }, f, indent=2)

    print(f"[{mode}] Queried {len(batch)} flight numbers, got {len(latest_results)} records, "
          f"appended to {HISTORY_PATH}")


if __name__ == "__main__":
    main()
