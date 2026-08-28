#!/usr/bin/env python3
"""Summarize sealed local V6.1 evidence without starting any live work."""

from __future__ import annotations

import argparse
import json
import math
import os
import time
from pathlib import Path
from typing import Any, Mapping


DEFAULT_ROOT = Path(
    "/data/predator/ly/Mem/experiments/local-qwen3-14b-awq-v1/v6_1_mab"
)


def _atomic_text(path: Path, value: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.tmp-{os.getpid()}")
    temporary.write_text(value, encoding="utf-8")
    os.replace(temporary, path)


def _atomic_json(path: Path, value: Mapping[str, Any]) -> None:
    _atomic_text(
        path,
        json.dumps(dict(value), ensure_ascii=False, sort_keys=True, indent=2) + "\n",
    )


def _json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"JSON object required: {path}")
    return value


def _p95_seconds(rows: list[dict[str, Any]]) -> float | None:
    durations = sorted(
        (int(row["end_ns"]) - int(row["start_ns"])) / 1_000_000_000
        for row in rows
        if isinstance(row.get("start_ns"), int)
        and isinstance(row.get("end_ns"), int)
        and int(row["end_ns"]) >= int(row["start_ns"])
    )
    if not durations:
        return None
    return durations[max(0, math.ceil(0.95 * len(durations)) - 1)]


def _baseline_rows(prefix: Path) -> dict[str, dict[str, Any]]:
    ledger = prefix / "campaign_ledger.jsonl"
    if not ledger.is_file():
        return {}
    result: dict[str, dict[str, Any]] = {}
    for line in ledger.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        row = json.loads(line)
        method = row.get("method")
        seal = row.get("construction_seal")
        timing_invalid = bool(
            seal
            and (Path(str(seal)).parent.parent / "timing_invalidation.json").is_file()
        )
        if (
            row.get("event") != "ATTEMPT_COMPLETE"
            or row.get("status") != "PASS"
            or row.get("episode_count") != 30
            or method not in {"B0", "V6_0"}
            or timing_invalid
        ):
            continue
        block = Path(str(row["construction_seal"])).parent
        metrics = _json(block / "metrics.json")
        inventory = _json(block / "work_inventory.json")
        transport = [
            json.loads(item)
            for item in (block / "transport_trace.jsonl").read_text(encoding="utf-8").splitlines()
            if item.strip()
        ]
        entry = {
            "method": method,
            "status": "PASS",
            "attempt_id": row.get("attempt_id"),
            "block_root": str(block),
            "makespan_s": int(metrics["t_build_ns"]) / 1_000_000_000,
            "transport_p95_s": _p95_seconds(transport),
            "transport_attempts": inventory.get("transport_attempts"),
            "embedding_items": inventory.get("embedding_items"),
            "db_writes": inventory.get("db_writes"),
            "evidence_limit": row.get("evidence_limit"),
        }
        if method == "V6_0":
            refinement = _json(block / "refinement_validation.json")
            entry["replay"] = refinement.get("proof", {}).get("replay")
            entry["request"] = refinement.get("proof", {}).get("request")
            entry["provider_proof"] = refinement.get("proof", {}).get("provider")
        result[str(method)] = entry
    return result


def _selected(autoresearch: Path) -> dict[str, Any] | None:
    path = autoresearch / "selected_policy.json"
    if not path.is_file():
        return None
    selected = _json(path)
    measured = dict(selected.get("selection_result") or {})
    return {
        "method": "V6_1",
        "status": selected.get("status"),
        "policy": selected.get("policy"),
        **measured,
    }


def _full5(root: Path) -> dict[str, Any]:
    queue_path = root / "full5/queue_manifest.json"
    state_path = root / "full5/state/supervisor_state.json"
    queue = _json(queue_path) if queue_path.is_file() else None
    state = _json(state_path) if state_path.is_file() else None
    blocks = list(queue.get("blocks", [])) if isinstance(queue, Mapping) else []
    return {
        "queue_status": queue.get("status") if isinstance(queue, Mapping) else "NOT_STARTED",
        "block_count": len(blocks),
        "status_counts": {
            status: sum(row.get("status") == status for row in blocks)
            for status in sorted({str(row.get("status")) for row in blocks})
        },
        "contexts_queued": sorted(
            {int(row["context_index"]) for row in blocks if isinstance(row.get("context_index"), int)}
        ),
        "methods_queued": sorted({str(row.get("method")) for row in blocks}),
        "supervisor_state": state,
    }


def _fmt(value: Any, digits: int = 2) -> str:
    return f"{float(value):.{digits}f}" if isinstance(value, (int, float)) else "pending"


def _markdown(summary: Mapping[str, Any]) -> str:
    prefix = summary["prefix30"]
    rows = []
    b0 = prefix.get("B0")
    b0_time = b0.get("makespan_s") if isinstance(b0, Mapping) else None
    for method in ("B0", "V6_0", "V6_1"):
        row = prefix.get(method)
        if not isinstance(row, Mapping):
            rows.append(f"| {method} | pending | pending | pending | pending | pending |")
            continue
        makespan = row.get("makespan_s")
        speedup = b0_time / makespan if isinstance(b0_time, (int, float)) and isinstance(makespan, (int, float)) else None
        p95 = row.get("native_real_provider_p95_s", row.get("transport_p95_s"))
        rows.append(
            "| "
            + " | ".join(
                (
                    method,
                    _fmt(makespan),
                    _fmt(speedup, 3),
                    _fmt(p95),
                    str(row.get("transport_attempts", row.get("real_provider_calls", "pending"))),
                    str(row.get("status", "pending")),
                )
            )
            + " |"
        )
    full = summary["full5"]
    return "\n".join(
        (
            "# MemBind V6.1 Local Campaign Summary",
            "",
            f"Generated at Unix `{summary['generated_at_unix']}` for profile `local-qwen3-14b-awq-v1`.",
            "",
            "## Prefix 30",
            "",
            "| Method | Makespan (s) | Speedup vs B0 | Provider P95 (s) | Provider/transport calls | Evidence status |",
            "|---|---:|---:|---:|---:|---|",
            *rows,
            "",
            "## Full Five Histories",
            "",
            f"Queue status: `{full['queue_status']}`; blocks: `{full['block_count']}`; "
            f"contexts queued: `{full['contexts_queued']}`; methods: `{full['methods_queued']}`.",
            "",
            "Pending cells remain pending until their sealed block exists.",
            "",
        )
    )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--experiment-root", type=Path, default=DEFAULT_ROOT)
    args = parser.parse_args()
    root = args.experiment_root.resolve()
    prefix = _baseline_rows(root / "prefix30")
    selected = _selected(root / "autoresearch") or _selected(root / "autoresearch_retry1")
    if selected is not None:
        prefix["V6_1"] = selected
    if "B0" in prefix:
        b0_time = prefix["B0"]["makespan_s"]
        for row in prefix.values():
            makespan = row.get("makespan_s")
            row["speedup_vs_b0"] = b0_time / makespan if isinstance(makespan, (int, float)) else None
    summary = {
        "schema_version": "membind.v6.1.local-campaign-summary.v1",
        "profile_id": "local-qwen3-14b-awq-v1",
        "generated_at_unix": time.time(),
        "prefix30": prefix,
        "full5": _full5(root),
    }
    _atomic_json(root / "summary/campaign_summary.json", summary)
    _atomic_text(root / "summary/campaign_summary.md", _markdown(summary))
    print(json.dumps(summary, ensure_ascii=False, sort_keys=True, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
