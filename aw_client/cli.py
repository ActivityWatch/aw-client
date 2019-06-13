#!/usr/bin/env python3
import json
import argparse
from datetime import timedelta, datetime, timezone
from pprint import pprint

import aw_client
from aw_core import Event


def _valid_date(s):
    # https://stackoverflow.com/questions/25470844/specify-format-for-input-arguments-argparse-python
    try:
        return datetime.strptime(s, "%Y-%m-%d")
    except ValueError:
        msg = "Not a valid date: '{0}'.".format(s)
        raise argparse.ArgumentTypeError(msg)


def main():
    now = datetime.now(timezone.utc)
    td1day = timedelta(days=1)
    td1yr = timedelta(days=365)

    parser = argparse.ArgumentParser(prog="aw-cli", description='A CLI utility for interacting with ActivityWatch.')
    parser.set_defaults(which='none')
    parser.add_argument('--host', default="localhost:5600", help="Host to use, in the format HOSTNAME[:PORT]")

    subparsers = parser.add_subparsers(help='sub-command help')

    parser_heartbeat = subparsers.add_parser('heartbeat', help='Send a heartbeat to the server')
    parser_heartbeat.set_defaults(which='heartbeat')
    parser_heartbeat.add_argument('--pulsetime', default=60, help='Pulsetime to use')
    parser_heartbeat.add_argument('bucket', help='bucketname to send heartbeat to')
    parser_heartbeat.add_argument('data', default="{}", help='JSON data to send in heartbeat')

    parser_buckets = subparsers.add_parser('buckets',
                                           help='List all buckets')
    parser_buckets.set_defaults(which='buckets')

    parser_buckets = subparsers.add_parser('events',
                                           help='Query events from bucket')
    parser_buckets.set_defaults(which='events')
    parser_buckets.add_argument('bucket')

    parser_query = subparsers.add_parser('query',
                                         help='Query events from bucket')
    parser_query.set_defaults(which='query')
    parser_query.add_argument('path')
    parser_query.add_argument('--name')
    parser_query.add_argument('--cache', action='store_true')
    parser_query.add_argument('--json', action='store_true', help='Output resulting JSON')
    parser_query.add_argument('--start', default=now - td1day, type=_valid_date)
    parser_query.add_argument('--end', default=now + 10 * td1yr, type=_valid_date)

    args = parser.parse_args()
    # print("Args: {}".format(args))

    client = aw_client.ActivityWatchClient(
        host=args.host.split(':')[0],
        port=(args.host.split(':')[1] if
              (len(args.host.split(':')) > 1) else 5600))

    if args.which == "heartbeat":
        e = Event(duration=0, data=json.loads(args.data), timestamp=now)
        print(e)
        client.heartbeat(args.bucket, e, args.pulsetime)
    elif args.which == "buckets":
        buckets = client.get_buckets()
        print("Buckets:")
        for bucket in buckets:
            print(" - {}".format(bucket))
    elif args.which == "events":
        events = client.get_events(args.bucket)
        print("events:")
        for e in events:
            print(" - {} ({}) {}".format(e.timestamp.replace(tzinfo=None, microsecond=0), str(e.duration).split(".")[0], e.data))
    elif args.which == "query":
        with open(args.path) as f:
            query = f.read()
        result = client.query(query, args.start, args.end, cache=args.cache, name=args.name)
        if args.json:
            print(json.dumps(result))
        else:
            for period in result:
                print("Showing 10 out of {} events:".format(len(period)))
                for event in period[:10]:
                    event.pop("id")
                    event.pop("timestamp")
                    print(" - Duration: {} \tData: {}".format(str(timedelta(seconds=event["duration"])).split(".")[0], event["data"]))
                print("Total duration:\t", timedelta(seconds=sum(e["duration"] for e in period)))
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
