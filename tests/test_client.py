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


client_name = "aw-test-client"
bucket_name = "test-bucket"
bucket_etype = "test"

# Test context manager
with ActivityWatchClient(client_name, testing=True) as client:
    time.sleep(1)

    # Check that client name is set correctly
    assert client.client_name == client_name

    # Delete bucket before creating it, and handle error if it doesn't already exist
    try:
        client.delete_bucket(bucket_name)
    except HTTPError as e:
        pass

    # Create bucket
    client.create_bucket(bucket_name, bucket_etype)

    # Check bucket
    buckets = client.get_buckets()
    assert bucket_name in buckets
    assert bucket_name == buckets[bucket_name]['id']
    assert bucket_etype == buckets[bucket_name]['type']

    # Insert events
    e1 = create_unique_event()
    e2 = create_unique_event()
    e3 = create_unique_event()
    events = [e1, e2, e3]
    client.insert_events(bucket_name, events)

    # Get events
    fetched_events = client.get_events(bucket_name, limit=len(events))

    # Assert events
    assert fetched_events == events
    assert events[0]['data']['label'] == e1['data']['label']

    # Check eventcount
    eventcount = client.get_eventcount(bucket_name)
    assert eventcount == 1

    # Delete bucket
    client.delete_bucket(bucket_name)
