"""Offline wiring tests for the retry-006 bilateral-sidecar controller."""

from __future__ import annotations

import copy
import hashlib
from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace

import pytest

from paper_eval.s4_candidate_projection import PROJECTION_SCHEMA_SHA256
from paper_eval.s4_sidecar_controller import (
    _sidecar_config,
    _verify_authority_sources,
    build_sidecar_result_payload,
    safe_event_sink,
)


PROJECT = Path(__file__).resolve().parents[1]


@dataclass(frozen=True)
class Episode:
    source_sequence: int
    source_hash: str
    name: str
    body: str


def _sha(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def _episodes() -> list[Episode]:
    return [
        Episode(index, _sha(f"source-{index}"), f"episode-{index}", f"body-{index}")
        for index in range(49)
    ]


def _authority(tmp_path: Path, *, attempt: str = "006") -> dict:
    root = tmp_path / f"runtime/private/cache-{attempt}"
    return {
        "payload": {
            "history": {
                "history_id": "07741c45",
                "episode_count": 49,
                "data_role": "DEVELOPMENT_EXPOSED",
            },
            "projection_schema_sha256": PROJECTION_SCHEMA_SHA256,
            "private_cache": {
                "prompt_relpath": f"runtime/private/cache-{attempt}/prompt.jsonl",
                "embedding_relpath": f"runtime/private/cache-{attempt}/embedding.jsonl",
                "candidate_sidecar_relpath": (
                    f"runtime/private/cache-{attempt}/candidate-sidecar.jsonl"
                ),
                "reportable_contents": False,
            },
            "runs": {
                "U0_CAPTURE": {
                    "cache_id": f"cache-{attempt}",
                    "method": "U0",
                    "mode": "capture",
                    "namespace": f"pev3-s4-u0-capture-20260815-{attempt}",
                    "run_id": f"s4-d0-capture-20260815-{attempt}",
                },
                "D0_READ_ONLY_REPLAY": {
                    "cache_id": f"cache-{attempt}",
                    "method": "D0",
                    "mode": "replay",
                    "namespace": f"pev3-s4-d0-replay-20260815-{attempt}",
                    "run_id": f"s4-d0-replay-20260815-{attempt}",
                },
            },
        },
        "project_root": tmp_path,
        "expected_sidecar": root / "candidate-sidecar.jsonl",
    }


def _evaluation() -> dict:
    return {
        "schema_version": "membind.paper-eval-v3.s4-sidecar-smoke-evaluation.v1",
        "verdict": "PASS",
        "failures": [],
        "canonical_graph_parity": True,
        "cache_and_sidecar_mutation_during_replay": False,
        "sidecar_record_count": 4,
        "replay_sidecar_record_count": 4,
        "sidecar_consumption_exact": True,
        "edge_sidecar_resolution_accounting": True,
        "s4_four_history_qualification_authorized": True,
        "s5_authorized": False,
        "pilot_execution_authorized": False,
    }


def test_sidecar_config_binds_manifest_schema_namespace_and_private_path(
    tmp_path: Path,
) -> None:
    authority = _authority(tmp_path)
    payload = authority["payload"]
    spec = {
        **payload["runs"]["U0_CAPTURE"],
        "phase": "U0_CAPTURE",
    }
    module = SimpleNamespace(resolve_extracted_edge=lambda: None)

    config = _sidecar_config(
        authority=authority,
        spec=spec,
        episodes=_episodes(),
        edge_operations_module=module,
        project_root=tmp_path,
    )

    assert config.path == authority["expected_sidecar"]
    assert config.namespace == spec["namespace"]
    assert config.identity["attempt_id"] == "006"
    assert config.identity["history_id"] == "07741c45"
    assert config.identity["cache_id"] == "cache-006"
    assert config.identity["projection_schema_sha256"] == PROJECTION_SCHEMA_SHA256
    assert len(config.identity["episode_manifest_sha256"]) == 64

    escaped = copy.deepcopy(authority)
    escaped["payload"]["private_cache"]["candidate_sidecar_relpath"] = (
        "../outside.jsonl"
    )
    with pytest.raises(ValueError, match="escaped"):
        _sidecar_config(
            authority=escaped,
            spec=spec,
            episodes=_episodes(),
            edge_operations_module=module,
            project_root=tmp_path,
        )


def test_sidecar_config_derives_retry_007_attempt_from_frozen_run_identity(
    tmp_path: Path,
) -> None:
    authority = _authority(tmp_path, attempt="007")
    spec = {
        **authority["payload"]["runs"]["U0_CAPTURE"],
        "phase": "U0_CAPTURE",
    }

    config = _sidecar_config(
        authority=authority,
        spec=spec,
        episodes=_episodes(),
        edge_operations_module=SimpleNamespace(resolve_extracted_edge=lambda: None),
        project_root=tmp_path,
    )

    assert config.identity["attempt_id"] == "007"
    assert config.identity["cache_id"] == "cache-007"


def test_result_payload_requires_complete_sidecar_pass() -> None:
    payload = build_sidecar_result_payload(
        evaluation=_evaluation(),
        authority_file_sha256="1" * 64,
        authority_consumption_file_sha256="2" * 64,
        capture_result_file_sha256="3" * 64,
        replay_result_file_sha256="4" * 64,
        candidate_sidecar_file_sha256="5" * 64,
    )

    assert payload["schema_version"] == (
        "membind.paper-eval-v3.s4-d0-sidecar-smoke-result.v3"
    )
    assert payload["verdict"] == "PASS"
    assert payload["authority"]["s4_four_history_qualification_authorized"] is True
    assert payload["authority"]["s5_authorized"] is False

    failed = _evaluation()
    failed["sidecar_consumption_exact"] = False
    with pytest.raises(ValueError, match="PASS"):
        build_sidecar_result_payload(
            evaluation=failed,
            authority_file_sha256="1" * 64,
            authority_consumption_file_sha256="2" * 64,
            capture_result_file_sha256="3" * 64,
            replay_result_file_sha256="4" * 64,
            candidate_sidecar_file_sha256="5" * 64,
        )


def test_safe_event_sink_and_source_binding_are_sanitized(capsys) -> None:
    safe_event_sink(
        {
            "event_type": "failure",
            "source_sequence": 7,
            "error_class": "CandidateSidecarError",
            "error_code": "CANDIDATE_MEMBERSHIP_DRIFT",
            "private": "must-not-print",
        }
    )
    output = capsys.readouterr().out
    assert "private" not in output
    assert "CANDIDATE_MEMBERSHIP_DRIFT" in output

    sources = {
        name: __import__("paper_eval.artifacts", fromlist=["sha256_file"]).sha256_file(
            PROJECT / path
        )
        for name, path in {
            "authority": "src/paper_eval/s4_sidecar_authority.py",
            "candidate_oracle": "src/paper_eval/s4_candidate_oracle.py",
            "candidate_projection": "src/paper_eval/s4_candidate_projection.py",
            "candidate_sidecar": "src/paper_eval/s4_candidate_sidecar.py",
            "candidate_sidecar_runtime": (
                "src/paper_eval/s4_candidate_sidecar_runtime.py"
            ),
            "controller": "src/paper_eval/s4_sidecar_controller.py",
            "production": "src/paper_eval/s4_d0_production.py",
            "result": "src/paper_eval/s4_sidecar_result.py",
            "runner": "src/paper_eval/s4_d0_runner.py",
            "test": "tests/test_s4_sidecar_controller.py",
        }.items()
    }
    _verify_authority_sources(
        {
            "payload": {
                "runs": _authority(PROJECT)["payload"]["runs"],
                "source_sha256": sources,
            }
        }
    )

    retry_007 = _authority(PROJECT, attempt="007")
    retry_007["payload"]["source_sha256"] = {
        **sources,
        "edge_identity": __import__(
            "paper_eval.artifacts", fromlist=["sha256_file"]
        ).sha256_file(PROJECT / "src/paper_eval/s4_edge_identity_diagnosis.py"),
    }
    _verify_authority_sources(retry_007)

    sources["candidate_sidecar"] = "0" * 64
    with pytest.raises(RuntimeError, match="source binding"):
        _verify_authority_sources(
            {
                "payload": {
                    "runs": _authority(PROJECT)["payload"]["runs"],
                    "source_sha256": sources,
                }
            }
        )
