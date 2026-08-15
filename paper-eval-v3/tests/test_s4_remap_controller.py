"""Offline TDD for the S4 candidate-remap retry controller."""

from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest

from paper_eval.artifacts import sha256_file
from paper_eval.s4_remap_controller import (
    _verify_authority_sources,
    build_remap_result_payload,
    safe_event_sink,
)


PROJECT = Path(__file__).resolve().parents[1]


def _evaluation() -> dict:
    return {
        "schema_version": "membind.paper-eval-v3.s4-d0-smoke-evaluation.v1",
        "verdict": "PASS",
        "failures": [],
        "canonical_graph_parity": True,
        "cache_mutation_during_replay": False,
        "candidate_remap_used": True,
        "candidate_remap_hit_count": 7,
        "candidate_oracle_resolution_accounting": True,
        "s4_four_history_qualification_authorized": True,
        "s5_authorized": False,
    }


def test_safe_event_sink_prints_error_code_but_not_private_fields(capsys) -> None:
    safe_event_sink(
        {
            "event_type": "failure",
            "source_sequence": 2,
            "error_class": "CandidateRemapError",
            "error_code": "CANDIDATE_MEMBERSHIP_DRIFT",
            "private": "must-not-print",
        }
    )

    value = json.loads(capsys.readouterr().out)
    assert value == {
        "event_type": "failure",
        "source_sequence": 2,
        "error_class": "CandidateRemapError",
        "error_code": "CANDIDATE_MEMBERSHIP_DRIFT",
    }


def test_result_payload_is_v2_and_does_not_authorize_later_live_work() -> None:
    payload = build_remap_result_payload(
        evaluation=_evaluation(),
        authority_file_sha256="1" * 64,
        authority_consumption_file_sha256="2" * 64,
        capture_result_file_sha256="3" * 64,
        replay_result_file_sha256="4" * 64,
    )

    assert payload["schema_version"] == (
        "membind.paper-eval-v3.s4-d0-remap-smoke-result.v2"
    )
    assert payload["verdict"] == "PASS"
    assert payload["evaluation"]["candidate_remap_hit_count"] == 7
    assert payload["authority"] == {
        "s4_four_history_qualification_authorized": True,
        "s5_authorized": False,
        "pilot_execution_authorized": False,
    }


@pytest.mark.parametrize(
    "mutate",
    [
        lambda value: value.update(verdict="FAIL"),
        lambda value: value.update(candidate_oracle_resolution_accounting=False),
        lambda value: value.update(cache_mutation_during_replay=True),
        lambda value: value.update(s5_authorized=True),
    ],
)
def test_result_payload_rejects_failed_or_incomplete_remap_gate(mutate) -> None:
    evaluation = _evaluation()
    mutate(evaluation)

    with pytest.raises(ValueError):
        build_remap_result_payload(
            evaluation=evaluation,
            authority_file_sha256="1" * 64,
            authority_consumption_file_sha256="2" * 64,
            capture_result_file_sha256="3" * 64,
            replay_result_file_sha256="4" * 64,
        )


def test_source_binding_includes_candidate_oracle_and_new_controller() -> None:
    source_sha = {
        "authority": sha256_file(PROJECT / "src/paper_eval/s4_remap_authority.py"),
        "candidate_oracle": sha256_file(
            PROJECT / "src/paper_eval/s4_candidate_oracle.py"
        ),
        "controller": sha256_file(
            PROJECT / "src/paper_eval/s4_remap_controller.py"
        ),
        "production": sha256_file(PROJECT / "src/paper_eval/s4_d0_production.py"),
        "runner": sha256_file(PROJECT / "src/paper_eval/s4_d0_runner.py"),
        "test": sha256_file(Path(__file__)),
    }
    authority = {"payload": {"source_sha256": source_sha}}

    _verify_authority_sources(authority)

    altered = copy.deepcopy(authority)
    altered["payload"]["source_sha256"]["candidate_oracle"] = "0" * 64
    with pytest.raises(RuntimeError, match="source binding"):
        _verify_authority_sources(altered)
