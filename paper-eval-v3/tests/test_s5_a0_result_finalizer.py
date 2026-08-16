"""Offline TDD for independent A0 scientific-result finalization."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest

from paper_eval.artifacts import finalize_envelope, sha256_file
from paper_eval.s5_a0_controller import S5A0ControllerError, execute_s5_a0_controller
from paper_eval.s5_a0_result_finalizer import (
    S5A0FinalizerError,
    S5A0FinalizerPaths,
    finalize_s5_a0_result,
    verify_s5_a0_result,
)
from paper_eval.s5_durable_attempt_store import S5AttemptStore, inspect_s5_attempt
from paper_eval.s5_native_method_adapters import run_a0
from paper_eval.s5_native_post_observation import (
    ENTITY_OBSERVATION,
    EPISODIC_OBSERVATION,
    RELATES_TO_OBSERVATION,
    observe_s5_native_post_namespace,
)
from tests.test_s5_a0_controller import _chain, _dependencies, _write_json
from tests.test_s5_native_post_observation import QueryExecutor


PROJECT = Path(__file__).resolve().parents[1]
FINALIZER_SOURCE = PROJECT / "src/paper_eval/s5_a0_result_finalizer.py"


class _DurableA0Runner:
    def __init__(
        self,
        *,
        attempt_root: Path,
        spec,
        identity,
        episodes,
        **_unused,
    ) -> None:
        self.attempt_root = attempt_root
        self.spec = spec
        self.identity = identity
        self.episodes = tuple(episodes)

    async def run(self) -> dict[str, object]:
        store = S5AttemptStore.create(
            self.attempt_root,
            run_id=self.spec.run_id,
            method="A0",
            production_core_identity_sha256=self.identity["identity_sha256"],
            source_sha256s=[item.source_sha256 for item in self.episodes],
        )

        async def persist(event):
            store.append_event(event)

        async def native(_episode):
            return None

        timestamp = 0

        def clock() -> int:
            nonlocal timestamp
            timestamp += 1
            return timestamp

        evidence = await run_a0(
            spec=self.spec,
            episodes=self.episodes,
            native_add_episode=native,
            persist_event=persist,
            clock_ns=clock,
        )
        evidence["production_core_identity_sha256"] = self.identity[
            "identity_sha256"
        ]
        finalized = store.finalize(evidence)
        return {
            **finalized,
            "payload": evidence,
            "production_identity_sha256": self.identity["identity_sha256"],
        }


def _rows(namespace: str, sources: list[dict[str, object]], *, violation: bool):
    episodes = [
        {
            "record_id": f"private-episode-{source['source_sequence']}",
            "group_id": namespace,
            **source,
        }
        for source in sources
    ]
    entity_a_group = "foreign-group" if violation else namespace
    entities = [
        {"record_id": "private-entity-a", "group_id": entity_a_group},
        {"record_id": "private-entity-b", "group_id": namespace},
    ]
    relations = [
        {
            "record_id": "private-relation",
            "group_id": namespace,
            "source_entity_id": "private-entity-a",
            "target_entity_id": "private-entity-b",
            "provenance": [
                {
                    "episode_id": "private-episode-0",
                    "group_id": namespace,
                    "exists": True,
                }
            ],
            "valid_at": "2026-01-01T00:00:00Z",
            "invalid_at": None,
        }
    ]
    return {
        EPISODIC_OBSERVATION: episodes,
        ENTITY_OBSERVATION: entities,
        RELATES_TO_OBSERVATION: relations,
    }


def _completed_chain(
    tmp_path: Path,
    *,
    observation_violation: bool = False,
    correct_verifier_binding: bool = True,
) -> S5A0FinalizerPaths:
    controller_paths, episodes = _chain(tmp_path)
    authority = json.loads(controller_paths.authority.read_text(encoding="utf-8"))
    authority["payload"]["source_sha256"]["result_verifier"] = (
        sha256_file(FINALIZER_SOURCE) if correct_verifier_binding else "8" * 64
    )
    authority = finalize_envelope(
        payload=authority["payload"],
        protocol_version="paper-eval-v3",
        git_commit="deadbeef",
        run_id=authority["run_id"],
    )
    _write_json(controller_paths.authority, authority)

    trace: list[str] = []
    dependencies = _dependencies(trace)
    dependencies["runner_factory"] = lambda **kwargs: _DurableA0Runner(**kwargs)
    controller_result = asyncio.run(
        execute_s5_a0_controller(
            paths=controller_paths,
            episodes=episodes,
            git_commit="deadbeef",
            **dependencies,
        )
    )
    assert controller_result["status"] == "controller_complete_evidence_only"

    authority_payload = authority["payload"]
    run = authority_payload["run"]
    sources = [
        {
            "source_sequence": item.source_sequence,
            "source_sha256": item.source_sha256,
        }
        for item in episodes
    ]
    attempt = inspect_s5_attempt(controller_paths.attempt_root)
    publications = [
        event for event in attempt["events"] if event["event_type"] == "publication"
    ]
    observation = asyncio.run(
        observe_s5_native_post_namespace(
            driver=object(),
            method="A0",
            run_id=run["run_id"],
            namespace=run["namespace"],
            expected_sources=sources,
            durable_publication_events=publications,
            query_executor=QueryExecutor(
                _rows(
                    run["namespace"],
                    sources,
                    violation=observation_violation,
                )
            ),
        )
    )
    observation_path = tmp_path / "runs" / run["run_id"] / "post_observation.json"
    _write_json(observation_path, observation)
    return S5A0FinalizerPaths(
        production_identity=controller_paths.production_identity,
        production_identity_qualification=(
            controller_paths.production_identity_qualification
        ),
        current_stage_pointer=controller_paths.current_stage_pointer,
        preflight=controller_paths.preflight,
        authority=controller_paths.authority,
        consumption=controller_paths.consumption,
        controller_root=controller_paths.controller_root,
        attempt_root=controller_paths.attempt_root,
        post_observation=observation_path,
        result=tmp_path / "runs" / run["run_id"] / "S5_A0_RESULT.json",
    )


def test_complete_cross_bound_chain_is_the_only_path_to_scientific_pass(
    tmp_path: Path,
) -> None:
    paths = _completed_chain(tmp_path)

    artifact = finalize_s5_a0_result(paths=paths, git_commit="deadbeef")
    verified = verify_s5_a0_result(artifact)

    assert paths.result.is_file()
    assert verified["payload"]["verdict"] == "PASS"
    assert verified["payload"]["smoke_summary"]["episode_count"] == 49
    assert verified["payload"]["smoke_summary"][
        "direct_invariant_violation_count"
    ] == 0
    assert verified["payload"]["authority"] == {
        "scientific_pass_authorized": True,
        "next_method_authorized": True,
        "current_stage_pointer_update_authorized": False,
        "pilot_execution_authorized": False,
        "formal_execution_authorized": False,
        "resume_authorized": False,
        "namespace_cleanup_authorized": False,
    }


def test_missing_observation_never_defaults_direct_invariants_to_zero(
    tmp_path: Path,
) -> None:
    paths = _completed_chain(tmp_path)
    paths.post_observation.unlink()

    with pytest.raises(S5A0FinalizerError, match="post_observation"):
        finalize_s5_a0_result(paths=paths, git_commit="deadbeef")
    assert not paths.result.exists()


def test_observed_direct_invariant_violation_blocks_a0_pass(tmp_path: Path) -> None:
    paths = _completed_chain(tmp_path, observation_violation=True)

    with pytest.raises(S5A0FinalizerError, match="direct_invariant"):
        finalize_s5_a0_result(paths=paths, git_commit="deadbeef")
    assert not paths.result.exists()


def test_authority_must_bind_the_executed_result_verifier_source(
    tmp_path: Path,
) -> None:
    # The controller now checks this binding before consuming live authority,
    # so a stale verifier can no longer execute far enough to reach finalization.
    with pytest.raises(S5A0ControllerError, match="result_verifier_source"):
        _completed_chain(tmp_path, correct_verifier_binding=False)


def test_native_or_observation_tampering_fails_before_result_write(
    tmp_path: Path,
) -> None:
    paths = _completed_chain(tmp_path)
    result_path = paths.attempt_root / "result.json"
    native = json.loads(result_path.read_text(encoding="utf-8"))
    native["payload"]["summary"]["publication_count"] = 0
    _write_json(result_path, native)

    with pytest.raises(S5A0FinalizerError, match="native_attempt"):
        finalize_s5_a0_result(paths=paths, git_commit="deadbeef")
    assert not paths.result.exists()


def test_final_result_is_exclusive_and_standalone_tampering_is_rejected(
    tmp_path: Path,
) -> None:
    paths = _completed_chain(tmp_path)
    artifact = finalize_s5_a0_result(paths=paths, git_commit="deadbeef")

    with pytest.raises(S5A0FinalizerError, match="result_exists"):
        finalize_s5_a0_result(paths=paths, git_commit="deadbeef")

    artifact["payload"]["smoke_summary"]["episode_count"] = 0
    with pytest.raises(S5A0FinalizerError):
        verify_s5_a0_result(artifact)
