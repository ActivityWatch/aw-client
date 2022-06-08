"""
Load ActivityWatch data into a dataframe, and export as CSV.
"""

from datetime import datetime, timedelta, timezone

import pandas as pd

from aw_client import ActivityWatchClient
from aw_client.queries import canonicalEvents, DesktopQueryParams
from aw_client.classes import default_classes


def build_query() -> str:
    canonicalQuery = canonicalEvents(
        DesktopQueryParams(
            bid_window="aw-watcher-window_",
            bid_afk="aw-watcher-afk_",
            classes=default_classes,
        )
    )
    return f"""
    {canonicalQuery}
    RETURN = {{"events": events}};
    """


def main() -> None:
    now = datetime.now(tz=timezone.utc)
    td30d = timedelta(days=30)

    aw = ActivityWatchClient()
    print("Querying...")
    query = build_query()
    data = aw.query(query, [(now - td30d, now)])

    events = [
        {
            "timestamp": e["timestamp"],
            "duration": timedelta(seconds=e["duration"]),
            **e["data"],
        }
        for e in data[0]["events"]
    ]

    for e in events:
        e["$category"] = " > ".join(e["$category"])

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
