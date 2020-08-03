import aw_client
from aw_transform.filter_period_intersect import _intersecting_eventpairs


def main():
    aw = aw_client.ActivityWatchClient(testing=True)
    buckets = aw.get_buckets()
    print(f"Buckets: {buckets.keys()}")
    src_id = input("Source bucket ID: ")
    dest_id = input("Destination bucket ID: ")

    src_events = aw.get_events(src_id)
    print(f"✓ src events: {len(src_events)}")
    dest_events = aw.get_events(dest_id)
    print(f"✓ dest events: {len(dest_events)}")

    print("Checking overlap")
    overlaps = list(_intersecting_eventpairs(src_events, dest_events))
    if overlaps:
        print("Buckets had overlap, can't safely merge")
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

    # src_b = buckets[src_id]
    # dest_b = buckets[dest_id]
    # print(src_b)
    # print(dest_b)

    print("Inserting source events into destination bucket...")
    # print("SKIPPING DUE TO BROKEN OVERLAP CHECK")
    aw.insert_events(dest_id, src_events)

    print("Operation complete")
    if input("Do you want to delete the source bucket? (y/n): ") == "y":
        print("Deleting source bucket")
        print("SKIPPING DUE TO BROKEN OVERLAP CHECK")
        # aw.delete_bucket(src_id)
        # print("Bucket deleted")

    print("Exiting")


if __name__ == "__main__":
    main()
