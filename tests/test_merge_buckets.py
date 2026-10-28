"""Regressions for examples/merge_buckets.py hostname-change recovery.

The example must copy source events without their IDs so Python backends
insert instead of replace, then verify destination content before deleting
the source. These tests drive the actual example main over real aw-core
datastores with an in-process client adapter (no HTTP server).
"""

from __future__ import annotations

import contextlib
import importlib.util
import io
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Iterator
from unittest.mock import patch

import pytest
from aw_core.models import Event
from aw_datastore import Datastore
from aw_datastore.storages import MemoryStorage, PeeweeStorage, SqliteStorage
from aw_datastore.storages.abstract import AbstractStorage

T0 = datetime(2026, 10, 1, tzinfo=timezone.utc)
STORAGES: list[type[AbstractStorage]] = [MemoryStorage, SqliteStorage, PeeweeStorage]


def _load_merge():
    path = Path(__file__).resolve().parents[1] / "examples" / "merge_buckets.py"
    spec = importlib.util.spec_from_file_location("merge_buckets_example", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


merge = _load_merge()


def _storage_kwargs(storage: type[AbstractStorage], tmp_path: Path) -> dict:
    if storage is MemoryStorage:
        return {}
    return {"filepath": str(tmp_path / "probe.db")}


def _seed(ds: Datastore) -> None:
    for bid in ("source", "destination"):
        ds.create_bucket(bid, "currentwindow", "probe", "host")
    for i in range(2):
        ds["source"].insert(
            Event(
                timestamp=T0 + timedelta(minutes=i),
                duration=30,
                data={"title": f"source-{i}"},
            )
        )
        ds["destination"].insert(
            Event(
                timestamp=T0 + timedelta(hours=1, minutes=i),
                duration=30,
                data={"title": f"destination-{i}"},
            )
        )


class StorageClient:
    """Mirrors ActivityWatchClient.insert_events' JSON roundtrip into a datastore."""

    def __init__(self, ds: Datastore):
        self.ds = ds
        self.insert_calls = 0
        self.fail_insert = False
        self.noop_insert = False

    def get_buckets(self):
        return self.ds.buckets()

    def get_events(self, bid: str) -> list[Event]:
        return self.ds[bid].get()

    def insert_events(self, bid: str, events: list[Event]):
        self.insert_calls += 1
        if self.fail_insert:
            raise OSError("synthetic write rejection")
        if self.noop_insert:
            return None
        self.ds[bid].insert([Event(**event.to_json_dict()) for event in events])
        return None

    def delete_bucket(self, bid: str, force: bool = False) -> None:
        self.ds.delete_bucket(bid)


def _run_main(client: StorageClient, answers: Iterator[str]) -> str:
    out = io.StringIO()
    with patch.object(
        merge.aw_client, "ActivityWatchClient", lambda **kwargs: client
    ), patch(
        "builtins.input", side_effect=lambda *args: next(answers)
    ), contextlib.redirect_stdout(out):
        merge.main()
    return out.getvalue()


@pytest.mark.parametrize("storage", STORAGES)
def test_merge_preserves_both_histories_then_deletes_source(storage, tmp_path):
    ds = Datastore(storage, testing=True, **_storage_kwargs(storage, tmp_path))
    _seed(ds)
    source_ids_before = {event.id for event in ds["source"].get()}
    client = StorageClient(ds)
    output = _run_main(client, iter(["source", "destination", "y", "y"]))
    after = ds["destination"].get()
    titles = sorted(event.data["title"] for event in after)
    assert titles == [
        "destination-0",
        "destination-1",
        "source-0",
        "source-1",
    ]
    assert len(after) == 4
    assert "Operation complete" in output
    assert "source" not in ds.buckets()
    copied = [
        event for event in after if str(event.data["title"]).startswith("source-")
    ]
    assert source_ids_before
    assert {event.id for event in copied}.isdisjoint(source_ids_before)
    assert all(event.id is not None for event in after)


@pytest.mark.parametrize("storage", STORAGES)
def test_overlap_aborts_without_insert_or_delete(storage, tmp_path):
    ds = Datastore(storage, testing=True, **_storage_kwargs(storage, tmp_path))
    _seed(ds)
    overlapping = Event(
        timestamp=T0 + timedelta(hours=1),
        duration=30,
        data={"title": "overlap"},
    )
    ds["source"].insert(overlapping)
    client = StorageClient(ds)
    with pytest.raises(SystemExit) as exited:
        _run_main(client, iter(["source", "destination"]))
    assert exited.value.code == 1
    assert client.insert_calls == 0
    assert "source" in ds.buckets()
    assert len(ds["destination"].get()) == 2


@pytest.mark.parametrize("storage", STORAGES)
def test_insert_failure_retains_source(storage, tmp_path):
    ds = Datastore(storage, testing=True, **_storage_kwargs(storage, tmp_path))
    _seed(ds)
    client = StorageClient(ds)
    client.fail_insert = True
    output = _run_main(client, iter(["source", "destination", "y"]))
    assert (
        "source bucket retained" in output.lower()
        or "insertion failed" in output.lower()
    )
    assert "source" in ds.buckets()
    assert len(ds["source"].get()) == 2
    assert len(ds["destination"].get()) == 2
    assert "Bucket deleted" not in output


@pytest.mark.parametrize("storage", STORAGES)
def test_unverified_insert_retains_source(storage, tmp_path):
    ds = Datastore(storage, testing=True, **_storage_kwargs(storage, tmp_path))
    _seed(ds)
    client = StorageClient(ds)
    client.noop_insert = True
    output = _run_main(client, iter(["source", "destination", "y"]))
    assert "source" in ds.buckets()
    assert len(ds["destination"].get()) == 2
    assert "Bucket deleted" not in output
    assert "Operation complete" not in output


def test_copy_events_without_ids_does_not_mutate_callers():
    original = Event(
        id=7,
        timestamp=T0,
        duration=30,
        data={"title": "keep"},
    )
    copies = merge.copy_events_without_ids([original])
    assert original.id == 7
    assert original.data["title"] == "keep"
    assert len(copies) == 1
    assert copies[0].id is None
    assert copies[0] == original  # content equality ignores id
    copies[0].data["title"] = "changed"
    assert original.data["title"] == "keep"
