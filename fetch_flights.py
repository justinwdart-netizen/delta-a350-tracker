"""
fetch_flights.py — runs inside the GitHub Action on a schedule.

UPDATED to add DL295 (ATL->HND) and DL294 (HND->ATL) to the tracked roster.
These were the only two HND-touching flight numbers missing from the full
HND inbound/outbound contribution matrix -- every other relevant flight
(DL167, DL166, DL121, DL120, DL7, DL8, DL274, DL275, DL290, DL291, DL41,
DL40) was already covered. Adding these two means the DL295/DL294 columns
in the Chain Tally, and the HND Contribution Matrix tab, will start
accumulating real automated samples instead of relying entirely on manual
FR24 backfill (which is capped at whatever ~7-day window is visible at
check time and can never recover history that's already scrolled past).

DEDUP ON WRITE: a separate investigation (see repo history / prior session)
found the original version of this script had no deduplication, and
Aviationstack's free/no-flight_date query returns the same ~80 historical
instances per flight number on every call regardless of what's new --
without dedup, every run re-appended all of it forever, which grew
flights_history.jsonl to exactly GitHub's 100MB file-size limit and started
hard-failing every push. This version hashes each record's core content
(ignoring the _fetch_* metadata fields, which differ every run even for an
identical underlying flight record) against what's already on disk and
only appends genuinely new content.

Quota math on Basic tier (10,000 requests/month), full roster now 58
flights (56 + DL295 + DL294), 4 runs/day: 58 x 4 x 30 = 6,960/month --
still comfortably under budget.
"""

import json
import os
import hashlib
import urllib.request
import urllib.parse
from datetime import datetime, timezone

API_KEY = os.environ["AVIATIONSTACK_API_KEY"]

# Full known Delta A350 flight-number roster. DL295 and DL294 added to close
# the HND inbound/outbound contribution-matrix gap -- every other
# HND-touching flight was already covered.
FULL_ROSTER = [
    "DL7", "DL8", "DL11", "DL12", "DL26", "DL27", "DL38", "DL39", "DL40", "DL41",
    "DL68", "DL69", "DL70", "DL71", "DL82", "DL83", "DL88", "DL89", "DL95", "DL96",
    "DL120", "DL121", "DL132", "DL133", "DL136", "DL137", "DL146", "DL147",
    "DL158", "DL159", "DL160", "DL161", "DL166", "DL167", "DL170", "DL171",
    "DL172", "DL173", "DL188", "DL189", "DL196", "DL197", "DL200", "DL201",
    "DL210", "DL211", "DL274", "DL275", "DL280", "DL281", "DL290", "DL291",
    "DL294", "DL295",  # NEW -- ATL<->HND connection, needed for the HND contribution matrix
    "DL327", "DL388", "DL389", "DL763",
]
# Note: DL9xxx repositioning/recovery numbers remain DELIBERATELY excluded.
# This is a design choice (relocation/supplementary flights, tracked
# manually when relevant), not a quota constraint -- do not add them here.

DATA_DIR = "data"
HISTORY_PATH = os.path.join(DATA_DIR, "flights_history.jsonl")
LATEST_PATH = os.path.join(DATA_DIR, "latest.json")
QUOTA_LOG_PATH = os.path.join(DATA_DIR, "quota_usage_log.jsonl")

# Fields that legitimately vary between fetches of the SAME underlying
# record and should be excluded from the dedup hash.
_METADATA_FIELDS = {"_fetch_timestamp_utc", "_fetch_flight_iata_queried", "_fetch_mode"}


def record_hash(rec):
    """Stable hash of a record's actual flight content, ignoring per-fetch
    metadata, so re-fetching an unchanged record doesn't create a
    'new' duplicate line."""
    core = {k: v for k, v in rec.items() if k not in _METADATA_FIELDS}
    blob = json.dumps(core, sort_keys=True, default=str)
    return hashlib.sha256(blob.encode()).hexdigest()


def load_existing_hashes():
    """Read the current history file once and hash every existing record,
    so new writes can skip anything already present."""
    hashes = set()
    if not os.path.exists(HISTORY_PATH):
        return hashes
    with open(HISTORY_PATH) as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
            except json.JSONDecodeError:
                continue
            hashes.add(record_hash(rec))
    return hashes


def fetch_flight(flight_iata):
    params = urllib.parse.urlencode({
        "access_key": API_KEY,
        "flight_iata": flight_iata,
    })
    url = f"https://api.aviationstack.com/v1/flights?{params}"
    with urllib.request.urlopen(url, timeout=30) as resp:
        return json.loads(resp.read().decode())


def log_quota_usage(mode, n_requests, fetch_ts):
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
        batch = [manual_flight]
        mode = "manual"
    else:
        batch = FULL_ROSTER
        mode = "full_roster"

    fetch_ts = datetime.now(timezone.utc).isoformat()
    latest_results = []
    n_requests = 0
    n_new = 0
    n_skipped_dupe = 0

    existing_hashes = load_existing_hashes()

    with open(HISTORY_PATH, "a") as history_file:
        for flight_iata in batch:
            try:
                result = fetch_flight(flight_iata)
                records = result.get("data", [])
                n_requests += 1
            except Exception as e:
                records = []
                n_requests += 1
                print(f"WARNING: fetch failed for {flight_iata}: {e}")

            for rec in records:
                rec["_fetch_timestamp_utc"] = fetch_ts
                rec["_fetch_flight_iata_queried"] = flight_iata
                rec["_fetch_mode"] = mode
                latest_results.append(rec)

                h = record_hash(rec)
                if h in existing_hashes:
                    n_skipped_dupe += 1
                    continue
                existing_hashes.add(h)
                n_new += 1
                history_file.write(json.dumps(rec) + "\n")

    with open(LATEST_PATH, "w") as f:
        json.dump({
            "fetch_timestamp_utc": fetch_ts,
            "fetch_mode": mode,
            "flight_numbers_queried": batch,
            "records": latest_results,
        }, f, indent=2)

    log_quota_usage(mode, n_requests, fetch_ts)

    print(f"[{mode}] Queried {len(batch)} flight numbers, used {n_requests} requests, "
          f"got {len(latest_results)} records ({n_new} new, {n_skipped_dupe} duplicates skipped), "
          f"appended to {HISTORY_PATH}")


if __name__ == "__main__":
    main()
