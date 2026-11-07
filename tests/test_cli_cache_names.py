"""Exercise real Click parsing and SDK query encoding without server traffic."""

import json
from datetime import datetime
from typing import Any, List

import pytest
import requests
from click.testing import CliRunner

from aw_client import ActivityWatchClient, cli


@pytest.fixture(params=["report", "canonical"])
def command(request):
    return request.param


@pytest.fixture
def transport(monkeypatch, command):
    calls = []
    event = {
        "timestamp": "2026-10-28T12:00:00+00:00",
        "duration": 60,
        "data": {"app": "editor", "title": "fixture", "$category": ["Work"]},
    }
    payload: List[Any] = (
        [{"window": {"cat_events": [event], "title_events": [event]}}]
        if command == "report"
        else [[event]]
    )

    class FixtureClient:
        # Bind the real query method without constructing config, locks or queues.
        query = ActivityWatchClient.query

        def __init__(self, *args, **kwargs):
            pass

        def _post(self, endpoint, data, params=None):
            calls.append((endpoint, data, params))
            response = requests.Response()
            response.status_code = 200
            response._content = json.dumps(payload).encode()
            return response

    monkeypatch.setattr(cli.aw_client, "ActivityWatchClient", FixtureClient)
    monkeypatch.setattr(cli, "get_classes", lambda: cli.default_classes)
    return calls


def invoke(command, *options):
    return CliRunner().invoke(
        cli.main,
        [command, "fixture-host", "--start", "2026-10-28", "--stop", "2026-10-29"]
        + list(options),
    )


def test_help_describes_cache_name(command, transport):
    result = CliRunner().invoke(cli.main, [command, "--help"])
    assert result.exit_code == 0, result.output
    assert "--name TEXT" in result.output
    assert "required with --cache" in result.output
    assert transport == []


def test_named_cache_reaches_sdk_transport(command, transport):
    uncached = invoke(command)
    assert uncached.exit_code == 0, uncached.exception
    assert "fixture" in uncached.output
    assert "name" not in transport[0][2]
    uncached_body = transport[0][1]
    transport.clear()

    result = invoke(command, "--cache", "--name", "fixture-query")
    assert result.exit_code == 0, result.exception
    assert result.output == uncached.output
    assert len(transport) == 1
    endpoint, body, params = transport[0]
    assert endpoint == "query/"
    assert params == {"name": "fixture-query", "cache": 1}
    assert body == uncached_body
    assert "fixture-host" in "\n".join(body["query"])
    start, stop = body["timeperiods"][0].split("/")
    assert datetime.fromisoformat(start).utcoffset() is not None
    assert datetime.fromisoformat(stop).utcoffset() is not None


def test_unnamed_cache_keeps_sdk_guard(command, transport):
    result = invoke(command, "--cache")
    assert result.exit_code != 0
    assert "not allowed to do caching without a query name" in str(result.exception)
    assert transport == []
