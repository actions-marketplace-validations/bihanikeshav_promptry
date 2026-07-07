"""Shared helpers for the dashboard API routers."""
from __future__ import annotations

import dataclasses


def dc_to_dict(obj):
    """Convert a dataclass to a dict, handling sets -> sorted lists."""
    if dataclasses.is_dataclass(obj) and not isinstance(obj, type):
        result = {}
        for f in dataclasses.fields(obj):
            value = getattr(obj, f.name)
            result[f.name] = dc_to_dict(value)
        return result
    elif isinstance(obj, dict):
        return {k: dc_to_dict(v) for k, v in obj.items()}
    elif isinstance(obj, (list, tuple)):
        return [dc_to_dict(item) for item in obj]
    elif isinstance(obj, set):
        return sorted(dc_to_dict(item) for item in obj)
    else:
        return obj
