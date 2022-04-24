#!/usr/bin/env python3
import time
from random import random
from datetime import datetime, timedelta, timezone
from requests.exceptions import HTTPError

import pytest

from aw_core.models import Event
from aw_client import ActivityWatchClient


def create_unique_event():
    return Event(
        timestamp=datetime.now(timezone.utc),
        duration=timedelta(),
        data={"label": str(random())},
    )


def test_full():
    now = datetime.now(timezone.utc)

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
        except HTTPError:
            pass

        # Create bucket
        client.create_bucket(bucket_name, bucket_etype)

        # Check bucket
        buckets = client.get_buckets()
        assert bucket_name in buckets
        assert bucket_name == buckets[bucket_name]["id"]
        assert bucket_etype == buckets[bucket_name]["type"]

        # Insert events
        e1 = create_unique_event()
        e2 = create_unique_event()
        e3 = create_unique_event()
        events = [e1, e2, e3]
        client.insert_events(bucket_name, events)

        # Get events
        fetched_events = client.get_events(bucket_name, limit=len(events))

        # Assert events
        assert [(e.timestamp, e.duration, e.data) for e in fetched_events] == [
            (e.timestamp, e.duration, e.data) for e in events
        ]

        # Check eventcount
        eventcount = client.get_eventcount(bucket_name)
        assert eventcount == len(events)

        result = client.query(
            f'RETURN = query_bucket("{bucket_name}");',
            timeperiods=[(now - timedelta(hours=1), now + timedelta(hours=1))],
        )
        assert len(result) == 1
        assert len(result[0]) == 3

        # Get single event
        e = client.get_event(bucket_name, fetched_events[1].id)
        assert e.id == fetched_events[1].id

        # Delete single event
        client.delete_event(bucket_name, fetched_events[1].id)
        assert client.get_event(bucket_name, fetched_events[1].id) is None

        # Test exception raising
        with pytest.raises(ValueError):
            # timeperiod end time does not have a timezone set
            result = client.query(
                f'RETURN = query_bucket("{bucket_name}");',
                timeperiods=[(now - timedelta(hours=1), datetime.now())],
            )

        # Create and delete an event: check that it no longer exists
        e_del = create_unique_event()
        client.insert_event(bucket_name, e_del)
        fetched_events = client.get_events(bucket_name)
        assert (e_del.timestamp, e_del.duration, e_del.data) in [
                (e.timestamp, e.duration, e.data) for e in fetched_events]

        e_del_fetched = [e for e in fetched_events if e.data == e_del.data][0]
        client.delete_event(bucket_name, e_del_fetched)
        fetched_events = client.get_events(bucket_name)
        assert (e_del.timestamp, e_del.duration, e_del.data) not in [
                (e.timestamp, e.duration, e.data) for e in fetched_events]

        # Delete bucket
        client.delete_bucket(bucket_name)
