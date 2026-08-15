"""Pure fixed-four-history plan for S4 D0 qualification after smoke PASS."""

from __future__ import annotations

import json
import os
import re
from collections.abc import Mapping, Sequence
from copy import deepcopy
from pathlib import Path
from typing import Any

from .artifacts import payload_sha256
from .s3_native_v2_freeze import verify_native_baseline_v2_freeze


SCHEMA = "membind.paper-eval-v3.s4-qualification-plan.v1"
HISTORY_IDS = ["07741c45", "b6019101", "6071bd76", "a2f3aa27"]
_SHA256 = re.compile(r"^[0-9a-f]{64}$")


def _mapping(value: object, *, label: str) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise ValueError(f"{label} must be a mapping")
    return deepcopy(dict(value))


def _sha(value: object, *, field: str) -> str:
    if not isinstance(value, str) or _SHA256.fullmatch(value) is None:
        raise ValueError(f"{field} is not a SHA256")
    return value


def _roles(value: Mapping[str, Any]) -> dict[str, list[str]]:
    artifact = _mapping(value, label="role registry")
    payload = _mapping(artifact.get("payload"), label="role registry payload")
    if (
        artifact.get("status") != "finalized"
        or artifact.get("payload_sha256") != payload_sha256(payload)
    ):
        raise ValueError("role registry hash drift")
    roles = _mapping(payload.get("roles"), label="role registry roles")
    expected = {"DEVELOPMENT_EXPOSED", "PILOT", "FINAL_PAPER_TEST"}
    if set(roles) != expected:
        raise ValueError("role registry inventory drift")
    result = {
        name: [str(item) for item in roles[name]]
        for name in sorted(roles)
    }
    flattened = [item for values in result.values() for item in values]
    if len(flattened) != len(set(flattened)):
        raise ValueError("role registry overlap")
    return result


def _live_block(history_id: str) -> dict[str, Any]:
    cache_id = f"s4q-d0-{history_id}-001"
    return {
        "history_id": history_id,
        "mode": "NEW_CAPTURE_REPLAY_BLOCK",
        "live_execution": False,
        "cache_id": cache_id,
        "runs": {
            "U0_CAPTURE": {
                "cache_id": cache_id,
                "method": "U0",
                "mode": "capture",
                "namespace": f"pev3-s4-u0-qual-{history_id}-001",
                "run_id": f"s4q-u0-{history_id}-001",
            },
            "D0_READ_ONLY_REPLAY": {
                "cache_id": cache_id,
                "method": "D0",
                "mode": "replay",
                "namespace": f"pev3-s4-d0-qual-{history_id}-001",
                "run_id": f"s4q-d0-{history_id}-001",
            },
        },
        "private_cache": {
            "prompt_relpath": f"runtime/private/{cache_id}/prompt.jsonl",
            "embedding_relpath": f"runtime/private/{cache_id}/embedding.jsonl",
            "reportable_contents": False,
        },
    }


def build_s4_qualification_plan(
    *,
    role_registry: Mapping[str, Any],
    role_registry_file_sha256: str,
    split: Mapping[str, Any],
    split_file_sha256: str,
    s3_freeze: Mapping[str, Any],
    s3_freeze_file_sha256: str,
    s4_workplan_sha256: str,
    source_sha256: Mapping[str, str],
) -> dict[str, Any]:
    roles = _roles(role_registry)
    split_value = _mapping(split, label="frozen split")
    selected_ids = split_value.get("calibration_question_ids")
    if not isinstance(selected_ids, list) or len(selected_ids) != 4:
        raise ValueError("S4 qualification requires exactly four calibration histories")
    selected_ids = [str(item) for item in selected_ids]
    if selected_ids != HISTORY_IDS:
        raise ValueError("S4 qualification four-history order drift")
    if not set(selected_ids).issubset(set(roles["DEVELOPMENT_EXPOSED"])):
        raise ValueError("S4 qualification history is not DEVELOPMENT_EXPOSED")
    if set(selected_ids) & set(roles["PILOT"] + roles["FINAL_PAPER_TEST"]):
        raise ValueError("S4 qualification overlaps held-out roles")
    freeze = verify_native_baseline_v2_freeze(s3_freeze)
    bindings = freeze["payload"]["method_policy_bindings"]
    if set(bindings) != {"U0", "A0", "P*", "M*"} or len(set(bindings.values())) != 1:
        raise ValueError("S4 qualification common method policy drift")
    common_policy = next(iter(bindings.values()))
    sources = _mapping(source_sha256, label="qualification plan sources")
    if set(sources) != {"plan", "test"}:
        raise ValueError("qualification plan source inventory drift")

    body = {
        "schema_version": SCHEMA,
        "stage": "S4_QUALIFICATION_OFFLINE_PLAN",
        "history_ids": selected_ids,
        "input_file_sha256": {
            "role_registry": _sha(
                role_registry_file_sha256, field="role registry file"
            ),
            "split": _sha(split_file_sha256, field="split file"),
            "s3_freeze": _sha(s3_freeze_file_sha256, field="S3 freeze file"),
            "s4_workplan": _sha(s4_workplan_sha256, field="S4 workplan"),
        },
        "common_method_policy_sha256": common_policy,
        "blocks": [
            {
                "history_id": HISTORY_IDS[0],
                "mode": "REUSE_SEALED_S4_SMOKE_PASS",
                "required_artifact": (
                    "artifacts/paper_eval/native/S4_D0_SMOKE_RESULT.json"
                ),
                "live_execution": False,
            },
            *[_live_block(history_id) for history_id in HISTORY_IDS[1:]],
        ],
        "execution_policy": {
            "sequential_blocks": True,
            "next_block_requires_prior_pass": True,
            "new_namespaces_before_authority": False,
            "checkpoint_each_publication": True,
            "private_oracle_contents_reportable": False,
        },
        "hard_gates": {
            "episode_source_coverage": "EXACT_100_PERCENT",
            "oracle_miss_or_fallback": 0,
            "cross_encoder_calls": 0,
            "canonical_graph_parity": "EXACT_100_PERCENT",
            "llm_call_count_contract": "CAPTURE_EQUALS_REPLAY_RESOLVED",
            "hidden_semantic_fallback": False,
        },
        "work_volume_guardrail": {
            "interval": [0.95, 1.05],
            "status": "PROJECT_SPECIFIC_FAIRNESS_GUARDRAIL_NOT_FIELD_STANDARD",
        },
        "fixed_common_evaluation": {
            "dataset_unchanged": True,
            "retrieval": "Graphiti Episode BM25/RRF",
            "top_k": 10,
            "reader": "Native Reader-v2 JSON USERONLY=false con max_tokens=800",
            "judge_unchanged": True,
        },
        "quality_interpretation": {
            "retrieval_and_qa": "PAIRED_DESCRIPTIVE_ONLY",
            "qa_hard_gate": False,
            "d0_minus_u0_one_pp_rule": False,
        },
        "source_sha256": {
            name: _sha(value, field=f"source {name}")
            for name, value in sorted(sources.items())
        },
        "authority": {
            "qualification_live_authorized": False,
            "s5_authorized": False,
            "pilot_execution_authorized": False,
        },
    }
    return verify_s4_qualification_plan(
        {**body, "plan_sha256": payload_sha256(body)}
    )


def verify_s4_qualification_plan(value: Mapping[str, Any]) -> dict[str, Any]:
    plan = _mapping(value, label="S4 qualification plan")
    stored = plan.pop("plan_sha256", None)
    expected_fields = {
        "schema_version",
        "stage",
        "history_ids",
        "input_file_sha256",
        "common_method_policy_sha256",
        "blocks",
        "execution_policy",
        "hard_gates",
        "work_volume_guardrail",
        "fixed_common_evaluation",
        "quality_interpretation",
        "source_sha256",
        "authority",
    }
    if set(plan) != expected_fields or stored != payload_sha256(plan):
        raise ValueError("S4 qualification plan shape or hash drift")
    if (
        plan.get("schema_version") != SCHEMA
        or plan.get("stage") != "S4_QUALIFICATION_OFFLINE_PLAN"
        or plan.get("history_ids") != HISTORY_IDS
    ):
        raise ValueError("S4 qualification plan identity drift")
    for value_sha in _mapping(
        plan.get("input_file_sha256"), label="qualification inputs"
    ).values():
        _sha(value_sha, field="qualification input")
    _sha(plan.get("common_method_policy_sha256"), field="common policy")
    blocks = plan.get("blocks")
    if not isinstance(blocks, Sequence) or isinstance(blocks, (str, bytes)) or len(blocks) != 4:
        raise ValueError("S4 qualification block inventory drift")
    expected_first = {
        "history_id": HISTORY_IDS[0],
        "mode": "REUSE_SEALED_S4_SMOKE_PASS",
        "required_artifact": "artifacts/paper_eval/native/S4_D0_SMOKE_RESULT.json",
        "live_execution": False,
    }
    if blocks[0] != expected_first:
        raise ValueError("S4 qualification smoke reuse drift")
    for history_id, block in zip(HISTORY_IDS[1:], blocks[1:], strict=True):
        if block != _live_block(history_id):
            raise ValueError("S4 qualification live block identity drift")
    if plan.get("execution_policy") != {
        "sequential_blocks": True,
        "next_block_requires_prior_pass": True,
        "new_namespaces_before_authority": False,
        "checkpoint_each_publication": True,
        "private_oracle_contents_reportable": False,
    }:
        raise ValueError("S4 qualification execution policy drift")
    if plan.get("hard_gates") != {
        "episode_source_coverage": "EXACT_100_PERCENT",
        "oracle_miss_or_fallback": 0,
        "cross_encoder_calls": 0,
        "canonical_graph_parity": "EXACT_100_PERCENT",
        "llm_call_count_contract": "CAPTURE_EQUALS_REPLAY_RESOLVED",
        "hidden_semantic_fallback": False,
    }:
        raise ValueError("S4 qualification hard-gate drift")
    if plan.get("work_volume_guardrail") != {
        "interval": [0.95, 1.05],
        "status": "PROJECT_SPECIFIC_FAIRNESS_GUARDRAIL_NOT_FIELD_STANDARD",
    }:
        raise ValueError("S4 qualification work guardrail drift")
    if plan.get("fixed_common_evaluation") != {
        "dataset_unchanged": True,
        "retrieval": "Graphiti Episode BM25/RRF",
        "top_k": 10,
        "reader": "Native Reader-v2 JSON USERONLY=false con max_tokens=800",
        "judge_unchanged": True,
    }:
        raise ValueError("S4 qualification common evaluation drift")
    if plan.get("quality_interpretation") != {
        "retrieval_and_qa": "PAIRED_DESCRIPTIVE_ONLY",
        "qa_hard_gate": False,
        "d0_minus_u0_one_pp_rule": False,
    }:
        raise ValueError("S4 qualification quality interpretation drift")
    sources = _mapping(plan.get("source_sha256"), label="qualification sources")
    if set(sources) != {"plan", "test"}:
        raise ValueError("S4 qualification source inventory drift")
    for name, source_sha in sources.items():
        _sha(source_sha, field=f"source {name}")
    if plan.get("authority") != {
        "qualification_live_authorized": False,
        "s5_authorized": False,
        "pilot_execution_authorized": False,
    }:
        raise ValueError("S4 qualification authority drift")
    return {**plan, "plan_sha256": stored}


def finalize_s4_qualification_plan(
    *, path: Path, plan: Mapping[str, Any]
) -> dict[str, Any]:
    verified = verify_s4_qualification_plan(plan)
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    encoded = (
        json.dumps(verified, ensure_ascii=True, indent=2, sort_keys=True) + "\n"
    ).encode("ascii")
    descriptor = os.open(target, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o664)
    try:
        os.write(descriptor, encoded)
        os.fsync(descriptor)
    finally:
        os.close(descriptor)
    return verified
