"""TDD contracts for the exclusive public M* terminal result."""

from __future__ import annotations

import copy
from pathlib import Path

import pytest

from paper_eval.artifacts import payload_sha256
from paper_eval.s5_mstar_result_finalizer import (
    S5MStarFinalizerError,
    build_s5_mstar_result,
    finalize_s5_mstar_result,
    verify_s5_mstar_result,
)


def _projection(*, outcome: str = "PASS") -> dict[str, object]:
    violations = 0 if outcome == "PASS" else 2
    return {
        "run_id": "s5-mstar-20260816-202",
        "execution_identity_sha256": "1" * 64,
        "source_manifest_sha256": "2" * 64,
        "production_identity_sha256": "3" * 64,
        "production_core_identity_sha256": "4" * 64,
        "predecessor": {
            "method": "P*",
            "verdict": "SCIENTIFIC_OUTCOME_COMPLETE",
            "result_file_sha256": "5" * 64,
            "result_payload_sha256": "6" * 64,
        },
        "smoke_summary": {
            "status": "PASS",
            "method": "M*",
            "episode_count": 49,
            "coverage": 1.0,
            "lost_count": 0,
            "duplicate_count": 0,
            "worker_count": 2,
            "publication_order": list(range(49)),
            "whole_update_overlap_observed": None,
            "fallback_count": 0,
            "direct_invariant_violation_count": violations,
            "scientific_outcome_not_adapter_failure": False,
            "post_return_stale_window_ns": [0] * 49,
        },
        "post_observation": {
            "status": outcome,
            "global_violation_total": violations,
            "native_observation_sha256": "7" * 64,
            "post_observation_sha256": "8" * 64,
        },
        "publication_journal": {
            "intent_count": 49,
            "commit_count": 49,
            "publication_count": 49,
            "recovered_publication_count": 0,
            "events_sha256": "9" * 64,
        },
        "bindings": {
            name: f"{index + 10:064x}"
            for index, name in enumerate(
                (
                    "production_identity_file_sha256",
                    "production_core_identity_file_sha256",
                    "fx0_qualification_file_sha256",
                    "production_identity_qualification_file_sha256",
                    "current_stage_pointer_file_sha256",
                    "preflight_file_sha256",
                    "authority_file_sha256",
                    "predecessor_file_sha256",
                    "consumption_file_sha256",
                    "controller_events_file_sha256",
                    "controller_checkpoint_file_sha256",
                    "attempt_manifest_file_sha256",
                    "attempt_events_file_sha256",
                    "attempt_checkpoint_file_sha256",
                    "attempt_result_file_sha256",
                    "publication_journal_file_sha256",
                    "post_observation_file_sha256",
                )
            )
        },
    }


def test_pass_result_requires_complete_publication_and_zero_invariants() -> None:
    checked = verify_s5_mstar_result(
        build_s5_mstar_result(projection=_projection(), git_commit="deadbeef")
    )
    payload = checked["payload"]
    assert payload["verdict"] == "PASS"
    assert payload["publication_journal"]["publication_count"] == 49
    assert payload["authority"]["scientific_pass_authorized"] is True
    assert payload["authority"]["pilot_execution_authorized"] is False


def test_valid_invariant_counterexample_is_terminal_but_never_pass_authority() -> None:
    checked = verify_s5_mstar_result(
        build_s5_mstar_result(
            projection=_projection(outcome="DIRECT_INVARIANT_VIOLATION_OBSERVED"),
            git_commit="deadbeef",
        )
    )
    payload = checked["payload"]
    assert payload["verdict"] == "SCIENTIFIC_OUTCOME_COMPLETE"
    assert payload["scientific_outcome"] == "DIRECT_INVARIANT_VIOLATION_OBSERVED"
    assert payload["authority"]["scientific_pass_authorized"] is False


def test_result_verifier_rejects_resealed_publication_or_summary_drift() -> None:
    artifact = build_s5_mstar_result(projection=_projection(), git_commit="deadbeef")
    tampered = copy.deepcopy(artifact)
    tampered["payload"]["publication_journal"]["publication_count"] = 48
    tampered["payload_sha256"] = payload_sha256(tampered["payload"])
    with pytest.raises(S5MStarFinalizerError):
        verify_s5_mstar_result(tampered)


def test_final_write_is_exclusive(tmp_path: Path) -> None:
    output = tmp_path / "S5_MSTAR_RESULT.json"
    finalize_s5_mstar_result(
        output_path=output, projection=_projection(), git_commit="deadbeef"
    )
    with pytest.raises(S5MStarFinalizerError, match="result_exists"):
        finalize_s5_mstar_result(
            output_path=output, projection=_projection(), git_commit="deadbeef"
        )

