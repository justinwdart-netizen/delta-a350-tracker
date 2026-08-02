"""
fetch_flights.py — runs inside the GitHub Action on a schedule.

UPDATED for Aviationstack Basic tier (10,000 requests/month), upgraded from
Free (100/month) on 1 Aug 2026.

WHAT CHANGED FROM THE FREE-TIER VERSION:
- No more rotation/sampling. The old version could only afford 3 flight
  numbers per run (2 "priority" + 1 rotating through the rest of the
  roster), which meant most of the fleet's flight numbers only got checked
  once every few weeks by chance. On Basic, we can afford to query the
  ENTIRE known roster every run.
- Quota math at 4 runs/day (every 6 hours): 56 flights x 4 runs x 30 days
  = 6,720 requests/month, comfortably under the 10,000 limit, leaving a
  ~3,280/month buffer for manual runs, backfills, or ad-hoc DL9xxx checks.
- rotation_state.json is no longer needed (nothing rotates anymore) but is
  left alone / ignored rather than deleted, in case you want to revert.
- Manual single-flight lookups (MANUAL_FLIGHT_NUMBER) still work exactly
  as before and don't count against the scheduled run's roster query.

WHY 4x/DAY AND NOT MORE: 6x/day would be 10,080/month — just over budget
with zero room for anything else. 4x/day (6,720/month) was chosen to leave
real margin rather than run right up against the limit.

NOTE ON flight_date: your paid tier may now support Aviationstack's
`flight_date` query parameter (confirmed blocked on free tier — see
Master Reference doc). This script does NOT use it yet, since it hasn't
been verified against your specific plan. If you confirm it works, targeted
historical/future-date queries could replace some of the "current instance
only" guessing this script still relies on. Test with one manual call
before wiring it in here.
"""

import json
import os
import urllib.request
import urllib.parse
from datetime import datetime, timezone

API_KEY = os.environ["AVIATIONSTACK_API_KEY"]

# Full known Delta A350 flight-number roster (from the FlightRadar24-derived
# rotation study). On the paid tier, EVERY number here is queried EVERY run.
FULL_ROSTER = [
    "DL7", "DL8", "DL11", "DL12", "DL26", "DL27", "DL38", "DL39", "DL40", "DL41",
    "DL68", "DL69", "DL70", "DL71", "DL82", "DL83", "DL88", "DL89", "DL95", "DL96",
    "DL120", "DL121", "DL132", "DL133", "DL136", "DL137", "DL146", "DL147",
    "DL158", "DL159", "DL160", "DL161", "DL166", "DL167", "DL170", "DL171",
    "DL172", "DL173", "DL188", "DL189", "DL196", "DL197", "DL200", "DL201",
    "DL210", "DL211", "DL274", "DL275", "DL280", "DL281", "DL290", "DL291",
    "DL327", "DL388", "DL389", "DL763",
]
# Note: DL9xxx repositioning/recovery numbers (DL9890, DL9912, DL9968,
# DL9969, DL9970) are DELIBERATELY excluded from the automated roster — this
# is a design choice, not a quota constraint. See conversation history: these
# are relocation/supplementary flights, tracked manually when relevant, not
# swept automatically.

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

    if manual_flight:
        # Manual on-demand lookup: query exactly this one flight. Doesn't
        # touch the scheduled roster run at all.
        batch = [manual_flight]
        mode = "manual"
    else:
        # Full roster, every scheduled run. No rotation, no sampling.
        batch = FULL_ROSTER
        mode = "full_roster"

    fetch_ts = datetime.now(timezone.utc).isoformat()
    latest_results = []
    n_requests = 0

    for flight_iata in batch:
        try:
            result = fetch_flight(flight_iata)
            records = result.get("data", [])
            n_requests += 1
        except Exception as e:
            records = []
            n_requests += 1  # the attempt still cost a request even if it failed
            print(f"WARNING: fetch failed for {flight_iata}: {e}")

        for rec in records:
            rec["_fetch_timestamp_utc"] = fetch_ts
            rec["_fetch_flight_iata_queried"] = flight_iata
            rec["_fetch_mode"] = mode
            latest_results.append(rec)
            with open(HISTORY_PATH, "a") as f:
                f.write(json.dumps(rec) + "\n")

    with open(LATEST_PATH, "w") as f:
        json.dump({
            "fetch_timestamp_utc": fetch_ts,
            "fetch_mode": mode,
            "flight_numbers_queried": batch,
            "records": latest_results,
        }, f, indent=2)

    log_quota_usage(mode, n_requests, fetch_ts)

    print(f"[{mode}] Queried {len(batch)} flight numbers, got {len(latest_results)} records, "
          f"used {n_requests} requests, appended to {HISTORY_PATH}")


if __name__ == "__main__":
    main()
