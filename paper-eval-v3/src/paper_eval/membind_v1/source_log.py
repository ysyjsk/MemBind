"""Immutable, contiguous source-log contracts for MemBind-v1.

This module deliberately contains only JSON-compatible workload data.  It has
no Graphiti, database, or model-service dependency, so a later compiler can be
given a source snapshot without receiving mutable graph capabilities.
"""

from __future__ import annotations

import json
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from paper_eval.artifacts import canonical_bytes, payload_sha256


_SHA256 = re.compile(r"^[0-9a-f]{64}$")


class MemBindV1SourceLogError(ValueError):
    """A source inventory is malformed or no longer immutable."""


def _fail(code: str) -> MemBindV1SourceLogError:
    return MemBindV1SourceLogError(code)


def _nonempty_text(value: object, code: str) -> str:
    if not isinstance(value, str) or not value:
        raise _fail(code)
    return value


def _nonnegative_int(value: object, code: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise _fail(code)
    return value


def _sha256(value: object, code: str) -> str:
    if not isinstance(value, str) or _SHA256.fullmatch(value) is None:
        raise _fail(code)
    return value


def _canonical_mapping(value: object, code: str) -> tuple[str, dict[str, Any]]:
    if not isinstance(value, Mapping):
        raise _fail(code)
    try:
        encoded = canonical_bytes(dict(value))
        decoded = json.loads(encoded.decode("utf-8"))
    except (TypeError, ValueError, UnicodeError, json.JSONDecodeError):
        raise _fail(code) from None
    if not isinstance(decoded, dict):
        raise _fail(code)
    return encoded.decode("utf-8"), decoded


@dataclass(frozen=True, slots=True)
class SourceRecord:
    """A self-hashing immutable projection of one source episode."""

    source_sequence: int
    episode_uuid: str
    group_id: str
    reference_time_ns: int
    source_filter: str
    _episode_projection_json: str
    source_sha256: str

    @classmethod
    def create(
        cls,
        *,
        source_sequence: int,
        episode_uuid: str,
        group_id: str,
        reference_time_ns: int,
        source_filter: str,
        episode_projection: Mapping[str, object],
        source_sha256: str | None = None,
    ) -> "SourceRecord":
        """Canonicalize the source payload and bind it to a SHA-256 identity."""

        sequence = _nonnegative_int(source_sequence, "source_sequence_invalid")
        uuid = _nonempty_text(episode_uuid, "episode_uuid_invalid")
        group = _nonempty_text(group_id, "group_id_invalid")
        timestamp = _nonnegative_int(reference_time_ns, "reference_time_invalid")
        filter_value = _nonempty_text(source_filter, "source_filter_invalid")
        projection_json, projection = _canonical_mapping(
            episode_projection, "episode_projection_invalid"
        )
        canonical_source = {
            "episode_projection": projection,
            "episode_uuid": uuid,
            "group_id": group,
            "reference_time_ns": timestamp,
            "source_filter": filter_value,
        }
        computed = payload_sha256(canonical_source)
        if source_sha256 is not None:
            supplied = _sha256(source_sha256, "source_hash_invalid")
            if supplied != computed:
                raise _fail("source_hash_mismatch")
        return cls(
            source_sequence=sequence,
            episode_uuid=uuid,
            group_id=group,
            reference_time_ns=timestamp,
            source_filter=filter_value,
            _episode_projection_json=projection_json,
            source_sha256=computed,
        )

    @property
    def episode_projection(self) -> dict[str, Any]:
        """Return a defensive decoded copy of the frozen source projection."""

        return json.loads(self._episode_projection_json)

    @property
    def episode_projection_sha256(self) -> str:
        return payload_sha256(json.loads(self._episode_projection_json))

    def verify(self) -> "SourceRecord":
        """Verify direct construction cannot bypass the source identity binding."""

        _nonnegative_int(self.source_sequence, "source_sequence_invalid")
        uuid = _nonempty_text(self.episode_uuid, "episode_uuid_invalid")
        group = _nonempty_text(self.group_id, "group_id_invalid")
        timestamp = _nonnegative_int(self.reference_time_ns, "reference_time_invalid")
        filter_value = _nonempty_text(self.source_filter, "source_filter_invalid")
        try:
            raw_projection = json.loads(self._episode_projection_json)
        except (TypeError, ValueError, json.JSONDecodeError):
            raise _fail("episode_projection_invalid") from None
        projection_json, projection = _canonical_mapping(
            raw_projection, "episode_projection_invalid"
        )
        if projection_json != self._episode_projection_json:
            raise _fail("episode_projection_not_canonical")
        source_hash = _sha256(self.source_sha256, "source_hash_invalid")
        expected = payload_sha256(
            {
                "episode_projection": projection,
                "episode_uuid": uuid,
                "group_id": group,
                "reference_time_ns": timestamp,
                "source_filter": filter_value,
            }
        )
        if source_hash != expected:
            raise _fail("source_hash_mismatch")
        return self

    def inventory_projection(self) -> dict[str, object]:
        """Return the complete stable representation bound by a SourceLog."""

        return {
            "episode_projection": self.episode_projection,
            "episode_uuid": self.episode_uuid,
            "group_id": self.group_id,
            "reference_time_ns": self.reference_time_ns,
            "source_filter": self.source_filter,
            "source_sequence": self.source_sequence,
            "source_sha256": self.source_sha256,
        }


@dataclass(frozen=True, slots=True)
class SourceLog:
    """An exact append-only source inventory represented as an immutable tuple."""

    _records: tuple[SourceRecord, ...]
    inventory_sha256: str

    @classmethod
    def create(cls, records: Sequence[SourceRecord]) -> "SourceLog":
        if isinstance(records, (str, bytes)) or not isinstance(records, Sequence):
            raise _fail("source_inventory_invalid")
        selected = tuple(records)
        if not selected:
            raise _fail("source_inventory_empty")
        if any(not isinstance(record, SourceRecord) for record in selected):
            raise _fail("source_record_invalid")
        for record in selected:
            record.verify()
        sequences = [record.source_sequence for record in selected]
        if sequences != list(range(len(selected))):
            raise _fail("source_sequence_not_contiguous")
        source_hashes = [record.source_sha256 for record in selected]
        if len(set(source_hashes)) != len(source_hashes):
            raise _fail("duplicate_source_identity")
        inventory = [record.inventory_projection() for record in selected]
        return cls(_records=selected, inventory_sha256=payload_sha256(inventory))

    @property
    def source_count(self) -> int:
        return len(self._records)

    @property
    def source_sequences(self) -> tuple[int, ...]:
        return tuple(record.source_sequence for record in self._records)

    @property
    def records(self) -> tuple[SourceRecord, ...]:
        return self._records

    def record(self, source_sequence: int) -> SourceRecord:
        sequence = _nonnegative_int(source_sequence, "source_sequence_invalid")
        try:
            return self._records[sequence]
        except IndexError:
            raise _fail("source_sequence_out_of_range") from None


__all__ = [
    "MemBindV1SourceLogError",
    "SourceLog",
    "SourceRecord",
]
