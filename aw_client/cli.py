#!/usr/bin/env python3
import json
import argparse
from datetime import timedelta, datetime, timezone
from pprint import pprint

import click

import aw_client
from aw_core import Event

now = datetime.now(timezone.utc)
td1day = timedelta(days=1)
td1yr = timedelta(days=365)


def _valid_date(s):
    # https://stackoverflow.com/questions/25470844/specify-format-for-input-arguments-argparse-python
    try:
        return datetime.strptime(s, "%Y-%m-%d")
    except ValueError:
        msg = "Not a valid date: '{0}'.".format(s)
        raise argparse.ArgumentTypeError(msg)


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
@click.option("--testing", is_flag=True, help="Set to use testing ports by default")
@click.pass_context
def main(ctx, testing: bool, host: str, port: int):
    ctx.obj = _Context()
    ctx.obj.client = aw_client.ActivityWatchClient(
        host=host,
        port=port if port != 5600 else (5666 if testing else 5600),
        testing=testing,
    )


@main.command(help="Send a heartbeat to bucket with ID `bucket_id` with JSON `data`")
@click.argument("bucket_id")
@click.argument("data")
@click.option("--pulsetime", default=60, help="pulsetime to use for merging heartbeats")
@click.pass_context
def heartbeat(ctx, bucket_id: str, data: str, pulsetime: int):
    now = datetime.now(timezone.utc)
    e = Event(duration=0, data=json.loads(data), timestamp=now)
    print(e)
    ctx.obj.client.heartbeat(bucket_id, e, pulsetime)


@main.command(help="List all buckets")
@click.pass_context
def buckets(ctx):
    buckets = ctx.obj.client.get_buckets()
    print("Buckets:")
    for bucket in buckets:
        print(" - {}".format(bucket))


@main.command(help="Query events from bucket with ID `bucket_id`")
@click.argument("bucket_id")
@click.pass_context
def events(ctx, bucket_id: str):
    events = ctx.obj.client.get_events(bucket_id)
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
@click.pass_context
def query(
    ctx,
    path: str,
    cache: bool,
    _json: bool,
    start: datetime,
    stop: datetime,
    name: str = None,
):
    with open(path) as f:
        query = f.read()
    result = ctx.obj.client.query(query, [(start, stop)], cache=cache, name=name)
    if _json:
        print(json.dumps(result))
    else:
        for period in result:
            print("Showing 10 out of {} events:".format(len(period)))
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


if __name__ == "__main__":
    main()
