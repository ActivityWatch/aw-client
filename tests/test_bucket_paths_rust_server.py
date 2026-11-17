"""Literal bucket-id routing against the default server, aw-server-rust.

Opt-in: set ``AW_SERVER_RUST_BIN`` to an ``aw-server`` binary built from
aw-server-rust. The server runs in ``--testing`` mode on a throwaway port and
datastore, so no real data is touched.
"""

import os
import shutil
import subprocess
import tempfile
import time
from datetime import datetime, timedelta, timezone

import pytest
import requests
from aw_core.models import Event

from aw_client import ActivityWatchClient

from test_bucket_paths import BUCKET_IDS

BIN = os.environ.get("AW_SERVER_RUST_BIN")
PORT = int(os.environ.get("AW_SERVER_RUST_PORT", "5699"))
START = datetime(2026, 1, 2, 3, 4, 5, tzinfo=timezone.utc)
END = START + timedelta(minutes=1)

pytestmark = pytest.mark.skipif(
    not BIN, reason="AW_SERVER_RUST_BIN not set (aw-server-rust binary)"
)


@pytest.fixture(scope="module")
def rust_server():
    assert BIN
    tmp = tempfile.mkdtemp(prefix="aw-rust-bucket-paths-")
    env = dict(os.environ)
    env["XDG_DATA_HOME"] = env["XDG_CONFIG_HOME"] = env["XDG_CACHE_HOME"] = tmp
    env["HOME"] = tmp
    proc = subprocess.Popen(
        [BIN, "--testing", "--port", str(PORT), "--dbpath", f"{tmp}/test.db"],
        env=env,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    try:
        for _ in range(100):
            try:
                requests.get(f"http://127.0.0.1:{PORT}/api/0/info", timeout=1)
                break
            except requests.RequestException:
                time.sleep(0.2)
        else:
            pytest.fail("aw-server-rust did not start")
        yield PORT
    finally:
        proc.terminate()
        proc.wait(timeout=10)
        shutil.rmtree(tmp, ignore_errors=True)


@pytest.mark.parametrize("bucket_id", BUCKET_IDS)
def test_rust_server_keeps_buckets_distinct(rust_server, bucket_id):
    client = ActivityWatchClient(
        "bucket-paths-test", testing=True, port=rust_server, host="127.0.0.1"
    )
    unrelated = {"bucket", "bucket#escape?%A", "sentinel"} - {bucket_id}
    for sentinel in unrelated:
        client.create_bucket(sentinel, "test-event")
        client.insert_event(
            sentinel, Event(timestamp=START, duration=1, data={"sentinel": sentinel})
        )
    before = {s: client.export_bucket(s) for s in unrelated}
    client.create_bucket(bucket_id, "test-event")
    assert bucket_id in client.get_buckets()
    event = Event(timestamp=START, duration=1, data={"literal_id": bucket_id})
    inserted = client.insert_event(bucket_id, event)
    assert inserted is not None
    assert client.get_events(bucket_id, start=START, end=END)[0].data == event.data
    assert client.get_eventcount(bucket_id, start=START, end=END) == 1
    assert bucket_id in client.export_bucket(bucket_id)["buckets"]
    client.heartbeat(bucket_id, event, pulsetime=12.5)
    client.delete_bucket(bucket_id, force=True)
    assert bucket_id not in client.get_buckets()
    assert {s: client.export_bucket(s) for s in unrelated} == before
