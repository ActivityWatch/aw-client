"""
Lists the most common words among uncategorized events, by duration, to help in creating categories.

This might make more sense as a notebook.
"""
from collections import Counter
from datetime import datetime, timedelta, timezone
from typing import (
    Any,
    Dict,
    List,
    Tuple,
)

import aw_client
from aw_client import queries
from aw_core import Event
from tabulate import tabulate

# set up client
awc = aw_client.ActivityWatchClient("test")


def example_categories():
    # TODO: Use tools in aw-research to load categories from toml file
    return [
        (
            ("Work", "ActivityWatch"),
            {"type": "regex", "regex": "aw-|activitywatch", "ignore_case": True},
        ),
    ]


def get_events(categories=List[Tuple[Tuple[str], Dict[str, Any]]]):
    """
    Retrieves AFK-filtered events, only returns events which are Uncategorized.
    """

    start = datetime(2022, 1, 1, tzinfo=timezone.utc)
    now = datetime.now(tz=timezone.utc)
    timeperiods = [(start, now)]

    canonicalQuery = queries.canonicalEvents(
        awc,
        queries.DesktopQueryParams(
            bid_window="aw-watcher-window_",
            bid_afk="aw-watcher-afk_",
            classes=categories,
        ),
    )
    res = awc.query(
        f"""
        {canonicalQuery}
        events = filter_keyvals(events, "$category", [["Uncategorized"]]);
        duration = sum_durations(events);
        RETURN = {{"events": events, "duration": duration}};
        """,
        timeperiods,
    )
    events = res[0]["events"]
    print(f"Fetched {len(events)} events")
    return [Event(**e) for e in events]


def events2words(events):
    for e in events:
        for v in e.data.values():
            if isinstance(v, str):
                for word in v.split():
                    if len(word) >= 3:
                        # normalize
                        word = word.lower()
                        yield (word, e.duration)


def main():
    categories = example_categories()
    events = get_events(categories)

    # find most common words, by duration
    corpus: Dict[str, timedelta] = Counter()  # type: ignore
    for word, duration in events2words(events):
        if word not in corpus:
            corpus[word] = timedelta(0)
        corpus[word] += duration

    # The top words are rarely useful for categorization, as they are usually browsers and other categories
    # of activity which are too broad for it to make sense as a rule (except as a fallback).
    print(tabulate(corpus.most_common(50), headers=["word", "duration"]))  # type: ignore


if __name__ == "__main__":
    main()
