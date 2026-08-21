from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

import pytest

from saturated_fixed_work_baseline_v1_2.artifacts import AttemptStore, SealEvidence
from saturated_fixed_work_baseline_v1_2.contracts import ResumeIdentity
from saturated_fixed_work_baseline_v1_2.dataset import (
    EXPECTED_EPISODE_COUNTS,
    EXPECTED_SOURCE_TOKENS,
)
from saturated_fixed_work_baseline_v1_2.formal_run_seal import (
    FormalRunSealError,
    verify_formal_run_seal,
    write_formal_run_seal,
)
from saturated_fixed_work_baseline_v1_2.live import (
    FormalBlock,
    build_formal_plan,
    derive_cache_salt,
    derive_namespace,
)
from saturated_fixed_work_baseline_v1_2.schedules import Method


RUN_ID = "sfwb-v1-2-formal-seal-test"
RESOURCE_ID = "4" * 64


def _canonical_bytes(value: Any) -> bytes:
    return json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    ).encode("utf-8")


def _self_hashed(value: dict[str, Any]) -> dict[str, Any]:
    result = dict(value)
    result["payload_sha256"] = hashlib.sha256(_canonical_bytes(result)).hexdigest()
    return result


def _write_json(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, sort_keys=True) + "\n", encoding="utf-8")


def _identity(
    namespace: str,
    resource_id: str = RESOURCE_ID,
    cache_sha256: str = "6" * 64,
) -> ResumeIdentity:
    return ResumeIdentity(
        project_sha256="1" * 64,
        data_sha256="2" * 64,
        provider_sha256="3" * 64,
        resource_sha256=resource_id,
        config_sha256="5" * 64,
        cache_sha256=cache_sha256,
        namespace=namespace,
    )


def _protocol(root: Path) -> tuple[FormalBlock, ...]:
    plan = build_formal_plan(RUN_ID)
    _write_json(
        root / "protocol_manifest.json",
        {
            "run_id": RUN_ID,
            "selection_rule": "FIRST_VALID_ATTEMPT",
            "formal_order": [
                {
                    "ordinal": block.ordinal,
                    "block_id": block.block_id,
                    "history_id": block.history_id,
                    "method": block.method.value,
                    "attempt_ordinal": block.attempt_ordinal,
                    "namespace": block.namespace,
                    "cache_salt_sha256": hashlib.sha256(
                        block.cache_salt.encode("ascii")
                    ).hexdigest(),
                }
                for block in plan
            ],
        },
    )
    (root / "RESOURCE_ENVELOPE_ID").write_text(RESOURCE_ID + "\n", encoding="ascii")
    return plan


def _materialize_valid_attempt(
    root: Path,
    block: FormalBlock,
    *,
    attempt_ordinal: int = 1,
    resource_id: str = RESOURCE_ID,
) -> AttemptStore:
    namespace = derive_namespace(
        RUN_ID, block.method, block.history_id, attempt_ordinal=attempt_ordinal
    )
    cache_salt = derive_cache_salt(
        RUN_ID, block.block_id, attempt_ordinal=attempt_ordinal
    )
    cache_sha256 = hashlib.sha256(cache_salt.encode("ascii")).hexdigest()
    identity = _identity(namespace, resource_id, cache_sha256)
    store = AttemptStore.create(root / "blocks" / block.block_id, identity)
    store.append_event({"event": "BLOCK_STARTED", "source_sequence": None})
    seal = store.seal(
        SealEvidence(
            episode_task_count=EXPECTED_EPISODE_COUNTS[block.history_id],
            terminal_episode_task_count=EXPECTED_EPISODE_COUNTS[block.history_id],
            open_spans=0,
            open_requests=0,
            open_transactions=0,
            orphan_tasks=0,
            unobserved_exceptions=0,
            service_idle=True,
            canonical_snapshot_hashes=("a" * 64, "a" * 64),
        )
    )
    authority = _self_hashed(
        {
            "schema_version": "membind.saturated-fixed-work.live-authority.v1",
            "protocol_version": "SATURATED_FIXED_WORK_CONSTRUCTION_PROTOCOL_V1_2",
            "run_id": RUN_ID,
            "block_id": block.block_id,
            "method": block.method.value,
            "history_id": block.history_id,
            "namespace": namespace,
            "attempt_ordinal": attempt_ordinal,
            "cache_salt_sha256": cache_sha256,
            "resume_identity": identity.__dict__ if hasattr(identity, "__dict__") else {
                "project_sha256": identity.project_sha256,
                "data_sha256": identity.data_sha256,
                "provider_sha256": identity.provider_sha256,
                "resource_sha256": identity.resource_sha256,
                "config_sha256": identity.config_sha256,
                "cache_sha256": identity.cache_sha256,
                "namespace": identity.namespace,
            },
        }
    )
    _write_json(store.root / "live_authority.json", authority)
    graph = {
        "entities": [{"group_id": namespace, "name": block.history_id}],
        "edges": [],
        "episodes": [],
    }
    _write_json(store.root / "canonical_graph.json", graph)
    _write_json(
        store.root / "block_metrics.json",
        {
            "schema_version": "membind.saturated-fixed-work.block-result.v1",
            "block_id": block.block_id,
            "attempt_id": store.root.name,
            "attempt_ordinal": attempt_ordinal,
            "method": block.method.value,
            "history_id": block.history_id,
            "namespace": namespace,
            "valid": True,
            "episode_count": EXPECTED_EPISODE_COUNTS[block.history_id],
            "created_sequences": list(
                range(EXPECTED_EPISODE_COUNTS[block.history_id])
            ),
            "feeder_workload_await_count": (
                EXPECTED_EPISODE_COUNTS[block.history_id]
                if block.method is Method.B0_NATIVE_SERIAL
                else 0
            ),
            "application_gate_count": 0,
            "artificial_sleep_count": 0,
            "configured_max_inflight": None,
            "source_tokens": EXPECTED_SOURCE_TOKENS[block.history_id],
            "build_makespan_s": float(block.ordinal),
            "llm_input_tokens": 1000 + block.ordinal,
            "direct_semantic_violations": 0,
            "inversion_count": 0,
            "resource_envelope_id": resource_id,
            "resource_availability": "MEASURED",
            "canonical_graph_hash": hashlib.sha256(
                _canonical_bytes(graph)
            ).hexdigest(),
            "seal_payload_sha256": seal["payload_sha256"],
        },
    )
    return store


def _complete_run(root: Path, *, retry_first: bool = False) -> tuple[FormalBlock, ...]:
    plan = _protocol(root)
    for index, block in enumerate(plan):
        if retry_first and index == 0:
            failed = AttemptStore.create(
                root / "blocks" / block.block_id, _identity(block.namespace)
            )
            failed.record_failure("builtins.TimeoutError", {"stage": "construction"})
            _materialize_valid_attempt(root, block, attempt_ordinal=2)
        else:
            _materialize_valid_attempt(root, block)
    return plan


def test_formal_seal_selects_first_valid_attempt_and_derives_resource_conformance(
    tmp_path: Path,
) -> None:
    plan = _complete_run(tmp_path, retry_first=True)

    seal = write_formal_run_seal(tmp_path)

    assert seal["status"] == "FORMAL_RUN_SEALED"
    assert seal["valid_construction_blocks"] == 8
    assert seal["all_formal_blocks_share_one_resource_envelope"] is True
    assert seal["resource_envelope_id"] == RESOURCE_ID
    assert seal["selected_attempts"][0]["attempt_id"] == "attempt-002"
    assert seal["selected_attempts"][0]["namespace"] == derive_namespace(
        RUN_ID, plan[0].method, plan[0].history_id, attempt_ordinal=2
    )
    verified = verify_formal_run_seal(tmp_path)
    assert verified["verified"] is True
    assert len(verified["rows"]) == 8
    assert all(row["attempt_root"] for row in verified["rows"])
    with pytest.raises(FormalRunSealError, match="FORMAL_RUN_SEAL_ALREADY_EXISTS"):
        write_formal_run_seal(tmp_path)


def test_formal_seal_rejects_resource_drift_even_when_metrics_claim_valid(
    tmp_path: Path,
) -> None:
    plan = _protocol(tmp_path)
    for index, block in enumerate(plan):
        _materialize_valid_attempt(
            tmp_path,
            block,
            resource_id="9" * 64 if index == 7 else RESOURCE_ID,
        )
    with pytest.raises(FormalRunSealError, match="FORMAL_RESOURCE_ENVELOPE_MISMATCH"):
        write_formal_run_seal(tmp_path)
    assert not (tmp_path / "formal/formal_run_seal.json").exists()


def test_formal_seal_verification_detects_selected_artifact_tampering(
    tmp_path: Path,
) -> None:
    _complete_run(tmp_path)
    write_formal_run_seal(tmp_path)
    metrics = next((tmp_path / "blocks").glob("*/attempt-001/block_metrics.json"))
    metrics.write_text("{}\n", encoding="utf-8")
    with pytest.raises(FormalRunSealError, match="FORMAL_SELECTED_ARTIFACT_HASH_MISMATCH"):
        verify_formal_run_seal(tmp_path)
