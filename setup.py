#!/usr/bin/env python

from setuptools import setup

setup(name='aw-client',
      version='0.2',
      description='Client library for ActivityWatch',
      author='Erik Bjäreholt',
      author_email='erik@bjareho.lt',
      url='https://github.com/ActivityWatch/aw-client',
      packages=['aw_client'],
      install_requires=[
          # for whatever reason, pip doesn't resolve dependencies in requirements.txt when package is installed by a dependent
          'aw-core',
          'requests'
      ],
      classifiers=[
          'Programming Language :: Python :: 3'
      ])
