#!/usr/bin/env python3
"""Run the fixed graph-derived QA overlay after all three baselines seal.

Construction targets are verified before any live client is created.  The
quality path is read-only and stores one recoverable private/public bundle per
method/history so a process interruption never requires resampling a completed
answer.
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from collections.abc import Mapping
from pathlib import Path
from typing import Any


PROJECT = Path(__file__).resolve().parents[1]
LEGACY = PROJECT.parent / "membind-validation"
NATIVE_RUNS_ROOT = PROJECT / "artifacts/paper_eval/native_baseline/runs"
SUITE_RUNS_ROOT = PROJECT / "artifacts/paper_eval/baseline_suite/runs"
OUTPUT_ROOT = PROJECT / "artifacts/paper_eval/graph_quality_overlay/runs"
CONSTRUCTION_BASE_URL = "http://10.87.5.247:8000/v1/"
CONSTRUCTION_MODEL = "qwen3-32b-fp8"

for source in (PROJECT / "src", LEGACY / "src"):
    if str(source) not in sys.path:
        sys.path.insert(0, str(source))

from paper_eval.artifacts import payload_sha256
from paper_eval.development_graph_quality_input import (
    load_development_graph_quality_records,
)
from paper_eval.graph_quality_judge import build_graph_quality_qwen_judge
from paper_eval.graph_quality_live import build_graph_quality_runtime
from paper_eval.graph_quality_overlay import (
    GraphQualityInputs,
    GraphQualityQuestionResult,
    run_graph_quality_question,
)
from paper_eval.graph_quality_suite import (
    GraphQualityTarget,
    discover_graph_quality_targets,
    run_graph_quality_targets,
)
from paper_eval.graph_quality_stages import GraphQualityStageStore
from paper_eval.graph_quality_transport import GraphQualityTransport
from paper_eval.graphiti_longmemeval_quality import (
    build_fresh_graph_quality_search_config,
)
from paper_eval.s2_retrieval_probe import (
    ProbeCounters,
    _expected_corpus_rows,
    _preflight_corpus,
    _read_only_query_guard,
)
from paper_eval.temporal_fact_reader import TemporalFactReader


async def _close(component: object, method_name: str) -> None:
    method = getattr(component, method_name, None)
    if not callable(method):
        return
    value = method()
    if value is not None and hasattr(value, "__await__"):
        await value


async def _close_components(*components: object | None) -> list[BaseException]:
    """Close every owned component and retain every cleanup failure."""

    errors: list[BaseException] = []
    for component in components:
        if component is None:
            continue
        try:
            await _close(component, "aclose")
        except BaseException as error:
            errors.append(error)
    return errors


def _bound_result(
    result: GraphQualityQuestionResult,
    *,
    runtime_identity: Mapping[str, Any],
    observed_episode_count: int,
    expected_content_sha256: str,
    observed_content_sha256: str,
    preflight_read_requests: int,
) -> GraphQualityQuestionResult:
    private = dict(result.private_artifact)
    private["runtime_identity"] = dict(runtime_identity)
    private["corpus_preflight"] = {
        "observed_episode_count": observed_episode_count,
        "expected_name_content_map_sha256": expected_content_sha256,
        "observed_name_content_map_sha256": observed_content_sha256,
        "neo4j_read_requests": preflight_read_requests,
    }
    public = dict(result.public_artifact)
    public.pop("payload_sha256", None)
    public.update(
        {
            "runtime_identity": dict(runtime_identity),
            "runtime_identity_sha256": payload_sha256(runtime_identity),
            "corpus_preflight": {
                "observed_episode_count": observed_episode_count,
                "expected_name_content_map_sha256": expected_content_sha256,
                "observed_name_content_map_sha256": observed_content_sha256,
                "neo4j_read_requests": preflight_read_requests,
                "pass": expected_content_sha256 == observed_content_sha256,
            },
            "private_artifact_sha256": payload_sha256(private),
        }
    )
    public["payload_sha256"] = payload_sha256(public)
    return GraphQualityQuestionResult(
        public_artifact=public,
        private_artifact=private,
    )


async def _run_live(
    *,
    overlay_run_id: str,
    targets: tuple[GraphQualityTarget, ...],
    records: Mapping[str, Mapping[str, Any]],
    runtime: Any,
    reader: TemporalFactReader,
    judge: Any,
) -> dict[str, Any]:
    from dataset import build_episodes

    runtime_identity_sha256 = payload_sha256(runtime.public_identity)
    search_config = build_fresh_graph_quality_search_config()
    model_dump = getattr(search_config, "model_dump", None)
    if not callable(model_dump):
        raise ValueError("graph-quality retrieval identity is unavailable")
    retrieval_identity = model_dump(mode="json")
    if not isinstance(retrieval_identity, dict):
        raise ValueError("graph-quality retrieval identity is invalid")
    quality_identity = {
        "retrieval_config_sha256": payload_sha256(retrieval_identity),
        "reader_config_sha256": reader.config_sha256,
        "judge_config_sha256": judge.config_sha256,
    }

    async def evaluate(
        target: GraphQualityTarget, attempt_root: Path
    ) -> GraphQualityQuestionResult:
        record = records.get(target.history_id)
        if not isinstance(record, Mapping):
            raise ValueError("graph-quality history is missing from the dataset")
        episodes = tuple(build_episodes(dict(record)))
        frozen_session_ids = tuple(
            str(getattr(value, "session_id", "")) for value in episodes
        )
        if len(episodes) != target.episode_count or any(
            not value for value in frozen_session_ids
        ):
            raise ValueError("graph-quality target corpus size or identity drift")
        preflight_counters = ProbeCounters()
        with _read_only_query_guard(runtime.graphiti.driver, preflight_counters):
            corpus = await _preflight_corpus(
                driver=runtime.graphiti.driver,
                namespace=target.namespace,
                expected_rows=_expected_corpus_rows(episodes),
                expected_frozen_session_ids=frozen_session_ids,
            )
        if corpus.observed_session_count != target.episode_count:
            raise ValueError("graph-quality observed episode corpus is incomplete")
        gold = record.get("answer_session_ids")
        if not isinstance(gold, list) or not gold:
            raise ValueError("graph-quality gold session inventory is invalid")
        inputs = GraphQualityInputs(
            overlay_run_id=overlay_run_id,
            method=target.method,
            history_id=target.history_id,
            namespace=target.namespace,
            question=str(record.get("question", "")),
            question_date=str(record.get("question_date", "")),
            question_type=str(record.get("question_type", "")),
            reference_answer=str(record.get("answer", "")),
            answer_session_ids=tuple(str(value) for value in gold),
            construction_result_sha256=target.construction_result_sha256,
        )
        result = await run_graph_quality_question(
            inputs=inputs,
            graph=runtime.graphiti,
            episode_uuid_to_session_id=corpus.uuid_to_session_id,
            reader=reader,
            judge=judge,
            stage_store=GraphQualityStageStore(attempt_root),
            runtime_identity_sha256=runtime_identity_sha256,
        )
        return _bound_result(
            result,
            runtime_identity=runtime.public_identity,
            observed_episode_count=corpus.observed_session_count,
            expected_content_sha256=corpus.expected_name_content_map_sha256,
            observed_content_sha256=corpus.observed_name_content_map_sha256,
            preflight_read_requests=preflight_counters.neo4j_read_requests,
        )

    return await run_graph_quality_targets(
        overlay_run_id=overlay_run_id,
        targets=targets,
        run_root=OUTPUT_ROOT / overlay_run_id,
        evaluate=evaluate,
        runtime_identity=runtime.public_identity,
        quality_identity=quality_identity,
    )


async def _run_live_and_close(
    *,
    overlay_run_id: str,
    targets: tuple[GraphQualityTarget, ...],
    records: Mapping[str, Mapping[str, Any]],
    runtime: Any,
    reader: TemporalFactReader,
    transport: Any,
    judge: Any,
) -> dict[str, Any]:
    """Run live work and close all async clients on that same event loop."""

    report: dict[str, Any] | None = None
    primary_error: BaseException | None = None
    try:
        report = await _run_live(
            overlay_run_id=overlay_run_id,
            targets=targets,
            records=records,
            runtime=runtime,
            reader=reader,
            judge=judge,
        )
    except BaseException as error:
        primary_error = error
    cleanup_errors = await _close_components(judge, transport, runtime)
    if primary_error is not None:
        if cleanup_errors:
            raise BaseExceptionGroup(
                "graph-quality live execution and cleanup failed",
                [primary_error, *cleanup_errors],
            )
        raise primary_error
    if cleanup_errors:
        raise BaseExceptionGroup("graph-quality cleanup failed", cleanup_errors)
    if report is None:
        raise RuntimeError("graph-quality live execution returned no report")
    return report


async def _build_clients_run_live_and_close(
    *,
    overlay_run_id: str,
    targets: tuple[GraphQualityTarget, ...],
    records: Mapping[str, Mapping[str, Any]],
    env: Mapping[str, str],
    runtime: Any,
) -> dict[str, Any]:
    """Construct, use, and close async clients within one event loop."""

    transport = None
    judge = None
    try:
        api_key = env.get("CONSTRUCTION_LLM_API_KEY") or "not-required"
        transport = GraphQualityTransport(
            model=CONSTRUCTION_MODEL,
            base_url=CONSTRUCTION_BASE_URL,
            api_key=api_key,
            timeout_seconds=180.0,
        )
        reader = TemporalFactReader(
            model=CONSTRUCTION_MODEL,
            transport=transport,
        )
        judge = build_graph_quality_qwen_judge(
            base_url=CONSTRUCTION_BASE_URL,
            api_key=api_key,
        )
    except BaseException as construction_error:
        cleanup_errors = await _close_components(judge, transport, runtime)
        if cleanup_errors:
            raise BaseExceptionGroup(
                "graph-quality construction and cleanup failed",
                [construction_error, *cleanup_errors],
            )
        raise
    return await _run_live_and_close(
        overlay_run_id=overlay_run_id,
        targets=targets,
        records=records,
        runtime=runtime,
        reader=reader,
        transport=transport,
        judge=judge,
    )


def _build_run_live_and_close(
    *,
    overlay_run_id: str,
    targets: tuple[GraphQualityTarget, ...],
    records: Mapping[str, Mapping[str, Any]],
    env: Mapping[str, str],
) -> dict[str, Any]:
    """Build Graphiti before the loop, then own one async client lifecycle."""

    runtime = build_graph_quality_runtime(env=env)
    return asyncio.run(
        _build_clients_run_live_and_close(
            overlay_run_id=overlay_run_id,
            targets=targets,
            records=records,
            env=env,
            runtime=runtime,
        )
    )


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Run read-only graph QA after U0/A0/P construction completes."
    )
    parser.add_argument("overlay_run_id")
    parser.add_argument("--native-run", required=True)
    parser.add_argument("--suite-run", required=True)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    # Discovery is deliberately first: an incomplete suite causes no live I/O.
    try:
        targets = discover_graph_quality_targets(
            native_runs_root=NATIVE_RUNS_ROOT,
            suite_runs_root=SUITE_RUNS_ROOT,
            native_run_id=args.native_run,
            suite_run_id=args.suite_run,
        )
        records = load_development_graph_quality_records()
    except BaseException as error:
        print(
            "STOP before_live_io error_class="
            f"{type(error).__module__}.{type(error).__qualname__}",
            flush=True,
        )
        return 1

    from graphiti_native import load_env_file

    env = load_env_file(LEGACY / ".env")
    try:
        if env.get("CONSTRUCTION_LLM_BASE_URL", "").rstrip("/") != (
            CONSTRUCTION_BASE_URL.rstrip("/")
        ) or env.get("CONSTRUCTION_LLM_MODEL") != CONSTRUCTION_MODEL:
            raise ValueError("graph-quality Reader deployment identity drift")
        report = _build_run_live_and_close(
            overlay_run_id=args.overlay_run_id,
            targets=targets,
            records=records,
            env=env,
        )
    except BaseException as error:
        print(
            "STOP error_class="
            f"{type(error).__module__}.{type(error).__qualname__}",
            flush=True,
        )
        return 1
    print(
        f"PASS overlay_run_id={args.overlay_run_id} "
        f"sha256={report['payload_sha256']}",
        flush=True,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
