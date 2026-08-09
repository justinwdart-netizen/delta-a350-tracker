"""
fetch_flights.py — runs inside the GitHub Action on a schedule.

CONSOLIDATED VERSION — merges work done across three separate chats on this
same repo, so nothing gets silently overwritten by pushing one chat's
version over another's:
  - Full-roster sweep + Basic-tier quota math (this chat)
  - quota_usage_log.jsonl request tracking (DL120-predictor chat)
  - blank OR "ALL" both trigger the full sweep (daily-route-tracking-by-tail chat)

Queries the FULL known Delta A350 flight-number roster every run and appends
results to data/flights_history.jsonl (one JSON record per line, never
overwritten, so history accumulates over time), overwrites data/latest.json
with just this run's results for convenience, and appends a small usage
record to data/quota_usage_log.jsonl so you can track actual monthly usage.

QUOTA MATH (Aviationstack Basic tier: 10,000 requests/month):
  Full roster = 56 flight numbers = 56 requests/run.
  4x/day: 56 * 4 * 30 = 6,720/month. 2x/day: 3,360/month. 1x/day: 1,680/month.
  All comfortably under the 10,000 cap -- adjust the workflow cron to your
  preferred frequency without quota risk.

NOTE ON DL9xxx (repositioning/relocation flights): intentionally excluded
from FULL_ROSTER. These are tracked manually when relevant (see Master
Reference doc), not swept automatically -- this was a deliberate design
choice, not an oversight, confirmed across chats.

ROTATION-RETIRED: the old PRIORITY_FLIGHTS / ROTATION_POOL / rotation_state.json
free-tier throttling (3 flights/run) is retired now that we're on Basic tier.
rotation_state.json is left in the repo but unused -- harmless to delete.
"""

import json
import os
import urllib.request
import urllib.parse
from datetime import datetime, timezone

API_KEY = os.environ["AVIATIONSTACK_API_KEY"]

# Full known Delta A350 flight-number roster (from the FlightRadar24-derived
# rotation study). Queried in full every run now that Basic-tier quota makes
# that cheap (see QUOTA MATH above). Deliberately excludes DL9xxx
# repositioning numbers -- see note above.
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
QUOTA_LOG_PATH = os.path.join(DATA_DIR, "quota_usage_log.jsonl")


def fetch_flight(flight_iata):
    params = urllib.parse.urlencode({
        "access_key": API_KEY,
        "flight_iata": flight_iata,
    })
    url = f"https://api.aviationstack.com/v1/flights?{params}"
    with urllib.request.urlopen(url, timeout=30) as resp:
        return json.loads(resp.read().decode())


def log_quota_usage(mode, n_requests, fetch_ts):
    """Append a small record of how many requests this run used, so you can
    track actual monthly usage against the 10,000 budget over time."""
    os.makedirs(DATA_DIR, exist_ok=True)
    with open(QUOTA_LOG_PATH, "a") as f:
        f.write(json.dumps({
            "fetch_timestamp_utc": fetch_ts,
            "mode": mode,
            "requests_used": n_requests,
        }) + "\n")


def main():
    os.makedirs(DATA_DIR, exist_ok=True)

    manual_flight = os.environ.get("MANUAL_FLIGHT_NUMBER", "").strip().upper()

    if manual_flight and manual_flight != "ALL":
        # Manual on-demand lookup: query exactly this one flight.
        batch = [manual_flight]
        mode = "manual"
    else:
        # Blank OR "ALL" both trigger the full-roster sweep.
        batch = FULL_ROSTER
        mode = "full_roster"

    fetch_ts = datetime.now(timezone.utc).isoformat()
    latest_results = []
    failures = []

    for flight_iata in batch:
        try:
            result = fetch_flight(flight_iata)
            records = result.get("data", [])
        except Exception as e:
            records = []
            failures.append(flight_iata)
            print(f"WARNING: fetch failed for {flight_iata}: {e}")

        for rec in records:
            rec["_fetch_timestamp_utc"] = fetch_ts
            rec["_fetch_flight_iata_queried"] = flight_iata
            rec["_fetch_mode"] = mode
            latest_results.append(rec)
            with open(HISTORY_PATH, "a") as f:
                f.write(json.dumps(rec) + "\n")

    log_quota_usage(mode, len(batch), fetch_ts)

    with open(LATEST_PATH, "w") as f:
        json.dump({
            "fetch_timestamp_utc": fetch_ts,
            "fetch_mode": mode,
            "flight_numbers_queried": batch,
            "records": latest_results,
            "failures": failures,
        }, f, indent=2)

    print(f"[{mode}] Queried {len(batch)} flight numbers, got {len(latest_results)} records, "
          f"{len(failures)} failures, appended to {HISTORY_PATH}; "
          f"{len(batch)} requests logged to {QUOTA_LOG_PATH}")


if __name__ == "__main__":
    main()
