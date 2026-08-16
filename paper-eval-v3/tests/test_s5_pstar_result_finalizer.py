"""Independent P*(C=2) scientific-result finalization contracts."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from paper_eval.artifacts import payload_sha256
from paper_eval.s5_graphiti_native_binding import S5GraphitiNativeBinding
from paper_eval.s5_pstar_controller import execute_s5_pstar_controller
from paper_eval.s5_pstar_post_observation import build_s5_pstar_post_observation
from paper_eval.s5_pstar_result_finalizer import (
    S5PStarFinalizerError,
    S5PStarFinalizerPaths,
    finalize_s5_pstar_result,
    verify_s5_pstar_result,
)
from tests.test_s5_pstar_controller import _chain


def _binding(*, fail_source: int | None = None) -> S5GraphitiNativeBinding:
    async def add_episode(_graphiti: object, episode: object) -> None:
        await asyncio.sleep(0.001)
        if getattr(episode, "source_sequence") == fail_source:
            raise RuntimeError("private treatment failure detail")

    def graphiti_episode_kwargs(episode: object) -> dict[str, object]:
        return {"episode": episode}

    add_episode.__module__ = "graphiti_native"
    add_episode.__qualname__ = "add_episode"
    graphiti_episode_kwargs.__module__ = "graphiti_native"
    graphiti_episode_kwargs.__qualname__ = "graphiti_episode_kwargs"
    return S5GraphitiNativeBinding(
        module_name="graphiti_native",
        add_episode=add_episode,
        graphiti_episode_kwargs=graphiti_episode_kwargs,
    )


def _write(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, sort_keys=True) + "\n", encoding="ascii")


def _completed_chain(
    root: Path,
    *,
    fail_source: int | None = None,
    global_violations: int = 0,
) -> S5PStarFinalizerPaths:
    controller, episodes = _chain(root)
    runtime = SimpleNamespace(graphiti=object())
    outcome = asyncio.run(
        execute_s5_pstar_controller(
            paths=controller,
            episodes=episodes,
            git_commit="deadbeef",
            env_loader=lambda: {"opaque": "private"},
            runtime_factory=lambda _env: runtime,
            readiness=lambda _runtime: None,
            binding_loader=lambda: _binding(fail_source=fail_source),
            close_runtime=lambda _runtime: None,
        )
    )
    assert outcome["status"] == "controller_complete_evidence_only"

    manifest = json.loads(
        (controller.attempt_root / "manifest.json").read_text(encoding="utf-8")
    )
    events = [
        json.loads(line)["event"]
        for line in (controller.attempt_root / "events.jsonl")
        .read_text(encoding="utf-8")
        .splitlines()
    ]
    expected = [
        {"source_sequence": index, "source_sha256": digest}
        for index, digest in enumerate(manifest["source_sha256s"])
    ]
    terminals = [
        {
            "source_sequence": event["source_sequence"],
            "source_sha256": event["source_sha256"],
            "terminal_classification": event["terminal_classification"],
        }
        for event in events
        if event["event_type"] == "source_terminal"
    ]
    published = [
        expected[row["source_sequence"]]
        for row in terminals
        if row["terminal_classification"] == "PUBLISHED"
    ]
    per_source = {
        str(row["source_sequence"]): 0
        for row in published
    }
    if global_violations:
        per_source[str(published[0]["source_sequence"])] = global_violations
    post = build_s5_pstar_post_observation(
        run_id=manifest["run_id"],
        expected_sources=expected,
        source_terminals=terminals,
        observed_episodics=published,
        violation_counts={"entity_namespace_escape_count": global_violations},
        per_source_violation_counts=per_source,
    )
    run_root = controller.controller_root.parent
    post_path = run_root / "post_observation.json"
    _write(post_path, post)
    return S5PStarFinalizerPaths(
        production_identity=controller.production_identity,
        production_identity_qualification=controller.production_identity_qualification,
        current_stage_pointer=controller.current_stage_pointer,
        preflight=controller.preflight,
        authority=controller.authority,
        predecessor=controller.predecessor,
        consumption=controller.consumption,
        controller_root=controller.controller_root,
        attempt_root=controller.attempt_root,
        post_observation=post_path,
        result=run_root / "S5_PSTAR_RESULT.json",
    )


@pytest.mark.parametrize(
    ("global_violations", "expected_outcome"),
    [
        (0, "PASS"),
        (2, "DIRECT_INVARIANT_VIOLATION_OBSERVED"),
    ],
)
def test_full_publication_is_independently_finalized_without_erasing_violations(
    tmp_path: Path,
    global_violations: int,
    expected_outcome: str,
) -> None:
    paths = _completed_chain(tmp_path, global_violations=global_violations)

    artifact = finalize_s5_pstar_result(paths=paths, git_commit="deadbeef")
    checked = verify_s5_pstar_result(artifact)

    assert checked["payload"]["verdict"] == "SCIENTIFIC_OUTCOME_COMPLETE"
    assert checked["payload"]["scientific_outcome"] == expected_outcome
    assert checked["payload"]["terminal_accounting"] == {
        "expected": 49,
        "published": 49,
        "failed": 0,
        "censored": 0,
    }
    assert checked["payload"]["direct_invariant_observation"][
        "global_violation_total"
    ] == global_violations
    assert checked["payload"]["authority"]["next_method_authorized"] is True
    assert checked["payload"]["authority"]["resume_authorized"] is False


def test_treatment_failure_is_a_complete_scientific_outcome_with_exact_accounting(
    tmp_path: Path,
) -> None:
    paths = _completed_chain(tmp_path, fail_source=1)

    checked = verify_s5_pstar_result(
        finalize_s5_pstar_result(paths=paths, git_commit="deadbeef")
    )

    payload = checked["payload"]
    assert payload["verdict"] == "SCIENTIFIC_OUTCOME_COMPLETE"
    assert payload["scientific_outcome"] == "TREATMENT_FAILURE_OBSERVED"
    accounting = payload["terminal_accounting"]
    assert accounting["expected"] == 49
    assert accounting["failed"] == 1
    assert accounting["published"] + accounting["failed"] + accounting["censored"] == 49
    assert payload["smoke_summary"] is None
    assert payload["authority"]["next_method_authorized"] is True


def test_finalizer_rejects_post_observation_or_predecessor_drift(
    tmp_path: Path,
) -> None:
    paths = _completed_chain(tmp_path)
    post = json.loads(paths.post_observation.read_text(encoding="utf-8"))
    post["accounting"]["published"] = 48
    post["accounting"]["censored"] = 1
    post["observation_sha256"] = payload_sha256(
        {key: value for key, value in post.items() if key != "observation_sha256"}
    )
    _write(paths.post_observation, post)

    with pytest.raises(S5PStarFinalizerError):
        finalize_s5_pstar_result(paths=paths, git_commit="deadbeef")
    assert not paths.result.exists()


def test_result_write_is_exclusive_and_standalone_tampering_fails(
    tmp_path: Path,
) -> None:
    paths = _completed_chain(tmp_path)
    artifact = finalize_s5_pstar_result(paths=paths, git_commit="deadbeef")

    with pytest.raises(S5PStarFinalizerError, match="result_exists"):
        finalize_s5_pstar_result(paths=paths, git_commit="deadbeef")

    artifact["payload"]["terminal_accounting"]["published"] = 0
    with pytest.raises(S5PStarFinalizerError):
        verify_s5_pstar_result(artifact)
