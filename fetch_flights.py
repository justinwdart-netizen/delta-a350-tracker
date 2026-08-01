"""
fetch_flights.py — runs inside the GitHub Action on a schedule.

Queries a small, quota-conscious set of flight numbers each run and appends
results to data/flights_history.jsonl (one JSON record per line, never
overwritten, so history accumulates over time) and overwrites
data/latest.json with just this run's results for convenience.

PRIORITY FLIGHTS: DL120 and DL275 are the user's most-flown routes and are
checked on EVERY automatic run, guaranteed. Only the remaining slot(s) rotate
through the rest of the known roster, so priority coverage is near-daily
while the rest of the fleet is still sampled over time, just more slowly.

QUOTA MATH: free Aviationstack tier = 100 requests/month. If this runs daily
(30 runs/month), keep FLIGHT_NUMBERS_PER_RUN small enough that
FLIGHT_NUMBERS_PER_RUN * 30 stays under 100 — 3 per run (90/month) is a safe
default: 2 priority + 1 rotating slot per run.

MANUAL_FLIGHT_NUMBER modes (set via the workflow_dispatch input):
  - blank              -> normal rotation (2 priority + 1 rotating), the daily default
  - a single number     -> e.g. "DL8", queries just that one flight
  - "ALL"                -> queries every flight number in FULL_ROSTER (56 requests
                            in one run). Costs over half the FREE-TIER MONTHLY QUOTA
                            in a single run — use deliberately, not routinely. Does
                            not touch rotation_state.json, so it never disturbs the
                            next scheduled rotation run.
"""

import json
import os
import time
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

# PRIORITY FLIGHTS: always checked on every automatic (non-manual) run, since
# these are the ones the user actually flies and cares about most. Only the
# remaining slots rotate through the rest of the roster below.
PRIORITY_FLIGHTS = ["DL120", "DL275"]

# Rotation pool excludes the priority flights so they aren't queried twice in
# the same run (they're already guaranteed a slot above).
ROTATION_POOL = [f for f in FULL_ROSTER if f not in PRIORITY_FLIGHTS]

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

    manual_flight = os.environ.get("MANUAL_FLIGHT_NUMBER", "").strip().upper()

    if manual_flight == "ALL":
        # Full-fleet sweep: every known flight number, one run. Deliberately
        # NOT the default — costs 56 requests, over half the free-tier
        # monthly quota, in a single run. Rotation state is left untouched
        # so the next scheduled/rotation run picks up exactly where it would
        # have anyway.
        batch = FULL_ROSTER
        mode = "manual_all"
        print(f"ALL mode: querying the full {len(batch)}-flight roster "
              f"({len(batch)} Aviationstack requests this run). This is a "
              f"quota-heavy, deliberate action, not the routine default.")
    elif manual_flight:
        # Manual on-demand lookup: query exactly this one flight, don't touch
        # the rotation state at all (so it doesn't skip/duplicate the next
        # scheduled batch).
        batch = [manual_flight]
        mode = "manual"
    else:
        remaining_slots = FLIGHT_NUMBERS_PER_RUN - len(PRIORITY_FLIGHTS)
        start = load_rotation_index()
        rotating_batch = [ROTATION_POOL[(start + i) % len(ROTATION_POOL)] for i in range(max(remaining_slots, 0))]
        if remaining_slots > 0:
            save_rotation_index((start + remaining_slots) % len(ROTATION_POOL))
        batch = PRIORITY_FLIGHTS + rotating_batch
        mode = "rotation"

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

        if mode == "manual_all":
            time.sleep(0.3)  # light pacing across a 56-request sweep, not needed for small batches

    with open(LATEST_PATH, "w") as f:
        json.dump({
            "fetch_timestamp_utc": fetch_ts,
            "fetch_mode": mode,
            "flight_numbers_queried": batch,
            "records": latest_results,
        }, f, indent=2)

    print(f"[{mode}] Queried {batch}, got {len(latest_results)} records, appended to {HISTORY_PATH}")


if __name__ == "__main__":
    main()
