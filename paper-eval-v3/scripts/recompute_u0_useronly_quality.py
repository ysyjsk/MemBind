#!/usr/bin/env python3
"""Recompute U0 quality with the paper-aligned user-only Reader overlay.

Construction artifacts and graph namespaces are read-only. Per-history output
is atomic and hash-sealed, so a rerun skips completed quality items.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any


PROJECT = Path(__file__).resolve().parents[1]
LEGACY = PROJECT.parent / "membind-validation"
DATASET = Path(
    "/data/predator/ly/Mem/data/raw/longmemeval-cleaned/"
    "longmemeval_s_cleaned.json"
)
NATIVE_RUNS_ROOT = PROJECT / "artifacts/paper_eval/native_baseline/runs"
OUTPUT_ROOT = (
    PROJECT
    / "artifacts/paper_eval/native_baseline/quality_overlays"
    / "reader-v3-useronly"
)
NATIVE_FREEZE = (
    PROJECT / "artifacts/paper_eval/native/NATIVE_BASELINE_V2_FREEZE.json"
)

for source in (PROJECT / "src", LEGACY / "src"):
    if str(source) not in sys.path:
        sys.path.insert(0, str(source))

from paper_eval.artifacts import atomic_write_json, payload_sha256
from paper_eval.baseline_suite import DEVELOPMENT_HISTORIES
from paper_eval.baseline_suite_quality import (
    build_baseline_quality_adapters,
    run_baseline_quality_chain,
)
from paper_eval.baseline_suite_u0_reuse import verify_reusable_u0_run
from paper_eval.native_baseline_runner import build_native_baseline_plan


def _json_object(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        raise ValueError(f"artifact unreadable: {path.name}") from None
    if not isinstance(value, dict):
        raise ValueError(f"artifact is not an object: {path.name}")
    return value


def _verified_item(value: Mapping[str, Any], history_id: str) -> dict[str, Any]:
    candidate = dict(value)
    observed = candidate.pop("payload_sha256", None)
    if observed != payload_sha256(candidate):
        raise ValueError("quality overlay item hash mismatch")
    if (
        candidate.get("history_id") != history_id
        or candidate.get("status") != "PASS"
    ):
        raise ValueError("quality overlay item identity mismatch")
    identity = candidate.get("quality_identity")
    if (
        not isinstance(identity, Mapping)
        or identity.get("baseline_id")
        != "native-graphiti-u0-reader-v3-useronly"
        or identity.get("useronly") is not True
    ):
        raise ValueError("quality overlay Reader identity mismatch")
    result = dict(candidate)
    result["payload_sha256"] = observed
    return result


def summarize_overlay_items(
    items: Sequence[Mapping[str, Any]],
    *,
    legacy_prompt_tokens: Sequence[int],
) -> dict[str, Any]:
    """Build the fixed-four development diagnostic without relabeling."""

    rows = [dict(item) for item in items]
    if [row.get("history_id") for row in rows] != list(DEVELOPMENT_HISTORIES):
        raise ValueError("quality overlay history inventory drift")
    if len(legacy_prompt_tokens) != len(rows):
        raise ValueError("legacy prompt token inventory drift")
    identities = [row.get("quality_identity") for row in rows]
    if any(not isinstance(identity, Mapping) for identity in identities):
        raise ValueError("quality overlay identity missing")
    reader_hashes = {identity.get("reader_config_sha256") for identity in identities}
    judge_hashes = {identity.get("judge_config_sha256") for identity in identities}
    if (
        len(reader_hashes) != 1
        or len(judge_hashes) != 1
        or any(identity.get("useronly") is not True for identity in identities)
    ):
        raise ValueError("quality overlay common identity drift")
    qualities = [row.get("quality") for row in rows]
    if any(not isinstance(quality, Mapping) for quality in qualities):
        raise ValueError("quality overlay result missing")
    if any(
        not isinstance(quality.get("judge"), Mapping)
        or quality["judge"].get("status") != "SUCCESS"
        for quality in qualities
    ):
        raise ValueError("quality overlay Judge is not successful")
    qa = [float(quality["qa_accuracy"]) for quality in qualities]
    recall = [
        float(quality["retrieval"]["evidence_recall_at_10"])
        for quality in qualities
    ]
    prompt_tokens = [
        int(quality["reader"]["prompt_tokens"])
        for quality in qualities
    ]
    legacy = [int(value) for value in legacy_prompt_tokens]
    if any(value < 0 for value in prompt_tokens + legacy):
        raise ValueError("quality overlay token count is invalid")
    current_total = sum(prompt_tokens)
    legacy_total = sum(legacy)
    return {
        "history_count": len(rows),
        "qa_accuracy_macro": sum(qa) / len(qa),
        "evidence_recall_at_10_macro": sum(recall) / len(recall),
        "reader_prompt_tokens_total": current_total,
        "legacy_reader_prompt_tokens_total": legacy_total,
        "prompt_token_reduction_fraction": (
            1.0 - current_total / legacy_total if legacy_total else None
        ),
        "reader_config_sha256": next(iter(reader_hashes)),
        "judge_config_sha256": next(iter(judge_hashes)),
        "judge_sensitivity_status": (
            "QWEN_RUBRIC_HEADLINE_OFFICIAL_GPT4O_NOT_RUN"
        ),
        "selection_timing": "POST_DEVELOPMENT_DIAGNOSTIC_PRE_PILOT",
        "heldout_data_accessed": False,
    }


async def _run_live(
    *,
    run_id: str,
    output_root: Path,
    retrieval_runtime: Any,
) -> dict[str, Any]:
    from dataset import build_episodes, load_json_records
    from graphiti_native import load_env_file

    source = verify_reusable_u0_run(NATIVE_RUNS_ROOT, run_id)
    records = {
        str(record["question_id"]): record
        for record in load_json_records(DATASET)
    }
    plans = {
        history.history_id: history
        for history in build_native_baseline_plan(run_id).histories
    }
    env = load_env_file(LEGACY / ".env")
    adapters = build_baseline_quality_adapters(
        env=env,
        frozen_baseline=_json_object(NATIVE_FREEZE),
    )
    target = Path(output_root) / run_id
    target.mkdir(parents=True, exist_ok=True)
    items: list[dict[str, Any]] = []
    legacy_tokens: list[int] = []
    try:
        for history_id in DEVELOPMENT_HISTORIES:
            source_result = _json_object(
                NATIVE_RUNS_ROOT / run_id / history_id / "history_result.json"
            )
            legacy_tokens.append(
                int(source_result["quality"]["reader"]["prompt_tokens"])
            )
            item_path = target / f"{history_id}.json"
            if item_path.exists():
                item = _verified_item(_json_object(item_path), history_id)
                print(f"SKIP quality {history_id} verified", flush=True)
                items.append(item)
                continue
            record = records[history_id]
            episodes = build_episodes(record)
            quality = await run_baseline_quality_chain(
                graph=retrieval_runtime.graphiti,
                record=record,
                episodes=episodes,
                history_id=history_id,
                namespace=plans[history_id].namespace,
                run_id=f"{run_id}-reader-v3-useronly",
                reader=adapters["reader"],
                judge=adapters["judge"],
            )
            item = {
                "schema_version": (
                    "membind.paper-eval-v3.u0-quality-useronly.v1"
                ),
                "status": "PASS",
                "source_run_id": run_id,
                "history_id": history_id,
                "source_history_result_payload_sha256": source_result[
                    "payload_sha256"
                ],
                "quality_identity": dict(adapters["quality_identity"]),
                "quality": quality,
            }
            item["payload_sha256"] = payload_sha256(item)
            atomic_write_json(item_path, item)
            items.append(_verified_item(item, history_id))
            print(
                f"COMPLETE quality {history_id} "
                f"qa={quality['qa_accuracy']} "
                f"r10={quality['retrieval']['evidence_recall_at_10']}",
                flush=True,
            )
        summary = summarize_overlay_items(
            items,
            legacy_prompt_tokens=legacy_tokens,
        )
        report: dict[str, Any] = {
            "schema_version": (
                "membind.paper-eval-v3.u0-quality-useronly-summary.v1"
            ),
            "status": "PASS",
            "source_run_id": run_id,
            "source_u0_reuse_payload_sha256": source["payload_sha256"],
            "history_order": list(DEVELOPMENT_HISTORIES),
            "selection_basis": (
                "LongMemEval ICLR 2025 section 5.1 states that session/round "
                "values retain user-side utterances in the reported reading "
                "experiments."
            ),
            "claim_scope": "DEVELOPMENT_DIAGNOSTIC_NOT_PAPER_SIGNIFICANCE",
            "summary": summary,
            "items": [
                {
                    "history_id": item["history_id"],
                    "payload_sha256": item["payload_sha256"],
                    "qa_accuracy": item["quality"]["qa_accuracy"],
                    "evidence_recall_at_10": item["quality"]["retrieval"][
                        "evidence_recall_at_10"
                    ],
                    "reader_prompt_tokens": item["quality"]["reader"][
                        "prompt_tokens"
                    ],
                    "judge_label": item["quality"]["judge"]["label"],
                }
                for item in items
            ],
        }
        report["payload_sha256"] = payload_sha256(report)
        atomic_write_json(target / "QUALITY_OVERLAY_SUMMARY.json", report)
        return report
    finally:
        for component, method in (
            (adapters.get("judge"), "aclose"),
            (adapters.get("transport"), "aclose"),
            (retrieval_runtime.graphiti, "close"),
        ):
            close = getattr(component, method, None)
            if callable(close):
                try:
                    result = close()
                    if result is not None and hasattr(result, "__await__"):
                        await result
                except Exception:
                    pass


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("run_id")
    args = parser.parse_args(argv)
    from graphiti_native import load_env_file
    from paper_eval.s2_r0_live import build_read_only_graphiti

    runtime = build_read_only_graphiti(
        env=load_env_file(LEGACY / ".env")
    )
    try:
        report = asyncio.run(
            _run_live(
                run_id=args.run_id,
                output_root=OUTPUT_ROOT,
                retrieval_runtime=runtime,
            )
        )
    except BaseException as error:
        print(
            "STOP error_class="
            f"{type(error).__module__}.{type(error).__qualname__}",
            flush=True,
        )
        return 1
    print(
        f"PASS qa={report['summary']['qa_accuracy_macro']:.3f} "
        f"r10={report['summary']['evidence_recall_at_10_macro']:.3f}",
        flush=True,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

