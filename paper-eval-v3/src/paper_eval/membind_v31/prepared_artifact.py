"""State-unbound, self-verifying PreparedArtifact for MemBind v3.1."""

from __future__ import annotations

import json
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from paper_eval.artifacts import canonical_bytes, payload_sha256


_SHA256 = re.compile(r"^[0-9a-f]{64}$")


class PreparedArtifactError(ValueError):
    """Prepared data are malformed, tampered, or bound to another identity."""


def _fail(code: str) -> PreparedArtifactError:
    return PreparedArtifactError(code)


def _sha256(value: object, code: str) -> str:
    if not isinstance(value, str) or _SHA256.fullmatch(value) is None:
        raise _fail(code)
    return value


def _sequence(value: object) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise _fail("source_sequence_invalid")
    return value


def _canonical_records(
    value: object,
    *,
    code: str,
    optional: bool,
) -> tuple[str, ...] | None:
    if optional and value is None:
        return None
    if isinstance(value, (str, bytes)) or not isinstance(value, Sequence):
        raise _fail(code)
    records: list[str] = []
    for item in value:
        if not isinstance(item, Mapping):
            raise _fail(code)
        try:
            encoded = canonical_bytes(dict(item)).decode("utf-8")
            decoded = json.loads(encoded)
        except (TypeError, ValueError, UnicodeError, json.JSONDecodeError):
            raise _fail(code) from None
        if not isinstance(decoded, dict):
            raise _fail(code)
        records.append(encoded)
    return tuple(records)


def _decoded(records: tuple[str, ...] | None) -> list[dict[str, Any]] | None:
    if records is None:
        return None
    return [json.loads(item) for item in records]


def _canonical_mapping(value: object, *, code: str) -> str:
    if not isinstance(value, Mapping):
        raise _fail(code)
    try:
        encoded = canonical_bytes(dict(value)).decode("utf-8")
        decoded = json.loads(encoded)
    except (TypeError, ValueError, UnicodeError, json.JSONDecodeError):
        raise _fail(code) from None
    if not isinstance(decoded, dict):
        raise _fail(code)
    return encoded


@dataclass(frozen=True, slots=True)
class PreparedArtifact:
    """Canonical raw extraction output bound to source, evidence, and proof."""

    source_sequence: int
    source_sha256: str
    evidence_sha256: str
    certification_sha256: str
    _raw_nodes_json: tuple[str, ...]
    _raw_edges_json: tuple[str, ...] | None
    _pure_intermediates_json: str
    artifact_sha256: str

    @classmethod
    def create(
        cls,
        *,
        source_sequence: int,
        source_sha256: str,
        evidence_sha256: str,
        certification_sha256: str,
        raw_nodes: Sequence[Mapping[str, object]],
        raw_edges: Sequence[Mapping[str, object]] | None = None,
        pure_intermediates: Mapping[str, object] | None = None,
    ) -> "PreparedArtifact":
        sequence = _sequence(source_sequence)
        source = _sha256(source_sha256, "source_sha256_invalid")
        evidence = _sha256(evidence_sha256, "evidence_sha256_invalid")
        certification = _sha256(
            certification_sha256, "certification_sha256_invalid"
        )
        nodes = _canonical_records(raw_nodes, code="raw_nodes_invalid", optional=False)
        assert nodes is not None
        edges = _canonical_records(raw_edges, code="raw_edges_invalid", optional=True)
        intermediates = _canonical_mapping(
            {} if pure_intermediates is None else pure_intermediates,
            code="pure_intermediates_invalid",
        )
        payload = cls._payload_from_parts(
            source_sequence=sequence,
            source_sha256=source,
            evidence_sha256=evidence,
            certification_sha256=certification,
            raw_nodes_json=nodes,
            raw_edges_json=edges,
            pure_intermediates_json=intermediates,
        )
        return cls(
            source_sequence=sequence,
            source_sha256=source,
            evidence_sha256=evidence,
            certification_sha256=certification,
            _raw_nodes_json=nodes,
            _raw_edges_json=edges,
            _pure_intermediates_json=intermediates,
            artifact_sha256=payload_sha256(payload),
        )

    @staticmethod
    def _payload_from_parts(
        *,
        source_sequence: int,
        source_sha256: str,
        evidence_sha256: str,
        certification_sha256: str,
        raw_nodes_json: tuple[str, ...],
        raw_edges_json: tuple[str, ...] | None,
        pure_intermediates_json: str,
    ) -> dict[str, object]:
        return {
            "certification_sha256": certification_sha256,
            "evidence_sha256": evidence_sha256,
            "raw_edges": _decoded(raw_edges_json),
            "raw_nodes": _decoded(raw_nodes_json),
            "pure_intermediates": json.loads(pure_intermediates_json),
            "source_sequence": source_sequence,
            "source_sha256": source_sha256,
        }

    @property
    def raw_nodes(self) -> list[dict[str, Any]]:
        result = _decoded(self._raw_nodes_json)
        assert result is not None
        return result

    @property
    def raw_edges(self) -> list[dict[str, Any]] | None:
        return _decoded(self._raw_edges_json)

    @property
    def pure_intermediates(self) -> dict[str, Any]:
        return json.loads(self._pure_intermediates_json)

    def payload(self) -> dict[str, object]:
        return self._payload_from_parts(
            source_sequence=self.source_sequence,
            source_sha256=self.source_sha256,
            evidence_sha256=self.evidence_sha256,
            certification_sha256=self.certification_sha256,
            raw_nodes_json=self._raw_nodes_json,
            raw_edges_json=self._raw_edges_json,
            pure_intermediates_json=self._pure_intermediates_json,
        )

    def to_document(self) -> dict[str, object]:
        return {**self.payload(), "artifact_sha256": self.artifact_sha256}

    def verify(
        self,
        *,
        expected_source_sha256: str | None = None,
        expected_evidence_sha256: str | None = None,
        expected_certification_sha256: str | None = None,
    ) -> "PreparedArtifact":
        _sequence(self.source_sequence)
        source = _sha256(self.source_sha256, "source_sha256_invalid")
        evidence = _sha256(self.evidence_sha256, "evidence_sha256_invalid")
        certification = _sha256(
            self.certification_sha256, "certification_sha256_invalid"
        )
        _sha256(self.artifact_sha256, "artifact_sha256_invalid")
        nodes = _canonical_records(
            _decoded(self._raw_nodes_json), code="raw_nodes_invalid", optional=False
        )
        edges = _canonical_records(
            _decoded(self._raw_edges_json), code="raw_edges_invalid", optional=True
        )
        intermediates = _canonical_mapping(
            json.loads(self._pure_intermediates_json),
            code="pure_intermediates_invalid",
        )
        if (
            nodes != self._raw_nodes_json
            or edges != self._raw_edges_json
            or intermediates != self._pure_intermediates_json
        ):
            raise _fail("artifact_not_canonical")
        if payload_sha256(self.payload()) != self.artifact_sha256:
            raise _fail("artifact_hash_mismatch")
        if expected_source_sha256 is not None and source != _sha256(
            expected_source_sha256, "expected_source_sha256_invalid"
        ):
            raise _fail("source_identity_conflict")
        if expected_evidence_sha256 is not None and evidence != _sha256(
            expected_evidence_sha256, "expected_evidence_sha256_invalid"
        ):
            raise _fail("evidence_identity_conflict")
        if expected_certification_sha256 is not None and certification != _sha256(
            expected_certification_sha256, "expected_certification_sha256_invalid"
        ):
            raise _fail("certification_identity_conflict")
        return self


__all__ = ["PreparedArtifact", "PreparedArtifactError"]
