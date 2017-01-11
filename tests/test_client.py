#!/usr/bin/env python3
import time

from random import random
from datetime import datetime, timedelta, timezone

from aw_core.models import Event
from aw_client import ActivityWatchClient

def create_unique_event():
    return Event(label=str(random()), timestamp=datetime.now(timezone.utc), duration=timedelta())

client = ActivityWatchClient("aw-test-client", testing=True)

bucket_name = "test-bucket"
bucket_etype = "test"
client.setup_bucket(bucket_name, bucket_etype)

e1 = create_unique_event()
e2 = create_unique_event()
e3 = create_unique_event()
client.send_event(bucket_name, e1)
client.replace_last_event(bucket_name, e2)
client.replace_last_event(bucket_name, e3)

client.connect()
time.sleep(1)

print("Getting events")
events = client.get_events(bucket_name)

print("Asserting events")
assert events[0]['label'] == e3['label']

print("Getting bucket")
buckets = client.get_buckets()
print("Asserting bucket")
assert bucket_name in buckets
assert bucket_name == buckets[bucket_name]['id']
assert bucket_etype == buckets[bucket_name]['type']
