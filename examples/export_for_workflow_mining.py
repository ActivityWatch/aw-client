"""
Export canonical window events to a flat JSON file shaped for external
process-mining / workflow-discovery tools: a list of
{"timestamp": <ISO8601>, "duration": <seconds>, "data": {...}} records, one
per event, sorted by time.

This is deliberately *not* a bucket dump -- it reuses the same categorizing
+ AFK-filtering canonical query as load_dataframe.py, so the exported events
are already the "what was actually happening" view rather than every raw
watcher heartbeat, which is what a downstream sequence-clustering or
process-mining tool actually wants as input.
"""

import argparse
import json
import os
import socket
from datetime import datetime, timedelta, timezone

from aw_client import ActivityWatchClient
from aw_client.classes import default_classes
from aw_client.queries import DesktopQueryParams, canonicalEvents


def build_query(hostname: str) -> str:
    canonicalQuery = canonicalEvents(
        DesktopQueryParams(
            bid_window=f"aw-watcher-window_{hostname}",
            bid_afk=f"aw-watcher-afk_{hostname}",
            classes=default_classes,
        )
    )
    return f"""
    {canonicalQuery}
    RETURN = {{"events": events}};
    """


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--days", type=int, default=7, help="How many days back to export (default: 7)"
    )
    parser.add_argument(
        "--hostname",
        default="fakedata" if os.getenv("CI") else socket.gethostname(),
        help="Hostname whose window/AFK buckets to query (default: this machine's)",
    )
    parser.add_argument(
        "--out", default="activitywatch_export.json", help="Output file path"
    )
    args = parser.parse_args()

    now = datetime.now(tz=timezone.utc)
    since = now - timedelta(days=args.days)

    aw = ActivityWatchClient(client_name="export_for_workflow_mining")
    print(f"Querying the last {args.days} day(s) of events for host '{args.hostname}'...")
    query = build_query(args.hostname)
    data = aw.query(query, [(since, now)])

    events = [
        {"timestamp": e["timestamp"], "duration": e["duration"], "data": e["data"]}
        for e in data[0]["events"]
    ]
    events.sort(key=lambda e: e["timestamp"])

    with open(args.out, "w") as f:
        json.dump(events, f, indent=2)

    print(f"Wrote {len(events)} events to {args.out}")


if __name__ == "__main__":
    main()
