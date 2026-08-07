"""
fetch_flights.py — runs inside the GitHub Action on a schedule.

Queries the FULL known Delta A350 flight-number roster every run and appends
results to data/flights_history.jsonl (one JSON record per line, never
overwritten, so history accumulates over time) and overwrites
data/latest.json with just this run's results for convenience.

QUOTA MATH (Aviationstack Basic tier: 10,000 requests/month):
  Full roster = 56 flight numbers = 56 requests/run.
  Even running this 4x/day: 56 * 4 * 30 = 6,720/month — comfortably under
  the 10,000 cap, with headroom for manual/on-demand runs too.
  (Previously throttled to 3/run under the free tier's 100/month cap — that
  rotation logic is retired now that we're on Basic. See ROTATION-RETIRED
  note below if reverting.)

SYNC NOTE (3 Aug 2026): this file was reconstructed to match the version
confirmed as actually live in the repo (paste-back confirmed word-for-word
in a separate chat), since the copy in this project had drifted out of sync
after multiple sessions edited the repo independently. If you've since made
further edits directly in GitHub that aren't reflected here, paste the
current file back before trusting this as ground truth again.
"""

import json
import os
import urllib.request
import urllib.parse
from datetime import datetime, timezone

API_KEY = os.environ["AVIATIONSTACK_API_KEY"]

# Full known Delta A350 flight-number roster (from the FlightRadar24-derived
# rotation study). Queried in full every run now that Basic-tier quota makes
# that cheap (see QUOTA MATH above).
FULL_ROSTER = [
    "DL7", "DL8", "DL11", "DL12", "DL26", "DL27", "DL38", "DL39", "DL40", "DL41",
    "DL68", "DL69", "DL70", "DL71", "DL82", "DL83", "DL88", "DL89", "DL95", "DL96",
    "DL120", "DL121", "DL132", "DL133", "DL136", "DL137", "DL146", "DL147",
    "DL158", "DL159", "DL160", "DL161", "DL166", "DL167", "DL170", "DL171",
    "DL172", "DL173", "DL188", "DL189", "DL196", "DL197", "DL200", "DL201",
    "DL210", "DL211", "DL274", "DL275", "DL280", "DL281", "DL290", "DL291",
    "DL327", "DL388", "DL389", "DL763",
]

# ROTATION-RETIRED: PRIORITY_FLIGHTS / ROTATION_POOL / rotation_state.json
# were the free-tier throttling mechanism (3 flights/run, cycling through
# the roster over many days). No longer used now that every run queries
# FULL_ROSTER directly. Kept out of this file entirely rather than left as
# dead code; see git history / Master Reference doc if you ever need to
# revert to a metered rotation (e.g. tier downgrade).

DATA_DIR = "data"
HISTORY_PATH = os.path.join(DATA_DIR, "flights_history.jsonl")
LATEST_PATH = os.path.join(DATA_DIR, "latest.json")

# DEDUP-ON-APPEND (added 7 Aug 2026): without this, flights_history.jsonl
# grew to 95MB / 82,668 lines with 93.3% exact-duplicate content in under
# a week -- Aviationstack's no-flight_date query returns the same ~80
# historical instances per flight number on every call, and this script
# was appending all of them, every run, forever. That eventually broke
# every future run outright (GitHub hard-rejects any single file over
# 100MB). Fix: before appending, load the "core" content (everything
# except our own _fetch_* metadata fields) of every record already in the
# file into a set, and skip writing any incoming record whose core content
# already exists. A record that's genuinely progressed (e.g. departure
# went from null to filled) has different core content and is correctly
# kept as a new line -- only byte-identical re-fetches are dropped.


def load_existing_record_hashes(path):
    """Read every existing record's core content (ignoring our own
    _fetch_* metadata) into a set, so new fetches can be checked against
    it before writing. Returns an empty set if the file doesn't exist yet."""
    hashes = set()
    if not os.path.exists(path):
        return hashes
    with open(path) as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
            except json.JSONDecodeError:
                continue
            core = {k: v for k, v in rec.items() if not k.startswith("_fetch")}
            hashes.add(json.dumps(core, sort_keys=True))
    return hashes

# WATCHDOG: a tiny, dedicated state file (NOT flights_history.jsonl, which
# is already 40MB+ and growing — reading it every run just to check a gap
# would be wasteful). Records only the timestamp of each automatic
# (non-manual) run, so a silently-skipped cron slot shows up as a loud
# WARNING in the very next run's log instead of going unnoticed for hours.
LAST_RUN_PATH = os.path.join(DATA_DIR, "last_run.json")

# Scheduled cadence is every 6 hours (see fetch-flights-workflow.yml).
# Threshold is set above 6h to allow for GitHub's normal scheduling jitter
# (observed: up to ~30-40 min late is routine) without false-alarming on
# every run — only flags gaps consistent with a genuinely missed slot.
EXPECTED_GAP_HOURS = 6
GAP_WARNING_THRESHOLD_HOURS = 7.5


def fetch_flight(flight_iata):
    params = urllib.parse.urlencode({
        "access_key": API_KEY,
        "flight_iata": flight_iata,
    })
    url = f"https://api.aviationstack.com/v1/flights?{params}"
    with urllib.request.urlopen(url, timeout=30) as resp:
        return json.loads(resp.read().decode())


def check_for_missed_run(now, mode):
    """WATCHDOG: compare 'now' against the last recorded automatic run and
    print a loud WARNING if the gap is too large to be explained by normal
    scheduling jitter -- i.e. a cron slot was likely silently skipped by
    GitHub, not just delayed. Only evaluated for automatic (non-manual)
    runs, since manual/on-demand runs aren't expected to land on the
    6-hour cadence and shouldn't trip a false alarm."""
    if mode == "manual":
        return
    if not os.path.exists(LAST_RUN_PATH):
        print("WATCHDOG: no last_run.json found yet (first run since this "
              "feature was added, or file missing) -- nothing to compare against.")
        return
    try:
        with open(LAST_RUN_PATH) as f:
            last = json.load(f)
        last_ts = datetime.fromisoformat(last["fetch_timestamp_utc"])
    except (json.JSONDecodeError, KeyError, ValueError) as e:
        print(f"WATCHDOG: could not read/parse {LAST_RUN_PATH} ({e}) -- skipping gap check.")
        return

    gap_hours = (now - last_ts).total_seconds() / 3600
    if gap_hours > GAP_WARNING_THRESHOLD_HOURS:
        print(f"WATCHDOG WARNING: {gap_hours:.1f}h since the last automatic run "
              f"(expected ~{EXPECTED_GAP_HOURS}h). This looks like at least one "
              f"scheduled cron slot was skipped, not just delayed -- worth "
              f"checking the Actions tab for a missing 'Scheduled' entry.")
    else:
        print(f"WATCHDOG: {gap_hours:.1f}h since the last automatic run -- within normal range.")


def record_run(now, mode):
    """WATCHDOG: persist this run's timestamp for the next run to compare
    against. Only updates for automatic runs, so a manual/on-demand check
    never resets the gap clock the next scheduled run is measured against."""
    if mode == "manual":
        return
    with open(LAST_RUN_PATH, "w") as f:
        json.dump({"fetch_timestamp_utc": now.isoformat(), "mode": mode}, f, indent=2)


def main():
    os.makedirs(DATA_DIR, exist_ok=True)

    manual_flight = os.environ.get("MANUAL_FLIGHT_NUMBER", "").strip().upper()

    if manual_flight:
        # Manual on-demand lookup: query exactly this one flight. Doesn't
        # touch the scheduled roster run at all.
        batch = [manual_flight]
        mode = "manual"
    else:
        # Full-roster sweep every automatic run (Basic-tier quota headroom).
        batch = FULL_ROSTER
        mode = "full_roster"

    now = datetime.now(timezone.utc)
    fetch_ts = now.isoformat()

    check_for_missed_run(now, mode)

    # DEDUP-ON-APPEND: load what's already in the file once, up front, so
    # every record from every flight in this run can be checked cheaply
    # against it (and against records already written earlier in this same
    # run) rather than re-reading the file per record.
    existing_hashes = load_existing_record_hashes(HISTORY_PATH)
    print(f"DEDUP: loaded {len(existing_hashes)} existing unique record(s) "
          f"from {HISTORY_PATH} to check new fetches against.")

    latest_results = []
    failures = []
    duplicates_skipped = 0

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

            core = {k: v for k, v in rec.items() if not k.startswith("_fetch")}
            core_hash = json.dumps(core, sort_keys=True)
            if core_hash in existing_hashes:
                duplicates_skipped += 1
                continue
            existing_hashes.add(core_hash)
            with open(HISTORY_PATH, "a") as f:
                f.write(json.dumps(rec) + "\n")

    with open(LATEST_PATH, "w") as f:
        json.dump({
            "fetch_timestamp_utc": fetch_ts,
            "fetch_mode": mode,
            "flight_numbers_queried": batch,
            "records": latest_results,
            "failures": failures,
        }, f, indent=2)

    record_run(now, mode)

    print(f"[{mode}] Queried {len(batch)} flight numbers, got {len(latest_results)} records, "
          f"{duplicates_skipped} exact duplicates skipped, {len(latest_results) - duplicates_skipped} "
          f"new records appended, {len(failures)} failures, history file: {HISTORY_PATH}")


if __name__ == "__main__":
    main()
