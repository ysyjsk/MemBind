"""Production boundary adapter for the frozen C4/E3 live replay.

This module does not define a new experiment. It loads the already-authorized
C4 schedule and data from CURRENT_STATE.json, constructs one fresh U0 Graphiti
runtime per frozen block, and delegates scheduling/checkpointing to
native_characterization_c4_runner.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import re
import sys
import time
from dataclasses import dataclass, replace
from pathlib import Path, PurePosixPath
from typing import Any, Callable, Mapping, Sequence

import current_state_gate
import dataset
import native_characterization_c4 as c4
import native_characterization_c4_artifacts as c4_artifacts
import native_characterization_c4_runner as c4_runner
from graphiti_core.utils.maintenance.graph_data_operations import clear_data
from graphiti_native import graphiti_episode_kwargs
from native_characterization_runtime import REQUESTED_MAX_TOKENS, build_u0_graphiti_from_env


VALIDATION_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_STATE_PATH = VALIDATION_ROOT / "CURRENT_STATE.json"
FROZEN_HISTORY_ID = "07741c45"
FROZEN_EPISODE_COUNT = 49
FROZEN_SPLIT_PATH = "artifacts/dataset/frozen_split_v1_3.json"
RUNS_ROOT = "artifacts/native_characterization/runs"
_C4_NAMESPACE_RE = re.compile(r"^nc-e3-[0-9a-f]{16}$")
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_FORBIDDEN_KEYS = {
    "api_key",
    "authorization",
    "body",
    "content",
    "credentials",
    "episode_body",
    "message",
    "messages",
    "password",
    "prompt",
    "raw_prompt",
    "raw_response",
    "request",
    "response",
    "secret",
    "token",
}
_SECRET_VALUE_RE = re.compile(
    r"(?i)(?:bearer\s+\S+|(?:api[-_]?key|authorization|password|secret)\s*[=:]\s*\S+)"
)


class C4LiveAdapterError(RuntimeError):
    """Sanitized C4 adapter failure with the only allowed token envelope."""

    def __init__(self, code: str, *, token_envelope: Mapping[str, Any] | None = None) -> None:
        super().__init__(code)
        self.code = code
        self.token_envelope = _token_envelope(token_envelope)


@dataclass(frozen=True)
class C4LiveDependencies:
    """Injectable boundaries for offline tests and the production CLI."""

    gate_checker: Callable[..., current_state_gate.GateDecision] = current_state_gate.require_live_action
    state_loader: Callable[[Path], Mapping[str, Any]] | None = None
    raw_dataset_loader: Callable[[Path], list[dict[str, Any]]] = dataset.load_json_records
    episode_builder: Callable[[dict[str, Any]], Sequence[dataset.Episode]] = dataset.build_episodes
    runtime_builder: Callable[..., Any] = build_u0_graphiti_from_env
    run_c4: Callable[..., Any] = c4_runner.run_c4_live


class _MonotonicAsyncClock:
    def now_ns(self) -> int:
        return time.monotonic_ns()

    async def sleep_until_ns(self, timestamp_ns: int) -> None:
        delay = max(0, (timestamp_ns - self.now_ns()) / 1_000_000_000)
        if delay:
            await asyncio.sleep(delay)


def _sha256(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _sha256_file(path: Path) -> str:
    return _sha256(path.read_bytes())


def _token_envelope(value: Mapping[str, Any] | None = None) -> dict[str, int | None]:
    supplied = value if isinstance(value, Mapping) else {}
    return c4_artifacts.nullable_token_envelope(
        prompt_tokens=supplied.get("prompt_tokens"),
        output_tokens=supplied.get("output_tokens"),
        requested_max_tokens=supplied.get("requested_max_tokens", REQUESTED_MAX_TOKENS),
    )


def _fail(code: str) -> C4LiveAdapterError:
    return C4LiveAdapterError(code)


def _safe_relative(value: Any, code: str) -> str:
    if not isinstance(value, str) or not value or "\\" in value:
        raise _fail(code)
    pure = PurePosixPath(value)
    if (
        pure.is_absolute()
        or pure.as_posix() != value
        or any(part in {"", ".", ".."} for part in pure.parts)
    ):
        raise _fail(code)
    return value


def _resolve_under(validation_root: Path, relative: Any, code: str) -> Path:
    safe = _safe_relative(relative, code)
    candidate = validation_root
    try:
        for part in PurePosixPath(safe).parts:
            candidate = candidate / part
            if candidate.is_symlink():
                raise _fail(code)
        resolved = candidate.resolve(strict=True)
        resolved.relative_to(validation_root)
    except C4LiveAdapterError:
        raise
    except (OSError, RuntimeError, ValueError):
        raise _fail(code) from None
    if not resolved.is_file():
        raise _fail(code)
    return resolved


def _read_json(path: Path, code: str) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="ascii"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        raise _fail(code) from None
    if not isinstance(value, dict):
        raise _fail(code)
    return value


def _load_state(path: Path, dependencies: C4LiveDependencies) -> Mapping[str, Any]:
    state = (
        dependencies.state_loader(path)
        if dependencies.state_loader is not None
        else _read_json(path, "state_invalid")
    )
    if not isinstance(state, Mapping):
        raise _fail("state_invalid")
    return state


def _validate_authorized_state(state: Mapping[str, Any]) -> Mapping[str, Any]:
    metadata = state.get("native_characterization_c4_authorization")
    if (
        state.get("protocol_version") != "current-validation-v1.3"
        or state.get("current_stage") != "NATIVE_CHARACTERIZATION"
        or state.get("status") != "native_characterization_c4_live_only"
        or state.get("current_action_scope") != "native_characterization_c4_live_only"
        or state.get("next_allowed_action") != "run_native_characterization_c4"
        or state.get("authorized_live_actions") != ["native_characterization_c4"]
        or state.get("native_characterization_live_authorized") is not True
        or state.get("service_admin_authorized") is not False
        or not isinstance(metadata, Mapping)
        or metadata.get("schema_version")
        != "membind.native-characterization-c4-authorization.v1"
        or metadata.get("live_authorized") is not True
        or metadata.get("operator_authorization_input") is not True
    ):
        raise _fail("state_not_exact_c4_live")
    return metadata


def _read_bound_object(
    validation_root: Path,
    relative: Any,
    expected_sha256: Any,
    expected_payload_sha256: Any,
    code: str,
) -> tuple[Path, dict[str, Any]]:
    if not isinstance(expected_sha256, str) or _SHA256_RE.fullmatch(expected_sha256) is None:
        raise _fail(f"{code}_hash_invalid")
    if (
        not isinstance(expected_payload_sha256, str)
        or _SHA256_RE.fullmatch(expected_payload_sha256) is None
    ):
        raise _fail(f"{code}_payload_invalid")
    path = _resolve_under(validation_root, relative, f"{code}_path_invalid")
    if _sha256_file(path) != expected_sha256:
        raise _fail(f"{code}_hash_mismatch")
    value = _read_json(path, f"{code}_invalid")
    if (
        value.get("payload_sha256") != expected_payload_sha256
        or c4_artifacts.payload_sha256(value) != expected_payload_sha256
    ):
        raise _fail(f"{code}_payload_mismatch")
    return path, value


def _validate_schedule(validation_root: Path, metadata: Mapping[str, Any]) -> dict[str, Any]:
    _path, schedule = _read_bound_object(
        validation_root,
        metadata.get("schedule_path"),
        metadata.get("schedule_sha256"),
        metadata.get("schedule_payload_sha256"),
        "schedule",
    )
    if (
        schedule.get("schema_version") != c4_artifacts.SCHEDULE_SCHEMA
        or schedule.get("status") != "dry_run"
        or schedule.get("stage") != "C4/E3_OFFLINE_SCHEDULE"
        or schedule.get("history_id") != FROZEN_HISTORY_ID
        or schedule.get("episode_ids")
        != [f"{FROZEN_HISTORY_ID}:{index}" for index in range(FROZEN_EPISODE_COUNT)]
    ):
        raise _fail("schedule_contract_mismatch")
    blocks = schedule.get("block_schedules")
    methods = [c4.NATIVE_SYNC] * 5 + [c4.NATIVE_ASYNC_SERIAL] * 5
    loads = [0.5, 0.8, 1.0, 1.2, 1.5] * 2
    if not isinstance(blocks, list) or len(blocks) != 10:
        raise _fail("schedule_contract_mismatch")
    namespaces: set[str] = set()
    for index, (block, method, load) in enumerate(zip(blocks, methods, loads)):
        if not isinstance(block, Mapping):
            raise _fail("schedule_contract_mismatch")
        namespace = block.get("graph_namespace")
        interval = block.get("interarrival_ns")
        offsets = block.get("absolute_arrival_offsets_ns")
        if (
            block.get("block_index") != index
            or block.get("method") != method
            or block.get("normalized_offered_load") != load
            or not isinstance(namespace, str)
            or _C4_NAMESPACE_RE.fullmatch(namespace) is None
            or namespace in namespaces
            or not isinstance(interval, int)
            or isinstance(interval, bool)
            or interval <= 0
            or offsets != [source * interval for source in range(FROZEN_EPISODE_COUNT)]
        ):
            raise _fail("schedule_contract_mismatch")
        namespaces.add(namespace)
    return schedule


def _validate_freeze(validation_root: Path, metadata: Mapping[str, Any]) -> dict[str, Any]:
    _path, freeze = _read_bound_object(
        validation_root,
        metadata.get("freeze_path"),
        metadata.get("freeze_sha256"),
        metadata.get("freeze_payload_sha256"),
        "freeze",
    )
    runtime = freeze.get("runtime_identities")
    construction = runtime.get("construction") if isinstance(runtime, Mapping) else None
    transition = freeze.get("state_transition")
    if (
        freeze.get("schema_version") != "membind.native-characterization-freeze.v1"
        or freeze.get("run_id") != "native-characterization-freeze-reference-aligned-64k"
        or not isinstance(construction, Mapping)
        or construction.get("vllm_version") != "0.26.0"
        or construction.get("served_model_id") != "qwen3-32b-fp8"
        or construction.get("max_model_len") != 65536
        or construction.get("rope_type") != "yarn"
        or construction.get("yarn_factor") != 2.0
        or construction.get("original_max_position_embeddings") != 32768
        or construction.get("rope_theta") != 1000000
        or not isinstance(transition, Mapping)
        or transition.get("execution_envelope_updated") is not True
        or transition.get("live_authorized") is not False
    ):
        raise _fail("freeze_64k_contract_mismatch")
    return freeze


def _load_frozen_episodes(
    validation_root: Path,
    freeze: Mapping[str, Any],
    dependencies: C4LiveDependencies,
) -> tuple[list[c4.Episode], list[str]]:
    split_path = _resolve_under(validation_root, FROZEN_SPLIT_PATH, "split_path_invalid")
    split_sha = _sha256_file(split_path)
    dataset_spec = freeze.get("dataset")
    input_hashes = freeze.get("input_hashes")
    if (
        not isinstance(dataset_spec, Mapping)
        or dataset_spec.get("split_sha256") != split_sha
        or (
            isinstance(input_hashes, Mapping)
            and input_hashes.get("split_sha256") != split_sha
        )
    ):
        raise _fail("split_hash_mismatch")
    split = _read_json(split_path, "split_invalid")
    if (
        split.get("protocol_version") != "current-validation-v1.3"
        or FROZEN_HISTORY_ID not in split.get("calibration_question_ids", [])
        or split.get("source_sha256") != dataset_spec.get("source_sha256")
    ):
        raise _fail("split_contract_mismatch")
    source_path_raw = split.get("source_path")
    if not isinstance(source_path_raw, str) or not source_path_raw:
        raise _fail("dataset_source_path_invalid")
    try:
        source_path = Path(source_path_raw).resolve(strict=True)
    except (OSError, RuntimeError):
        raise _fail("dataset_source_path_invalid") from None
    if _sha256_file(source_path) != split.get("source_sha256"):
        raise _fail("dataset_source_hash_mismatch")
    records = dependencies.raw_dataset_loader(source_path)
    matches = [
        record
        for record in records
        if isinstance(record, dict) and str(record.get("question_id")) == FROZEN_HISTORY_ID
    ]
    if len(matches) != 1:
        raise _fail("frozen_history_missing")
    built = list(dependencies.episode_builder(matches[0]))
    histories = dataset_spec.get("calibration_histories")
    expected_history = None
    if isinstance(histories, list):
        for item in histories:
            if isinstance(item, Mapping) and item.get("history_id") == FROZEN_HISTORY_ID:
                expected_history = item
                break
    expected_episodes = expected_history.get("episodes") if isinstance(expected_history, Mapping) else None
    if (
        not isinstance(expected_history, Mapping)
        or expected_history.get("episode_count") != FROZEN_EPISODE_COUNT
        or not isinstance(expected_episodes, list)
        or len(expected_episodes) != FROZEN_EPISODE_COUNT
        or len(built) != FROZEN_EPISODE_COUNT
    ):
        raise _fail("frozen_episode_contract_mismatch")
    source_hashes: list[str] = []
    for index, (episode, expected) in enumerate(zip(built, expected_episodes)):
        if (
            not isinstance(expected, Mapping)
            or episode.question_id != FROZEN_HISTORY_ID
            or episode.source_sequence != index
            or expected.get("source_sequence") != index
            or episode.source_hash != expected.get("episode_source_sha256")
        ):
            raise _fail("frozen_episode_contract_mismatch")
        source_hashes.append(episode.source_hash)
    return [
        c4.Episode(source_sequence=episode.source_sequence, payload=episode)
        for episode in built
    ], source_hashes


def _provenance_hashes(metadata: Mapping[str, Any], freeze: Mapping[str, Any]) -> dict[str, str]:
    c2 = metadata.get("c2_evidence")
    c3 = metadata.get("c3_evidence")
    dataset_spec = freeze.get("dataset")
    if not isinstance(c2, Mapping) or not isinstance(c3, Mapping) or not isinstance(dataset_spec, Mapping):
        raise _fail("provenance_missing")
    values = {
        "freeze_64k_sha256": metadata.get("freeze_sha256"),
        "freeze_64k_payload_sha256": metadata.get("freeze_payload_sha256"),
        "c2_manifest_sha256": c2.get("manifest_sha256"),
        "c2_checkpoint_sha256": c2.get("checkpoint_sha256"),
        "c2_verification_sha256": c2.get("verification_sha256"),
        "c2_verification_payload_sha256": c2.get("verification_payload_sha256"),
        "c3_analyzer_source_sha256": c3.get("analyzer_source_sha256"),
        "c3_dependency_map_sha256": c3.get("dependency_map_sha256"),
        "c3_dependency_map_payload_sha256": c3.get("dependency_map_payload_sha256"),
        "c3_e2_sha256": c3.get("e2_sha256"),
        "c3_e2_payload_sha256": c3.get("e2_payload_sha256"),
        "dataset_source_sha256": dataset_spec.get("source_sha256"),
    }
    if set(values) != set(c4_artifacts.REQUIRED_PROVENANCE_HASHES):
        raise _fail("provenance_invalid")
    for value in values.values():
        if not isinstance(value, str) or _SHA256_RE.fullmatch(value) is None:
            raise _fail("provenance_invalid")
    return {key: str(values[key]) for key in c4_artifacts.REQUIRED_PROVENANCE_HASHES}


def _is_safe_progress(value: Any) -> bool:
    if isinstance(value, Mapping):
        for key, child in value.items():
            if not isinstance(key, str) or key.casefold() in _FORBIDDEN_KEYS:
                return False
            if not _is_safe_progress(child):
                return False
        return True
    if isinstance(value, (list, tuple)):
        return all(_is_safe_progress(item) for item in value)
    if value is None or isinstance(value, (bool, int, float)):
        return True
    if isinstance(value, str):
        lowered = value.casefold()
        return not (
            _SECRET_VALUE_RE.search(value)
            or "episode body" in lowered
            or "api-key" in lowered
        )
    return False


def _progress_sink(stream: Any | None) -> Callable[[Mapping[str, Any]], None]:
    def sink(event: Mapping[str, Any]) -> None:
        if stream is None or not _is_safe_progress(event):
            return
        try:
            stream.write(
                json.dumps(event, ensure_ascii=True, sort_keys=True, separators=(",", ":"))
                + "\n"
            )
            stream.flush()
        except Exception:
            return

    return sink


async def _ensure_driver_ready(graphiti: Any) -> None:
    driver = getattr(graphiti, "driver", None)
    init_task = getattr(driver, "_init_task", None)
    if init_task is not None:
        await init_task
        return
    readiness = getattr(driver, "build_indices_and_constraints", None)
    if callable(readiness):
        await readiness()


async def _namespace_counts(driver: Any, namespace: str) -> c4_runner.NamespaceCounts:
    execute_query = getattr(driver, "execute_query", None)
    if not callable(execute_query):
        raise _fail("driver_execute_query_missing")
    query = """
CALL {
  MATCH (n)
  WHERE n.group_id = $group_id
  RETURN count(n) AS node_count
}
CALL {
  MATCH ()-[r]->()
  WHERE r.group_id = $group_id
  RETURN count(r) AS relationship_count
}
RETURN node_count, relationship_count
"""
    try:
        result = await execute_query(query, params={"group_id": namespace})
        records = getattr(result, "records", None)
        if not isinstance(records, Sequence) or isinstance(records, (str, bytes)) or len(records) != 1:
            raise _fail("namespace_count_invalid")
        record = records[0]
        nodes = record["node_count"]
        relationships = record["relationship_count"]
    except C4LiveAdapterError:
        raise
    except Exception:
        raise _fail("namespace_count_failed") from None
    if (
        not isinstance(nodes, int)
        or isinstance(nodes, bool)
        or nodes < 0
        or not isinstance(relationships, int)
        or isinstance(relationships, bool)
        or relationships < 0
    ):
        raise _fail("namespace_count_invalid")
    if nodes != 0 or relationships != 0:
        raise _fail("namespace_not_empty")
    return c4_runner.NamespaceCounts(nodes, relationships)


class _GraphitiBlockRuntime:
    def __init__(self, graphiti: Any, block: c4_runner.C4Block) -> None:
        self.graphiti = graphiti
        self.block = block

    async def namespace_counts(self) -> c4_runner.NamespaceCounts:
        return await _namespace_counts(self.graphiti.driver, self.block.graph_namespace)

    async def clear_namespace(self) -> None:
        try:
            await clear_data(self.graphiti.driver, group_ids=[self.block.graph_namespace])
        except Exception:
            raise _fail("graphiti_clear_namespace_failed") from None

    async def service(self, episode: c4.Episode, service_start_ns: int) -> None:
        payload = episode.payload
        if not isinstance(payload, dataset.Episode):
            raise _fail("episode_payload_invalid")
        runtime_episode = replace(payload, group_id=self.block.graph_namespace)
        try:
            await self.graphiti.add_episode(**graphiti_episode_kwargs(runtime_episode))
        except C4LiveAdapterError:
            raise
        except Exception as exc:
            raise C4LiveAdapterError("graphiti_add_episode_failed") from exc

    async def close(self) -> None:
        close = getattr(self.graphiti, "close", None)
        if callable(close):
            try:
                await close()
            except Exception:
                raise _fail("graphiti_close_failed") from None


def _runtime_factory(
    dependencies: C4LiveDependencies,
    state_path: Path,
) -> Callable[[c4_runner.C4Block], Any]:
    async def factory(block: c4_runner.C4Block) -> _GraphitiBlockRuntime:
        if _C4_NAMESPACE_RE.fullmatch(block.graph_namespace) is None:
            raise _fail("c4_namespace_invalid")

        def authorization_checker(action: current_state_gate.LiveAction) -> current_state_gate.GateDecision:
            return dependencies.gate_checker(action, state_path=state_path)

        runtime = dependencies.runtime_builder(
            authorization_checker=authorization_checker,
            live_action=current_state_gate.LiveAction.NATIVE_CHARACTERIZATION_C4,
            structured_output_mode="json_schema",
        )
        graphiti = getattr(runtime, "graphiti", None)
        if graphiti is None:
            raise _fail("runtime_graphiti_missing")
        await _ensure_driver_ready(graphiti)
        return _GraphitiBlockRuntime(graphiti, block)

    return factory


def _validate_exact_paths(validation_root: Path, state_path: Path) -> tuple[Path, Path]:
    try:
        validation = Path(validation_root).resolve(strict=True)
        state = Path(state_path).resolve(strict=True)
    except (OSError, RuntimeError):
        raise _fail("path_invalid") from None
    if validation != VALIDATION_ROOT.resolve() or state != DEFAULT_STATE_PATH.resolve():
        raise _fail("path_not_exact_c4_authorized_location")
    return validation, state


async def execute_c4_live(
    *,
    validation_root: str | Path = VALIDATION_ROOT,
    state_path: str | Path = DEFAULT_STATE_PATH,
    resume_run_id: str | None = None,
    recover_terminal_failure: bool = False,
    dependencies: C4LiveDependencies | None = None,
    progress_stream: Any | None = sys.stdout,
) -> dict[str, object]:
    """Run the authorized C4 live replay through the production adapter."""

    deps = dependencies or C4LiveDependencies()
    validation, state = _validate_exact_paths(Path(validation_root), Path(state_path))
    deps.gate_checker(current_state_gate.LiveAction.NATIVE_CHARACTERIZATION_C4, state_path=state)
    state_value = _load_state(state, deps)
    metadata = _validate_authorized_state(state_value)
    schedule = _validate_schedule(validation, metadata)
    freeze = _validate_freeze(validation, metadata)
    episodes, source_hashes = _load_frozen_episodes(validation, freeze, deps)
    provenance = _provenance_hashes(metadata, freeze)
    return await deps.run_c4(
        runs_root=validation / RUNS_ROOT,
        schedule=schedule,
        provenance_hashes=provenance,
        episodes=episodes,
        episode_source_hashes=source_hashes,
        clock=_MonotonicAsyncClock(),
        runtime_factory=_runtime_factory(deps, state),
        state_path=state,
        creation_command=[
            "native-characterization-c4-live",
            "--validation-root",
            str(validation),
            "--state",
            str(state),
            *(["--resume-run-id", resume_run_id] if resume_run_id is not None else []),
            *(["--recover-terminal-failure"] if recover_terminal_failure else []),
        ],
        gate_checker=deps.gate_checker,
        resume_run_id=resume_run_id,
        recover_terminal_failure=recover_terminal_failure,
        progress_sink=_progress_sink(progress_stream),
        post_finalize_verifier=c4_artifacts.verify_c4_artifacts,
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run the authorized frozen C4/E3 replay")
    parser.add_argument("--validation-root", type=Path, default=VALIDATION_ROOT)
    parser.add_argument("--state", type=Path, default=DEFAULT_STATE_PATH)
    parser.add_argument("--resume-run-id")
    parser.add_argument("--recover-terminal-failure", action="store_true")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        result = asyncio.run(
            execute_c4_live(
                validation_root=args.validation_root,
                state_path=args.state,
                resume_run_id=args.resume_run_id,
                recover_terminal_failure=args.recover_terminal_failure,
                dependencies=C4LiveDependencies(),
                progress_stream=sys.stdout,
            )
        )
    except C4LiveAdapterError as exc:
        print(
            json.dumps(
                {"status": "error", "error_class": type(exc).__name__, "code": exc.code},
                ensure_ascii=True,
                sort_keys=True,
            ),
            file=sys.stderr,
        )
        return 1
    print(json.dumps(result, ensure_ascii=True, sort_keys=True))
    return 0


__all__ = [
    "C4LiveAdapterError",
    "C4LiveDependencies",
    "DEFAULT_STATE_PATH",
    "VALIDATION_ROOT",
    "build_parser",
    "execute_c4_live",
    "graphiti_episode_kwargs",
    "main",
]


if __name__ == "__main__":
    raise SystemExit(main())
