"""
Minimal setup.py shim.
Required for editable installs on Python 3.9 with older pip versions
that do not support PEP 660 (build_editable).
The real metadata lives in pyproject.toml.
"""
from setuptools import setup

setup()
