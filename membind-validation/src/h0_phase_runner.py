"""Protocol v1.3 H0 phase primitives with sanitized evidence boundaries.

This module contains no credential loading or service construction.  Live
adapters must pass already-authorized clients and graphs; every return value and
checkpoint emitted here is limited to identifiers, counts, hashes, and flags.
"""

from __future__ import annotations

from h0_bootstrap import disable_implicit_dotenv

disable_implicit_dotenv()

import hashlib
import inspect
import json
import math
import re
from copy import deepcopy
from dataclasses import dataclass
from typing import Any, Callable, Mapping, Sequence

from graphiti_core.nodes import EpisodeType, EpisodicNode
from graphiti_core.prompts import prompt_library
from graphiti_core.prompts.extract_nodes import ExtractedEntities
from graphiti_core.utils.maintenance.node_operations import _build_entity_types_context
from graphiti_core.utils.text_utils import concatenate_episodes

from dataset import Episode, build_episodes
from graphiti_native import parse_datetime
from h0_runtime import (
    H0AttemptLedger,
    H0InfrastructureError,
    H0ManifestError,
    H0QualificationError,
    H0SemanticError,
    canonical_json_sha256,
    validate_semantic_stage,
)
from instrumentation import episode_scope


_SHA256_RE = re.compile(r"[0-9a-f]{64}")
SAFE_SEMANTIC_FIELDS = frozenset(
    {
        "call_key",
        "response_model_name",
        "entity_count",
        "distinct_normalized_entity_name_count",
        "semantic_payload_sha256",
        "failure_codes",
        "qualified",
    }
)
_COLLECTOR_INPUT_FIELDS = SAFE_SEMANTIC_FIELDS | {"repeated_trial_index"}


async def _maybe_await(value: Any) -> Any:
    return await value if inspect.isawaitable(value) else value


def _require_nonnegative_int(value: Any, field: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise TypeError(f"{field} must be a non-negative integer")
    return value


def _safe_string_list_hash(values: Sequence[str]) -> str:
    return canonical_json_sha256([str(value) for value in values])


class H0SemanticEvidenceCollector:
    """Validate the client callback and retain only its preregistered projection."""

    def __init__(self) -> None:
        self.records: list[dict[str, Any]] = []
        self.repeated_trial_indices: list[int | None] = []

    def __call__(self, record: Mapping[str, Any]) -> None:
        if not isinstance(record, Mapping):
            raise TypeError("semantic evidence must be a mapping")
        fields = set(record)
        if fields != _COLLECTOR_INPUT_FIELDS:
            raise ValueError("semantic evidence fields do not match the safe contract")
        call_key = record.get("call_key")
        model_name = record.get("response_model_name")
        if not isinstance(call_key, str) or not call_key:
            raise TypeError("semantic call_key must be nonempty text")
        if not isinstance(model_name, str) or not model_name:
            raise TypeError("semantic response_model_name must be nonempty text")
        entity_count = _require_nonnegative_int(record.get("entity_count"), "entity_count")
        distinct_count = _require_nonnegative_int(
            record.get("distinct_normalized_entity_name_count"),
            "distinct_normalized_entity_name_count",
        )
        digest = record.get("semantic_payload_sha256")
        if not isinstance(digest, str) or _SHA256_RE.fullmatch(digest) is None:
            raise ValueError("semantic payload hash is invalid")
        failures = record.get("failure_codes")
        if not isinstance(failures, list) or any(
            not isinstance(value, str) for value in failures
        ):
            raise TypeError("semantic failure_codes must be a text list")
        if record.get("qualified") is not True or failures:
            raise ValueError("unqualified semantic evidence cannot enter a passing stage")
        repeated = _require_nonnegative_int(
            record.get("repeated_trial_index"), "repeated_trial_index"
        )
        self.records.append(
            {
                "call_key": call_key,
                "response_model_name": model_name,
                "entity_count": entity_count,
                "distinct_normalized_entity_name_count": distinct_count,
                "semantic_payload_sha256": digest,
                "failure_codes": [],
                "qualified": True,
            }
        )
        self.repeated_trial_indices.append(repeated)


@dataclass(frozen=True)
class H0APreparedCall:
    question_id: str
    source_sequence: int
    previous_episode_count: int
    messages: tuple[Any, ...]
    safe_evidence: dict[str, Any]


def _message_evidence(message: Any) -> dict[str, Any]:
    role = str(getattr(message, "role", ""))
    content = getattr(message, "content", None)
    if role not in {"system", "user"} or not isinstance(content, str):
        raise TypeError("H0-A prompt messages must be system/user text")
    encoded = content.encode("utf-8")
    return {
        "role": role,
        "character_count": len(content),
        "utf8_byte_count": len(encoded),
        "content_sha256": hashlib.sha256(encoded).hexdigest(),
    }


def prepare_h0_a_call(record: Mapping[str, Any]) -> H0APreparedCall:
    """Reconstruct the exact pinned Graphiti source-zero extraction call."""

    episodes = build_episodes(dict(record))
    if str(record.get("question_id") or "") != "07741c45" or not episodes:
        raise ValueError("H0-A requires frozen calibration history 07741c45")
    episode = episodes[0]
    if episode.source_sequence != 0:
        raise ValueError("H0-A requires source sequence zero")
    episodic = EpisodicNode(
        name=episode.name,
        group_id=episode.group_id,
        labels=[],
        source=EpisodeType.message,
        content=episode.body,
        source_description="LongMemEval-S haystack session",
        valid_at=parse_datetime(episode.reference_time),
    )
    context = {
        "episode_content": concatenate_episodes([episodic]),
        "episode_timestamp": episodic.valid_at.isoformat(),
        "previous_episodes": [],
        "custom_extraction_instructions": "",
        "entity_types": _build_entity_types_context(None),
        "source_description": episodic.source_description,
    }
    messages = tuple(prompt_library.extract_nodes.extract_message(context))
    safe_evidence = {
        "question_id": episode.question_id,
        "source_sequence": 0,
        "episode_source_sha256": episode.source_hash,
        "episode_body_sha256": hashlib.sha256(episode.body.encode("utf-8")).hexdigest(),
        "message_evidence": [_message_evidence(message) for message in messages],
        "message_bundle_sha256": canonical_json_sha256(
            [
                {"role": getattr(message, "role"), "content": getattr(message, "content")}
                for message in messages
            ]
        ),
        "response_schema_sha256": canonical_json_sha256(
            ExtractedEntities.model_json_schema()
        ),
        "raw_prompt_persisted": False,
    }
    return H0APreparedCall(
        question_id=episode.question_id,
        source_sequence=0,
        previous_episode_count=0,
        messages=messages,
        safe_evidence=safe_evidence,
    )


async def run_h0_a(
    *,
    record: Mapping[str, Any],
    stage_attempt_id: str,
    client_factory: Callable[[int, H0AttemptLedger, H0SemanticEvidenceCollector], Any],
    ledger: H0AttemptLedger,
    semantic_collector: H0SemanticEvidenceCollector,
    semantic_guardrail: Mapping[str, Any],
    trial_checkpoint: Callable[[dict[str, Any]], Any],
) -> dict[str, Any]:
    """Run the three bounded direct extraction calls with fresh clients."""

    if not stage_attempt_id:
        raise ValueError("stage_attempt_id is required")
    if ledger.stage_attempt_id != stage_attempt_id:
        raise H0ManifestError("H0-A ledger attempt ID mismatch")
    prepared = prepare_h0_a_call(record)
    start_records = len(semantic_collector.records)
    semantic_stage: dict[str, Any] | None = None
    for repeated_trial_index in range(3):
        client = client_factory(repeated_trial_index, ledger, semantic_collector)
        if client is None:
            raise TypeError("H0-A client factory returned no client")
        try:
            if getattr(client, "h0_ledger", None) is not ledger:
                raise H0ManifestError("H0-A clients must use the shared stage ledger")
            trial_start = len(ledger.trials)
            attempt_start = len(ledger.attempts)
            semantic_before = len(semantic_collector.records)
            with episode_scope(stage_attempt_id, 0):
                await client.generate_response(
                    deepcopy(list(prepared.messages)),
                    response_model=ExtractedEntities,
                    group_id="07741c45",
                    prompt_name="extract_nodes.extract_message",
                    attribute_extraction=False,
                )
            new_trial_ids = list(ledger.trials)[trial_start:]
            new_attempts = ledger.attempts[attempt_start:]
            if len(new_trial_ids) != 1 or len(new_attempts) != 1:
                raise H0QualificationError(
                    "H0-A requires one logical call and one HTTP attempt per trial"
                )
            trial = ledger.trials[new_trial_ids[0]]
            if (
                trial.get("call_key")
                != "07741c45:0:extract_nodes.extract_message"
                or trial.get("repeated_trial_index") != repeated_trial_index
                or ledger.trial_verdict(new_trial_ids[0]).get("qualified") is not True
                or new_attempts[0].get("retry_index") != 0
            ):
                raise H0QualificationError("H0-A ledger trial did not qualify")
            if len(semantic_collector.records) != semantic_before + 1:
                raise H0SemanticError("H0-A requires one semantic record per logical trial")
            if semantic_collector.repeated_trial_indices[-1] != repeated_trial_index:
                raise H0SemanticError("H0-A semantic repeated-trial index mismatch")
        finally:
            await close_h0_client(client)
        observed = len(semantic_collector.records) - start_records
        if observed != repeated_trial_index + 1:
            raise H0SemanticError("H0-A requires one semantic record per logical trial")
        if repeated_trial_index == 2:
            semantic_stage = validate_semantic_stage(
                semantic_guardrail,
                semantic_collector.records[start_records:],
            )
            if semantic_stage.get("qualified") is not True:
                raise H0SemanticError("H0-A semantic stage did not qualify")
        semantic_record = semantic_collector.records[-1]
        checkpoint = {
            "schema_version": "membind.h0.phase-checkpoint.v1",
            "stage_attempt_id": stage_attempt_id,
            "phase": "H0-A",
            "question_id": prepared.question_id,
            "source_sequence": 0,
            "repeated_trial_index": repeated_trial_index,
            "logical_call_count": 1,
            "http_attempt_count": 1,
            "retry_count": 0,
            "semantic_payload_sha256": semantic_record["semantic_payload_sha256"],
            "prompt_evidence_sha256": canonical_json_sha256(prepared.safe_evidence),
            "qualified": True,
            "final_stage_checks_passed": repeated_trial_index == 2,
            "raw_prompts_persisted": False,
            "raw_responses_persisted": False,
            "secrets_persisted": False,
        }
        await _maybe_await(trial_checkpoint(checkpoint))
    return {
        "schema_version": "membind.h0.phase-result.v1",
        "stage_attempt_id": stage_attempt_id,
        "phase": "H0-A",
        "question_id": prepared.question_id,
        "qualified": True,
        "logical_call_count": 3,
        "http_attempt_count": 3,
        "retry_count": 0,
        "semantic_record_count": 3,
        "semantic_stage_sha256": canonical_json_sha256(semantic_stage),
        "prompt_evidence_sha256": canonical_json_sha256(prepared.safe_evidence),
        "secrets_persisted": False,
    }


async def close_h0_client(client: Any) -> None:
    """Close the per-trial tokenizer and OpenAI HTTP client exactly once each."""

    targets = (getattr(client, "h0_token_counter", None), getattr(client, "client", None))
    seen: set[int] = set()
    errors: list[BaseException] = []
    for target in targets:
        if target is None or id(target) in seen:
            continue
        seen.add(id(target))
        close = getattr(target, "close", None)
        if not callable(close):
            errors.append(TypeError("H0 client resource has no close method"))
            continue
        try:
            await _maybe_await(close())
        except BaseException as exc:  # close both resources before surfacing cleanup failure
            errors.append(exc)
    if errors:
        raise H0ManifestError("H0 client cleanup failed") from errors[0]


def _mapping_tuple(value: Any) -> tuple[int, str, str] | None:
    if not isinstance(value, Mapping):
        return None
    sequence = value.get("source_sequence")
    source_hash = value.get("source_hash")
    session_id = value.get("session_id")
    if (
        isinstance(sequence, bool)
        or not isinstance(sequence, int)
        or sequence < 0
        or not isinstance(source_hash, str)
        or not isinstance(session_id, str)
    ):
        return None
    return sequence, source_hash, session_id


def _edge_attribution_values(value: Any) -> tuple[str | None, list[int]]:
    if value is None or value == []:
        return "edge_attribution_missing", []
    items = value if isinstance(value, list) else [value]
    if any(isinstance(item, bool) or not isinstance(item, int) for item in items):
        return "edge_attribution_missing", []
    return None, list(items)


def validate_full_history_outputs(
    *,
    instance: Mapping[str, Any],
    episodes: Sequence[Episode],
    graph_output: Mapping[str, Any],
    retrieval_output: Mapping[str, Any],
) -> dict[str, Any]:
    """Validate raw in-memory outputs and return only safe aggregate evidence."""

    failures: list[str] = []
    expected = {
        (episode.source_sequence, episode.source_hash, episode.session_id)
        for episode in episodes
    }
    raw_mappings = graph_output.get("episodes")
    raw_mappings = raw_mappings if isinstance(raw_mappings, list) else []
    parsed_mappings = [_mapping_tuple(value) for value in raw_mappings]
    actual = {value for value in parsed_mappings if value is not None}
    unknown_mapping_count = sum(
        1 for value in parsed_mappings if value is None or value not in expected
    )
    if len(raw_mappings) != len(episodes):
        failures.append("episode_mapping_count_mismatch")
    if actual != expected or len(raw_mappings) != len(expected):
        failures.append("episode_mapping_not_exact")
    if unknown_mapping_count:
        failures.append("unknown_episode_mapping")

    entities = graph_output.get("entities")
    entities = entities if isinstance(entities, list) else []
    edges = graph_output.get("edges")
    edges = edges if isinstance(edges, list) else []
    if not entities:
        failures.append("semantic_graph_empty")
    allowed_sequences = {episode.source_sequence for episode in episodes}
    missing_attribution = 0
    out_of_scope_attribution = 0
    for edge in edges:
        value = edge.get("source_episode_sequence") if isinstance(edge, Mapping) else None
        failure, values = _edge_attribution_values(value)
        if failure is not None:
            missing_attribution += 1
            continue
        if any(sequence not in allowed_sequences for sequence in values):
            out_of_scope_attribution += 1
    if missing_attribution:
        failures.append("edge_attribution_missing")
    if out_of_scope_attribution:
        failures.append("edge_attribution_out_of_scope")

    gold = instance.get("answer_session_ids")
    gold = [str(value) for value in gold] if isinstance(gold, list) else []
    if not gold:
        failures.append("gold_evidence_empty")
    top_k = retrieval_output.get("top_k")
    if top_k != 10:
        failures.append("retrieval_top_k_not_10")
    metrics = retrieval_output.get("metrics")
    recall = metrics.get("evidence_recall_at_10") if isinstance(metrics, Mapping) else None
    if (
        isinstance(recall, bool)
        or not isinstance(recall, (int, float))
        or not math.isfinite(float(recall))
        or float(recall) <= 0
    ):
        failures.append("evidence_recall_at_10_nonpositive")
    retrieved = retrieval_output.get("retrieved_episode_ids")
    retrieved = [str(value) for value in retrieved] if isinstance(retrieved, list) else []
    graph_hash = graph_output.get("canonical_graph_hash")
    if not isinstance(graph_hash, str) or _SHA256_RE.fullmatch(graph_hash) is None:
        failures.append("canonical_graph_hash_invalid")

    failure_codes = list(dict.fromkeys(failures))
    return {
        "schema_version": "membind.h0.full-history-evidence.v1",
        "question_id": str(instance.get("question_id") or ""),
        "qualified": not failure_codes,
        "failure_codes": failure_codes,
        "entity_count": len(entities),
        "edge_count": len(edges),
        "episode_mapping_count": len(raw_mappings),
        "expected_episode_count": len(episodes),
        "unknown_mapping_count": unknown_mapping_count,
        "episode_mapping_exact": actual == expected and len(raw_mappings) == len(expected),
        "edge_attribution_complete": not missing_attribution
        and not out_of_scope_attribution,
        "edge_attribution_missing_count": missing_attribution,
        "edge_attribution_out_of_scope_count": out_of_scope_attribution,
        "canonical_graph_sha256": graph_hash if isinstance(graph_hash, str) else None,
        "top_k": top_k,
        "gold_evidence_count": len(gold),
        "retrieved_evidence_count": len(retrieved),
        "gold_evidence_ids_sha256": _safe_string_list_hash(gold),
        "retrieved_evidence_ids_sha256": _safe_string_list_hash(retrieved),
        "evidence_recall_at_10": float(recall)
        if isinstance(recall, (int, float)) and not isinstance(recall, bool)
        else None,
        "raw_graph_persisted": False,
        "raw_retrieval_persisted": False,
    }


async def run_full_history(
    *,
    instance: Mapping[str, Any],
    episodes: Sequence[Episode],
    stage_attempt_id: str,
    graph_factory: Callable[[], Any],
    clear_graph: Callable[[Any], Any],
    assert_graph_empty: Callable[[Any], Any],
    close_graph: Callable[[Any], Any],
    ingest_episode: Callable[[Any, Episode], Any],
    export_graph: Callable[[Any, list[Episode], str], Any],
    evaluate_retrieval: Callable[[Any, Mapping[str, Any], list[Episode]], Any],
    source_checkpoint: Callable[[dict[str, Any]], Any],
    semantic_collector: H0SemanticEvidenceCollector,
    semantic_guardrail: Mapping[str, Any],
    ledger: H0AttemptLedger,
    cleanup_error_sink: Callable[[dict[str, Any]], Any] | None = None,
) -> dict[str, Any]:
    """Run one complete history serially in a fresh, caller-provided graph."""

    if not stage_attempt_id:
        raise ValueError("stage_attempt_id is required")
    if ledger.stage_attempt_id != stage_attempt_id:
        raise H0ManifestError("full-history ledger attempt ID mismatch")
    ordered = sorted(episodes, key=lambda episode: episode.source_sequence)
    if not ordered or [episode.source_sequence for episode in ordered] != list(
        range(len(ordered))
    ):
        raise ValueError("full-history sources must be contiguous from zero")
    question_id = str(instance.get("question_id") or "")
    if any(episode.question_id != question_id for episode in ordered):
        raise ValueError("full-history episode identity mismatch")
    graph = await _maybe_await(graph_factory())
    if graph is None:
        raise TypeError("graph factory returned no graph")
    graph_close_attempted = False
    primary_error: BaseException | None = None
    semantic_start = len(semantic_collector.records)
    trial_stage_start = len(ledger.trials)
    attempt_stage_start = len(ledger.attempts)
    try:
        graph_llm = getattr(graph, "llm_client", None)
        if graph_llm is None:
            graph_llm = getattr(getattr(graph, "clients", None), "llm_client", None)
        if getattr(graph_llm, "h0_ledger", None) is not ledger:
            raise H0ManifestError("full-history graph must use the shared stage ledger")
        await _maybe_await(clear_graph(graph))
        await _maybe_await(assert_graph_empty(graph))
        for index, episode in enumerate(ordered):
            trial_before = len(ledger.trials)
            attempt_before = len(ledger.attempts)
            semantic_before = len(semantic_collector.records)
            with episode_scope(question_id, episode.source_sequence):
                await _maybe_await(ingest_episode(graph, episode))
            new_trial_ids = list(ledger.trials)[trial_before:]
            new_attempts = ledger.attempts[attempt_before:]
            semantic_delta = len(semantic_collector.records) - semantic_before
            if not new_trial_ids:
                raise H0ManifestError("source produced no LLM evidence")
            if len(new_attempts) != len(new_trial_ids):
                raise H0QualificationError(
                    "source logical-call/HTTP-attempt counts do not match"
                )
            if semantic_delta != len(new_trial_ids):
                raise H0ManifestError("source semantic evidence count mismatch")
            expected_prefix = f"{question_id}:{episode.source_sequence}:"
            for logical_id in new_trial_ids:
                trial = ledger.trials[logical_id]
                if (
                    not str(trial.get("call_key") or "").startswith(expected_prefix)
                    or trial.get("repeated_trial_index") != 0
                    or ledger.trial_verdict(logical_id).get("qualified") is not True
                ):
                    raise H0QualificationError("source logical trial did not qualify")
            if any(attempt.get("retry_index") != 0 for attempt in new_attempts):
                raise H0QualificationError("candidate-induced retry cannot qualify")
            new_semantic_indices = semantic_collector.repeated_trial_indices[
                semantic_before:
            ]
            if new_semantic_indices != [0] * len(new_trial_ids):
                raise H0SemanticError("source semantic repeated-trial index mismatch")
            source_ledger_sha256 = canonical_json_sha256(ledger.safe_artifact())
            if index < len(ordered) - 1:
                await _maybe_await(
                    source_checkpoint(
                        {
                            "schema_version": "membind.h0.phase-checkpoint.v1",
                            "stage_attempt_id": stage_attempt_id,
                            "question_id": question_id,
                            "source_sequence": episode.source_sequence,
                            "final_stage_checks_passed": False,
                            "source_sha256": episode.source_hash,
                            "logical_call_count": len(new_trial_ids),
                            "http_attempt_count": len(new_attempts),
                            "retry_count": 0,
                            "ledger_sha256": source_ledger_sha256,
                            "raw_prompts_persisted": False,
                            "raw_responses_persisted": False,
                            "secrets_persisted": False,
                        }
                    )
                )

        graph_output = await _maybe_await(
            export_graph(graph, ordered, ordered[0].group_id)
        )
        retrieval_output = await _maybe_await(
            evaluate_retrieval(graph, instance, ordered)
        )
        evidence = validate_full_history_outputs(
            instance=instance,
            episodes=ordered,
            graph_output=graph_output,
            retrieval_output=retrieval_output,
        )
        try:
            logical_call_count = len(ledger.trials) - trial_stage_start
            http_attempt_count = len(ledger.attempts) - attempt_stage_start
            semantic_record_count = len(semantic_collector.records) - semantic_start
            if logical_call_count <= 0 or semantic_record_count != logical_call_count:
                raise H0SemanticError("full-history semantic/ledger count mismatch")
            if http_attempt_count != logical_call_count:
                raise H0QualificationError(
                    "full-history logical-call/HTTP-attempt counts do not match"
                )
            semantic_stage = validate_semantic_stage(
                semantic_guardrail,
                semantic_collector.records[semantic_start:],
            )
            if semantic_stage.get("qualified") is not True:
                raise H0SemanticError("semantic stage did not qualify")
        except H0SemanticError:
            return {
                "schema_version": "membind.h0.phase-result.v1",
                "stage_attempt_id": stage_attempt_id,
                "question_id": question_id,
                "qualified": False,
                "failure_codes": ["semantic_stage_failure"],
                "secrets_persisted": False,
            }
        if not evidence["qualified"]:
            return {
                **evidence,
                "stage_attempt_id": stage_attempt_id,
                "semantic_stage_sha256": canonical_json_sha256(semantic_stage),
            }
        final_episode = ordered[-1]
        graph_close_attempted = True
        await _maybe_await(close_graph(graph))
        await _maybe_await(
            source_checkpoint(
                {
                    "schema_version": "membind.h0.phase-checkpoint.v1",
                    "stage_attempt_id": stage_attempt_id,
                    "question_id": question_id,
                    "source_sequence": final_episode.source_sequence,
                    "final_stage_checks_passed": True,
                    "source_sha256": final_episode.source_hash,
                    "full_history_evidence_sha256": canonical_json_sha256(evidence),
                    "semantic_stage_sha256": canonical_json_sha256(semantic_stage),
                    "logical_call_count": logical_call_count,
                    "http_attempt_count": http_attempt_count,
                    "retry_count": 0,
                    "ledger_sha256": canonical_json_sha256(ledger.safe_artifact()),
                    "raw_prompts_persisted": False,
                    "raw_responses_persisted": False,
                    "secrets_persisted": False,
                }
            )
        )
        return {
            **evidence,
            "stage_attempt_id": stage_attempt_id,
            "logical_call_count": logical_call_count,
            "http_attempt_count": http_attempt_count,
            "retry_count": 0,
            "semantic_record_count": semantic_record_count,
            "semantic_records": deepcopy(
                semantic_collector.records[semantic_start:]
            ),
            "ledger_sha256": canonical_json_sha256(ledger.safe_artifact()),
            "semantic_stage_sha256": canonical_json_sha256(semantic_stage),
        }
    except BaseException as exc:
        primary_error = exc
        raise
    finally:
        if not graph_close_attempted:
            graph_close_attempted = True
            try:
                await _maybe_await(close_graph(graph))
            except BaseException:
                if primary_error is None:
                    raise
                if cleanup_error_sink is not None:
                    event = {
                        "schema_version": "membind.h0.cleanup-failure.v1",
                        "stage_attempt_id": stage_attempt_id,
                        "question_id": question_id,
                        "event": "secondary_cleanup_failure",
                        "primary_failure_class": (
                            "infrastructure"
                            if isinstance(primary_error, H0InfrastructureError)
                            else "protocol_or_runtime"
                        ),
                        "cleanup_failure_class": "cleanup_error",
                        "raw_errors_persisted": False,
                        "secrets_persisted": False,
                    }
                    try:
                        await _maybe_await(cleanup_error_sink(event))
                    except BaseException:
                        pass


@dataclass(frozen=True)
class H0FullHistoryItem:
    """One in-memory calibration history selected by the frozen H0 phase."""

    question_id: str
    instance: Mapping[str, Any]
    episodes: tuple[Episode, ...]


def build_h0_full_history_workload(
    corpus: Any,
    phase_name: str,
) -> tuple[H0FullHistoryItem, ...]:
    """Select exactly the preregistered H0-B or H0-C complete histories."""

    if phase_name not in {"H0-B", "H0-C"}:
        raise ValueError("full-history phase must be H0-B or H0-C")
    expected_ids = ("07741c45", "b6019101", "6071bd76", "a2f3aa27")
    if tuple(getattr(corpus, "question_ids", ())) != expected_ids:
        raise H0ManifestError("H0 calibration history order is not frozen")
    records = getattr(corpus, "records", None)
    episodes_by_id = getattr(corpus, "episodes", None)
    if not isinstance(records, Mapping) or not isinstance(episodes_by_id, Mapping):
        raise H0ManifestError("H0 calibration corpus is incomplete")
    selected = expected_ids[:1] if phase_name == "H0-B" else expected_ids[1:]
    expected_counts = {"07741c45": 49, "b6019101": 49, "6071bd76": 46, "a2f3aa27": 44}
    items: list[H0FullHistoryItem] = []
    for question_id in selected:
        record = records.get(question_id)
        raw_episodes = episodes_by_id.get(question_id)
        if not isinstance(record, Mapping) or not isinstance(raw_episodes, (tuple, list)):
            raise H0ManifestError(f"H0 history is missing: {question_id}")
        episodes = tuple(raw_episodes)
        sequences = [episode.source_sequence for episode in episodes]
        if (
            len(episodes) != expected_counts[question_id]
            or sequences != list(range(expected_counts[question_id]))
            or any(episode.question_id != question_id for episode in episodes)
        ):
            raise H0ManifestError(f"H0 history workload drift: {question_id}")
        items.append(H0FullHistoryItem(question_id, record, episodes))
    return tuple(items)


async def run_h0_full_history_phase(
    *,
    corpus: Any,
    phase_name: str,
    stage_attempt_id: str,
    history_runner: Callable[..., Any],
    semantic_guardrail: Mapping[str, Any],
    prior_interrupted_attempt_id: str | None = None,
) -> dict[str, Any]:
    """Run H0-B/C from the first history; interrupted attempts are never resumed."""

    if not stage_attempt_id:
        raise ValueError("stage_attempt_id is required")
    if prior_interrupted_attempt_id == stage_attempt_id:
        raise H0ManifestError("infrastructure recovery requires a new attempt ID")
    if not callable(history_runner):
        raise TypeError("history_runner must be callable")
    if not isinstance(semantic_guardrail, Mapping):
        raise TypeError("semantic_guardrail must be a mapping")
    items = build_h0_full_history_workload(corpus, phase_name)
    completed: list[dict[str, str]] = []
    combined_semantic_records: list[dict[str, Any]] = []
    for item in items:
        result = await _maybe_await(
            history_runner(
                item=item,
                stage_attempt_id=stage_attempt_id,
                phase_name=phase_name,
            )
        )
        if not isinstance(result, Mapping):
            raise H0ManifestError("history runner returned invalid evidence")
        result_hash = canonical_json_sha256(result)
        if result.get("qualified") is not True:
            return {
                "schema_version": "membind.h0.full-history-phase-result.v1",
                "stage_attempt_id": stage_attempt_id,
                "phase": phase_name,
                "qualified": False,
                "completed_history_count": len(completed),
                "failed_history_id": item.question_id,
                "failed_history_evidence_sha256": result_hash,
                "partial_qualification_reusable": False,
            }
        raw_semantic_records = result.get("semantic_records")
        if not isinstance(raw_semantic_records, list) or not raw_semantic_records:
            raise H0ManifestError("qualified history has no semantic record projection")
        for raw_record in raw_semantic_records:
            if not isinstance(raw_record, Mapping) or set(raw_record) != SAFE_SEMANTIC_FIELDS:
                raise H0ManifestError("history semantic record projection is invalid")
            validator = H0SemanticEvidenceCollector()
            validator({**dict(raw_record), "repeated_trial_index": 0})
            combined_semantic_records.append(validator.records[0])
        completed.append(
            {"question_id": item.question_id, "evidence_sha256": result_hash}
        )
    try:
        semantic_stage = validate_semantic_stage(
            semantic_guardrail, combined_semantic_records
        )
    except H0SemanticError:
        return {
            "schema_version": "membind.h0.full-history-phase-result.v1",
            "stage_attempt_id": stage_attempt_id,
            "phase": phase_name,
            "qualified": False,
            "failure_codes": ["cross_history_semantic_stage_failure"],
            "completed_history_count": len(completed),
            "combined_semantic_record_count": len(combined_semantic_records),
            "combined_semantic_projection_sha256": canonical_json_sha256(
                combined_semantic_records
            ),
            "partial_qualification_reusable": False,
        }
    return {
        "schema_version": "membind.h0.full-history-phase-result.v1",
        "stage_attempt_id": stage_attempt_id,
        "phase": phase_name,
        "qualified": True,
        "completed_history_count": len(completed),
        "completed_histories": completed,
        "combined_semantic_record_count": len(combined_semantic_records),
        "combined_semantic_projection_sha256": canonical_json_sha256(
            combined_semantic_records
        ),
        "semantic_stage_sha256": canonical_json_sha256(semantic_stage),
        "partial_qualification_reusable": True,
    }
