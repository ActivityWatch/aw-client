"""Exercise report output with its generated AQL and an in-memory datastore."""

import json
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from typing import Any, cast

import pytest
from aw_core.models import Event
from aw_datastore import Datastore
from aw_datastore.storages import MemoryStorage
from aw_query import query as run_query

from aw_client import cli


@pytest.mark.parametrize("count", [0, 1, 99, 100, 101, 1000])
@pytest.mark.parametrize("limit", [1, 10, 200])
def test_report_total_uses_uncapped_active_duration(count, limit, monkeypatch, capsys):
    start = datetime(2026, 1, 1, tzinfo=timezone.utc)
    stop = start + timedelta(days=1)
    datastore = Datastore(MemoryStorage, testing=True)
    window = datastore.create_bucket(
        "aw-watcher-window_fixture", "currentwindow", "test-client", "fixture"
    )
    afk = datastore.create_bucket(
        "aw-watcher-afk_fixture", "afkstatus", "test-client", "fixture"
    )
    for i in range(count):
        window.insert(
            Event(
                timestamp=start + timedelta(seconds=i * 10),
                duration=10,
                data={"app": "Editor", "title": f"file-{i}"},
            )
        )
    # Add a distinct AFK title: the total must exclude it even when uncapped.
    window.insert(
        Event(
            timestamp=start + timedelta(seconds=count * 10),
            duration=20,
            data={"app": "Editor", "title": "away"},
        )
    )
    if count:
        afk.insert(
            Event(timestamp=start, duration=count * 10, data={"status": "not-afk"})
        )
    afk.insert(
        Event(
            timestamp=start + timedelta(seconds=count * 10),
            duration=20,
            data={"status": "afk"},
        )
    )

    class Client:
        calls = 0

        def query(self, query, periods, **kwargs):
            self.calls += 1
            assert periods == [(start, stop)]
            self.result = run_query("test", query, start, stop, datastore)
            # Match the HTTP response's JSON conversion of AQL timedeltas.
            return json.loads(
                json.dumps(
                    [self.result],
                    default=lambda value: value.total_seconds()
                    if isinstance(value, timedelta)
                    else value.isoformat(),
                )
            )

    client = Client()
    monkeypatch.setattr(
        cli, "get_classes", lambda: [(["Work"], {"type": "regex", "regex": "Editor"})]
    )
    cast(Any, cli.report.callback).__wrapped__(
        SimpleNamespace(client=client),
        hostname="fixture",
        cache=False,
        start=start,
        stop=stop,
        limit=limit,
    )
    output = capsys.readouterr().out
    assert client.calls == 1
    assert len(client.result["window"]["title_events"]) == min(count, 100)
    assert client.result["window"]["duration"] == timedelta(seconds=count * 10)
    assert all(
        event["data"]["$category"] == ["Work"]
        for event in client.result["window"]["cat_events"]
    )
    assert "away" not in output
    actual = output.split("Total duration:\t ")[1].strip()
    assert actual == str(timedelta(seconds=count * 10))
