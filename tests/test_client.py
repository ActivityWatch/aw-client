#!/usr/bin/env python3
import time

from random import random
from datetime import datetime, timedelta, timezone
from requests.exceptions import HTTPError

from aw_core.models import Event
from aw_client import ActivityWatchClient

def create_unique_event():
    return Event(
        timestamp=datetime.now(timezone.utc),
        duration=timedelta(),
        data={"label": str(random())}
    )

client = ActivityWatchClient("aw-test-client", testing=True)
client.connect()

bucket_name = "test-bucket"
bucket_etype = "test"
# Delete bucket before creating it, and handle error if it doesn't already exist
try:
    client.delete_bucket(bucket_name)
except HTTPError as e:
    pass
client.create_bucket(bucket_name, bucket_etype)

e1 = create_unique_event()
client.insert_event(bucket_name, e1)

print("Getting events")
events = client.get_events(bucket_name)

print("Asserting events")
assert events[0]['data']['label'] == e1['data']['label']

print("Getting bucket")
buckets = client.get_buckets()
print("Asserting bucket")
assert bucket_name in buckets
assert bucket_name == buckets[bucket_name]['id']
assert bucket_etype == buckets[bucket_name]['type']
