"""Parameterized correctness results for one fixed-three S4 sidecar block."""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from copy import deepcopy
from typing import Any

from . import PROTOCOL_VERSION
from .artifacts import finalize_envelope, payload_sha256
from .s4_sidecar_qualification_data import (
    EXPECTED_EPISODE_COUNTS,
    LIVE_HISTORY_IDS,
)


EVALUATION_SCHEMA = (
    "membind.paper-eval-v3.s4-sidecar-qualification-evaluation.v1"
)
RESULT_SCHEMA = (
    "membind.paper-eval-v3.s4-sidecar-qualification-block-result.v1"
)
FIXED_THREE_RESULT_SCHEMA = (
    "membind.paper-eval-v3.s4-sidecar-fixed-three-result.v1"
)
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_BASE_FIELDS = {
    "live_llm_calls",
    "live_embedding_calls",
    "resolved_prompt_count",
    "resolved_embedding_count",
    "unexpected_prompt_count",
    "unexpected_embedding_count",
    "live_fallback_count",
    "cross_encoder_call_count",
}
_SIDECAR_FIELDS = {
    "sidecar_exact_hit_count",
    "sidecar_remap_hit_count",
    "sidecar_rejection_count",
    "sidecar_capture_append_count",
    "sidecar_capture_reuse_count",
    "sidecar_replay_binding_count",
    "sidecar_record_count",
    "sidecar_consumed_count",
    "sidecar_remaining_count",
    "sidecar_resumed_consumed_count",
    "sidecar_prepared_count",
}
_REMAP_FIELDS = {
    "exact_prompt_hit_count",
    "candidate_remap_hit_count",
    "candidate_remap_node_hit_count",
    "candidate_remap_edge_hit_count",
    "candidate_remap_rejection_count",
}
_CACHE_FIELDS = {
    "prompt_cache_sha256",
    "embedding_cache_sha256",
    "candidate_sidecar_sha256",
}


def _mapping(value: object, *, label: str) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise ValueError(f"{label} must be a mapping")
    return deepcopy(dict(value))


def _sha(value: object, *, field: str) -> str:
    if not isinstance(value, str) or _SHA256.fullmatch(value) is None:
        raise ValueError(f"{field} is not a SHA256")
    return value


def _nonnegative_ints(value: Mapping[str, Any], fields: set[str]) -> bool:
    return all(
        isinstance(value.get(field), int)
        and not isinstance(value.get(field), bool)
        and value[field] >= 0
        for field in fields
    )


def _payload(value: Mapping[str, Any]) -> dict[str, Any]:
    child = value.get("payload")
    return deepcopy(dict(child)) if isinstance(child, Mapping) else {}


def evaluate_s4_sidecar_qualification_block(
    *,
    capture_result: Mapping[str, Any],
    replay_result: Mapping[str, Any],
    history_id: str,
    expected_episode_count: int,
) -> dict[str, Any]:
    """Recompute every construction correctness gate for one dynamic block."""

    if (
        history_id not in LIVE_HISTORY_IDS
        or expected_episode_count != EXPECTED_EPISODE_COUNTS[history_id]
    ):
        raise ValueError("S4 qualification result history identity drift")
    capture = _payload(capture_result)
    replay = _payload(replay_result)
    capture_runtime = (
        deepcopy(dict(capture.get("runtime_evidence")))
        if isinstance(capture.get("runtime_evidence"), Mapping)
        else {}
    )
    replay_runtime = (
        deepcopy(dict(replay.get("runtime_evidence")))
        if isinstance(replay.get("runtime_evidence"), Mapping)
        else {}
    )
    capture_cache = (
        deepcopy(dict(capture.get("cache_evidence")))
        if isinstance(capture.get("cache_evidence"), Mapping)
        else {}
    )
    replay_cache = (
        deepcopy(dict(replay.get("cache_evidence")))
        if isinstance(replay.get("cache_evidence"), Mapping)
        else {}
    )
    failures: list[str] = []

    def fail(code: str) -> None:
        if code not in failures:
            failures.append(code)

    complete = list(range(expected_episode_count))
    if (
        capture.get("history_id") != history_id
        or capture.get("status") != "PASS"
        or capture.get("expected_episode_count") != expected_episode_count
        or capture.get("completed_source_sequences") != complete
    ):
        fail("capture_episode_coverage")
    if (
        replay.get("history_id") != history_id
        or replay.get("status") != "PASS"
        or replay.get("expected_episode_count") != expected_episode_count
        or replay.get("completed_source_sequences") != complete
    ):
        fail("replay_episode_coverage")

    graph_parity = (
        isinstance(capture.get("canonical_graph_sha256"), str)
        and capture.get("canonical_graph_sha256")
        == replay.get("canonical_graph_sha256")
    )
    if not graph_parity:
        fail("canonical_graph_parity")
    cache_mutation = not (
        set(capture_cache) == _CACHE_FIELDS == set(replay_cache)
        and capture_cache == replay_cache
    )
    if cache_mutation:
        fail("cache_or_sidecar_mutation")

    capture_fields = _BASE_FIELDS | _SIDECAR_FIELDS
    replay_fields = capture_fields | _REMAP_FIELDS
    evidence_shape = (
        set(capture_runtime) == capture_fields
        and set(replay_runtime) == replay_fields
        and _nonnegative_ints(capture_runtime, capture_fields)
        and _nonnegative_ints(replay_runtime, replay_fields)
    )
    if not evidence_shape:
        fail("sidecar_evidence_shape")

    semantic_ratio: float | None = None
    capture_records = 0
    replay_records = 0
    consumption_exact = False
    sidecar_accounting = False
    if evidence_shape:
        if (
            capture_runtime["live_llm_calls"] <= 0
            or capture_runtime["live_embedding_calls"] <= 0
        ):
            fail("capture_live_model_call")
        if (
            replay_runtime["live_llm_calls"] != 0
            or replay_runtime["live_embedding_calls"] != 0
        ):
            fail("replay_live_model_call")
        if (
            replay_runtime["unexpected_prompt_count"] != 0
            or replay_runtime["unexpected_embedding_count"] != 0
        ):
            fail("replay_oracle_miss")
        if replay_runtime["live_fallback_count"] != 0:
            fail("replay_live_fallback")
        if replay_runtime["cross_encoder_call_count"] != 0:
            fail("replay_cross_encoder_call")
        for field in ("resolved_prompt_count", "resolved_embedding_count"):
            if capture_runtime[field] != replay_runtime[field]:
                fail(field)
        capture_work = (
            capture_runtime["resolved_prompt_count"]
            + capture_runtime["resolved_embedding_count"]
        )
        replay_work = (
            replay_runtime["resolved_prompt_count"]
            + replay_runtime["resolved_embedding_count"]
        )
        semantic_ratio = replay_work / capture_work if capture_work else None
        if semantic_ratio != 1.0:
            fail("semantic_work_ratio")

        if (
            capture_runtime["sidecar_rejection_count"] != 0
            or replay_runtime["sidecar_rejection_count"] != 0
            or replay_runtime["candidate_remap_rejection_count"] != 0
        ):
            fail("sidecar_or_remap_rejection")
        if replay_runtime["candidate_remap_edge_hit_count"] != 0:
            fail("legacy_edge_remap_used")
        if (
            replay_runtime["candidate_remap_node_hit_count"]
            + replay_runtime["candidate_remap_edge_hit_count"]
            != replay_runtime["candidate_remap_hit_count"]
        ):
            fail("candidate_remap_breakdown")

        capture_records = capture_runtime["sidecar_record_count"]
        replay_records = replay_runtime["sidecar_record_count"]
        if capture_records <= 0 or capture_records != replay_records:
            fail("sidecar_record_parity")
        consumption_exact = (
            replay_runtime["sidecar_prepared_count"] == 0
            and replay_runtime["sidecar_remaining_count"] == 0
            and replay_runtime["sidecar_consumed_count"] == replay_records
        )
        if not consumption_exact:
            fail("sidecar_consumption")
        exact_non_sidecar = (
            replay_runtime["exact_prompt_hit_count"]
            - replay_runtime["sidecar_exact_hit_count"]
        )
        sidecar_accounting = (
            exact_non_sidecar >= 0
            and replay_runtime["sidecar_exact_hit_count"]
            <= replay_runtime["sidecar_remap_hit_count"]
            and replay_runtime["sidecar_remap_hit_count"] == replay_records
            and exact_non_sidecar
            + replay_runtime["candidate_remap_hit_count"]
            + replay_runtime["sidecar_remap_hit_count"]
            == replay_runtime["resolved_prompt_count"]
        )
        if not sidecar_accounting:
            fail("edge_sidecar_resolution_accounting")

    verdict = "PASS" if not failures else "FAIL"
    return {
        "schema_version": EVALUATION_SCHEMA,
        "history_id": history_id,
        "expected_episode_count": expected_episode_count,
        "verdict": verdict,
        "failures": failures,
        "canonical_graph_parity": graph_parity,
        "cache_and_sidecar_mutation_during_replay": cache_mutation,
        "sidecar_record_count": capture_records,
        "replay_sidecar_record_count": replay_records,
        "sidecar_consumption_exact": consumption_exact,
        "edge_sidecar_resolution_accounting": sidecar_accounting,
        "semantic_work_ratio": semantic_ratio,
        "live_serving_token_ratio": None,
        "live_serving_token_ratio_is_fairness_guardrail": False,
        "s5_authorized": False,
        "pilot_execution_authorized": False,
    }


def _envelope(value: Mapping[str, Any], *, label: str) -> dict[str, Any]:
    artifact = _mapping(value, label=label)
    payload = _mapping(artifact.get("payload"), label=f"{label} payload")
    if (
        artifact.get("protocol_version") != PROTOCOL_VERSION
        or artifact.get("status") != "finalized"
        or artifact.get("payload_sha256") != payload_sha256(payload)
    ):
        raise ValueError(f"{label} envelope hash drift")
    artifact["payload"] = payload
    return artifact


def build_s4_sidecar_qualification_block_result(
    *,
    history_id: str,
    block_index: int,
    expected_episode_count: int,
    authority_file_sha256: str,
    authority_payload_sha256: str,
    consumption_file_sha256: str,
    capture_result: Mapping[str, Any],
    capture_result_file_sha256: str,
    replay_result: Mapping[str, Any],
    replay_result_file_sha256: str,
    candidate_sidecar_file_sha256: str,
    git_commit: str,
) -> dict[str, Any]:
    """Seal one PASS block; failed evaluations remain non-authorizing."""

    if (
        history_id not in LIVE_HISTORY_IDS
        or block_index != LIVE_HISTORY_IDS.index(history_id)
        or expected_episode_count != EXPECTED_EPISODE_COUNTS[history_id]
    ):
        raise ValueError("S4 qualification block result identity drift")
    capture = _envelope(capture_result, label="capture phase")
    replay = _envelope(replay_result, label="replay phase")
    evaluation = evaluate_s4_sidecar_qualification_block(
        capture_result=capture,
        replay_result=replay,
        history_id=history_id,
        expected_episode_count=expected_episode_count,
    )
    if evaluation["verdict"] != "PASS":
        raise ValueError("S4 qualification block is not a complete PASS")
    sidecar_sha = _sha(
        candidate_sidecar_file_sha256, field="candidate sidecar file"
    )
    for phase, label in ((capture, "capture"), (replay, "replay")):
        cache = _mapping(
            phase["payload"].get("cache_evidence"),
            label=f"{label} cache evidence",
        )
        if cache.get("candidate_sidecar_sha256") != sidecar_sha:
            raise ValueError("S4 qualification sidecar file binding drift")
    payload = {
        "schema_version": RESULT_SCHEMA,
        "stage": "S4_FIXED_THREE_SIDECAR_QUALIFICATION_BLOCK",
        "history_id": history_id,
        "block_index": block_index,
        "expected_episode_count": expected_episode_count,
        "verdict": "PASS",
        "authority_file_sha256": _sha(
            authority_file_sha256, field="authority file"
        ),
        "authority_payload_sha256": _sha(
            authority_payload_sha256, field="authority payload"
        ),
        "consumption_file_sha256": _sha(
            consumption_file_sha256, field="consumption file"
        ),
        "capture_result_file_sha256": _sha(
            capture_result_file_sha256, field="capture result file"
        ),
        "replay_result_file_sha256": _sha(
            replay_result_file_sha256, field="replay result file"
        ),
        "candidate_sidecar_file_sha256": sidecar_sha,
        "capture_result_payload_sha256": capture["payload_sha256"],
        "replay_result_payload_sha256": replay["payload_sha256"],
        "evaluation": evaluation,
        "paired_descriptive_status": (
            "NOT_EXECUTED_IN_CONSTRUCTION_CORRECTNESS_LANE"
        ),
        "next_block_authorized": block_index < len(LIVE_HISTORY_IDS) - 1,
        "fixed_three_aggregation_authorized": (
            block_index == len(LIVE_HISTORY_IDS) - 1
        ),
        "s5_authorized": False,
        "pilot_execution_authorized": False,
    }
    return verify_s4_sidecar_qualification_block_result(
        finalize_envelope(
            payload=payload,
            protocol_version=PROTOCOL_VERSION,
            git_commit=str(git_commit),
            run_id=f"s4q-result-{history_id}-001",
        )
    )


def _reject_private(value: object) -> None:
    forbidden = {
        "answer",
        "api_key",
        "body",
        "content",
        "fact",
        "messages",
        "password",
        "prompt",
        "question",
        "raw_output",
        "raw_response",
        "secret",
        "uuid",
    }
    if isinstance(value, Mapping):
        for key, child in value.items():
            if str(key).casefold() in forbidden:
                raise ValueError("S4 qualification result contains private data")
            _reject_private(child)
    elif isinstance(value, Sequence) and not isinstance(value, (str, bytes)):
        for child in value:
            _reject_private(child)


def verify_s4_sidecar_qualification_block_result(
    value: Mapping[str, Any],
) -> dict[str, Any]:
    artifact = _envelope(value, label="S4 qualification block result")
    payload = artifact["payload"]
    expected_fields = {
        "schema_version",
        "stage",
        "history_id",
        "block_index",
        "expected_episode_count",
        "verdict",
        "authority_file_sha256",
        "authority_payload_sha256",
        "consumption_file_sha256",
        "capture_result_file_sha256",
        "replay_result_file_sha256",
        "candidate_sidecar_file_sha256",
        "capture_result_payload_sha256",
        "replay_result_payload_sha256",
        "evaluation",
        "paired_descriptive_status",
        "next_block_authorized",
        "fixed_three_aggregation_authorized",
        "s5_authorized",
        "pilot_execution_authorized",
    }
    history_id = payload.get("history_id")
    if history_id not in LIVE_HISTORY_IDS:
        raise ValueError("S4 qualification result history drift")
    block_index = LIVE_HISTORY_IDS.index(history_id)
    evaluation = _mapping(payload.get("evaluation"), label="block evaluation")
    if (
        set(payload) != expected_fields
        or artifact.get("run_id") != f"s4q-result-{history_id}-001"
        or payload.get("schema_version") != RESULT_SCHEMA
        or payload.get("stage")
        != "S4_FIXED_THREE_SIDECAR_QUALIFICATION_BLOCK"
        or payload.get("block_index") != block_index
        or payload.get("expected_episode_count")
        != EXPECTED_EPISODE_COUNTS[history_id]
        or payload.get("verdict") != "PASS"
        or evaluation.get("verdict") != "PASS"
        or evaluation.get("failures") != []
        or evaluation.get("history_id") != history_id
        or evaluation.get("expected_episode_count")
        != EXPECTED_EPISODE_COUNTS[history_id]
        or evaluation.get("semantic_work_ratio") != 1.0
        or payload.get("paired_descriptive_status")
        != "NOT_EXECUTED_IN_CONSTRUCTION_CORRECTNESS_LANE"
        or payload.get("next_block_authorized")
        is not (block_index < len(LIVE_HISTORY_IDS) - 1)
        or payload.get("fixed_three_aggregation_authorized")
        is not (block_index == len(LIVE_HISTORY_IDS) - 1)
        or payload.get("s5_authorized") is not False
        or payload.get("pilot_execution_authorized") is not False
    ):
        raise ValueError("S4 qualification block result identity or gate drift")
    for field in expected_fields:
        if field.endswith("sha256"):
            _sha(payload.get(field), field=field)
    _reject_private(payload)
    return artifact


def verify_s4_sidecar_qualification_block_result_external(
    *,
    result: Mapping[str, Any],
    authority_file_sha256: str,
    authority_payload_sha256: str,
    consumption_file_sha256: str,
    capture_result: Mapping[str, Any],
    capture_result_file_sha256: str,
    replay_result: Mapping[str, Any],
    replay_result_file_sha256: str,
    candidate_sidecar_file_sha256: str,
) -> dict[str, Any]:
    """Rebuild a block result from phase files instead of trusting its verdict."""

    try:
        selected = verify_s4_sidecar_qualification_block_result(result)
        payload = selected["payload"]
        rebuilt = build_s4_sidecar_qualification_block_result(
            history_id=payload["history_id"],
            block_index=payload["block_index"],
            expected_episode_count=payload["expected_episode_count"],
            authority_file_sha256=authority_file_sha256,
            authority_payload_sha256=authority_payload_sha256,
            consumption_file_sha256=consumption_file_sha256,
            capture_result=capture_result,
            capture_result_file_sha256=capture_result_file_sha256,
            replay_result=replay_result,
            replay_result_file_sha256=replay_result_file_sha256,
            candidate_sidecar_file_sha256=candidate_sidecar_file_sha256,
            git_commit=str(selected["git_commit"]),
        )
    except Exception as error:
        raise ValueError(
            f"S4 qualification block external evidence failed: {type(error).__name__}"
        ) from None
    if rebuilt != selected:
        raise ValueError("S4 qualification block external evidence drift")
    return selected


def build_s4_sidecar_fixed_three_result(
    *,
    authority_file_sha256: str,
    authority_payload_sha256: str,
    consumption_file_sha256: str,
    activation_file_sha256: str,
    activation_payload_sha256: str,
    block_results: Sequence[Mapping[str, Any]],
    block_result_file_sha256: Mapping[str, str],
    git_commit: str,
) -> dict[str, Any]:
    """Aggregate exactly three strict block PASS artifacts without widening S5."""

    if isinstance(block_results, (str, bytes)) or len(block_results) != 3:
        raise ValueError("fixed-three aggregation requires three ordered strict PASS results")
    selected_results = [
        verify_s4_sidecar_qualification_block_result(value)
        for value in block_results
    ]
    if [value["payload"]["history_id"] for value in selected_results] != list(
        LIVE_HISTORY_IDS
    ):
        raise ValueError("fixed-three aggregation requires ordered strict PASS results")
    file_hashes = _mapping(
        block_result_file_sha256, label="fixed-three block result hashes"
    )
    if set(file_hashes) != set(LIVE_HISTORY_IDS):
        raise ValueError("fixed-three block result hash inventory drift")
    blocks = []
    for index, (history_id, result) in enumerate(
        zip(LIVE_HISTORY_IDS, selected_results, strict=True)
    ):
        payload = result["payload"]
        if (
            payload.get("block_index") != index
            or payload.get("verdict") != "PASS"
            or payload.get("s5_authorized") is not False
        ):
            raise ValueError("fixed-three aggregation requires ordered strict PASS results")
        blocks.append(
            {
                "history_id": history_id,
                "episode_count": EXPECTED_EPISODE_COUNTS[history_id],
                "file_sha256": _sha(
                    file_hashes[history_id], field=f"{history_id} result file"
                ),
                "payload_sha256": _sha(
                    result.get("payload_sha256"),
                    field=f"{history_id} result payload",
                ),
                "canonical_graph_parity": True,
                "semantic_work_ratio": 1.0,
            }
        )
    payload = {
        "schema_version": FIXED_THREE_RESULT_SCHEMA,
        "stage": "S4_FIXED_THREE_SIDECAR_QUALIFICATION",
        "verdict": "PASS",
        "authority_file_sha256": _sha(
            authority_file_sha256, field="authority file"
        ),
        "authority_payload_sha256": _sha(
            authority_payload_sha256, field="authority payload"
        ),
        "consumption_file_sha256": _sha(
            consumption_file_sha256, field="consumption file"
        ),
        "activation_file_sha256": _sha(
            activation_file_sha256, field="activation file"
        ),
        "activation_payload_sha256": _sha(
            activation_payload_sha256, field="activation payload"
        ),
        "completed_history_ids": list(LIVE_HISTORY_IDS),
        "completed_episode_count": sum(EXPECTED_EPISODE_COUNTS.values()),
        "blocks": blocks,
        "construction_correctness_status": "PASS",
        "paired_descriptive_status": (
            "NOT_EXECUTED_IN_CONSTRUCTION_CORRECTNESS_LANE"
        ),
        "full_s4_freeze_authorized": False,
        "s5_authorized": False,
        "pilot_execution_authorized": False,
    }
    return verify_s4_sidecar_fixed_three_result(
        finalize_envelope(
            payload=payload,
            protocol_version=PROTOCOL_VERSION,
            git_commit=str(git_commit),
            run_id="s4-fixed-three-sidecar-result-20260815-001",
        )
    )


def verify_s4_sidecar_fixed_three_result(
    value: Mapping[str, Any],
) -> dict[str, Any]:
    artifact = _envelope(value, label="S4 fixed-three result")
    payload = artifact["payload"]
    if (
        set(payload)
        != {
            "schema_version",
            "stage",
            "verdict",
            "authority_file_sha256",
            "authority_payload_sha256",
            "consumption_file_sha256",
            "activation_file_sha256",
            "activation_payload_sha256",
            "completed_history_ids",
            "completed_episode_count",
            "blocks",
            "construction_correctness_status",
            "paired_descriptive_status",
            "full_s4_freeze_authorized",
            "s5_authorized",
            "pilot_execution_authorized",
        }
        or artifact.get("run_id")
        != "s4-fixed-three-sidecar-result-20260815-001"
        or payload.get("schema_version") != FIXED_THREE_RESULT_SCHEMA
        or payload.get("stage") != "S4_FIXED_THREE_SIDECAR_QUALIFICATION"
        or payload.get("verdict") != "PASS"
        or payload.get("completed_history_ids") != list(LIVE_HISTORY_IDS)
        or payload.get("completed_episode_count")
        != sum(EXPECTED_EPISODE_COUNTS.values())
        or payload.get("construction_correctness_status") != "PASS"
        or payload.get("paired_descriptive_status")
        != "NOT_EXECUTED_IN_CONSTRUCTION_CORRECTNESS_LANE"
        or payload.get("full_s4_freeze_authorized") is not False
        or payload.get("s5_authorized") is not False
        or payload.get("pilot_execution_authorized") is not False
    ):
        raise ValueError("S4 fixed-three result identity or scope drift")
    for field in (
        "authority_file_sha256",
        "authority_payload_sha256",
        "consumption_file_sha256",
        "activation_file_sha256",
        "activation_payload_sha256",
    ):
        _sha(payload.get(field), field=field)
    blocks = payload.get("blocks")
    if not isinstance(blocks, list) or len(blocks) != len(LIVE_HISTORY_IDS):
        raise ValueError("S4 fixed-three result block inventory drift")
    for history_id, block in zip(LIVE_HISTORY_IDS, blocks, strict=True):
        selected = _mapping(block, label="fixed-three block projection")
        if selected != {
            "history_id": history_id,
            "episode_count": EXPECTED_EPISODE_COUNTS[history_id],
            "file_sha256": selected.get("file_sha256"),
            "payload_sha256": selected.get("payload_sha256"),
            "canonical_graph_parity": True,
            "semantic_work_ratio": 1.0,
        }:
            raise ValueError("S4 fixed-three block projection drift")
        _sha(selected.get("file_sha256"), field="block result file")
        _sha(selected.get("payload_sha256"), field="block result payload")
    _reject_private(payload)
    return artifact
