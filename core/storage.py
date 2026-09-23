"""Atomic state replacement: readers never see half-written JSON."""
import json
import os
import tempfile
from collections import deque


def save_json(path, value):
    directory = os.path.dirname(path) or "."
    os.makedirs(directory, exist_ok=True)
    fd, temporary = tempfile.mkstemp(dir=directory, suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            json.dump(value, stream, allow_nan=False)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def read_json(path, default):
    try:
        with open(path, encoding="utf-8") as stream:
            return json.load(stream)
    except FileNotFoundError:
        return default


def read_tail(path, limit):
    try:
        with open(path, encoding="utf-8") as stream:
            lines = deque(stream, maxlen=limit)
    except FileNotFoundError:
        return []
    result = []
    for line in lines:
        try:
            result.append(json.loads(line))
        except json.JSONDecodeError:
            continue  # A writer may still be appending the last line.
    return result
