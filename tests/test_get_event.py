import pytest
import requests

from aw_client import ActivityWatchClient
from aw_client import client as client_module


def _response(status_code: int, body: bytes = b"{}") -> requests.Response:
    r = requests.Response()
    r.status_code = status_code
    r._content = body
    r.url = "http://127.0.0.1:5600/api/0/buckets/b/events/1"
    return r


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setattr(client_module, "SingleInstance", lambda name: object())
    monkeypatch.setattr(
        client_module, "load_local_server_api_key", lambda *a, **kw: None
    )
    return ActivityWatchClient("test-client", host="127.0.0.1", port=5600)


def test_get_event_returns_none_on_404(client, monkeypatch):
    monkeypatch.setattr(client_module.req, "get", lambda *a, **kw: _response(404))
    assert client.get_event("b", 1) is None


def test_get_event_raises_on_other_errors(client, monkeypatch):
    monkeypatch.setattr(client_module.req, "get", lambda *a, **kw: _response(500))
    with pytest.raises(requests.exceptions.HTTPError):
        client.get_event("b", 1)
