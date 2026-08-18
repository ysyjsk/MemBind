"""TDD contracts for the one-history MemBind v3.1 feasibility gate."""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest

from paper_eval.artifacts import payload_sha256


SCRIPT = Path(__file__).resolve().parents[1] / "scripts/run_membind_v31_single_history.py"


def _module():
    spec = importlib.util.spec_from_file_location("single_history_runner", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _seal(body: dict[str, object], field: str = "payload_sha256") -> dict[str, object]:
    return {**body, field: payload_sha256(body)}


def test_smoke_gate_requires_same_plan_and_pass_status(tmp_path: Path) -> None:
    module = _module()
    body = {
        "schema_version": "membind.paper-eval-v3.membind-v31-smoke-gate.v1",
        "status": "PASS",
        "formal_blocks_authorized": True,
        "plan_payload_sha256": "a" * 64,
    }
    path = tmp_path / "gate.json"
    path.write_text(json.dumps(_seal(body)), encoding="utf-8")
    observed = module.verify_smoke_gate(path, plan_payload_sha256="a" * 64)
    assert observed["status"] == "PASS"
    with pytest.raises(module.SingleHistoryError, match="smoke_gate_binding_invalid"):
        module.verify_smoke_gate(path, plan_payload_sha256="b" * 64)


def test_cleanup_evidence_is_exact_and_zero_after_cleanup(tmp_path: Path) -> None:
    module = _module()
    body = {
        "namespace": "pev3-membind-v31-dev-test-membind-07741c45",
        "scope": "EXACT_GROUP_ID_ONLY",
        "global_cleanup_used": False,
        "post_cleanup_node_count": 0,
        "post_cleanup_relationship_count": 0,
    }
    path = tmp_path / "cleanup.json"
    path.write_text(json.dumps(_seal(body)), encoding="utf-8")
    assert module.verify_cleanup_evidence(path, namespace=body["namespace"])["scope"] == "EXACT_GROUP_ID_ONLY"
    bad = dict(body)
    bad["post_cleanup_node_count"] = 1
    path.write_text(json.dumps(_seal(bad)), encoding="utf-8")
    with pytest.raises(module.SingleHistoryError, match="cleanup_evidence_not_fresh"):
        module.verify_cleanup_evidence(path, namespace=body["namespace"])


def test_manifest_freezes_single_history_and_is_not_main_table_eligible() -> None:
    module = _module()
    block = {
        "method": "MemBind",
        "history_id": "07741c45",
        "source_count": 49,
        "namespace": "pev3-membind-v31-dev-test-membind-07741c45",
        "source_manifest_sha256": "a" * 64,
        "history_arrival_trace_sha256": "b" * 64,
        "shared_execution_envelope_sha256": "c" * 64,
        "compile_workers": 2,
        "lookahead": 2,
        "global_llm_admission_k": 2,
        "policy": "FRONTIER_FIRST_CACHE_AFFINITY",
    }
    plan = {"payload_sha256": "d" * 64, "blocks": [block]}
    gate = {"payload_sha256": "e" * 64}
    cleanup = {"payload_sha256": "f" * 64}
    result = module.build_manifest(attempt_id="membind-v31-feasibility-test-001", plan=plan, gate=gate, cleanup=cleanup)
    assert result["source_count"] == 49
    assert result["history_id"] == "07741c45"
    assert result["formal_main_table_eligible"] is False
    assert result["result_role"].endswith("NOT_FINAL_TABLE")
    assert result["payload_sha256"] == payload_sha256({k: v for k, v in result.items() if k != "payload_sha256"})


def test_existing_attempt_root_is_rejected_without_in_place_resume(monkeypatch, tmp_path: Path) -> None:
    module = _module()
    root = tmp_path / "attempt"
    root.mkdir()
    with pytest.raises(module.SingleHistoryError, match="method_plan_unreadable"):
        module.run_single_history(
            plan_path=tmp_path / "missing-plan.json",
            smoke_gate_path=tmp_path / "missing-gate.json",
            cleanup_evidence_path=tmp_path / "missing-cleanup.json",
            attempt_root=root,
            attempt_id="membind-v31-feasibility-test-002",
            hooks=object(),
        )

