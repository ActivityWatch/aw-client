import json
from datetime import datetime, timezone
from urllib.parse import parse_qs, urlsplit

import pytest
import requests

from aw_client import ActivityWatchClient
from aw_client import client as client_module


@pytest.fixture
def client(monkeypatch):
    # Keep HTTP-only tests off the user's config, lock and queue files.
    monkeypatch.setattr(
        client_module,
        "load_config",
        lambda: {
            "server": {"hostname": "127.0.0.1", "port": 5600},
            "client": {"commit_interval": 10},
        },
    )
    monkeypatch.setattr(client_module, "SingleInstance", lambda name: object())
    monkeypatch.setattr(client_module, "RequestQueue", lambda client: object())
    monkeypatch.setattr(
        client_module, "load_local_server_api_key", lambda *a, **kw: None
    )
    return ActivityWatchClient("test-client", host="127.0.0.1", port=5600)


@pytest.fixture
def captured_requests(monkeypatch):
    captured = []

    def post(url, data=None, headers=None, params=None, **kwargs):
        captured.append(
            requests.Request(
                "POST", url, data=data, headers=headers, params=params
            ).prepare()
        )
        response = requests.Response()
        response.status_code = 200
        response._content = b'[17, [{"data": {"title": "synthetic"}}]]'
        return response

    monkeypatch.setattr(client_module.req, "post", post)
    return captured


@pytest.fixture
def periods():
    return [
        (
            datetime(2026, 10, 1, tzinfo=timezone.utc),
            datetime(2026, 10, 2, tzinfo=timezone.utc),
        )
    ]


@pytest.mark.parametrize(
    "options,expected_params",
    [
        ({}, {"cache": ["false"]}),
        ({"cache": False}, {"cache": ["false"]}),
        ({"cache": False, "name": "report"}, {"cache": ["false"]}),
        (
            {"cache": True, "name": "report"},
            {"cache": ["true"], "name": ["report"]},
        ),
    ],
)
def test_query_sends_explicit_cache_option(
    client, captured_requests, periods, options, expected_params
):
    result = client.query("RETURN = 17;\n", periods, **options)

    assert result == [17, [{"data": {"title": "synthetic"}}]]
    assert len(captured_requests) == 1
    request = captured_requests[0]
    assert urlsplit(request.url).path == "/api/0/query/"
    assert parse_qs(urlsplit(request.url).query) == expected_params
    assert json.loads(request.body) == {
        "timeperiods": ["2026-10-01T00:00:00+00:00/2026-10-02T00:00:00+00:00"],
        "query": ["RETURN = 17;", ""],
    }


def test_query_cache_requires_name(client, captured_requests, periods):
    with pytest.raises(Exception, match="caching without a query name"):
        client.query("RETURN = 17;", periods, cache=True)
    assert captured_requests == []


@pytest.mark.parametrize("naive_bound", [0, 1])
def test_query_rejects_naive_bounds(client, captured_requests, periods, naive_bound):
    bounds = list(periods[0])
    bounds[naive_bound] = bounds[naive_bound].replace(tzinfo=None)
    with pytest.raises(ValueError, match="timezone set"):
        client.query("RETURN = 17;", [tuple(bounds)], cache=False)
    assert captured_requests == []
