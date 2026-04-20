#!/usr/bin/env python3
"""
Fetch the current week's booking data from The Food Corridor's public ganttdata endpoint
and output a JSON file suitable for the CCK schedule display.

Also maintains a changelog of booking additions, removals, and modifications.

NOTE: TFC's ganttdata endpoint interprets the `date` parameter in US Eastern Time,
so we must send midnight-ET timestamps (not UTC) for each day.
"""

import json
import os
import sys
import urllib.request
from datetime import datetime, timezone, timedelta

TFC_URL = "https://app.thefoodcorridor.com/listings/46758-clarence-creative-kitchen/tfc_calendars/ganttdata"
INCLUDE_SPACES = {"Primary Kitchen"}
MAX_CHANGELOG_ENTRIES = 200  # Keep last 200 entries to avoid file growing forever

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

def get_week_range():
    """Get Sunday-Saturday date range for the current week in Eastern Time."""
    ET = timezone(get_et_offset())
    now_et = datetime.now(ET)
    days_since_sunday = now_et.weekday() + 1
    if days_since_sunday == 7:
        days_since_sunday = 0
    sunday = now_et - timedelta(days=days_since_sunday)
    sunday = sunday.replace(hour=0, minute=0, second=0, microsecond=0)
    return sunday

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
    """Create a unique key for an event based on title, start, end."""
    return (ev["title"], ev["startMs"], ev["endMs"])

def event_summary(ev):
    """Human-readable summary of an event."""
    return f"{ev['title']} ({format_event_time(ev['startMs'])} – {format_event_time(ev['endMs'])})"

def compute_changelog(old_events, new_events, timestamp):
    """Compare old and new events and return a list of changelog entries."""
    entries = []
    old_map = {event_key(e): e for e in old_events}
    new_map = {event_key(e): e for e in new_events}

    old_keys = set(old_map.keys())
    new_keys = set(new_map.keys())

    # Added events
    for k in sorted(new_keys - old_keys, key=lambda x: x[1]):
        ev = new_map[k]
        entries.append({
            "time": timestamp,
            "type": "added",
            "description": f"Booking added: {event_summary(ev)}",
            "event": ev
        })

    # Removed events
    for k in sorted(old_keys - new_keys, key=lambda x: x[1]):
        ev = old_map[k]
        entries.append({
            "time": timestamp,
            "type": "removed",
            "description": f"Booking removed: {event_summary(ev)}",
            "event": ev
        })

    return entries

def main():
    sunday = get_week_range()
    seen = set()
    all_events = []

    for day_offset in range(7):
        day = sunday + timedelta(days=day_offset)
        ts = int(day.timestamp())
        raw = fetch_day(ts)

        for item in raw:
            if not item.get("title"):
                continue
            if item.get("calendar") not in INCLUDE_SPACES:
                continue

            key = (item["startDate"], item["endDate"], item["title"], item["calendar"])
            if key in seen:
                continue
            seen.add(key)

            clean_title = item["title"].replace("Grandfathered", "").strip()

            all_events.append({
                "title": clean_title,
                "space": item["calendar"],
                "startMs": item["startDate"],
                "endMs": item["endDate"],
                "color": item.get("color", "#A45EBF"),
            })

    all_events.sort(key=lambda e: e["startMs"])

    now_iso = datetime.now(timezone.utc).isoformat()

    # --- Load previous events.json for changelog comparison ---
    old_events = []
    if os.path.exists("events.json"):
        try:
            with open("events.json") as f:
                old_data = json.load(f)
                old_events = old_data.get("events", [])
        except Exception:
            pass

    # --- Compute changelog ---
    new_entries = compute_changelog(old_events, all_events, now_iso)

    # --- Load existing changelog and append ---
    changelog = []
    if os.path.exists("changelog.json"):
        try:
            with open("changelog.json") as f:
                changelog = json.load(f)
        except Exception:
            pass

    if new_entries:
        changelog = new_entries + changelog  # newest first
        changelog = changelog[:MAX_CHANGELOG_ENTRIES]  # trim

        with open("changelog.json", "w") as f:
            json.dump(changelog, f, indent=2)

    # --- Write events.json ---
    output = {
        "fetchedAt": now_iso,
        "weekStart": sunday.isoformat(),
        "events": all_events,
    }
    with open("events.json", "w") as f:
        json.dump(output, f, indent=2)

    # Report
    if new_entries:
        print(f"Found {len(new_entries)} change(s):")
        for e in new_entries:
            print(f"  [{e['type']}] {e['description']}")
    else:
        print("No changes detected.")

if __name__ == "__main__":
    main()
