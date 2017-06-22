#!/usr/bin/env python3
import time

from random import random
from datetime import datetime, timedelta, timezone

from aw_core.models import Event
from aw_client import ActivityWatchClient

def create_unique_event():
    return Event(data={"label": str(random())}, timestamp=datetime.now(timezone.utc), duration=timedelta())

client = ActivityWatchClient("aw-test-client", testing=True)

bucket_name = "test-bucket"
bucket_etype = "test"
client.setup_bucket(bucket_name, bucket_etype)

client.connect()
time.sleep(1)

e1 = create_unique_event()
e2 = create_unique_event()
e3 = create_unique_event()
events = [e1, e2, e3]
client.send_events(bucket_name, events)

print("Getting events")
fetched_events = client.get_events(bucket_name, limit=len(events))

print("Asserting events")
print(fetched_events)
print(events)
assert fetched_events == events

print("Getting bucket")
buckets = client.get_buckets()
print("Asserting bucket")
assert bucket_name in buckets
assert bucket_name == buckets[bucket_name]['id']
assert bucket_etype == buckets[bucket_name]['type']

client.delete_bucket(bucket_name)
