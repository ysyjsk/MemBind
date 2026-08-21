from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

from saturated_fixed_work_baseline_v1_3.simple_campaign import (
    SimplifiedCampaignError,
    build_execution_identity,
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
