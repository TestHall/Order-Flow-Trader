"""Validierung gegen die JSON-Schemas unter schemas/ (Handbuch Kap. 5)."""
from __future__ import annotations

import json
from functools import lru_cache
from typing import Any

from jsonschema import Draft202012Validator
from referencing import Registry, Resource

from orderflow.common.paths import SCHEMA_DIR

SCHEMAS = ("market_event", "snapshot", "decision", "review")


class SchemaViolation(ValueError):
    def __init__(self, schema: str, errors: list[str]):
        super().__init__(f"{schema}: " + "; ".join(errors))
        self.schema = schema
        self.errors = errors


@lru_cache(maxsize=1)
def _registry() -> Registry:
    resources = []
    for name in SCHEMAS:
        doc = json.loads((SCHEMA_DIR / f"{name}.schema.json").read_text(encoding="utf-8"))
        resources.append((doc["$id"], Resource.from_contents(doc)))
    return Registry().with_resources(resources)


@lru_cache(maxsize=None)
def validator(name: str) -> Draft202012Validator:
    doc = json.loads((SCHEMA_DIR / f"{name}.schema.json").read_text(encoding="utf-8"))
    Draft202012Validator.check_schema(doc)
    return Draft202012Validator(doc, registry=_registry())


def errors(instance: Any, name: str) -> list[str]:
    return sorted(
        f"{'/'.join(map(str, e.absolute_path)) or '<root>'}: {e.message}"
        for e in validator(name).iter_errors(instance)
    )


def validate(instance: Any, name: str) -> None:
    errs = errors(instance, name)
    if errs:
        raise SchemaViolation(name, errs)
