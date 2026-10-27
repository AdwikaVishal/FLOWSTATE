"""A small, dependency-free JSON-schema subset for declared inputs and outputs.

The engine validates data at the boundary without executing generated code and
without a full JSON Schema implementation. Supported keywords: ``type``,
``required``, ``properties``, ``additionalProperties``, ``enum``, ``const``,
``minimum``, ``maximum``, ``minLength``, ``maxLength``, ``pattern``, ``items``,
``anyOf``. Unsupported keywords are ignored deliberately; a schema is a guard,
not a general-purpose validator.
"""

from __future__ import annotations

import re
from typing import Any

_TYPE_CHECKS = {
    "object": lambda value: isinstance(value, dict),
    "array": lambda value: isinstance(value, list),
    "string": lambda value: isinstance(value, str),
    "number": lambda value: isinstance(value, (int, float)) and not isinstance(value, bool),
    "integer": lambda value: isinstance(value, int) and not isinstance(value, bool),
    "boolean": lambda value: isinstance(value, bool),
    "null": lambda value: value is None,
}


def validate_schema(value: Any, schema: dict[str, Any] | None, *, path: str = "value") -> list[str]:
    """Return a list of human-readable validation failures (empty means valid)."""
    if not schema or not isinstance(schema, dict):
        return []

    errors: list[str] = []
    expected_type = schema.get("type")
    if expected_type:
        types = expected_type if isinstance(expected_type, list) else [expected_type]
        if not any(_TYPE_CHECKS.get(str(item), lambda _: True)(value) for item in types):
            errors.append(f"{path} must be of type {'/'.join(str(item) for item in types)}")
            return errors

    if "const" in schema and value != schema["const"]:
        errors.append(f"{path} must equal {schema['const']!r}")
    if isinstance(schema.get("enum"), list) and value not in schema["enum"]:
        errors.append(f"{path} must be one of {schema['enum']!r}")

    if isinstance(value, dict):
        for required in schema.get("required", []) or []:
            if required not in value:
                errors.append(f"{path}.{required} is required")
        properties = schema.get("properties") or {}
        for key, child in properties.items():
            if key in value and isinstance(child, dict):
                errors.extend(validate_schema(value[key], child, path=f"{path}.{key}"))
        if schema.get("additionalProperties") is False:
            allowed = set(properties)
            for key in value:
                if key not in allowed:
                    errors.append(f"{path}.{key} is not allowed")

    if isinstance(value, list) and isinstance(schema.get("items"), dict):
        for index, item in enumerate(value):
            errors.extend(validate_schema(item, schema["items"], path=f"{path}.{index}"))

    if isinstance(value, str):
        if isinstance(schema.get("minLength"), int) and len(value) < schema["minLength"]:
            errors.append(f"{path} must be at least {schema['minLength']} characters")
        if isinstance(schema.get("maxLength"), int) and len(value) > schema["maxLength"]:
            errors.append(f"{path} must be at most {schema['maxLength']} characters")
        if isinstance(schema.get("pattern"), str):
            try:
                if re.search(schema["pattern"], value) is None:
                    errors.append(f"{path} does not match {schema['pattern']!r}")
            except re.error:
                errors.append(f"{path} schema pattern is invalid")

    if isinstance(value, (int, float)) and not isinstance(value, bool):
        if isinstance(schema.get("minimum"), (int, float)) and value < schema["minimum"]:
            errors.append(f"{path} must be >= {schema['minimum']}")
        if isinstance(schema.get("maximum"), (int, float)) and value > schema["maximum"]:
            errors.append(f"{path} must be <= {schema['maximum']}")

    any_of = schema.get("anyOf")
    if (
        isinstance(any_of, list)
        and any_of
        and not any(
            not validate_schema(value, item, path=path) for item in any_of if isinstance(item, dict)
        )
    ):
        errors.append(f"{path} does not satisfy any allowed variant")

    return errors
