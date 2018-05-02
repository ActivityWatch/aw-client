import aw_client

import argparse


if __name__ == "__main__":
    parser = argparse.ArgumentParser(prog="aw-cli", description='A CLI utility for interacting with ActivityWatch.')
    parser.set_defaults(which='none')
    subparsers = parser.add_subparsers(help='sub-command help')

    parser_heartbeat = subparsers.add_parser('heartbeat', help='Send a heartbeat to the server')
    parser_heartbeat.set_defaults(which='heartbeat')
    parser_heartbeat.add_argument('--pulsetime', default=60, help='Pulsetime to use')

    parser_buckets = subparsers.add_parser('buckets',
                                           help='List all buckets')
    parser_buckets.set_defaults(which='buckets')

    args = parser.parse_args()
    # print("Args: {}".format(args))

    if args.which == "heartbeat":
        raise NotImplementedError
    elif args.which == "buckets":
        client = aw_client.ActivityWatchClient()

        buckets = client.get_buckets()
        print("Buckets:")
        for bucket in buckets:
            print(" - {}".format(bucket))
    else:
        parser.print_help()
