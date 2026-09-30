import json
from datetime import datetime, timezone
from typing import Any, Dict

from click.testing import CliRunner

from aw_client import cli, queries
from aw_client.classes import default_classes, get_classes
from aw_client.queries import _bucket_hostname
from aw_client.summary import build_summary, find_browser_buckets, format_summary

START = datetime(2026, 8, 17, 9, tzinfo=timezone.utc)
STOP = datetime(2026, 8, 17, 12, tzinfo=timezone.utc)


AGGREGATE_RESULT = {
    "active_seconds": 7200,
    "uncategorized_seconds": 1800,
    "category_events": [
        {"duration": 5400, "data": {"$category": ["Work", "Programming"]}},
        {"duration": 1800, "data": {"$category": ["Uncategorized"]}},
    ],
    "app_events": [
        {"duration": 5000, "data": {"app": "Code"}},
        {"duration": 2200, "data": {"app": "Firefox"}},
    ],
    "domain_events": [
        {"duration": 1200, "data": {"$domain": "github.com"}},
    ],
}


def test_find_browser_buckets_excludes_unattributed_buckets_by_default():
    buckets = {
        "chrome-laptop": {
            "id": "chrome-laptop",
            "type": "web.tab.current",
            "hostname": "laptop",
        },
        "firefox-legacy": {
            "id": "firefox-legacy",
            "type": "web.tab.current",
            "hostname": "unknown",
        },
        "chrome-desktop": {
            "id": "chrome-desktop",
            "type": "web.tab.current",
            "hostname": "desktop",
        },
        "window-laptop": {
            "id": "window-laptop",
            "type": "currentwindow",
            "hostname": "laptop",
        },
    }

    # A bucket with no usable hostname may belong to another machine on a shared
    # server, so it must not be folded into this host's summary by default.
    assert find_browser_buckets(buckets, "laptop") == ["chrome-laptop"]


def test_find_browser_buckets_includes_unattributed_buckets_when_opted_in():
    buckets = {
        "chrome-laptop": {
            "id": "chrome-laptop",
            "type": "web.tab.current",
            "hostname": "laptop",
        },
        "firefox-legacy": {
            "id": "firefox-legacy",
            "type": "web.tab.current",
            "hostname": "unknown",
        },
        "firefox-nohost": {
            "id": "firefox-nohost",
            "type": "web.tab.current",
        },
        "chrome-desktop": {
            "id": "chrome-desktop",
            "type": "web.tab.current",
            "hostname": "desktop",
        },
    }

    assert find_browser_buckets(buckets, "laptop", include_legacy=True) == [
        "chrome-laptop",
        "firefox-legacy",
        "firefox-nohost",
    ]
    # Opting in must still never pull in a bucket attributed to another host.
    assert "chrome-desktop" not in find_browser_buckets(
        buckets, "laptop", include_legacy=True
    )


def test_find_browser_buckets_attributes_synced_buckets_to_their_origin_host():
    buckets = {
        "chrome-laptop": {
            "id": "chrome-laptop",
            "type": "web.tab.current",
            "hostname": "laptop",
        },
        # aw-sync names the origin in the bucket ID suffix. This copy lost its
        # hostname metadata, so only the suffix can attribute it.
        "chrome-server-synced-from-laptop": {
            "id": "chrome-server-synced-from-laptop",
            "type": "web.tab.current",
        },
        "firefox-server-synced-from-desktop": {
            "id": "firefox-server-synced-from-desktop",
            "type": "web.tab.current",
            "hostname": "unknown",
        },
    }

    # A synced bucket belongs to the host it was synced FROM, so it is counted in
    # that host's summary even though the bucket lives on the serving machine.
    assert find_browser_buckets(buckets, "laptop") == [
        "chrome-laptop",
        "chrome-server-synced-from-laptop",
    ]
    assert find_browser_buckets(buckets, "desktop") == [
        "firefox-server-synced-from-desktop"
    ]
    # And the serving machine must not inherit them, not even via the legacy
    # opt-in: the suffix attributes them to a real host, so they are not
    # unattributed buckets.
    assert find_browser_buckets(buckets, "server", include_legacy=True) == []


def test_find_browser_buckets_matches_the_shared_hostname_ladder():
    """Alignment guard: attribution must agree with queries._bucket_hostname.

    The ladder is the single source of truth for host attribution. This test
    fails if ``find_browser_buckets`` drifts from it, so the two callers cannot
    start disagreeing about which host a bucket belongs to.
    """
    shapes: Dict[str, Dict[str, Any]] = {
        "hostname-only": {"hostname": "laptop"},
        "data-hostname-only": {"data": {"hostname": "laptop"}},
        "synced-no-metadata": {"id": "chrome-server-synced-from-laptop"},
        "synced-unknown-metadata": {
            "id": "firefox-server-synced-from-laptop",
            "hostname": "unknown",
        },
        "unattributed": {"hostname": "unknown"},
        "no-hostname-at-all": {},
        "other-host": {"hostname": "desktop"},
        "metadata-beats-suffix": {
            "id": "brave-server-synced-from-laptop",
            "hostname": "server",
        },
    }
    buckets = {
        name: {"id": bucket.get("id") or name, "type": "web.tab.current", **bucket}
        for name, bucket in shapes.items()
    }

    for host in ("laptop", "server", "desktop"):
        attributed = sorted(
            bucket["id"]
            for bucket in buckets.values()
            if _bucket_hostname(bucket["id"], bucket) == host
        )
        assert find_browser_buckets(buckets, host) == sorted(set(attributed))

        unattributed = sorted(
            bucket["id"]
            for bucket in buckets.values()
            if _bucket_hostname(bucket["id"], bucket) is None
        )
        assert find_browser_buckets(buckets, host, include_legacy=True) == sorted(
            set(attributed + unattributed)
        )

    # Precedence is inherited from the ladder, not decided here: when a synced
    # bucket's metadata names the serving machine while its suffix names the
    # origin, the metadata rung wins. Pinned so a change to that ordering is a
    # deliberate one.
    assert (
        _bucket_hostname("brave-server-synced-from-laptop", {"hostname": "server"})
        == "server"
    )


def test_build_summary_records_legacy_bucket_policy():
    excluded = build_summary(AGGREGATE_RESULT, START, STOP)
    assert excluded["redaction"]["legacy_unknown_host_buckets"] == "excluded"

    included = build_summary(AGGREGATE_RESULT, START, STOP, include_legacy_buckets=True)
    assert included["redaction"]["legacy_unknown_host_buckets"] == "included"


def test_build_summary_is_aggregate_only_and_tracks_category_coverage():
    payload = build_summary(AGGREGATE_RESULT, START, STOP)

    assert payload["totals"] == {
        "active_seconds": 7200.0,
        "categorized_seconds": 5400.0,
        "uncategorized_seconds": 1800.0,
        "categorized_ratio": 0.75,
    }
    assert payload["categories"][0]["name"] == ["Work", "Programming"]
    assert payload["domains"] == [{"domain": "github.com", "seconds": 1200.0}]
    assert payload["redaction"]["raw_events"] == "omitted"

    serialized = json.dumps(payload)
    assert "window_title" in serialized  # redaction policy is explicit
    assert '"title"' not in serialized
    assert '"url"' not in serialized


def test_build_summary_can_omit_domains():
    payload = build_summary(AGGREGATE_RESULT, START, STOP, include_domains=False)

    assert payload["domains"] == []
    assert payload["redaction"]["domains"] == "omitted"


def test_build_summary_can_omit_apps_and_domains():
    payload = build_summary(
        AGGREGATE_RESULT,
        START,
        STOP,
        include_apps=False,
        include_domains=False,
    )

    assert payload["apps"] == []
    assert payload["domains"] == []
    assert payload["redaction"]["application_names"] == "omitted"


def test_privacy_summary_query_returns_only_aggregate_collections():
    query = queries.privacySummary(
        queries.DesktopQueryParams(
            bid_window="aw-watcher-window-laptop",
            bid_afk="aw-watcher-afk-laptop",
            bid_browsers=["aw-watcher-web-chrome-laptop"],
            classes=default_classes,
        ),
        limit=7,
    )
    return_clause = query.rsplit("RETURN =", 1)[1]

    assert "filter_period_intersect(browser_events, not_afk)" in query
    assert "limit_events(category_events, 7)" in query
    assert '"category_events": category_events' in return_clause
    assert '"uncategorized_seconds": uncategorized_seconds' in return_clause
    assert '"app_events": app_events' in return_clause
    assert '"domain_events": domain_events' in return_clause
    assert '"events":' not in return_clause
    assert '"title_events"' not in return_clause
    assert '"url_events"' not in return_clause


def test_category_coverage_uses_untruncated_server_total():
    result = dict(AGGREGATE_RESULT)
    result["category_events"] = [
        {"duration": 5400, "data": {"$category": ["Work", "Programming"]}}
    ]

    payload = build_summary(result, START, STOP, limit=1)

    assert payload["totals"]["uncategorized_seconds"] == 1800
    assert payload["totals"]["categorized_ratio"] == 0.75
    assert payload["truncation"] == {
        "per_section_limit": 1,
        "policy": "top_by_duration",
    }


def test_privacy_summary_rejects_non_positive_limits():
    params = queries.DesktopQueryParams(
        bid_window="window", bid_afk="afk", classes=default_classes
    )

    try:
        queries.privacySummary(params, limit=0)
    except ValueError as error:
        assert str(error) == "limit must be at least 1"
    else:
        raise AssertionError("privacySummary accepted a zero limit")


def test_format_summary_remains_redacted():
    rendered = format_summary(build_summary(AGGREGATE_RESULT, START, STOP))

    assert "Work > Programming" in rendered
    assert "github.com" in rendered
    assert "window titles" in rendered
    assert "full URLs" in rendered


def test_get_classes_reuses_caller_client():
    class SettingsClient:
        def get_setting(self, key):
            assert key == "classes"
            return [
                {
                    "name": ["Products", "Example"],
                    "rule": {"type": "regex", "regex": "Example"},
                }
            ]

    assert get_classes(SettingsClient()) == [
        (["Products", "Example"], {"type": "regex", "regex": "Example"})
    ]


def test_summary_cli_emits_json_without_sensitive_fields(monkeypatch):
    class FakeClient:
        def __init__(self, *args, **kwargs):
            pass

        def get_setting(self, key):
            assert key == "classes"
            return [
                {
                    "name": ["Work"],
                    "rule": {"type": "regex", "regex": "Code"},
                }
            ]

        def get_buckets(self):
            return {
                "aw-watcher-web-chrome-laptop": {
                    "type": "web.tab.current",
                    "hostname": "laptop",
                }
            }

        def query(self, query, periods, cache=False):
            return [AGGREGATE_RESULT]

    monkeypatch.setattr(cli.aw_client, "ActivityWatchClient", FakeClient)
    runner = CliRunner()
    result = runner.invoke(
        cli.main,
        [
            "summary",
            "laptop",
            "--start",
            "2026-08-17T09:00:00",
            "--stop",
            "2026-08-17T12:00:00",
            "--format",
            "json",
        ],
    )

    assert result.exit_code == 0, result.output
    payload = json.loads(result.stdout)
    assert payload["source"] == "activitywatch"
    assert payload["redaction"]["full_urls"] == "omitted"
    assert "github.com" in result.stdout
    assert '"title"' not in result.stdout
    assert '"url"' not in result.stdout
