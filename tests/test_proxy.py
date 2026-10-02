import http.server
import json
import threading

import pytest
import requests

from aw_client import ActivityWatchClient
from aw_client import client as client_module

DEAD_PROXY = "http://127.0.0.1:9"


class _InfoHandler(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        body = json.dumps({"hostname": "stub", "testing": True}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self):
        self._record_and_ok()

    def do_DELETE(self):
        self._record_and_ok()

    def _record_and_ok(self):
        length = int(self.headers.get("Content-Length") or 0)
        self.rfile.read(length)
        self.server.requests.append((self.command, self.path))  # type: ignore[attr-defined]
        self.send_response(200)
        self.send_header("Content-Length", "0")
        self.end_headers()

    def log_message(self, *args):
        pass


@pytest.fixture
def stub_server():
    server = http.server.HTTPServer(("127.0.0.1", 0), _InfoHandler)
    server.requests = []  # type: ignore[attr-defined]
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield server
    server.shutdown()
    server.server_close()


@pytest.fixture
def dead_proxy_env(tmp_path, monkeypatch):
    """A proxied environment where NO_PROXY lists localhost but not 127.0.0.1."""
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    monkeypatch.setattr(client_module, "SingleInstance", lambda name: object())
    # Delete lowercase variants before setting: env vars are case-insensitive
    # on Windows, so deleting after setting would remove the uppercase one too.
    for var in ("http_proxy", "https_proxy", "all_proxy", "no_proxy"):
        monkeypatch.delenv(var, raising=False)
    for var in ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY"):
        monkeypatch.setenv(var, DEAD_PROXY)
    monkeypatch.setenv("NO_PROXY", "localhost")


@pytest.mark.parametrize(
    "host",
    [
        "localhost",
        "LOCALHOST",
        "localhost.",
        "foo.localhost",
        "127.0.0.1",
        "127.1.2.3",
        "::1",
        "[::1]",
    ],
)
def test_is_loopback_host(host):
    assert client_module._is_loopback_host(host)


@pytest.mark.parametrize(
    "host",
    ["aw.example.com", "192.168.1.10", "10.0.0.1", "::2", "localhost.example.com"],
)
def test_is_not_loopback_host(host):
    assert not client_module._is_loopback_host(host)


@pytest.mark.parametrize("host", ["127.0.0.1", "localhost"])
def test_loopback_server_bypasses_env_proxy(dead_proxy_env, stub_server, host):
    port = stub_server.server_address[1]
    client = ActivityWatchClient("test-client", host=host, port=port)
    assert client.get_info()["hostname"] == "stub"
    # POST and DELETE go through the same bypass
    client.create_bucket("test-bucket", "test")
    client.delete_bucket("test-bucket")
    assert stub_server.requests == [
        ("POST", "/api/0/buckets/test-bucket"),
        ("DELETE", "/api/0/buckets/test-bucket"),
    ]


def test_remote_server_still_uses_env_proxy(dead_proxy_env):
    client = ActivityWatchClient("test-client", host="aw.example.com", port=5600)
    with pytest.raises(requests.exceptions.ProxyError):
        client.get_info()
