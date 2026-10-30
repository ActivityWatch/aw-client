"""
Load ActivityWatch data into a dataframe, and export as CSV.
"""

import os
import socket
from datetime import datetime, timedelta, timezone

import iso8601
import pandas as pd
from aw_client import ActivityWatchClient
from aw_client.classes import default_classes
from aw_client.queries import DesktopQueryParams, canonicalEvents


def build_query() -> str:
    hostname = "fakedata" if os.getenv("CI") else socket.gethostname()
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

    events = data[0]["events"]
    payloads = [
        {**e["data"], "$category": " > ".join(e["data"]["$category"])} for e in events
    ]
    df = pd.json_normalize(payloads) if events else pd.DataFrame(columns=["$category"])

    # Reserve envelope names after flattening. Choose unused names across all
    # rows, including literal dotted keys and flattened nested payload fields.
    for column in ("timestamp", "duration"):
        if column in df.columns:
            renamed = "data." + column
            while renamed in df.columns:
                renamed = "data." + renamed
            df.rename(columns={column: renamed}, inplace=True)

    df.insert(0, "duration", pd.to_timedelta([e["duration"] for e in events], unit="s"))
    df.index = pd.DatetimeIndex(
        pd.to_datetime([iso8601.parse_date(e["timestamp"]) for e in events], utc=True),
        name="timestamp",
    )

    print(df)

    answer = input("Do you want to export to CSV? (y/N): ")
    if answer == "y":
        filename = "output.csv"
        df.to_csv(filename)
        print(f"Wrote to {filename}")


if __name__ == "__main__":
    main()
