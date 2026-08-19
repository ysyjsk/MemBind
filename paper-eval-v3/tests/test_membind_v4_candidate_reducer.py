"""Offline TDD contracts for sealed v4 candidate reduction inputs."""

from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path

import pytest

from paper_eval.artifacts import atomic_write_json, payload_sha256, sha256_file
from paper_eval.membind_v4.reducer import V4ReducerError, reduce_candidate


def _seal(body: dict[str, object]) -> dict[str, object]:
    value = dict(body)
    value["payload_sha256"] = payload_sha256(value)
    return value


def _write(path: Path, value: dict[str, object]) -> Path:
    atomic_write_json(path, value)
    return path


def _fixture(tmp_path: Path) -> tuple[Path, Path]:
    candidate_root = tmp_path / "candidates" / "c01"
    candidate_root.mkdir(parents=True)
    _write(
        candidate_root / "candidate.json",
        _seal(
            {
                "schema_version": "membind.paper-eval-v4.candidate.v1",
                "status": "COMPLETED",
                "candidate_id": "c01",
                "source_count": 6,
            }
        ),
    )
    _write(
        candidate_root / "summary.json",
        _seal(
            {
                "schema_version": "membind.paper-eval-v4.summary.v1",
                "status": "PASS",
                "candidate_id": "c01",
                "history_id": "07741c45",
                "source_count": 6,
                "qualified_node_resolve_count": 0,
                "speculation_launch_count": 0,
                "exact_validation_completed_count": 0,
                "semantic_hit_count": 0,
                "semantic_miss_count": 0,
                "overlap_count": 0,
                "direct_violation_count": 0,
                "result": {
                    "performance": {
                        "makespan_ns": 120,
                        "p95_freshness_ns": 24,
                    }
                },
            }
        ),
    )
    reference_path = _write(
        tmp_path / "PREFIX_REFERENCE.json",
        _seal(
            {
                "schema_version": "membind.paper-eval-v3.membind-v4-prefix-reference.v1",
                "status": "PASS",
                "history_id": "07741c45",
                "prefixes": {
                    "sources_0_5": {
                        "source_count": 6,
                        "methods": {
                            "MemBind": {
                                "makespan_ns": 100,
                                "freshness_ns_p95": 20,
                            }
                        },
                    }
                },
            }
        ),
    )
    return candidate_root, reference_path


def _rewrite_sealed(path: Path, mutate: Callable[[dict[str, object]], None]) -> None:
    body = json.loads(path.read_text(encoding="utf-8"))
    body.pop("payload_sha256")
    mutate(body)
    atomic_write_json(path, _seal(body))


def test_candidate_reduction_binds_all_sealed_inputs(tmp_path: Path) -> None:
    candidate_root, reference_path = _fixture(tmp_path)

    result = reduce_candidate(
        candidate_root=candidate_root,
        reference_path=reference_path,
    )

    bindings = result["input_bindings"]
    for name, path in (
        ("candidate", candidate_root / "candidate.json"),
        ("summary", candidate_root / "summary.json"),
        ("prefix_reference", reference_path),
    ):
        artifact = json.loads(path.read_text(encoding="utf-8"))
        assert bindings[name]["payload_sha256"] == artifact["payload_sha256"]
        assert bindings[name]["file_sha256"] == sha256_file(path)


@pytest.mark.parametrize(
    ("selected", "error"),
    (
        ("summary", "candidate_summary_payload_hash_mismatch"),
        ("reference", "prefix_reference_payload_hash_mismatch"),
    ),
)
def test_candidate_reduction_rejects_tampered_input_seals(
    tmp_path: Path,
    selected: str,
    error: str,
) -> None:
    candidate_root, reference_path = _fixture(tmp_path)
    path = candidate_root / "summary.json" if selected == "summary" else reference_path
    body = json.loads(path.read_text(encoding="utf-8"))
    body["status"] = "TAMPERED"
    atomic_write_json(path, body)

    with pytest.raises(V4ReducerError, match=error):
        reduce_candidate(candidate_root=candidate_root, reference_path=reference_path)


@pytest.mark.parametrize(
    ("selected", "mutate", "error"),
    (
        (
            "summary",
            lambda body: body.__setitem__("schema_version", "wrong.summary.v1"),
            "candidate_summary_schema_invalid",
        ),
        (
            "reference",
            lambda body: body.__setitem__("schema_version", "wrong.reference.v1"),
            "prefix_reference_schema_invalid",
        ),
        (
            "summary",
            lambda body: body.__setitem__("candidate_id", "c02"),
            "candidate_summary_identity_drift",
        ),
        (
            "candidate",
            lambda body: body.__setitem__("source_count", 12),
            "candidate_summary_identity_drift",
        ),
    ),
)
def test_candidate_reduction_rejects_schema_or_candidate_identity_drift(
    tmp_path: Path,
    selected: str,
    mutate: Callable[[dict[str, object]], None],
    error: str,
) -> None:
    candidate_root, reference_path = _fixture(tmp_path)
    paths = {
        "candidate": candidate_root / "candidate.json",
        "summary": candidate_root / "summary.json",
        "reference": reference_path,
    }
    _rewrite_sealed(paths[selected], mutate)

    with pytest.raises(V4ReducerError, match=error):
        reduce_candidate(candidate_root=candidate_root, reference_path=reference_path)


def test_candidate_reduction_rejects_reference_prefix_source_count_drift(
    tmp_path: Path,
) -> None:
    candidate_root, reference_path = _fixture(tmp_path)

    def mutate(body: dict[str, object]) -> None:
        body["prefixes"]["sources_0_5"]["source_count"] = 12  # type: ignore[index]

    _rewrite_sealed(reference_path, mutate)

    with pytest.raises(V4ReducerError, match="prefix_reference_identity_drift"):
        reduce_candidate(candidate_root=candidate_root, reference_path=reference_path)
