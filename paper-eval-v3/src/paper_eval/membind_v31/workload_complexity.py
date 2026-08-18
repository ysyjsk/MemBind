"""Deterministic, content-free complexity counts for the frozen v3.1 workload.

The source-turn unit is one raw message inside a LongMemEval session.  Input
tokens are counted only from the already rendered ``Episode.body`` using the
pinned Qwen tokenizer with special-token insertion disabled.  No message,
rendered body, or token ID is retained in the returned artifact.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Mapping, Sequence
from copy import deepcopy
from typing import Any

from paper_eval.artifacts import payload_sha256


WORKLOAD_COMPLEXITY_SCHEMA = (
    "membind.paper-eval-v3.membind-v31-workload-complexity.v1"
)
WORKLOAD_HISTORIES = ("07741c45", "b6019101", "6071bd76", "a2f3aa27")
_SHA256 = re.compile(r"^[0-9a-f]{64}$")


class WorkloadComplexityError(ValueError):
    """A source, renderer, tokenizer, or content-free output invariant failed."""


def _fail(code: str) -> WorkloadComplexityError:
    return WorkloadComplexityError(code)


def _sha(value: object, code: str) -> str:
    if not isinstance(value, str) or _SHA256.fullmatch(value) is None:
        raise _fail(code)
    return value


def _mapping(value: object, code: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise _fail(code)
    return value


def _verify_development_input(value: Mapping[str, object]) -> dict[str, Any]:
    selected = deepcopy(dict(value))
    stored = _sha(selected.get("payload_sha256"), "development_input_invalid")
    body = {key: child for key, child in selected.items() if key != "payload_sha256"}
    if stored != payload_sha256(body):
        raise _fail("development_input_invalid")
    if (
        selected.get("data_role") != "DEVELOPMENT_EXPOSED"
        or selected.get("selection_policy")
        != "EXACT_FROZEN_DEVELOPMENT_HISTORIES_ONLY"
        or tuple(selected.get("history_order", ())) != WORKLOAD_HISTORIES
    ):
        raise _fail("development_input_invalid")
    records = selected.get("records")
    if (
        isinstance(records, (str, bytes))
        or not isinstance(records, Sequence)
        or tuple(
            record.get("question_id") if isinstance(record, Mapping) else None
            for record in records
        )
        != WORKLOAD_HISTORIES
    ):
        raise _fail("development_history_inventory_invalid")
    return selected


def _raw_turn_count(record: Mapping[str, object]) -> int:
    sessions = record.get("haystack_sessions")
    if (
        isinstance(sessions, (str, bytes))
        or not isinstance(sessions, Sequence)
        or not sessions
    ):
        raise _fail("raw_session_inventory_invalid")
    count = 0
    for session in sessions:
        # The frozen LongMemEval input represents every session as its raw
        # message list.  Accepting a projected mapping here could silently turn
        # a session into one turn, so shape drift is deliberately rejected.
        if (
            isinstance(session, (str, bytes))
            or not isinstance(session, Sequence)
            or not session
        ):
            raise _fail("raw_session_invalid")
        count += len(session)
    return count


def _token_count(tokenizer: object, body: str) -> int:
    encode = getattr(tokenizer, "encode", None)
    if not callable(encode):
        raise _fail("tokenizer_invalid")
    try:
        encoded = encode(body, add_special_tokens=False)
    except Exception:
        raise _fail("tokenization_failed") from None
    token_ids = getattr(encoded, "ids", encoded)
    if (
        isinstance(token_ids, (str, bytes))
        or not isinstance(token_ids, Sequence)
        or any(
            isinstance(token, bool) or not isinstance(token, int) or token < 0
            for token in token_ids
        )
    ):
        raise _fail("tokenization_failed")
    return len(token_ids)


def build_workload_complexity_freeze(
    *,
    development_input: Mapping[str, object],
    development_input_file_sha256: str,
    baseline_plan: Mapping[str, object],
    renderer_identity: Mapping[str, object],
    tokenizer_identity: Mapping[str, object],
    tokenizer: object,
    episode_builder: Callable[[dict[str, Any]], Sequence[object]],
    methodology_sha256: str,
    workplan_sha256: str,
) -> dict[str, object]:
    """Build the sealed four-history complexity artifact without content."""

    if not callable(episode_builder):
        raise _fail("episode_builder_invalid")
    selected_input = _verify_development_input(development_input)
    baseline = _mapping(baseline_plan, "baseline_plan_invalid")
    sources = _mapping(
        baseline.get("history_source_sha256s"), "baseline_source_inventory_invalid"
    )
    if tuple(sources) != WORKLOAD_HISTORIES:
        raise _fail("baseline_source_inventory_invalid")
    counts = _mapping(selected_input.get("episode_counts"), "episode_counts_invalid")
    records = selected_input["records"]
    history_rows: dict[str, dict[str, int]] = {}
    for history_id, raw_record in zip(WORKLOAD_HISTORIES, records, strict=True):
        record = _mapping(raw_record, "development_record_invalid")
        raw_turns = _raw_turn_count(record)
        try:
            episodes = episode_builder(deepcopy(dict(record)))
        except Exception:
            raise _fail("episode_render_failed") from None
        if (
            isinstance(episodes, (str, bytes))
            or not isinstance(episodes, Sequence)
            or not episodes
            or counts.get(history_id) != len(episodes)
        ):
            raise _fail("episode_inventory_invalid")
        expected_sources = sources.get(history_id)
        observed_sources = [getattr(episode, "source_hash", None) for episode in episodes]
        if (
            not isinstance(expected_sources, list)
            or observed_sources != expected_sources
            or any(_SHA256.fullmatch(str(value)) is None for value in observed_sources)
        ):
            raise _fail("source_identity_mismatch")
        characters = 0
        tokens = 0
        for episode in episodes:
            body = getattr(episode, "body", None)
            if not isinstance(body, str) or not body:
                raise _fail("rendered_episode_body_invalid")
            characters += len(body)
            tokens += _token_count(tokenizer, body)
        history_rows[history_id] = {
            "episode_count": len(episodes),
            "source_turn_count": raw_turns,
            "source_input_token_count": tokens,
            "source_input_character_count": characters,
        }

    count_fields = (
        "episode_count",
        "source_turn_count",
        "source_input_token_count",
        "source_input_character_count",
    )
    body: dict[str, object] = {
        "schema_version": WORKLOAD_COMPLEXITY_SCHEMA,
        "status": "PASS",
        "methodology_sha256": _sha(methodology_sha256, "methodology_hash_invalid"),
        "workplan_sha256": _sha(workplan_sha256, "workplan_hash_invalid"),
        "data_role": "DEVELOPMENT_EXPOSED",
        "heldout_data_accessed": False,
        "live_service_calls_performed": False,
        "history_order": list(WORKLOAD_HISTORIES),
        "source_manifest_sha256": _sha(
            baseline.get("source_manifest_sha256"), "source_manifest_invalid"
        ),
        "source_identity": {
            "baseline_plan_payload_sha256": _sha(
                baseline.get("payload_sha256"), "baseline_plan_invalid"
            ),
            "development_input_file_sha256": _sha(
                development_input_file_sha256, "development_input_file_invalid"
            ),
            "development_input_payload_sha256": selected_input["payload_sha256"],
            "source_dataset_sha256": _sha(
                selected_input.get("source_dataset_sha256"),
                "source_dataset_identity_invalid",
            ),
            "per_source_identities_persisted": False,
        },
        "renderer_identity": deepcopy(dict(renderer_identity)),
        "tokenizer_identity": {
            **deepcopy(dict(tokenizer_identity)),
            "add_special_tokens": False,
        },
        "definitions": {
            "source_input_characters": "sum(len(rendered Episode.body))",
            "source_input_tokens": (
                "sum(Qwen tokenizer encode(rendered Episode.body, "
                "add_special_tokens=False))"
            ),
            "source_turn": "one raw message in each frozen LongMemEval session",
        },
        "histories": history_rows,
        "totals": {
            field: sum(row[field] for row in history_rows.values())
            for field in count_fields
        },
        "raw_content_persisted": False,
        "token_ids_persisted": False,
    }
    body["payload_sha256"] = payload_sha256(body)
    return body


__all__ = [
    "WORKLOAD_COMPLEXITY_SCHEMA",
    "WORKLOAD_HISTORIES",
    "WorkloadComplexityError",
    "build_workload_complexity_freeze",
]
