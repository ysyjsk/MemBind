"""One-shot S2-R0 controller with consumption-before-I/O ordering."""

from __future__ import annotations

import asyncio
import hashlib
import inspect
import os
import subprocess
import sys
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .artifacts import payload_sha256, sha256_file
from .s2_retrieval_probe import (
    ProbeCounters,
    build_episode_bm25_search_config,
    corpus_identity_sha256,
    finalize_episode_surface_probe,
    run_episode_surface_probe,
    search_config_identity,
)
from .s2_r0_authorization import (
    EXPECTED_EPISODE_COUNT,
    EXPECTED_HISTORY_ID,
    EXPECTED_NAMESPACE,
    consume_s2r0_authorization,
)
from .s2_r0_live import (
    S2R0Runtime,
    build_read_only_graphiti,
    finalize_s2r0_failure,
)


ROOT = Path(__file__).resolve().parents[3]
PROJECT = ROOT / "paper-eval-v3"
LEGACY = ROOT / "membind-validation"
DEFAULT_DATASET = Path(
    "/data/predator/ly/Mem/data/raw/longmemeval-cleaned/longmemeval_s_cleaned.json"
)
DEFAULT_SPLIT = LEGACY / "artifacts/dataset/frozen_split_v1_3.json"
DEFAULT_NATIVE = PROJECT / "artifacts/paper_eval/native"
DEFAULT_RUN_ID = "s2r0-20260814-001"
DEFAULT_QUALIFICATION = DEFAULT_NATIVE / "S2_R0_OFFLINE_QUALIFICATION.json"
DEFAULT_AUTHORIZATION = DEFAULT_NATIVE / "S2_R0_AUTHORIZATION.json"
DEFAULT_RUN_DIR = DEFAULT_NATIVE / "runs" / DEFAULT_RUN_ID
DEFAULT_CONSUMPTION = DEFAULT_RUN_DIR / "S2_R0_AUTHORIZATION_CONSUMPTION.json"
DEFAULT_RESULT = DEFAULT_RUN_DIR / "S2_R0_EPISODE_PROBE.json"
DEFAULT_FAILURE = DEFAULT_RUN_DIR / "S2_R0_FAILURE.json"
RETRY_002_RUN_ID = "s2r0-20260814-002"
RETRY_002_QUALIFICATION = (
    DEFAULT_NATIVE / "S2_R0_RETRY_002_OFFLINE_QUALIFICATION.json"
)
RETRY_002_AUTHORIZATION = DEFAULT_NATIVE / "S2_R0_RETRY_002_AUTHORIZATION.json"
RETRY_002_RUN_DIR = DEFAULT_NATIVE / "runs" / RETRY_002_RUN_ID
RETRY_002_CONSUMPTION = (
    RETRY_002_RUN_DIR / "S2_R0_AUTHORIZATION_CONSUMPTION.json"
)
RETRY_002_RESULT = RETRY_002_RUN_DIR / "S2_R0_EPISODE_PROBE.json"
RETRY_002_FAILURE = RETRY_002_RUN_DIR / "S2_R0_FAILURE.json"


def production_binding_paths() -> dict[str, Path]:
    """Return the public, content-hashed inputs to the S2-R0 authority."""

    graphiti = LEGACY / ".venv/lib/python3.12/site-packages/graphiti_core"
    historical_run = DEFAULT_NATIVE / "runs/s2-live-20260814-001"
    s1_run = DEFAULT_NATIVE / "runs/s1-20260814-001"
    return {
        "parent_protocol": ROOT
        / "（主实验）MemBind_PAPER_EVALUATION_WORKPLAN_v3_SMALL_FIRST_FINAL.md",
        "amendment": PROJECT / "PAPER_EVALUATION_PROTOCOL_AMENDMENT_v1.1.md",
        "literature_audit": PROJECT / "S2_LITERATURE_AND_CODE_DESIGN_AUDIT_20260814.md",
        "dataset": DEFAULT_DATASET,
        "frozen_split": DEFAULT_SPLIT,
        "dataset_builder_source": LEGACY / "src/dataset.py",
        "dataset_parity": DEFAULT_NATIVE / "DATASET_PARITY.json",
        "s0_current_state": PROJECT / "artifacts/paper_eval/S0_CURRENT_STATE.json",
        "s1_summary": DEFAULT_NATIVE / "U0_SMOKE.json",
        "s1_checkpoint": s1_run / "checkpoint.json",
        "s1_events": s1_run / "events.jsonl",
        "u0_qualification": DEFAULT_NATIVE / "U0_QUALIFICATION.json",
        "historical_s2_reference": DEFAULT_NATIVE / "U0_REFERENCE_SANITY.json",
        "historical_s2_checkpoint": historical_run / "checkpoint.json",
        "historical_s2_events": historical_run / "events.jsonl",
        "historical_s2_adapter_identity": historical_run / "adapter_identity.json",
        "s2_contract_review": DEFAULT_NATIVE / "S2_RETRIEVAL_CONTRACT_REVIEW.json",
        "artifacts_source": PROJECT / "src/paper_eval/artifacts.py",
        "probe_source": PROJECT / "src/paper_eval/s2_retrieval_probe.py",
        "contract_source": PROJECT / "src/paper_eval/s2_retrieval_contract.py",
        "authorization_source": PROJECT / "src/paper_eval/s2_r0_authorization.py",
        "live_source": PROJECT / "src/paper_eval/s2_r0_live.py",
        "controller_source": PROJECT / "src/paper_eval/s2_r0_controller.py",
        "probe_test": PROJECT / "tests/test_s2_retrieval_probe.py",
        "authorization_test": PROJECT / "tests/test_s2_r0_authorization.py",
        "live_test": PROJECT / "tests/test_s2_r0_live.py",
        "controller_test": PROJECT / "tests/test_s2_r0_controller.py",
        "protocol_test": PROJECT / "tests/test_s2_protocol_amendment.py",
        "production_test": PROJECT / "tests/test_s2_r0_production.py",
        "finalize_script": PROJECT / "scripts/finalize_s2_r0.py",
        "run_script": PROJECT / "scripts/run_s2_r0.py",
        "graphiti_graphiti": graphiti / "graphiti.py",
        "graphiti_search": graphiti / "search/search.py",
        "graphiti_search_config": graphiti / "search/search_config.py",
        "graphiti_search_utils": graphiti / "search/search_utils.py",
        "graphiti_neo4j_driver": graphiti / "driver/neo4j_driver.py",
        "graphiti_neo4j_search_ops": graphiti
        / "driver/neo4j/operations/search_ops.py",
        "focused_green": PROJECT / "logs/TDD_FOCUSED_GREEN_S2R0_FINAL_20260814.xml",
        "full_green": PROJECT / "logs/TDD_FULL_OFFLINE_GREEN_S2R0_FINAL_20260814.xml",
        "prior_s2r0_authorization": DEFAULT_AUTHORIZATION,
        "prior_s2r0_consumption": DEFAULT_CONSUMPTION,
        "prior_s2r0_failure": DEFAULT_FAILURE,
        "s2r0_failure_root_cause": (
            PROJECT / "S2_R0_FAILURE_ROOT_CAUSE_20260814.md"
        ),
        "retry_execution_plan": (
            PROJECT / "S2_R0_RETRY_002_EXECUTION_PLAN_20260814.md"
        ),
        "repair_red": (
            PROJECT / "logs/TDD_RED_S2R0_QUERY_PARAMETER_COLLISION_20260814.xml"
        ),
        "repair_targeted_green": (
            PROJECT / "logs/TDD_GREEN_S2R0_QUERY_PARAMETER_COLLISION_20260814.xml"
        ),
        "repair_focused_green": (
            PROJECT
            / "logs/TDD_FOCUSED_GREEN_S2R0_POST_FAILURE_REPAIR_20260814.xml"
        ),
        "repair_full_green": (
            PROJECT
            / "logs/TDD_FULL_OFFLINE_GREEN_S2R0_POST_FAILURE_REPAIR_20260814.xml"
        ),
    }


def retry_002_binding_paths() -> dict[str, Path]:
    """Bind attempt 002 to attempt 001, its repair, and new final tests."""

    bindings = production_binding_paths()
    bindings.update(
        {
            "finalize_script": PROJECT / "scripts/finalize_s2_r0_retry_002.py",
            "run_script": PROJECT / "scripts/run_s2_r0_retry_002.py",
            "focused_green": (
                PROJECT
                / "logs/TDD_FOCUSED_GREEN_S2R0_RETRY_002_FINAL_20260814.xml"
            ),
            "full_green": (
                PROJECT
                / "logs/TDD_FULL_OFFLINE_GREEN_S2R0_RETRY_002_FINAL_20260814.xml"
            ),
        }
    )
    return bindings


def production_dependencies(
    *, dataset_path: Path = DEFAULT_DATASET, split_path: Path = DEFAULT_SPLIT
) -> S2R0ControllerDependencies:
    """Assemble only dataset, local Neo4j, and upstream Episode search."""

    legacy_source = str(LEGACY / "src")
    if legacy_source not in sys.path:
        sys.path.insert(0, legacy_source)
    from dataset import build_episodes

    from .s1_live import load_fixed_history

    def load_env() -> Mapping[str, str]:
        return load_neo4j_env_file(LEGACY / ".env")

    return S2R0ControllerDependencies(
        load_history=lambda: load_fixed_history(dataset_path, split_path),
        build_episodes=lambda value: build_episodes(dict(value)),
        build_search_config=build_episode_bm25_search_config,
        load_env=load_env,
        build_runtime=lambda env: build_read_only_graphiti(env=env),
        run_probe=run_episode_surface_probe,
        finalize_probe=finalize_episode_surface_probe,
    )


def load_neo4j_env_file(
    path: Path, *, environ: Mapping[str, str] | None = None
) -> dict[str, str]:
    """Read only the three Neo4j fields without mutating process state."""

    required = ("NEO4J_URI", "NEO4J_USER", "NEO4J_PASSWORD")
    selected: dict[str, str] = {}
    try:
        lines = Path(path).read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeError) as error:
        raise ValueError(f"S2-R0 Neo4j environment is unreadable: {type(error).__name__}") from None
    for raw_line in lines:
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[7:].lstrip()
        key, separator, raw_value = line.partition("=")
        key = key.strip()
        if not separator or key not in required:
            continue
        value = raw_value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
            value = value[1:-1]
        selected[key] = value
    fallback = os.environ if environ is None else environ
    return {key: selected.get(key) or str(fallback.get(key, "")) for key in required}


def git_commit() -> str:
    return subprocess.check_output(
        ["git", "rev-parse", "HEAD"], cwd=ROOT, text=True
    ).strip()


@dataclass(frozen=True)
class S2R0ControllerDependencies:
    load_history: Callable[[], Mapping[str, Any]]
    build_episodes: Callable[[Mapping[str, Any]], Sequence[Any]]
    build_search_config: Callable[[], Any]
    load_env: Callable[[], Mapping[str, str]]
    build_runtime: Callable[[Mapping[str, str]], S2R0Runtime]
    run_probe: Callable[..., Awaitable[Any] | Any]
    finalize_probe: Callable[..., Mapping[str, Any]]


@dataclass(frozen=True)
class S2R0ExecutionOutcome:
    status: str
    artifact_path: Path
    run_id: str


async def _await(value: Any) -> Any:
    return await value if inspect.isawaitable(value) else value


async def _run_and_close(
    *,
    runtime: S2R0Runtime,
    run_probe: Callable[..., Awaitable[Any] | Any],
    probe_kwargs: Mapping[str, Any],
) -> Any:
    try:
        return await _await(run_probe(**dict(probe_kwargs)))
    finally:
        close = getattr(runtime.graphiti, "close", None)
        if callable(close):
            await _await(close())


def _validate_history(
    history: Mapping[str, Any], episodes: Sequence[Any], authorization: Mapping[str, Any]
) -> tuple[str, tuple[str, ...], tuple[str, ...]]:
    if str(history.get("question_id", "")) != EXPECTED_HISTORY_ID:
        raise ValueError("S2-R0 history identity drift")
    if len(episodes) != EXPECTED_EPISODE_COUNT:
        raise ValueError("S2-R0 episode count drift")
    question = history.get("question")
    session_ids = history.get("haystack_session_ids")
    gold_ids = history.get("answer_session_ids")
    if (
        not isinstance(question, str)
        or not question
        or not isinstance(session_ids, list)
        or not isinstance(gold_ids, list)
        or not gold_ids
    ):
        raise ValueError("S2-R0 history contract is incomplete")
    sessions = tuple(str(value) for value in session_ids)
    gold = tuple(str(value) for value in gold_ids)
    if (
        len(sessions) != EXPECTED_EPISODE_COUNT
        or len(set(sessions)) != len(sessions)
        or len(set(gold)) != len(gold)
        or not set(gold).issubset(sessions)
    ):
        raise ValueError("S2-R0 session mapping drift")
    episode_sessions = tuple(str(getattr(item, "session_id", "")) for item in episodes)
    if episode_sessions != sessions:
        raise ValueError("S2-R0 episode/session projection drift")
    if corpus_identity_sha256(episodes) != authorization.get(
        "frozen_corpus_identity_sha256"
    ):
        raise ValueError("S2-R0 frozen corpus identity drift")
    if payload_sha256(list(sessions)) != authorization.get(
        "ordered_session_ids_sha256"
    ):
        raise ValueError("S2-R0 ordered session identity drift")
    if payload_sha256(list(gold)) != authorization.get("gold_session_ids_sha256"):
        raise ValueError("S2-R0 gold session identity drift")
    if payload_sha256([str(getattr(item, "name", "")) for item in episodes]) != (
        authorization.get("episode_names_sha256")
    ):
        raise ValueError("S2-R0 episode name identity drift")
    content_hashes = [
        hashlib.sha256(str(getattr(item, "body", "")).encode("utf-8")).hexdigest()
        for item in episodes
    ]
    if payload_sha256(content_hashes) != authorization.get(
        "episode_content_hash_sequence_sha256"
    ):
        raise ValueError("S2-R0 episode content identity drift")
    if len(gold) != authorization.get("gold_session_count"):
        raise ValueError("S2-R0 gold session count drift")
    return question, sessions, gold


def execute_s2r0_once(
    *,
    authorization_path: Path,
    consumption_path: Path,
    failure_path: Path,
    binding_paths: Mapping[str, Path],
    dependencies: S2R0ControllerDependencies,
    git_commit: str,
    expected_run_id: str,
) -> S2R0ExecutionOutcome:
    """Consume once, build outside a loop, run once, seal, and stop."""

    _consumed, authorization_sha256, authorization = consume_s2r0_authorization(
        Path(authorization_path),
        Path(consumption_path),
        binding_paths=binding_paths,
        expected_run_id=expected_run_id,
        git_commit=git_commit,
    )
    consumption_sha256 = sha256_file(Path(consumption_path))
    result_path = Path(str(authorization["expected_output_path"]))
    counters = ProbeCounters()
    runtime: S2R0Runtime | None = None
    try:
        history = dict(dependencies.load_history())
        episodes = list(dependencies.build_episodes(history))
        question, sessions, gold = _validate_history(
            history, episodes, authorization
        )
        search_config = dependencies.build_search_config()
        config_identity = search_config_identity(search_config)
        if config_identity != authorization.get("retrieval_config"):
            raise ValueError("S2-R0 retrieval config identity drift")
        env = dict(dependencies.load_env())
        runtime = dependencies.build_runtime(env)
        counters = runtime.counters
        result = asyncio.run(
            _run_and_close(
                runtime=runtime,
                run_probe=dependencies.run_probe,
                probe_kwargs={
                    "graph": runtime.graphiti,
                    "query": question,
                    "namespace": EXPECTED_NAMESPACE,
                    "episodes": episodes,
                    "expected_frozen_session_ids": sessions,
                    "expected_corpus_identity_sha256": authorization[
                        "frozen_corpus_identity_sha256"
                    ],
                    "answer_session_ids": gold,
                    "edge_attributed_source_session_coverage": 0.0,
                    "search_config": search_config,
                    "top_k": 10,
                    "counters": counters,
                },
            )
        )
        dependencies.finalize_probe(
            result_path,
            run_id=expected_run_id,
            history_id=EXPECTED_HISTORY_ID,
            namespace=EXPECTED_NAMESPACE,
            result=result,
            reference_sanity_sha256=authorization["binding_sha256"][
                "historical_s2_reference"
            ],
            authorization_sha256=authorization_sha256,
            consumption_sha256=consumption_sha256,
            dataset_sha256=authorization["dataset_sha256"],
            frozen_split_sha256=authorization["frozen_split_sha256"],
            source_sha256=authorization["binding_sha256"],
            git_commit=git_commit,
        )
        return S2R0ExecutionOutcome(
            status="COMPLETED", artifact_path=result_path, run_id=expected_run_id
        )
    except Exception as error:
        failure = finalize_s2r0_failure(
            Path(failure_path),
            run_id=expected_run_id,
            history_id=EXPECTED_HISTORY_ID,
            namespace=EXPECTED_NAMESPACE,
            error=error,
            counters=counters,
            authorization_sha256=authorization_sha256,
            consumption_sha256=consumption_sha256,
            git_commit=git_commit,
        )
        return S2R0ExecutionOutcome(
            status=str(failure["payload"]["status"]),
            artifact_path=Path(failure_path),
            run_id=expected_run_id,
        )
