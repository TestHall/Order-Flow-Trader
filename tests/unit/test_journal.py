"""Append-only Journal mit Hash-Kette (Kap. 5.4)."""
import json

import pytest

from orderflow.journal.journal import Journal, JournalCorrupted


def test_append_only_und_wiederanlauf(tmp_path):
    p = tmp_path / "j.jsonl"
    j = Journal(p)
    j.append("DECISION", 1, {"a": 1})
    j.append("ORDER", 2, {"b": 2})
    j2 = Journal(p)
    assert len(j2) == 2 and j2.head == j.head
    j2.append("FILL", 3, {"c": 3})
    assert [r["kind"] for r in Journal(p).records()] == ["DECISION", "ORDER", "FILL"]
    assert not hasattr(j2, "update") and not hasattr(j2, "delete")


def test_manipulation_wird_erkannt(tmp_path):
    p = tmp_path / "j.jsonl"
    j = Journal(p)
    j.append("DECISION", 1, {"action": "NO_TRADE"})
    j.append("DECISION", 2, {"action": "NO_TRADE"})
    lines = p.read_text().splitlines()
    rec = json.loads(lines[0])
    rec["payload"]["action"] = "PROPOSE_SHORT"   # nachträgliches Überschreiben
    lines[0] = json.dumps(rec)
    p.write_text("\n".join(lines) + "\n")
    with pytest.raises(JournalCorrupted):
        Journal(p)
