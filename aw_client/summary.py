"""Privacy-safe ActivityWatch summary helpers."""

from datetime import datetime, timedelta
from typing import Any, Dict, Iterable, List

from tabulate import tabulate

Summary = Dict[str, Any]


# Hostname values that carry no usable attribution. Legacy watchers report these
# instead of a real host, so such buckets cannot be assigned to any machine.
_UNATTRIBUTED_HOSTNAMES = (None, "", "unknown")


def find_browser_buckets(
    buckets: Dict[str, dict], hostname: str, include_legacy: bool = False
) -> List[str]:
    """Return browser bucket IDs for a host.

    Buckets whose hostname metadata is missing or "unknown" cannot be attributed
    to a machine. On a server collecting from several machines they may belong to
    a different host, so folding them into this host's summary would leak another
    machine's browsing domains. They are excluded unless the caller opts in with
    ``include_legacy``, which is only safe on a single-machine server.
    """
    matches = []
    for bucket_id, bucket in buckets.items():
        if bucket.get("type") != "web.tab.current":
            continue

        data = bucket.get("data") or {}
        bucket_hostname = bucket.get("hostname") or data.get("hostname")
        if bucket_hostname == hostname:
            pass
        elif include_legacy and bucket_hostname in _UNATTRIBUTED_HOSTNAMES:
            pass
        else:
            continue

        matches.append(bucket.get("id") or bucket_id)
    return sorted(set(matches))


def _seconds(value: Any) -> float:
    return round(float(value or 0), 3)


def _aggregate_rows(
    events: Iterable[dict], data_key: str, output_key: str
) -> List[dict]:
    rows = []
    for event in events:
        value = (event.get("data") or {}).get(data_key)
        if value in (None, "", []):
            continue
        if data_key == "$category" and not isinstance(value, list):
            value = [str(value)]
        rows.append({output_key: value, "seconds": _seconds(event.get("duration"))})
    return sorted(rows, key=lambda row: row["seconds"], reverse=True)


def build_summary(
    result: dict,
    start: datetime,
    stop: datetime,
    include_apps: bool = True,
    include_domains: bool = True,
    limit: int = 20,
    include_legacy_buckets: bool = False,
) -> Summary:
    """Normalize an aggregate query result into a provider-neutral payload."""
    categories = _aggregate_rows(result.get("category_events", []), "$category", "name")
    apps = (
        _aggregate_rows(result.get("app_events", []), "app", "app")
        if include_apps
        else []
    )
    domains = (
        _aggregate_rows(result.get("domain_events", []), "$domain", "domain")
        if include_domains
        else []
    )

    active_seconds = _seconds(result.get("active_seconds"))
    uncategorized_seconds = _seconds(result.get("uncategorized_seconds"))
    categorized_seconds = _seconds(max(0, active_seconds - uncategorized_seconds))
    categorized_ratio = (
        round(categorized_seconds / active_seconds, 4) if active_seconds else 0
    )

    return {
        "source": "activitywatch",
        "schema_version": 1,
        "range": {
            "start": start.isoformat(),
            "end": stop.isoformat(),
            "timezone": str(start.tzinfo),
        },
        "totals": {
            "active_seconds": active_seconds,
            "categorized_seconds": categorized_seconds,
            "uncategorized_seconds": uncategorized_seconds,
            "categorized_ratio": categorized_ratio,
        },
        "categories": categories,
        "apps": apps,
        "domains": domains,
        "truncation": {
            "per_section_limit": limit,
            "policy": "top_by_duration",
        },
        "redaction": {
            "category_names": "included",
            "application_names": "included" if include_apps else "omitted",
            "window_titles": "omitted",
            "full_urls": "omitted",
            "document_names": "omitted",
            "chat_or_email_subjects": "omitted",
            "domains": "included" if include_domains else "omitted",
            "raw_events": "omitted",
            "legacy_unknown_host_buckets": (
                "included" if include_legacy_buckets else "excluded"
            ),
        },
    }


def _format_seconds(seconds: float) -> str:
    return str(timedelta(seconds=round(seconds)))


def _table(rows: List[dict], label_key: str, label: str) -> str:
    if not rows:
        return ""
    values = []
    for row in rows:
        value = row[label_key]
        if isinstance(value, list):
            value = " > ".join(value)
        values.append((value, _format_seconds(row["seconds"])))
    return tabulate(values, headers=[label, "Active time"])


def format_summary(summary: Summary) -> str:
    """Render a privacy-safe summary for humans without expanding sensitive data."""
    totals = summary["totals"]
    ratio = totals["categorized_ratio"] * 100
    sections = [
        "ActivityWatch privacy-safe summary",
        f"Range: {summary['range']['start']} to {summary['range']['end']}",
        f"Active time: {_format_seconds(totals['active_seconds'])}",
        f"Categorized: {ratio:.1f}%",
    ]

    for title, rows, key, label in (
        ("Categories", summary["categories"], "name", "Category"),
        ("Applications", summary["apps"], "app", "Application"),
        ("Domains", summary["domains"], "domain", "Domain"),
    ):
        table = _table(rows, key, label)
        if table:
            sections.extend((title, table))

    sections.append(
        "Redacted: window titles, full URLs, document names, message subjects, raw events"
    )
    return "\n\n".join(sections)
