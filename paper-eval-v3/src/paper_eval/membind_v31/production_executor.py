"""Production executor factory and source-bound three-episode smoke adapter.

Only the sealed ``DEVELOPMENT_EXPOSED_4`` artifact is loaded.  Formal blocks
delegate unchanged to ``execute_v31_live_block``.  Smoke uses the same runtime,
State-Cut, source-log, Graphiti adapter, request admission, and coordinator,
but persists a separate self-verifying three-source plan and namespace.
"""

from __future__ import annotations

import asyncio
import importlib.util
import inspect
import json
import math
import sys
import time
from collections.abc import Awaitable, Callable, Mapping, Sequence
from copy import deepcopy
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from paper_eval.artifacts import (
    append_jsonl_durable,
    atomic_write_json,
    payload_sha256,
    sha256_file,
)
from paper_eval.membind_v1.graphiti_factories import build_source_log_from_episodes
from paper_eval.membind_v31.admission import AdmissionPolicy
from paper_eval.membind_v31.certification import StateCutCertification
from paper_eval.membind_v31.coordinator import run_membind_v31_stream
from paper_eval.membind_v31.freezer import (
    V31FreezePaths,
    load_v31_state_cut_certification,
)
from paper_eval.membind_v31.live_block import (
    V31LiveHooks,
    execute_v31_live_block,
    production_v31_live_hooks,
)
from paper_eval.membind_v31.materialization import inspect_materialized_control
from paper_eval.membind_v31.orchestration import OrchestrationHooks, SmokeSpec
from paper_eval.membind_v31.prepared_artifact import PreparedArtifact


HISTORIES = ("07741c45", "b6019101", "6071bd76", "a2f3aa27")
SMOKE_PLAN_SCHEMA = "membind.paper-eval-v3.membind-v31-source-bound-smoke-plan.v1"
SMOKE_RESULT_SCHEMA = "membind.paper-eval-v3.membind-v31-smoke-result.v1"


class ProductionExecutorError(ValueError):
    """A production input, source identity, or smoke boundary is invalid."""


def _fail(code: str) -> ProductionExecutorError:
    return ProductionExecutorError(code)


def _sealed(value: Mapping[str, object], code: str) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise _fail(code)
    selected = deepcopy(dict(value))
    stored = selected.get("payload_sha256")
    body = {key: child for key, child in selected.items() if key != "payload_sha256"}
    if not isinstance(stored, str) or stored != payload_sha256(body):
        raise _fail(code)
    return selected


def _seal(body: Mapping[str, object]) -> dict[str, Any]:
    result = deepcopy(dict(body))
    result["payload_sha256"] = payload_sha256(result)
    return result


def _read_json(path: Path, code: str) -> dict[str, Any]:
    try:
        value = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        raise _fail(code) from None
    if not isinstance(value, dict):
        raise _fail(code)
    return value


def _plan_shape(value: Mapping[str, object]) -> dict[str, Any]:
    plan = _sealed(value, "method plan invalid")
    if (
        plan.get("data_role") != "DEVELOPMENT_EXPOSED"
        or tuple(plan.get("histories", ())) != HISTORIES
        or plan.get("global_llm_admission_k") != 2
        or plan.get("compile_workers") != 2
        or plan.get("lookahead") != 2
        or not isinstance(plan.get("history_source_sha256s"), Mapping)
        or not isinstance(plan.get("arrival_traces"), Mapping)
        or not isinstance(plan.get("blocks"), list)
        or len(plan["blocks"]) != 6
    ):
        raise _fail("method plan invalid")
    return plan


@dataclass(frozen=True, slots=True)
class ProductionExecutorPaths:
    repository_root: Path
    project_root: Path
    legacy_root: Path
    control_root: Path
    development_input: Path
    env_file: Path
    freeze_paths: V31FreezePaths

    @classmethod
    def from_repository(cls, repository_root: Path) -> "ProductionExecutorPaths":
        root = Path(repository_root).resolve()
        project = root / "paper-eval-v3"
        legacy = root / "membind-validation"
        return cls(
            repository_root=root,
            project_root=project,
            legacy_root=legacy,
            control_root=project / "artifacts/paper_eval/membind_v31",
            development_input=(
                project
                / "artifacts/paper_eval/development_inputs/"
                "LONGMEMEVAL_S_DEVELOPMENT_EXPOSED_4.json"
            ),
            env_file=legacy / ".env",
            freeze_paths=V31FreezePaths.from_repository(root),
        )


def _default_episode_builder(path: Path) -> Callable[[dict[str, Any]], Sequence[object]]:
    module_path = Path(path) / "src/dataset.py"
    spec = importlib.util.spec_from_file_location("membind_v31_production_dataset", module_path)
    if spec is None or spec.loader is None:
        raise _fail("episode renderer unavailable")
    module = importlib.util.module_from_spec(spec)
    module_name = spec.name
    had_previous = module_name in sys.modules
    previous = sys.modules.get(module_name)
    # importlib loaders and Python 3.12 dataclasses expect self-registration.
    sys.modules[module_name] = module
    try:
        spec.loader.exec_module(module)
    except Exception:
        raise _fail("episode renderer unavailable") from None
    finally:
        if had_previous:
            sys.modules[module_name] = previous
        else:
            sys.modules.pop(module_name, None)
    builder = getattr(module, "build_episodes", None)
    if not callable(builder):
        raise _fail("episode renderer unavailable")
    return builder


def load_development_episodes(
    *,
    development_input: Path,
    verified_plan: Mapping[str, object],
    episode_builder: Callable[[dict[str, Any]], Sequence[object]],
) -> dict[str, tuple[object, ...]]:
    """Load exactly four sealed exposed histories; never scan the raw dataset."""

    if not callable(episode_builder):
        raise _fail("episode builder invalid")
    plan = _plan_shape(verified_plan)
    artifact = _sealed(
        _read_json(development_input, "development input unreadable"),
        "development input invalid",
    )
    records = artifact.get("records")
    if (
        artifact.get("data_role") != "DEVELOPMENT_EXPOSED"
        or artifact.get("selection_policy")
        != "EXACT_FROZEN_DEVELOPMENT_HISTORIES_ONLY"
        or tuple(artifact.get("history_order", ())) != HISTORIES
        or isinstance(records, (str, bytes))
        or not isinstance(records, Sequence)
        or tuple(
            record.get("question_id") if isinstance(record, Mapping) else None
            for record in records
        )
        != HISTORIES
    ):
        raise _fail("development history inventory invalid")
    sources = plan["history_source_sha256s"]
    result: dict[str, tuple[object, ...]] = {}
    for history, raw_record in zip(HISTORIES, records, strict=True):
        if not isinstance(raw_record, Mapping):
            raise _fail("development history invalid")
        try:
            episodes = tuple(episode_builder(deepcopy(dict(raw_record))))
        except Exception:
            raise _fail("episode rendering failed") from None
        expected = sources.get(history)
        observed = [getattr(episode, "source_hash", None) for episode in episodes]
        if (
            not isinstance(expected, list)
            or not episodes
            or observed != expected
            or artifact.get("episode_counts", {}).get(history) != len(episodes)
        ):
            raise _fail("development source identity mismatch")
        result[history] = episodes
    return result


def build_source_bound_smoke_plan(
    *,
    smoke_spec: SmokeSpec,
    verified_plan: Mapping[str, object],
    episodes: Sequence[object],
) -> dict[str, Any]:
    """Build a self-verifying plan whose namespace and sources equal runtime use."""

    if not isinstance(smoke_spec, SmokeSpec):
        raise _fail("smoke spec invalid")
    plan = _plan_shape(verified_plan)
    if (
        smoke_spec.plan_payload_sha256 != plan["payload_sha256"]
        or smoke_spec.block_index != 0
        or smoke_spec.method != "MemBind"
        or smoke_spec.history_id != HISTORIES[0]
        or smoke_spec.source_sequences != (0, 1, 2)
        or smoke_spec.global_llm_admission_k != 2
        or isinstance(episodes, (str, bytes))
        or not isinstance(episodes, Sequence)
        or len(episodes) != 3
    ):
        raise _fail("smoke source-bound identity invalid")
    block = plan["blocks"][0]
    if block.get("method") != "MemBind" or block.get("history_id") != HISTORIES[0]:
        raise _fail("smoke parent block invalid")
    expected_sources = list(plan["history_source_sha256s"][HISTORIES[0]][:3])
    observed_sources = [getattr(episode, "source_hash", None) for episode in episodes]
    if observed_sources != expected_sources:
        raise _fail("smoke source prefix mismatch")
    offsets = list(plan["arrival_traces"][HISTORIES[0]]["arrival_offsets_ns"][:3])
    if len(offsets) != 3 or offsets[0] != 0:
        raise _fail("smoke arrival trace invalid")
    smoke_cache_salt = payload_sha256(
        {
            "attempt_id": smoke_spec.attempt_id,
            "namespace": smoke_spec.namespace,
            "parent_cache_salt_sha256": block["cache_salt_sha256"],
            "purpose": "THREE_EPISODE_SMOKE",
        }
    )
    return _seal(
        {
            "schema_version": SMOKE_PLAN_SCHEMA,
            "status": "AUTHORIZED",
            "attempt_id": smoke_spec.attempt_id,
            "parent_plan_payload_sha256": plan["payload_sha256"],
            "parent_block_index": 0,
            "parent_namespace": block["namespace"],
            "formal_namespace_reused": False,
            "method": "MemBind",
            "policy": "FRONTIER_FIRST_CACHE_AFFINITY",
            "history_id": HISTORIES[0],
            "namespace": smoke_spec.namespace,
            "source_sequences": [0, 1, 2],
            "source_count": 3,
            "source_sha256s": expected_sources,
            "arrival_offsets_ns": offsets,
            "shared_execution_envelope_sha256": plan[
                "shared_execution_envelope_sha256"
            ],
            "global_llm_admission_k": 2,
            "compile_workers": 2,
            "lookahead": 2,
            "cache_salt_sha256": smoke_cache_salt,
        }
    )


def _snapshot(value: object, code: str) -> dict[str, object]:
    if not isinstance(value, Mapping):
        raise _fail(code)
    names = value.get("episode_names")
    if isinstance(names, (str, bytes)) or not isinstance(names, Sequence):
        raise _fail(code)
    try:
        nodes = int(value.get("node_count", 0))
        relationships = int(value.get("relationship_count", 0))
    except (TypeError, ValueError):
        raise _fail(code) from None
    if nodes < 0 or relationships < 0:
        raise _fail(code)
    return {
        "node_count": nodes,
        "relationship_count": relationships,
        "episode_names": sorted(str(name) for name in names),
    }


async def _await(value: object, code: str) -> object:
    if not inspect.isawaitable(value):
        raise _fail(code)
    return await value


def _append_public(path: Path, row: Mapping[str, object], schema: str) -> None:
    body = {"schema_version": schema, "row": deepcopy(dict(row))}
    append_jsonl_durable(path, {"record": body, "record_sha256": payload_sha256(body)})


async def execute_v31_three_episode_smoke(
    *,
    smoke_spec: SmokeSpec,
    verified_plan: Mapping[str, object],
    episodes: Sequence[object],
    env: Mapping[str, str],
    smoke_root: Path,
    state_cut_certification: StateCutCertification,
    hooks: V31LiveHooks | None = None,
    coordinator: Callable[..., Awaitable[Mapping[str, object]]] = run_membind_v31_stream,
) -> dict[str, object]:
    """Execute the real three-source path under one source-bound smoke plan."""

    smoke_plan = build_source_bound_smoke_plan(
        smoke_spec=smoke_spec, verified_plan=verified_plan, episodes=episodes
    )
    if not isinstance(env, Mapping) or any(
        not isinstance(key, str) or not isinstance(value, str) for key, value in env.items()
    ):
        raise _fail("environment invalid")
    if not isinstance(state_cut_certification, StateCutCertification):
        raise _fail("state-cut certification invalid")
    try:
        certification = state_cut_certification.verify()
    except ValueError:
        raise _fail("state-cut certification invalid") from None
    if not callable(coordinator):
        raise _fail("coordinator invalid")
    selected_hooks = production_v31_live_hooks() if hooks is None else hooks
    if not isinstance(selected_hooks, V31LiveHooks):
        raise _fail("live hooks invalid")
    root = Path(smoke_root)
    if not root.is_dir():
        raise _fail("smoke root missing")
    plan_path = root / "SMOKE_EXECUTION_PLAN.json"
    if plan_path.exists():
        raise _fail("smoke plan already exists")
    atomic_write_json(plan_path, smoke_plan)
    prepared_root = root / "private/prepared"
    prepared_root.mkdir(parents=True, exist_ok=False)
    namespace = smoke_plan["namespace"]
    try:
        scoped = tuple(
            selected_hooks.namespace_episode(episode, namespace) for episode in episodes
        )
        source_log, raw_hashes = build_source_log_from_episodes(
            scoped,
            namespace=namespace,
            reference_time_to_ns=selected_hooks.reference_time_to_ns,
        )
    except Exception:
        raise _fail("smoke source-log materialization failed") from None
    if list(raw_hashes) != smoke_plan["source_sha256s"]:
        raise _fail("smoke source-log identity mismatch")
    prepared_count = 0
    visibility_count = 0
    lifecycle_count = 0

    def request_observer(row: dict[str, object]) -> None:
        _append_public(
            root / "llm.jsonl",
            row,
            "membind.paper-eval-v3.membind-v31-smoke-llm.v1",
        )

    def persist_prepared(artifact: PreparedArtifact) -> None:
        nonlocal prepared_count
        if not isinstance(artifact, PreparedArtifact):
            raise _fail("smoke prepared artifact invalid")
        artifact.verify(
            expected_source_sha256=source_log.record(
                artifact.source_sequence
            ).source_sha256,
            expected_certification_sha256=certification.certification_sha256,
        )
        target = prepared_root / f"{artifact.source_sequence:08d}.json"
        if target.exists():
            raise _fail("smoke prepared artifact duplicate")
        atomic_write_json(target, artifact.to_document())
        prepared_count += 1

    lifecycle_map = {
        "arrival": "ARRIVAL",
        "arrival_failure": "TERMINAL_FAILURE",
        "compile_start": "COMPILE_STARTED",
        "prepared_durable": "PREPARED_DURABLE",
        "bind_start": "BIND_STARTED",
        "compile_failure": "TERMINAL_FAILURE",
        "bind_failure": "TERMINAL_FAILURE",
    }

    def lifecycle(row: dict[str, object]) -> None:
        nonlocal lifecycle_count
        event_type = lifecycle_map.get(str(row.get("event_type")))
        if event_type is None:
            return
        body = {
            "event_sequence": lifecycle_count,
            "event_type": event_type,
            "source_sequence": int(row["source_sequence"]),
            "timestamp_ns": int(row["timestamp_ns"]),
        }
        append_jsonl_durable(
            root / "events.jsonl",
            {"event": body, "event_sha256": payload_sha256(body)},
        )
        lifecycle_count += 1

    runtime: object | None = None
    try:
        runtime = selected_hooks.runtime_builder(
            env={**dict(env), "CONSTRUCTION_CACHE_SALT": smoke_plan["cache_salt_sha256"]},
            policy=AdmissionPolicy.CACHE_AFFINE,
            request_id_prefix=f"v31-smoke-{smoke_spec.attempt_id}",
            observer=request_observer,
        )
        if inspect.isawaitable(runtime):
            raise _fail("smoke runtime builder must be synchronous")
        if getattr(runtime, "shared_execution_envelope_sha256", None) != smoke_plan[
            "shared_execution_envelope_sha256"
        ]:
            raise _fail("smoke execution envelope mismatch")
        await _await(selected_hooks.runtime_ready(runtime), "smoke runtime ready must be async")
        initial = _snapshot(
            await _await(
                selected_hooks.namespace_probe(runtime, namespace),
                "smoke namespace probe must be async",
            ),
            "smoke initial namespace invalid",
        )
        if initial != {"node_count": 0, "relationship_count": 0, "episode_names": []}:
            raise _fail("smoke namespace not fresh")
        adapter = selected_hooks.adapter_factory(runtime, certification)

        async def visibility(sequence: int, _result: object) -> bool:
            nonlocal visibility_count
            value = await _await(
                selected_hooks.source_visibility_probe(runtime, source_log.record(sequence)),
                "smoke visibility probe must be async",
            )
            if value is True:
                visibility_count += 1
            return value is True

        def commit(sequence: int, _result: object) -> None:
            nonlocal lifecycle_count
            body = {
                "event_sequence": lifecycle_count,
                "event_type": "COMMIT_RETURNED",
                "source_sequence": sequence,
                "timestamp_ns": time.monotonic_ns(),
            }
            append_jsonl_durable(
                root / "events.jsonl",
                {"event": body, "event_sha256": payload_sha256(body)},
            )
            lifecycle_count += 1

        def publication(sequence: int, _result: object) -> None:
            nonlocal lifecycle_count
            body = {
                "event_sequence": lifecycle_count,
                "event_type": "PUBLICATION_DURABLE",
                "source_sequence": sequence,
                "timestamp_ns": time.monotonic_ns(),
                "visibility_confirmed": True,
            }
            append_jsonl_durable(
                root / "events.jsonl",
                {"event": body, "event_sha256": payload_sha256(body)},
            )
            lifecycle_count += 1

        result = await coordinator(
            stream_id=smoke_spec.history_id,
            source_log=source_log,
            arrival_offsets_ns=tuple(smoke_plan["arrival_offsets_ns"]),
            adapter=adapter,
            request_client=getattr(runtime, "admitted_llm"),
            compile_workers=2,
            lookahead=2,
            observer=lifecycle,
            publication_probe=visibility,
            prepared_persistor=persist_prepared,
            commit_observer=commit,
            publication_persistor=publication,
        )
        publications = result.get("publication_source_sequences")
        violations = result.get("direct_violation_count")
        if publications != [0, 1, 2] or violations != 0:
            raise _fail("smoke coordinator contract invalid")
        final = _snapshot(
            await _await(
                selected_hooks.namespace_probe(runtime, namespace),
                "smoke namespace probe must be async",
            ),
            "smoke final namespace invalid",
        )
        expected_names = sorted(str(getattr(episode, "name")) for episode in scoped)
        if final["episode_names"] != expected_names:
            raise _fail("smoke final namespace coverage invalid")
        admission = getattr(runtime, "admitted_llm").observation()
        observed = admission.get("observed_max_inflight")
        if isinstance(observed, bool) or not isinstance(observed, int) or not 0 <= observed <= 2:
            raise _fail("smoke request admission invalid")
        if prepared_count != 3 or visibility_count != 3:
            raise _fail("smoke evidence coverage invalid")
        return _seal(
            {
                "schema_version": SMOKE_RESULT_SCHEMA,
                "status": "PASS",
                "attempt_id": smoke_spec.attempt_id,
                "plan_payload_sha256": smoke_spec.plan_payload_sha256,
                "source_bound_smoke_plan_payload_sha256": smoke_plan["payload_sha256"],
                "method": "MemBind",
                "history_id": smoke_spec.history_id,
                "namespace": namespace,
                "source_sequences": [0, 1, 2],
                "source_count": 3,
                "global_llm_admission_k": 2,
                "observed_max_inflight": observed,
                "verified_prepared_artifact_count": prepared_count,
                "publication_source_sequences": [0, 1, 2],
                "visibility_confirmed_count": visibility_count,
                "direct_violation_count": 0,
                "initial_namespace": initial,
                "final_namespace": final,
            }
        )
    finally:
        if runtime is not None:
            await _await(
                selected_hooks.close_runtime(runtime),
                "smoke runtime close must be async",
            )


def _default_control_plan(path: Path) -> dict[str, Any]:
    try:
        return inspect_materialized_control(path)["method_plan"]
    except ValueError:
        # The live plan intentionally precedes baseline acceptance/merge commit.
        return _plan_shape(_read_json(Path(path) / "V31_METHOD_PLAN.json", "method plan unavailable"))


def _default_env_loader(path: Path) -> Mapping[str, str]:
    module_path = path.parent / "src/graphiti_native.py"
    spec = importlib.util.spec_from_file_location("membind_v31_graphiti_native", module_path)
    if spec is None or spec.loader is None:
        raise _fail("env loader unavailable")
    module = importlib.util.module_from_spec(spec)
    # ``graphiti_native.py`` imports sibling legacy modules (dataset,
    # instrumentation, ...).  Loading it by file location does not otherwise
    # populate that directory on ``sys.path``; scope the temporary insertion
    # to this import and restore the caller's path exactly afterwards.
    source_root = str(module_path.parent)
    had_path = source_root in sys.path
    if not had_path:
        sys.path.insert(0, source_root)
    try:
        spec.loader.exec_module(module)
        result = module.load_env_file(path)
    except Exception:
        raise _fail("env load failed") from None
    finally:
        if not had_path:
            try:
                sys.path.remove(source_root)
            except ValueError:
                pass
    if not isinstance(result, Mapping):
        raise _fail("env load failed")
    return dict(result)


@dataclass(frozen=True, slots=True)
class ProductionExecutorDependencies:
    load_control_plan: Callable[[Path], Mapping[str, object]]
    load_env: Callable[[Path], Mapping[str, str]]
    load_certification: Callable[[V31FreezePaths], StateCutCertification]
    load_episodes: Callable[[Path, Mapping[str, object]], Mapping[str, Sequence[object]]]
    execute_smoke: Callable[..., Awaitable[Mapping[str, object]]]
    execute_block: Callable[..., Awaitable[Mapping[str, object]]]


def _default_dependencies(paths: ProductionExecutorPaths) -> ProductionExecutorDependencies:
    builder = _default_episode_builder(paths.legacy_root)
    return ProductionExecutorDependencies(
        load_control_plan=_default_control_plan,
        load_env=_default_env_loader,
        load_certification=load_v31_state_cut_certification,
        load_episodes=lambda path, plan: load_development_episodes(
            development_input=path,
            verified_plan=plan,
            episode_builder=builder,
        ),
        execute_smoke=execute_v31_three_episode_smoke,
        execute_block=execute_v31_live_block,
    )


def build_production_executor_hooks(
    *,
    paths: ProductionExecutorPaths | None = None,
    dependencies: ProductionExecutorDependencies | None = None,
) -> OrchestrationHooks:
    """Build synchronous orchestration hooks around the async production units."""

    selected_paths = (
        ProductionExecutorPaths.from_repository(Path(__file__).resolve().parents[4])
        if paths is None
        else paths
    )
    if not isinstance(selected_paths, ProductionExecutorPaths):
        raise _fail("production paths invalid")
    selected_dependencies = (
        _default_dependencies(selected_paths) if dependencies is None else dependencies
    )
    if not isinstance(selected_dependencies, ProductionExecutorDependencies):
        raise _fail("production dependencies invalid")
    plan = _plan_shape(selected_dependencies.load_control_plan(selected_paths.control_root))
    executor_identity = payload_sha256(
        {
            "implementation_sha256": sha256_file(Path(__file__)),
            "live_block_sha256": sha256_file(
                selected_paths.project_root / "src/paper_eval/membind_v31/live_block.py"
            ),
            "method_plan_payload_sha256": plan["payload_sha256"],
            "development_input_path": str(
                selected_paths.development_input.relative_to(selected_paths.repository_root)
            ),
            "env_path": str(selected_paths.env_file.relative_to(selected_paths.repository_root)),
        }
    )
    loaded: dict[str, object] = {}

    def context() -> tuple[Mapping[str, str], StateCutCertification, Mapping[str, Sequence[object]]]:
        if not loaded:
            env = selected_dependencies.load_env(selected_paths.env_file)
            certification = selected_dependencies.load_certification(
                selected_paths.freeze_paths
            )
            episodes = selected_dependencies.load_episodes(
                selected_paths.development_input, plan
            )
            if (
                not isinstance(env, Mapping)
                or any(not isinstance(key, str) or not isinstance(value, str) for key, value in env.items())
                or not isinstance(certification, StateCutCertification)
                or tuple(episodes) != HISTORIES
            ):
                raise _fail("production context invalid")
            loaded.update(env=dict(env), certification=certification, episodes=episodes)
        return loaded["env"], loaded["certification"], loaded["episodes"]  # type: ignore[return-value]

    def run_smoke(smoke_spec: SmokeSpec, root: Path) -> Mapping[str, object]:
        env, certification, episodes = context()
        if smoke_spec.plan_payload_sha256 != plan["payload_sha256"]:
            raise _fail("smoke plan binding invalid")
        return asyncio.run(
            selected_dependencies.execute_smoke(
                smoke_spec=smoke_spec,
                verified_plan=plan,
                episodes=episodes[smoke_spec.history_id][:3],
                env=env,
                smoke_root=Path(root),
                state_cut_certification=certification,
            )
        )

    def run_block(
        supplied_plan: Mapping[str, object], block_index: int, root: Path
    ) -> Mapping[str, object]:
        env, certification, episodes = context()
        selected = _plan_shape(supplied_plan)
        if selected != plan:
            raise _fail("formal plan binding invalid")
        if isinstance(block_index, bool) or not isinstance(block_index, int) or not 0 <= block_index < 6:
            raise _fail("formal block index invalid")
        block = plan["blocks"][block_index]
        return asyncio.run(
            selected_dependencies.execute_block(
                verified_plan=plan,
                block_index=block_index,
                episodes=episodes[block["history_id"]],
                env=env,
                block_root=Path(root),
                state_cut_certification=certification,
                compile_workers=plan["compile_workers"],
                lookahead=plan["lookahead"],
            )
        )

    return OrchestrationHooks(
        executor_identity_sha256=executor_identity,
        run_smoke=run_smoke,
        run_block=run_block,
    )


def build_hooks() -> OrchestrationHooks:
    """CLI entrypoint used as ``paper_eval.membind_v31.production_executor:build_hooks``."""

    return build_production_executor_hooks()


__all__ = [
    "ProductionExecutorDependencies",
    "ProductionExecutorError",
    "ProductionExecutorPaths",
    "build_hooks",
    "build_production_executor_hooks",
    "build_source_bound_smoke_plan",
    "execute_v31_three_episode_smoke",
    "load_development_episodes",
]
