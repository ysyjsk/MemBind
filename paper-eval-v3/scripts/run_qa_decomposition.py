#!/usr/bin/env python3
"""Run the bounded U0 Top-10 versus gold-only QA decomposition.

The script never rebuilds or mutates Graphiti. It reuses four sealed U0
namespaces, performs one read-only Episode retrieval per history, and seals the
Reader before the Judge for each of two predeclared contexts. Raw material is
written only beneath ignored private paths.
"""

from __future__ import annotations

import argparse
import asyncio
import fcntl
import json
import sys
from collections.abc import Mapping
from pathlib import Path
from typing import Any


PROJECT = Path(__file__).resolve().parents[1]
ROOT = PROJECT.parent
LEGACY = ROOT / "membind-validation"
NATIVE_RUNS_ROOT = PROJECT / "artifacts/paper_eval/native_baseline/runs"
OUTPUT_ROOT = PROJECT / "artifacts/paper_eval/qa_decomposition/runs"
NATIVE_FREEZE = PROJECT / "artifacts/paper_eval/native/NATIVE_BASELINE_V2_FREEZE.json"
DEFAULT_SOURCE_RUN_ID = "nb-20260816-001"
DEFAULT_OVERLAY_RUN_ID = "qd-dev-20260817-001"
MODEL = "qwen3-32b-fp8"
BASE_URL = "http://10.87.5.247:8000/v1/"

for source in (PROJECT / "src", LEGACY / "src"):
    if str(source) not in sys.path:
        sys.path.insert(0, str(source))

from paper_eval.artifacts import atomic_write_json, payload_sha256
from paper_eval.baseline_suite import DEVELOPMENT_HISTORIES
from paper_eval.baseline_suite_u0_reuse import verify_reusable_u0_run
from paper_eval.development_graph_quality_input import (
    load_development_graph_quality_records,
)
from paper_eval.graph_quality_judge import build_graph_quality_qwen_judge
from paper_eval.graph_quality_transport import GraphQualityTransport
from paper_eval.native_baseline_runner import build_native_baseline_plan
from paper_eval.native_reader_v2 import OfficialConSessionReader
from paper_eval.qa_decomposition import (
    VARIANTS,
    select_variant_sessions,
    summarize_results,
)
from paper_eval.qa_decomposition_live import (
    QADecompositionUnit,
    execute_qa_decomposition_unit,
)
from paper_eval.s2_formal_retrieval import run_formal_session_retrieval
from paper_eval.s2_retrieval_probe import (
    ProbeCounters,
    build_episode_bm25_search_config,
    corpus_identity_sha256,
)


def _json_object(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        raise ValueError(f"QA decomposition artifact unreadable: {path.name}") from None
    if not isinstance(value, dict):
        raise ValueError(f"QA decomposition artifact invalid: {path.name}")
    return value


async def _close(value: object, method_name: str) -> None:
    method = getattr(value, method_name, None)
    if not callable(method):
        return
    result = method()
    if result is not None and hasattr(result, "__await__"):
        await result


async def _run(
    *,
    source_run_id: str,
    overlay_run_id: str,
    records: Mapping[str, Mapping[str, Any]],
    runtime: Any,
    env: Mapping[str, str],
) -> dict[str, Any]:
    from dataset import build_episodes

    source = verify_reusable_u0_run(NATIVE_RUNS_ROOT, source_run_id)
    plans = {
        value.history_id: value
        for value in build_native_baseline_plan(source_run_id).histories
    }
    api_key = env.get("CONSTRUCTION_LLM_API_KEY") or "not-required"
    transport = GraphQualityTransport(
        model=MODEL,
        base_url=BASE_URL,
        api_key=api_key,
        timeout_seconds=180.0,
    )
    reader = OfficialConSessionReader(model=MODEL, transport=transport)
    judge = build_graph_quality_qwen_judge(base_url=BASE_URL, api_key=api_key)
    rows: list[dict[str, Any]] = []
    try:
        for history_id in DEVELOPMENT_HISTORIES:
            record = records.get(history_id)
            plan = plans.get(history_id)
            if not isinstance(record, Mapping) or plan is None:
                raise ValueError("QA decomposition development inventory drift")
            source_result = _json_object(
                NATIVE_RUNS_ROOT / source_run_id / history_id / "history_result.json"
            )
            construction_hash = source_result.get("payload_sha256")
            if not isinstance(construction_hash, str) or len(construction_hash) != 64:
                raise ValueError("QA decomposition source result identity is invalid")
            episodes = tuple(build_episodes(dict(record)))
            counters = ProbeCounters()
            retrieval = await run_formal_session_retrieval(
                graph=runtime.graphiti,
                query=str(record["question"]),
                namespace=plan.namespace,
                episodes=episodes,
                expected_frozen_session_ids=tuple(
                    str(getattr(value, "session_id", "")) for value in episodes
                ),
                expected_corpus_identity_sha256=corpus_identity_sha256(episodes),
                search_config=build_episode_bm25_search_config(),
                counters=counters,
            )
            print(
                f"RETRIEVAL history={history_id} top10=10 "
                f"neo4j_reads={retrieval.neo4j_read_requests}",
                flush=True,
            )
            for variant in VARIANTS:
                selection = select_variant_sessions(
                    record=record,
                    variant=variant,
                    retrieved_session_ids=retrieval.retrieved_session_ids,
                    top_k=10,
                )
                unit = QADecompositionUnit(
                    overlay_run_id=overlay_run_id,
                    source_run_id=source_run_id,
                    history_id=history_id,
                    namespace=plan.namespace,
                    construction_result_sha256=construction_hash,
                    record=record,
                    selection=selection,
                )
                public = await execute_qa_decomposition_unit(
                    unit=unit,
                    reader=reader,
                    judge=judge,
                    unit_root=OUTPUT_ROOT / overlay_run_id / history_id / variant,
                )
                rows.append(public)
                print(
                    f"CHECKPOINT history={history_id} variant={variant} "
                    f"qa={public['qa_accuracy']:.0f} "
                    f"prompt_tokens={public['reader_prompt_tokens']}",
                    flush=True,
                )
        summary = summarize_results(rows)
        report: dict[str, Any] = {
            "schema_version": "membind.paper-eval-v3.qa-decomposition-report.v1",
            "status": "PASS",
            "overlay_run_id": overlay_run_id,
            "source_run_id": source_run_id,
            "source_u0_reuse_payload_sha256": source["payload_sha256"],
            "history_order": list(DEVELOPMENT_HISTORIES),
            "variant_order": list(VARIANTS),
            "claim_scope": "DEVELOPMENT_DIAGNOSTIC_NOT_PAPER_SIGNIFICANCE",
            "construction_mutations": 0,
            "heldout_data_accessed": False,
            "summary": summary,
            "units": rows,
        }
        report["payload_sha256"] = payload_sha256(report)
        atomic_write_json(
            OUTPUT_ROOT / overlay_run_id / "QA_DECOMPOSITION_RESULTS.json",
            report,
        )
        return report
    finally:
        errors: list[BaseException] = []
        for component, method_name in (
            (judge, "aclose"),
            (transport, "aclose"),
            (runtime.graphiti, "close"),
        ):
            try:
                await _close(component, method_name)
            except BaseException as error:
                errors.append(error)
        if errors:
            raise BaseExceptionGroup("QA decomposition cleanup failed", errors)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source-run-id", default=DEFAULT_SOURCE_RUN_ID)
    parser.add_argument("--overlay-run-id", default=DEFAULT_OVERLAY_RUN_ID)
    args = parser.parse_args(argv)
    from graphiti_native import load_env_file
    from paper_eval.s2_r0_live import build_read_only_graphiti

    run_root = OUTPUT_ROOT / args.overlay_run_id
    run_root.mkdir(parents=True, exist_ok=True)
    lock_path = run_root / "run.lock"
    lock = lock_path.open("a+", encoding="utf-8")
    try:
        fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        print("STOP error_class=QADecompositionRunAlreadyActive", flush=True)
        return 2
    try:
        env = load_env_file(LEGACY / ".env")
        records = load_development_graph_quality_records()
        runtime = build_read_only_graphiti(env=env)
        report = asyncio.run(
            _run(
                source_run_id=args.source_run_id,
                overlay_run_id=args.overlay_run_id,
                records=records,
                runtime=runtime,
                env=env,
            )
        )
    except BaseException as error:
        print(
            "STOP error_class="
            f"{type(error).__module__}.{type(error).__qualname__}",
            flush=True,
        )
        return 1
    finally:
        fcntl.flock(lock.fileno(), fcntl.LOCK_UN)
        lock.close()
    print(
        "PASS "
        f"top10_qa={report['summary']['top10']['qa_accuracy_macro']:.3f} "
        f"gold_only_qa={report['summary']['gold_only']['qa_accuracy_macro']:.3f} "
        f"oracle_gain={report['summary']['oracle_gain']:.3f}",
        flush=True,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
