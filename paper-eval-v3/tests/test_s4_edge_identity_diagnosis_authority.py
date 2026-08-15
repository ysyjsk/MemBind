"""Offline authority tests for the single retry-005 read-only diagnosis."""

from __future__ import annotations

import copy
from pathlib import Path

import pytest

from paper_eval.s4_edge_identity_diagnosis_authority import (
    build_diagnosis_authority,
    consume_diagnosis_authority,
    verify_diagnosis_authority,
    verify_diagnosis_authority_consumption,
    write_diagnosis_authority_exclusive,
)


SHA = "a" * 64
EVIDENCE = {
    "capture_canonical_graph_sha256": "1" * 64,
    "capture_phase_result_sha256": "2" * 64,
    "dataset_sha256": "3" * 64,
    "embedding_cache_sha256": "4" * 64,
    "prompt_cache_sha256": "5" * 64,
    "replay_checkpoint_sha256": "6" * 64,
    "replay_events_sha256": "7" * 64,
    "replay_phase_result_sha256": "8" * 64,
    "split_sha256": "9" * 64,
}
SOURCES = {
    "authority": "1" * 64,
    "controller": "2" * 64,
    "diagnosis": "3" * 64,
    "dry_run": "4" * 64,
    "production": "5" * 64,
    "test": "6" * 64,
}


def _authority() -> dict:
    return build_diagnosis_authority(
        source_hash="b" * 64,
        episode_manifest_sha256="c" * 64,
        evidence_sha256=EVIDENCE,
        source_sha256=SOURCES,
    )


def test_authority_binds_only_the_preserved_retry_005_read() -> None:
    authority = _authority()

    assert verify_diagnosis_authority(authority) == authority
    assert authority["execution_identity"] == {
        "attempt_id": "005",
        "history_id": "07741c45",
        "namespace": "pev3-s4-d0-replay-20260815-005",
        "replay_run_id": "s4-d0-replay-20260815-005",
        "source_sequence": 7,
        "source_hash": "b" * 64,
        "episode_manifest_sha256": "c" * 64,
    }
    assert authority["authority"] == {
        "read_only_source_7_diagnosis_authorized": True,
        "cleanup_authorized": False,
        "retry_006_authorized": False,
        "qualification_authorized": False,
        "s5_authorized": False,
    }


@pytest.mark.parametrize(
    ("section", "field", "value"),
    [
        ("execution_identity", "source_sequence", 6),
        ("execution_identity", "namespace", "foreign"),
        ("authority", "cleanup_authorized", True),
        ("evidence_sha256", "prompt_cache_sha256", "f" * 64),
    ],
)
def test_authority_verifier_rejects_binding_or_scope_drift(
    section: str,
    field: str,
    value,
) -> None:
    authority = _authority()
    authority[section][field] = value

    with pytest.raises(ValueError):
        verify_diagnosis_authority(authority)


def test_authority_and_consumption_are_both_exclusive(tmp_path: Path) -> None:
    authority = _authority()
    authority_path = tmp_path / "authority.json"
    consumption_path = tmp_path / "consumption.json"

    write_diagnosis_authority_exclusive(authority_path, authority)
    with pytest.raises(FileExistsError):
        write_diagnosis_authority_exclusive(authority_path, authority)

    consumption = consume_diagnosis_authority(
        authority=authority,
        authority_file_sha256=SHA,
        output_path=consumption_path,
    )
    assert verify_diagnosis_authority_consumption(
        consumption,
        authority=authority,
        authority_file_sha256=SHA,
    ) == consumption
    with pytest.raises(FileExistsError):
        consume_diagnosis_authority(
            authority=copy.deepcopy(authority),
            authority_file_sha256=SHA,
            output_path=consumption_path,
        )
