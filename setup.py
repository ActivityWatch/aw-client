#!/usr/bin/env python

from setuptools import setup

setup(name='aw-client',
      version='0.3',
      description='Client library for ActivityWatch',
      author='Erik Bjäreholt',
      author_email='erik@bjareho.lt',
      url='https://github.com/ActivityWatch/aw-client',
      packages=['aw_client'],
      install_requires=[
          # for whatever reason, pip doesn't resolve dependencies in requirements.txt when package is installed by a dependent
          # but unfortunately, pipenv won't allow direct URL requirements: https://travis-ci.org/ActivityWatch/aw-research/jobs/450184764
          #'aw-core @ git+https://github.com/ActivityWatch/aw-core.git#egg=aw-core',
          'aw-core',
          'requests',
          'persist-queue'
      ],
      classifiers=[
          'Programming Language :: Python :: 3'
      ])
