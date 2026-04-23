from typing import Any, Dict, Optional

from aw_client import ActivityWatchClient
from aw_client.config import load_local_server_api_key
from aw_client import client as client_module


class DummyResponse:
    def __init__(self, data: Optional[Dict[str, Any]] = None):
        self._data = data or {}

    def raise_for_status(self) -> None:
        return None

    def json(self) -> Dict[str, Any]:
        return self._data


def write_server_config(tmp_path, filename: str, content: str) -> None:
    config_dir = tmp_path / "activitywatch" / "aw-server-rust"
    config_dir.mkdir(parents=True, exist_ok=True)
    (config_dir / filename).write_text(content)


def test_load_local_server_api_key_matches_port(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    write_server_config(
        tmp_path,
        "config.toml",
        'port = 5601\n\n[auth]\napi_key = "secret123"\n',
    )
    write_server_config(
        tmp_path,
        "config-testing.toml",
        'port = 5666\n\n[auth]\napi_key = "testing-secret"\n',
    )

    assert load_local_server_api_key("127.0.0.1", 5601) == "secret123"
    assert load_local_server_api_key("localhost", "5666") == "testing-secret"
    assert load_local_server_api_key("::1", 5601) == "secret123"
    assert load_local_server_api_key("127.0.0.1", 5600) is None
    assert load_local_server_api_key("example.com", 5601) is None


def test_client_sends_authorization_header_for_local_server(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    write_server_config(
        tmp_path,
        "config.toml",
        'port = 5600\n\n[auth]\napi_key = "secret123"\n',
    )
    monkeypatch.setattr(client_module, "SingleInstance", lambda name: object())

    captured = {}

    def fake_get(url, params=None, headers=None):
        captured["url"] = url
        captured["headers"] = headers
        return DummyResponse({"hostname": "test-host", "testing": False})

    monkeypatch.setattr(client_module.req, "get", fake_get)

    client = ActivityWatchClient("test-client", host="127.0.0.1", port=5600)
    assert client.get_info()["hostname"] == "test-host"
    assert captured["url"] == "http://127.0.0.1:5600/api/0/info"
    assert captured["headers"]["Authorization"] == "Bearer secret123"


def test_client_skips_authorization_header_for_remote_server(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    write_server_config(
        tmp_path,
        "config.toml",
        'port = 5600\n\n[auth]\napi_key = "secret123"\n',
    )
    monkeypatch.setattr(client_module, "SingleInstance", lambda name: object())

    captured = {}

    def fake_get(url, params=None, headers=None):
        captured["headers"] = headers
        return DummyResponse({"hostname": "remote-host", "testing": False})

    monkeypatch.setattr(client_module.req, "get", fake_get)

    client = ActivityWatchClient("test-client", host="aw.example.com", port=5600)
    assert client.get_info()["hostname"] == "remote-host"
    assert "Authorization" not in captured["headers"]
