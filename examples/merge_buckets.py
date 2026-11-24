from datetime import timedelta
from typing import List, Sequence

import aw_client
from aw_core.models import Event
from aw_transform.filter_period_intersect import _intersecting_eventpairs


def copy_events_without_ids(events: Sequence[Event]) -> List[Event]:
    """Return insertable copies that do not carry source IDs.

    Caller objects and their data dicts are left unchanged. Python backends
    treat a set ID as replace/upsert, which is how hostname-change merges lose
    or overwrite destination history.
    """
    copies: List[Event] = []
    for event in events:
        copies.append(
            Event(
                timestamp=event.timestamp,
                duration=event.duration,
                data=dict(event.data),
            )
        )
    return copies


def destination_contains(actual: Sequence[Event], expected: Sequence[Event]) -> bool:
    """True when every expected event's content appears in actual (IDs ignored)."""
    remaining = list(actual)
    for want in expected:
        for index, have in enumerate(remaining):
            if have == want:
                remaining.pop(index)
                break
        else:
            return False
    return True


def main():
    """
    Inserts all events from one bucket into another bucket, after checking for
    overlap (which you shouldn't have if it was caused by a changing hostname).

    Useful to fix duplicate buckets caused by a changing hostname, as in this issue:
      https://github.com/ActivityWatch/activitywatch/issues/454
    """

    # You need to set testing=False if you're going to run this on your normal instance
    aw = aw_client.ActivityWatchClient(testing=True)

    buckets = aw.get_buckets()
    print("Available bucket IDs:")
    print()
    for id in buckets.keys():
        print(id)
    print()

    src_id = input("Source bucket ID: ")
    dest_id = input("Destination bucket ID: ")

    src_events = aw.get_events(src_id)
    print(f"✓ src events: {len(src_events)}")
    dest_events = aw.get_events(dest_id)
    print(f"✓ dest events: {len(dest_events)}")

    print("Checking overlap...")
    overlaps = list(_intersecting_eventpairs(src_events, dest_events))
    if overlaps:
        total_duration_src = sum((e.duration for e in src_events), timedelta())
        total_overlap = sum((tp.duration for _, _, tp in overlaps), timedelta())
        print(
            f"Buckets had overlap ({total_overlap} out of {total_duration_src}), can't safely merge, exiting."
        )
        exit(1)
    print("No overlap detected, continuing...")

    print("You want to merge these two buckets:")
    print(f" - {src_id}")
    print(f" - {dest_id}")
    print(
        "WARNING: you should backup/export the buckets before attempting this operation"
    )
    if input("Does that look right? (y/n): ") != "y":
        print("Aborting")
        exit(1)

    original_dest = list(dest_events)
    copies = copy_events_without_ids(src_events)
    print("Inserting source events into destination bucket...")
    try:
        # Python bulk insert returns None on success; that is not a failure.
        aw.insert_events(dest_id, copies)
    except Exception as exc:
        print(f"Insertion failed ({exc}); source bucket retained")
        print("Exiting")
        return

    after = aw.get_events(dest_id)
    if not destination_contains(after, list(original_dest) + list(src_events)):
        print(
            "Destination does not contain original events plus copied source "
            "events; source bucket retained"
        )
        print("Exiting")
        return

    print("Operation complete")
    if input("Do you want to delete the source bucket? (y/n): ") == "y":
        aw.delete_bucket(src_id, force=True)
        print("Bucket deleted")

    print("Exiting")


if __name__ == "__main__":
    main()
