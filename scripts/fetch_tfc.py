#!/usr/bin/env python3
"""
Fetch the current week's booking data from The Food Corridor's public ganttdata endpoint
and output a JSON file suitable for the CCK schedule display.

NOTE: TFC's ganttdata endpoint interprets the `date` parameter in US Eastern Time,
so we must send midnight-ET timestamps (not UTC) for each day.
"""

import json
import sys
import urllib.request
from datetime import datetime, timezone, timedelta

TFC_URL = "https://app.thefoodcorridor.com/listings/46758-clarence-creative-kitchen/tfc_calendars/ganttdata"
# Only include these spaces on the display
INCLUDE_SPACES = {"Primary Kitchen"}

def get_et_offset():
    """Determine current US Eastern offset (EDT=-4 or EST=-5) based on DST rules."""
    # US DST: 2nd Sunday in March to 1st Sunday in November
    now_utc = datetime.now(timezone.utc)
    year = now_utc.year
    # Find 2nd Sunday in March
    mar1 = datetime(year, 3, 1, tzinfo=timezone.utc)
    dst_start = mar1 + timedelta(days=(6 - mar1.weekday()) % 7 + 7)  # 2nd Sunday
    dst_start = dst_start.replace(hour=7)  # 2 AM EST = 7 AM UTC
    # Find 1st Sunday in November
    nov1 = datetime(year, 11, 1, tzinfo=timezone.utc)
    dst_end = nov1 + timedelta(days=(6 - nov1.weekday()) % 7)  # 1st Sunday
    dst_end = dst_end.replace(hour=6)  # 2 AM EDT = 6 AM UTC
    if dst_start <= now_utc < dst_end:
        return timedelta(hours=-4)  # EDT
    return timedelta(hours=-5)  # EST

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

def main():
    sunday = get_week_range()
    seen = set()  # deduplicate events across day boundaries
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

            # Deduplicate by start time + title + calendar
            key = (item["startDate"], item["endDate"], item["title"], item["calendar"])
            if key in seen:
                continue
            seen.add(key)

            # Clean up display title (strip internal labels like "Grandfathered")
            clean_title = item["title"].replace("Grandfathered", "").strip()

            all_events.append({
                "title": clean_title,
                "space": item["calendar"],
                "startMs": item["startDate"],
                "endMs": item["endDate"],
                "color": item.get("color", "#A45EBF"),
            })

    # Sort by start time
    all_events.sort(key=lambda e: e["startMs"])

    output = {
        "fetchedAt": datetime.now(timezone.utc).isoformat(),
        "weekStart": sunday.isoformat(),
        "events": all_events,
    }

    print(json.dumps(output, indent=2))

if __name__ == "__main__":
    main()
