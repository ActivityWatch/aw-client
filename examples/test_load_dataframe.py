"""Offline regressions for the dataframe example's display and CSV boundary."""

import copy
from datetime import timedelta
from io import StringIO
from unittest.mock import Mock

import pandas as pd
import pytest

import load_dataframe


def event(data=None, timestamp="2026-10-30T08:00:00Z", duration=60):
    return {
        "timestamp": timestamp,
        "duration": duration,
        "data": {"$category": ["Work", "Programming"], "app": "editor", **(data or {})},
    }


@pytest.mark.parametrize("export", [False, True])
@pytest.mark.parametrize("case", ["empty", "ordinary", "collisions"])
def test_main_dataframe_schema_and_csv(monkeypatch, tmp_path, case, export):
    events = [] if case == "empty" else [event()]
    if case == "collisions":
        events = [
            event({"timestamp": "payload time", "duration": 999}),
            event(
                {
                    "timestamp": "other payload time",
                    "duration": 123,
                    "data.timestamp": "literal prefix",
                    "data.data.timestamp": "second prefix",
                    "data": {"duration": "nested prefix"},
                },
                timestamp="2026-10-30T10:00:00+02:00",
                duration=0.5,
            ),
        ]
    original = copy.deepcopy(events)
    client = Mock()
    client.query.return_value = [{"events": events}]
    monkeypatch.setattr(load_dataframe, "ActivityWatchClient", lambda: client)
    monkeypatch.setattr(load_dataframe, "build_query", lambda: "synthetic query")
    prompt = Mock(return_value="y" if export else "n")
    monkeypatch.setattr("builtins.input", prompt)
    frames: list[pd.DataFrame] = []

    def capture_print(*args):
        frames.extend(
            arg.copy(deep=True) for arg in args if isinstance(arg, pd.DataFrame)
        )

    monkeypatch.setattr("builtins.print", capture_print)
    monkeypatch.chdir(tmp_path)
    load_dataframe.main()

    assert events == original
    prompt.assert_called_once_with("Do you want to export to CSV? (y/N): ")
    client.query.assert_called_once()
    assert client.query.call_args.args[0] == "synthetic query"
    assert len(frames) == 1
    frame = frames[0]
    assert isinstance(frame.index, pd.DatetimeIndex)
    assert frame.index.name == "timestamp"
    assert str(frame.index.tz) == "UTC"
    assert pd.api.types.is_timedelta64_dtype(frame["duration"].dtype)
    if not events:
        assert list(frame.columns) == ["duration", "$category"]
        assert frame["$category"].dtype == object
    assert len(frame) == len(events)
    if events:
        assert frame.index[0] == pd.Timestamp("2026-10-30T08:00:00Z")
        assert frame["duration"].iloc[0] == timedelta(minutes=1)
        assert frame["$category"].tolist() == ["Work > Programming"] * len(events)
        assert frame["app"].tolist() == ["editor"] * len(events)
    if case == "ordinary":
        assert list(frame.columns) == ["duration", "$category", "app"]
    if case == "collisions":
        assert frame.index[1] == frame.index[0]
        assert frame["duration"].iloc[1] == timedelta(seconds=0.5)
        assert frame["data.data.data.timestamp"].tolist() == [
            "payload time",
            "other payload time",
        ]
        assert frame["data.data.duration"].tolist() == [999, 123]
        assert frame["data.timestamp"].iloc[1] == "literal prefix"
        assert frame["data.data.timestamp"].iloc[1] == "second prefix"
        assert frame["data.duration"].iloc[1] == "nested prefix"

    output = tmp_path / "output.csv"
    assert output.exists() == export
    if export:
        expected = frame.to_csv()
        assert output.read_text() == expected
        if not events:
            assert expected == "timestamp,duration,$category\n"
        csv_frame = pd.read_csv(StringIO(expected), index_col="timestamp")
        assert list(csv_frame.columns) == list(frame.columns)
        assert len(csv_frame) == len(events)
        if events:
            assert pd.Timestamp(csv_frame.index[0]) == frame.index[0]
            assert csv_frame["duration"].iloc[0] == "0 days 00:01:00"
        if case == "collisions":
            assert csv_frame["data.data.data.timestamp"].iloc[1] == "other payload time"
            assert csv_frame["data.timestamp"].iloc[1] == "literal prefix"
