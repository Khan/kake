#!/usr/bin/env python

import io
from setuptools import find_packages, setup

setup(
    name='kake',
    version='0.1',
    author='Khan Academy',
    license='MIT',
    packages=find_packages(),
    install_requires=[],
    scripts=[],
    description='"make" library (and server, and commandline-tool)',
    long_description='\n' + io.open('README.md', encoding='utf-8').read(),
)
