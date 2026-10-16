"""Zugriffsgrenzen zwischen den Komponenten (Kap. 2) als harte Vorgabe, nicht nur Konvention.

Jedes Paket darf nur die hier freigegebenen Pakete importieren. Insbesondere:
- der KI-Analyst kann weder Preise/Fills (execution) noch Prüfergebnisse (verifier) festlegen,
- niemand außer Tests importiert den Prüfer (keine verdeckten Lösungen für den Kandidaten),
- der Prüfer ist unabhängig von Regelmodul und Ausführung implementiert.
"""
import ast
from pathlib import Path

SRC = Path(__file__).resolve().parents[2] / "src" / "orderflow"

ALLOWED = {
    "common": set(),
    "dataadapter": {"common"},
    "features": {"common", "dataadapter"},
    "journal": {"common"},
    "execution": {"common", "dataadapter", "features", "journal"},
    "rules": {"common", "dataadapter", "features", "journal", "execution.risk", "execution.costs"},
    "analyst": {"common", "journal"},
    "verifier": {"common", "dataadapter", "journal"},
    "replay": {"common", "dataadapter", "features", "journal", "execution", "rules"},
}


def _imports(path: Path) -> set[str]:
    out = set()
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
        if isinstance(node, ast.ImportFrom) and node.module and node.module.startswith("orderflow."):
            out.add(node.module.split(".", 1)[1])
        elif isinstance(node, ast.Import):
            out.update(a.name.split(".", 1)[1] for a in node.names if a.name.startswith("orderflow."))
    return out


def _permitted(target: str, allowed: set[str], own: str) -> bool:
    if target.split(".")[0] == own:
        return True
    return any(target == a or target.startswith(a + ".") or target.split(".")[0] == a and "." not in a
               for a in allowed)


def test_alle_pakete_sind_erfasst():
    pkgs = {p.name for p in SRC.iterdir() if p.is_dir() and (p / "__init__.py").exists()}
    assert pkgs == set(ALLOWED)


def test_importgrenzen():
    violations = []
    for pkg, allowed in ALLOWED.items():
        for f in (SRC / pkg).rglob("*.py"):
            for target in _imports(f):
                if not _permitted(target, allowed, pkg):
                    violations.append(f"{f.relative_to(SRC)} -> {target}")
    assert violations == []


OPERATOR_MODULES = {"cli.py"}   # Betreiberschicht: startet Replay und danach getrennt den Prüfer


def test_niemand_importiert_den_pruefer():
    for f in SRC.rglob("*.py"):
        rel = f.relative_to(SRC)
        if rel.parts[0] == "verifier" or (len(rel.parts) == 1 and rel.name in OPERATOR_MODULES):
            continue
        assert not any(t.startswith("verifier") for t in _imports(f)), f


def test_nur_bekannte_module_auf_oberster_ebene():
    top = {f.name for f in SRC.glob("*.py")}
    assert top == {"__init__.py"} | OPERATOR_MODULES
