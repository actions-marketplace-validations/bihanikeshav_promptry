"""Single source for the stdlib ``tomllib`` with a ``tomli`` fallback.

``tomllib`` is stdlib on Python 3.11+; on 3.10 we fall back to the ``tomli``
backport (declared as a dependency for that interpreter). Import ``tomllib``
from here instead of re-writing the version guard in every module.
"""
from __future__ import annotations

try:
    import tomllib
except ModuleNotFoundError:  # Python < 3.11
    import tomli as tomllib  # type: ignore[no-redef]

load = tomllib.load
loads = tomllib.loads

__all__ = ["tomllib", "load", "loads"]
