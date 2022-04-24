"""
Load ActivityWatch data into a dataframe, and export as CSV.
"""

from datetime import datetime, timedelta, timezone

import pandas as pd

from aw_client import ActivityWatchClient


_query = """
window = flood(query_bucket(find_bucket("aw-watcher-window_")));
afk = flood(query_bucket(find_bucket("aw-watcher-afk_")));
afk = filter_keyvals(afk, "status", ["not-afk"]);
events = filter_period_intersect(window, afk);
RETURN = {"events": events};
"""


def main() -> None:
    now = datetime.now(tz=timezone.utc)
    td30d = timedelta(days=30)

    aw = ActivityWatchClient()
    print("Querying...")
    data = aw.query(_query, [(now - td30d, now)])

    events = [
        {
            "timestamp": e["timestamp"],
            "duration": timedelta(seconds=e["duration"]),
            **e["data"],
        }
        for e in data[0]["events"]
    ]

    df = pd.json_normalize(events)
    df["timestamp"] = pd.to_datetime(df["timestamp"], infer_datetime_format=True)
    df.set_index("timestamp", inplace=True)

    print(df)

    answer = input("Do you want to export to CSV? (y/N): ")
    if answer == "y":
        filename = "output.csv"
        df.to_csv(filename)
        print(f"Wrote to {filename}")


if __name__ == "__main__":
    main()
