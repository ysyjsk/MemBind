"""Single-use production controller for the S5 P*(C=2) smoke.

This controller accepts both a fully successful P* scheduler result and a
complete treatment-failure result.  Neither is scientific evidence until the
independent post-observation/finalizer chain has verified it.
"""

from __future__ import annotations

import argparse
import asyncio
import importlib
import inspect
import json
import re
import sys
from collections.abc import Awaitable, Callable, Mapping, Sequence
from copy import deepcopy
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .artifacts import append_jsonl_durable, atomic_write_json, payload_sha256, sha256_file
from .s5_live_authority import consume_s5_live_authority, verify_s5_live_authority
from .s5_live_preflight import verify_s5_live_preflight
from .s5_native_method_adapters import P_STAR, S5EpisodeRef, S5MethodSpec
from .s5_production_identity_qualification import (
    bind_s5_production_identity_qualification,
    verify_s5_production_identity_qualification,
)
from .s5_production_runner import S5ProductionRunner, verify_s5_production_identity
from .s5_graphiti_native_binding import load_graphiti_native_binding
from .s5_a0_result_finalizer import verify_s5_a0_result


EVENT_SCHEMA = "membind.paper-eval-v3.s5-pstar-controller-event.v1"
CHECKPOINT_SCHEMA = "membind.paper-eval-v3.s5-pstar-controller-checkpoint.v1"
_RUN_ID = re.compile(r"^s5-p-star-[0-9]{8}-[0-9]{3}$")
_RESULT_VERIFIER = Path(__file__).with_name("s5_pstar_result_finalizer.py")
_PROJECT = Path(__file__).resolve().parents[2]
_ROOT = _PROJECT.parent
_LEGACY = _ROOT / "membind-validation"
_LEGACY_SRC = _LEGACY / "src"
_DATASET = Path(
    "/data/predator/ly/Mem/data/raw/longmemeval-cleaned/longmemeval_s_cleaned.json"
)
_PRIVATE = {
    "api_key", "authorization", "body", "content", "credential", "episode",
    "group_id", "messages", "namespace", "password", "prompt", "raw_output",
    "raw_response", "request", "response", "secret",
}


class S5PStarControllerError(ValueError):
    """A P* chain binding or single-use lifecycle failed closed."""


def _fail(code: str) -> S5PStarControllerError:
    return S5PStarControllerError(code)


@dataclass(frozen=True)
class S5PStarControllerPaths:
    production_identity: Path
    production_identity_qualification: Path
    current_stage_pointer: Path
    preflight: Path
    authority: Path
    predecessor: Path
    consumption: Path
    controller_root: Path
    attempt_root: Path


@dataclass(frozen=True)
class S5PStarProductionPaths:
    controller: S5PStarControllerPaths
    env_file: Path
    dataset: Path = _DATASET
    frozen_split: Path = _LEGACY / "artifacts/dataset/frozen_split_v1_3.json"
    dataset_builder: Path = _LEGACY_SRC / "dataset.py"
    legacy_src: Path = _LEGACY_SRC


def _public(value: object) -> None:
    if isinstance(value, Mapping):
        for key, child in value.items():
            if not isinstance(key, str) or key.casefold() in _PRIVATE:
                raise _fail("private_controller_field")
            _public(child)
    elif isinstance(value, Sequence) and not isinstance(value, (str, bytes)):
        for child in value:
            _public(child)


def _read(path: Path, code: str) -> dict[str, Any]:
    try:
        value = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        raise _fail(code) from None
    if not isinstance(value, dict):
        raise _fail(code)
    return value


def _manifest(episodes: Sequence[S5EpisodeRef]) -> str:
    selected = tuple(episodes)
    if (
        len(selected) != 49
        or any(not isinstance(item, S5EpisodeRef) for item in selected)
        or [item.source_sequence for item in selected] != list(range(49))
    ):
        raise _fail("episode_manifest_invalid")
    return payload_sha256([
        {"source_sequence": item.source_sequence, "source_sha256": item.source_sha256}
        for item in selected
    ])


class _Evidence:
    def __init__(self, root: Path, run_id: str) -> None:
        self.root = Path(root)
        if self.root.exists():
            raise _fail("controller_attempt_exists")
        self.root.mkdir(parents=True)
        self.events_path = self.root / "events.jsonl"
        self.checkpoint_path = self.root / "checkpoint.json"
        self.run_id = run_id
        self.events: list[dict[str, object]] = []

    def append(self, event_type: str, **fields: object) -> None:
        event = {
            "schema_version": EVENT_SCHEMA,
            "event_sequence": len(self.events),
            "event_type": event_type,
            "run_id": self.run_id,
            **fields,
        }
        _public(event)
        append_jsonl_durable(
            self.events_path,
            {"event": event, "event_sha256": payload_sha256(event)},
        )
        self.events.append(event)

    def checkpoint(self, **fields: object) -> None:
        checkpoint = {
            "schema_version": CHECKPOINT_SCHEMA,
            "run_id": self.run_id,
            "event_count": len(self.events),
            **fields,
        }
        _public(checkpoint)
        checkpoint["checkpoint_sha256"] = payload_sha256(checkpoint)
        atomic_write_json(self.checkpoint_path, checkpoint)


def _preconsume(
    paths: S5PStarControllerPaths, episodes: Sequence[S5EpisodeRef]
) -> tuple[dict[str, Any], dict[str, Any], str]:
    if not isinstance(paths, S5PStarControllerPaths):
        raise _fail("controller_paths_invalid")
    if paths.consumption.exists() or paths.controller_root.exists() or paths.attempt_root.exists():
        raise _fail("single_use_output_exists")
    try:
        identity = verify_s5_production_identity(_read(paths.production_identity, "identity_invalid"))
        qualification = verify_s5_production_identity_qualification(
            _read(paths.production_identity_qualification, "qualification_invalid")
        )
        qualification_binding = bind_s5_production_identity_qualification(
            qualification, file_sha256=sha256_file(paths.production_identity_qualification)
        )
        preflight = verify_s5_live_preflight(_read(paths.preflight, "preflight_invalid"))
        authority = verify_s5_live_authority(_read(paths.authority, "authority_invalid"))
    except Exception:
        raise _fail("qualified_chain_invalid") from None
    pointer = _read(paths.current_stage_pointer, "pointer_invalid")
    pointer_payload = pointer.get("payload")
    run = authority["payload"].get("run")
    if (
        identity.get("method") != P_STAR
        or qualification_binding.get("method") != P_STAR
        or qualification_binding.get("production_identity_sha256") != identity.get("identity_sha256")
        or qualification_binding.get("production_identity_file_sha256") != sha256_file(paths.production_identity)
        or not isinstance(pointer_payload, Mapping)
        or pointer.get("payload_sha256") != payload_sha256(pointer_payload)
        or pointer_payload.get("current_stage") != "S3_CONFIGURATION_FROZEN"
        or qualification_binding.get("current_stage_pointer", {}).get("file_sha256") != sha256_file(paths.current_stage_pointer)
        or preflight["payload"].get("method") != P_STAR
        or authority["payload"].get("method") != P_STAR
        or authority["payload"].get("production_identity_qualification") != qualification_binding
        or authority["payload"].get("preflight_file_sha256") != sha256_file(paths.preflight)
        or authority["payload"].get("preflight_payload_sha256") != preflight.get("payload_sha256")
        or not isinstance(run, Mapping)
        or run.get("method") != P_STAR
        or run.get("configured_concurrency") != 2
        or run.get("source_manifest_sha256") != _manifest(episodes)
    ):
        raise _fail("qualified_chain_binding_invalid")
    run_id = str(run.get("run_id", ""))
    if _RUN_ID.fullmatch(run_id) is None or run.get("namespace") != f"pev3-{run_id}":
        raise _fail("run_identity_invalid")
    if any(getattr(item.native_episode, "group_id", None) != run["namespace"] for item in episodes):
        raise _fail("episode_namespace_binding_invalid")
    try:
        predecessor = verify_s5_a0_result(
            _read(paths.predecessor, "predecessor_invalid")
        )
    except Exception:
        raise _fail("predecessor_result_invalid") from None
    predecessor_payload = predecessor.get("payload")
    authority_predecessor = authority["payload"].get("predecessor")
    if (
        not isinstance(predecessor_payload, Mapping)
        or predecessor.get("payload_sha256") != payload_sha256(predecessor_payload)
        or predecessor_payload.get("method") != "A0"
        or predecessor_payload.get("verdict") != "PASS"
        or not isinstance(authority_predecessor, Mapping)
        or authority_predecessor.get("method") != "A0"
        or authority_predecessor.get("verdict") != "PASS"
        or authority_predecessor.get("result_file_sha256") != sha256_file(paths.predecessor)
        or authority_predecessor.get("result_payload_sha256") != predecessor.get("payload_sha256")
    ):
        raise _fail("predecessor_binding_invalid")
    sources = authority["payload"].get("source_sha256")
    if (
        not isinstance(sources, Mapping)
        or sources.get("controller") != sha256_file(Path(__file__))
        or sources.get("result_verifier") != sha256_file(_RESULT_VERIFIER)
    ):
        raise _fail("authority_source_drift")
    return identity, authority, sha256_file(paths.authority)


async def _await(value: Awaitable[Any] | Any) -> Any:
    return await value if inspect.isawaitable(value) else value


def _failure(stage: str, error: BaseException | str) -> dict[str, object]:
    error_class = error if isinstance(error, str) else f"{type(error).__module__}.{type(error).__qualname__}"
    return {
        "status": "incomplete_non_mergeable",
        "failure_stage": stage,
        "error_class": error_class,
        "scientific_outcome_candidate": False,
        "resume_authorized": False,
        "namespace_cleanup_authorized": False,
        "next_method_authorized": False,
        "current_stage_pointer_update_authorized": False,
    }


async def execute_s5_pstar_controller(
    *,
    paths: S5PStarControllerPaths,
    episodes: Sequence[S5EpisodeRef],
    git_commit: str,
    env_loader: Callable[[], Mapping[str, str]],
    runtime_factory: Callable[[Mapping[str, str]], object],
    readiness: Callable[[object], Awaitable[object] | object],
    binding_loader: Callable[[], object],
    runner_factory: Callable[..., object] = S5ProductionRunner,
    close_runtime: Callable[[object], Awaitable[object] | object],
) -> dict[str, object]:
    identity, authority, authority_file_sha = _preconsume(paths, episodes)
    run_id = str(authority["payload"]["run"]["run_id"])
    try:
        consumption = consume_s5_live_authority(
            authority=authority,
            authority_file_sha256=authority_file_sha,
            output_path=paths.consumption,
            git_commit=git_commit,
            run_id=f"{run_id}-authority-consumption",
        )
    except Exception:
        raise _fail("authority_consumption_failed") from None
    evidence = _Evidence(paths.controller_root, run_id)
    evidence.append(
        "authority_consumed", method=P_STAR,
        authority_file_sha256=authority_file_sha,
        authority_payload_sha256=authority["payload_sha256"],
        consumption_payload_sha256=consumption["payload_sha256"],
    )
    runtime: object | None = None
    stage = "runtime_construction"
    try:
        runtime = runtime_factory(env_loader())
        evidence.append("runtime_constructed", method=P_STAR)
        stage = "runtime_readiness"
        await _await(readiness(runtime))
        evidence.append("runtime_ready", method=P_STAR)
        stage = "native_execution"
        spec = S5MethodSpec(
            run_id=run_id,
            method=P_STAR,
            native_path_identity_sha256=str(identity["graphiti_native_source_sha256"]),
        )
        runner = runner_factory(
            attempt_root=paths.attempt_root,
            spec=spec,
            identity=identity,
            graphiti=getattr(runtime, "graphiti"),
            binding=binding_loader(),
            episodes=tuple(episodes),
        )
        evidence.append("native_runner_started", method=P_STAR)
        native = await _await(runner.run())
        stage = "runtime_close"
        selected_runtime, runtime = runtime, None
        await _await(close_runtime(selected_runtime))
        evidence.append("runtime_closed", method=P_STAR)
        native_status = native.get("status") if isinstance(native, Mapping) else None
        payload_status = native.get("payload", {}).get("status") if isinstance(native, Mapping) and isinstance(native.get("payload"), Mapping) else None
        valid = (native_status, payload_status) in {
            ("complete", "PASS"),
            ("scientific_outcome_complete", "SCIENTIFIC_OUTCOME_COMPLETE"),
        }
        if not valid:
            failure = _failure("native_execution", "paper_eval.s5_pstar_controller.NativeAttemptIncomplete")
            evidence.append("native_attempt_incomplete", method=P_STAR, error_class=failure["error_class"])
            evidence.checkpoint(**failure)
            return failure
        evidence.append(
            "raw_runner_evidence_complete", method=P_STAR,
            native_attempt_status=native_status,
            production_identity_sha256=identity["identity_sha256"],
        )
        result = {
            "status": "controller_complete_evidence_only",
            "native_attempt_status": native_status,
            "scientific_outcome_candidate": True,
            "resume_authorized": False,
            "namespace_cleanup_authorized": False,
            "next_method_authorized": False,
            "current_stage_pointer_update_authorized": False,
        }
        evidence.checkpoint(**result)
        return result
    except Exception as error:
        failure = _failure(stage, error)
        evidence.append("controller_failure", method=P_STAR, failure_stage=stage, error_class=failure["error_class"])
        evidence.checkpoint(**failure)
        return failure
    finally:
        if runtime is not None:
            try:
                await _await(close_runtime(runtime))
            except Exception:
                pass


def inspect_s5_pstar_controller_attempt(root: Path) -> dict[str, object]:
    try:
        records = [json.loads(line) for line in (Path(root) / "events.jsonl").read_text().splitlines()]
        checkpoint = _read(Path(root) / "checkpoint.json", "controller_checkpoint_invalid")
    except (OSError, UnicodeError, json.JSONDecodeError):
        raise _fail("controller_evidence_invalid") from None
    events: list[dict[str, object]] = []
    for record in records:
        if not isinstance(record, Mapping) or record.get("event_sha256") != payload_sha256(record.get("event", {})):
            raise _fail("controller_event_invalid")
        events.append(dict(record["event"]))
    seal = checkpoint.pop("checkpoint_sha256", None)
    if (
        not events
        or [event.get("event_sequence") for event in events] != list(range(len(events)))
        or checkpoint.get("event_count") != len(events)
        or seal != payload_sha256(checkpoint)
        or checkpoint.get("resume_authorized") is not False
        or checkpoint.get("next_method_authorized") is not False
    ):
        raise _fail("controller_evidence_invalid")
    checkpoint["checkpoint_sha256"] = seal
    return {"events": events, "checkpoint": checkpoint}


def _import_exact(module_name: str, path: Path) -> object:
    source_root = str(Path(path).resolve().parent)
    if source_root not in sys.path:
        sys.path.insert(0, source_root)
    module = importlib.import_module(module_name)
    if Path(str(getattr(module, "__file__", ""))).resolve() != Path(path).resolve():
        raise _fail(f"{module_name}_source_drift")
    return module


def _production_episodes(paths: S5PStarProductionPaths, namespace: str) -> tuple[S5EpisodeRef, ...]:
    from .s1_live import load_fixed_history

    history = load_fixed_history(paths.dataset, paths.frozen_split)
    builder_module = _import_exact("dataset", paths.dataset_builder)
    builder = getattr(builder_module, "build_episodes", None)
    if not callable(builder):
        raise _fail("dataset_builder_missing")
    native = tuple(builder(dict(history)))
    refs: list[S5EpisodeRef] = []
    from dataclasses import replace
    for index, episode in enumerate(native):
        rebound = replace(episode, group_id=namespace)
        refs.append(S5EpisodeRef(index, str(getattr(rebound, "source_hash", "")), rebound))
    _manifest(refs)
    return tuple(refs)


def _consumed_checker(paths: S5PStarControllerPaths, authority: Mapping[str, Any]):
    def check(action: object) -> object:
        action_name = getattr(action, "value", action)
        if action_name != "native_characterization_c0":
            raise _fail("runtime_live_action_invalid")
        from .s5_live_authority import verify_s5_live_authority_consumption
        consumption = verify_s5_live_authority_consumption(
            _read(paths.consumption, "consumption_invalid")
        )
        payload = consumption["payload"]
        if payload.get("method") != P_STAR or payload.get("run") != authority["payload"].get("run"):
            raise _fail("consumption_binding_invalid")
        return {"status": "S5_AUTHORITY_CONSUMED"}
    return check


async def execute_s5_pstar_production(
    *, paths: S5PStarProductionPaths, git_commit: str,
) -> dict[str, object]:
    if not isinstance(paths, S5PStarProductionPaths):
        raise _fail("production_paths_invalid")
    authority = verify_s5_live_authority(_read(paths.controller.authority, "authority_invalid"))
    run = authority["payload"]["run"]
    episodes = _production_episodes(paths, str(run["namespace"]))

    def env_loader() -> Mapping[str, str]:
        module = _import_exact("graphiti_native", paths.legacy_src / "graphiti_native.py")
        loaded = module.load_env_file(paths.env_file)
        if not isinstance(loaded, Mapping):
            raise _fail("environment_invalid")
        return dict(loaded)

    def runtime_factory(_env: Mapping[str, str]) -> object:
        module = _import_exact(
            "native_characterization_runtime",
            paths.legacy_src / "native_characterization_runtime.py",
        )
        return module.build_u0_graphiti_from_env(
            authorization_checker=_consumed_checker(paths.controller, authority),
            live_action="native_characterization_c0",
            env_loader=lambda: None,
            structured_output_mode="json_schema",
        )

    async def readiness(runtime: object) -> None:
        graphiti = getattr(runtime, "graphiti", None)
        driver = getattr(graphiti, "driver", None)
        task = getattr(driver, "_init_task", None)
        if task is not None:
            await _await(task)
        else:
            await _await(driver.build_indices_and_constraints())

    async def close(runtime: object) -> None:
        graphiti = getattr(runtime, "graphiti", None)
        closer = getattr(graphiti, "close", None) or getattr(getattr(graphiti, "driver", None), "close", None)
        if not callable(closer):
            raise _fail("runtime_close_missing")
        await _await(closer())

    return await execute_s5_pstar_controller(
        paths=paths.controller, episodes=episodes, git_commit=git_commit,
        env_loader=env_loader, runtime_factory=runtime_factory, readiness=readiness,
        binding_loader=load_graphiti_native_binding, close_runtime=close,
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Execute one qualified S5 P*(C=2) smoke")
    for name in ("production-identity", "production-identity-qualification", "preflight", "authority", "predecessor", "run-root"):
        parser.add_argument(f"--{name}", type=Path, required=True)
    parser.add_argument("--git-commit", required=True)
    parser.add_argument("--current-stage-pointer", type=Path, default=_PROJECT / "runtime/CURRENT_STAGE_STATUS.json")
    parser.add_argument("--env-file", type=Path, default=_LEGACY / ".env")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    root = Path(args.run_root)
    controller = S5PStarControllerPaths(
        production_identity=args.production_identity,
        production_identity_qualification=args.production_identity_qualification,
        current_stage_pointer=args.current_stage_pointer,
        preflight=args.preflight, authority=args.authority, predecessor=args.predecessor,
        consumption=root / "authority_consumption.json",
        controller_root=root / "controller", attempt_root=root / "attempt",
    )
    try:
        result = asyncio.run(execute_s5_pstar_production(
            paths=S5PStarProductionPaths(controller=controller, env_file=args.env_file),
            git_commit=str(args.git_commit),
        ))
    except Exception as error:
        print(json.dumps({"status": "error", "error_class": type(error).__name__}, sort_keys=True), file=sys.stderr)
        return 1
    print(json.dumps(result, sort_keys=True))
    return 0 if result.get("status") == "controller_complete_evidence_only" else 2


__all__ = [
    "S5PStarControllerError", "S5PStarControllerPaths",
    "S5PStarProductionPaths", "build_parser", "execute_s5_pstar_controller",
    "execute_s5_pstar_production", "inspect_s5_pstar_controller_attempt", "main",
]


if __name__ == "__main__":
    raise SystemExit(main())
