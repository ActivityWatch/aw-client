#!/usr/bin/env python

from setuptools import setup, find_packages

setup(name='aw-client',
      version='0.1',
      description='Client library for ActivityWatch',
      author='Erik Bjäreholt',
      author_email='erik@bjareho.lt',
      url='https://github.com/ActivityWatch/aw-client',
      namespace_packages=['aw'],
      packages=['aw.client'],
      install_requires=['aw-core', 'requests', 'appdirs'],
     )
