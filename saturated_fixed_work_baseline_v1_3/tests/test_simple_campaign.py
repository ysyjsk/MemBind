from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

from saturated_fixed_work_baseline_v1_3.simple_campaign import (
    QUALIFICATION_BLOCK_IDS,
    SimplifiedCampaignError,
    build_execution_identity,
    load_existing_baseline_reference,
    validate_simplified_preflight,
)


def test_simplified_preflight_has_only_execution_gates() -> None:
    evidence = {
        "construction_endpoint": True,
        "embedding_endpoint": True,
        "neo4j": True,
        "workload": True,
        "runner": True,
        "instrumentation": True,
        "warmup": True,
        "idle": True,
    }

    result = validate_simplified_preflight(evidence)

    assert result["status"] == "PASS"
    assert result["formal_run_authorized"] is True
    assert result["required_gates"] == tuple(evidence)


def test_simplified_preflight_rejects_only_core_failures() -> None:
    evidence = {
        "construction_endpoint": True,
        "embedding_endpoint": True,
        "neo4j": True,
        "workload": True,
        "runner": False,
        "instrumentation": True,
        "warmup": True,
        "idle": True,
    }

    with pytest.raises(SimplifiedCampaignError, match="RUNNER_UNAVAILABLE"):
        validate_simplified_preflight(evidence)


def test_execution_identity_is_derived_without_resource_inputs(tmp_path: Path) -> None:
    identity = build_execution_identity(
        run_id="sfwb-v1-3-simple-test",
        repository_root=tmp_path,
        workload_sha256="a" * 64,
        namespace="simple-namespace",
    )

    expected = hashlib.sha256(
        b"sfwb-v1-3-simple-test\0" + b"a" * 64 + b"\0simple-namespace"
    ).hexdigest()
    assert identity.namespace == "simple-namespace"
    assert identity.execution_sha256 == expected
    assert not hasattr(identity, "gpu_uuid")
    assert not hasattr(identity, "resource_envelope_id")


def test_qualification_adds_membind_after_the_existing_baselines() -> None:
    assert QUALIFICATION_BLOCK_IDS == (
        "qualification-b0-a",
        "qualification-b0-b",
        "qualification-b1",
        "qualification-membind",
    )


def test_existing_baseline_can_be_reused_without_rerunning_it() -> None:
    repository_root = Path(__file__).resolve().parents[2]
    baseline_root = (
        repository_root
        / "saturated_fixed_work_baseline_v1_3/artifacts/"
        "sfwb-v1-3-simple-20260821-004"
    )

    reference = load_existing_baseline_reference(baseline_root)

    assert reference.run_id == "sfwb-v1-3-simple-20260821-004"
    assert reference.block_ids == QUALIFICATION_BLOCK_IDS[:3]
    assert len(reference.source_sha256s) == 12
    assert reference.source_tokens == 24610
    assert reference.b0_namespace.endswith("attempt-001")
    assert reference.b0_canonical_graph.is_file()
