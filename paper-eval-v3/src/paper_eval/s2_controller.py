"""Production controller for the authorized one-chain S2 live sanity run.

This module only consumes the completed S1 namespace.  It deliberately has
no construction, namespace cleanup, retry, or resume entry point.
"""

from __future__ import annotations

import argparse
import asyncio
import inspect
import json
import os
import re
import subprocess
import sys
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from . import PROTOCOL_VERSION
from .artifacts import atomic_write_json, finalize_envelope, payload_sha256, sha256_file
from .s1_controller import ensure_runtime_ready
from .s1_live import EXPECTED_S1_HISTORY_ID, load_fixed_history
from .s2_adapters import (
    OpenAIChatCompletionsTransport,
    build_qualified_qwen_judge,
    project_s2_adapter_identity,
)
from .s2_durable import S2DurableRun, S2DurableResult, run_s2_durable
from .s2_live import S2LiveInputs
from .s2_qualification import SCHEMA as QUALIFICATION_SCHEMA
from .s2_qualification import verify_u0_qualification
from .s2_reader import OfficialFactsReader
from .s2_retrieval_contract import EDGE_SURFACE_CONTRACT


ROOT = Path(__file__).resolve().parents[3]
LEGACY = ROOT / "membind-validation"
DEFAULT_DATASET = Path(
    "/data/predator/ly/Mem/data/raw/longmemeval-cleaned/longmemeval_s_cleaned.json"
)
DEFAULT_SPLIT = LEGACY / "artifacts/dataset/frozen_split_v1_3.json"
DEFAULT_QUALIFICATION = (
    ROOT / "paper-eval-v3/artifacts/paper_eval/native/U0_QUALIFICATION.json"
)
DEFAULT_RUN_ROOT = ROOT / "paper-eval-v3/artifacts/paper_eval/native/runs"
DEFAULT_FINAL = (
    ROOT / "paper-eval-v3/artifacts/paper_eval/native/U0_REFERENCE_SANITY.json"
)
DEFAULT_S0_CURRENT_STATE = ROOT / "paper-eval-v3/artifacts/paper_eval/S0_CURRENT_STATE.json"
EXPECTED_NAMESPACE = "pev3-s1-20260814-001"
EXPECTED_EPISODE_COUNT = 49
_RUN_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,159}$")

_EXECUTION_SOURCE_PATHS = {
    "paper_eval.s2_controller": Path(__file__).resolve(),
    "paper_eval.s2_live": Path(__file__).resolve().with_name("s2_live.py"),
    "paper_eval.s2_durable": Path(__file__).resolve().with_name("s2_durable.py"),
    "paper_eval.s2_reader": Path(__file__).resolve().with_name("s2_reader.py"),
    "paper_eval.s2_adapters": Path(__file__).resolve().with_name("s2_adapters.py"),
    "legacy.dataset": LEGACY / "src/dataset.py",
    "legacy.graphiti_native": LEGACY / "src/graphiti_native.py",
    "legacy.native_characterization_runtime": (
        LEGACY / "src/native_characterization_runtime.py"
    ),
    "legacy.judge_backend": LEGACY / "src/evaluation/backends/openai_compatible.py",
    "legacy.longmemeval_adapter": LEGACY / "src/evaluation/benchmarks/longmemeval.py",
    "legacy.longmemeval_vendor": (
        LEGACY / "src/evaluation/vendor/longmemeval_evaluate_qa.py"
    ),
    "graphiti.graphiti": (
        LEGACY / ".venv/lib/python3.12/site-packages/graphiti_core/graphiti.py"
    ),
    "graphiti.search.search": (
        LEGACY
        / ".venv/lib/python3.12/site-packages/graphiti_core/search/search.py"
    ),
    "graphiti.search.search_config": (
        LEGACY
        / ".venv/lib/python3.12/site-packages/graphiti_core/search/search_config.py"
    ),
    "graphiti.search.search_config_recipes": (
        LEGACY
        / ".venv/lib/python3.12/site-packages/graphiti_core/search/search_config_recipes.py"
    ),
    "graphiti.search.search_utils": (
        LEGACY
        / ".venv/lib/python3.12/site-packages/graphiti_core/search/search_utils.py"
    ),
    "graphiti.openai_generic_client": (
        LEGACY
        / ".venv/lib/python3.12/site-packages/graphiti_core/llm_client/openai_generic_client.py"
    ),
}


class S2ControllerError(RuntimeError):
    """Sanitized controller preflight or dependency-assembly failure."""


@dataclass(frozen=True)
class S2ControllerDependencies:
    """Injectable boundaries keep controller policy fully offline-testable."""

    load_history: Callable[[], Mapping[str, Any]]
    build_episodes: Callable[[Mapping[str, Any]], Sequence[Any]]
    build_runtime: Callable[[], Any]
    ensure_runtime_ready: Callable[[Any], Any]
    build_reader: Callable[[], tuple[Any, Any]]
    build_judge: Callable[[], Any]
    project_adapter_identity: Callable[..., Mapping[str, Any]]
    run_durable: Callable[..., Awaitable[S2DurableResult] | S2DurableResult]


def _load_qualification(path: Path) -> tuple[dict[str, Any], str]:
    """Verify the sealed authorization and return only its safe payload/hash."""

    try:
        verify_u0_qualification(Path(path))
        envelope = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, UnicodeError, ValueError, json.JSONDecodeError) as error:
        raise S2ControllerError(
            f"qualification rejected: {type(error).__name__}"
        ) from None
    payload = envelope.get("payload")
    if not isinstance(payload, dict):
        raise S2ControllerError("qualification rejected: invalid payload")
    exact = {
        "stage": "S2",
        "method": "U0",
        "verdict": "PASS",
        "authorization": "AUTHORIZE_S2_U0_1_HISTORY",
        "history_id": EXPECTED_S1_HISTORY_ID,
        "namespace": EXPECTED_NAMESPACE,
        "s1_run_id": "s1-20260814-001",
        "episode_count": EXPECTED_EPISODE_COUNT,
        "qualification_scope": "one_history_u0_only",
        "s0_current_state_sha256": sha256_file(DEFAULT_S0_CURRENT_STATE),
    }
    current_s0_sha256 = exact["s0_current_state_sha256"]
    if (
        current_s0_sha256 == "missing"
        or envelope.get("protocol_version") != QUALIFICATION_SCHEMA
        or any(payload.get(key) != expected for key, expected in exact.items())
    ):
        raise S2ControllerError("qualification rejected: execution identity drift")
    checks = payload.get("checks")
    if (
        not isinstance(checks, Mapping)
        or not checks
        or any(value is not True for value in checks.values())
        or payload.get("failure_reasons") != []
    ):
        raise S2ControllerError("qualification rejected: incomplete checks")
    return payload, sha256_file(Path(path))


def _reject_started_run(*, artifact_root: Path, run_id: str) -> None:
    """Reject a run ID before constructing any live client or runtime."""

    if _RUN_ID_RE.fullmatch(run_id) is None:
        raise S2ControllerError("run ID rejected")
    marker = Path(artifact_root) / run_id / ".started"
    if marker.exists():
        raise S2ControllerError("run already started")


def _s2_inputs(*, run_id: str, instance: Mapping[str, Any]) -> S2LiveInputs:
    if _RUN_ID_RE.fullmatch(run_id) is None:
        raise S2ControllerError("run ID rejected")
    if str(instance.get("question_id", "")) != EXPECTED_S1_HISTORY_ID:
        raise S2ControllerError("history identity drift")
    answer_session_ids = instance.get("answer_session_ids")
    if not isinstance(answer_session_ids, list) or not answer_session_ids:
        raise S2ControllerError("history evidence identity is missing")
    try:
        return S2LiveInputs(
            run_id=run_id,
            history_id=EXPECTED_S1_HISTORY_ID,
            namespace=EXPECTED_NAMESPACE,
            question=str(instance["question"]),
            question_date=str(instance["question_date"]),
            question_type=str(instance["question_type"]),
            reference_answer=str(instance["answer"]),
            answer_session_ids=tuple(str(value) for value in answer_session_ids),
        )
    except (KeyError, TypeError, ValueError) as error:
        raise S2ControllerError(f"history rejected: {type(error).__name__}") from None


async def _await(value: Any) -> Any:
    return await value if inspect.isawaitable(value) else value


async def _close(value: Any, *names: str) -> None:
    if value is None:
        return
    for name in names:
        method = getattr(value, name, None)
        if callable(method):
            await _await(method())
            return


def _validated_adapter_identity(value: Mapping[str, Any]) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise S2ControllerError("adapter identity rejected")
    identity = dict(value)
    stored = identity.pop("identity_sha256", None)
    if not isinstance(stored, str) or stored != payload_sha256(identity):
        raise S2ControllerError("adapter identity hash rejected")
    return {**identity, "identity_sha256": stored}


def _execution_source_hashes() -> dict[str, str]:
    hashes = {name: sha256_file(path) for name, path in _EXECUTION_SOURCE_PATHS.items()}
    if any(value == "missing" for value in hashes.values()):
        raise S2ControllerError("execution source identity is incomplete")
    return hashes


async def execute_s2_controller(
    *,
    run_id: str,
    qualification_path: Path,
    artifact_root: Path,
    final_output: Path,
    adapter_identity_output: Path,
    dependencies: S2ControllerDependencies,
    git_commit: str,
) -> S2DurableResult:
    """Execute exactly one authorized S2 retrieval/Reader/Judge chain."""

    _reject_started_run(artifact_root=artifact_root, run_id=run_id)
    _qualification, qualification_sha256 = _load_qualification(qualification_path)
    if Path(adapter_identity_output).exists():
        raise S2ControllerError("adapter identity sidecar already exists")
    instance = dict(dependencies.load_history())
    inputs = _s2_inputs(run_id=run_id, instance=instance)
    episodes = list(dependencies.build_episodes(instance))
    if len(episodes) != EXPECTED_EPISODE_COUNT:
        raise S2ControllerError("episode coverage drift")
    sequences = [getattr(episode, "source_sequence", index) for index, episode in enumerate(episodes)]
    if sequences != list(range(EXPECTED_EPISODE_COUNT)):
        raise S2ControllerError("episode source order drift")
    execution_source_sha256 = _execution_source_hashes()

    runtime: Any | None = None
    reader: Any | None = None
    transport: Any | None = None
    judge: Any | None = None
    durable_entered = False
    run: S2DurableRun | None = None
    try:
        reader, transport = dependencies.build_reader()
        judge = dependencies.build_judge()
        identity = _validated_adapter_identity(
            dependencies.project_adapter_identity(
                reader_transport=transport,
                reader=reader,
                judge=judge,
            )
        )
        identity_payload = {
            **identity,
            "qualification_sha256": qualification_sha256,
            "s0_current_state_sha256": sha256_file(DEFAULT_S0_CURRENT_STATE),
            "execution_source_sha256": execution_source_sha256,
            "execution_policy": {
                "construction_calls": 0,
                "namespace_cleanup_calls": 0,
                **EDGE_SURFACE_CONTRACT.to_identity(),
                "retrieval_top_k": 10,
                "reader_max_attempts": 1,
                "judge_max_attempts": 1,
            },
        }
        identity_artifact = finalize_envelope(
            payload=identity_payload,
            protocol_version=PROTOCOL_VERSION,
            git_commit=git_commit,
            run_id=run_id,
        )
        atomic_write_json(Path(adapter_identity_output), identity_artifact)
        adapter_identity_sha256 = sha256_file(Path(adapter_identity_output))

        runtime = dependencies.build_runtime()
        await _await(dependencies.ensure_runtime_ready(runtime))
        run = S2DurableRun(
            Path(artifact_root),
            inputs,
            final_output=Path(final_output),
        )
        durable_entered = True
        return await _await(
            dependencies.run_durable(
                run=run,
                graph=runtime.graphiti,
                episodes=episodes,
                reader=reader,
                judge=judge,
                git_commit=git_commit,
                qualification_evidence_sha256=qualification_sha256,
                adapter_identity_sha256=adapter_identity_sha256,
            )
        )
    finally:
        close_errors: list[BaseException] = []
        for resource, names in (
            (transport, ("aclose", "close")),
            (judge, ("aclose", "close")),
        ):
            try:
                await _close(resource, *names)
            except Exception as error:
                close_errors.append(error)
        # The durable runner owns Graphiti once entered and closes it in its
        # scientific core's finally block.  Pre-run assembly failures do not.
        if runtime is not None and not durable_entered:
            try:
                await _close(getattr(runtime, "graphiti", None), "close", "aclose")
            except Exception as error:
                close_errors.append(error)
        if close_errors:
            active_exception = sys.exc_info()[0] is not None
            cleanup_payload = {
                "stage": "S2",
                "run_id": run_id,
                "status": "FAILURE_PATH_WARNING" if active_exception else "WARNING",
                "result_usable": False,
                "error_classes": [type(error).__name__ for error in close_errors],
            }
            atomic_write_json(
                Path(artifact_root) / run_id / "cleanup_status.json",
                finalize_envelope(
                    payload=cleanup_payload,
                    protocol_version=PROTOCOL_VERSION,
                    git_commit=git_commit,
                    run_id=run_id,
                ),
            )
            if not active_exception:
                raise S2ControllerError("S2 cleanup failed")


def _production_dependencies(
    *, dataset_path: Path = DEFAULT_DATASET, split_path: Path = DEFAULT_SPLIT
) -> S2ControllerDependencies:
    """Build live dependencies lazily from the existing private env file."""

    source = str(LEGACY / "src")
    if source not in sys.path:
        sys.path.insert(0, source)
    from dataset import build_episodes
    from graphiti_native import load_env_file
    from native_characterization_runtime import build_u0_graphiti_from_env

    loaded: dict[str, str] | None = None

    def settings() -> dict[str, str]:
        nonlocal loaded
        if loaded is None:
            loaded = load_env_file(LEGACY / ".env")
        return loaded

    def private(name: str, *fallbacks: str) -> str:
        current = settings()
        value = current.get(name) or os.environ.get(name)
        for fallback in fallbacks:
            value = value or current.get(fallback) or os.environ.get(fallback)
        if not value:
            raise S2ControllerError(f"private runtime setting missing: {name}")
        return value

    def build_runtime() -> Any:
        current = settings()
        return build_u0_graphiti_from_env(
            authorization_checker=lambda _action: None,
            env_loader=lambda: current,
        )

    def build_reader() -> tuple[OfficialFactsReader, OpenAIChatCompletionsTransport]:
        model = private("CONSTRUCTION_LLM_MODEL")
        transport = OpenAIChatCompletionsTransport(
            model=model,
            base_url=private("CONSTRUCTION_LLM_BASE_URL"),
            api_key=private("CONSTRUCTION_LLM_API_KEY", "VLLM_API_KEY"),
        )
        return OfficialFactsReader(model=model, transport=transport), transport

    return S2ControllerDependencies(
        load_history=lambda: load_fixed_history(dataset_path, split_path),
        build_episodes=build_episodes,
        build_runtime=build_runtime,
        ensure_runtime_ready=ensure_runtime_ready,
        build_reader=build_reader,
        build_judge=lambda: build_qualified_qwen_judge(
            base_url=private("CONSTRUCTION_LLM_BASE_URL"),
            api_key=private("CONSTRUCTION_LLM_API_KEY", "VLLM_API_KEY"),
        ),
        project_adapter_identity=project_s2_adapter_identity,
        run_durable=run_s2_durable,
    )


def _git_commit() -> str:
    return subprocess.check_output(
        ["git", "rev-parse", "HEAD"], cwd=ROOT, text=True
    ).strip()


async def _main(args: argparse.Namespace) -> int:
    try:
        result = await execute_s2_controller(
            run_id=args.run_id,
            qualification_path=args.qualification,
            artifact_root=args.artifact_root,
            final_output=args.final_output,
            adapter_identity_output=(
                args.adapter_identity_output
                or args.artifact_root / args.run_id / "adapter_identity.json"
            ),
            dependencies=_production_dependencies(
                dataset_path=args.dataset,
                split_path=args.split,
            ),
            git_commit=_git_commit(),
        )
    except Exception as error:
        print(
            json.dumps(
                {"run_id": args.run_id, "status": "FAIL", "error_class": type(error).__name__},
                sort_keys=True,
            ),
            flush=True,
        )
        return 2
    print(
        json.dumps(
            {
                "run_id": args.run_id,
                "status": result.payload["status"],
                "edge_attributed_source_session_coverage_at_10": result.payload[
                    "edge_attributed_source_session_coverage_at_10"
                ],
                "qa_accuracy": result.payload["qa_accuracy"],
                "near_zero_stop_triggered": result.payload["near_zero_stop_triggered"],
            },
            sort_keys=True,
        ),
        flush=True,
    )
    return 0 if result.payload["status"] == "PASS" else 2


def parser() -> argparse.ArgumentParser:
    value = argparse.ArgumentParser(description=__doc__)
    value.add_argument("--run-id", required=True)
    value.add_argument("--dataset", type=Path, default=DEFAULT_DATASET)
    value.add_argument("--split", type=Path, default=DEFAULT_SPLIT)
    value.add_argument("--qualification", type=Path, default=DEFAULT_QUALIFICATION)
    value.add_argument("--artifact-root", type=Path, default=DEFAULT_RUN_ROOT)
    value.add_argument("--final-output", type=Path, default=DEFAULT_FINAL)
    value.add_argument(
        "--adapter-identity-output", type=Path, default=None
    )
    return value


def main() -> None:
    raise SystemExit(asyncio.run(_main(parser().parse_args())))


if __name__ == "__main__":
    main()
