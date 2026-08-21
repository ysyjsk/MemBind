"""Provider-free certification helpers for the Native Serial reference."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Sequence
from typing import Any


class NativeSerialCertificationError(ValueError):
    """A captured serial fixture cannot be compared safely."""


def _digest(value: object) -> str:
    return hashlib.sha256(
        json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode(
            "utf-8"
        )
    ).hexdigest()


def _rows(value: Sequence[object]) -> tuple[dict[str, Any], ...]:
    if isinstance(value, (str, bytes)) or not isinstance(value, Sequence):
        raise NativeSerialCertificationError("SERIAL_FIXTURE_SEQUENCE_REQUIRED")
    result = tuple(value)
    if any(not isinstance(row, dict) for row in result):
        raise NativeSerialCertificationError("SERIAL_FIXTURE_ROW_INVALID")
    return result  # type: ignore[return-value]


def _operator_lineage(rows: tuple[dict[str, Any], ...]) -> tuple[tuple[str, ...], ...]:
    return tuple(tuple(row.get("operators", ())) for row in rows)


def _effects(rows: tuple[dict[str, Any], ...]) -> tuple[object, ...]:
    return tuple(row.get("effect") for row in rows)


def _publication(rows: tuple[dict[str, Any], ...]) -> tuple[object, ...]:
    return tuple(row.get("publication", row.get("source_sequence")) for row in rows)


def certify_native_serial_fixture(
    official_rows: Sequence[object], harness_rows: Sequence[object]
) -> dict[str, Any]:
    """Compare an official-style serial loop with the B0 harness trace."""

    official = _rows(official_rows)
    harness = _rows(harness_rows)
    source_official = tuple(row.get("source_sequence") for row in official)
    source_harness = tuple(row.get("source_sequence") for row in harness)
    lineage_equal = _operator_lineage(official) == _operator_lineage(harness)
    effects_equal = _effects(official) == _effects(harness)
    publication_equal = _publication(official) == _publication(harness)
    work_equal = len(official) == len(harness) and sum(
        len(row.get("operators", ())) for row in official
    ) == sum(len(row.get("operators", ())) for row in harness)
    checks = {
        "source_coverage_equal": source_official == source_harness,
        "operator_lineage_equal": lineage_equal,
        "work_cardinality_equal": work_equal,
        "effect_cardinality_equal": effects_equal,
        "publication_order_equal": publication_equal,
        "canonical_effect_digest_equal": _digest(_effects(official)) == _digest(_effects(harness)),
    }
    passed = all(checks.values())
    return {
        "schema_version": "sfwb.v1.3.native-serial-certification.v1",
        "provider_free": True,
        "official_style_loop": "for episode in source_order: await graphiti.add_episode(episode)",
        "harness": "saturated_fixed_work_baseline_v1_3 B0",
        "source_count": len(official),
        "checks": checks,
        **checks,
        "status": "PASS" if passed else "STOP_NATIVE_SERIAL_REFERENCE_NOT_CERTIFIED",
        "decision": "NATIVE_SERIAL_REFERENCE_CERTIFIED" if passed else "STOP_NATIVE_SERIAL_REFERENCE_NOT_CERTIFIED",
    }


__all__ = ["NativeSerialCertificationError", "certify_native_serial_fixture"]
