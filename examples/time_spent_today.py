from datetime import datetime, time, timedelta, timezone
import socket

import aw_client

if __name__ == "__main__":
    bucket_id = f"aw-watcher-afk_{socket.gethostname()}"

    now = datetime.now(timezone.utc)
    today = now.astimezone().date()
    daystart = datetime.combine(today, time()).astimezone(timezone.utc)
    # Convert each local midnight separately: DST days can last 23 or 25 hours.
    dayend = datetime.combine(today + timedelta(days=1), time()).astimezone(
        timezone.utc
    )

    awc = aw_client.ActivityWatchClient("testclient")
    events = awc.get_events(bucket_id, start=daystart, end=dayend)
    events = [e for e in events if e.data["status"] == "not-afk"]
    total_duration = sum(
        (
            max(
                timedelta(),
                min(e.timestamp + e.duration, now) - max(e.timestamp, daystart),
            )
            for e in events
        ),
        timedelta(),
    )
    print(f"Total time spent on computer today: {total_duration}")
