"""Configured URLs must use the same destination for requests, auth and proxies."""

from typing import Any, Dict

import pytest
import tomlkit

from aw_client import ActivityWatchClient
from aw_client import client as client_module
from aw_client.config import default_config


@pytest.fixture
def configured_client(monkeypatch, tmp_path):
    config = tomlkit.parse(default_config)
    monkeypatch.setattr(client_module, "load_config", lambda: config)
    monkeypatch.setattr(client_module, "SingleInstance", lambda name: name)
    monkeypatch.setattr(client_module, "get_data_dir", lambda *args: str(tmp_path))
    monkeypatch.delenv("AW_PROFILE", raising=False)
    return config


def test_https_baseurl_controls_request_auth_and_proxy(configured_client, monkeypatch):
    configured_client["server"]["baseurl"] = "https://aw.example.com/activitywatch/"
    auth_destinations = []

    def local_key(host, port, profile=None):
        auth_destinations.append((host, port))
        return "local-secret" if host == "127.0.0.1" else None

    monkeypatch.setattr(client_module, "load_local_server_api_key", local_key)
    captured: Dict[str, Any] = {}

    def fake_get(url, **kwargs):
        captured.update(url=url, **kwargs)
        response = client_module.req.Response()
        response.status_code = 200
        response._content = b'{"hostname": "remote"}'
        return response

    monkeypatch.setattr(client_module.req, "get", fake_get)
    client = ActivityWatchClient("baseurl-test")
    assert client.get_info() == {"hostname": "remote"}
    assert captured["url"] == "https://aw.example.com/activitywatch/api/0/info"
    assert captured["headers"] == {}
    assert captured["proxies"] is None
    assert auth_destinations == [("aw.example.com", 443)]
    assert str(client.instance).startswith("baseurl-test-at-url-")


def test_loopback_baseurl_uses_its_own_port(configured_client, monkeypatch):
    configured_client["server"]["baseurl"] = "http://127.0.0.1:8123"
    seen = []

    def local_key(host, port, profile=None):
        seen.append((host, port))
        return "local-key"

    monkeypatch.setattr(client_module, "load_local_server_api_key", local_key)
    client = ActivityWatchClient("baseurl-test")
    assert seen == [("127.0.0.1", 8123)]
    assert client._headers() == {"Authorization": "Bearer local-key"}
    assert client._proxies == client_module._NO_PROXIES


@pytest.mark.parametrize("kwargs", [{"host": "explicit.example"}, {"port": 9876}])
def test_explicit_destination_overrides_configured_url(configured_client, kwargs):
    configured_client["server"]["baseurl"] = "https://configured.example"
    client = ActivityWatchClient("baseurl-test", **kwargs)
    expected_host = kwargs.get("host", "127.0.0.1")
    expected_port = kwargs.get("port", "5600")
    assert client.server_address == f"http://{expected_host}:{expected_port}"


def test_baseurl_is_profile_specific(configured_client):
    configured_client["server"]["baseurl"] = "https://default.example"
    configured_client["server-testing"]["baseurl"] = "https://testing.example:8443"
    client = ActivityWatchClient("baseurl-test", testing=True)
    assert client._url("info") == "https://testing.example:8443/api/0/info"


@pytest.mark.parametrize("value", [None, ""])
def test_absent_or_empty_baseurl_preserves_default(configured_client, value):
    if value is not None:
        configured_client["server"]["baseurl"] = value
    client = ActivityWatchClient("baseurl-test")
    assert client.server_address == "http://127.0.0.1:5600"


@pytest.mark.parametrize(
    "baseurl",
    [
        "aw.example.com",
        "ftp://aw.example.com",
        "https:///missing-host",
        "https://aw.example.com:bad",
        "https://user:password@aw.example.com",
        "https://aw.example.com?query=1",
        "https://aw.example.com#fragment",
    ],
)
def test_invalid_baseurl_is_rejected(configured_client, baseurl):
    configured_client["server"]["baseurl"] = baseurl
    with pytest.raises(ValueError, match="baseurl"):
        ActivityWatchClient("baseurl-test")


@pytest.mark.parametrize(
    "args, expected",
    [
        (["buckets"], "https://configured.example"),
        (["--host", "explicit.example", "buckets"], "http://explicit.example:5600"),
        (["--port", "9876", "buckets"], "http://127.0.0.1:9876"),
    ],
)
def test_cli_honors_baseurl_unless_explicitly_overridden(
    configured_client, monkeypatch, args, expected
):
    from click.testing import CliRunner

    from aw_client.cli import main

    configured_client["server"]["baseurl"] = "https://configured.example"
    addresses = []

    def get_buckets(self):
        addresses.append(self.server_address)
        return {}

    monkeypatch.setattr(ActivityWatchClient, "get_buckets", get_buckets)
    result = CliRunner().invoke(main, args)
    assert result.exit_code == 0, result.output
    assert addresses == [expected]


@pytest.mark.parametrize(
    "baseurl, expected_key",
    [
        ("https://aw.example.com", None),
        ("http://127.0.0.1:5600", "local-secret"),
        ("http://127.0.0.1:8123", None),
        ("http://127.0.0.1:0", None),
    ],
)
def test_actual_key_discovery_uses_url_destination(
    configured_client, monkeypatch, tmp_path, baseurl, expected_key
):
    from aw_client import config as config_module

    server_config = tmp_path / "config.toml"
    server_config.write_text('port = 5600\n[auth]\napi_key = "local-secret"\n')
    monkeypatch.setattr(
        config_module,
        "rust_server_config_candidates",
        lambda profile: [(str(server_config), 5600)],
    )
    configured_client["server"]["baseurl"] = baseurl
    client = ActivityWatchClient("baseurl-test")
    assert client.server_api_key == expected_key


def test_url_lock_names_are_path_specific_and_filename_safe(configured_client):
    configured_client["server"]["baseurl"] = "https://[::1]:8123/first"
    first = ActivityWatchClient("baseurl-test")
    configured_client["server"]["baseurl"] = "https://[::1]:8123/second"
    second = ActivityWatchClient("baseurl-test")
    assert first.instance != second.instance
    assert ":" not in str(first.instance)
    assert "/" not in str(first.instance)
    assert first._url("info") == "https://[::1]:8123/first/api/0/info"


def test_protocol_does_not_override_configured_baseurl(configured_client):
    configured_client["server"]["baseurl"] = "https://configured.example"
    client = ActivityWatchClient("baseurl-test", protocol="http")
    assert client.server_address == "https://configured.example"


@pytest.mark.parametrize(
    "baseurl, expected_port",
    [
        ("https://research.example/aw/", 443),
        ("https://research.example:8443/aw/", 8443),
    ],
)
def test_named_profile_with_only_baseurl(
    configured_client, monkeypatch, baseurl, expected_port
):
    configured_client["server-research"] = {"baseurl": baseurl}
    destinations = []

    def local_key(host, port, profile=None):
        destinations.append((host, port, profile))
        return None

    monkeypatch.setattr(client_module, "load_local_server_api_key", local_key)
    client = ActivityWatchClient("baseurl-test", profile="research")
    assert client._url("info") == baseurl.rstrip("/") + "/api/0/info"
    assert destinations == [("research.example", expected_port, "research")]
    assert client._proxies is None
    assert client.commit_interval == configured_client["client"]["commit_interval"]


@pytest.mark.parametrize(
    "kwargs, expected",
    [
        ({"host": "explicit.example"}, "http://explicit.example:5600"),
        ({"port": 9876}, "http://127.0.0.1:9876"),
    ],
)
def test_url_only_profile_allows_explicit_override(configured_client, kwargs, expected):
    configured_client["server-research"] = {"baseurl": "https://research.example/aw"}
    client = ActivityWatchClient("baseurl-test", profile="research", **kwargs)
    assert client.server_address == expected


def test_named_profile_fallback_keeps_default_server_url(configured_client):
    configured_client["server"]["baseurl"] = "https://configured.example"
    client = ActivityWatchClient("baseurl-test", profile="research")
    assert client.server_address == "https://configured.example"
