"""Run the README-listed example with synthetic events and a fixed local clock."""

import copy
import datetime as dt
import os
from pathlib import Path
import runpy
import socket
import time

import aw_client
from aw_core import Event
import pytest


@pytest.fixture
def local_timezone(monkeypatch):
    if not hasattr(time, "tzset"):
        pytest.skip("Changing the process local timezone requires time.tzset")
    original = os.environ.get("TZ")

    def set_timezone(name):
        monkeypatch.setenv("TZ", name)
        time.tzset()

    yield set_timezone
    if original is None:
        os.environ.pop("TZ", None)
    else:
        os.environ["TZ"] = original
    time.tzset()


def run_example(monkeypatch, capsys, now, events):
    calls = []
    clock_calls = []
    original = copy.deepcopy(events)

    class Clock(dt.datetime):
        @classmethod
        def now(cls, tz=None):
            clock_calls.append(tz)
            return now.astimezone(tz) if tz else now.astimezone().replace(tzinfo=None)

    class Client:
        def __init__(self, name):
            assert name == "testclient"

        def get_events(self, bucket_id, *, start, end):
            calls.append((bucket_id, start, end))
            return events

    monkeypatch.setattr(dt, "datetime", Clock)
    monkeypatch.setattr(aw_client, "ActivityWatchClient", Client)
    runpy.run_path(
        str(Path(__file__).parents[1] / "examples" / "time_spent_today.py"),
        run_name="__main__",
    )
    assert events == original
    assert len(clock_calls) == 1
    assert len(calls) == 1
    assert calls[0][0] == f"aw-watcher-afk_{socket.gethostname()}"
    return capsys.readouterr().out.strip(), calls[0][1:]


@pytest.mark.parametrize(
    "start,duration,status,expected",
    [
        ("2026-10-29T23:00:00Z", 7200, "not-afk", "1:00:00"),
        ("2026-10-30T11:00:00Z", 7200, "not-afk", "1:00:00"),
        ("2026-10-29T23:00:00Z", 14 * 3600, "not-afk", "12:00:00"),
        ("2026-10-29T22:00:00Z", 3600, "not-afk", "0:00:00"),
        ("2026-10-29T23:00:00Z", 3600, "not-afk", "0:00:00"),
        ("2026-10-30T12:00:00Z", 3600, "not-afk", "0:00:00"),
        ("2026-10-30T13:00:00Z", 3600, "not-afk", "0:00:00"),
        ("2026-10-30T08:00:00Z", 3600, "not-afk", "1:00:00"),
        ("2026-10-30T08:00:00Z", 3600, "afk", "0:00:00"),
        ("2026-10-30T08:00:00Z", 0, "not-afk", "0:00:00"),
        ("2026-10-30T08:00:00Z", -60, "not-afk", "0:00:00"),
    ],
)
def test_interval_clipping(
    monkeypatch, capsys, local_timezone, start, duration, status, expected
):
    local_timezone("UTC")
    now = dt.datetime(2026, 10, 30, 12, tzinfo=dt.timezone.utc)
    event = Event(timestamp=start, duration=duration, data={"status": status})
    output, _ = run_example(monkeypatch, capsys, now, [event])
    assert output == f"Total time spent on computer today: {expected}"


def test_empty_events(monkeypatch, capsys, local_timezone):
    local_timezone("UTC")
    now = dt.datetime(2026, 10, 30, 12, tzinfo=dt.timezone.utc)
    output, _ = run_example(monkeypatch, capsys, now, [])
    assert output == "Total time spent on computer today: 0:00:00"


@pytest.mark.parametrize(
    "date,start,end,elapsed",
    [
        ((2026, 3, 29), "2026-03-28T23:00:00+00:00", "2026-03-29T22:00:00+00:00", 23),
        ((2026, 10, 25), "2026-10-24T22:00:00+00:00", "2026-10-25T23:00:00+00:00", 25),
        ((2026, 10, 30), "2026-10-29T23:00:00+00:00", "2026-10-30T23:00:00+00:00", 24),
    ],
)
def test_local_calendar_bounds(
    monkeypatch, capsys, local_timezone, date, start, end, elapsed
):
    local_timezone("Europe/Stockholm")
    year, month, day = date
    now = dt.datetime(year, month, day, 12, tzinfo=dt.timezone.utc)
    midnight = dt.datetime.fromisoformat(start)
    event = Event(
        timestamp=midnight, duration=elapsed * 3600, data={"status": "not-afk"}
    )
    output, bounds = run_example(monkeypatch, capsys, now, [event])
    assert bounds == (midnight, dt.datetime.fromisoformat(end))
    assert all(bound.utcoffset() == dt.timedelta() for bound in bounds)
    assert bounds[1] - bounds[0] == dt.timedelta(hours=elapsed)
    assert output == f"Total time spent on computer today: {now - midnight}"
