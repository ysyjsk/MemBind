#!/usr/bin/env python3
"""Run the zero-provider MAB8192 budget and identity gate."""

from __future__ import annotations

import argparse
import hashlib
import json
import time
from pathlib import Path
from typing import Any

from mab_quality_v2_final_qa.mab8192_adapter import MAB8192Manifest, adapter_identity
from mab_quality_v2_final_qa.mab_main_dataset import DATASET_REVISION, load_main_contexts


MODEL_LIMIT_NO_YARN = 40960
MODEL_LIMIT_YARN = 65536
SAFETY_MARGIN = 256
EDGE_MAX_TOKENS = 16384


def _jsonl_rows(root: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    if not root.exists():
        return rows
    for path in root.rglob("*.jsonl"):
        try:
            lines = path.read_text(encoding="utf-8", errors="ignore").splitlines()
        except OSError:
            continue
        for line in lines:
            try:
                value = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(value, dict):
                rows.append(value)
    return rows


def _tokenizer(path: Path):
    try:
        from tokenizers import Tokenizer
    except ImportError as exc:  # pragma: no cover - exercised only outside pinned env
        raise RuntimeError("the pinned tokenizers package is required for L0") from exc
    return Tokenizer.from_file(str(path))


def run(*, dataset_path: Path, tokenizer_path: Path, history_root: Path, output_root: Path) -> dict[str, Any]:
    contexts = load_main_contexts(dataset_path)
    tokenizer = _tokenizer(tokenizer_path)
    output_root.mkdir(parents=True, exist_ok=False)
    manifest_rows: list[dict[str, Any]] = []
    body_witness: dict[str, Any] | None = None
    total_chunks = 0
    for context in contexts:
        manifest = MAB8192Manifest.from_context(context, dataset_revision=DATASET_REVISION)
        path = output_root / f"manifest.{context.context_id.rsplit(':', 1)[-1]}.json"
        path.write_text(json.dumps(manifest.to_dict(), ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        total_chunks += len(manifest.chunks)
        for chunk in manifest.chunks:
            token_count = len(tokenizer.encode(chunk.body).ids)
            row = {
                "context_id": chunk.context_id,
                "session_id": chunk.session_id,
                "source_sequence": chunk.source_sequence,
                "chunk_ordinal": chunk.chunk_ordinal,
                "chunk_count": chunk.chunk_count,
                "global_sequence": chunk.global_sequence,
                "characters": len(chunk.body),
                "tokens": token_count,
                "chunk_sha256": chunk.chunk_sha256,
                "previous_chunk_id": chunk.previous_chunk_id,
            }
            manifest_rows.append(row)
            if body_witness is None or token_count > body_witness["tokens"]:
                body_witness = row
    rows = _jsonl_rows(history_root)
    # Only extraction diagnostics with an explicit prompt name are prompt
    # witnesses.  Queue/route heartbeat rows also carry ``prompt_tokens`` in
    # some historical campaigns but are not model request budgets.
    prompt_rows = [
        row
        for row in rows
        if isinstance(row.get("prompt_name"), str)
        and isinstance(row.get("observed_prompt_tokens", row.get("prompt_tokens")), int)
    ]
    max_prompt = max(
        (int(row.get("observed_prompt_tokens", row.get("prompt_tokens", 0))) for row in prompt_rows),
        default=0,
    )
    output_budgets = [
        int(row.get("requested_max_tokens"))
        for row in prompt_rows
        if isinstance(row.get("requested_max_tokens"), int)
    ]
    max_output = max(output_budgets, default=EDGE_MAX_TOKENS)
    budget_witness = {
        "historical_trace_root": str(history_root),
        "historical_trace_rows": len(prompt_rows),
        "max_prompt_tokens": max_prompt,
        "max_output_tokens": max_output,
        "safety_margin_tokens": SAFETY_MARGIN,
        "sum_tokens": max_prompt + max_output + SAFETY_MARGIN,
    }
    selected = "NO_YARN_40960" if budget_witness["sum_tokens"] <= MODEL_LIMIT_NO_YARN else "YARN_2X_65536"
    decision = {
        "schema_version": "membind.l0-mab8192-budget.v1",
        "status": "PASS",
        "provider_calls": 0,
        "dataset_revision": DATASET_REVISION,
        "context_count": len(contexts),
        "session_count": sum(len(context.sessions) for context in contexts),
        "chunk_count": total_chunks,
        "adapter": adapter_identity(),
        "manifest_rows": len(manifest_rows),
        "body_token_witness": body_witness,
        "historical_prompt_output_witness": budget_witness,
        "deployment_selection": {
            "profile": selected,
            "max_model_len": MODEL_LIMIT_NO_YARN if selected == "NO_YARN_40960" else MODEL_LIMIT_YARN,
            "yarn": None
            if selected == "NO_YARN_40960"
            else {"factor": 2.0, "original_max_position_embeddings": 32768},
            "reason": "deterministic static budget before live calls",
        },
        "manifest_files": sorted(path.name for path in output_root.glob("manifest.*.json")),
    }
    decision["decision_sha256"] = hashlib.sha256(
        json.dumps(decision, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    (output_root / "chunk_inventory.json").write_text(json.dumps(manifest_rows, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    (output_root / "L0_BUDGET_DECISION.json").write_text(json.dumps(decision, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    (output_root / "L0_BUDGET_DECISION.md").write_text(
        "# L0 MAB8192 Budget Decision\n\n"
        f"Status: `PASS`; provider calls: `0`; contexts: `{decision['context_count']}`; sessions: `{decision['session_count']}`; chunks: `{decision['chunk_count']}`.\n\n"
        f"The maximum historical prompt witness is `{max_prompt}` tokens and the maximum upstream output budget witness is `{max_output}` tokens. With a fixed `{SAFETY_MARGIN}`-token safety margin, the static sum is `{budget_witness['sum_tokens']}`. The selected deployment is `{selected}`.\n\n"
        "This decision is made before any live result and is bound to the immutable MAB8192 manifest.\n",
        encoding="utf-8",
    )
    return decision


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dataset", type=Path, default=Path("mab_quality_v2_final_qa/data/official_5_contexts.json"))
    parser.add_argument("--tokenizer", type=Path, default=Path("/data/predator/ly/Mem/models/Qwen3-8B-AWQ/tokenizer.json"))
    parser.add_argument("--history-root", type=Path, default=Path("/data/predator/ly/Mem/experiments/local-qwen3-8b-awq-dualreplica-v1"))
    parser.add_argument("--output-root", type=Path)
    args = parser.parse_args()
    output = args.output_root or Path("/data/predator/ly/Mem/experiments/local-qwen3-8b-awq-dualreplica-v1") / f"l0-mab8192-{time.strftime('%Y%m%dT%H%M%SZ', time.gmtime())}"
    result = run(dataset_path=args.dataset, tokenizer_path=args.tokenizer, history_root=args.history_root, output_root=output)
    print(json.dumps({"output_root": str(output), "decision_sha256": result["decision_sha256"], "deployment": result["deployment_selection"]}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
