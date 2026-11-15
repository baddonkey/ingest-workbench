"""Canonical JSON encoding shared by evidence and persistence contracts."""
import json


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=False).encode()