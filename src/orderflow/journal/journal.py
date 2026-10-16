"""Append-only Journal mit Hash-Kette (Kap. 5.4).

Jeder Eintrag verweist auf den Hash seines Vorgängers. Ein Hash belegt Unverändertheit des
gespeicherten Inhalts, nicht seine sachliche Wahrheit. Es gibt keine Update- oder Delete-Operation.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Iterator

from orderflow.common.hashing import GENESIS, canonical_json, sha256_hex
from orderflow.common.timeutil import format_ts


class JournalCorrupted(RuntimeError):
    pass


class Journal:
    def __init__(self, path: Path | None = None):
        self.path = Path(path) if path is not None else None
        self._records: list[dict[str, Any]] = []
        self._head = GENESIS
        if self.path is not None and self.path.exists():
            with self.path.open(encoding="utf-8") as fh:
                for line in fh:
                    if line.strip():
                        self._records.append(json.loads(line))
            self._head = verify_chain(self._records)

    def append(self, kind: str, ts: int, payload: dict[str, Any]) -> dict[str, Any]:
        body = {"seq": len(self._records), "ts": ts, "ts_iso": format_ts(ts), "kind": kind,
                "payload": json.loads(canonical_json(payload)), "prev_hash": self._head}
        body["hash"] = sha256_hex(canonical_json(body))
        self._records.append(body)
        self._head = body["hash"]
        if self.path is not None:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with self.path.open("a", encoding="utf-8") as fh:
                fh.write(canonical_json(body) + "\n")
        return body

    def records(self, kind: str | None = None) -> Iterator[dict[str, Any]]:
        for r in self._records:
            if kind is None or r["kind"] == kind:
                yield r

    def __len__(self) -> int:
        return len(self._records)

    @property
    def head(self) -> str:
        return self._head


def verify_chain(records: list[dict[str, Any]]) -> str:
    head = GENESIS
    for i, r in enumerate(records):
        body = {k: v for k, v in r.items() if k != "hash"}
        if r.get("seq") != i or r.get("prev_hash") != head or sha256_hex(canonical_json(body)) != r.get("hash"):
            raise JournalCorrupted(f"Journal-Eintrag {i} verletzt die Hash-Kette")
        head = r["hash"]
    return head
