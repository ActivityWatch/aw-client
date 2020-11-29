# NOTE: Might not treat timezones correctly.

from datetime import datetime, time, timedelta
import aw_client
import socket

# Set this to your AFK bucket
bucket_id = "aw-watcher-afk_{}".format(socket.gethostname())

daystart = datetime.combine(datetime.now().date(), time())
dayend = daystart + timedelta(days=1)

awc = aw_client.ActivityWatchClient("testclient")
events = awc.get_events(bucket_id, start=daystart, end=dayend)
events = [e for e in events if e.data["status"] == "not-afk"]
total_duration = sum((e.duration for e in events), timedelta())
print("Total time spent on computer today: {}".format(total_duration))
