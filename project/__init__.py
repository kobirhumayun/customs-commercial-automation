"""Core package for customs commercial automation."""

from importlib import metadata


try:
    __version__ = metadata.version("customs-commercial-automation")
except metadata.PackageNotFoundError:
    __version__ = "0.1.0"
