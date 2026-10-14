from aw_client import ActivityWatchClient
from aw_client import client as client_module


class DummyResponse:
    def __init__(self, data):
        self._data = data

    def raise_for_status(self) -> None:
        return None

    def json(self):
        return self._data


def test_delete_events_posts_ids_and_returns_count(monkeypatch):
    # Keep this HTTP-only test off the user's config, lock and queue files.
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

    captured = {}

    def fake_post(url, data=None, headers=None, **kwargs):
        captured["url"] = url
        captured["data"] = data
        return DummyResponse(2)

    monkeypatch.setattr(client_module.req, "post", fake_post)

    client = ActivityWatchClient("test-client", host="127.0.0.1", port=5600)
    assert client.delete_events("test-bucket", [1, 2, 99]) == 2
    assert (
        captured["url"]
        == "http://127.0.0.1:5600/api/0/buckets/test-bucket/events/delete"
    )
    assert captured["data"] == b'{"ids": [1, 2, 99]}'
