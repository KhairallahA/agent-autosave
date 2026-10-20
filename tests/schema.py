"""A small JSON Schema validator for the subset our schemas use (stdlib only).

Supported: type (one or a list), const, enum, required, properties,
additionalProperties (true, false or a schema for every other value), items, minItems, minLength, minimum,
pattern, oneOf, and the annotations $schema, title, description and format
(format is not checked; our timestamps carry a pattern). Any other keyword
raises, so a schema can't quietly use something this does not check.
"""
from __future__ import annotations

import re
from typing import Any

ANNOTATIONS = {"$schema", "title", "description", "format"}
KEYWORDS = ANNOTATIONS | {"type", "const", "enum", "required", "properties", "additionalProperties", "items",
                          "minItems", "minLength", "minimum", "pattern", "oneOf"}
TYPES = {"object": dict, "array": list, "string": str, "boolean": bool, "null": type(None)}


def _is(value: Any, kind: str) -> bool:
    if kind == "integer":
        return isinstance(value, int) and not isinstance(value, bool)
    if kind == "number":
        return isinstance(value, (int, float)) and not isinstance(value, bool)
    return isinstance(value, TYPES[kind])


def errors(value: Any, schema: dict[str, Any], path: str = "$") -> list[str]:
    """Every way `value` breaks `schema`, as "path: problem" lines."""
    unknown = set(schema) - KEYWORDS
    if unknown:
        raise ValueError("{}: unsupported schema keywords {}".format(path, sorted(unknown)))
    found: list[str] = []
    if "type" in schema:
        kinds = schema["type"] if isinstance(schema["type"], list) else [schema["type"]]
        if not any(_is(value, kind) for kind in kinds):
            return ["{}: expected {}, got {}".format(path, "/".join(kinds), type(value).__name__)]
    if "const" in schema and value != schema["const"]:
        found.append("{}: must be {!r}".format(path, schema["const"]))
    if "enum" in schema and value not in schema["enum"]:
        found.append("{}: {!r} is not one of {}".format(path, value, schema["enum"]))
    if "oneOf" in schema:
        matches = [s for s in schema["oneOf"] if not errors(value, s, path)]
        if len(matches) != 1:
            found.append("{}: matches {} of the oneOf branches, not exactly one".format(path, len(matches)))
    if isinstance(value, str):
        if len(value) < schema.get("minLength", 0):
            found.append("{}: shorter than {}".format(path, schema["minLength"]))
        if "pattern" in schema and not re.search(schema["pattern"], value):
            found.append("{}: {!r} does not match {}".format(path, value, schema["pattern"]))
    if _is(value, "number") and "minimum" in schema and value < schema["minimum"]:
        found.append("{}: below {}".format(path, schema["minimum"]))
    if isinstance(value, list):
        if len(value) < schema.get("minItems", 0):
            found.append("{}: fewer than {} items".format(path, schema["minItems"]))
        if "items" in schema:
            for index, item in enumerate(value):
                found += errors(item, schema["items"], "{}[{}]".format(path, index))
    if isinstance(value, dict):
        for key in schema.get("required", []):
            if key not in value:
                found.append("{}: missing {}".format(path, key))
        properties = schema.get("properties", {})
        for key, item in value.items():
            if key in properties:
                found += errors(item, properties[key], "{}.{}".format(path, key))
            elif schema.get("additionalProperties", True) is False:
                found.append("{}: unexpected field {}".format(path, key))
            elif isinstance(schema.get("additionalProperties"), dict):
                found += errors(item, schema["additionalProperties"], "{}.{}".format(path, key))
    return found
