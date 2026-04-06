#!/usr/bin/env python3
"""
Fetch the current week's booking data from The Food Corridor's public ganttdata endpoint
and output a JSON file suitable for the CCK schedule display.
"""

import json
import sys
import urllib.request
from datetime import datetime, timezone, timedelta

TFC_URL = "https://app.thefoodcorridor.com/listings/46758-clarence-creative-kitchen/tfc_calendars/ganttdata"
# Only include these spaces on the display
INCLUDE_SPACES = {"Primary Kitchen", "CCK Studio"}

def get_week_range():
    """Get Sunday-Saturday date range for the current week (UTC-based)."""
    now = datetime.now(timezone.utc)
    # Find Sunday (start of week)
    days_since_sunday = now.weekday() + 1  # Monday=0, so Sunday= +1 mod 7
    if days_since_sunday == 7:
        days_since_sunday = 0
    sunday = now - timedelta(days=days_since_sunday)
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
    all_events = []

    for day_offset in range(7):
        day = sunday + timedelta(days=day_offset)
        ts = int(day.timestamp())
        raw = fetch_day(ts)

        for item in raw:
            # Skip empty placeholder rows
            if not item.get("title"):
                continue
            # Skip spaces we don't care about
            if item.get("calendar") not in INCLUDE_SPACES:
                continue

            # Clean up display title (strip internal labels like "Grandfathered")
            clean_title = item["title"].replace("Grandfathered", "").strip()

            all_events.append({
                "title": clean_title,
                "space": item["calendar"],
                "startMs": item["startDate"],
                "endMs": item["endDate"],
                "color": item.get("color", "#A45EBF"),
            })

    output = {
        "fetchedAt": datetime.now(timezone.utc).isoformat(),
        "weekStart": sunday.isoformat(),
        "events": all_events,
    }

    print(json.dumps(output, indent=2))

if __name__ == "__main__":
    main()
