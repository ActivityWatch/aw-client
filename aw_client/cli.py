#!/usr/bin/env python3

import json
import logging
import textwrap
from datetime import datetime, timedelta, timezone
from typing import List, Optional

import click
from aw_core import Event
from tabulate import tabulate

import aw_client

from . import queries
from .classes import default_classes, get_classes

now = datetime.now(timezone.utc)
td1day = timedelta(days=1)
td1yr = timedelta(days=365)

logger = logging.getLogger(__name__)


class _Context:
    client: aw_client.ActivityWatchClient


@click.group(
    help="CLI utility for aw-client to aid in interacting with the ActivityWatch server"
)
@click.option(
    "--host",
    default="127.0.0.1",
    help="Address of host",
)
@click.option(
    "--port",
    default=5600,
    help="Port to use",
)
@click.option(
    "-v",
    "--verbose",
    is_flag=True,
    help="Verbosity",
)
@click.option("--testing", is_flag=True, help="Set to use testing ports by default")
@click.pass_context
def main(ctx, testing: bool, verbose: bool, host: str, port: int):
    ctx.obj = _Context()
    ctx.obj.client = aw_client.ActivityWatchClient(
        host=host,
        port=port if port != 5600 else (5666 if testing else 5600),
        testing=testing,
    )
    logging.basicConfig(level=logging.DEBUG if verbose else logging.INFO)


@main.command(help="Send a heartbeat to bucket with ID `bucket_id` with JSON `data`")
@click.argument("bucket_id")
@click.argument("data")
@click.option("--pulsetime", default=60, help="pulsetime to use for merging heartbeats")
@click.pass_obj
def heartbeat(obj: _Context, bucket_id: str, data: str, pulsetime: int):
    now = datetime.now(timezone.utc)
    e = Event(duration=0, data=json.loads(data), timestamp=now)
    print(e)
    obj.client.heartbeat(bucket_id, e, pulsetime)


@main.command(help="List all buckets")
@click.pass_obj
def buckets(obj: _Context):
    buckets = obj.client.get_buckets()
    print("Buckets:")
    for bucket in buckets:
        print(f" - {bucket}")


@main.command(help="Query events from bucket with ID `bucket_id`")
@click.argument("bucket_id")
@click.pass_obj
def events(obj: _Context, bucket_id: str):
    events = obj.client.get_events(bucket_id)
    print("events:")
    for e in events:
        print(
            " - {} ({}) {}".format(
                e.timestamp.replace(tzinfo=None, microsecond=0),
                str(e.duration).split(".")[0],
                e.data,
            )
        )


@main.command(help="Run a query in file at `path` on the server")
@click.argument("path")
@click.option("--name")
@click.option("--cache", is_flag=True)
@click.option("--json", is_flag=True)
@click.option("--start", default=now - td1day, type=click.DateTime())
@click.option("--stop", default=now + td1yr, type=click.DateTime())
@click.pass_obj
def query(
    obj: _Context,
    path: str,
    cache: bool,
    _json: bool,
    start: datetime,
    stop: datetime,
    name: Optional[str] = None,
):
    with open(path) as f:
        query = f.read()
    result = obj.client.query(query, [(start, stop)], cache=cache, name=name)
    if _json:
        print(json.dumps(result))
    else:
        for period in result:
            print(f"Showing 10 out of {len(period)} events:")
            for event in period[:10]:
                event.pop("id")
                event.pop("timestamp")
                print(
                    " - Duration: {} \tData: {}".format(
                        str(timedelta(seconds=event["duration"])).split(".")[0],
                        event["data"],
                    )
                )
            print(
                "Total duration:\t",
                timedelta(seconds=sum(e["duration"] for e in period)),
            )


@main.command(help="Generate an activity report")
@click.argument("hostname")
@click.option("--cache", is_flag=True)
@click.option("--start", default=now - td1day, type=click.DateTime())
@click.option("--stop", default=now + td1yr, type=click.DateTime())
@click.option("--limit", default=10)
@click.pass_obj
def report(
    obj: _Context,
    hostname: str,
    cache: bool,
    start: datetime,
    stop: datetime,
    name: Optional[str] = None,
    limit: int = 10,
):
    logger.info(f"Querying between {start} and {stop}")
    bid_window = f"aw-watcher-window_{hostname}"
    bid_afk = f"aw-watcher-afk_{hostname}"

    if not start.tzinfo:
        start = start.astimezone()
    if not stop.tzinfo:
        stop = stop.astimezone()

    bid_browsers: List[str] = []

    classes = get_classes()
    params = queries.DesktopQueryParams(
        bid_browsers=bid_browsers,
        classes=classes,
        filter_classes=[],
        filter_afk=True,
        include_audible=True,
        bid_window=bid_window,
        bid_afk=bid_afk,
    )
    query = queries.fullDesktopQuery(params)
    logger.debug("Query: \n" + queries.pretty_query(query))

    result = obj.client.query(query, [(start, stop)], cache=cache, name=name)

    # TODO: Print titles, apps, categories, with most time
    for period in result:
        print()
        # print(period["window"]["cat_events"])

        cat_events = _parse_events(period["window"]["cat_events"])
        print_top(
            cat_events,
            lambda e: " > ".join(e.data["$category"]),
            title="Categories",
            n=limit,
        )

        title_events = _parse_events(period["window"]["title_events"])
        print_top(title_events, lambda e: e.data["title"], title="Titles", n=limit)

        active_events = _parse_events(period["window"]["title_events"])
        print(
            "Total duration:\t",
            sum((e.duration for e in active_events), timedelta()),
        )


def _parse_events(events: List[dict]) -> List[Event]:
    return [Event(**event) for event in events]


def print_top(events: List[Event], key=lambda e: e.data, title="Events", n=10):
    print(f"Top {n} {title}" + (f" (out of {len(events)})" if len(events) > 10 else ""))
    print(
        tabulate(
            [
                (event.duration, key(event))
                for event in sorted(events, key=lambda e: e.duration, reverse=True)[:10]
            ],
            headers=["Duration", "Key"],
        )
    )
    print()


@main.command(help="Query 'canonical events' for a single host (filtered, classified)")
@click.argument("hostname")
@click.option("--cache", is_flag=True)
@click.option("--start", default=now - td1day, type=click.DateTime())
@click.option("--stop", default=now + td1yr, type=click.DateTime())
@click.pass_obj
def canonical(
    obj: _Context,
    hostname: str,
    cache: bool,
    start: datetime,
    stop: datetime,
    name: Optional[str] = None,
):
    logger.info(f"Querying between {start} and {stop}")
    bid_window = f"aw-watcher-window_{hostname}"
    bid_afk = f"aw-watcher-afk_{hostname}"

    if not start.tzinfo:
        start = start.astimezone()
    if not stop.tzinfo:
        stop = stop.astimezone()

    classes = default_classes

    query = queries.canonicalEvents(
        queries.DesktopQueryParams(
            bid_window=bid_window,
            bid_afk=bid_afk,
            classes=classes,
        )
    )
    query = f"""{query}\n RETURN = events;"""
    logger.debug("Query: \n" + queries.pretty_query(query))

    result = obj.client.query(query, [(start, stop)], cache=cache, name=name)

    # TODO: Print titles, apps, categories, with most time
    for period in result:
        print()
        events = _parse_events(period)
        print(f"Showing last 10 out of {len(events)} events:")

        print(
            tabulate(
                [
                    (
                        str(e.timestamp).split(".")[0],
                        str(e.duration).split(".")[0],
                        f'[{e.data["app"]}] {textwrap.shorten(e.data["title"], 60, placeholder="...")}',
                    )
                    for e in events[-10:]
                ],
                headers=["Timestamp", "Duration", "Data"],
            )
        )

        print()
        print(
            "Total duration:\t",
            timedelta(seconds=sum(e["duration"] for e in period)),
        )


if __name__ == "__main__":
    main()
