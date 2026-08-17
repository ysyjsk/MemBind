"""Offline RED/GREEN tests for independent S6 namespace observation."""

from __future__ import annotations

import asyncio
import copy
from collections.abc import Mapping
from pathlib import Path

import pytest

from paper_eval.artifacts import payload_sha256
from paper_eval.s6_block_postprocess import (
    ENTITY_OBSERVATION,
    EPISODIC_OBSERVATION,
    RELATES_TO_OBSERVATION,
    S6BlockPostprocessError,
    finalize_s6_post_observation,
    observe_s6_post_namespace,
    verify_s6_post_observation,
    verify_s6_post_observation_artifact,
)
from paper_eval.s6_calibration_contract import (
    DEVELOPMENT_HISTORIES_PAYLOAD_SHA256,
    build_s6_matrix,
)


def _matrix() -> dict[str, object]:
    return build_s6_matrix(
        input_bindings={
            "s6_development_histories_payload_sha256": (
                DEVELOPMENT_HISTORIES_PAYLOAD_SHA256
            ),
            "parent_protocol_sha256": "1" * 64,
            "s5_pstar_result_file_sha256": "2" * 64,
            "s5_pstar_result_payload_sha256": "3" * 64,
            "s5_mstar_result_file_sha256": "4" * 64,
            "s5_mstar_result_payload_sha256": "5" * 64,
        }
    )


def _cell(method: str = "M*") -> dict[str, object]:
    return copy.deepcopy(_matrix()["cells"][1 if method == "M*" else 0])


def _sources(count: int = 3) -> list[dict[str, object]]:
    return [
        {"source_sequence": index, "source_sha256": f"{index + 1:064x}"}
        for index in range(count)
    ]


def _terminals(
    statuses: tuple[str, ...] = ("PUBLISHED", "PUBLISHED", "PUBLISHED"),
) -> list[dict[str, object]]:
    return [
        {**source, "status": statuses[index]}
        for index, source in enumerate(_sources(len(statuses)))
    ]


def _rows(
    count: int = 3, *, method: str = "M*"
) -> dict[str, list[dict[str, object]]]:
    episodes = [
        {
            "record_id": f"episode-{index}",
            "group_id": str(_cell(method)["namespace"]),
            **source,
        }
        for index, source in enumerate(_sources(count))
    ]
    return {
        EPISODIC_OBSERVATION: episodes,
        ENTITY_OBSERVATION: [
            {"record_id": "entity-a", "group_id": str(_cell(method)["namespace"])},
            {"record_id": "entity-b", "group_id": str(_cell(method)["namespace"])},
        ],
        RELATES_TO_OBSERVATION: [
            {
                "record_id": "relation-a",
                "group_id": str(_cell(method)["namespace"]),
                "source_entity_id": "entity-a",
                "target_entity_id": "entity-b",
                "provenance": [
                    {
                        "episode_id": "episode-0",
                        "group_id": str(_cell(method)["namespace"]),
                        "exists": True,
                    }
                ],
                "valid_at": "2026-01-01T00:00:00+00:00",
                "invalid_at": None,
                "expired_at": None,
            }
        ],
    }


async def _observe(
    *,
    method: str = "M*",
    sources: list[dict[str, object]] | None = None,
    terminals: list[dict[str, object]] | None = None,
    rows: dict[str, list[dict[str, object]]] | None = None,
) -> dict[str, object]:
    selected_rows = rows or _rows()

    async def query(
        _driver: object, observation: str, _namespace: str
    ) -> list[dict[str, object]]:
        return copy.deepcopy(selected_rows[observation])

    return await observe_s6_post_namespace(
        driver=object(),
        cell=_cell(method),
        execution_identity_sha256="a" * 64,
        expected_sources=sources or _sources(),
        source_terminals=terminals or _terminals(),
        query_executor=query,
    )


def test_complete_namespace_pass_binds_terminal_and_observed_manifests() -> None:
    artifact = asyncio.run(_observe())

    assert verify_s6_post_observation(artifact) == artifact
    assert artifact["status"] == "PASS"
    assert artifact["source_manifest_sha256"] == payload_sha256(_sources())
    assert artifact["terminal_manifest_sha256"] == payload_sha256(_terminals())
    assert artifact["published_manifest_sha256"] == payload_sha256(_sources())
    assert artifact["observed_episodic_manifest_sha256"] == payload_sha256(_sources())
    assert artifact["counts"] == {
        "expected_source_count": 3,
        "published_source_count": 3,
        "failed_source_count": 0,
        "censored_source_count": 0,
        "episodic_count": 3,
        "lost_episodic_count": 0,
        "duplicate_episodic_count": 0,
        "unexpected_episodic_count": 0,
        "episodic_namespace_escape_count": 0,
        "entity_count": 2,
        "relates_to_count": 1,
        "entity_namespace_escape_count": 0,
        "relation_namespace_escape_count": 0,
        "endpoint_escape_count": 0,
        "provenance_dangling_count": 0,
        "provenance_cross_namespace_count": 0,
        "valid_invalid_reversal_count": 0,
    }
    assert artifact["global_violation_total"] == 0
    assert str(_cell()["namespace"]) not in repr(artifact)


def test_pstar_failure_observes_only_published_subset_and_allows_multi_failed() -> None:
    statuses = ("PUBLISHED", "FAILED", "FAILED", "CENSORED")
    sources = _sources(4)
    terminals = _terminals(statuses)
    rows = _rows(1, method="P*")

    artifact = asyncio.run(
        _observe(method="P*", sources=sources, terminals=terminals, rows=rows)
    )

    assert artifact["status"] == "PASS"
    assert artifact["counts"]["published_source_count"] == 1
    assert artifact["counts"]["failed_source_count"] == 2
    assert artifact["counts"]["censored_source_count"] == 1
    assert artifact["published_manifest_sha256"] == payload_sha256([sources[0]])
    assert artifact["global_violation_total"] == 0


def test_lost_duplicate_and_unexpected_episodics_are_direct_violations() -> None:
    rows = _rows()
    rows[EPISODIC_OBSERVATION] = [
        rows[EPISODIC_OBSERVATION][0],
        {
            **rows[EPISODIC_OBSERVATION][0],
            "record_id": "episode-0-duplicate",
        },
        rows[EPISODIC_OBSERVATION][2],
        {
            "record_id": "unexpected",
            "group_id": str(_cell()["namespace"]),
            "source_sequence": 3,
            "source_sha256": "0" * 64,
        },
    ]

    artifact = asyncio.run(_observe(rows=rows))

    assert artifact["status"] == "INVARIANT_VIOLATIONS_OBSERVED"
    assert artifact["counts"]["lost_episodic_count"] == 1
    assert artifact["counts"]["duplicate_episodic_count"] == 1
    assert artifact["counts"]["unexpected_episodic_count"] == 1
    assert artifact["global_violation_total"] == 3
    assert artifact["per_source_violation_counts"] == {"0": 1, "1": 1, "2": 0}


def test_graph_escape_provenance_and_temporal_violations_are_counted() -> None:
    rows = _rows()
    rows[ENTITY_OBSERVATION][0]["group_id"] = "foreign"
    rows[RELATES_TO_OBSERVATION][0].update(
        {
            "group_id": "foreign",
            "source_entity_id": "missing",
            "target_entity_id": "entity-b",
            "provenance": [
                {"episode_id": "missing", "group_id": "foreign", "exists": False},
                {
                    "episode_id": "episode-0",
                    "group_id": str(_cell()["namespace"]),
                    "exists": True,
                },
            ],
            "valid_at": "2026-03-01T00:00:00+00:00",
            "invalid_at": "2026-02-01T00:00:00+00:00",
        }
    )

    artifact = asyncio.run(_observe(rows=rows))

    assert artifact["counts"]["entity_namespace_escape_count"] == 1
    assert artifact["counts"]["relation_namespace_escape_count"] == 1
    assert artifact["counts"]["endpoint_escape_count"] == 1
    assert artifact["counts"]["provenance_dangling_count"] == 1
    assert artifact["counts"]["valid_invalid_reversal_count"] == 1
    assert artifact["global_violation_total"] == 5


def test_query_failure_is_sanitized() -> None:
    async def broken(
        _driver: object, _observation: str, _namespace: str
    ) -> list[dict[str, object]]:
        raise RuntimeError("private neo4j URI and credentials")

    with pytest.raises(S6BlockPostprocessError, match="query_execution_failed") as caught:
        asyncio.run(
            observe_s6_post_namespace(
                driver=object(),
                cell=_cell(),
                execution_identity_sha256="a" * 64,
                expected_sources=_sources(),
                source_terminals=_terminals(),
                query_executor=broken,
            )
        )
    assert "private neo4j" not in str(caught.value)


def test_verifier_recomputes_manifests_counts_and_seal() -> None:
    artifact = asyncio.run(_observe())
    for mutation in (
        lambda value: value["counts"].update(lost_episodic_count=1),
        lambda value: value.update(global_violation_total=1),
        lambda value: value.update(cell_index=2),
        lambda value: value["source_classifications"][0].update(status="FAILED"),
        lambda value: value.update(namespace="private"),
    ):
        altered = copy.deepcopy(artifact)
        mutation(altered)
        altered["observation_sha256"] = payload_sha256(
            {key: item for key, item in altered.items() if key != "observation_sha256"}
        )
        with pytest.raises(S6BlockPostprocessError):
            verify_s6_post_observation(altered)


def test_finalized_post_observation_is_exclusive(tmp_path: Path) -> None:
    payload = asyncio.run(_observe())
    output = tmp_path / "post_observation.json"

    artifact = finalize_s6_post_observation(
        output_path=output,
        payload=payload,
        git_commit="a" * 40,
    )

    assert artifact["run_id"] == "s6-07741c45-mstar-c1-001-post-observation"
    assert artifact["status"] == "finalized"
    assert verify_s6_post_observation_artifact(artifact) == artifact
    with pytest.raises(FileExistsError):
        finalize_s6_post_observation(
            output_path=output,
            payload=payload,
            git_commit="a" * 40,
        )
