"""Passive semantic fingerprints for already-produced MemBind runtime objects.

The helper is intentionally provider-free.  Callers must name the semantic
fields explicitly; runtime metadata and object representation are never part
of the digest.  This makes the resulting telemetry useful for paired offline
analysis without changing a Graphiti call, query, prompt, batch, or effect.
"""

from __future__ import annotations

import hashlib
import json
import math
from collections.abc import Mapping, Sequence
from dataclasses import asdict, is_dataclass
from typing import Any


FINGERPRINT_SCHEMA_VERSION = "sfwb.v1.3.v5.semantic-fingerprint.v1"
_RUNTIME_METADATA_NAMES = frozenset(
    {
        "run_id",
        "namespace",
        "group_id",
        "stream_id",
        "arrival_time_ns",
        "timestamp_ns",
        "start_ns",
        "end_ns",
        "duration_ns",
        "event_sequence",
        "request_id",
        "trace_id",
        "span_id",
        "memory_address",
        "object_id",
    }
)


class SemanticFingerprintError(ValueError):
    """The semantic projection is ambiguous or not canonically serializable."""


def _fail(code: str) -> SemanticFingerprintError:
    return SemanticFingerprintError(code)


def _mapping(value: object) -> Mapping[str, Any]:
    if isinstance(value, Mapping):
        result = value
    elif is_dataclass(value) and not isinstance(value, type):
        result = asdict(value)
    else:
        model_dump = getattr(value, "model_dump", None)
        if callable(model_dump):
            try:
                candidate = model_dump(mode="json")
            except TypeError:
                candidate = model_dump()
            if isinstance(candidate, Mapping):
                result = candidate
            else:
                raise _fail("SEMANTIC_OBJECT_MAPPING_REQUIRED")
        else:
            to_dict = getattr(value, "dict", None)
            if callable(to_dict):
                candidate = to_dict()
                if isinstance(candidate, Mapping):
                    result = candidate
                else:
                    raise _fail("SEMANTIC_OBJECT_MAPPING_REQUIRED")
            else:
                raise _fail("SEMANTIC_OBJECT_MAPPING_REQUIRED")
    if any(not isinstance(key, str) for key in result):
        raise _fail("SEMANTIC_MAPPING_KEYS_MUST_BE_STRINGS")
    return result


def _lookup(value: object, field: str) -> object:
    current: object = value
    for component in field.split("."):
        if isinstance(current, Mapping):
            if component not in current:
                raise _fail(f"SEMANTIC_FIELD_MISSING:{field}")
            current = current[component]
            continue
        try:
            current = getattr(current, component)
        except AttributeError:
            raise _fail(f"SEMANTIC_FIELD_MISSING:{field}") from None
    return current


def _canonical(value: object) -> object:
    if value is None or isinstance(value, (str, bool, int)):
        return value
    if isinstance(value, float):
        if not math.isfinite(value):
            raise _fail("NONFINITE_NUMBER")
        return value
    if isinstance(value, Mapping):
        if any(not isinstance(key, str) for key in value):
            raise _fail("SEMANTIC_MAPPING_KEYS_MUST_BE_STRINGS")
        return {key: _canonical(value[key]) for key in sorted(value)}
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        return [_canonical(item) for item in value]
    if isinstance(value, (set, frozenset)):
        raise _fail("UNORDERED_COLLECTION_AMBIGUOUS")
    if is_dataclass(value) and not isinstance(value, type):
        return _canonical(asdict(value))
    raise _fail("SEMANTIC_VALUE_NOT_CANONICAL")


def _bytes(value: object) -> bytes:
    try:
        return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")
    except (TypeError, ValueError, UnicodeError) as error:
        raise _fail("SEMANTIC_CANONICAL_ENCODING_FAILED") from error


def _validate_fields(semantic_fields: Sequence[str], runtime_metadata_fields: Sequence[str]) -> tuple[tuple[str, ...], frozenset[str]]:
    if isinstance(semantic_fields, (str, bytes)) or not isinstance(semantic_fields, Sequence) or not semantic_fields:
        raise _fail("SEMANTIC_FIELDS_REQUIRED")
    if isinstance(runtime_metadata_fields, (str, bytes)) or not isinstance(runtime_metadata_fields, Sequence):
        raise _fail("RUNTIME_METADATA_FIELDS_INVALID")
    fields = tuple(semantic_fields)
    if any(not isinstance(field, str) or not field for field in fields) or len(set(fields)) != len(fields):
        raise _fail("SEMANTIC_FIELDS_INVALID")
    metadata = frozenset(str(field) for field in runtime_metadata_fields) | _RUNTIME_METADATA_NAMES
    if any(field.split(".", 1)[0] in metadata or field in metadata for field in fields):
        raise _fail("RUNTIME_METADATA_FIELD_DECLARED_SEMANTIC")
    return fields, metadata


def semantic_projection(
    value: object,
    *,
    boundary: str,
    semantic_fields: Sequence[str],
    runtime_metadata_fields: Sequence[str] = (),
) -> dict[str, Any]:
    """Return the explicit, metadata-free canonical projection for one object."""

    if not isinstance(boundary, str) or not boundary.strip():
        raise _fail("BOUNDARY_INVALID")
    fields, _ = _validate_fields(semantic_fields, runtime_metadata_fields)
    source = _mapping(value)
    # Reading only declared paths is deliberate: hashing an entire runtime
    # object would silently mix semantic fields with run/transport metadata.
    selected = {field: _canonical(_lookup(source, field)) for field in fields}
    return {"boundary": boundary, "fields": selected, "schema_version": FINGERPRINT_SCHEMA_VERSION}


def semantic_fingerprint(
    value: object,
    *,
    boundary: str,
    semantic_fields: Sequence[str],
    runtime_metadata_fields: Sequence[str] = (),
) -> str:
    """Hash one already-produced semantic object using explicit fields only."""

    return hashlib.sha256(_bytes(semantic_projection(value, boundary=boundary, semantic_fields=semantic_fields, runtime_metadata_fields=runtime_metadata_fields))).hexdigest()


def fingerprint_records(
    values: Sequence[object],
    *,
    boundary: str,
    semantic_fields: Sequence[str],
    runtime_metadata_fields: Sequence[str] = (),
) -> dict[str, Any]:
    """Return count plus ordered and membership digests for a semantic list."""

    if isinstance(values, (str, bytes)) or not isinstance(values, Sequence):
        raise _fail("SEMANTIC_RECORD_SEQUENCE_REQUIRED")
    projections = [semantic_projection(value, boundary=boundary, semantic_fields=semantic_fields, runtime_metadata_fields=runtime_metadata_fields) for value in values]
    ordered_payload = {"boundary": boundary, "items": projections, "schema_version": FINGERPRINT_SCHEMA_VERSION}
    membership_payload = {
        "boundary": boundary,
        "items": sorted(projections, key=_bytes),
        "schema_version": FINGERPRINT_SCHEMA_VERSION,
    }
    return {
        "schema_version": FINGERPRINT_SCHEMA_VERSION,
        "boundary": boundary,
        "count": len(projections),
        "cardinality": len(projections),
        "output_count": len(projections),
        "ordered_identity_sha256": hashlib.sha256(_bytes(ordered_payload)).hexdigest(),
        "membership_identity_sha256": hashlib.sha256(_bytes(membership_payload)).hexdigest(),
        "ordered_semantic_identity_sha256": hashlib.sha256(_bytes(ordered_payload)).hexdigest(),
        "ordering_preserving_sha256": hashlib.sha256(_bytes(ordered_payload)).hexdigest(),
        "content_identity_sha256": hashlib.sha256(_bytes(membership_payload)).hexdigest(),
    }


__all__ = [
    "FINGERPRINT_SCHEMA_VERSION",
    "SemanticFingerprintError",
    "fingerprint_records",
    "semantic_fingerprint",
    "semantic_projection",
]
