"""Fail-closed position translation for S4 deterministic replay prompts.

The pinned Graphiti ingestion path exposes positional candidate references in
two prompts. This module accepts an order-only prompt miss only after proving a
one-to-one mapping between the capture and replay candidate sets, then returns
an in-memory copy of the cached response with those positions translated.
Persistent cache records are never changed.
"""

from __future__ import annotations

import ast
import copy
import hashlib
import json
from collections.abc import Mapping
from dataclasses import asdict, dataclass, is_dataclass
from typing import Any


NODE_PROMPT = "dedupe_nodes.nodes"
EDGE_PROMPT = "dedupe_edges.resolve_edge"
SUPPORTED_PROMPTS = frozenset({NODE_PROMPT, EDGE_PROMPT})

_PART_FIELDS = (
    "model_revision",
    "decoding_config",
    "structured_output_schema",
    "system_prompt",
    "user_prompt",
)
_NODE_SECTION = ("<EXISTING ENTITIES>", "</EXISTING ENTITIES>")
_EDGE_RELATED_SECTION = ("<EXISTING FACTS>", "</EXISTING FACTS>")
_EDGE_INVALIDATION_SECTION = (
    "<FACT INVALIDATION CANDIDATES>",
    "</FACT INVALIDATION CANDIDATES>",
)


class CandidateRemapError(RuntimeError):
    """A semantic prompt miss could not be translated without ambiguity."""

    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


class _RemappedRecord:
    """Read-only view of a cache record with an in-memory parsed response."""

    def __init__(self, original: Any, parsed_response: Any) -> None:
        self._original = original
        self.parsed_response = parsed_response

    def __getattr__(self, name: str) -> Any:
        return getattr(self._original, name)


@dataclass(frozen=True)
class _ParsedPrompt:
    skeleton: str
    candidates: Any


def _parts_dict(value: Any) -> dict[str, Any]:
    if is_dataclass(value):
        selected = asdict(value)
    elif isinstance(value, Mapping):
        selected = dict(value)
    else:
        selected = {field: getattr(value, field) for field in _PART_FIELDS}
    if not set(_PART_FIELDS).issubset(selected):
        raise CandidateRemapError("MALFORMED_PROMPT_PARTS")
    return {field: selected[field] for field in _PART_FIELDS}


def _prompt_name(parts: Mapping[str, Any]) -> str | None:
    decoding = parts.get("decoding_config")
    if not isinstance(decoding, Mapping):
        return None
    value = decoding.get("prompt_name")
    return str(value) if value is not None else None


def _same_non_user_parts(
    capture: Mapping[str, Any],
    replay: Mapping[str, Any],
) -> bool:
    return all(
        capture[field] == replay[field]
        for field in _PART_FIELDS
        if field != "user_prompt"
    )


def _extract_sections(
    prompt: str,
    sections: tuple[tuple[str, str], ...],
) -> tuple[list[str], str]:
    if not isinstance(prompt, str):
        raise CandidateRemapError("MALFORMED_CANDIDATE_PROMPT")
    spans: list[tuple[int, int, str]] = []
    values: list[str] = []
    for index, (opening, closing) in enumerate(sections):
        if prompt.count(opening) != 1 or prompt.count(closing) != 1:
            raise CandidateRemapError("MALFORMED_CANDIDATE_PROMPT")
        content_start = prompt.index(opening) + len(opening)
        try:
            content_end = prompt.index(closing, content_start)
        except ValueError as error:
            raise CandidateRemapError("MALFORMED_CANDIDATE_PROMPT") from error
        if content_end < content_start:
            raise CandidateRemapError("MALFORMED_CANDIDATE_PROMPT")
        values.append(prompt[content_start:content_end].strip())
        spans.append((content_start, content_end, f"__S4_CANDIDATE_SECTION_{index}__"))

    skeleton = prompt
    for start, end, replacement in sorted(spans, reverse=True):
        skeleton = skeleton[:start] + replacement + skeleton[end:]
    return values, skeleton


def _is_int(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def _canonical_identity(value: Mapping[str, Any]) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def _parse_node_prompt(prompt: str) -> _ParsedPrompt:
    sections, skeleton = _extract_sections(prompt, (_NODE_SECTION,))
    try:
        raw = json.loads(sections[0])
    except (TypeError, ValueError) as error:
        raise CandidateRemapError("MALFORMED_CANDIDATE_PROMPT") from error
    if not isinstance(raw, list):
        raise CandidateRemapError("MALFORMED_CANDIDATE_PROMPT")

    candidates: list[tuple[int, str]] = []
    for position, value in enumerate(raw):
        if not isinstance(value, dict) or "candidate_id" not in value:
            raise CandidateRemapError("MALFORMED_CANDIDATE_PROMPT")
        candidate_id = value["candidate_id"]
        if not _is_int(candidate_id) or candidate_id != position:
            raise CandidateRemapError("NONCONTIGUOUS_CANDIDATE_IDS")
        identity_value = dict(value)
        identity_value.pop("candidate_id")
        candidates.append((candidate_id, _canonical_identity(identity_value)))
    return _ParsedPrompt(skeleton=skeleton, candidates=tuple(candidates))


def _parse_edge_partition(
    source: str,
    *,
    offset: int,
) -> tuple[tuple[int, str], ...]:
    try:
        raw = ast.literal_eval(source)
    except (SyntaxError, ValueError) as error:
        raise CandidateRemapError("MALFORMED_CANDIDATE_PROMPT") from error
    if not isinstance(raw, list):
        raise CandidateRemapError("MALFORMED_CANDIDATE_PROMPT")

    candidates: list[tuple[int, str]] = []
    for position, value in enumerate(raw):
        if not isinstance(value, dict) or set(value) != {"idx", "fact"}:
            raise CandidateRemapError("MALFORMED_CANDIDATE_PROMPT")
        candidate_id = value["idx"]
        if not _is_int(candidate_id) or candidate_id != offset + position:
            raise CandidateRemapError("NONCONTIGUOUS_CANDIDATE_IDS")
        if not isinstance(value["fact"], str):
            raise CandidateRemapError("MALFORMED_CANDIDATE_PROMPT")
        candidates.append((candidate_id, value["fact"]))
    return tuple(candidates)


def _parse_edge_prompt(prompt: str) -> _ParsedPrompt:
    sections, skeleton = _extract_sections(
        prompt,
        (_EDGE_RELATED_SECTION, _EDGE_INVALIDATION_SECTION),
    )
    related = _parse_edge_partition(sections[0], offset=0)
    invalidation = _parse_edge_partition(sections[1], offset=len(related))
    return _ParsedPrompt(
        skeleton=skeleton,
        candidates=(related, invalidation),
    )


def _parse_prompt(prompt_name: str, prompt: str) -> _ParsedPrompt:
    if prompt_name == NODE_PROMPT:
        return _parse_node_prompt(prompt)
    if prompt_name == EDGE_PROMPT:
        return _parse_edge_prompt(prompt)
    raise CandidateRemapError("UNSUPPORTED_POSITIONAL_PROMPT")


def _unique_id_map(
    capture: tuple[tuple[int, str], ...],
    replay: tuple[tuple[int, str], ...],
    *,
    drift_code: str,
) -> dict[int, int]:
    capture_identity = [identity for _, identity in capture]
    replay_identity = [identity for _, identity in replay]
    if len(set(capture_identity)) != len(capture_identity) or len(
        set(replay_identity)
    ) != len(replay_identity):
        raise CandidateRemapError("AMBIGUOUS_CANDIDATE_IDENTITY")
    if set(capture_identity) != set(replay_identity):
        raise CandidateRemapError(drift_code)
    replay_by_identity = {
        identity: candidate_id for candidate_id, identity in replay
    }
    return {
        capture_id: replay_by_identity[identity]
        for capture_id, identity in capture
    }


def _node_mapping(capture: _ParsedPrompt, replay: _ParsedPrompt) -> dict[int, int]:
    return _unique_id_map(
        capture.candidates,
        replay.candidates,
        drift_code="CANDIDATE_MEMBERSHIP_DRIFT",
    )


def _edge_mapping(
    capture: _ParsedPrompt,
    replay: _ParsedPrompt,
) -> tuple[dict[int, int], dict[int, int]]:
    capture_related, capture_invalidation = capture.candidates
    replay_related, replay_invalidation = replay.candidates

    capture_all = {identity for _, identity in capture_related + capture_invalidation}
    replay_all = {identity for _, identity in replay_related + replay_invalidation}
    if capture_all == replay_all and (
        {identity for _, identity in capture_related}
        != {identity for _, identity in replay_related}
        or {identity for _, identity in capture_invalidation}
        != {identity for _, identity in replay_invalidation}
    ):
        raise CandidateRemapError("CANDIDATE_PARTITION_DRIFT")

    related_map = _unique_id_map(
        capture_related,
        replay_related,
        drift_code="CANDIDATE_MEMBERSHIP_DRIFT",
    )
    invalidation_map = _unique_id_map(
        capture_invalidation,
        replay_invalidation,
        drift_code="CANDIDATE_MEMBERSHIP_DRIFT",
    )
    return related_map, invalidation_map


def _remap_node_response(value: Any, mapping: Mapping[int, int]) -> Any:
    if not isinstance(value, dict) or not isinstance(
        value.get("entity_resolutions"), list
    ):
        raise CandidateRemapError("MALFORMED_CACHED_RESPONSE")
    selected = copy.deepcopy(value)
    for resolution in selected["entity_resolutions"]:
        if not isinstance(resolution, dict) or "duplicate_candidate_id" not in resolution:
            raise CandidateRemapError("MALFORMED_CACHED_RESPONSE")
        candidate_id = resolution["duplicate_candidate_id"]
        if not _is_int(candidate_id):
            raise CandidateRemapError("CACHED_RESPONSE_INDEX_TYPE")
        if candidate_id == -1:
            continue
        if candidate_id < -1 or candidate_id not in mapping:
            raise CandidateRemapError("CACHED_RESPONSE_INDEX_OUT_OF_RANGE")
        resolution["duplicate_candidate_id"] = mapping[candidate_id]
    return selected


def _remap_edge_response(
    value: Any,
    related_map: Mapping[int, int],
    invalidation_map: Mapping[int, int],
) -> Any:
    if not isinstance(value, dict):
        raise CandidateRemapError("MALFORMED_CACHED_RESPONSE")
    selected = copy.deepcopy(value)
    duplicate_facts = selected.get("duplicate_facts")
    contradicted_facts = selected.get("contradicted_facts")
    if not isinstance(duplicate_facts, list) or not isinstance(
        contradicted_facts, list
    ):
        raise CandidateRemapError("MALFORMED_CACHED_RESPONSE")

    translated_duplicates: list[int] = []
    for candidate_id in duplicate_facts:
        if not _is_int(candidate_id):
            raise CandidateRemapError("CACHED_RESPONSE_INDEX_TYPE")
        if candidate_id in invalidation_map:
            raise CandidateRemapError("CACHED_RESPONSE_WRONG_PARTITION")
        if candidate_id not in related_map:
            raise CandidateRemapError("CACHED_RESPONSE_INDEX_OUT_OF_RANGE")
        translated_duplicates.append(related_map[candidate_id])

    combined_map = {**related_map, **invalidation_map}
    translated_contradictions: list[int] = []
    for candidate_id in contradicted_facts:
        if not _is_int(candidate_id):
            raise CandidateRemapError("CACHED_RESPONSE_INDEX_TYPE")
        if candidate_id not in combined_map:
            raise CandidateRemapError("CACHED_RESPONSE_INDEX_OUT_OF_RANGE")
        translated_contradictions.append(combined_map[candidate_id])

    selected["duplicate_facts"] = translated_duplicates
    selected["contradicted_facts"] = translated_contradictions
    return selected


def _safe_hash(parts: Mapping[str, Any]) -> str:
    encoded = json.dumps(
        dict(parts),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


class CandidateAwareReplayCache:
    """Exact prompt cache with a fail-closed, in-memory positional fallback."""

    def __init__(self, inner: Any) -> None:
        if getattr(inner, "read_only", None) is not True:
            raise ValueError("candidate-aware replay requires a read-only cache")
        self.inner = inner
        self.exact_prompt_hit_count = 0
        self.candidate_remap_hit_count = 0
        self.candidate_remap_rejection_count = 0
        self.remap_hit_counts: dict[str, int] = {}
        self.remap_diagnostics: list[dict[str, Any]] = []

    def __getattr__(self, name: str) -> Any:
        return getattr(self.inner, name)

    def _records(self) -> list[Any]:
        records = getattr(self.inner, "_records", None)
        if not isinstance(records, Mapping):
            raise CandidateRemapError("CACHE_RECORD_INDEX_UNAVAILABLE")
        return list(records.values())

    def _reject(
        self,
        error: CandidateRemapError,
        *,
        prompt_name: str,
        parts: Mapping[str, Any],
    ) -> None:
        self.candidate_remap_rejection_count += 1
        self.remap_diagnostics.append(
            {
                "classification": error.code,
                "prompt_name": prompt_name,
                "requested_prompt_sha256": _safe_hash(parts),
            }
        )

    def get(self, parts: Any) -> Any | None:
        exact = self.inner.get(parts)
        if exact is not None:
            self.exact_prompt_hit_count += 1
            return exact

        requested = _parts_dict(parts)
        prompt_name = _prompt_name(requested)
        if prompt_name not in SUPPORTED_PROMPTS:
            return None

        eligible: list[tuple[Any, dict[str, Any]]] = []
        for record in self._records():
            capture = _parts_dict(getattr(record, "prompt_parts", {}))
            if _prompt_name(capture) == prompt_name and _same_non_user_parts(
                capture, requested
            ):
                eligible.append((record, capture))
        if not eligible:
            return None

        try:
            replay_prompt = _parse_prompt(prompt_name, requested["user_prompt"])
            matches: list[Any] = []
            drift_errors: list[CandidateRemapError] = []
            for record, capture in eligible:
                capture_prompt = _parse_prompt(prompt_name, capture["user_prompt"])
                if capture_prompt.skeleton != replay_prompt.skeleton:
                    continue
                try:
                    if prompt_name == NODE_PROMPT:
                        mapping = _node_mapping(capture_prompt, replay_prompt)
                        parsed = _remap_node_response(
                            getattr(record, "parsed_response", None),
                            mapping,
                        )
                    else:
                        related_map, invalidation_map = _edge_mapping(
                            capture_prompt,
                            replay_prompt,
                        )
                        parsed = _remap_edge_response(
                            getattr(record, "parsed_response", None),
                            related_map,
                            invalidation_map,
                        )
                except CandidateRemapError as error:
                    drift_errors.append(error)
                    continue
                matches.append(_RemappedRecord(record, parsed))

            if len(matches) > 1:
                raise CandidateRemapError("SEMANTIC_CACHE_COLLISION")
            if len(matches) == 1:
                self.candidate_remap_hit_count += 1
                self.remap_hit_counts[prompt_name] = (
                    self.remap_hit_counts.get(prompt_name, 0) + 1
                )
                return matches[0]
            if drift_errors:
                raise drift_errors[0]
            return None
        except CandidateRemapError as error:
            self._reject(
                error,
                prompt_name=prompt_name,
                parts=requested,
            )
            raise

    def put(self, *args: Any, **kwargs: Any) -> Any:
        return self.inner.put(*args, **kwargs)

    def record_unexpected(self, *args: Any, **kwargs: Any) -> Any:
        return self.inner.record_unexpected(*args, **kwargs)

    def resolve(self, *args: Any, **kwargs: Any) -> Any:
        if not args:
            raise TypeError("resolve requires prompt parts")
        selected = self.get(args[0])
        if selected is not None:
            return selected
        return self.inner.resolve(*args, **kwargs)
