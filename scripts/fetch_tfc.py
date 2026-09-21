#!/usr/bin/env python3
"""
Fetch a rolling window of booking data from The Food Corridor's public ganttdata
endpoint and output a JSON file suitable for a CCK schedule display.

The window is: yesterday, today, and N-2 days forward — so the calendar always
shows mostly upcoming bookings rather than a full week of past data.

Also maintains a changelog of booking additions, removals, and modifications.

Configured per-feed via CLI arguments so one code path drives every board:

    # Kitchen A board (7-day window)
    python3 scripts/fetch_tfc.py

    # Kitchen B + Studio board (4-day window)
    python3 scripts/fetch_tfc.py --spaces "Kitchen B" "CCK Studio" \
        --days 4 --out events-b.json --changelog changelog-b.json

SPACE-NAME GUARD: if a requested space no longer appears anywhere in TFC's
response, this script exits non-zero instead of writing an empty feed. Renaming
a space in TFC used to silently blank the display — the Action stayed green
while publishing zero bookings. Now it fails loudly and the last good data stays
in place.

NOTE: TFC's ganttdata endpoint interprets the `date` parameter in US Eastern Time,
so we must send midnight-ET timestamps (not UTC) for each day.
"""

import argparse
import json
import os
import sys
import urllib.request
from datetime import datetime, timezone, timedelta

TFC_URL = "https://app.thefoodcorridor.com/listings/46758-clarence-creative-kitchen/tfc_calendars/ganttdata"
MAX_CHANGELOG_ENTRIES = 200  # Keep last 200 entries to avoid file growing forever

def parse_args():
    p = argparse.ArgumentParser(description="Sync TFC bookings into a schedule feed.")
    p.add_argument("--spaces", nargs="+", default=["Kitchen A"],
                   help="TFC space (calendar) names to include. Must match TFC exactly.")
    p.add_argument("--days", type=int, default=7,
                   help="Window length in days, starting yesterday. Default 7.")
    p.add_argument("--out", default="events.json",
                   help="Path to write the events feed. Default events.json.")
    p.add_argument("--changelog", default="changelog.json",
                   help="Path to write the changelog. Default changelog.json.")
    return p.parse_args()

def get_et_offset():
    """Determine current US Eastern offset (EDT=-4 or EST=-5) based on DST rules."""
    now_utc = datetime.now(timezone.utc)
    year = now_utc.year
    mar1 = datetime(year, 3, 1, tzinfo=timezone.utc)
    dst_start = mar1 + timedelta(days=(6 - mar1.weekday()) % 7 + 7)
    dst_start = dst_start.replace(hour=7)
    nov1 = datetime(year, 11, 1, tzinfo=timezone.utc)
    dst_end = nov1 + timedelta(days=(6 - nov1.weekday()) % 7)
    dst_end = dst_end.replace(hour=6)
    if dst_start <= now_utc < dst_end:
        return timedelta(hours=-4)
    return timedelta(hours=-5)

def get_rolling_window_start():
    """Get yesterday's date in Eastern Time (start of the rolling window)."""
    ET = timezone(get_et_offset())
    now_et = datetime.now(ET)
    yesterday = now_et - timedelta(days=1)
    yesterday = yesterday.replace(hour=0, minute=0, second=0, microsecond=0)
    return yesterday

def fetch_day(timestamp):
    """Fetch one day of gantt data from TFC."""
    url = f"{TFC_URL}?date={timestamp}&day=1"
    req = urllib.request.Request(url, headers={"User-Agent": "CCK-Schedule-Sync/1.0"})
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            return json.loads(resp.read())
    except Exception as e:
        print(f"Warning: Failed to fetch date {timestamp}: {e}", file=sys.stderr)
        return []

def format_event_time(ms):
    """Format a millisecond timestamp to a readable ET string like 'Tue Apr 15 6:00 AM'."""
    et_off = get_et_offset()
    dt = datetime.fromtimestamp(ms / 1000, tz=timezone.utc) + et_off
    return dt.strftime("%a %b %d %-I:%M %p")

def event_key(ev):
    """Create a unique key for an event based on title, space, start, end."""
    return (ev["title"], ev["space"], ev["startMs"], ev["endMs"])

# Set in main(): only label the space when a feed covers more than one, so the
# single-space Kitchen A changelog reads exactly as it always has.
SHOW_SPACE_IN_SUMMARY = False

def event_summary(ev):
    """Human-readable summary of an event."""
    where = f" — {ev['space']}" if SHOW_SPACE_IN_SUMMARY else ""
    return f"{ev['title']}{where} ({format_event_time(ev['startMs'])} – {format_event_time(ev['endMs'])})"

def is_future_event(ev):
    """Check if an event hasn't ended yet (today or future)."""
    et_off = get_et_offset()
    now_et = datetime.now(timezone(et_off))
    # Compare against end time so in-progress events aren't treated as past
    end_dt = datetime.fromtimestamp(ev["endMs"] / 1000, tz=timezone.utc) + et_off
    end_dt = end_dt.replace(tzinfo=timezone(et_off))
    return end_dt >= now_et.replace(hour=0, minute=0, second=0, microsecond=0)

def events_in_both_windows(old_events, new_events):
    """Find the overlapping date range between old and new event sets.
    Only compare events that fall on dates present in BOTH sets,
    so the daily rollover doesn't generate false adds/removes."""
    def event_dates(events):
        dates = set()
        et_off = get_et_offset()
        for ev in events:
            dt = datetime.fromtimestamp(ev["startMs"] / 1000, tz=timezone.utc) + et_off
            dates.add(dt.strftime("%Y-%m-%d"))
        return dates

    return event_dates(old_events) & event_dates(new_events)

def event_date_str(ev):
    """Get the ET date string for an event."""
    et_off = get_et_offset()
    dt = datetime.fromtimestamp(ev["startMs"] / 1000, tz=timezone.utc) + et_off
    return dt.strftime("%Y-%m-%d")

def compute_changelog(old_events, new_events, timestamp):
    """Compare old and new events, only logging genuine booking changes.
    Ignores: past events, window rollover (old days falling off / new days appearing)."""
    entries = []

    # Only compare events on dates that exist in both old and new data.
    # This prevents the rollover from flooding the changelog.
    shared_dates = events_in_both_windows(old_events, new_events)

    old_comparable = [e for e in old_events if event_date_str(e) in shared_dates and is_future_event(e)]
    new_comparable = [e for e in new_events if event_date_str(e) in shared_dates and is_future_event(e)]

    old_map = {event_key(e): e for e in old_comparable}
    new_map = {event_key(e): e for e in new_comparable}

    old_keys = set(old_map.keys())
    new_keys = set(new_map.keys())

    # Added events (on shared dates)
    for k in sorted(new_keys - old_keys, key=lambda x: x[2]):
        ev = new_map[k]
        entries.append({
            "time": timestamp,
            "type": "added",
            "description": f"Booking added: {event_summary(ev)}",
            "event": ev
        })

    # Removed events (on shared dates, future only)
    for k in sorted(old_keys - new_keys, key=lambda x: x[2]):
        ev = old_map[k]
        if is_future_event(ev):
            entries.append({
                "time": timestamp,
                "type": "removed",
                "description": f"Booking removed: {event_summary(ev)}",
                "event": ev
            })

    return entries

def main():
    global SHOW_SPACE_IN_SUMMARY
    args = parse_args()
    include_spaces = set(args.spaces)
    SHOW_SPACE_IN_SUMMARY = len(include_spaces) > 1

    window_start = get_rolling_window_start()  # yesterday
    seen = set()
    all_events = []
    spaces_seen = set()   # every calendar name TFC returned, for the guard
    fetch_failures = 0

    for day_offset in range(args.days):  # yesterday + today + (days-2) forward
        day = window_start + timedelta(days=day_offset)
        ts = int(day.timestamp())
        raw = fetch_day(ts)
        if not raw:
            fetch_failures += 1

        for item in raw:
            cal = item.get("calendar")
            if cal:
                spaces_seen.add(cal)

            if not item.get("title"):
                continue
            if cal not in include_spaces:
                continue

            key = (item["startDate"], item["endDate"], item["title"], cal)
            if key in seen:
                continue
            seen.add(key)

            clean_title = item["title"].replace("Grandfathered", "").strip()

            all_events.append({
                "title": clean_title,
                "space": cal,
                "startMs": item["startDate"],
                "endMs": item["endDate"],
                "color": item.get("color", "#A45EBF"),
            })

    # ── GUARD ─────────────────────────────────────────────────────────────
    # A space we asked for is missing from TFC entirely — almost always a
    # rename. Fail loudly rather than publishing an empty calendar, and leave
    # the previous feed in place so the display keeps showing the last
    # known-good schedule.
    if fetch_failures == args.days:
        print("ERROR: every TFC request failed — not writing feed.", file=sys.stderr)
        sys.exit(1)

    missing = sorted(s for s in include_spaces if s not in spaces_seen)
    if missing:
        print(f"ERROR: space(s) not found in TFC: {missing}", file=sys.stderr)
        print(f"       TFC currently returns: {sorted(spaces_seen)}", file=sys.stderr)
        print("       A space was probably renamed. Update --spaces to match, "
              "otherwise the display would silently go blank.", file=sys.stderr)
        sys.exit(1)
    # ──────────────────────────────────────────────────────────────────────

    all_events.sort(key=lambda e: (e["startMs"], e["space"]))

    now_iso = datetime.now(timezone.utc).isoformat()

    # --- Load previous feed for changelog comparison ---
    old_events = []
    if os.path.exists(args.out):
        try:
            with open(args.out) as f:
                old_events = json.load(f).get("events", [])
        except Exception:
            pass

    # --- Compute changelog ---
    new_entries = compute_changelog(old_events, all_events, now_iso)

    # --- Load existing changelog and append ---
    changelog = []
    if os.path.exists(args.changelog):
        try:
            with open(args.changelog) as f:
                changelog = json.load(f)
        except Exception:
            pass

    if new_entries:
        changelog = new_entries + changelog  # newest first
        changelog = changelog[:MAX_CHANGELOG_ENTRIES]  # trim

        with open(args.changelog, "w") as f:
            json.dump(changelog, f, indent=2)

    # --- Write the feed ---
    output = {
        "fetchedAt": now_iso,
        "windowStart": window_start.isoformat(),
        "windowDays": args.days,
        "spaces": sorted(include_spaces),
        "events": all_events,
    }
    with open(args.out, "w") as f:
        json.dump(output, f, indent=2)

    # Report
    print(f"{args.out}: {len(all_events)} booking(s) across {sorted(include_spaces)}")
    if new_entries:
        print(f"Found {len(new_entries)} change(s):")
        for e in new_entries:
            print(f"  [{e['type']}] {e['description']}")
    else:
        print("No changes detected.")

if __name__ == "__main__":
    main()
