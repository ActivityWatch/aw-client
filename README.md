aw-client
=========

[![GitHub Actions badge](https://github.com/ActivityWatch/aw-client/workflows/Build/badge.svg)](https://github.com/ActivityWatch/aw-client/actions)
[![PyPI](https://img.shields.io/pypi/v/aw-client)](https://pypi.org/project/aw-client/)
[![Code style: black](https://img.shields.io/badge/code%20style-black-000000.svg)](https://github.com/psf/black)
[![Typechecking: Mypy](http://www.mypy-lang.org/static/mypy_badge.svg)](http://mypy-lang.org/)

Client library for ActivityWatch in Python.

Please see [the documentation][docs] for usage, and take a look at `examples/`.

 - [Documentation][docs]
 - [API Reference][apiref]

[docs]: https://docs.activitywatch.net/en/latest/
[apiref]: https://docs.activitywatch.net/en/latest/api/python.html#aw-client

## How to install

Install from pip: `pip install aw-client`

Install the latest version directly from github without cloning: `pip install git+https://github.com/ActivityWatch/aw-client.git`

To install from a cloned version:

 - clone repo: `git clone https://github.com/ActivityWatch/aw-client.git`
 - cd into the directory: `cd aw-client`
 - run `poetry install` (will create a virtualenv, if none activated)
   - If you don't want to use poetry you can also use `pip install .`, but that might not get the exact version of the dependencies (due to not reading the `poetry.lock` file).

## Usage

For the CLI:

```
$ aw-client --help
Usage: aw-client [OPTIONS] COMMAND [ARGS]...

  CLI utility for aw-client to aid in interacting with the ActivityWatch
  server

Options:
  --host TEXT     Address of host
  --port INTEGER  Port to use
  -v, --verbose   Verbosity
  --testing       Set to use testing ports by default
  --help          Show this message and exit.

Commands:
  buckets    List all buckets
  canonical  Query 'canonical events' for a single host (filtered,...
  events     Query events from bucket with ID `bucket_id`
  heartbeat  Send a heartbeat to bucket with ID `bucket_id` with JSON `data`
  query      Run a query in file at `path` on the server
  report     Generate an activity report
```


## Server URL configuration

To connect through an HTTPS reverse proxy, set `baseurl` in the `[server]`
section of your `aw-client` configuration file:

```toml
[server]
baseurl = "https://aw.example.com/activitywatch"
```

Use the URL prefix **before** `/api/0`; a path prefix and a trailing slash are
supported. Only HTTP(S) URLs without embedded credentials, a query or a fragment
are accepted. TLS certificate verification remains enabled.

`baseurl` takes precedence over configured `hostname`/`port` and the constructor's
`protocol`. An explicitly passed `host` or `port` (including CLI `--host`/`--port`)
opts out of `baseurl` and uses the existing protocol/host/port behavior. An absent
or empty `baseurl` preserves that behavior too. A profile is an isolated instance
with its own configuration directory, so set `baseurl` in the `[server]` section of
that profile's own `aw-client.toml` (for example
`~/.config/activitywatch-research/aw-client/aw-client.toml`). No
`[server-<profile>]` section is needed, with one exception.

The `testing` profile: the built-in configuration already defines
`[server-testing]`, and that section wins over `[server]`. So with
`--testing` (an alias for `--profile testing`), set `baseurl` in
`[server-testing]` of the testing profile's own `aw-client.toml`:

```toml
[server-testing]
baseurl = "https://aw-testing.example.com/activitywatch"
```

Local API-key discovery and proxy bypass use the configured URL's actual host
and port, not the default loopback address. Remote URLs do not receive a key
read from the local server configuration.

## HTTP timeouts

All HTTP verbs use a finite **10-second connect/read inactivity timeout** by
default. Set `ActivityWatchClient(..., request_timeout=30)` to change the
client-wide policy, including queued heartbeats and bucket registration.
The CLI exposes the same policy as `aw-client --request-timeout 120 query ...`.
This replaces previously unbounded HTTP waits; callers of large event reads,
bulk inserts, or CLI reports may need a larger policy on slow servers.
Values must be positive and finite. A timeout raises `requests.Timeout`;
queued heartbeat timeouts retain the pending SQLite request for replay.

`get_info`, `query`, `import_bucket`, `export_bucket` and `export_all` accept a
keyword-only `timeout=` override, for example `client.export_all(timeout=120)`.
`None` means use the client's policy, **not** disable timeouts. A long operation's
override does not change the queue's policy. Other operations can use a larger
client-wide policy.

`wait_for_start(timeout=10)` retries connection errors and timeouts until its
startup budget expires. Each attempt uses at most half the remaining budget
because Requests applies its timeout separately to connect and read. It does
not start another request after the deadline.

`disconnect()` stops and joins the worker; it does not drain the queue. Against
an accepted-but-silent loopback peer, the in-flight request is limited by the
configured read timeout, and registration stops between buckets. Remaining
requests stay on disk for reconnect. A stop requested during thread startup is
also preserved. With the default policy, a silent connected peer therefore
normally adds at most 10 seconds to shutdown (up to another connect timeout if
connection establishment is still in progress), plus scheduling overhead.

These are socket inactivity limits, **not strict whole-operation deadlines**.
DNS resolution, multiple connection addresses, redirects, or a response that
keeps trickling bytes can exceed them; the joining shutdown does not impose a
separate wall-clock cutoff for those cases.

## Debugging

* Run python with `LOG_LEVEL=debug` to get additional debugging output
* If invalid events have been queued for submission, you may need to delete the file-based queues generated by this library
* To use the development version of this library use `aw-client = {path = "../aw-client" }` in `pyproject.toml`

## Examples

The [`examples/`](examples/) directory contains a couple of example scripts, including:

 - [`time_spent_today.py`](examples/time_spent_today.py) - fetches all non-afk events and sums their duration to get the total active time for the day.
 - [`working_hours.py`](https://github.com/ActivityWatch/aw-client/blob/master/examples/working_hours.py) - computes hours worked per day (matching a "work" category rule), and exports matching events to a JSON file (for auditing).
 - [`load_dataframe.py`](https://github.com/ActivityWatch/aw-client/blob/master/examples/load_dataframe.py) - loads events from a host using a categorizing & AFK-filtering query, put result in a pandas dataframe, and export as CSV.
 - [`merge_buckets.py`](examples/merge_buckets.py) - merges two buckets with non-intersecting events by moving all events from one into the other.
 - [`redact_sensitive.py`](examples/redact_sensitive.py) - redact sensitive events.

`load_dataframe.py` also handles empty query results, exporting a header-only CSV when requested.
