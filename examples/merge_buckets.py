from datetime import timedelta

import aw_client
from aw_transform.filter_period_intersect import _intersecting_eventpairs


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
    print(f"Buckets: {buckets.keys()}")

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

    print("Inserting source events into destination bucket...")
    aw.insert_events(dest_id, src_events)

    print("Operation complete")
    if input("Do you want to delete the source bucket? (y/n): ") == "y":
        aw.delete_bucket(src_id)
        print("Bucket deleted")

    print("Exiting")


if __name__ == "__main__":
    main()
