#!/usr/bin/env python

from setuptools import setup

setup(name='aw-client',
      version='0.1',
      description='Client library for ActivityWatch',
      author='Erik Bjäreholt',
      author_email='erik@bjareho.lt',
      url='https://github.com/ActivityWatch/aw-client',
      packages=['aw_client'],
      install_requires=[
          'aw-core',
          'requests',
          'appdirs'
      ],
      dependency_links=[
          'https://github.com/ActivityWatch/aw-core/tarball/master#aw-core'
      ])
