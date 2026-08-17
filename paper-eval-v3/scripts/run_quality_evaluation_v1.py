#!/usr/bin/env python3
"""Run Quality Evaluation v1 over sealed U0/A0/P(C=2) graphs only.

The command never constructs, mutates, or cleans a graph.  It evaluates U0
first, seals the development freeze decision, and only then applies the exact
same retrieval/context/Reader/Judge identity to A0 and P(C=2).  Every question
uses private-first recoverable checkpoints so a disconnect does not resample a
completed Reader or Judge output.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import re
import sys
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any


PROJECT = Path(__file__).resolve().parents[1]
LEGACY = PROJECT.parent / "membind-validation"
NATIVE_RUNS_ROOT = PROJECT / "artifacts/paper_eval/native_baseline/runs"
SUITE_RUNS_ROOT = PROJECT / "artifacts/paper_eval/baseline_suite/runs"
OUTPUT_ROOT = PROJECT / "artifacts/paper_eval/quality_evaluation_v1/runs"
CONSTRUCTION_BASE_URL = "http://10.87.5.247:8000/v1/"
CONSTRUCTION_MODEL = "qwen3-32b-fp8"
RUN_ID = re.compile(r"^qev1-[a-z0-9][a-z0-9-]{2,63}$")
METHOD_SLUG = {"U0": "u0", "A0": "a0", "P(C=2)": "pc2"}


def _safe_error_message(error: BaseException) -> str:
    """Keep runner-owned diagnostics useful without dumping request payloads."""

    value = str(error).replace("\n", " ").replace("\r", " ").strip()
    return value[:300] if value else "no_message"

for source in (PROJECT / "src", LEGACY / "src"):
    if str(source) not in sys.path:
        sys.path.insert(0, str(source))

from paper_eval.artifacts import atomic_write_json, payload_sha256
from paper_eval.baseline_suite import DEVELOPMENT_HISTORIES
from paper_eval.development_graph_quality_input import (
    load_development_graph_quality_records,
)
from paper_eval.graph_quality_judge import build_graph_quality_qwen_judge
from paper_eval.graph_quality_live import build_graph_quality_runtime
from paper_eval.graph_quality_stages import GraphQualityStageStore
from paper_eval.graph_quality_suite import (
    GraphQualityTarget,
    discover_graph_quality_targets,
)
from paper_eval.graph_quality_transport import GraphQualityTransport
from paper_eval.quality_evaluation_v1 import (
    CONTEXT_POLICY,
    CONTEXT_POLICY_SHA256,
)
from paper_eval.quality_evaluation_v1_overlay import (
    QualityV1Inputs,
    QualityV1QuestionResult,
    run_quality_v1_question,
)
from paper_eval.quality_evaluation_v1_reader import QualityEvaluationV1Reader
from paper_eval.quality_evaluation_v1_retrieval import (
    build_quality_v1_search_config,
)
from paper_eval.quality_evaluation_v1_suite import (
    decide_u0_freeze,
    load_or_restore_quality_v1_bundle,
    persist_quality_v1_bundle,
    summarize_quality_v1,
)
from paper_eval.s2_retrieval_probe import (
    ProbeCounters,
    _expected_corpus_rows,
    _preflight_corpus,
    _read_only_query_guard,
)


def _partition_targets(
    targets: Sequence[Any],
) -> tuple[tuple[Any, ...], tuple[Any, ...]]:
    expected = [
        (method, history)
        for method in ("U0", "A0", "P(C=2)")
        for history in DEVELOPMENT_HISTORIES
    ]
    observed = [
        (getattr(value, "method", None), getattr(value, "history_id", None))
        for value in targets
    ]
    if observed != expected:
        raise ValueError("Quality v1 target inventory is incomplete or reordered")
    boundary = len(DEVELOPMENT_HISTORIES)
    return tuple(targets[:boundary]), tuple(targets[boundary:])


def _attempt_inventory(unit_root: Path) -> list[tuple[int, Path]]:
    root = Path(unit_root)
    if not root.exists():
        return []
    if not root.is_dir() or root.is_symlink():
        raise ValueError("Quality v1 attempt inventory is invalid")
    result: list[tuple[int, Path]] = []
    for path in sorted(root.iterdir()):
        match = re.fullmatch(r"attempt-([0-9]{3})", path.name)
        if match is None or not path.is_dir() or path.is_symlink():
            raise ValueError("Quality v1 attempt inventory is invalid")
        result.append((int(match.group(1)), path))
    if [ordinal for ordinal, _path in result] != list(range(1, len(result) + 1)):
        raise ValueError("Quality v1 attempt inventory is noncontiguous")
    return result


def _select_attempt_root(
    unit_root: Path,
) -> tuple[str, int, Path, dict[str, Any] | None]:
    attempts = _attempt_inventory(unit_root)
    completed: list[tuple[int, Path, dict[str, Any]]] = []
    incomplete: list[tuple[int, Path]] = []
    for ordinal, path in attempts:
        bundle = path / "private_bundle.json"
        failure = path / "failure.json"
        if bundle.exists() and failure.exists():
            raise ValueError("Quality v1 attempt has both completion and failure")
        if bundle.exists():
            completed.append(
                (ordinal, path, load_or_restore_quality_v1_bundle(path))
            )
        elif not failure.exists():
            incomplete.append((ordinal, path))
    if len(completed) > 1:
        raise ValueError("Quality v1 unit has multiple completed attempts")
    if completed:
        if incomplete or completed[0][0] != attempts[-1][0]:
            raise ValueError("Quality v1 attempt inventory has work after completion")
        ordinal, path, public = completed[0]
        return "REUSE", ordinal, path, public
    if len(incomplete) > 1 or (
        incomplete and incomplete[0][0] != attempts[-1][0]
    ):
        raise ValueError("Quality v1 attempt inventory has multiple incomplete attempts")
    if incomplete:
        ordinal, path = incomplete[0]
        return "RUN", ordinal, path, None
    ordinal = len(attempts) + 1
    if ordinal > 999:
        raise ValueError("Quality v1 attempt inventory is exhausted")
    return "RUN", ordinal, Path(unit_root) / f"attempt-{ordinal:03d}", None


def _quality_identity(reader: Any, judge: Any) -> dict[str, str]:
    config = build_quality_v1_search_config()
    dump = getattr(config, "model_dump", None)
    if not callable(dump):
        raise ValueError("Quality v1 retrieval identity is unavailable")
    retrieval = dump(mode="json")
    if not isinstance(retrieval, dict):
        raise ValueError("Quality v1 retrieval identity is invalid")
    identity = {
        "retrieval_config_sha256": payload_sha256(retrieval),
        "context_policy_sha256": CONTEXT_POLICY_SHA256,
        "reader_config_sha256": str(getattr(reader, "config_sha256", "")),
        "judge_config_sha256": str(getattr(judge, "config_sha256", "")),
    }
    if any(not re.fullmatch(r"[0-9a-f]{64}", value) for value in identity.values()):
        raise ValueError("Quality v1 component identity is invalid")
    return identity


def _verified_reused_public(
    public: Mapping[str, Any],
    *,
    run_id: str,
    target: Any,
    runtime_identity_sha256: str,
    quality_identity: Mapping[str, str],
) -> dict[str, Any]:
    value = dict(public)
    observed_hash = value.get("payload_sha256")
    if observed_hash != payload_sha256(
        {key: child for key, child in value.items() if key != "payload_sha256"}
    ):
        raise ValueError("Quality v1 reused public artifact hash mismatch")
    if (
        value.get("overlay_run_id") != run_id
        or value.get("method") != target.method
        or value.get("history_id") != target.history_id
        or value.get("namespace_sha256")
        != hashlib.sha256(target.namespace.encode("utf-8")).hexdigest()
        or value.get("construction_result_sha256")
        != target.construction_result_sha256
        or value.get("runtime_identity_sha256") != runtime_identity_sha256
        or value.get("quality_identity") != dict(quality_identity)
    ):
        raise ValueError("Quality v1 reused public artifact identity drift")
    return value


def _bound_result(
    result: QualityV1QuestionResult,
    *,
    runtime_identity: Mapping[str, Any],
    quality_identity: Mapping[str, str],
    corpus: Any,
    preflight_read_requests: int,
) -> QualityV1QuestionResult:
    private = dict(result.private_artifact)
    private.pop("payload_sha256", None)
    private.update(
        {
            "runtime_identity": dict(runtime_identity),
            "quality_identity": dict(quality_identity),
            "corpus_preflight": {
                "observed_episode_count": corpus.observed_session_count,
                "expected_name_content_map_sha256": (
                    corpus.expected_name_content_map_sha256
                ),
                "observed_name_content_map_sha256": (
                    corpus.observed_name_content_map_sha256
                ),
                "neo4j_read_requests": preflight_read_requests,
            },
        }
    )
    private["payload_sha256"] = payload_sha256(private)
    public = dict(result.public_artifact)
    public.pop("payload_sha256", None)
    public.update(
        {
            "runtime_identity": dict(runtime_identity),
            "runtime_identity_sha256": payload_sha256(runtime_identity),
            "quality_identity": dict(quality_identity),
            "corpus_preflight": {
                "observed_episode_count": corpus.observed_session_count,
                "expected_name_content_map_sha256": (
                    corpus.expected_name_content_map_sha256
                ),
                "observed_name_content_map_sha256": (
                    corpus.observed_name_content_map_sha256
                ),
                "neo4j_read_requests": preflight_read_requests,
                "pass": (
                    corpus.expected_name_content_map_sha256
                    == corpus.observed_name_content_map_sha256
                ),
            },
            "private_payload_sha256": private["payload_sha256"],
        }
    )
    public["payload_sha256"] = payload_sha256(public)
    return QualityV1QuestionResult(public, private)


def _row(public: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "method": public["method"],
        "history_id": public["history_id"],
        "qa_accuracy": public["qa_accuracy"],
        "judge_valid_denominator": public["judge_valid_denominator"],
        "failure_category": public["failure_category"],
        "session_metrics": dict(public["session_metrics"]),
        "edge_provenance_metrics": dict(public["edge_provenance_metrics"]),
        "temporal_diagnostics": dict(public["temporal_diagnostics"]),
        "context": dict(public["context"]),
        "reader": dict(public["reader"]) if public.get("reader") else None,
        "payload_sha256": public["payload_sha256"],
    }


async def _close(component: object | None) -> None:
    if component is None:
        return
    method = getattr(component, "aclose", None)
    if callable(method):
        value = method()
        if value is not None and hasattr(value, "__await__"):
            await value


async def _run_clients(
    *,
    run_id: str,
    run_root: Path,
    targets: Sequence[GraphQualityTarget],
    records: Mapping[str, Mapping[str, Any]],
    env: Mapping[str, str],
    runtime: Any,
) -> dict[str, Any]:
    from dataset import build_episodes

    transport = None
    judge = None
    primary_error: BaseException | None = None
    report: dict[str, Any] | None = None
    try:
        api_key = env.get("CONSTRUCTION_LLM_API_KEY") or "not-required"
        transport = GraphQualityTransport(
            model=CONSTRUCTION_MODEL,
            base_url=CONSTRUCTION_BASE_URL,
            api_key=api_key,
            timeout_seconds=180.0,
        )
        reader = QualityEvaluationV1Reader(
            model=CONSTRUCTION_MODEL,
            transport=transport,
        )
        judge = build_graph_quality_qwen_judge(
            base_url=CONSTRUCTION_BASE_URL,
            api_key=api_key,
        )
        quality_identity = _quality_identity(reader, judge)
        runtime_identity_sha256 = payload_sha256(runtime.public_identity)
        u0_targets, remaining_targets = _partition_targets(targets)
        rows: list[dict[str, Any]] = []

        async def one(target: GraphQualityTarget) -> dict[str, Any]:
            unit_root = (
                run_root / "units" / METHOD_SLUG[target.method] / target.history_id
            )
            action, attempt, attempt_root, reused = _select_attempt_root(unit_root)
            if action == "REUSE":
                assert reused is not None
                public = _verified_reused_public(
                    reused,
                    run_id=run_id,
                    target=target,
                    runtime_identity_sha256=runtime_identity_sha256,
                    quality_identity=quality_identity,
                )
                print(
                    f"REUSE method={target.method} history={target.history_id} "
                    f"attempt={attempt}",
                    flush=True,
                )
                return public

            record = records.get(target.history_id)
            if not isinstance(record, Mapping):
                raise ValueError("Quality v1 history record is missing")
            episodes = tuple(build_episodes(dict(record)))
            frozen_session_ids = tuple(
                str(getattr(value, "session_id", "")) for value in episodes
            )
            if len(episodes) != target.episode_count or any(
                not value for value in frozen_session_ids
            ):
                raise ValueError("Quality v1 target corpus identity drift")
            attempt_root.mkdir(parents=True, exist_ok=True)
            atomic_write_json(
                attempt_root / "target.json",
                {
                    "method": target.method,
                    "history_id": target.history_id,
                    "namespace_sha256": hashlib.sha256(
                        target.namespace.encode("utf-8")
                    ).hexdigest(),
                    "construction_result_sha256": (
                        target.construction_result_sha256
                    ),
                    "attempt_ordinal": attempt,
                },
            )
            counters = ProbeCounters()
            try:
                with _read_only_query_guard(runtime.graphiti.driver, counters):
                    corpus = await _preflight_corpus(
                        driver=runtime.graphiti.driver,
                        namespace=target.namespace,
                        expected_rows=_expected_corpus_rows(episodes),
                        expected_frozen_session_ids=frozen_session_ids,
                    )
                if corpus.observed_session_count != target.episode_count:
                    raise ValueError("Quality v1 observed corpus is incomplete")
                result = await run_quality_v1_question(
                    inputs=QualityV1Inputs(
                        overlay_run_id=run_id,
                        method=target.method,
                        history_id=target.history_id,
                        namespace=target.namespace,
                        construction_result_sha256=(
                            target.construction_result_sha256
                        ),
                        record=record,
                    ),
                    graph=runtime.graphiti,
                    episode_uuid_to_session_id=corpus.uuid_to_session_id,
                    reader=reader,
                    judge=judge,
                    stage_store=GraphQualityStageStore(attempt_root),
                    runtime_identity_sha256=runtime_identity_sha256,
                )
                bound = _bound_result(
                    result,
                    runtime_identity=runtime.public_identity,
                    quality_identity=quality_identity,
                    corpus=corpus,
                    preflight_read_requests=counters.neo4j_read_requests,
                )
                public = persist_quality_v1_bundle(
                    attempt_root,
                    public_artifact=bound.public_artifact,
                    private_artifact=bound.private_artifact,
                )
            except BaseException as error:
                failure = {
                    "schema_version": "membind.paper-eval-v3.quality-v1-failure.v1",
                    "status": "incomplete_non_mergeable",
                    "method": target.method,
                    "history_id": target.history_id,
                    "attempt_ordinal": attempt,
                    "error_class": (
                        f"{type(error).__module__}.{type(error).__qualname__}"
                    ),
                    "error_message": _safe_error_message(error),
                }
                failure["payload_sha256"] = payload_sha256(failure)
                atomic_write_json(attempt_root / "failure.json", failure)
                raise
            reader_public = public.get("reader") or {}
            print(
                f"SEALED method={target.method} history={target.history_id} "
                f"attempt={attempt} qa={public['qa_accuracy']} "
                f"gold_ranks={public['session_metrics']['gold_ranks']} "
                f"context_evidence={public['context']['evidence_count']} "
                f"reader_prompt_tokens={reader_public.get('prompt_tokens')} "
                f"failure_category={public['failure_category']}",
                flush=True,
            )
            return public

        async def phase(phase_targets: Sequence[GraphQualityTarget]) -> None:
            for target in phase_targets:
                public = await one(target)
                rows.append(_row(public))
                progress = {
                    "schema_version": "membind.paper-eval-v3.quality-v1-progress.v1",
                    "run_id": run_id,
                    "status": "RUNNING",
                    "completed_unit_count": len(rows),
                    "completed": [
                        {
                            "method": value["method"],
                            "history_id": value["history_id"],
                            "qa_accuracy": value["qa_accuracy"],
                            "payload_sha256": value["payload_sha256"],
                        }
                        for value in rows
                    ],
                }
                progress["payload_sha256"] = payload_sha256(progress)
                atomic_write_json(run_root / "progress.json", progress)

        await phase(u0_targets)
        decision = decide_u0_freeze(rows)
        decision.update(
            {
                "run_id": run_id,
                "quality_identity": quality_identity,
                "context_policy": CONTEXT_POLICY,
            }
        )
        decision["payload_sha256"] = payload_sha256(decision)
        atomic_write_json(run_root / "U0_FREEZE_DECISION.json", decision)
        print(
            f"U0_DECISION decision={decision['decision']} "
            f"correct={decision['correct_count']}/"
            f"{decision['valid_denominator']}",
            flush=True,
        )
        if decision["decision"] != "FREEZE_QUALITY_EVALUATION_V1":
            report = {
                "schema_version": "membind.paper-eval-v3.quality-v1-report.v1",
                "run_id": run_id,
                "status": "STOPPED_AT_U0_GATE",
                "u0_decision": decision,
                "summary": summarize_quality_v1(rows, methods=("U0",)),
                "quality_identity": quality_identity,
            }
        else:
            await phase(remaining_targets)
            report = {
                "schema_version": "membind.paper-eval-v3.quality-v1-report.v1",
                "run_id": run_id,
                "status": "PASS",
                "u0_decision": decision,
                "summary": summarize_quality_v1(rows),
                "quality_identity": quality_identity,
                "runtime_identity": runtime.public_identity,
                "construction_rerun": False,
                "construction_latency_includes_quality": False,
            }
        report["payload_sha256"] = payload_sha256(report)
        atomic_write_json(run_root / "QUALITY_EVALUATION_V1_RESULTS.json", report)
    except BaseException as error:
        primary_error = error
    cleanup_errors: list[BaseException] = []
    for component in (judge, transport, runtime):
        try:
            await _close(component)
        except BaseException as error:
            cleanup_errors.append(error)
    if primary_error is not None:
        if cleanup_errors:
            raise BaseExceptionGroup(
                "Quality v1 execution and cleanup failed",
                [primary_error, *cleanup_errors],
            )
        raise primary_error
    if cleanup_errors:
        raise BaseExceptionGroup("Quality v1 cleanup failed", cleanup_errors)
    if report is None:
        raise RuntimeError("Quality v1 produced no report")
    return report


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Evaluate sealed U0/A0/P graphs without rerunning construction."
    )
    parser.add_argument("run_id")
    parser.add_argument("--native-run", required=True)
    parser.add_argument("--suite-run", required=True)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    if RUN_ID.fullmatch(args.run_id) is None:
        print("STOP before_live_io error_class=ValueError", flush=True)
        return 1
    run_root = OUTPUT_ROOT / args.run_id
    try:
        targets = discover_graph_quality_targets(
            native_runs_root=NATIVE_RUNS_ROOT,
            suite_runs_root=SUITE_RUNS_ROOT,
            native_run_id=args.native_run,
            suite_run_id=args.suite_run,
        )
        records = load_development_graph_quality_records()
        from graphiti_native import load_env_file

        env = load_env_file(LEGACY / ".env")
        if (
            env.get("CONSTRUCTION_LLM_BASE_URL", "").rstrip("/")
            != CONSTRUCTION_BASE_URL.rstrip("/")
            or env.get("CONSTRUCTION_LLM_MODEL") != CONSTRUCTION_MODEL
        ):
            raise ValueError("Quality v1 Reader deployment identity drift")
        # Graphiti must be built outside an active event loop because its Neo4j
        # driver performs loop-sensitive initialization.
        runtime = build_graph_quality_runtime(env=env)
        report = asyncio.run(
            _run_clients(
                run_id=args.run_id,
                run_root=run_root,
                targets=targets,
                records=records,
                env=env,
                runtime=runtime,
            )
        )
    except BaseException as error:
        print(
            "STOP error_class="
            f"{type(error).__module__}.{type(error).__qualname__} "
            f"message={json.dumps(_safe_error_message(error))}",
            flush=True,
        )
        return 1
    print(
        f"{report['status']} run_id={args.run_id} "
        f"sha256={report['payload_sha256']}",
        flush=True,
    )
    return 0 if report["status"] == "PASS" else 2


if __name__ == "__main__":
    raise SystemExit(main())
