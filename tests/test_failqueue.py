#!/usr/bin/env python3
import time

from random import random
from datetime import datetime, timedelta, timezone

from aw_core.models import Event
from aw_client import ActivityWatchClient

def create_unique_event():
    return Event(label=str(random()), timestamp=datetime.now(timezone.utc), duration=timedelta())

bucket_name = "test-bucket"
bucket_etype = "test"

input("Make sure aw-server isn't running, then press enter > ")
client1 = ActivityWatchClient("aw-test-client", testing=True)
client1.setup_bucket(bucket_name, bucket_etype)

print("Creating events")
e1 = create_unique_event()
e2 = create_unique_event()
e3 = create_unique_event()
client1.send_event(bucket_name, e1)
client1.replace_last_event(bucket_name, e2)
client1.replace_last_event(bucket_name, e3)

print("Trying to send events (should fail)")
client1.connect()
time.sleep(1)
client1.disconnect()

input("Start aw-server, then press enter > ")

client2 = ActivityWatchClient("aw-test-client", testing=True)
client2.setup_bucket(bucket_name, bucket_etype)
client2.connect()
time.sleep(1)


print("Getting events")
events = client2.get_events(bucket_name)

print("Asserting latest event")
#print(events)
#print(events[0]['label'])
#print(e3['label'])
assert events[0]['label'] == e3['label']
