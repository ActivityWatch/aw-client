"""
Script that computes how many hours was spent in a regex-specified "work" category for each day in a given month.

Also saves the matching work-events to a JSON file (for auditing purposes).
"""

import json
import logging
import os
import re
import socket
import sys
from datetime import datetime, time, timedelta
from typing import Dict, List, Tuple

import aw_client
from aw_client import queries
from aw_core import Event
from aw_transform import flood
from tabulate import tabulate

EXAMPLE_REGEX = r"activitywatch|algobit|defiarb|github.com"
OUTPUT_HTML = os.environ.get("OUTPUT_HTML", "").lower() == "true"

td1d = timedelta(days=1)
day_offset = timedelta(hours=4)


def _pretty_timedelta(td: timedelta) -> str:
    s = str(td)
    s = re.sub(r"^(0+[:]?)+", "", s)
    s = s.rjust(len(str(td)), " ")
    s = re.sub(r"[.]\d+", "", s)
    return s


assert _pretty_timedelta(timedelta(seconds=120)) == "   2:00"
assert _pretty_timedelta(timedelta(hours=9, minutes=5)) == "9:05:00"


def generous_approx(events: List[dict], max_break: float) -> timedelta:
    """
    Returns a generous approximation of worked time by including non-categorized time when shorter than a specific duration

    max_break: Max time (in seconds) to flood when there's an empty slot between events
    """
    events_e: List[Event] = [Event(**e) for e in events]
    return sum(
        map(lambda e: e.duration, flood(events_e, max_break)),
        timedelta(),
    )


def query(regex: str, timeperiods, hostname: str):
    print("Querying events...")
    print(f"  Day offset: {day_offset}")
    print("")

    categories: List[Tuple[List[str], Dict]] = [
        (
            ["Work"],
            {
                "type": "regex",
                "regex": regex,
                "ignore_case": True,
            },
        )
    ]

    aw = aw_client.ActivityWatchClient()

    canonicalQuery = queries.canonicalEvents(
        queries.DesktopQueryParams(
            bid_window=f"aw-watcher-window_{hostname}",
            bid_afk=f"aw-watcher-afk_{hostname}",
            classes=categories,
            filter_classes=[["Work"]],
        )
    )
    query = f"""
    {canonicalQuery}
    duration = sum_durations(events);
    RETURN = {{"events": events, "duration": duration}};
    """

    res = aw.query(query, timeperiods)

    return res


def main():
    if len(sys.argv) < 2:
        print("Usage: python3 working_hours.py <regex> [hostname]")
        exit(1)

    regex = sys.argv[1]
    print(f"Using regex: {regex}")

    if len(sys.argv) > 2:
        hostname = sys.argv[2]
        print(f"Using hostname: {hostname}")
    else:
        hostname = socket.gethostname()

    now = datetime.now().astimezone()
    today = (datetime.combine(now.date(), time()) + day_offset).astimezone()

    timeperiods = [(today - i * td1d, today - (i - 1) * td1d) for i in range(5)]
    timeperiods.reverse()

    res = query(regex, timeperiods, hostname)

    for break_time in [0, 5 * 60, 15 * 60]:
        _print(timeperiods, res, break_time, {"regex": regex})

    fn = "working_hours_events.json"
    with open(fn, "w") as f:
        print(f"Saving to {fn}...")
        json.dump(res, f, indent=2)


def _print(timeperiods, res, break_time, params: dict):
    print("Using:")
    print(f"  break_time={break_time}")
    print("\n".join(f"  {key}={val}" for key, val in params.items()))
    print(
        tabulate(
            [
                [
                    start.date(),
                    # Without flooding:
                    # _pretty_timedelta(timedelta(seconds=res[i]["duration"])),
                    # With flooding:
                    _pretty_timedelta(generous_approx(res[i]["events"], break_time)),
                    len(res[i]["events"]),
                ]
                for i, (start, stop) in enumerate(timeperiods)
            ],
            headers=["Date", "Duration", "Events"],
            colalign=(
                "left",
                "right",
            ),
            tablefmt="html" if OUTPUT_HTML else "simple",
        )
    )

    print(
        f"Total: {sum((generous_approx(res[i]['events'], break_time) for i in range(len(timeperiods))), timedelta())}"
    )
    print("")


if __name__ == "__main__":
    # ignore log warnings in aw_transform
    logging.getLogger("aw_transform").setLevel(logging.ERROR)

    main()
