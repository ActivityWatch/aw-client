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
from typing import Any

import iso8601
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


def serialize_events(raw_events: list) -> list:
    """Keep only the fields downstream tools need, sorted by *parsed* time.
    Sorting on the raw timestamp string is not equivalent: two valid
    ISO8601 timestamps using different UTC offsets (e.g. "-05:00" vs "Z")
    can sort backwards as strings even though one is unambiguously earlier
    than the other once parsed."""
    events = [
        {"timestamp": e["timestamp"], "duration": e["duration"], "data": e["data"]}
        for e in raw_events
    ]
    events.sort(key=lambda e: iso8601.parse_date(e["timestamp"]))
    return events


def write_json_atomic(path: str, data: Any) -> None:
    """Write to a temp file in the same directory, then atomically replace
    the destination. Without this, a failure partway through serialization
    (a very large export, disk full mid-write) would leave a truncated file
    in place of whatever good export was already there."""
    tmp_path = f"{path}.tmp"
    with open(tmp_path, "w") as f:
        json.dump(data, f, indent=2)
    os.replace(tmp_path, path)


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

    events = serialize_events(data[0]["events"])
    write_json_atomic(args.out, events)

    print(f"Wrote {len(events)} events to {args.out}")


def test_build_query_embeds_hostname():
    query = build_query("myhost")
    assert "aw-watcher-window_myhost" in query
    assert "aw-watcher-afk_myhost" in query


def test_serialize_events_sorts_by_parsed_time_not_string():
    # Both timestamps are valid ISO8601, but a plain string sort gets this
    # backwards: the first is 2024-01-02 04:30 UTC, the second is
    # 2024-01-02 01:00 UTC (earlier) -- yet "...01-01T23..." < "...01-02T01..."
    # as strings, because the offsets differ.
    raw = [
        {"timestamp": "2024-01-01T23:30:00-05:00", "duration": 60, "data": {"app": "later"}},
        {"timestamp": "2024-01-02T01:00:00Z", "duration": 60, "data": {"app": "earlier"}},
    ]
    result = serialize_events(raw)
    assert [e["data"]["app"] for e in result] == ["earlier", "later"]


def test_serialize_events_keeps_only_expected_fields():
    raw = [{"timestamp": "2024-01-01T09:00:00Z", "duration": 60, "data": {"app": "a"}, "id": 123}]
    result = serialize_events(raw)
    assert set(result[0].keys()) == {"timestamp", "duration", "data"}


def test_write_json_atomic_preserves_prior_file_on_failed_write(tmp_path):
    path = str(tmp_path / "export.json")
    write_json_atomic(path, [{"ok": True}])

    class Unserializable:
        pass

    try:
        write_json_atomic(path, {"bad": Unserializable()})
    except TypeError:
        pass

    with open(path) as f:
        assert json.load(f) == [{"ok": True}]


if __name__ == "__main__":
    main()
