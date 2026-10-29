"""Bucket IDs are literal URL segments, even after requests prepares the URL.

Use a real localhost HTTP server: mocking requests would miss its percent-escape
normalization and the loss of endpoint suffixes to query strings/fragments.
"""

import json
import threading
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Dict, List, Tuple
from urllib.parse import parse_qs, quote, unquote, urlsplit

import pytest
from aw_core.models import Event

from aw_client import ActivityWatchClient
from aw_client import client as client_module


BUCKET_IDS = [
    "ordinary-bucket",
    "bucket#fragment",
    "bucket?force=1&other=query",
    "bucket%literal",
    "bucket%23escape%3F%25%41",
    "bucket-雪-é",
]
START = datetime(2026, 1, 2, 3, 4, 5, tzinfo=timezone.utc)
END = START + timedelta(minutes=1)


@pytest.fixture
def wire_client(monkeypatch, tmp_path):
    records = []
    responses: List[Any] = []

    class Handler(BaseHTTPRequestHandler):
        def handle_request(self):
            raw_body = self.rfile.read(int(self.headers.get("Content-Length", 0)))
            records.append(
                (self.command, self.path, json.loads(raw_body) if raw_body else None)
            )
            if responses and callable(responses[0]):
                status, body = responses[0](records[-1])
            else:
                status, body = responses.pop(0) if responses else (200, {})
            payload = json.dumps(body).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

        do_GET = do_POST = do_DELETE = handle_request

        def log_message(self, *args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(
        target=server.serve_forever, kwargs={"poll_interval": 0.05}, daemon=True
    )
    thread.start()
    # Isolate config, locks, API keys and the real persistent queue, not HTTP.
    monkeypatch.setattr(
        client_module,
        "load_config",
        lambda: {
            "server": {"hostname": "127.0.0.1", "port": server.server_port},
            "client": {"commit_interval": 10},
        },
    )
    monkeypatch.setattr(client_module, "SingleInstance", lambda name: object())
    monkeypatch.setattr(client_module, "get_data_dir", lambda *args: str(tmp_path))
    monkeypatch.setattr(
        client_module, "load_local_server_api_key", lambda *a, **kw: None
    )
    client = ActivityWatchClient(
        "bucket-path-test", host="127.0.0.1", port=server.server_port, profile="default"
    )
    try:
        yield client, records, responses
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def _assert_bucket_target(record, method, bucket_id, suffix, params):
    actual_method, target, _body = record
    parsed = urlsplit(target)
    assert actual_method == method
    assert parsed.path == f"/api/0/buckets/{quote(bucket_id, safe='')}{suffix}"
    assert unquote(parsed.path.split("/")[4]) == bucket_id
    assert parse_qs(parsed.query) == params
    assert not parsed.fragment


# Every constructor is exercised separately, so a missed site cannot be hidden
# by a failure in an earlier operation on the same bucket.
OPERATIONS = [
    ("get_event", "GET", "/events/7"),
    ("get_events", "GET", "/events"),
    ("insert_event", "POST", "/events"),
    ("insert_events", "POST", "/events"),
    ("delete_event", "DELETE", "/events/7"),
    ("delete_events", "POST", "/events/delete"),
    ("get_eventcount", "GET", "/events/count"),
    ("create_bucket", "POST", ""),
    ("delete_bucket", "DELETE", ""),
    ("export_bucket", "GET", "/export"),
    ("heartbeat", "POST", "/heartbeat"),
]


@pytest.mark.parametrize("bucket_id", BUCKET_IDS)
@pytest.mark.parametrize("operation,method,suffix", OPERATIONS)
def test_immediate_bucket_endpoints(wire_client, bucket_id, operation, method, suffix):
    client, records, responses = wire_client
    event = Event(timestamp=START, duration=1, data={"literal_id": bucket_id})
    event_json = event.to_json_dict()
    params = {}
    body: Any = None
    response: Any = {}
    args: Tuple[Any, ...] = ()
    kwargs: Dict[str, Any] = {}
    if operation in {"get_event", "delete_event"}:
        args = (7,)
        response = event_json
    elif operation in {"get_events", "get_eventcount"}:
        kwargs = {"start": START, "end": END}
        params = {"start": [START.isoformat()], "end": [END.isoformat()]}
        if operation == "get_events":
            kwargs["limit"] = 3
            params["limit"] = ["3"]
            response = [event_json]
        else:
            response = 1
    elif operation in {"insert_event", "insert_events"}:
        args = (event if operation == "insert_event" else [event],)
        body = response = [event_json]
    elif operation == "delete_events":
        args = ([7, 8],)
        body = {"ids": [7, 8]}
        response = 2
    elif operation == "create_bucket":
        args = ("test-event",)
        body = {
            "client": client.client_name,
            "hostname": client.client_hostname,
            "type": "test-event",
        }
    elif operation == "delete_bucket":
        kwargs = {"force": True}
        params = {"force": ["1"]}
    elif operation == "heartbeat":
        args = (event,)
        kwargs = {"pulsetime": 12.5}
        params = {"pulsetime": ["12.5"]}
        body = event_json
    if method == "DELETE":
        body = {}
    responses.append((200, response))

    result = getattr(client, operation)(bucket_id, *args, **kwargs)

    assert len(records) == 1
    _assert_bucket_target(records[0], method, bucket_id, suffix, params)
    assert records[0][2] == body
    if operation == "get_eventcount":
        assert result == 1
    elif operation == "delete_events":
        assert result == 2
    elif operation == "get_event":
        assert result.data == {"literal_id": bucket_id}
    elif operation in {"get_events", "insert_events"}:
        assert result[0].data == {"literal_id": bucket_id}
    elif operation == "insert_event":
        assert result.data == {"literal_id": bucket_id}


@pytest.mark.parametrize("bucket_id", BUCKET_IDS)
def test_queued_heartbeat_retries_literal_bucket(wire_client, bucket_id):
    client, records, responses = wire_client
    queue = client.request_queue
    # Drive the real queue deterministically without a background dispatcher.
    with pytest.warns(UserWarning, match="connect"):
        client.create_bucket(bucket_id, "test-event", queued=True)
    assert queue._registered_buckets[0].id == bucket_id
    assert queue._try_connect()
    first = Event(timestamp=START, duration=1, data={"literal_id": bucket_id})
    second = Event(timestamp=END, duration=1, data={"next": bucket_id})
    client.heartbeat(bucket_id, first, pulsetime=12.5, queued=True)
    client.heartbeat(bucket_id, second, pulsetime=12.5, queued=True)
    assert set(client.last_heartbeat) == {bucket_id}
    assert queue._persistqueue.qsize() == 1
    responses.extend([(503, {"message": "retry"}), (200, {})])

    queue._dispatch_request()
    assert queue._current is not None
    assert not queue.wait_for_queue_empty(timeout=0)
    queue._dispatch_request()
    assert queue._current is None
    assert queue._persistqueue.qsize() == 0

    assert len(records) == 3
    _assert_bucket_target(records[0], "POST", bucket_id, "", {})
    for record in records[1:]:
        _assert_bucket_target(
            record, "POST", bucket_id, "/heartbeat", {"pulsetime": ["12.5"]}
        )
        assert record[2] == first.to_json_dict()
    assert records[1] == records[2]


@pytest.mark.parametrize("bucket_id", BUCKET_IDS)
def test_delete_bucket_does_not_add_force_by_default(wire_client, bucket_id):
    client, records, _responses = wire_client
    client.delete_bucket(bucket_id)
    _assert_bucket_target(records[0], "DELETE", bucket_id, "", {})


@pytest.mark.parametrize("bucket_id", BUCKET_IDS)
def test_import_keeps_literal_bucket_id_in_body(wire_client, bucket_id):
    client, records, _responses = wire_client
    bucket = {"id": bucket_id, "type": "test-event", "events": []}
    client.import_bucket(bucket)
    assert records == [("POST", "/api/0/import", {"buckets": {bucket_id: bucket}})]


@pytest.fixture
def memory_server(wire_client, monkeypatch):
    # Optional integration dependencies must not skip the standalone wire suite.
    server_module = pytest.importorskip("aw_server.server")
    storages = pytest.importorskip("aw_datastore.storages")
    monkeypatch.delenv("AW_PROFILE", raising=False)
    app = server_module.AWFlask(
        "127.0.0.1",
        testing=True,
        storage_method=storages.MemoryStorage,
        query_cache=False,
    )
    flask_client = app.test_client()
    client, _records, responses = wire_client
    # Independently establish server support before exercising SDK routing.
    for bucket_id in BUCKET_IDS:
        route = f"/api/0/buckets/{quote(bucket_id, safe='')}"
        assert (
            flask_client.post(
                route,
                json={"type": "test-event", "client": "control", "hostname": "fixture"},
            ).status_code
            == 200
        )
        assert bucket_id in flask_client.get("/api/0/buckets/").get_json()
        assert flask_client.delete(route + "?force=1").status_code == 200

    def forward(record):
        method, target, body = record
        response = flask_client.open(target, method=method, json=body)
        # Existing buckets return 304 with no body. Do not forward attachment
        # headers: Unicode filenames are unrelated to path-segment routing.
        payload = response.get_data(as_text=True)
        return response.status_code, json.loads(payload) if payload else None

    responses.append(forward)
    return client


@pytest.mark.parametrize("bucket_id", BUCKET_IDS)
def test_memory_server_keeps_buckets_distinct(memory_server, bucket_id):
    client = memory_server
    # These sentinels catch fragment/query truncation and percent decoding into
    # another real bucket, not just the happy-path response of the target.
    unrelated = {"bucket", "bucket#escape?%A", "sentinel"} - {bucket_id}
    for sentinel in unrelated:
        client.create_bucket(sentinel, "test-event")
        client.insert_event(
            sentinel, Event(timestamp=START, duration=1, data={"sentinel": sentinel})
        )
    before = {sentinel: client.export_bucket(sentinel) for sentinel in unrelated}
    client.create_bucket(bucket_id, "test-event")
    assert bucket_id in client.get_buckets()
    event = Event(timestamp=START, duration=1, data={"literal_id": bucket_id})
    inserted = client.insert_event(bucket_id, event)
    assert inserted is not None
    assert client.get_event(bucket_id, inserted.id).data == event.data
    assert client.get_events(bucket_id, start=START, end=END)[0].data == event.data
    assert client.get_eventcount(bucket_id, start=START, end=END) == 1
    assert bucket_id in client.export_bucket(bucket_id)["buckets"]
    client.delete_event(bucket_id, inserted.id)
    inserted_many = client.insert_events(bucket_id, [event])
    assert inserted_many
    assert client.delete_events(bucket_id, [inserted_many[0].id]) == 1
    client.heartbeat(bucket_id, event, pulsetime=12.5)
    assert client.get_eventcount(bucket_id) == 1
    with pytest.warns(UserWarning, match="connect"):
        client.setup_bucket(bucket_id, "test-event")
    assert client.request_queue._try_connect()
    client.heartbeat(bucket_id, event, pulsetime=12.5, queued=True)
    client.heartbeat(
        bucket_id,
        Event(timestamp=END, duration=1, data={"next": bucket_id}),
        pulsetime=12.5,
        queued=True,
    )
    client.request_queue._dispatch_request()
    assert client.request_queue._current is None
    assert client.get_eventcount(bucket_id) == 1
    client.delete_bucket(bucket_id, force=True)
    assert bucket_id not in client.get_buckets()
    assert {
        sentinel: client.export_bucket(sentinel) for sentinel in unrelated
    } == before
