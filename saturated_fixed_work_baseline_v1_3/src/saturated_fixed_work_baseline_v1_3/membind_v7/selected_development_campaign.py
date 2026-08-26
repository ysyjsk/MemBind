"""Protocol loader for the candidate-selected V7 development replacement."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Mapping

from .development_campaign import verify_development_source_bindings
from .selected_development_runtime import (
    SELECTED_CONSTRUCTION_AUTHORITY,
    SELECTED_CONSTRUCTION_BASE_URL,
    SELECTED_CONSTRUCTION_MODEL,
    SELECTED_PROVIDER_IDENTITY_KIND,
    load_selected_development_runtime_freeze,
)


class SelectedDevelopmentCampaignError(RuntimeError):
    pass


_RUNTIME_FREEZE_SHA256 = (
    "132293be2a9f01abc6d2e3c5c6e1db2d5b8b17c2bb6f6ab4bc5910eb117ace80"
)
_METHODOLOGY_SHA256 = (
    "a3abb7e6ea481952ed868886bfd958bad9060812e42ca1eb3d96e46a1d77dd0a"
)
_METHOD_SELECTION_SHA256 = (
    "0a2958aeeedc2b7b8762247d5d6cf15252e2b6b737b5807a51a127461a632c53"
)
_DATASET_SHA256 = (
    "97fd80207f3419fc57c3684db824334224546d6bdd62c17ef52cd116eec9ffc8"
)


def _object(path: Path, *, label: str) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="ascii"))
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise SelectedDevelopmentCampaignError(f"{label} is unreadable") from error
    if not isinstance(value, dict):
        raise SelectedDevelopmentCampaignError(f"{label} is invalid")
    return value


def _mapping(value: Any, *, label: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise SelectedDevelopmentCampaignError(f"{label} is missing")
    return value


def _require(value: Any, expected: Any, *, label: str) -> None:
    if value != expected:
        raise SelectedDevelopmentCampaignError(
            f"selected development protocol drifted: {label}"
        )


def load_selected_development_protocol(
    path: str | Path, *, verify_references: bool = True
) -> dict[str, Any]:
    """Verify the 122B replacement before any external operation."""

    selected = Path(path).resolve()
    value = _object(selected, label="selected development protocol")
    for field, expected in {
        "schema_version": "membind.v7.selected-development-protocol.v1",
        "status": "FROZEN_BEFORE_SELECTED_R1_R3_REPLACEMENT",
        "campaign_scope": "TEMPORARY_PROVIDER_DEVELOPMENT",
        "provider_identity_kind": SELECTED_PROVIDER_IDENTITY_KIND,
        "formal_r1_r3_eligible": False,
        "live_treatment_authorized": False,
        "scientific_method_selection_update_allowed": False,
        "provider_swap_requires_new_formal_campaign": True,
        "old_read_return_allowed": False,
        "native_demand_skip_allowed": False,
        "repair_apply_allowed": False,
        "response_replay_allowed": False,
        "raw_request_persistence_allowed": False,
        "raw_response_persistence_allowed": False,
        "raw_embedding_persistence_allowed": False,
        "credential_persistence_allowed": False,
    }.items():
        _require(value.get(field), expected, label=field)
    runtime = _mapping(value.get("selected_runtime_freeze"), label="runtime freeze")
    _require(
        runtime.get("path"),
        "BAILIAN_122B_SILICONFLOW_DEVELOPMENT_RUNTIME_FREEZE.json",
        label="selected_runtime_freeze.path",
    )
    _require(
        runtime.get("sha256"),
        _RUNTIME_FREEZE_SHA256,
        label="selected_runtime_freeze.sha256",
    )
    construction = _mapping(value.get("construction"), label="construction identity")
    for field, expected in {
        "authority": SELECTED_CONSTRUCTION_AUTHORITY,
        "base_url": SELECTED_CONSTRUCTION_BASE_URL,
        "model": SELECTED_CONSTRUCTION_MODEL,
        "api_key_env": "DASHSCOPE_API_KEY",
        "structured_output_mode": "json_object",
        "response_validation": "pydantic-v2",
        "enable_thinking": False,
        "sdk_max_retries": 0,
        "hard_attempt_limit_per_request": 1,
    }.items():
        _require(construction.get(field), expected, label=f"construction.{field}")
    embedding = _mapping(value.get("embedding"), label="embedding identity")
    for field, expected in {
        "authority": "siliconflow-openai-compatible-v1",
        "base_url": "https://api.siliconflow.cn/v1",
        "model": "Qwen/Qwen3-Embedding-0.6B",
        "dimension": 1024,
        "api_key_env": "SILICONFLOW_API_KEY",
        "dimension_policy": "EXACT_NO_TRUNCATION",
        "sdk_max_retries": 0,
        "hard_attempt_limit_per_request": 1,
    }.items():
        _require(embedding.get(field), expected, label=f"embedding.{field}")
    if construction["authority"] == embedding["authority"]:
        raise SelectedDevelopmentCampaignError("selected provider identities were mixed")
    methodology = _mapping(
        value.get("source_methodology_protocol"), label="methodology protocol"
    )
    _require(methodology.get("path"), "R1_R3_PROTOCOL_FREEZE_V5.json", label="methodology.path")
    _require(methodology.get("sha256"), _METHODOLOGY_SHA256, label="methodology.sha256")
    scientific = _mapping(
        value.get("scientific_method_selection"), label="scientific selection"
    )
    for field, expected in {
        "path": "METHOD_SELECTION.json",
        "sha256": _METHOD_SELECTION_SHA256,
        "update_allowed": False,
    }.items():
        _require(scientific.get(field), expected, label=f"scientific.{field}")
    workload = _mapping(value.get("workload"), label="workload")
    for field, expected in {
        "dataset": "ai-hyz/MemoryAgentBench",
        "dataset_revision": "7ea066982b140a19337e17e60d45d4076e042faf",
        "local_file_sha256": _DATASET_SHA256,
    }.items():
        _require(workload.get(field), expected, label=f"workload.{field}")
    if workload.get("r1_r2") != {
        "context_index": 0,
        "source_start": 0,
        "source_count": 2,
    }:
        raise SelectedDevelopmentCampaignError("selected R1/R2 workload drifted")
    if workload.get("r3_blocks") != [
        {
            "block_id": "R3-A",
            "context_index": 1,
            "source_start": 0,
            "source_count": 6,
            "seed": 17,
        },
        {
            "block_id": "R3-B",
            "context_index": 2,
            "source_start": 0,
            "source_count": 6,
            "seed": 23,
        },
    ]:
        raise SelectedDevelopmentCampaignError("selected R3 workload drifted")
    if value.get("thresholds") != {
        "false_stable_max": 0,
        "false_unaffected_max": 0,
        "csp_min": 0.1,
        "sca_work_max": 4.0,
        "affected_fraction_max": 0.5,
        "reconvergence_min": 0.25,
        "required_headroom_floor_ns": 100_000_000,
        "required_headroom_ratio": 0.1,
        "stable_prediction_min": 1,
        "gross_saved_cp_min_ns": 1,
    }:
        raise SelectedDevelopmentCampaignError("selected thresholds drifted")
    if verify_references:
        v7_root = selected.parent
        verify_development_source_bindings(
            v7_root,
            {
                str(runtime["path"]): str(runtime["sha256"]),
                str(methodology["path"]): str(methodology["sha256"]),
                str(scientific["path"]): str(scientific["sha256"]),
            },
        )
        load_selected_development_runtime_freeze(v7_root / str(runtime["path"]))
        replacement = _mapping(
            value.get("replacement_authorization"),
            label="replacement authorization",
        )
        references = _mapping(
            replacement.get("reference_sha256"),
            label="replacement references",
        )
        verify_development_source_bindings(v7_root, references)
        harness = _mapping(value.get("observer_harness"), label="observer harness")
        if (
            harness.get("schema_version")
            != "membind.v7.selected-development-observer-harness.v1"
            or harness.get("status") != "PASS"
        ):
            raise SelectedDevelopmentCampaignError(
                "selected development observer harness is invalid"
            )
        sources = _mapping(
            harness.get("source_sha256"), label="campaign source hashes"
        )
        verify_development_source_bindings(selected.parents[2], sources)
    return value


__all__ = [
    "SelectedDevelopmentCampaignError",
    "load_selected_development_protocol",
]
