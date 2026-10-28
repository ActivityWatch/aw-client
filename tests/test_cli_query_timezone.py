"""Exercise query bound conversion offline through Click and the real client."""

import json
from datetime import datetime, timezone
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import pytest
from click.testing import CliRunner

from aw_client import ActivityWatchClient
from aw_client import cli


@pytest.mark.parametrize("json_output", [False, True])
@pytest.mark.parametrize(
    "start_text, stop_text",
    [
        (None, None),
        ("2026-01-15 12:00:00", None),
        (None, "2027-07-15 12:00:00"),
        ("2026-01-15 12:00:00", "2027-07-15 12:00:00"),
    ],
)
def test_query_timezone_preserves_aware_defaults(
    tmp_path, monkeypatch, start_text, stop_text, json_output
):
    zone = ZoneInfo("Europe/Stockholm")
    defaults = {
        "start": datetime(2026, 1, 15, 12, tzinfo=timezone.utc),
        "stop": datetime(2027, 7, 15, 12, tzinfo=timezone.utc),
    }
    for parameter in cli.query.params:
        if parameter.name in defaults:
            monkeypatch.setattr(parameter, "default", defaults[parameter.name])

    captured = []

    def evaluate(text, periods, **kwargs):
        captured.extend(periods)
        return [[]]

    path = tmp_path / "query.awq"
    path.write_text("RETURN = [];")
    args = [str(path), "--timezone", "Europe/Stockholm"]
    if json_output:
        args.append("--json")
    for option, value in [("--start", start_text), ("--stop", stop_text)]:
        if value:
            args.extend([option, value])
    result = CliRunner().invoke(
        cli.query, args, obj=SimpleNamespace(client=SimpleNamespace(query=evaluate))
    )
    assert result.exit_code == 0, repr(result.exception)
    start, stop = captured[0]
    expected_start = (
        datetime.fromisoformat(start_text).replace(tzinfo=zone)
        if start_text
        else defaults["start"]
    )
    expected_stop = (
        datetime.fromisoformat(stop_text).replace(tzinfo=zone)
        if stop_text
        else defaults["stop"]
    )
    assert start.astimezone(timezone.utc) == expected_start.astimezone(timezone.utc)
    assert stop.astimezone(timezone.utc) == expected_stop.astimezone(timezone.utc)
    assert stop.astimezone(timezone.utc) - start.astimezone(
        timezone.utc
    ) == expected_stop.astimezone(timezone.utc) - expected_start.astimezone(
        timezone.utc
    )
    if json_output:
        assert json.loads(result.output) == [[]]
    else:
        assert "Total duration:\t 0:00:00" in result.output


def test_query_timezone_periods_pass_real_client_validation(tmp_path, monkeypatch):
    # Bypass construction only: query() still performs real period validation and
    # serialization. Record the transport rather than contacting a live server.
    client = object.__new__(ActivityWatchClient)
    captured = []

    def post(endpoint, data, **kwargs):
        captured.append(data)
        return SimpleNamespace(json=lambda: [[]])

    monkeypatch.setattr(client, "_post", post)
    path = tmp_path / "query.awq"
    path.write_text("RETURN = [];")
    result = CliRunner().invoke(
        cli.query,
        [str(path), "--timezone", "Europe/Stockholm", "--json"],
        obj=SimpleNamespace(client=client),
    )
    assert result.exit_code == 0, repr(result.exception)
    start, stop = captured[0]["timeperiods"][0].split("/")
    assert (
        datetime.fromisoformat(start).astimezone(timezone.utc) == cli.now - cli.td1day
    )
    assert datetime.fromisoformat(stop).astimezone(timezone.utc) == cli.now + cli.td1yr
