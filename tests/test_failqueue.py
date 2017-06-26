#!/usr/bin/env python3
import time
from pprint import pprint
from random import randint
from datetime import datetime, timedelta, timezone
import logging

from aw_core.models import Event
from aw_client import ActivityWatchClient

now = datetime.now(timezone.utc)


def create_unique_event():
    return Event(timestamp=now, data={"label": str(randint(0, 10000))})


def test_failqueue():
    client_name = "aw-test-client-" + str(randint(0, 10000))
    bucket_id = "test-bucket-" + str(randint(0, 10000))
    bucket_etype = "test"

    input("Make sure aw-server isn't running, then press enter > ")
    client1 = ActivityWatchClient(client_name, testing=True)
    client1.create_bucket(bucket_id, bucket_etype, queued=True)

    print("Creating events")
    events = [create_unique_event() for _ in range(3)]
    for i, e in enumerate(events):
        e.timestamp += timedelta(seconds=i)
        client1.heartbeat(bucket_id, e, pulsetime=1, queued=True)

    print("Trying to send events (should fail)")
    with client1:
        time.sleep(1)

    input("Start aw-server with --testing, then press enter > ")

    client2 = ActivityWatchClient(client_name, testing=True)
    client2.create_bucket(bucket_id, bucket_etype, queued=True)

    # Here the previously queued events should be sent
    with client2:
        time.sleep(1)

    print("Getting events")
    recv_events = client2.get_events(bucket_id)

    print("Asserting latest event")
    pprint(recv_events)
    pprint(recv_events[0].data['label'])
    pprint(events[2].data['label'])
    assert recv_events[0].data['label'] == events[2].data['label']
