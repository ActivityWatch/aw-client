"""
Lists the most common words among uncategorized events, by duration, to help in creating categories.

This might make more sense as a notebook.
"""

import json
from collections import Counter
from datetime import datetime, timedelta, timezone
from tabulate import tabulate

from aw_core import Event
import aw_client

# set up client
awc = aw_client.ActivityWatchClient("test")


def get_events():
    """
    Retrieves AFK-filtered events, only returns events which are Uncategorized.
    """

    start = datetime(2022, 1, 1, tzinfo=timezone.utc)
    now = datetime.now(tz=timezone.utc)
    timeperiods = [[start, now]]

    # TODO: Use tools in aw-research to load categories from toml file
    categories = [
        [
            ["Work"],
            {"type": "regex", "regex": "aw-|activitywatch", "ignore_case": True},
        ],
    ]

    # TODO: Harmonize with https://github.com/ActivityWatch/aw-client/blob/2cb09e768c32bb6e22e869b830f53addfb130a71/examples/working_hours.py#L75-L87
    #       Perhaps put in a new module aw_client.queries?
    res = awc.query(
        f"""
    window = flood(query_bucket(find_bucket("aw-watcher-window_")));
    afk = flood(query_bucket(find_bucket("aw-watcher-afk_")));
    events = filter_period_intersect(window, filter_keyvals(afk, "status", ["not-afk"]));
    events = categorize(events, {json.dumps(categories)});
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


if __name__ == "__main__":
    events = get_events()

    # find most common words, by duration
    corpus = Counter()
    for word, duration in events2words(events):
        if word not in corpus:
            corpus[word] = timedelta(0)
        corpus[word] += duration

    # The top words are rarely useful for categorization, as they are usually browsers and other categories
    # of activity which are too broad for it to make sense as a rule (except as a fallback).
    print(tabulate(corpus.most_common(50), headers=["word", "duration"]))
