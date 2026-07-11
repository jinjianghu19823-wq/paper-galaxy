"""Strict decoders for JSON stored in SQLite columns."""

from __future__ import annotations

import json
import math
from typing import Any


class StoredJSONError(ValueError):
    """Raised when persisted JSON is malformed or has the wrong shape."""

    def __init__(self, code: str, message: str) -> None:
        self.code = code
        super().__init__(message)


def load_json_list(value: object) -> list[object]:
    """Decode an optional persisted JSON list without hiding corruption."""

    decoded = _decode(value, empty=[])
    if not isinstance(decoded, list):
        raise StoredJSONError(
            "json_type_mismatch", "Persisted JSON value must be a list."
        )
    return list(decoded)


def load_json_object(value: object) -> dict[str, object]:
    """Decode an optional persisted JSON object without hiding corruption."""

    decoded = _decode(value, empty={})
    if not isinstance(decoded, dict):
        raise StoredJSONError(
            "json_type_mismatch", "Persisted JSON value must be an object."
        )
    if not all(isinstance(key, str) for key in decoded):
        raise StoredJSONError(
            "json_type_mismatch", "Persisted JSON object keys must be strings."
        )
    return dict(decoded)


def _decode(value: object, *, empty: Any) -> object:
    if value is None:
        return empty
    if isinstance(value, (list, dict)):
        _validate_json_value(value)
        return value
    if not isinstance(value, str):
        raise StoredJSONError(
            "json_type_mismatch", "Persisted JSON must be text or a decoded container."
        )
    try:
        decoded = json.loads(
            value,
            parse_constant=_reject_non_finite_constant,
            object_pairs_hook=_reject_duplicate_keys,
        )
        _validate_json_value(decoded)
        return decoded
    except (json.JSONDecodeError, _StrictJSONDecodeError) as exc:
        raise StoredJSONError("invalid_json", "Persisted JSON is malformed.") from exc


class _StrictJSONDecodeError(ValueError):
    """Internal signal for JSON accepted by Python but rejected by RFC JSON."""


def _reject_non_finite_constant(value: str) -> object:
    raise _StrictJSONDecodeError(f"non-finite JSON constant: {value}")


def _reject_duplicate_keys(pairs: list[tuple[str, object]]) -> dict[str, object]:
    decoded: dict[str, object] = {}
    for key, value in pairs:
        if key in decoded:
            raise _StrictJSONDecodeError(f"duplicate JSON object key: {key}")
        decoded[key] = value
    return decoded


def _validate_json_value(value: object) -> None:
    if isinstance(value, float) and not math.isfinite(value):
        raise StoredJSONError(
            "invalid_json", "Persisted JSON contains a non-finite number."
        )
    if isinstance(value, list):
        for item in value:
            _validate_json_value(item)
    elif isinstance(value, dict):
        for key, item in value.items():
            if not isinstance(key, str):
                raise StoredJSONError(
                    "json_type_mismatch",
                    "Persisted JSON object keys must be strings.",
                )
            _validate_json_value(item)
    elif value is not None and not isinstance(value, (str, int, float, bool)):
        raise StoredJSONError(
            "json_type_mismatch",
            "Persisted JSON contains a non-JSON value.",
        )
