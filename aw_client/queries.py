"""
Common queries.

Most of these are from: https://github.com/ActivityWatch/aw-webui/blob/master/src/queries.ts
"""

import dataclasses
import json
import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import (
    Any,
    Dict,
    List,
    Optional,
    Sequence,
    Tuple,
    Union,
)

from typing_extensions import TypeGuard

import logging

import aw_client

from .classes import get_classes

logger = logging.getLogger(__name__)


class EnhancedJSONEncoder(json.JSONEncoder):
    """For encoding dataclasses into JSON"""

    def default(self, o):
        if dataclasses.is_dataclass(o):
            return dataclasses.asdict(o)  # type: ignore
        return super().default(o)


"""
Do these dataclasses look confusing?
Read up on dataclass inheritance: https://stackoverflow.com/a/53085935/965332
"""


@dataclass
class _QueryParamsDefaultsBase:
    bid_browsers: List[str] = field(default_factory=list)
    classes: List[Tuple[List[str], dict]] = field(default_factory=list)
    filter_classes: List[List[str]] = field(default_factory=list)
    filter_afk: bool = True
    include_audible: bool = True


@dataclass
class QueryParams(_QueryParamsDefaultsBase):
    pass


@dataclass
class _DesktopQueryParamsBase:
    bid_window: str
    bid_afk: str
    always_active_pattern: Optional[str] = None


@dataclass
class DesktopQueryParams(QueryParams, _DesktopQueryParamsBase):
    pass


@dataclass
class _AndroidQueryParamsBase:
    bid_android: str


@dataclass
class AndroidQueryParams(QueryParams, _AndroidQueryParamsBase):
    pass


def isDesktopParams(params: QueryParams) -> TypeGuard[DesktopQueryParams]:
    return isinstance(params, DesktopQueryParams)


def isAndroidParams(params: QueryParams) -> TypeGuard[AndroidQueryParams]:
    return isinstance(params, AndroidQueryParams)


def _query_bucket(bid: str, exact: bool) -> str:
    """Query a bucket by exact ID, or by prefix via find_bucket.

    Exact IDs avoid find_bucket matching the wrong bucket when similar names
    exist (e.g. host vs host.localdomain). See ActivityWatch/aw-webui#590.
    """
    if exact:
        return f'query_bucket("{bid}")'
    return f'query_bucket(find_bucket("{bid}"))'


def canonicalEvents(
    params: Union[DesktopQueryParams, AndroidQueryParams],
    *,
    return_variable_suffix: Optional[str] = None,
    merge_android: bool = True,
    exact_bucket_ids: bool = False,
) -> str:
    """Build the query fragment that computes the canonical `events` for one host.

    Puts its results in `events` and `not_afk`. Android buckets have no AFK
    concept, so on Android `not_afk` is the app events themselves.

    Keyword arguments (used by :func:`canonicalMultideviceEvents`):

    - ``return_variable_suffix``: also store results in ``events_<suffix>``
      and ``not_afk_<suffix>`` so several hosts can be combined in one query.
    - ``merge_android``: merge Android events by app (reduces event count, but
      the merged events no longer have meaningful timestamps, so disable it
      when the events are combined with other timelines).
    - ``exact_bucket_ids``: bucket IDs are exact, use ``query_bucket``
      directly instead of prefix-matching with ``find_bucket``.
    """
    if not params.classes:
        # if categories not explicitly set,
        # get categories from server settings
        params.classes = get_classes()

    # Needs escaping for regex patterns like '\w' to work (JSON.stringify adds extra unnecessary escaping)
    classes_str = json.dumps(params.classes, cls=EnhancedJSONEncoder)
    classes_str = re.sub(r"\\\\", r"\\", classes_str)

    cat_filter_str = json.dumps(params.filter_classes)

    # For simplicity, we assume that bid_window and bid_android are exchangeable (note however it needs special treatment)
    bid_window = (
        params.bid_window
        if isinstance(params, DesktopQueryParams)
        else params.bid_android
    )

    return "\n".join(
        [
            # Fetch window/app events
            f"events = flood({_query_bucket(bid_window, exact_bucket_ids)});",
            # On Android, merge events to avoid overload of events
            (
                'events = merge_events_by_keys(events, ["app"]);'
                if isAndroidParams(params) and merge_android
                else ""
            ),
            # Fetch not-afk events
            (
                f"""
            not_afk = flood({_query_bucket(params.bid_afk, exact_bucket_ids)});
            not_afk = filter_keyvals(not_afk, "status", ["not-afk"]);"""
                + (
                    """
            not_treat_as_afk = filter_keyvals_regex(events, "app", "%s");
            not_afk = period_union(not_afk, not_treat_as_afk);
            not_treat_as_afk = filter_keyvals_regex(events, "title", "%s");
            not_afk = period_union(not_afk, not_treat_as_afk);"""
                    % (
                        params.always_active_pattern.replace('"', '\\"'),
                        params.always_active_pattern.replace('"', '\\"'),
                    )
                    if params.always_active_pattern
                    else ""
                )
                if isDesktopParams(params)
                # Android has no AFK bucket: treat all app events as active
                # (matches the single-device Android view in aw-webui).
                else "not_afk = events;"
            ),
            # Fetch browser events
            (
                (browserEvents(params) if isDesktopParams(params) else "")
                + (  # Include focused and audible browser events as indications of not-afk
                    """
            audible_events = filter_keyvals(browser_events, "audible", [true]);
            not_afk = period_union(not_afk, audible_events);
            """
                    if params.include_audible
                    else ""
                )
                if params.bid_browsers
                else ""
            ),
            # Filter out window events when the user was afk
            (
                "events = filter_period_intersect(events, not_afk);"
                if isDesktopParams(params) and params.filter_afk
                else ""
            ),
            # Categorize
            f"events = categorize(events, {classes_str});" if params.classes else "",
            # Filter out selected categories
            (
                f'events = filter_keyvals(events, "$category", {cat_filter_str});'
                if params.filter_classes
                else ""
            ),
            # "Return" events by storing them in host-suffixed variables
            (
                f"events_{return_variable_suffix} = events;\n"
                f"not_afk_{return_variable_suffix} = not_afk;"
                if return_variable_suffix
                else ""
            ),
        ]
    )


HostQueryParams = Union[DesktopQueryParams, AndroidQueryParams]


def safe_hostname(hostname: str) -> str:
    """Strip a hostname down to characters valid in a query variable name."""
    return re.sub(r"[^a-zA-Z0-9_]", "", hostname)


def canonicalMultideviceEvents(host_params: Sequence[HostQueryParams]) -> str:
    """Build a query computing canonical `events` and `not_afk` across several hosts.

    Each element of ``host_params`` describes one host (desktop or Android)
    with exact bucket IDs, for example as returned by :func:`multideviceHostParams`.
    Every host is queried individually (with its own AFK filtering), and the
    per-host results are then combined with ``union_no_overlap``. The order of
    ``host_params`` is the priority order: where hosts overlap in time, the
    earlier host wins and later hosts only fill the gaps, so time is never
    double counted.

    Follows ``canonicalMultideviceEvents`` in aw-webui, with one difference:
    Android events are not merged by app before the union, since merged
    events keep only the first timestamp and would claim time they did not
    cover.

    Classes are resolved once (from the first host's params, or the server
    settings if none are set) and applied to every host.
    """
    if not host_params:
        return "events = [];\nnot_afk = [];"

    classes = next((p.classes for p in host_params if p.classes), None)
    if not classes:
        classes = get_classes()

    fragments = []
    suffixes = []
    for i, params in enumerate(host_params):
        # Include the index so hosts that sanitize to the same name cannot collide
        if isinstance(params, DesktopQueryParams):
            bid = params.bid_window
        else:
            bid = params.bid_android
        suffix = f"{i}_{safe_hostname(bid)}"
        suffixes.append(suffix)
        if isinstance(params, DesktopQueryParams):
            params = dataclasses.replace(
                params,
                classes=classes,
                bid_window=escape_doublequote(params.bid_window),
                bid_afk=escape_doublequote(params.bid_afk),
                bid_browsers=[escape_doublequote(b) for b in params.bid_browsers],
            )
        else:
            params = dataclasses.replace(
                params,
                classes=classes,
                bid_android=escape_doublequote(params.bid_android),
            )
        fragments.append(
            canonicalEvents(
                params,
                return_variable_suffix=suffix,
                merge_android=False,
                exact_bucket_ids=True,
            )
        )

    lines = fragments + ["events = [];", "not_afk = [];"]
    for suffix in suffixes:
        lines += [
            f"events = union_no_overlap(events, sort_by_timestamp(events_{suffix}));",
            f"not_afk = union_no_overlap(not_afk, sort_by_timestamp(not_afk_{suffix}));",
        ]
    return "\n".join(lines)


_SYNCED_FROM = "-synced-from-"


def _bucket_hostname(bid: str, bucket: Dict[str, Any]) -> Optional[str]:
    candidates = [
        bucket.get("hostname"),
        (bucket.get("data") or {}).get("hostname"),
        bid.rsplit(_SYNCED_FROM, 1)[1] if _SYNCED_FROM in bid else None,
    ]
    return next((h for h in candidates if h and h != "unknown"), None)


def _base_bucket_id(bid: str) -> str:
    """Bucket ID without any ``-synced-from-<host>`` suffix."""
    return bid.split(_SYNCED_FROM, 1)[0]


_Candidate = Tuple[str, Dict[str, Any]]


def _rank_buckets(
    candidates: List[_Candidate], canonical_prefixes: Sequence[str] = ()
) -> List[_Candidate]:
    """Sort candidate buckets for one host and role, best first.

    Prefers buckets whose (unsynced) ID uses the watcher's canonical naming,
    then local buckets over synced copies, then the most recently updated.
    """

    def rank(item: _Candidate) -> Tuple[bool, bool, str]:
        bid, bucket = item
        base = _base_bucket_id(bid)
        canonical = any(
            base == prefix.rstrip("_") or base.startswith(prefix)
            for prefix in canonical_prefixes
        )
        return (canonical, _SYNCED_FROM not in bid, bucket.get("last_updated") or "")

    return sorted(candidates, key=rank, reverse=True)


def _pick_bucket(
    candidates: List[_Candidate], canonical_prefixes: Sequence[str]
) -> Optional[_Candidate]:
    ranked = _rank_buckets(candidates, canonical_prefixes)
    return ranked[0] if ranked else None


def multideviceHostParams(
    buckets: Dict[str, Dict[str, Any]],
    hosts: Optional[Sequence[str]] = None,
    **common: Any,
) -> List[HostQueryParams]:
    """Discover per-host query params from bucket metadata.

    ``buckets`` is the result of ``ActivityWatchClient.get_buckets()``. Hosts
    are identified by the bucket ``hostname`` field, which aw-sync preserves
    for synced buckets (whose IDs carry a ``-synced-from-<host>`` suffix).

    - Hosts with both a window and an AFK bucket become ``DesktopQueryParams``,
      including any browser buckets attributed to that host (unless
      ``bid_browsers`` is passed explicitly). Browser buckets without a known
      hostname cannot be attributed to a host and are not included.
    - Hosts with only an Android (or imported ScreenTime) bucket become
      ``AndroidQueryParams`` (no AFK filtering, as mobile hosts have no AFK bucket).
    - Other hosts are skipped.

    If ``hosts`` is given, only those hosts are included, in that order (the
    order is the priority order used by :func:`canonicalMultideviceEvents`).
    Otherwise all hosts are included, desktop hosts first, each group ordered
    by most recently updated.

    Remaining keyword arguments (e.g. ``classes``, ``filter_classes``,
    ``filter_afk``, ``always_active_pattern``) are passed to every params object
    (``always_active_pattern`` and ``bid_browsers`` only to desktop hosts).
    """
    by_host: Dict[str, Dict[str, List[_Candidate]]] = {}
    for bid, bucket in buckets.items():
        hostname = _bucket_hostname(bid, bucket)
        if hostname is None:
            continue
        btype = bucket.get("type")
        if btype == "afkstatus":
            role = "afk"
        elif btype == "currentwindow" and bid.startswith("aw-watcher-android"):
            role = "android"
        elif btype == "app" and bid.startswith("aw-import-screentime"):
            role = "android"
        elif btype == "currentwindow":
            role = "window"
        elif btype == "web.tab.current" and not bid.startswith("aw-watcher-android"):
            role = "browser"
        else:
            continue
        by_host.setdefault(hostname, {}).setdefault(role, []).append((bid, bucket))

    android_common = {
        k: v
        for k, v in common.items()
        if k not in ("always_active_pattern", "bid_browsers")
    }

    result: Dict[str, HostQueryParams] = {}
    # Last activity of the buckets actually selected for each host (for ordering)
    last_updated: Dict[str, str] = {}
    for hostname, roles in by_host.items():
        window = _pick_bucket(roles.get("window", []), ["aw-watcher-window_"])
        afk = _pick_bucket(roles.get("afk", []), ["aw-watcher-afk_"])
        android = _pick_bucket(
            roles.get("android", []), ["aw-watcher-android_", "aw-import-screentime"]
        )
        selected: List[_Candidate]
        if window and afk:
            desktop_common = dict(common)
            if "bid_browsers" not in desktop_common:
                desktop_common["bid_browsers"] = [
                    bid for bid, _ in _rank_buckets(roles.get("browser", []))
                ]
            result[hostname] = DesktopQueryParams(
                bid_window=window[0], bid_afk=afk[0], **desktop_common
            )
            selected = [window, afk]
        elif android:
            result[hostname] = AndroidQueryParams(
                bid_android=android[0], **android_common
            )
            selected = [android]
        else:
            continue
        last_updated[hostname] = max(b.get("last_updated") or "" for _, b in selected)

    if hosts is not None:
        for host in hosts:
            if host not in result:
                logger.warning(
                    f"Skipping host {host} in multidevice query: no window+afk or android bucket"
                )
        return [result[host] for host in hosts if host in result]

    ordered = sorted(
        result,
        key=lambda h: (isDesktopParams(result[h]), last_updated.get(h, "")),
        reverse=True,
    )
    return [result[host] for host in ordered]


def pretty_query(query: str) -> str:
    return "\n".join([line.strip() for line in query.split("\n") if line.strip()])


def _browser_in_buckets(browser: str, browserbuckets: List[str]) -> Optional[str]:
    for bucket in browserbuckets:
        if browser in bucket:
            return bucket
    return None


def browsersWithBuckets(browserbuckets: List[str]) -> List[Tuple[str, str]]:
    """Returns a list of (browserName, bucketId) pairs for found browser buckets"""
    browsername_to_bucketid: List[Tuple[str, Optional[str]]] = [
        (browserName, _browser_in_buckets(browserName, browserbuckets))
        for browserName in browser_appnames
    ]

    # Only return browsers for which a bucket could be found
    return [t for t in browsername_to_bucketid if t[1]]  # type: ignore


def browserEvents(params: DesktopQueryParams) -> str:
    """Returns a list of active browser events (where the browser was the active window) from all browser buckets"""
    code = "browser_events = [];"

    for browserName, bucketId in browsersWithBuckets(params.bid_browsers):
        browser_appnames_str = json.dumps(browser_appnames[browserName])
        code += f"""
          events_{browserName} = flood(query_bucket("{bucketId}"));
          window_{browserName} = filter_keyvals(events, "app", {browser_appnames_str});
          events_{browserName} = filter_period_intersect(events_{browserName}, window_{browserName});
          events_{browserName} = split_url_events(events_{browserName});
          browser_events = concat(browser_events, events_{browserName});
          browser_events = sort_by_timestamp(browser_events);
        """
    return code


browser_appnames = {
    "chrome": [
        # Chrome
        "Google Chrome",
        "Google-chrome",
        "chrome.exe",
        "google-chrome-stable",
        # Chromium
        "Chromium",
        "Chromium-browser",
        "Chromium-browser-chromium",
        "chromium.exe",
        # Pre-releases
        "Google-chrome-beta",
        "Google-chrome-unstable",
        # Brave (should this be merged with the brave entry?)
        "Brave-browser",
    ],
    "firefox": [
        "Firefox",
        "Firefox.exe",
        "firefox",
        "firefox.exe",
        "Firefox Developer Edition",
        "firefoxdeveloperedition",
        "Firefox-esr",
        "Firefox Beta",
        "Nightly",
        "org.mozilla.firefox",
    ],
    "opera": ["opera.exe", "Opera"],
    "brave": ["brave.exe"],
    "edge": [
        "msedge.exe",  # Windows
        "Microsoft Edge",  # macOS
    ],
    "vivaldi": ["Vivaldi-stable", "Vivaldi-snapshot", "vivaldi.exe"],
}

default_limit = 100


def querystr_to_array(querystr: str) -> List[str]:
    return [line + ";" for line in querystr.split(";") if line]


def escape_doublequote(s: str) -> str:
    return s.replace('"', '\\"')


def fullDesktopQuery(
    params: DesktopQueryParams,
) -> str:
    # Escape `"`
    params.bid_window = escape_doublequote(params.bid_window)
    params.bid_afk = escape_doublequote(params.bid_afk)
    params.bid_browsers = [escape_doublequote(bucket) for bucket in params.bid_browsers]

    # Build the base query
    query = f"""
    {canonicalEvents(params)}
    title_events = sort_by_duration(merge_events_by_keys(events, ["app", "title"]));
    app_events   = sort_by_duration(merge_events_by_keys(title_events, ["app"]));
    cat_events   = sort_by_duration(merge_events_by_keys(events, ["$category"]));
    app_events  = limit_events(app_events, {default_limit});
    title_events  = limit_events(title_events, {default_limit});
    duration = sum_durations(events);
    """

    # Add browser-related query parts if browser buckets exist
    if params.bid_browsers:
        query += f"""
        browser_events = split_url_events(browser_events);
        browser_urls = merge_events_by_keys(browser_events, ["url"]);
        browser_urls = sort_by_duration(browser_urls);
        browser_urls = limit_events(browser_urls, {default_limit});
        browser_domains = merge_events_by_keys(browser_events, ["$domain"]);
        browser_domains = sort_by_duration(browser_domains);
        browser_domains = limit_events(browser_domains, {default_limit});
        browser_duration = sum_durations(browser_events);
        """
    else:
        query += """
        browser_events = [];
        browser_urls = [];
        browser_domains = [];
        browser_duration = 0;
        """

    # Add the return statement
    query += """
        RETURN = {
            "events": events,
            "window": {
                "app_events": app_events,
                "title_events": title_events,
                "cat_events": cat_events,
                "active_events": not_afk,
                "duration": duration
            },
            "browser": {
                "domains": browser_domains,
                "urls": browser_urls,
                "duration": browser_duration
            }
        };
    """
    return query


def test_fullDesktopQuery():
    params = DesktopQueryParams(
        bid_window="aw-watcher-window_",
        bid_afk="aw-watcher-afk_",
    )
    now = datetime.now(tz=timezone.utc)
    start = now - timedelta(days=7)
    end = now
    timeperiods = [(start, end)]
    query = fullDesktopQuery(params)

    awc = aw_client.ActivityWatchClient("test")
    res = awc.query(query, timeperiods)[0]
    events = res["events"]
    print(len(events))


if __name__ == "__main__":
    test_fullDesktopQuery()
