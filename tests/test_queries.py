"""Tests for the multidevice query helpers in aw_client.queries.

The generated queries are executed with aw-core's query2 engine against an
in-memory datastore, so they are checked end to end without a running server.
"""

from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Tuple

import pytest
from aw_core.models import Event
from aw_datastore import Datastore
from aw_datastore.storages import MemoryStorage
from aw_query import query2

from aw_client.queries import (
    AndroidQueryParams,
    DesktopQueryParams,
    canonicalMultideviceEvents,
    isAndroidParams,
    isDesktopParams,
    multideviceHostParams,
)

T0 = datetime(2026, 1, 1, 12, 0, tzinfo=timezone.utc)
CLASSES = [(["Work"], {"type": "regex", "regex": "code"})]


def _t(minutes: float) -> datetime:
    return T0 + timedelta(minutes=minutes)


Span = Tuple[float, float, Dict[str, Any]]


def _insert(ds: Datastore, bid: str, btype: str, host: str, spans: List[Span]):
    bucket = ds.create_bucket(bid, btype, "test-client", host)
    for start, end, data in spans:
        bucket.insert(
            Event(
                timestamp=_t(start), duration=timedelta(minutes=end - start), data=data
            )
        )


def _run(ds: Datastore, query: str) -> List[dict]:
    return query2.query("test", query, _t(-60), _t(24 * 60), ds)


def _minutes(events: List[dict]) -> float:
    return sum(e["duration"].total_seconds() for e in events) / 60


def _assert_no_overlap(events: List[dict]):
    events = sorted(events, key=lambda e: e["timestamp"])
    for a, b in zip(events, events[1:]):
        assert a["timestamp"] + a["duration"] <= b["timestamp"]


@pytest.fixture
def datastore():
    return Datastore(MemoryStorage, testing=True)


def _buckets(ds: Datastore) -> Dict[str, Dict[str, Any]]:
    """Bucket metadata in the shape returned by ActivityWatchClient.get_buckets()."""
    buckets = ds.buckets()
    for bid, bucket in buckets.items():
        bucket.setdefault("last_updated", None)
        last = ds[bid].get(limit=1)
        if last:
            bucket["last_updated"] = (last[0].timestamp + last[0].duration).isoformat()
    return buckets


def test_desktop_and_android_union(datastore):
    ds = datastore
    # Desktop: 60 min of window events, but only the first 40 min are not-afk.
    _insert(
        ds,
        "aw-watcher-window_desk",
        "currentwindow",
        "desk",
        [(0, 60, {"app": "code", "title": "x"})],
    )
    _insert(
        ds,
        "aw-watcher-afk_desk",
        "afkstatus",
        "desk",
        [(0, 40, {"status": "not-afk"}), (40, 60, {"status": "afk"})],
    )
    # Phone (synced, no afk bucket): 30-50 overlaps desktop's active time for
    # 10 min, then fills 10 min of the desktop's afk time. 100-110 is disjoint.
    _insert(
        ds,
        "aw-watcher-android-synced-from-phone",
        "currentwindow",
        "phone",
        [(30, 50, {"app": "Chat"}), (100, 110, {"app": "Chat"})],
    )

    host_params = multideviceHostParams(_buckets(ds), classes=CLASSES)
    assert [type(p) for p in host_params] == [DesktopQueryParams, AndroidQueryParams]

    query = canonicalMultideviceEvents(host_params) + "\nRETURN = events;"
    events = _run(ds, query)

    _assert_no_overlap(events)
    # desk 0-40 (40) + phone 40-50 (10) + phone 100-110 (10)
    assert _minutes(events) == pytest.approx(60)
    assert _minutes([e for e in events if e["data"]["app"] == "code"]) == pytest.approx(
        40
    )
    assert all("$category" in e["data"] for e in events)


def test_android_events_not_merged_before_union(datastore):
    """Merged Android events would keep only their first timestamp and claim
    time the phone was not in use (here: the desktop's active time)."""
    ds = datastore
    _insert(
        ds,
        "aw-watcher-window_desk",
        "currentwindow",
        "desk",
        [(10, 100, {"app": "code", "title": "x"})],
    )
    _insert(
        ds,
        "aw-watcher-afk_desk",
        "afkstatus",
        "desk",
        [(10, 100, {"status": "not-afk"})],
    )
    # Same app used twice on the phone, before and after the desktop session.
    _insert(
        ds,
        "aw-watcher-android-synced-from-phone",
        "currentwindow",
        "phone",
        [(0, 10, {"app": "Chat"}), (100, 110, {"app": "Chat"})],
    )

    host_params = multideviceHostParams(_buckets(ds), classes=CLASSES)
    events = _run(ds, canonicalMultideviceEvents(host_params) + "\nRETURN = events;")
    assert _minutes(events) == pytest.approx(110)
    assert _minutes([e for e in events if e["data"]["app"] == "Chat"]) == pytest.approx(
        20
    )


def test_host_priority_order(datastore):
    ds = datastore
    for host in ["a", "b"]:
        _insert(
            ds,
            f"aw-watcher-window_{host}",
            "currentwindow",
            host,
            [(0, 30, {"app": f"app-{host}", "title": ""})],
        )
        _insert(
            ds,
            f"aw-watcher-afk_{host}",
            "afkstatus",
            host,
            [(0, 30, {"status": "not-afk"})],
        )
    buckets = _buckets(ds)

    for order in (["a", "b"], ["b", "a"]):
        host_params = multideviceHostParams(buckets, hosts=order, classes=CLASSES)
        events = _run(
            ds, canonicalMultideviceEvents(host_params) + "\nRETURN = events;"
        )
        assert {e["data"]["app"] for e in events} == {f"app-{order[0]}"}
        assert _minutes(events) == pytest.approx(30)


def test_not_afk_is_combined(datastore):
    ds = datastore
    _insert(
        ds,
        "aw-watcher-window_desk",
        "currentwindow",
        "desk",
        [(0, 60, {"app": "code", "title": "x"})],
    )
    _insert(
        ds,
        "aw-watcher-afk_desk",
        "afkstatus",
        "desk",
        [(0, 20, {"status": "not-afk"}), (20, 60, {"status": "afk"})],
    )
    _insert(
        ds,
        "aw-watcher-android_phone",
        "currentwindow",
        "phone",
        [(30, 40, {"app": "Chat"})],
    )
    host_params = multideviceHostParams(_buckets(ds), classes=CLASSES)
    not_afk = _run(ds, canonicalMultideviceEvents(host_params) + "\nRETURN = not_afk;")
    assert _minutes(not_afk) == pytest.approx(30)


def test_empty_host_list():
    assert "events = [];" in canonicalMultideviceEvents([])


def _meta(
    btype: str, hostname: Optional[str], last_updated: str = "2026-01-01"
) -> dict:
    return {"type": btype, "hostname": hostname, "last_updated": last_updated}


def test_discovery_from_bucket_metadata():
    buckets = {
        # Local desktop host
        "aw-watcher-window_laptop.localdomain": _meta(
            "currentwindow", "laptop.localdomain", "2026-09-01"
        ),
        "aw-watcher-afk_laptop.localdomain": _meta(
            "afkstatus", "laptop.localdomain", "2026-09-01"
        ),
        # Similar-named older host (must not be confused with the one above)
        "aw-watcher-window_laptop.local": _meta(
            "currentwindow", "laptop.local", "2023-01-01"
        ),
        "aw-watcher-afk_laptop.local": _meta("afkstatus", "laptop.local", "2023-01-01"),
        # Desktop host synced via aw-sync
        "aw-watcher-window_desktop-synced-from-desktop": _meta(
            "currentwindow", "desktop", "2025-01-01"
        ),
        "aw-watcher-afk_desktop-synced-from-desktop": _meta(
            "afkstatus", "desktop", "2025-01-01"
        ),
        # Synced phone with a stray test bucket that must not be picked
        "aw-watcher-android-test-synced-from-phone": _meta(
            "currentwindow", "phone", "2026-09-20"
        ),
        "aw-watcher-android-synced-from-phone": _meta(
            "currentwindow", "phone", "2026-09-10"
        ),
        "aw-watcher-android-unlock-synced-from-phone": _meta(
            "os.lockscreen.unlocks", "phone"
        ),
        # Hosts that cannot be queried
        "aw-watcher-window_windowonly": _meta("currentwindow", "windowonly"),
        "aw-watcher-web-firefox": _meta("web.tab.current", "unknown"),
        "aw-stopwatch": _meta("general.stopwatch", "unknown"),
    }
    params = multideviceHostParams(
        buckets, filter_afk=False, always_active_pattern="zoom"
    )

    desktop = [p for p in params if isDesktopParams(p)]
    android = [p for p in params if isAndroidParams(p)]
    assert [p.bid_window for p in desktop] == [
        "aw-watcher-window_laptop.localdomain",
        "aw-watcher-window_desktop-synced-from-desktop",
        "aw-watcher-window_laptop.local",
    ]
    assert [p.bid_afk for p in desktop][
        1
    ] == "aw-watcher-afk_desktop-synced-from-desktop"
    assert [p.bid_android for p in android] == ["aw-watcher-android-synced-from-phone"]
    # Desktop hosts come first by default
    assert params[: len(desktop)] == desktop
    assert all(not p.filter_afk for p in params)
    assert all(p.always_active_pattern == "zoom" for p in desktop)

    # Explicit host list selects and orders, skipping hosts without buckets
    params = multideviceHostParams(buckets, hosts=["phone", "missing", "desktop"])
    assert [type(p) for p in params] == [AndroidQueryParams, DesktopQueryParams]


def test_discovery_prefers_local_over_synced_copy():
    buckets = {
        "aw-watcher-window_desk": _meta("currentwindow", "desk", "2026-01-01"),
        "aw-watcher-afk_desk": _meta("afkstatus", "desk", "2026-01-01"),
        "aw-watcher-window_desk-synced-from-desk": _meta(
            "currentwindow", "desk", "2026-01-02"
        ),
        "aw-watcher-afk_desk-synced-from-desk": _meta(
            "afkstatus", "desk", "2026-01-02"
        ),
    }
    (params,) = multideviceHostParams(buckets)
    assert isDesktopParams(params)
    assert params.bid_window == "aw-watcher-window_desk"
    assert params.bid_afk == "aw-watcher-afk_desk"


def test_discovery_falls_back_to_synced_from_suffix():
    buckets = {
        "aw-watcher-window_desk-synced-from-desk": {"type": "currentwindow"},
        "aw-watcher-afk_desk-synced-from-desk": {"type": "afkstatus"},
    }
    (params,) = multideviceHostParams(buckets)
    assert isDesktopParams(params)
    assert params.bid_window == "aw-watcher-window_desk-synced-from-desk"
