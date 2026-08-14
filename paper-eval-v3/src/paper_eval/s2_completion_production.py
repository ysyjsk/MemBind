"""Production wiring for one authorized, gold-blind S2 completion chain."""

from __future__ import annotations

import inspect
import os
import sys
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .s1_live import load_fixed_history
from .s2_adapters import (
    OpenAIChatCompletionsTransport,
    build_qualified_qwen_judge,
)
from .s2_completion_chain import execute_bounded_completion_chain
from .s2_completion_controller import CompletionLiveExecutor
from .s2_formal_retrieval import run_formal_session_retrieval
from .s2_live import S2LiveInputs
from .s2_retrieval_probe import (
    build_episode_bm25_search_config,
    corpus_identity_sha256,
)
from .s2_r0_live import build_read_only_graphiti
from .s2_session_reader import OfficialSessionReader


ROOT = Path(__file__).resolve().parents[3]
LEGACY = ROOT / "membind-validation"
DEFAULT_DATASET = Path(
    "/data/predator/ly/Mem/data/raw/longmemeval-cleaned/longmemeval_s_cleaned.json"
)
DEFAULT_SPLIT = LEGACY / "artifacts/dataset/frozen_split_v1_3.json"
EXPECTED_HISTORY_ID = "07741c45"
EXPECTED_NAMESPACE = "pev3-s1-20260814-001"
EXPECTED_MODEL = "qwen3-32b-fp8"
EXPECTED_BASE_URL = "http://10.87.5.247:8000/v1/"
EXPECTED_NEO4J_URI = "bolt://localhost:7687"

_ENV_FIELDS = (
    "NEO4J_URI",
    "NEO4J_USER",
    "NEO4J_PASSWORD",
    "CONSTRUCTION_LLM_BASE_URL",
    "CONSTRUCTION_LLM_API_KEY",
    "CONSTRUCTION_LLM_MODEL",
)


def load_completion_env_file(
    path: Path, *, environ: Mapping[str, str] | None = None
) -> dict[str, str]:
    """Load only the six fields needed by this chain without mutating env."""

    selected: dict[str, str] = {}
    try:
        lines = Path(path).read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeError) as error:
        raise ValueError(
            f"completion runtime identity is unreadable: {type(error).__name__}"
        ) from None
    for raw in lines:
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[7:].lstrip()
        key, separator, raw_value = line.partition("=")
        key = key.strip()
        if not separator or key not in _ENV_FIELDS:
            continue
        value = raw_value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
            value = value[1:-1]
        selected[key] = value
    fallback = os.environ if environ is None else environ
    loaded = {
        field: selected.get(field) or str(fallback.get(field, ""))
        for field in _ENV_FIELDS
    }
    loaded["CONSTRUCTION_LLM_API_KEY"] = (
        loaded["CONSTRUCTION_LLM_API_KEY"] or "not-required"
    )
    if not loaded["NEO4J_USER"] or not loaded["NEO4J_PASSWORD"]:
        raise ValueError("completion runtime identity is missing Neo4j credentials")
    if (
        loaded["NEO4J_URI"] != EXPECTED_NEO4J_URI
        or loaded["CONSTRUCTION_LLM_BASE_URL"].rstrip("/")
        != EXPECTED_BASE_URL.rstrip("/")
        or loaded["CONSTRUCTION_LLM_MODEL"] != EXPECTED_MODEL
    ):
        raise ValueError("completion runtime identity drift")
    loaded["CONSTRUCTION_LLM_BASE_URL"] = EXPECTED_BASE_URL
    return loaded


@dataclass(frozen=True)
class CompletionProductionFactories:
    load_history: Callable[[Path, Path], Mapping[str, Any]]
    build_episodes: Callable[[Mapping[str, Any]], Sequence[Any]]
    build_runtime: Callable[[Mapping[str, str]], Any]
    build_search_config: Callable[[], Any]
    build_transport: Callable[..., Any]
    build_reader: Callable[..., Any]
    build_judge: Callable[..., Any]
    run_retrieval: Callable[..., Awaitable[Any] | Any]
    execute_chain: Callable[..., Awaitable[Any] | Any]
    corpus_identity: Callable[[Sequence[Any]], str]


def _production_factories() -> CompletionProductionFactories:
    legacy_source = str(LEGACY / "src")
    if legacy_source not in sys.path:
        sys.path.insert(0, legacy_source)
    from dataset import build_episodes

    return CompletionProductionFactories(
        load_history=lambda dataset, split: load_fixed_history(dataset, split),
        build_episodes=lambda record: build_episodes(dict(record)),
        build_runtime=lambda env: build_read_only_graphiti(env=env),
        build_search_config=build_episode_bm25_search_config,
        build_transport=lambda **kwargs: OpenAIChatCompletionsTransport(**kwargs),
        build_reader=lambda **kwargs: OfficialSessionReader(**kwargs),
        build_judge=lambda **kwargs: build_qualified_qwen_judge(**kwargs),
        run_retrieval=run_formal_session_retrieval,
        execute_chain=execute_bounded_completion_chain,
        corpus_identity=corpus_identity_sha256,
    )


async def _await(value: Awaitable[Any] | Any) -> Any:
    return await value if inspect.isawaitable(value) else value


def build_production_live_executor(
    *,
    env: Mapping[str, str],
    dataset_path: Path = DEFAULT_DATASET,
    split_path: Path = DEFAULT_SPLIT,
    factories: CompletionProductionFactories | None = None,
    run_id: str = "s2-completion-20260814-001",
    expected_session_count: int = 49,
) -> CompletionLiveExecutor:
    """Build clients after authority consumption; no request occurs here."""

    selected = factories or _production_factories()
    record = dict(selected.load_history(Path(dataset_path), Path(split_path)))
    if (
        record.get("question_id") != EXPECTED_HISTORY_ID
        or record.get("question_type") != "knowledge-update"
    ):
        raise ValueError("completion fixed history identity drift")
    session_ids = record.get("haystack_session_ids")
    gold_ids = record.get("answer_session_ids")
    if (
        not isinstance(session_ids, list)
        or len(session_ids) != expected_session_count
        or len(set(str(value) for value in session_ids)) != expected_session_count
        or not isinstance(gold_ids, list)
        or len(gold_ids) != 2
    ):
        raise ValueError("completion session corpus identity drift")
    episodes = tuple(selected.build_episodes(record))
    episode_sessions = tuple(str(getattr(item, "session_id", "")) for item in episodes)
    frozen_sessions = tuple(str(value) for value in session_ids)
    if episode_sessions != frozen_sessions:
        raise ValueError("completion episode/session projection drift")

    loaded_env = dict(env)
    required = {field: str(loaded_env.get(field, "")) for field in _ENV_FIELDS}
    required["CONSTRUCTION_LLM_API_KEY"] = (
        required["CONSTRUCTION_LLM_API_KEY"] or "not-required"
    )
    if (
        required["NEO4J_URI"] != EXPECTED_NEO4J_URI
        or not required["NEO4J_USER"]
        or not required["NEO4J_PASSWORD"]
        or required["CONSTRUCTION_LLM_BASE_URL"].rstrip("/")
        != EXPECTED_BASE_URL.rstrip("/")
        or required["CONSTRUCTION_LLM_MODEL"] != EXPECTED_MODEL
    ):
        raise ValueError("completion runtime identity drift")

    runtime = selected.build_runtime(required)
    search_config = selected.build_search_config()
    transport = selected.build_transport(
        model=EXPECTED_MODEL,
        base_url=EXPECTED_BASE_URL,
        api_key=required["CONSTRUCTION_LLM_API_KEY"],
        timeout_seconds=180.0,
    )
    reader = selected.build_reader(model=EXPECTED_MODEL, transport=transport)
    judge = selected.build_judge(
        base_url=EXPECTED_BASE_URL,
        api_key=required["CONSTRUCTION_LLM_API_KEY"],
    )
    corpus_hash = selected.corpus_identity(episodes)
    inputs = S2LiveInputs(
        run_id=run_id,
        history_id=EXPECTED_HISTORY_ID,
        namespace=EXPECTED_NAMESPACE,
        question=str(record.get("question", "")),
        question_date=str(record.get("question_date", "")),
        question_type=str(record.get("question_type", "")),
        reference_answer=str(record.get("answer", "")),
        answer_session_ids=tuple(str(value) for value in gold_ids),
    )

    async def retrieve(*, question: str, namespace: str) -> Any:
        if question != inputs.question or namespace != inputs.namespace:
            raise ValueError("completion retrieval input drift")
        return await _await(
            selected.run_retrieval(
                graph=runtime.graphiti,
                query=question,
                namespace=namespace,
                episodes=episodes,
                expected_frozen_session_ids=frozen_sessions,
                expected_corpus_identity_sha256=corpus_hash,
                search_config=search_config,
                counters=runtime.counters,
            )
        )

    async def execute(checkpoint: Callable[[str, dict[str, Any]], None]) -> Any:
        return await _await(
            selected.execute_chain(
                inputs=inputs,
                dataset_record=record,
                retrieve=retrieve,
                reader=reader,
                judge=judge,
                on_checkpoint=checkpoint,
            )
        )

    async def close() -> None:
        errors: list[BaseException] = []
        for component, method_name in (
            (runtime.graphiti, "close"),
            (transport, "aclose"),
            (judge, "aclose"),
        ):
            method = getattr(component, method_name, None)
            if not callable(method):
                continue
            try:
                await _await(method())
            except BaseException as error:
                errors.append(error)
        if errors:
            raise errors[0]

    return CompletionLiveExecutor(execute=execute, close=close)
