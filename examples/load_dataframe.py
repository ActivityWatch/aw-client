"""
Load ActivityWatch data into a dataframe, and export as CSV.
"""
import socket
from datetime import datetime, timedelta, timezone

import iso8601
import pandas as pd
from aw_client import ActivityWatchClient
from aw_client.classes import default_classes
from aw_client.queries import DesktopQueryParams, canonicalEvents


def build_query() -> str:
    hostname = socket.gethostname()
    canonicalQuery = canonicalEvents(
        DesktopQueryParams(
            bid_window=f"aw-watcher-window_{hostname}",
            bid_afk=f"aw-watcher-afk_{hostname}",
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
    df["timestamp"] = pd.to_datetime(df["timestamp"].apply(iso8601.parse_date))
    df.set_index("timestamp", inplace=True)

    print(df)

    answer = input("Do you want to export to CSV? (y/N): ")
    if answer == "y":
        filename = "output.csv"
        df.to_csv(filename)
        print(f"Wrote to {filename}")


if __name__ == "__main__":
    main()
