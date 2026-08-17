#!/usr/bin/env python3
"""Reuse completed U0, then run A0 and P(C=2) development histories.

This is intentionally a small sequential entrypoint. Each live history owns
an independent durable attempt, so rerunning the same command skips verified
work and allocates a fresh attempt only for an interrupted history.
"""

from __future__ import annotations

import argparse
import fcntl
import json
import sys
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any


PROJECT = Path(__file__).resolve().parents[1]
if str(PROJECT / "src") not in sys.path:
    sys.path.insert(0, str(PROJECT / "src"))

from paper_eval.artifacts import atomic_write_json, payload_sha256
from paper_eval.baseline_suite import (
    DEVELOPMENT_HISTORIES,
    baseline_block_namespace,
    build_baseline_suite_plan,
)
from paper_eval.baseline_suite_artifacts import inspect_baseline_block
from paper_eval.baseline_suite_block_live import execute_baseline_block_sync
from paper_eval.baseline_suite_u0_reuse import verify_reusable_u0_run


RUNS_ROOT = PROJECT / "artifacts/paper_eval/baseline_suite/runs"
NATIVE_RUNS_ROOT = PROJECT / "artifacts/paper_eval/native_baseline/runs"
METHODS = ("A0", "P(C=2)")
SLUGS = {"A0": "a0", "P(C=2)": "pc2"}


class Hooks:
    """Injectable filesystem/live boundaries used by offline tests."""

    def __init__(
        self,
        *,
        verify_u0: Callable[[Path, str], dict[str, Any]],
        inspect_block: Callable[[Path, Mapping[str, object]], dict[str, object]],
        execute_block: Callable[..., dict[str, Any]],
    ) -> None:
        self.verify_u0 = verify_u0
        self.inspect_block = inspect_block
        self.execute_block = execute_block


DEFAULT_HOOKS = Hooks(
    verify_u0=verify_reusable_u0_run,
    inspect_block=inspect_baseline_block,
    execute_block=execute_baseline_block_sync,
)


def _block_root(run_root: Path, method: str, history_id: str, attempt: int) -> Path:
    return (
        run_root
        / "blocks"
        / SLUGS[method]
        / history_id
        / f"attempt-{attempt:03d}"
    )


def _attempt_block(
    base: Mapping[str, Any],
    *,
    attempt: int,
) -> dict[str, Any]:
    block = dict(base)
    block["attempt_ordinal"] = attempt
    block["namespace"] = baseline_block_namespace(
        suite_run_id=str(block["suite_run_id"]),
        method=str(block["method"]),
        history_id=str(block["history_id"]),
        attempt_ordinal=attempt,
    )
    return block


def _select_attempt(
    *,
    run_root: Path,
    base: Mapping[str, Any],
    hooks: Hooks,
) -> tuple[str, dict[str, Any], Path, dict[str, object] | None]:
    method = str(base["method"])
    history_id = str(base["history_id"])
    next_attempt = 1
    for attempt in range(1, 1000):
        root = _block_root(run_root, method, history_id, attempt)
        if not root.exists():
            next_attempt = attempt
            break
        block = _attempt_block(base, attempt=attempt)
        observed = hooks.inspect_block(root, block)
        if (
            observed.get("status") == "completed"
            and observed.get("artifacts_verified") is True
        ):
            return "SKIP", block, root, observed
        next_attempt = attempt + 1
    if next_attempt > 999:
        raise RuntimeError("baseline history exhausted attempt ordinals")
    block = _attempt_block(base, attempt=next_attempt)
    root = _block_root(run_root, method, history_id, next_attempt)
    return "RUN", block, root, None


def _project_block(
    observed: Mapping[str, object],
    *,
    disposition: str,
) -> dict[str, Any]:
    block = observed.get("block")
    result = observed.get("result")
    if not isinstance(block, Mapping) or not isinstance(result, Mapping):
        raise RuntimeError("completed baseline block has no verified result")
    payload = result.get("payload")
    if not isinstance(payload, Mapping):
        raise RuntimeError("completed baseline block payload is missing")
    for field in ("method", "history_id"):
        if payload.get(field) != block.get(field):
            raise RuntimeError("completed baseline block identity drift")
    quality_identity = payload.get("quality_identity")
    if (
        payload.get("quality_status") != "MEASURED"
        or not isinstance(quality_identity, Mapping)
    ):
        raise RuntimeError("completed baseline block quality identity missing")
    return {
        "method": str(block["method"]),
        "history_id": str(block["history_id"]),
        "attempt_ordinal": int(block["attempt_ordinal"]),
        "disposition": disposition,
        "episode_count": int(payload["episode_count"]),
        "metrics": dict(payload.get("metrics", {})),
        "work_volume": dict(payload.get("work_volume", {})),
        "quality_status": payload.get("quality_status"),
        "quality_identity": dict(quality_identity),
        "result_payload_sha256": result.get("result_payload_sha256"),
    }


def _bind_u0_once(path: Path, u0: Mapping[str, Any]) -> None:
    """Create one immutable suite-to-U0 binding or verify its exact replay."""

    if not path.exists():
        atomic_write_json(path, dict(u0))
        return
    try:
        stored = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        raise RuntimeError("stored U0 source identity is unreadable") from None
    if not isinstance(stored, dict) or stored != dict(u0):
        raise RuntimeError("U0 source identity drift for existing suite run")


def _require_common_quality_identity(
    row: Mapping[str, Any],
    expected: Mapping[str, Any],
) -> None:
    if row.get("quality_identity") != dict(expected):
        raise RuntimeError("baseline quality identity drift")


def _u0_quality_metrics(u0: Mapping[str, Any]) -> dict[str, Any]:
    histories = u0.get("histories")
    if not isinstance(histories, list) or not histories:
        raise RuntimeError("U0 quality history inventory missing")
    rows: list[dict[str, Any]] = []
    for history in histories:
        if not isinstance(history, Mapping):
            raise RuntimeError("U0 quality history invalid")
        metrics = history.get("quality_metrics")
        if not isinstance(metrics, Mapping):
            raise RuntimeError("U0 quality metrics missing")
        rows.append(
            {
                "history_id": history.get("history_id"),
                "qa_accuracy": float(metrics["qa_accuracy"]),
                "evidence_recall_at_10": float(
                    metrics["evidence_recall_at_10"]
                ),
            }
        )
    return {
        "qa_accuracy_macro": sum(row["qa_accuracy"] for row in rows) / len(rows),
        "evidence_recall_at_10_macro": sum(
            row["evidence_recall_at_10"] for row in rows
        )
        / len(rows),
        "histories": rows,
    }


def _progress(
    *,
    run_id: str,
    status: str,
    u0: Mapping[str, Any],
    blocks: list[Mapping[str, Any]],
    error_class: str | None = None,
) -> dict[str, Any]:
    return {
        "schema_version": "membind.paper-eval-v3.three-baseline-progress.v1",
        "run_id": run_id,
        "status": status,
        "execution_order": ["U0_REUSED", "A0", "P(C=2)"],
        "u0_source_run_id": u0["source_run_id"],
        "u0_payload_sha256": u0["payload_sha256"],
        "completed_block_count": len(blocks),
        "expected_live_block_count": 8,
        "blocks": [dict(row) for row in blocks],
        "error_class": error_class,
    }


def run_remaining_baselines(
    *,
    run_id: str,
    reuse_u0_run: str,
    runs_root: Path = RUNS_ROOT,
    native_runs_root: Path = NATIVE_RUNS_ROOT,
    hooks: Hooks = DEFAULT_HOOKS,
) -> dict[str, Any]:
    """Verify U0 and execute the eight remaining blocks in strict order."""

    plan = build_baseline_suite_plan(
        run_id,
        mode="development",
        reuse_u0_run=reuse_u0_run,
    )
    run_root = Path(runs_root) / run_id
    run_root.mkdir(parents=True, exist_ok=True)
    lock_path = run_root / "run.lock"
    with lock_path.open("a+b") as lock:
        try:
            fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise RuntimeError("three-baseline command is already running") from None

        u0 = hooks.verify_u0(Path(native_runs_root), reuse_u0_run)
        _bind_u0_once(run_root / "u0_reuse.json", u0)
        quality_identity = u0.get("quality_identity")
        if not isinstance(quality_identity, Mapping):
            raise RuntimeError("U0 quality identity missing")
        completed: list[dict[str, Any]] = []
        bases = [
            block
            for block in plan["blocks"]
            if block["method"] in METHODS
        ]
        for base in bases:
            action, block, block_root, observed = _select_attempt(
                run_root=run_root,
                base=base,
                hooks=hooks,
            )
            method = str(block["method"])
            history_id = str(block["history_id"])
            if action == "SKIP":
                print(f"SKIP {method} {history_id} verified", flush=True)
                assert observed is not None
                completed.append(
                    _project_block(observed, disposition="SKIPPED_VERIFIED")
                )
                _require_common_quality_identity(
                    completed[-1], quality_identity
                )
                continue
            print(
                f"START {method} {history_id} "
                f"attempt={block['attempt_ordinal']}",
                flush=True,
            )
            try:
                hooks.execute_block(block=block, block_root=block_root)
                observed = hooks.inspect_block(block_root, block)
                row = _project_block(observed, disposition="EXECUTED")
                _require_common_quality_identity(row, quality_identity)
                completed.append(row)
                atomic_write_json(
                    run_root / "progress.json",
                    _progress(
                        run_id=run_id,
                        status="RUNNING",
                        u0=u0,
                        blocks=completed,
                    ),
                )
                print(f"COMPLETE {method} {history_id}", flush=True)
            except BaseException as error:
                atomic_write_json(
                    run_root / "progress.json",
                    _progress(
                        run_id=run_id,
                        status="STOPPED_ON_ERROR",
                        u0=u0,
                        blocks=completed,
                        error_class=(
                            f"{type(error).__module__}."
                            f"{type(error).__qualname__}"
                        ),
                    ),
                )
                raise

        u0_episode_count = sum(
            int(row["episode_count"]) for row in u0["histories"]
        )
        report: dict[str, Any] = {
            "schema_version": "membind.paper-eval-v3.three-baseline-report.v1",
            "run_id": run_id,
            "status": "PASS",
            "execution_order": ["U0_REUSED", "A0", "P(C=2)"],
            "fairness": {
                "same_development_histories": list(DEVELOPMENT_HISTORIES),
                "strict_method_serial_execution": True,
                "common_graphiti_model_embedding_reader_judge": True,
                "quality_identity_verified": True,
                "quality_identity": dict(quality_identity),
            },
            "u0": {
                "source_run_id": u0["source_run_id"],
                "episode_count": u0_episode_count,
                "payload_sha256": u0["payload_sha256"],
                "namespace_reuse": False,
                "quality_metrics": _u0_quality_metrics(u0),
            },
            "blocks": completed,
        }
        report["payload_sha256"] = payload_sha256(report)
        atomic_write_json(run_root / "THREE_BASELINE_RESULTS.json", report)
        atomic_write_json(
            run_root / "progress.json",
            _progress(
                run_id=run_id,
                status="COMPLETED",
                u0=u0,
                blocks=completed,
            ),
        )
        return report


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Reuse U0, then run A0 and P(C=2) sequentially."
    )
    parser.add_argument("run_id")
    parser.add_argument("--reuse-u0-run", required=True)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        report = run_remaining_baselines(
            run_id=args.run_id,
            reuse_u0_run=args.reuse_u0_run,
        )
    except BaseException as error:
        print(
            "STOP error_class="
            f"{type(error).__module__}.{type(error).__qualname__}",
            flush=True,
        )
        return 1
    print(
        f"PASS run_id={args.run_id} sha256={report['payload_sha256']}",
        flush=True,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
