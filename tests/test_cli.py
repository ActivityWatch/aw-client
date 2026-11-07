import json
import time

import pytest
import requests
from click.testing import CliRunner

from aw_client import client as client_module
from aw_client.cli import main


@pytest.fixture
def kolkata_time_zone(monkeypatch):
    """Use a local time zone that isn't UTC, which CI runners use."""
    if not hasattr(time, "tzset"):
        pytest.skip("time.tzset() is not available on Windows")
    with monkeypatch.context() as m:
        m.setenv("TZ", "Asia/Kolkata")
        time.tzset()
        yield
    time.tzset()


def test_query_uses_local_time_without_timezone(
    kolkata_time_zone, monkeypatch, tmp_path
):
    # Keep the client off the user's config, lock and queue files.
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
    timeperiods = []

    def post(url, data, **kwargs):
        timeperiods.extend(json.loads(data)["timeperiods"])
        response = requests.Response()
        response.status_code = 200
        response._content = b"[[]]"
        return response

    monkeypatch.setattr(client_module.req, "post", post)
    query_file = tmp_path / "query.awq"
    query_file.write_text("RETURN = [];")

    args = ["query", "--start", "2025-08-25 12:36:00", "--stop", "2025-08-26"]
    result = CliRunner().invoke(main, [*args, str(query_file)])

    assert result.exit_code == 0, repr(result.exception)
    assert timeperiods == ["2025-08-25T12:36:00+05:30/2025-08-26T00:00:00+05:30"]
