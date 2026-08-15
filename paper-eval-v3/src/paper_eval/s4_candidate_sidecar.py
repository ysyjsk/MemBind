"""Durable hash-only candidate sidecar and one-shot replay binder for S4."""

from __future__ import annotations

import copy
import contextvars
import json
import os
import re
from collections import Counter
from collections.abc import Iterator, Mapping, Sequence
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from .artifacts import canonical_bytes, payload_sha256


SIDECAR_SCHEMA = "membind.paper-eval-v3.s4-candidate-sidecar.v1"
CALL_SCHEMA = "membind.paper-eval-v3.s4-candidate-call.v1"
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_HISTORY_ID = re.compile(r"^[0-9a-f]{8}$")
_SAFE_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
_IDENTITY_FIELDS = {
    "attempt_id",
    "cache_id",
    "episode_manifest_sha256",
    "history_id",
    "projection_schema_sha256",
}
_FORBIDDEN_KEYS = {
    "created_at",
    "fact",
    "group_id",
    "prompt",
    "rank",
    "raw_fact",
    "raw_prompt",
    "raw_response",
    "response",
    "uuid",
}


class CandidateSidecarError(ValueError):
    """The sidecar cannot prove a unique, partition-preserving binding."""


CandidateSidecarReplayBinding = Mapping[str, Any]
_CURRENT_REPLAY_BINDING: contextvars.ContextVar[
    CandidateSidecarReplayBinding | None
] = contextvars.ContextVar("s4_candidate_sidecar_replay_binding", default=None)


def current_replay_binding() -> CandidateSidecarReplayBinding | None:
    """Return the replay binding active in the current execution context."""

    return _CURRENT_REPLAY_BINDING.get()


def replay_binding_sha256(binding: CandidateSidecarReplayBinding) -> str:
    """Hash one validated in-memory binding for cache acknowledgement."""

    if not isinstance(binding, Mapping):
        raise CandidateSidecarError("replay binding is not a mapping")
    selected = copy.deepcopy(dict(binding))
    _public_hash_only(selected)
    return payload_sha256(selected)


@contextmanager
def activate_replay_binding(
    binding: CandidateSidecarReplayBinding,
) -> Iterator[CandidateSidecarReplayBinding]:
    """Activate one replay binding and restore the prior context on exit."""

    if not isinstance(binding, Mapping):
        raise CandidateSidecarError("replay binding is not a mapping")
    selected = copy.deepcopy(dict(binding))
    token = _CURRENT_REPLAY_BINDING.set(selected)
    try:
        yield selected
    finally:
        _CURRENT_REPLAY_BINDING.reset(token)


def _sha(value: object, *, field: str) -> str:
    if not isinstance(value, str) or _SHA256.fullmatch(value) is None:
        raise CandidateSidecarError(f"{field} is not a lowercase SHA256")
    return value


def _identity(value: object) -> dict[str, Any]:
    if not isinstance(value, Mapping) or set(value) != _IDENTITY_FIELDS:
        raise CandidateSidecarError("sidecar identity shape drift")
    selected = copy.deepcopy(dict(value))
    if (
        not isinstance(selected.get("attempt_id"), str)
        or _SAFE_ID.fullmatch(selected["attempt_id"]) is None
        or not isinstance(selected.get("history_id"), str)
        or _HISTORY_ID.fullmatch(selected["history_id"]) is None
        or not isinstance(selected.get("cache_id"), str)
        or _SAFE_ID.fullmatch(selected["cache_id"]) is None
    ):
        raise CandidateSidecarError("sidecar attempt/history/cache identity drift")
    _sha(selected.get("episode_manifest_sha256"), field="episode manifest")
    _sha(selected.get("projection_schema_sha256"), field="projection schema")
    return selected


def _public_hash_only(value: object) -> None:
    if isinstance(value, Mapping):
        for key, child in value.items():
            name = str(key).casefold()
            if name in _FORBIDDEN_KEYS or name.startswith(("raw_", "private_")):
                raise CandidateSidecarError("sidecar contains raw or volatile data")
            _public_hash_only(child)
    elif isinstance(value, Sequence) and not isinstance(value, (str, bytes)):
        for child in value:
            _public_hash_only(child)


def _candidate_entries(value: object, *, offset: int) -> list[dict[str, Any]]:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes)):
        raise CandidateSidecarError("candidate partition is malformed")
    selected: list[dict[str, Any]] = []
    identities: set[str] = set()
    for ordinal, candidate in enumerate(value):
        if not isinstance(candidate, Mapping) or set(candidate) != {
            "candidate_id",
            "fact_sha256",
            "logical_identity_sha256",
        }:
            raise CandidateSidecarError("candidate entry shape drift")
        candidate_id = candidate.get("candidate_id")
        if (
            not isinstance(candidate_id, int)
            or isinstance(candidate_id, bool)
            or candidate_id != offset + ordinal
        ):
            raise CandidateSidecarError("candidate IDs are not contiguous")
        identity_sha = _sha(
            candidate.get("logical_identity_sha256"),
            field="logical candidate identity",
        )
        if identity_sha in identities:
            raise CandidateSidecarError("AMBIGUOUS_LOGICAL_CANDIDATE_IDENTITY")
        identities.add(identity_sha)
        selected.append(
            {
                "candidate_id": candidate_id,
                "fact_sha256": _sha(
                    candidate.get("fact_sha256"), field="candidate fact"
                ),
                "logical_identity_sha256": identity_sha,
            }
        )
    return selected


def build_candidate_call_record(
    *,
    source_sequence: int,
    source_hash: str,
    logical_call_sha256: str,
    prompt_sha256: str,
    related: Sequence[Mapping[str, Any]],
    invalidation: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    """Build one prompt-bound capture record without candidate text or UUIDs."""

    if (
        not isinstance(source_sequence, int)
        or isinstance(source_sequence, bool)
        or source_sequence < 0
    ):
        raise CandidateSidecarError("source sequence is invalid")
    related_entries = _candidate_entries(related, offset=0)
    invalidation_entries = _candidate_entries(
        invalidation, offset=len(related_entries)
    )
    related_ids = {
        item["logical_identity_sha256"] for item in related_entries
    }
    invalidation_ids = {
        item["logical_identity_sha256"] for item in invalidation_entries
    }
    if related_ids & invalidation_ids:
        raise CandidateSidecarError("CANDIDATE_PARTITION_COLLISION")
    body: dict[str, Any] = {
        "schema_version": CALL_SCHEMA,
        "record_type": "candidate_call",
        "source_sequence": source_sequence,
        "source_hash": _sha(source_hash, field="source hash"),
        "logical_call_sha256": _sha(
            logical_call_sha256, field="logical call"
        ),
        "prompt_sha256": _sha(prompt_sha256, field="capture prompt"),
        "partitions": {
            "related": related_entries,
            "invalidation": invalidation_entries,
        },
    }
    _public_hash_only(body)
    body["record_sha256"] = payload_sha256(body)
    return body


def _record(value: object) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise CandidateSidecarError("candidate call record is not a mapping")
    selected = copy.deepcopy(dict(value))
    _public_hash_only(selected)
    declared = _sha(selected.pop("record_sha256", None), field="call record")
    if payload_sha256(selected) != declared:
        raise CandidateSidecarError("candidate call record hash drift")
    if set(selected) != {
        "logical_call_sha256",
        "partitions",
        "prompt_sha256",
        "record_type",
        "schema_version",
        "source_hash",
        "source_sequence",
    }:
        raise CandidateSidecarError("candidate call record shape drift")
    partitions = selected.get("partitions")
    if not isinstance(partitions, Mapping) or set(partitions) != {
        "related",
        "invalidation",
    }:
        raise CandidateSidecarError("candidate call partition shape drift")
    rebuilt = build_candidate_call_record(
        source_sequence=selected["source_sequence"],
        source_hash=selected["source_hash"],
        logical_call_sha256=selected["logical_call_sha256"],
        prompt_sha256=selected["prompt_sha256"],
        related=partitions["related"],
        invalidation=partitions["invalidation"],
    )
    if rebuilt["record_sha256"] != declared:
        raise CandidateSidecarError("candidate call record canonicalization drift")
    return rebuilt


def _key(record: Mapping[str, Any]) -> tuple[int, str]:
    return int(record["source_sequence"]), str(record["logical_call_sha256"])


def _id_map(
    capture: Sequence[Mapping[str, Any]],
    replay: Sequence[Mapping[str, Any]],
) -> dict[int, int]:
    capture_by_identity = {
        item["logical_identity_sha256"]: item for item in capture
    }
    replay_by_identity = {
        item["logical_identity_sha256"]: item for item in replay
    }
    if set(capture_by_identity) != set(replay_by_identity):
        raise CandidateSidecarError("CANDIDATE_MEMBERSHIP_DRIFT")
    for identity_sha, captured in capture_by_identity.items():
        if captured["fact_sha256"] != replay_by_identity[identity_sha]["fact_sha256"]:
            raise CandidateSidecarError("CANDIDATE_FACT_DRIFT")
    return {
        int(captured["candidate_id"]): int(
            replay_by_identity[identity_sha]["candidate_id"]
        )
        for identity_sha, captured in capture_by_identity.items()
    }


class ReplaySidecarBinder:
    """Bind each capture call exactly once to an enriched replay projection."""

    def __init__(
        self,
        *,
        identity: Mapping[str, Any],
        records: Sequence[Mapping[str, Any]],
        consumed_source_sequences: Sequence[int] = (),
    ) -> None:
        self.identity = _identity(identity)
        self._records: dict[tuple[int, str], dict[str, Any]] = {}
        self._consumed: set[tuple[int, str]] = set()
        self._prepared: set[tuple[int, str]] = set()
        for value in records:
            record = _record(value)
            key = _key(record)
            if key in self._records:
                raise CandidateSidecarError("SIDECAR_CALL_CORRELATION_COLLISION")
            self._records[key] = record
        consumed_sources = set(consumed_source_sequences)
        if any(
            not isinstance(value, int) or isinstance(value, bool) or value < 0
            for value in consumed_sources
        ) or len(consumed_sources) != len(tuple(consumed_source_sequences)):
            raise CandidateSidecarError("checkpoint consumption prefix is malformed")
        self._consumed = {
            key for key in self._records if key[0] in consumed_sources
        }
        self.resumed_consumed_count = len(self._consumed)

    @property
    def consumed_count(self) -> int:
        return len(self._consumed)

    @property
    def remaining_count(self) -> int:
        return len(self._records) - len(self._consumed)

    @property
    def prepared_count(self) -> int:
        return len(self._prepared)

    def remaining_for_source(self, source_sequence: int) -> int:
        return sum(
            key[0] == source_sequence and key not in self._consumed
            for key in self._records
        )

    def prepare(
        self,
        *,
        source_sequence: int,
        source_hash: str,
        logical_call_sha256: str,
        related: Sequence[Mapping[str, Any]],
        invalidation: Sequence[Mapping[str, Any]],
    ) -> "ReplayBindingLease":
        key = (source_sequence, _sha(logical_call_sha256, field="logical call"))
        capture = self._records.get(key)
        if capture is None:
            raise CandidateSidecarError("SIDECAR_CALL_CORRELATION_MISSING")
        if key in self._consumed:
            raise CandidateSidecarError("SIDECAR_CALL_ALREADY_CONSUMED")
        if key in self._prepared:
            raise CandidateSidecarError("SIDECAR_CALL_ALREADY_PREPARED")
        if capture["source_hash"] != _sha(source_hash, field="source hash"):
            raise CandidateSidecarError("SIDECAR_SOURCE_HASH_DRIFT")
        captured_partitions = capture["partitions"]
        replay_related = _candidate_entries(related, offset=0)
        replay_invalidation = _candidate_entries(
            invalidation, offset=len(replay_related)
        )
        capture_related_ids = {
            item["logical_identity_sha256"]
            for item in captured_partitions["related"]
        }
        capture_invalidation_ids = {
            item["logical_identity_sha256"]
            for item in captured_partitions["invalidation"]
        }
        replay_related_ids = {
            item["logical_identity_sha256"] for item in replay_related
        }
        replay_invalidation_ids = {
            item["logical_identity_sha256"] for item in replay_invalidation
        }
        if (
            capture_related_ids | capture_invalidation_ids
            == replay_related_ids | replay_invalidation_ids
            and (
                capture_related_ids != replay_related_ids
                or capture_invalidation_ids != replay_invalidation_ids
            )
        ):
            raise CandidateSidecarError("CANDIDATE_PARTITION_DRIFT")
        related_map = _id_map(captured_partitions["related"], replay_related)
        invalidation_map = _id_map(
            captured_partitions["invalidation"], replay_invalidation
        )
        binding = {
            "source_sequence": source_sequence,
            "logical_call_sha256": key[1],
            "capture_prompt_sha256": capture["prompt_sha256"],
            "related_id_map": related_map,
            "invalidation_id_map": invalidation_map,
            "capture_partitions": copy.deepcopy(captured_partitions),
            "replay_partitions": {
                "related": replay_related,
                "invalidation": replay_invalidation,
            },
        }
        self._prepared.add(key)
        return ReplayBindingLease(self, key=key, binding=binding)

    def bind(
        self,
        *,
        source_sequence: int,
        source_hash: str,
        logical_call_sha256: str,
        related: Sequence[Mapping[str, Any]],
        invalidation: Sequence[Mapping[str, Any]],
    ) -> dict[str, Any]:
        """Prepare and immediately commit for callers without an oracle step."""

        lease = self.prepare(
            source_sequence=source_sequence,
            source_hash=source_hash,
            logical_call_sha256=logical_call_sha256,
            related=related,
            invalidation=invalidation,
        )
        binding = lease.binding
        lease.commit()
        return binding

    def _commit(self, key: tuple[int, str]) -> None:
        if key not in self._prepared or key in self._consumed:
            raise CandidateSidecarError("sidecar binding lease state drift")
        self._prepared.remove(key)
        self._consumed.add(key)

    def _rollback(self, key: tuple[int, str]) -> None:
        if key not in self._prepared or key in self._consumed:
            raise CandidateSidecarError("sidecar binding lease state drift")
        self._prepared.remove(key)


class ReplayBindingLease:
    """Reserve one call until the prompt oracle accepts or rejects it."""

    def __init__(
        self,
        binder: ReplaySidecarBinder,
        *,
        key: tuple[int, str],
        binding: Mapping[str, Any],
    ) -> None:
        self._binder = binder
        self._key = key
        self._binding = copy.deepcopy(dict(binding))
        self._finalized = False

    @property
    def binding(self) -> dict[str, Any]:
        return copy.deepcopy(self._binding)

    def commit(self) -> None:
        if self._finalized:
            raise CandidateSidecarError("sidecar binding lease is finalized")
        self._binder._commit(self._key)
        self._finalized = True

    def rollback(self) -> None:
        if self._finalized:
            raise CandidateSidecarError("sidecar binding lease is finalized")
        self._binder._rollback(self._key)
        self._finalized = True


def remap_edge_response(
    value: Mapping[str, Any], binding: Mapping[str, Any]
) -> dict[str, Any]:
    """Translate only Graphiti's positional edge response references."""

    if not isinstance(value, Mapping):
        raise CandidateSidecarError("cached edge response is malformed")
    selected = copy.deepcopy(dict(value))
    duplicates = selected.get("duplicate_facts")
    contradictions = selected.get("contradicted_facts")
    if not isinstance(duplicates, list) or not isinstance(contradictions, list):
        raise CandidateSidecarError("cached edge response fields are malformed")
    related = binding.get("related_id_map")
    invalidation = binding.get("invalidation_id_map")
    if not isinstance(related, Mapping) or not isinstance(invalidation, Mapping):
        raise CandidateSidecarError("sidecar binding maps are malformed")
    combined = {**related, **invalidation}
    try:
        selected["duplicate_facts"] = [related[index] for index in duplicates]
        selected["contradicted_facts"] = [combined[index] for index in contradictions]
    except (KeyError, TypeError) as error:
        raise CandidateSidecarError("cached edge response index is invalid") from error
    return selected


def _header(identity: Mapping[str, Any]) -> dict[str, Any]:
    body = {
        "schema_version": SIDECAR_SCHEMA,
        "record_type": "header",
        "identity": _identity(identity),
    }
    body["header_sha256"] = payload_sha256(body)
    return body


def _seal_cache_evidence(value: object) -> dict[str, str] | None:
    if value is None:
        return None
    if not isinstance(value, Mapping) or set(value) != {
        "prompt_cache_sha256",
        "embedding_cache_sha256",
    }:
        raise CandidateSidecarError("sidecar seal cache evidence shape drift")
    return {
        name: _sha(value[name], field=f"sidecar seal {name}")
        for name in sorted(value)
    }


def _seal(
    records: Sequence[Mapping[str, Any]],
    *,
    cache_evidence: Mapping[str, str] | None = None,
) -> dict[str, Any]:
    keys = [
        {"source_sequence": record["source_sequence"], "logical_call_sha256": record["logical_call_sha256"]}
        for record in records
    ]
    counts = Counter(int(record["source_sequence"]) for record in records)
    body = {
        "schema_version": SIDECAR_SCHEMA,
        "record_type": "seal",
        "record_count": len(records),
        "records_sha256": payload_sha256(list(records)),
        "record_keys_sha256": payload_sha256(keys),
        "episode_call_counts_sha256": payload_sha256(
            [
                {"call_count": counts[source], "source_sequence": source}
                for source in sorted(counts)
            ]
        ),
        "cache_evidence": _seal_cache_evidence(cache_evidence),
    }
    body["seal_sha256"] = payload_sha256(body)
    return body


def _append_line(path: Path, value: Mapping[str, Any], *, exclusive: bool) -> None:
    flags = os.O_WRONLY | (os.O_CREAT | os.O_EXCL if exclusive else os.O_APPEND)
    descriptor = os.open(path, flags, 0o600)
    try:
        os.write(descriptor, canonical_bytes(value) + b"\n")
        os.fsync(descriptor)
    finally:
        os.close(descriptor)
    directory = os.open(path.parent, os.O_RDONLY)
    try:
        os.fsync(directory)
    finally:
        os.close(directory)


def _lines(path: Path) -> list[dict[str, Any]]:
    try:
        raw = Path(path).read_text(encoding="ascii")
        values = [json.loads(line) for line in raw.split("\n") if line]
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise CandidateSidecarError("candidate sidecar is unreadable") from error
    if any(not isinstance(value, dict) for value in values):
        raise CandidateSidecarError("candidate sidecar contains a non-object")
    return values


def _parse_header(value: object, expected_identity: Mapping[str, Any]) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise CandidateSidecarError("candidate sidecar header is missing")
    selected = copy.deepcopy(dict(value))
    declared = _sha(selected.pop("header_sha256", None), field="sidecar header")
    if payload_sha256(selected) != declared or selected != {
        "schema_version": SIDECAR_SCHEMA,
        "record_type": "header",
        "identity": _identity(expected_identity),
    }:
        raise CandidateSidecarError("candidate sidecar header drift")
    selected["header_sha256"] = declared
    return selected


class CaptureSidecarStore:
    """Append+fsync capture records, with exact unsealed resume and final seal."""

    def __init__(
        self, path: Path, identity: Mapping[str, Any], records: Sequence[Mapping[str, Any]]
    ) -> None:
        self.path = Path(path)
        self.identity = _identity(identity)
        self.records = [_record(value) for value in records]
        self._records_by_key = {_key(record): record for record in self.records}
        if len(self._records_by_key) != len(self.records):
            raise CandidateSidecarError("duplicate capture sidecar record")
        self._sealed = False
        self._seal_value: dict[str, Any] | None = None

    @classmethod
    def create(
        cls, path: Path, *, identity: Mapping[str, Any]
    ) -> "CaptureSidecarStore":
        selected_path = Path(path)
        selected_path.parent.mkdir(parents=True, exist_ok=True)
        _append_line(selected_path, _header(identity), exclusive=True)
        return cls(selected_path, identity, [])

    @classmethod
    def resume(
        cls, path: Path, *, identity: Mapping[str, Any]
    ) -> "CaptureSidecarStore":
        values = _lines(Path(path))
        if not values:
            raise CandidateSidecarError("candidate sidecar is empty")
        _parse_header(values[0], identity)
        if any(value.get("record_type") == "seal" for value in values[1:]):
            raise CandidateSidecarError("candidate sidecar is already sealed")
        return cls(Path(path), identity, [_record(value) for value in values[1:]])

    @classmethod
    def resume_for_finalization(
        cls, path: Path, *, identity: Mapping[str, Any]
    ) -> "CaptureSidecarStore":
        """Resume an open capture or verify a sealed terminalization window."""

        values = _lines(Path(path))
        if not any(value.get("record_type") == "seal" for value in values[1:]):
            return cls.resume(path, identity=identity)
        loaded = load_capture_sidecar(path, expected_identity=identity)
        selected = cls(Path(path), identity, loaded["records"])
        selected._sealed = True
        selected._seal_value = copy.deepcopy(loaded["seal"])
        return selected

    @property
    def is_sealed(self) -> bool:
        return self._sealed

    def append(self, value: Mapping[str, Any]) -> None:
        if self._sealed:
            raise CandidateSidecarError("candidate sidecar is sealed")
        record = _record(value)
        key = _key(record)
        if key in self._records_by_key:
            raise CandidateSidecarError("duplicate capture sidecar record")
        _append_line(self.path, record, exclusive=False)
        self.records.append(record)
        self._records_by_key[key] = record

    def ensure(self, value: Mapping[str, Any]) -> bool:
        """Append once, or verify the exact durable record during resume."""

        if self._sealed:
            raise CandidateSidecarError("candidate sidecar is sealed")
        record = _record(value)
        key = _key(record)
        existing = self._records_by_key.get(key)
        if existing is not None:
            if existing != record:
                raise CandidateSidecarError("conflicting capture sidecar record")
            return False
        _append_line(self.path, record, exclusive=False)
        self.records.append(record)
        self._records_by_key[key] = record
        return True

    def seal(
        self,
        *,
        cache_evidence: Mapping[str, str] | None = None,
    ) -> dict[str, Any]:
        if self._sealed:
            expected = _seal(self.records, cache_evidence=cache_evidence)
            if self._seal_value != expected:
                raise CandidateSidecarError("candidate sidecar sealed evidence drift")
            return copy.deepcopy(expected)
        seal = _seal(self.records, cache_evidence=cache_evidence)
        _append_line(self.path, seal, exclusive=False)
        self._sealed = True
        self._seal_value = copy.deepcopy(seal)
        return copy.deepcopy(seal)


def load_capture_sidecar(
    path: Path, *, expected_identity: Mapping[str, Any]
) -> dict[str, Any]:
    values = _lines(Path(path))
    if len(values) < 2:
        raise CandidateSidecarError("sealed candidate sidecar is incomplete")
    header = _parse_header(values[0], expected_identity)
    if values[-1].get("record_type") != "seal" or any(
        value.get("record_type") == "seal" for value in values[1:-1]
    ):
        raise CandidateSidecarError("candidate sidecar seal placement drift")
    records = [_record(value) for value in values[1:-1]]
    seal = copy.deepcopy(values[-1])
    declared = _sha(seal.pop("seal_sha256", None), field="sidecar seal")
    expected_seal = _seal(
        records,
        cache_evidence=seal.get("cache_evidence"),
    )
    if payload_sha256(seal) != declared or seal != {
        key: value for key, value in expected_seal.items() if key != "seal_sha256"
    }:
        raise CandidateSidecarError("candidate sidecar seal drift")
    seal["seal_sha256"] = declared
    return {
        "identity": copy.deepcopy(header["identity"]),
        "records": records,
        "seal": seal,
    }
