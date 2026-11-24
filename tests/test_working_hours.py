"""Calendar-boundary regressions for the two working-hours examples, offline."""

import importlib.util
import sys
import time
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest

EXAMPLES = Path(__file__).resolve().parents[1] / "examples"


def load(name):
    spec = importlib.util.spec_from_file_location(name, EXAMPLES / f"{name}.py")
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def local_zone(monkeypatch):
    if not hasattr(time, "tzset"):
        pytest.skip("system-local TZ control requires tzset")
    with monkeypatch.context() as mp:
        mp.setenv("TZ", "Europe/Stockholm")
        time.tzset()
        try:
            yield
        finally:
            mp.undo()
            time.tzset()


def clock(day, hour=12):
    class Clock(datetime):
        @classmethod
        def now(cls, tz=None):
            value = datetime.combine(day, datetime.min.time()).replace(hour=hour)
            return value if tz is None else value.astimezone(tz)

    return Clock


def assert_periods(periods, first, count, offset):
    assert len(periods) == count
    for i, (start, end) in enumerate(periods):
        day = first + timedelta(days=i)
        expected_start = (
            datetime.combine(day, datetime.min.time()) + offset
        ).astimezone()
        expected_end = (
            datetime.combine(day + timedelta(days=1), datetime.min.time()) + offset
        ).astimezone()
        assert start == expected_start
        assert end == expected_end
        if i:
            assert periods[i - 1][1] == start
        hours = (
            end.astimezone(timezone.utc) - start.astimezone(timezone.utc)
        ).total_seconds() / 3600
        expected_hours = {date(2026, 3, 28): 23, date(2026, 10, 24): 25}.get(day, 24)
        assert hours == expected_hours


@pytest.mark.usefixtures("local_zone")
@pytest.mark.parametrize(
    "day", [date(2026, 3, 30), date(2026, 10, 26), date(2026, 10, 30)]
)
@pytest.mark.parametrize("offset", [timedelta(hours=4), timedelta(hours=6, minutes=30)])
def test_local_main_calendar_periods(monkeypatch, tmp_path, day, offset):
    work = load("working_hours")
    monkeypatch.setattr(work, "datetime", clock(day))
    monkeypatch.setattr(work, "day_offset", offset)
    periods = []

    def query(regex, bounds, hostname):
        periods.extend(bounds)
        assert (regex, hostname) == ("synthetic", "synthetic-host")
        return [{"events": [], "duration": 0} for _ in bounds]

    monkeypatch.setattr(work, "query", query)
    monkeypatch.setattr(
        sys, "argv", ["working_hours.py", "synthetic", "synthetic-host"]
    )
    monkeypatch.chdir(tmp_path)
    work.main()
    assert_periods(periods, day - timedelta(days=4), 5, offset)
    assert (tmp_path / "working_hours_events.json").exists()


@pytest.mark.usefixtures("local_zone")
@pytest.mark.parametrize(
    "day", [date(2026, 3, 30), date(2026, 10, 26), date(2026, 10, 30)]
)
@pytest.mark.parametrize("empty", [False, True])
@pytest.mark.parametrize("hour", [2, 12])
@pytest.mark.parametrize("offset", [timedelta(hours=4), timedelta(hours=6, minutes=30)])
def test_sheets_calendar_resume_and_future_skip(monkeypatch, day, empty, hour, offset):
    work = load("working_hours")
    monkeypatch.setitem(sys.modules, "working_hours", work)
    monkeypatch.syspath_prepend(str(EXAMPLES))
    sheets = load("working_hours_gspread")
    monkeypatch.setattr(work, "day_offset", offset)
    monkeypatch.setattr(work, "datetime", clock(day, hour))
    monkeypatch.setattr(sheets, "datetime", clock(day, hour))
    periods, updates, appends = [], [], []
    last = day - timedelta(days=3)
    rows = [] if empty else [[str(last), "0"]]
    worksheet = SimpleNamespace(
        title="synthetic",
        get_all_values=lambda: rows,
        update_cell=lambda *args: updates.append(args),
        append_row=lambda row, **kwargs: appends.append(row),
    )
    document = SimpleNamespace(title="synthetic", worksheet=lambda name: worksheet)
    monkeypatch.setattr(
        sheets.gspread,
        "service_account",
        lambda: SimpleNamespace(open_by_key=lambda key: document),
    )

    def query(regex, bounds, hostname):
        periods.extend(bounds)
        return [{"events": [], "duration": 0} for _ in bounds]

    monkeypatch.setattr(work, "query", query)
    sheets.update_sheet("synthetic", "synthetic")
    first = day - timedelta(days=29) if empty else last
    assert_periods(periods, first, 30 if empty else 4, work.day_offset)
    assert updates == ([] if empty else [(1, 2, 0.0)])
    append_start = first if empty else last + timedelta(days=1)
    append_stop = day if hour >= 4 else day - timedelta(days=1)
    assert appends == [
        [str(append_start + timedelta(days=i)), 0.0]
        for i in range((append_stop - append_start).days + 1)
    ]
