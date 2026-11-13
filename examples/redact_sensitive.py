"""
Interactive preview of sensitive-data redaction.

Wet writes are refused: the client/server APIs have no common atomic event
replacement operation. Delete-then-insert can lose history on failure, while
inserting with an existing ID has backend-dependent behavior. Export/back up
history before using a tool with a verified replacement contract.

The preview prints raw before-values, including sensitive data. Terminal output
and captured logs are NOT sanitized.

Issues/improvements:
 - If an event matches the sensitive string, only the sensitive field will be redacted (so if the title matches but not the URL, the URL will remain unredacted)
 - One might not want to redact to the non-informative 'REDACTED', but instead to a string with secret meaning.

"""

import re
import sys
from copy import deepcopy
from typing import (
    List,
    Pattern,
    Set,
    Union,
)

from aw_client import ActivityWatchClient
from aw_core import Event

aw: ActivityWatchClient

REDACTED = "REDACTED"
DRYRUN = True


def main():
    global DRYRUN
    DRYRUN = "--wet" not in sys.argv

    global aw
    aw = ActivityWatchClient(testing=True)

    buckets = aw.get_buckets()
    print("Buckets: ")
    print("\n".join([" - " + bid for bid in buckets.keys()]) + "\n")

    bid_to_redact = input(
        "In which bucket are the events you want to redact? (* for all): "
    )
    assert bid_to_redact == "*" or bid_to_redact in buckets, "Not a valid option"

    regex_or_string = input(
        "Do you want to search by regex or string? (regex/string): "
    )
    assert regex_or_string in ["regex", "string"], "Not a valid option"

    print("\nNOTE: Matching is not case sensitive!")
    pattern: Union[str, Pattern]
    if regex_or_string == "string":
        pattern = input("Enter a string indicating sensitive content: ").lower()
    else:
        pattern = re.compile(
            input("Enter a regex indicating sensitive content: "), re.IGNORECASE
        )

    print("")
    if DRYRUN:
        print(
            "NOTE: Performing a dry run, no events will be modified. Wet writes are unsupported."
        )
    else:
        print(
            "WARNING: Wet writes are unsupported without atomic event replacement. Matching events can be previewed, but no events will be modified."
        )
    print(
        "WARNING: Preview prints raw sensitive values; captured logs are not sanitized."
    )
    input("Press ENTER to continue, or Ctrl-C to abort")

    if bid_to_redact == "*":
        for bucket_id in buckets.keys():
            if bucket_id.startswith("aw-watcher-afk"):
                continue
            _redact_bucket(bucket_id, pattern)
    else:
        _redact_bucket(bid_to_redact, pattern)


def _redact_bucket(bucket_id: str, pattern: Union[str, Pattern]):
    print(f"\nChecking bucket: {bucket_id}")

    global aw
    events = aw.get_events(bucket_id, limit=-1)
    sensitive_ids = _find_sensitive(events, pattern)
    print(f"Found {len(sensitive_ids)} sensitive events")

    if not sensitive_ids:
        return

    yes_redact = input(
        f"Do you want to replace all the matching strings with '{REDACTED}'? (y/N): "
    )
    if yes_redact == "y":
        for e in events:
            if e.id in sensitive_ids:
                e_before = e
                e = _redact_event(e, pattern)
                print(f"\nData before: {e_before.data}")
                print(f"Data after:  {e.data}")

                if DRYRUN:
                    print("DRYRUN, preview only; no events modified")
                else:
                    raise RuntimeError(
                        "Wet redaction is unsupported: no common atomic event "
                        "replacement API. No events were modified."
                    )


def _check_event(e: Event, pattern: Union[str, Pattern]) -> bool:
    for v in e.data.values():
        if isinstance(v, str):
            if isinstance(pattern, str):
                if pattern in v.lower():
                    return True
            else:
                if pattern.search(v):
                    return True
    return False


def _redact_event(e: Event, pattern: Union[str, Pattern]) -> Event:
    e = deepcopy(e)
    for k, v in e.data.items():
        if isinstance(v, str):
            if isinstance(pattern, str):
                if pattern in v.lower():
                    e.data[k] = REDACTED
            else:
                if pattern.search(v):
                    e.data[k] = REDACTED
    return e


def _find_sensitive(el: List[Event], pattern: Union[str, Pattern]) -> Set:
    sensitive_ids = set()
    for e in el:
        if _check_event(e, pattern):
            sensitive_ids.add(e.id)

    return sensitive_ids


if __name__ == "__main__":
    main()
