"""Single-use authority for the three remaining S4 sidecar blocks."""

from __future__ import annotations

import json
import os
import re
from collections.abc import Mapping, Sequence
from copy import deepcopy
from pathlib import Path
from typing import Any

from . import PROTOCOL_VERSION
from .artifacts import finalize_envelope, payload_sha256
from .s4_qualification_plan import verify_s4_qualification_plan
from .s4_sidecar_qualification_data import (
    EXPECTED_EPISODE_COUNTS,
    LIVE_HISTORY_IDS,
)


AUTHORITY_SCHEMA = (
    "membind.paper-eval-v3.s4-sidecar-fixed-three-authority.v1"
)
CONSUMPTION_SCHEMA = (
    "membind.paper-eval-v3.s4-sidecar-fixed-three-consumption.v1"
)
AUTHORITY_RUN_ID = "s4-fixed-three-sidecar-authority-20260815-001"
AUTHORITY_SOURCE_NAMES = frozenset(
    {
        "authority",
        "candidate_oracle",
        "candidate_projection",
        "candidate_sidecar",
        "candidate_sidecar_runtime",
        "controller",
        "data",
        "production",
        "result",
        "runner",
        "test_authority",
        "test_controller",
        "test_data",
        "test_result",
    }
)
_SHA256 = re.compile(r"^[0-9a-f]{64}$")


def _mapping(value: object, *, label: str) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise ValueError(f"{label} must be a mapping")
    return deepcopy(dict(value))


def _sha(value: object, *, field: str) -> str:
    if not isinstance(value, str) or _SHA256.fullmatch(value) is None:
        raise ValueError(f"{field} is not a SHA256")
    return value


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
        "prompt_parts",
        "question",
        "raw_output",
        "raw_response",
        "secret",
        "uuid",
    }
    if isinstance(value, Mapping):
        for key, child in value.items():
            if str(key).casefold() in forbidden:
                raise ValueError("S4 qualification authority contains private data")
            _reject_private(child)
    elif isinstance(value, Sequence) and not isinstance(value, (str, bytes)):
        for child in value:
            _reject_private(child)


def _scope() -> dict[str, bool]:
    return {
        "single_use": True,
        "fixed_three_pipeline_authorized": True,
        "capture_before_replay": True,
        "sequential_blocks": True,
        "next_block_requires_prior_pass": True,
        "s5_authorized": False,
        "pilot_execution_authorized": False,
    }


def _verify_activation(
    value: Mapping[str, Any],
    *,
    plan: Mapping[str, Any],
    plan_file_sha256: str,
) -> dict[str, Any]:
    artifact = _mapping(value, label="S4 qualification activation")
    payload = _mapping(artifact.get("payload"), label="activation payload")
    projection = _mapping(
        payload.get("activated_projection"), label="activation projection"
    )
    if (
        artifact.get("protocol_version") != PROTOCOL_VERSION
        or artifact.get("status") != "finalized"
        or artifact.get("payload_sha256") != payload_sha256(payload)
        or artifact.get("run_id")
        != "s4-sidecar-qualification-activation-20260815-003"
        or payload.get("schema_version")
        != "membind.paper-eval-v3.s4-sidecar-qualification-activation.v3"
        or payload.get("stage") != "S4_QUALIFICATION_ACTIVATION"
        or payload.get("status")
        != "ACTIVATED_BY_VERIFIED_SIDECAR_SMOKE_PASS"
        or payload.get("qualification_plan", {}).get("file_sha256")
        != plan_file_sha256
        or payload.get("qualification_plan", {}).get("plan_sha256")
        != plan.get("plan_sha256")
        or projection
        != {
            "reused_smoke_history_id": "07741c45",
            "live_history_ids": list(LIVE_HISTORY_IDS),
            "live_blocks_sha256": payload_sha256(plan["blocks"][1:]),
            "sequential_blocks": True,
            "next_block_requires_prior_pass": True,
        }
        or payload.get("verified_smoke", {}).get("run_id")
        != "s4-d0-sidecar-smoke-result-20260815-008"
        or payload.get("verified_smoke", {}).get("verdict") != "PASS"
        or payload.get("verified_smoke", {}).get("history_id") != "07741c45"
        or payload.get("authority")
        != {
            "qualification_live_authorized": True,
            "s5_authorized": False,
            "pilot_execution_authorized": False,
        }
    ):
        raise ValueError("S4 qualification activation identity or scope drift")
    return artifact


def _history_bindings(value: object) -> list[dict[str, Any]]:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes)):
        raise ValueError("S4 qualification history bindings are malformed")
    selected = [_mapping(item, label="history binding") for item in value]
    if [item.get("history_id") for item in selected] != list(LIVE_HISTORY_IDS):
        raise ValueError("S4 qualification history binding order drift")
    result: list[dict[str, Any]] = []
    for history_id, item in zip(LIVE_HISTORY_IDS, selected, strict=True):
        if set(item) != {
            "history_id",
            "episode_count",
            "episode_manifest_sha256",
        } or item.get("episode_count") != EXPECTED_EPISODE_COUNTS[history_id]:
            raise ValueError("S4 qualification history binding drift")
        result.append(
            {
                "history_id": history_id,
                "episode_count": item["episode_count"],
                "episode_manifest_sha256": _sha(
                    item.get("episode_manifest_sha256"),
                    field="episode manifest",
                ),
            }
        )
    return result


def _private_cache(plan_block: Mapping[str, Any]) -> dict[str, Any]:
    private = _mapping(plan_block.get("private_cache"), label="plan private cache")
    cache_id = plan_block.get("cache_id")
    expected = {
        "prompt_relpath": f"runtime/private/{cache_id}/prompt.jsonl",
        "embedding_relpath": f"runtime/private/{cache_id}/embedding.jsonl",
        "reportable_contents": False,
    }
    if private != expected:
        raise ValueError("S4 qualification private cache drift")
    return {
        **private,
        "candidate_sidecar_relpath": (
            f"runtime/private/{cache_id}/candidate-sidecar.jsonl"
        ),
    }


def _blocks(
    plan: Mapping[str, Any], bindings: Sequence[Mapping[str, Any]]
) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for index, (history_id, plan_block, binding) in enumerate(
        zip(LIVE_HISTORY_IDS, plan["blocks"][1:], bindings, strict=True)
    ):
        if plan_block.get("history_id") != history_id:
            raise ValueError("S4 qualification plan block order drift")
        result.append(
            {
                "block_index": index,
                "history": {
                    "data_role": "DEVELOPMENT_EXPOSED",
                    **deepcopy(dict(binding)),
                },
                "plan_block": deepcopy(dict(plan_block)),
                "plan_block_sha256": payload_sha256(plan_block),
                "private_cache": _private_cache(plan_block),
            }
        )
    return result


def build_s4_sidecar_qualification_authority(
    *,
    qualification_plan: Mapping[str, Any],
    qualification_plan_file_sha256: str,
    activation: Mapping[str, Any],
    activation_file_sha256: str,
    dataset_file_sha256: str,
    split_file_sha256: str,
    history_bindings: Sequence[Mapping[str, Any]],
    source_sha256: Mapping[str, str],
    git_commit: str,
) -> dict[str, Any]:
    """Build, but do not persist, one exact fixed-three live authority."""

    plan = verify_s4_qualification_plan(qualification_plan)
    plan_file_sha = _sha(
        qualification_plan_file_sha256, field="qualification plan file"
    )
    selected_activation = _verify_activation(
        activation, plan=plan, plan_file_sha256=plan_file_sha
    )
    if split_file_sha256 != plan["input_file_sha256"]["split"]:
        raise ValueError("S4 qualification split file binding drift")
    bindings = _history_bindings(history_bindings)
    sources = _mapping(source_sha256, label="qualification authority sources")
    if set(sources) != AUTHORITY_SOURCE_NAMES:
        raise ValueError("S4 qualification authority source inventory drift")
    payload = {
        "schema_version": AUTHORITY_SCHEMA,
        "stage": "S4_FIXED_THREE_SIDECAR_QUALIFICATION",
        "status": "AUTHORIZED_SINGLE_USE",
        "qualification_plan": {
            "file_sha256": plan_file_sha,
            "plan_sha256": plan["plan_sha256"],
            "common_method_policy_sha256": plan[
                "common_method_policy_sha256"
            ],
        },
        "activation": {
            "file_sha256": _sha(
                activation_file_sha256, field="qualification activation file"
            ),
            "payload_sha256": _sha(
                selected_activation.get("payload_sha256"),
                field="qualification activation payload",
            ),
            "run_id": selected_activation["run_id"],
        },
        "dataset": {
            "file_sha256": _sha(dataset_file_sha256, field="dataset file"),
            "split_file_sha256": _sha(split_file_sha256, field="split file"),
        },
        "execution_order": list(LIVE_HISTORY_IDS),
        "blocks": _blocks(plan, bindings),
        "source_sha256": {
            name: _sha(digest, field=f"source {name}")
            for name, digest in sorted(sources.items())
        },
        "scope": _scope(),
    }
    return verify_s4_sidecar_qualification_authority(
        finalize_envelope(
            payload=payload,
            protocol_version=PROTOCOL_VERSION,
            git_commit=str(git_commit),
            run_id=AUTHORITY_RUN_ID,
        )
    )


def verify_s4_sidecar_qualification_authority(
    value: Mapping[str, Any],
) -> dict[str, Any]:
    artifact = _mapping(value, label="S4 qualification authority")
    if set(artifact) != {
        "protocol_version",
        "git_commit",
        "run_id",
        "status",
        "payload",
        "payload_sha256",
    }:
        raise ValueError("S4 qualification authority envelope shape drift")
    payload = _mapping(artifact.get("payload"), label="authority payload")
    if (
        artifact.get("protocol_version") != PROTOCOL_VERSION
        or artifact.get("run_id") != AUTHORITY_RUN_ID
        or artifact.get("status") != "finalized"
        or artifact.get("payload_sha256") != payload_sha256(payload)
        or set(payload)
        != {
            "schema_version",
            "stage",
            "status",
            "qualification_plan",
            "activation",
            "dataset",
            "execution_order",
            "blocks",
            "source_sha256",
            "scope",
        }
        or payload.get("schema_version") != AUTHORITY_SCHEMA
        or payload.get("stage") != "S4_FIXED_THREE_SIDECAR_QUALIFICATION"
        or payload.get("status") != "AUTHORIZED_SINGLE_USE"
        or payload.get("execution_order") != list(LIVE_HISTORY_IDS)
        or payload.get("scope") != _scope()
    ):
        raise ValueError("S4 qualification authority identity or scope drift")
    plan = _mapping(payload.get("qualification_plan"), label="authority plan")
    if set(plan) != {
        "file_sha256",
        "plan_sha256",
        "common_method_policy_sha256",
    }:
        raise ValueError("S4 qualification authority plan shape drift")
    activation = _mapping(payload.get("activation"), label="authority activation")
    if set(activation) != {"file_sha256", "payload_sha256", "run_id"} or activation.get(
        "run_id"
    ) != "s4-sidecar-qualification-activation-20260815-003":
        raise ValueError("S4 qualification activation binding drift")
    dataset = _mapping(payload.get("dataset"), label="authority dataset")
    if set(dataset) != {"file_sha256", "split_file_sha256"}:
        raise ValueError("S4 qualification dataset binding drift")
    for evidence in (plan, activation, dataset):
        for name, digest in evidence.items():
            if name != "run_id":
                _sha(digest, field=f"authority {name}")

    blocks = payload.get("blocks")
    if not isinstance(blocks, list) or len(blocks) != 3:
        raise ValueError("S4 qualification block inventory drift")
    bindings = []
    embedded_plan = {"blocks": [None]}
    for expected_index, (history_id, block) in enumerate(
        zip(LIVE_HISTORY_IDS, blocks, strict=True)
    ):
        selected = _mapping(block, label="authority block")
        history = _mapping(selected.get("history"), label="block history")
        plan_block = _mapping(selected.get("plan_block"), label="embedded plan block")
        if (
            set(selected)
            != {
                "block_index",
                "history",
                "plan_block",
                "plan_block_sha256",
                "private_cache",
            }
            or selected.get("block_index") != expected_index
            or history.get("data_role") != "DEVELOPMENT_EXPOSED"
            or history.get("history_id") != history_id
            or history.get("episode_count") != EXPECTED_EPISODE_COUNTS[history_id]
            or set(history)
            != {
                "data_role",
                "history_id",
                "episode_count",
                "episode_manifest_sha256",
            }
            or selected.get("plan_block_sha256") != payload_sha256(plan_block)
            or plan_block.get("history_id") != history_id
            or selected.get("private_cache") != _private_cache(plan_block)
        ):
            raise ValueError("S4 qualification block identity drift")
        _sha(history.get("episode_manifest_sha256"), field="episode manifest")
        bindings.append({key: history[key] for key in ("history_id", "episode_count", "episode_manifest_sha256")})
        embedded_plan["blocks"].append(plan_block)
    _history_bindings(bindings)
    sources = _mapping(payload.get("source_sha256"), label="authority sources")
    if set(sources) != AUTHORITY_SOURCE_NAMES:
        raise ValueError("S4 qualification authority source inventory drift")
    for name, digest in sources.items():
        _sha(digest, field=f"source {name}")
    _reject_private(payload)
    artifact["payload"] = payload
    return artifact


def _write_exclusive(path: Path, value: Mapping[str, Any]) -> None:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    encoded = (json.dumps(value, ensure_ascii=True, indent=2, sort_keys=True) + "\n").encode("ascii")
    descriptor = os.open(target, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o664)
    try:
        os.write(descriptor, encoded)
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def verify_s4_sidecar_qualification_authority_consumption(
    value: Mapping[str, Any],
) -> dict[str, Any]:
    artifact = _mapping(value, label="S4 qualification consumption")
    payload = _mapping(artifact.get("payload"), label="consumption payload")
    if (
        artifact.get("protocol_version") != PROTOCOL_VERSION
        or artifact.get("status") != "finalized"
        or artifact.get("payload_sha256") != payload_sha256(payload)
        or set(payload)
        != {
            "schema_version",
            "stage",
            "authority_file_sha256",
            "authority_payload_sha256",
            "consumed_action",
            "execution_order",
        }
        or payload.get("schema_version") != CONSUMPTION_SCHEMA
        or payload.get("stage") != "S4_FIXED_THREE_SIDECAR_QUALIFICATION"
        or payload.get("consumed_action")
        != "S4_FIXED_THREE_SIDECAR_QUALIFICATION_PIPELINE"
        or payload.get("execution_order") != list(LIVE_HISTORY_IDS)
    ):
        raise ValueError("S4 qualification consumption identity drift")
    _sha(payload.get("authority_file_sha256"), field="authority file")
    _sha(payload.get("authority_payload_sha256"), field="authority payload")
    _reject_private(payload)
    artifact["payload"] = payload
    return artifact


def consume_s4_sidecar_qualification_authority(
    *,
    authority: Mapping[str, Any],
    authority_file_sha256: str,
    output_path: Path,
    git_commit: str,
) -> dict[str, Any]:
    selected = verify_s4_sidecar_qualification_authority(authority)
    artifact = verify_s4_sidecar_qualification_authority_consumption(
        finalize_envelope(
            payload={
                "schema_version": CONSUMPTION_SCHEMA,
                "stage": "S4_FIXED_THREE_SIDECAR_QUALIFICATION",
                "authority_file_sha256": _sha(
                    authority_file_sha256, field="authority file"
                ),
                "authority_payload_sha256": selected["payload_sha256"],
                "consumed_action": (
                    "S4_FIXED_THREE_SIDECAR_QUALIFICATION_PIPELINE"
                ),
                "execution_order": list(LIVE_HISTORY_IDS),
            },
            protocol_version=PROTOCOL_VERSION,
            git_commit=str(git_commit),
            run_id="s4-fixed-three-sidecar-consumption-20260815-001",
        )
    )
    _write_exclusive(output_path, artifact)
    return artifact
