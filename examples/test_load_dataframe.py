"""Offline coverage for empty query results in the dataframe example."""

import pandas as pd
import pytest

import load_dataframe


@pytest.mark.parametrize("export", [False, True])
@pytest.mark.parametrize("empty", [False, True])
def test_empty_and_ordinary_results(monkeypatch, tmp_path, export, empty):
    events = (
        []
        if empty
        else [
            {
                "timestamp": "2026-10-30T08:00:00Z",
                "duration": 60,
                "data": {"$category": ["Work"], "app": "editor"},
            }
        ]
    )

    class Client:
        def query(self, *_):
            return [{"events": events}]

    monkeypatch.setattr(load_dataframe, "ActivityWatchClient", Client)
    monkeypatch.setattr(load_dataframe, "build_query", lambda: "synthetic query")
    monkeypatch.setattr("builtins.input", lambda _: "y" if export else "n")
    frames = []
    monkeypatch.setattr("builtins.print", lambda value: frames.append(value))
    monkeypatch.chdir(tmp_path)
    load_dataframe.main()

    frame = next(value for value in frames if isinstance(value, pd.DataFrame))
    assert frame.index.name == "timestamp"
    assert len(frame) == len(events)
    assert str(frame.index.tz) == "UTC"
    assert "duration" in frame.columns
    assert pd.api.types.is_timedelta64_dtype(frame["duration"])
    if not empty:
        assert frame["$category"].tolist() == ["Work"]
        assert frame["duration"].iloc[0] == pd.Timedelta(seconds=60)
    output = tmp_path / "output.csv"
    assert output.exists() == export
    if export:
        assert output.read_text() == frame.to_csv()
        assert output.read_text().startswith("timestamp,duration,")
