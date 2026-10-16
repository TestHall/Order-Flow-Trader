"""Gemeinsame Fixtures und maschinenlesbarer PASS/FAIL-Bericht (Handbuch Kap. 16.3 Schritt 1).

Mit `pytest --selftest-report PFAD` wird nach dem Lauf eine JSON-Datei mit Ist- und Sollwerten
jeder Prüfung geschrieben.
"""
from __future__ import annotations

import json
from datetime import date
from decimal import Decimal
from fractions import Fraction
from pathlib import Path
from typing import Any

import pytest

from orderflow.common.config import load_policy, load_sim_config

_RECORDS: dict[str, dict[str, Any]] = {}
_OUTCOMES: dict[str, str] = {}


def _jsonable(v: Any) -> Any:
    if isinstance(v, Fraction):
        return {"fraction": f"{v.numerator}/{v.denominator}", "float": float(v)}
    if isinstance(v, Decimal):
        return {"decimal": str(v)}
    if isinstance(v, date):
        return v.isoformat()
    if isinstance(v, (list, tuple)):
        return [_jsonable(x) for x in v]
    if isinstance(v, dict):
        return {str(k): _jsonable(x) for k, x in v.items()}
    if hasattr(v, "value") and type(v).__module__ != "builtins":
        return v.value
    return v


class SelfTest:
    def __init__(self, nodeid: str, test_id: str, title: str, chapter: str):
        self.record = {"test_id": test_id, "title": title, "chapter": chapter, "nodeid": nodeid, "checks": []}

    def check(self, name: str, actual: Any, expected: Any) -> None:
        ok = actual == expected
        self.record["checks"].append({"name": name, "expected": _jsonable(expected),
                                      "actual": _jsonable(actual), "pass": ok})
        assert ok, f"{self.record['test_id']} {name}: erwartet {expected!r}, erhalten {actual!r}"

    def true(self, name: str, condition: bool, detail: Any = None) -> None:
        self.record["checks"].append({"name": name, "expected": True, "actual": bool(condition),
                                      "detail": _jsonable(detail), "pass": bool(condition)})
        assert condition, f"{self.record['test_id']} {name}: {detail!r}"


@pytest.fixture
def selftest(request):
    def make(test_id: str, title: str, chapter: str) -> SelfTest:
        st = SelfTest(request.node.nodeid, test_id, title, chapter)
        _RECORDS[request.node.nodeid] = st.record
        return st
    return make


@pytest.fixture(scope="session")
def policy():
    return load_policy("OF-A-1.0")


@pytest.fixture(scope="session")
def sim():
    return load_sim_config("SIM-1.0")


def pytest_addoption(parser):
    parser.addoption("--selftest-report", default=None, help="Pfad für den PASS/FAIL-Bericht (JSON)")


@pytest.hookimpl(hookwrapper=True)
def pytest_runtest_makereport(item, call):
    outcome = yield
    rep = outcome.get_result()
    if rep.when == "call" or (rep.when == "setup" and rep.outcome != "passed"):
        _OUTCOMES[item.nodeid] = "PASS" if rep.outcome == "passed" else "FAIL"


def pytest_sessionfinish(session, exitstatus):
    path = session.config.getoption("--selftest-report")
    if not path:
        return
    pol, simc = load_policy(), load_sim_config()
    tests = []
    for nodeid, rec in sorted(_RECORDS.items(), key=lambda kv: (kv[1]["test_id"], kv[0])):
        tests.append({**rec, "status": _OUTCOMES.get(nodeid, "FAIL")})
    other = sorted(n for n in _OUTCOMES if n not in _RECORDS)
    report = {
        "report": "Öffentliche Selbsttests T01–T20 und Zustandsinvarianten",
        "handbook": "KI-Order-Flow-Ausbildung, Version 1.0 (23.09.2026), Kap. 10 und 7.5",
        "policy_version": pol.policy_version,
        "policy_sha256": pol.policy_hash,
        "simulation_version": simc.simulation_version,
        "simulation_sha256": simc.config_hash,
        "statement": ("Nur synthetische Daten. Bestandene Tests belegen technische Regelkonformität, "
                      "keine Marktleistung und keinen handelbaren Vorteil (Kap. 1, 16.3)."),
        "summary": {
            "handbook_tests_passed": sum(1 for t in tests if t["status"] == "PASS"),
            "handbook_tests_failed": sum(1 for t in tests if t["status"] != "PASS"),
            "handbook_test_ids": sorted({t["test_id"] for t in tests}),
            "other_tests_passed": sum(1 for n in other if _OUTCOMES[n] == "PASS"),
            "other_tests_failed": sum(1 for n in other if _OUTCOMES[n] != "PASS"),
        },
        "tests": tests,
        "other_tests": [{"nodeid": n, "status": _OUTCOMES[n]} for n in other],
    }
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    Path(path).write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
