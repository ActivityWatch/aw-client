#!/usr/bin/env python

from setuptools import setup, find_packages

setup(name='actwa-client',
      version='0.1',
      description='Client library for ActivityWatch',
      author='Erik Bjäreholt',
      author_email='erik@bjareho.lt',
      url='https://github.com/ActivityWatch/actwa-client',
      packages=['actwa.client'],
      install_requires=['actwa-core', 'requests'],
     )
