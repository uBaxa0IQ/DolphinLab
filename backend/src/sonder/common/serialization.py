"""Identifiers and strict JSON encoding shared by domain services."""

import json
import uuid


def uid(prefix: str) -> str:
    return prefix + "_" + uuid.uuid4().hex[:12]


def dumps(value) -> str:
    return json.dumps(value, ensure_ascii=False, allow_nan=False, default=str)
