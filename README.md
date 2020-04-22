aw-client
=========

[![Build Status](https://travis-ci.org/ActivityWatch/aw-client.svg?branch=master)](https://travis-ci.org/ActivityWatch/aw-client)

[**Documentation**](https://activitywatch.readthedocs.io/en/latest/)

Client library for ActivityWatch in Python.

Please see the documentation for usage and examples.

## How to install

To install the latest git version directly from github without cloning, run
`pip install git+https://github.com/ActivityWatch/aw-client.git`

To install from a cloned version, cd into the directory and run
`poetry install` to install inside an virtualenv. If you want to install it
system-wide it can be installed with `pip install .`, but that has the issue
that it might not get the exact version of the dependencies due to not reading
the poetry.lock file.
