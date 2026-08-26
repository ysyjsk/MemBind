#!/usr/bin/env python3
"""Probe frozen Bailian candidates on the repeated failing Graphiti schema."""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
import sys
from pathlib import Path
from typing import Any, Mapping


ROOT = Path(__file__).resolve().parents[2]
PROJECT = ROOT / "saturated_fixed_work_baseline_v1_3"
for source in (
    PROJECT / "src",
    ROOT / "mab_quality_v2_final_qa/src",
    ROOT / "membind-validation/src",
):
    selected = str(source)
    if selected not in sys.path:
        sys.path.insert(0, selected)

from saturated_fixed_work_baseline_v1_3.membind_v7.development_model_probe import (  # noqa: E402
    sanitize_probe_execution,
    select_development_model,
)
from saturated_fixed_work_baseline_v1_3.membind_v7.provider_diagnostics import (  # noqa: E402
    _execute_structured_extraction_probe_async,
    build_structured_edge_extraction_probe,
    build_structured_extraction_probe,
)


PROTOCOL = PROJECT / "v7/BAILIAN_V7_DEVELOPMENT_MODEL_CANDIDATE_PROTOCOL.json"
DATASET = ROOT / "mab_quality_v2_final_qa/data/official_5_contexts.json"


def _sha256(path: str | Path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def _write_exclusive(path: Path, value: Mapping[str, Any]) -> None:
    payload = (
        json.dumps(
            dict(value),
            ensure_ascii=True,
            sort_keys=True,
            indent=2,
            allow_nan=False,
        )
        + "\n"
    ).encode("ascii")
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    try:
        view = memoryview(payload)
        while view:
            written = os.write(descriptor, view)
            if written <= 0:
                raise OSError("candidate probe artifact write made no progress")
            view = view[written:]
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _object(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="ascii"))
    if not isinstance(value, dict):
        raise ValueError("candidate protocol must be an object")
    return value


def _source_bindings() -> dict[str, str]:
    paths = {
        "mab_quality_v2_final_qa/src/mab_quality_v2_final_qa/mab_main_dataset.py": (
            ROOT
            / "mab_quality_v2_final_qa/src/mab_quality_v2_final_qa/mab_main_dataset.py"
        ),
        "membind-validation/.venv/lib/python3.12/site-packages/graphiti_core/prompts/extract_edges.py": (
            ROOT
            / "membind-validation/.venv/lib/python3.12/site-packages/graphiti_core/prompts/extract_edges.py"
        ),
        "membind-validation/.venv/lib/python3.12/site-packages/graphiti_core/prompts/extract_nodes.py": (
            ROOT
            / "membind-validation/.venv/lib/python3.12/site-packages/graphiti_core/prompts/extract_nodes.py"
        ),
        "membind-validation/src/graphiti_native.py": (
            ROOT / "membind-validation/src/graphiti_native.py"
        ),
        "membind-validation/src/structured_output.py": (
            ROOT / "membind-validation/src/structured_output.py"
        ),
        "saturated_fixed_work_baseline_v1_3/scripts/probe_v7_bailian_development_candidates.py": (
            Path(__file__).resolve()
        ),
        "saturated_fixed_work_baseline_v1_3/src/saturated_fixed_work_baseline_v1_3/membind_v7/development_model_probe.py": (
            PROJECT
            / "src/saturated_fixed_work_baseline_v1_3/membind_v7/development_model_probe.py"
        ),
        "saturated_fixed_work_baseline_v1_3/src/saturated_fixed_work_baseline_v1_3/membind_v7/provider_diagnostics.py": (
            PROJECT
            / "src/saturated_fixed_work_baseline_v1_3/membind_v7/provider_diagnostics.py"
        ),
    }
    return {name: _sha256(path) for name, path in sorted(paths.items())}


def _load_protocol() -> dict[str, Any]:
    value = _object(PROTOCOL)
    expected = {
        "schema_version": "membind.v7.development-model-candidate-protocol.v1",
        "status": "FROZEN_BEFORE_CANDIDATE_PROBES",
        "base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1",
        "api_key_env": "DASHSCOPE_API_KEY",
        "structured_output_mode": "json_object",
        "enable_thinking": False,
        "sdk_max_retries": 0,
        "hard_attempt_limit_per_probe": 1,
        "required_node_passes": 1,
        "required_edge_repetitions": 5,
        "selection_rule": "FIRST_FULL_PASS_IN_FROZEN_ORDER",
        "database_allowed": False,
        "embedding_allowed": False,
        "formal_r1_r3_eligible": False,
        "live_treatment_authorized": False,
    }
    if any(value.get(field) != selected for field, selected in expected.items()):
        raise ValueError("candidate probe protocol drifted")
    if value.get("candidates") != [
        "qwen3.5-122b-a10b",
        "qwen3.5-397b-a17b",
        "qwen3.5-plus-2026-04-20",
    ]:
        raise ValueError("candidate model order drifted")
    workload = value.get("workload")
    if workload != {
        "dataset_sha256": "97fd80207f3419fc57c3684db824334224546d6bdd62c17ef52cd116eec9ffc8",
        "context_index": 2,
        "source_sequence": 2,
        "previous_episode_count": 2,
        "failing_prompt_name": "extract_edges.edge",
        "failing_response_model": "graphiti_core.prompts.extract_edges.ExtractedEdges",
    }:
        raise ValueError("candidate probe workload drifted")
    if _sha256(DATASET) != workload["dataset_sha256"]:
        raise ValueError("candidate probe dataset hash drifted")
    source_sha256 = value.get("source_sha256")
    if source_sha256 != _source_bindings():
        raise ValueError("candidate probe source hash drifted")
    return value


def _entity_names(items: tuple[Any, ...]) -> list[str]:
    result: list[str] = []
    for item in items:
        name = getattr(item, "name", None)
        if isinstance(name, str) and name and name not in result:
            result.append(name)
    return result


async def _run(args: argparse.Namespace, protocol: Mapping[str, Any]) -> dict[str, Any]:
    import httpx
    from openai import AsyncOpenAI

    from mab_quality_v2_final_qa.mab_main_dataset import (
        build_authority,
        build_episode_inputs,
    )

    credential = os.environ.get("DASHSCOPE_API_KEY")
    if not credential:
        raise ValueError("DASHSCOPE_API_KEY is required")
    timeout = httpx.Timeout(
        connect=min(10.0, args.timeout_seconds),
        read=args.timeout_seconds,
        write=args.timeout_seconds,
        pool=args.timeout_seconds,
    )
    http_client = httpx.AsyncClient(
        timeout=timeout,
        follow_redirects=False,
        trust_env=False,
    )
    client = AsyncOpenAI(
        api_key=credential,
        base_url=str(protocol["base_url"]),
        timeout=timeout,
        max_retries=0,
        http_client=http_client,
    )
    authority = build_authority(DATASET)
    workload = protocol["workload"]
    episodes = tuple(
        build_episode_inputs(authority["contexts"][int(workload["context_index"])])
    )
    sequence = int(workload["source_sequence"])
    current = episodes[sequence]
    previous = episodes[:sequence]
    results: list[dict[str, Any]] = []
    try:
        available_response = await client.models.list()
        available = {
            str(item.id)
            for item in list(available_response.data)
            if isinstance(getattr(item, "id", None), str)
        }
        for model in protocol["candidates"]:
            row: dict[str, Any] = {
                "model": model,
                "available": model in available,
                "node": None,
                "edges": [],
                "entity_names_persisted": False,
                "raw_request_persisted": False,
                "raw_response_persisted": False,
                "response_hash_persisted": False,
            }
            if model not in available:
                results.append(row)
                continue
            namespace = f"membind-v7-{args.run_id}-{model}"
            node_probe = build_structured_extraction_probe(
                episode=current,
                previous_episodes=previous,
                namespace=namespace,
                model=model,
                max_tokens=16_384,
                structured_output_mode="json_object",
                send_max_tokens=False,
            )
            node_execution = await _execute_structured_extraction_probe_async(
                node_probe,
                completions=client.chat.completions,
                timeout_seconds=args.timeout_seconds,
            )
            row["node"] = sanitize_probe_execution(node_execution.result)
            names = _entity_names(node_execution.parsed_items)
            row["node_entity_count_in_memory"] = len(names)
            if node_execution.result.get("status") == "PASS" and len(names) >= 2:
                for _repetition in range(int(protocol["required_edge_repetitions"])):
                    edge_probe = build_structured_edge_extraction_probe(
                        episode=current,
                        previous_episodes=previous,
                        namespace=namespace,
                        model=model,
                        max_tokens=16_384,
                        entity_names=names,
                        structured_output_mode="json_object",
                        send_max_tokens=False,
                    )
                    edge_execution = await _execute_structured_extraction_probe_async(
                        edge_probe,
                        completions=client.chat.completions,
                        timeout_seconds=args.timeout_seconds,
                    )
                    row["edges"].append(
                        sanitize_probe_execution(edge_execution.result)
                    )
            names.clear()
            results.append(row)
    finally:
        await client.close()
        credential = ""

    selection = select_development_model(
        candidates=protocol["candidates"],
        results=results,
        required_edge_repetitions=int(protocol["required_edge_repetitions"]),
    )
    return {
        "schema_version": "membind.v7.development-model-candidate-artifact.v1",
        "status": "PASS" if selection["status"] == "SELECTED" else "NO_ELIGIBLE_MODEL",
        "run_id": args.run_id,
        "protocol_sha256": _sha256(PROTOCOL),
        "source_sha256": _source_bindings(),
        "candidate_results": results,
        "selection": selection,
        "model_list_http_attempt_count": 1,
        "database_called": False,
        "embedding_called": False,
        "treatment_calls": 0,
        "response_replay_calls": 0,
        "formal_r1_r3_eligible": False,
        "gate_outcome": "NOT_EVALUATED",
        "live_treatment_authorized": False,
        "raw_request_persisted": False,
        "raw_response_persisted": False,
        "response_hash_persisted": False,
        "raw_embedding_persisted": False,
        "credentials_recorded": False,
    }


def _failure(args: argparse.Namespace, error: BaseException) -> dict[str, Any]:
    return {
        "schema_version": "membind.v7.development-model-candidate-failure.v1",
        "status": "FAILED_CLOSED",
        "run_id": args.run_id,
        "protocol_sha256": _sha256(PROTOCOL),
        "error_type": f"{type(error).__module__}.{type(error).__qualname__}",
        "error_message_sha256": hashlib.sha256(
            str(error).encode("utf-8", errors="backslashreplace")
        ).hexdigest(),
        "gate_outcome": "NOT_EVALUATED",
        "formal_r1_r3_eligible": False,
        "live_treatment_authorized": False,
        "raw_request_persisted": False,
        "raw_response_persisted": False,
        "response_hash_persisted": False,
        "credentials_recorded": False,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--timeout-seconds", type=float, default=900.0)
    args = parser.parse_args()
    if not args.run_id or "/" in args.run_id or "\\" in args.run_id:
        parser.error("--run-id must be a non-path identity")
    if args.output.exists():
        parser.error("--output must be fresh")
    if args.timeout_seconds <= 0:
        parser.error("--timeout-seconds must be positive")
    protocol = _load_protocol()
    try:
        result = asyncio.run(_run(args, protocol))
    except BaseException as error:
        result = _failure(args, error)
    _write_exclusive(args.output, result)
    print(
        json.dumps(
            {
                "status": result["status"],
                "run_id": args.run_id,
                "output": str(args.output.resolve()),
                "selected_model": (result.get("selection") or {}).get(
                    "selected_model"
                ),
                "gate_outcome": "NOT_EVALUATED",
            },
            ensure_ascii=True,
            sort_keys=True,
        )
    )
    return 0 if result["status"] == "PASS" else 2


if __name__ == "__main__":
    raise SystemExit(main())
