"""One-shot controller for the retry-005 source-7 read-only diagnosis."""

from __future__ import annotations

import argparse
import asyncio
import json
from collections.abc import Mapping, Sequence
from contextlib import ExitStack
from pathlib import Path
from typing import Any, Callable

from .artifacts import sha256_file
from .s4_controller import _legacy_episodes
from .s4_d0_production import (
    S4CachePaths,
    build_s4_phase_runtime,
)
from .s4_edge_identity_diagnosis import (
    build_edge_identity_diagnosis,
    verify_edge_identity_diagnosis,
    write_edge_identity_diagnosis_exclusive,
)
from .s4_edge_identity_diagnosis_authority import (
    consume_diagnosis_authority,
    verify_diagnosis_authority,
)
from .s4_edge_identity_diagnosis_production import (
    build_episode_manifest,
    candidate_call_diagnoses,
    install_edge_resolution_hook,
    namespace_snapshot,
    persisted_evidence_diagnosis,
    validate_d2_runtime,
)
from .s4_edge_identity_dry_run import (
    D2DiagnosticStop,
    D2SideEffectCounters,
    EdgeCandidateBarrier,
    model_client_fence,
    publication_fence,
    read_only_database_fence,
)


ROOT = Path(__file__).resolve().parents[3]
PROJECT = ROOT / "paper-eval-v3"
LEGACY = ROOT / "membind-validation"
NATIVE = PROJECT / "artifacts/paper_eval/native"
CAPTURE_RUN = NATIVE / "runs/s4-d0-capture-20260815-005"
REPLAY_RUN = NATIVE / "runs/s4-d0-replay-20260815-005"
DEFAULT_DATASET = Path(
    "/data/predator/ly/Mem/data/raw/longmemeval-cleaned/longmemeval_s_cleaned.json"
)
DEFAULT_SPLIT = LEGACY / "artifacts/dataset/frozen_split.json"
DEFAULT_AUTHORITY = (
    PROJECT / "runtime/S4_EDGE_IDENTITY_DIAGNOSIS_AUTHORITY_RETRY_005.json"
)
DEFAULT_CONSUMPTION = (
    PROJECT
    / "runtime/S4_EDGE_IDENTITY_DIAGNOSIS_AUTHORITY_CONSUMPTION_RETRY_005.json"
)
DEFAULT_OUTPUT = NATIVE / "S4_EDGE_IDENTITY_DIAGNOSIS_RETRY_005.json"


class ControllerError(RuntimeError):
    """The bounded controller input or execution identity drifted."""


def _payload(value: object, *, label: str) -> dict[str, Any]:
    if not isinstance(value, Mapping) or not isinstance(value.get("payload"), Mapping):
        raise ControllerError(f"{label} phase artifact is malformed")
    return dict(value["payload"])


def validate_retry005_state(
    *,
    capture_phase: Mapping[str, Any],
    replay_phase: Mapping[str, Any],
    replay_checkpoint: Mapping[str, Any],
    replay_events: Sequence[Mapping[str, Any]],
) -> dict[str, int]:
    """Require the exact sealed capture and failed replay prefix."""

    capture = _payload(capture_phase, label="capture")
    replay = _payload(replay_phase, label="replay")
    if (
        capture_phase.get("run_id") != "s4-d0-capture-20260815-005"
        or capture.get("stage") != "S4"
        or capture.get("phase") != "U0_CAPTURE"
        or capture.get("run_id") != "s4-d0-capture-20260815-005"
        or capture.get("history_id") != "07741c45"
        or capture.get("namespace") != "pev3-s4-u0-capture-20260815-005"
        or capture.get("mode") != "capture"
        or capture.get("status") != "PASS"
        or capture.get("mergeable") is not True
        or capture.get("completed_source_sequences") != list(range(49))
        or capture.get("error_class") is not None
    ):
        raise ControllerError("retry-005 capture evidence drift")
    if (
        replay_phase.get("run_id") != "s4-d0-replay-20260815-005"
        or replay.get("stage") != "S4"
        or replay.get("phase") != "D0_READ_ONLY_REPLAY"
        or replay.get("run_id") != "s4-d0-replay-20260815-005"
        or replay.get("history_id") != "07741c45"
        or replay.get("namespace") != "pev3-s4-d0-replay-20260815-005"
        or replay.get("mode") != "replay"
        or replay.get("status") != "INCOMPLETE"
        or replay.get("mergeable") is not False
        or replay.get("completed_source_sequences") != list(range(7))
        or replay.get("error_class") != "CandidateRemapError"
    ):
        raise ControllerError("retry-005 replay evidence drift")
    checkpoint = dict(replay_checkpoint)
    state = checkpoint.get("namespace_state")
    if (
        checkpoint.get("run_id") != "s4-d0-replay-20260815-005"
        or checkpoint.get("phase") != "D0_READ_ONLY_REPLAY"
        or checkpoint.get("status") != "incomplete"
        or checkpoint.get("completed_source_sequences") != list(range(7))
        or checkpoint.get("namespace") != "pev3-s4-d0-replay-20260815-005"
        or not isinstance(state, Mapping)
        or state.get("node_count") != 32
        or state.get("relationship_count") != 48
        or not isinstance(state.get("episode_names"), list)
        or len(state["episode_names"]) != 7
    ):
        raise ControllerError("retry-005 replay checkpoint drift")
    failures = [event for event in replay_events if event.get("event_type") == "failure"]
    if len(failures) != 1 or {
        key: failures[0].get(key)
        for key in (
            "source_sequence",
            "error_class",
            "error_code",
            "failure_stage",
        )
    } != {
        "source_sequence": 7,
        "error_class": "CandidateRemapError",
        "error_code": "AMBIGUOUS_CANDIDATE_IDENTITY",
        "failure_stage": "add_episode",
    }:
        raise ControllerError("retry-005 failure event drift")
    return {
        "completed_prefix_count": 7,
        "failure_source_sequence": 7,
        "namespace_node_count": 32,
        "namespace_relationship_count": 48,
    }


def compose_replay_spec(authority: Mapping[str, Any]) -> dict[str, str]:
    identity = authority.get("execution_identity")
    if not isinstance(identity, Mapping):
        raise ControllerError("diagnosis authority execution identity is missing")
    expected = {
        "history_id": "07741c45",
        "namespace": "pev3-s4-d0-replay-20260815-005",
        "replay_run_id": "s4-d0-replay-20260815-005",
    }
    if any(identity.get(name) != value for name, value in expected.items()):
        raise ControllerError("diagnosis authority replay identity drift")
    return {
        "phase": "D0_READ_ONLY_REPLAY",
        "run_id": "s4-d0-replay-20260815-005",
        "history_id": "07741c45",
        "namespace": "pev3-s4-d0-replay-20260815-005",
        "method": "D0",
        "mode": "replay",
        "cache_id": "s4-d0-remap-07741c45-20260815-005",
    }


def construct_runtime_without_event_loop(builder: Callable[[], Any]) -> Any:
    """Construct Neo4jDriver synchronously so it cannot schedule schema work."""

    try:
        asyncio.get_running_loop()
    except RuntimeError:
        pass
    else:
        raise ControllerError("D2 runtime construction inside an active event loop")
    runtime = builder()
    validate_d2_runtime(runtime)
    return runtime


def _json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise ControllerError(f"unreadable JSON evidence: {path.name}") from error
    if not isinstance(value, dict):
        raise ControllerError(f"JSON evidence is not an object: {path.name}")
    return value


def _jsonl(path: Path) -> list[dict[str, Any]]:
    try:
        values = [
            json.loads(line)
            for line in Path(path).read_text(encoding="utf-8").split("\n")
            if line.strip()
        ]
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise ControllerError(f"unreadable JSONL evidence: {path.name}") from error
    if any(not isinstance(value, dict) for value in values):
        raise ControllerError(f"JSONL evidence contains a non-object: {path.name}")
    return values


def evidence_paths(dataset: Path, split: Path) -> dict[str, Path]:
    return {
        "capture_canonical_graph_sha256": CAPTURE_RUN / "canonical_graph.json",
        "capture_phase_result_sha256": CAPTURE_RUN / "phase_result.json",
        "dataset_sha256": Path(dataset),
        "embedding_cache_sha256": PROJECT
        / "runtime/private/s4-d0-remap-07741c45-20260815-005/embedding.jsonl",
        "prompt_cache_sha256": PROJECT
        / "runtime/private/s4-d0-remap-07741c45-20260815-005/prompt.jsonl",
        "replay_checkpoint_sha256": REPLAY_RUN / "checkpoint.json",
        "replay_events_sha256": REPLAY_RUN / "events.jsonl",
        "replay_phase_result_sha256": REPLAY_RUN / "phase_result.json",
        "split_sha256": Path(split),
    }


def evidence_sha256(dataset: Path, split: Path) -> dict[str, str]:
    selected = {
        name: sha256_file(path) for name, path in evidence_paths(dataset, split).items()
    }
    if "missing" in selected.values():
        raise ControllerError("diagnosis input evidence is missing")
    return selected


def source_sha256() -> dict[str, str]:
    return {
        "authority": sha256_file(
            PROJECT / "src/paper_eval/s4_edge_identity_diagnosis_authority.py"
        ),
        "controller": sha256_file(
            PROJECT / "src/paper_eval/s4_edge_identity_diagnosis_controller.py"
        ),
        "diagnosis": sha256_file(
            PROJECT / "src/paper_eval/s4_edge_identity_diagnosis.py"
        ),
        "dry_run": sha256_file(
            PROJECT / "src/paper_eval/s4_edge_identity_dry_run.py"
        ),
        "production": sha256_file(
            PROJECT / "src/paper_eval/s4_edge_identity_diagnosis_production.py"
        ),
        "test": sha256_file(
            PROJECT / "tests/test_s4_edge_identity_diagnosis_controller.py"
        ),
    }


async def _execute_and_close(
    *,
    runtime: Any,
    episodes: Sequence[Any],
    authority: Mapping[str, Any],
    persisted_diagnosis: Mapping[str, Any],
) -> dict[str, Any]:
    graph = runtime.graph
    driver = validate_d2_runtime(runtime)
    identity = authority["execution_identity"]
    cache = authority["private_cache"]
    prompt_path = PROJECT / cache["prompt_relpath"]
    embedding_path = PROJECT / cache["embedding_relpath"]
    cache_before = {
        "prompt": sha256_file(prompt_path),
        "embedding": sha256_file(embedding_path),
    }
    counters = D2SideEffectCounters()
    barrier = EdgeCandidateBarrier(expected_call_count=10, timeout_seconds=30.0)
    try:
        from graphiti_core import helpers
        from graphiti_core.utils.maintenance import edge_operations

        if int(helpers.SEMAPHORE_LIMIT) < 10:
            raise ControllerError("Graphiti semaphore cannot admit the ten-call barrier")
        with ExitStack() as stack:
            stack.enter_context(model_client_fence(graph, counters))
            stack.enter_context(read_only_database_fence(driver, counters))
            stack.enter_context(publication_fence(graph, counters))
            stack.enter_context(install_edge_resolution_hook(edge_operations, barrier))
            pre_state = await namespace_snapshot(driver, identity["namespace"])
            if (
                pre_state["node_count"] != 32
                or pre_state["relationship_count"] != 48
                or pre_state["episode_count"] != 7
            ):
                raise ControllerError("preserved retry-005 namespace prefix drift")
            try:
                await graph.add_episode(**runtime.episode_kwargs(episodes[7]))
            except D2DiagnosticStop:
                pass
            else:
                raise ControllerError("D2 source-7 call escaped the diagnostic stop")
            await barrier.wait_until_all_released()
            calls = await candidate_call_diagnoses(
                records=barrier.records,
                driver=driver,
                namespace=identity["namespace"],
                episodes=episodes,
            )
            post_state = await namespace_snapshot(driver, identity["namespace"])
            if validate_d2_runtime(runtime) is not driver:
                raise ControllerError("D2 Graphiti driver changed during diagnosis")

        cache_after = {
            "prompt": sha256_file(prompt_path),
            "embedding": sha256_file(embedding_path),
        }
        counters_dict = counters.public_dict()
        counters_dict["cache_write_count"] = int(cache_before != cache_after)
        runtime_evidence = runtime.runtime_evidence()
        if any(
            int(runtime_evidence.get(name, 0)) != 0
            for name in (
                "live_llm_calls",
                "live_embedding_calls",
                "unexpected_prompt_count",
                "unexpected_embedding_count",
                "live_fallback_count",
                "cross_encoder_call_count",
                "candidate_remap_rejection_count",
            )
        ):
            raise ControllerError("D2 cache-only runtime evidence drift")
        artifact = build_edge_identity_diagnosis(
            replay_run_id=identity["replay_run_id"],
            history_id=identity["history_id"],
            attempt_id=identity["attempt_id"],
            source_sequence=identity["source_sequence"],
            source_hash=identity["source_hash"],
            episode_manifest_sha256=identity["episode_manifest_sha256"],
            evidence_sha256=authority["evidence_sha256"],
            persisted_evidence_diagnosis=persisted_diagnosis,
            candidate_call_diagnoses=calls,
            pre_state=pre_state,
            post_state=post_state,
            cache_sha256_before=cache_before,
            cache_sha256_after=cache_after,
            side_effect_counters=counters_dict,
        )
        return verify_edge_identity_diagnosis(
            artifact,
            expected_evidence_sha256=authority["evidence_sha256"],
            expected_source_hash=identity["source_hash"],
            expected_episode_manifest_sha256=identity[
                "episode_manifest_sha256"
            ],
        )
    finally:
        await graph.close()


def run_controller(args: argparse.Namespace) -> dict[str, Any]:
    if args.output.exists():
        raise FileExistsError(str(args.output))
    authority = verify_diagnosis_authority(_json(args.authority))
    actual_evidence = evidence_sha256(args.dataset, args.split)
    if authority["evidence_sha256"] != actual_evidence:
        raise ControllerError("diagnosis authority input-file binding drift")
    if authority["source_sha256"] != source_sha256():
        raise ControllerError("diagnosis authority source binding drift")
    validate_retry005_state(
        capture_phase=_json(CAPTURE_RUN / "phase_result.json"),
        replay_phase=_json(REPLAY_RUN / "phase_result.json"),
        replay_checkpoint=_json(REPLAY_RUN / "checkpoint.json"),
        replay_events=_jsonl(REPLAY_RUN / "events.jsonl"),
    )
    episodes = _legacy_episodes(args.dataset, args.split)
    manifest, manifest_sha = build_episode_manifest(episodes)
    del manifest
    identity = authority["execution_identity"]
    if (
        identity["source_hash"] != episodes[7].source_hash
        or identity["episode_manifest_sha256"] != manifest_sha
        or args.output.resolve()
        != (PROJECT / authority["output_relpath"]).resolve()
    ):
        raise ControllerError("diagnosis authority dataset/output binding drift")
    cache_paths = S4CachePaths(
        prompt=PROJECT / authority["private_cache"]["prompt_relpath"],
        embedding=PROJECT / authority["private_cache"]["embedding_relpath"],
    )
    spec = compose_replay_spec(authority)
    persisted = persisted_evidence_diagnosis(
        episodes=episodes,
        prompt_cache_path=cache_paths.prompt,
        canonical_graph_path=CAPTURE_RUN / "canonical_graph.json",
        source_sequence=7,
    )
    consume_diagnosis_authority(
        authority=authority,
        authority_file_sha256=sha256_file(args.authority),
        output_path=args.consumption,
    )
    runtime = construct_runtime_without_event_loop(
        lambda: build_s4_phase_runtime(
            spec=spec,
            cache_paths=cache_paths,
            resume_capture=False,
        )
    )
    artifact = asyncio.run(
        _execute_and_close(
            runtime=runtime,
            episodes=episodes,
            authority=authority,
            persisted_diagnosis=persisted,
        )
    )
    write_edge_identity_diagnosis_exclusive(args.output, artifact)
    print(
        json.dumps(
            {
                "artifact_sha256": artifact["artifact_sha256"],
                "candidate_call_count": len(artifact["candidate_call_diagnoses"]),
                "reason": artifact["reason"],
                "verdict": artifact["verdict"],
            },
            sort_keys=True,
        ),
        flush=True,
    )
    return artifact


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dataset", type=Path, default=DEFAULT_DATASET)
    parser.add_argument("--split", type=Path, default=DEFAULT_SPLIT)
    parser.add_argument("--authority", type=Path, default=DEFAULT_AUTHORITY)
    parser.add_argument("--consumption", type=Path, default=DEFAULT_CONSUMPTION)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    return parser


def main() -> None:
    run_controller(_parser().parse_args())


if __name__ == "__main__":
    main()
