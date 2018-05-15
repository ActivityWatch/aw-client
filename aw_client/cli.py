import argparse
from datetime import timedelta, datetime

import aw_client


def main():
    now = datetime.now()
    td1day = timedelta(days=1)
    td1yr = timedelta(days=365)

    parser = argparse.ArgumentParser(prog="aw-cli", description='A CLI utility for interacting with ActivityWatch.')
    parser.set_defaults(which='none')
    parser.add_argument('--host', default="localhost:5600", help="Host to use, on the format HOSTNAME:PORT")

    subparsers = parser.add_subparsers(help='sub-command help')

    parser_heartbeat = subparsers.add_parser('heartbeat', help='Send a heartbeat to the server')
    parser_heartbeat.set_defaults(which='heartbeat')
    parser_heartbeat.add_argument('--pulsetime', default=60, help='Pulsetime to use')

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
    parser_query.add_argument('--start', default=now - td1day)
    parser_query.add_argument('--end', default=now + 10 * td1yr)

    args = parser.parse_args()
    # print("Args: {}".format(args))

    client = aw_client.ActivityWatchClient(host=args.host)

    if args.which == "heartbeat":
        raise NotImplementedError
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
        result = client.query(query, args.start, args.end)
        print("events:")
        print(result)
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
