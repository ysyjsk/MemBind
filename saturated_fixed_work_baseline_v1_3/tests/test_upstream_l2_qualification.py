from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[2]
SCRIPT = (
    ROOT
    / "saturated_fixed_work_baseline_v1_3"
    / "scripts"
    / "run_upstream_l2_qualification.py"
)


def _module():
    scripts = str(SCRIPT.parent)
    sys.path.insert(0, scripts)
    spec = importlib.util.spec_from_file_location("run_upstream_l2_qualification", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    try:
        spec.loader.exec_module(module)
    finally:
        sys.path.remove(scripts)
    return module


def test_l2_manifest_is_full_history_and_fixed_a_c_b_order(
    tmp_path: Path, monkeypatch
) -> None:
    module = _module()
    platform = tmp_path / "platform.json"
    platform.write_text(
        json.dumps(
            {
                "payload_sha256": "p" * 64,
                "profile_id": module.PROFILE_ID,
                "deployment_policy_id": module.DEPLOYMENT_POLICY.policy_id,
                "llm_model": {
                    "served_model": module.DEPLOYMENT_POLICY.served_model,
                    "revision": module.DEPLOYMENT_POLICY.revision,
                },
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(
        module,
        "implementation_bundle",
        lambda _runner: {"payload_sha256": "s" * 64},
    )
    manifest = module.build_manifest(tmp_path / "l2", platform)
    assert manifest["history_index"] == 0
    assert manifest["cell_count"] == 3
    assert tuple(manifest["fixed_order"]) == tuple(module.ARMS)
    assert [cell["arm"] for cell in manifest["cells"]] == list(module.ARMS)
    assert all(cell["replicate_id"] == 0 for cell in manifest["cells"])
    assert len({cell["attempt_id"] for cell in manifest["cells"]}) == 3
    assert len({cell["namespace"] for cell in manifest["cells"]}) == 3


def test_l2_manifest_checksum_and_identity_drift_fail_closed(
    tmp_path: Path, monkeypatch
) -> None:
    module = _module()
    platform = tmp_path / "platform.json"
    platform.write_text(
        json.dumps(
            {
                "payload_sha256": "p" * 64,
                "profile_id": module.PROFILE_ID,
                "deployment_policy_id": module.DEPLOYMENT_POLICY.policy_id,
                "llm_model": {
                    "served_model": module.DEPLOYMENT_POLICY.served_model,
                    "revision": module.DEPLOYMENT_POLICY.revision,
                },
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(
        module,
        "implementation_bundle",
        lambda _runner: {"payload_sha256": "s" * 64},
    )
    manifest = module.build_manifest(tmp_path / "l2", platform)
    manifest["cells"][0]["history_id"] = "drift"
    with pytest.raises(RuntimeError, match="identity drift"):
        module.validate_manifest(manifest)


def test_l2_measured_environment_binds_exact_cell_provenance() -> None:
    module = _module()
    cell = {
        "campaign_id": "qualification-campaign",
        "cell_id": "l2-h0-r0-native",
        "attempt_id": "attempt-1",
    }
    measured = module._qualification_env(
        {"MEMBIND_PROVENANCE_RUN_ID": "stale"},
        cell,
    )
    assert measured["MEMBIND_PROVENANCE_RUN_ID"] == (
        "qualification-campaign-l2-h0-r0-native-attempt-1"
    )


def test_formal_environment_binds_dual_replica_provider_and_seed(
    monkeypatch,
) -> None:
    monkeypatch.setenv("MEMBIND_DEPLOYMENT_POLICY_ID", "P2_QWEN3_14B_AWQ")
    monkeypatch.setenv(
        "MEMBIND_PROFILE_ID", "local-qwen3-14b-awq-dualreplica-v1"
    )
    monkeypatch.setenv("MEMBIND_LLM_MODEL_NAME", "qwen3-14b-awq")
    module = _module()
    env = module._formal_env()
    assert env["MAB_RUNTIME_PROVIDER"] == "LOCAL_DUAL_REPLICA"
    assert env["CONSTRUCTION_LLM_MODEL"] == "qwen3-14b-awq"
    assert env["CONSTRUCTION_SEED"] == "20260806"


def test_l2_source_has_no_old_method_or_runtime_imports() -> None:
    source = SCRIPT.read_text(encoding="utf-8")
    prohibited = (
        "SHARED_BOUNDED",
        "bounded_edge_tasks",
        "finite_edge_task",
        "structured_output_recovery",
        "run_mab_v61_8b.py",
    )
    assert all(value not in source for value in prohibited)
