"""Provider-free tests for MEG validated-execution offline artifacts."""

from __future__ import annotations

import json
import sys
from pathlib import Path

from paper_eval.membind_v4.mseg.offline_validation import (
    build_offline_validation_documents,
)


PROJECT = Path(__file__).resolve().parents[1]
CAPTURE = (
    PROJECT
    / "artifacts/paper_eval/membind_v4/vdc_capture"
    / "membind-v31-opt-w4-vdc-capture-20260820-002"
)
GRAPHITI = (
    PROJECT.parent
    / "membind-validation/.venv/lib/python3.12/site-packages/graphiti_core"
)


def test_offline_documents_bind_real_replay_but_do_not_invent_shadow_hits() -> None:
    documents = build_offline_validation_documents(
        project_root=PROJECT,
        graphiti_root=GRAPHITI,
        capture_bundle_path=CAPTURE / "VDC_CAPTURE_BUNDLE.json",
        replay_verification_path=CAPTURE / "VDC_CAPTURE_REPLAY_VERIFICATION.json",
        provider_free_test_count=30,
    )
    oracle = documents["MEG_VALIDATION_SHADOW_ORACLE.json"]
    readview = documents["MEG_READVIEW_CAPTURE.json"]
    assert isinstance(oracle, dict)
    assert isinstance(readview, dict)
    assert oracle["status"] == "STOP_INSTRUMENTATION_FAILURE"
    assert oracle["bounded_real_capture_started"] is False
    assert oracle["validation_hit"] == oracle["validation_miss"] == 0
    assert oracle["offline_qualification"]["passive_replay_equivalence"] == "PASS"
    assert readview["historical_exact_node_readviews_projected"] == 12
    assert readview["shadow_probe_attempts"] == 0


def test_offline_cli_writes_only_new_artifact_names(tmp_path) -> None:
    sys.path.insert(0, str(PROJECT / "scripts"))
    from run_meg_validated_execution_offline import main

    output = tmp_path / "offline"
    assert main(["--output-root", str(output)]) == 0
    assert sorted(path.name for path in output.iterdir()) == [
        "MEG_OPERATOR_READINESS_AUDIT.json",
        "MEG_OPERATOR_READINESS_AUDIT.md",
        "MEG_READVIEW_CAPTURE.json",
        "MEG_READVIEW_CAPTURE.md",
        "MEG_VALIDATED_CONTINUATION_DECISION.md",
        "MEG_VALIDATION_SHADOW_ORACLE.json",
        "MEG_VALIDATION_SHADOW_ORACLE.md",
    ]
    oracle = json.loads(
        (output / "MEG_VALIDATION_SHADOW_ORACLE.json").read_text(encoding="utf-8")
    )
    assert oracle["correctness"] == {
        "live_services_started": 0,
        "publication_modifications": 0,
        "shadow_llm_calls": 0,
        "writes_from_shadow": 0,
    }
