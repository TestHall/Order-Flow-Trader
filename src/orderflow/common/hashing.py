from __future__ import annotations

import hashlib
import json
from decimal import Decimal
from fractions import Fraction
from typing import Any


def _default(obj: Any) -> Any:
    if isinstance(obj, Decimal):
        return str(obj)
    if isinstance(obj, Fraction):
        return f"{obj.numerator}/{obj.denominator}"
    raise TypeError(f"nicht serialisierbar: {type(obj).__name__}")


def canonical_json(obj: Any) -> str:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False, default=_default)


def sha256_hex(data: str | bytes) -> str:
    if isinstance(data, str):
        data = data.encode("utf-8")
    return hashlib.sha256(data).hexdigest()


def hash_obj(obj: Any) -> str:
    return sha256_hex(canonical_json(obj))


def chain(prev_digest: str, item_hash: str) -> str:
    return sha256_hex(prev_digest + item_hash)


GENESIS = "0" * 64
