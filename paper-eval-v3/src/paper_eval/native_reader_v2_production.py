"""Production wiring for the single frozen Native Reader-v2 canary."""

from __future__ import annotations

import inspect
import json
import sys
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .native_reader_v2 import OfficialConSessionReader
from .native_reader_v2_controller import ReaderV2LiveExecutor
from .native_reader_v2_qualification import CANARY_HISTORY_ID, CANARY_NAMESPACE
from .s2_adapters import OpenAIChatCompletionsTransport, build_qualified_qwen_judge
from .s2_completion_chain import execute_bounded_completion_chain
from .s2_completion_production import (
    EXPECTED_BASE_URL,
    EXPECTED_MODEL,
    EXPECTED_NEO4J_URI,
    LEGACY,
)
from .s2_formal_retrieval import run_formal_session_retrieval
from .s2_live import S2LiveInputs
from .s2_retrieval_probe import (
    build_episode_bm25_search_config,
    corpus_identity_sha256,
)
from .s2_r0_live import build_read_only_graphiti


DEFAULT_DATASET = Path(
    "/data/predator/ly/Mem/data/raw/longmemeval-cleaned/longmemeval_s_cleaned.json"
)
EXPECTED_SESSION_COUNT = 49


@dataclass(frozen=True)
class ReaderV2ProductionFactories:
    load_history: Callable[[Path], Mapping[str, Any]]
    build_episodes: Callable[[Mapping[str, Any]], Sequence[Any]]
    build_runtime: Callable[[Mapping[str, str]], Any]
    build_search_config: Callable[[], Any]
    build_transport: Callable[..., Any]
    build_reader: Callable[..., Any]
    build_judge: Callable[..., Any]
    run_retrieval: Callable[..., Awaitable[Any] | Any]
    execute_chain: Callable[..., Awaitable[Any] | Any]
    corpus_identity: Callable[[Sequence[Any]], str]


@dataclass(frozen=True)
class ReaderV2CanaryInputs(S2LiveInputs):
    """Retain S2 input shape while binding the disclosed historical C2 graph."""

    def __post_init__(self) -> None:
        for field in (
            "run_id",
            "history_id",
            "namespace",
            "question",
            "question_date",
            "question_type",
            "reference_answer",
        ):
            value = getattr(self, field)
            if not isinstance(value, str) or not value:
                raise ValueError(f"{field} must be nonempty")
        if self.history_id != CANARY_HISTORY_ID or self.namespace != CANARY_NAMESPACE:
            raise ValueError("Reader-v2 canary identity drift")
        if not isinstance(self.answer_session_ids, tuple) or not self.answer_session_ids:
            raise ValueError("answer_session_ids must be a nonempty tuple")
        if any(not isinstance(value, str) or not value for value in self.answer_session_ids):
            raise ValueError("answer_session_ids contain an invalid value")


def _load_history(path: Path) -> dict[str, Any]:
    try:
        records = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise ValueError(f"Reader-v2 dataset unreadable: {type(error).__name__}") from None
    if not isinstance(records, list):
        raise ValueError("Reader-v2 dataset must be a JSON list")
    matches = [
        dict(record)
        for record in records
        if isinstance(record, Mapping)
        and str(record.get("question_id")) == CANARY_HISTORY_ID
    ]
    if len(matches) != 1:
        raise ValueError("Reader-v2 canary must occur exactly once")
    return matches[0]


def _production_factories() -> ReaderV2ProductionFactories:
    legacy_source = str(LEGACY / "src")
    if legacy_source not in sys.path:
        sys.path.insert(0, legacy_source)
    from dataset import build_episodes

    return ReaderV2ProductionFactories(
        load_history=_load_history,
        build_episodes=lambda record: build_episodes(dict(record)),
        build_runtime=lambda env: build_read_only_graphiti(env=env),
        build_search_config=build_episode_bm25_search_config,
        build_transport=lambda **kwargs: OpenAIChatCompletionsTransport(**kwargs),
        build_reader=lambda **kwargs: OfficialConSessionReader(**kwargs),
        build_judge=lambda **kwargs: build_qualified_qwen_judge(**kwargs),
        run_retrieval=run_formal_session_retrieval,
        execute_chain=execute_bounded_completion_chain,
        corpus_identity=corpus_identity_sha256,
    )


async def _await(value: Awaitable[Any] | Any) -> Any:
    return await value if inspect.isawaitable(value) else value


def _runtime_env(value: Mapping[str, str]) -> dict[str, str]:
    env = {key: str(child) for key, child in value.items()}
    env["CONSTRUCTION_LLM_API_KEY"] = env.get("CONSTRUCTION_LLM_API_KEY") or "not-required"
    if (
        env.get("NEO4J_URI") != EXPECTED_NEO4J_URI
        or not env.get("NEO4J_USER")
        or not env.get("NEO4J_PASSWORD")
        or env.get("CONSTRUCTION_LLM_BASE_URL", "").rstrip("/")
        != EXPECTED_BASE_URL.rstrip("/")
        or env.get("CONSTRUCTION_LLM_MODEL") != EXPECTED_MODEL
    ):
        raise ValueError("Reader-v2 runtime identity drift")
    env["CONSTRUCTION_LLM_BASE_URL"] = EXPECTED_BASE_URL
    return env


def build_reader_v2_live_executor(
    *,
    env: Mapping[str, str],
    dataset_path: Path = DEFAULT_DATASET,
    factories: ReaderV2ProductionFactories | None = None,
    run_id: str = "native-reader-v2-canary-20260814-001",
) -> ReaderV2LiveExecutor:
    """Build clients after authority consumption without issuing live requests."""

    selected = factories or _production_factories()
    record = dict(selected.load_history(Path(dataset_path)))
    session_ids = record.get("haystack_session_ids")
    gold_ids = record.get("answer_session_ids")
    if (
        record.get("question_id") != CANARY_HISTORY_ID
        or record.get("question_type") != "knowledge-update"
    ):
        raise ValueError("Reader-v2 canary identity drift")
    if (
        not isinstance(session_ids, list)
        or len(session_ids) != EXPECTED_SESSION_COUNT
        or len(set(str(value) for value in session_ids)) != EXPECTED_SESSION_COUNT
        or not isinstance(gold_ids, list)
        or len(gold_ids) != 2
    ):
        raise ValueError("Reader-v2 canary session corpus drift")
    episodes = tuple(selected.build_episodes(record))
    frozen_sessions = tuple(str(value) for value in session_ids)
    if tuple(str(getattr(item, "session_id", "")) for item in episodes) != frozen_sessions:
        raise ValueError("Reader-v2 episode/session projection drift")

    loaded = _runtime_env(env)
    runtime = selected.build_runtime(loaded)
    search_config = selected.build_search_config()
    transport = selected.build_transport(
        model=EXPECTED_MODEL,
        base_url=EXPECTED_BASE_URL,
        api_key=loaded["CONSTRUCTION_LLM_API_KEY"],
        timeout_seconds=180.0,
    )
    reader = selected.build_reader(model=EXPECTED_MODEL, transport=transport)
    if not isinstance(reader, OfficialConSessionReader):
        raise ValueError("Reader-v2 production Reader drift")
    judge = selected.build_judge(
        base_url=EXPECTED_BASE_URL,
        api_key=loaded["CONSTRUCTION_LLM_API_KEY"],
    )
    corpus_hash = selected.corpus_identity(episodes)
    inputs = ReaderV2CanaryInputs(
        run_id=run_id,
        history_id=CANARY_HISTORY_ID,
        namespace=CANARY_NAMESPACE,
        question=str(record.get("question", "")),
        question_date=str(record.get("question_date", "")),
        question_type=str(record.get("question_type", "")),
        reference_answer=str(record.get("answer", "")),
        answer_session_ids=tuple(str(value) for value in gold_ids),
    )

    async def retrieve(*, question: str, namespace: str) -> Any:
        if question != inputs.question or namespace != inputs.namespace:
            raise ValueError("Reader-v2 retrieval input drift")
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

    return ReaderV2LiveExecutor(execute=execute, close=close)
