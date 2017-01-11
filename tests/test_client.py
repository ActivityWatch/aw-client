#!/usr/bin/env python3

from random import random
from datetime import datetime, timedelta, timezone

from aw_core.models import Event
from aw_client import ActivityWatchClient

def create_unique_event():
    return Event(label=str(random()), timestamp=datetime.now(timezone.utc), duration=timedelta())

# Disconnected
input("Do not start aw-server yet, press enter to continue")

client = ActivityWatchClient("aw-test-client", testing=True)
client.connect()

bucket_name = "test-bucket"
bucket_etype = "test"
client.setup_bucket(bucket_name, bucket_etype)

e1 = create_unique_event()
e2 = create_unique_event()
client.send_event(bucket_name, e1)
client.replace_last_event(bucket_name, e2)

assert client.connected == False


# Connected
input("Start aw-server and press enter")

e3 = create_unique_event()
client.replace_last_event(bucket_name, e3)

print("Getting events prior to connect")
events = client.get_events(bucket_name)
print("Asserting events prior to connect")
assert e1 not in events
assert e2 not in events
assert e3 not in events

client.connect()

print("Getting events after connect")
events = client.get_events(bucket_name)

print("Asserting events after connect")
assert events[0]['label'] == e3['label']

buckets = client.get_buckets()
assert client.connected == True

assert bucket_name in buckets
assert bucket_name == buckets[bucket_name]['id']
assert bucket_etype == buckets[bucket_name]['type']
