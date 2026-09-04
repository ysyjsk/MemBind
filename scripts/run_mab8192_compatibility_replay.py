#!/usr/bin/env python3
"""Run the two preregistered P0 compatibility replays exactly once.

The raw full-session edge request characterizes the historical failure.  The
lossless-8192 request is the deployment gate.  Neither request writes Neo4j or
enters construction data.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
import sys
import time
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Mapping


ROOT = Path(__file__).resolve().parents[1]
for source in (
    ROOT / "mab_quality_v2_final_qa/src",
    ROOT / "saturated_fixed_work_baseline_v1_3/src",
):
    if str(source) not in sys.path:
        sys.path.insert(0, str(source))

from mab_quality_v2_final_qa.mab8192_adapter import MAB8192Manifest, adapter_identity  # noqa: E402
from mab_quality_v2_final_qa.mab_main_dataset import (  # noqa: E402
    DATASET_REVISION,
    build_authority,
    build_episode_inputs,
)
from saturated_fixed_work_baseline_v1_3.membind_v6_1.upstream_runtime import (  # noqa: E402
    deployment_wire_fields,
    P0_MODEL,
    P0_SAMPLING,
    logical_request_seed,
    resolve_deployment_policy,
    request_hash,
)


DEPLOYMENT_POLICY = resolve_deployment_policy()
MODEL = DEPLOYMENT_POLICY.served_model
SAMPLING = dict(DEPLOYMENT_POLICY.sampling)
from saturated_fixed_work_baseline_v1_3.membind_v7.provider_diagnostics import (  # noqa: E402
    build_structured_edge_extraction_probe,
    build_structured_extraction_probe,
)


def _wire_request(
    request: Mapping[str, Any],
    *,
    context_id: str,
    source_sequence: int,
    chunk_ordinal: int,
    prompt_name: str,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Apply only the selected deployment fields and derive the wire seed."""

    wire = dict(request)
    messages_hash = request_hash({"messages": wire["messages"]})
    identity = {
        "dataset_revision": DATASET_REVISION,
        "context_id": context_id,
        "source_sequence": int(source_sequence),
        "chunk_ordinal": int(chunk_ordinal),
        "prompt_name": prompt_name,
        "canonical_messages_hash": messages_hash,
    }
    wire.update(
        deployment_wire_fields(DEPLOYMENT_POLICY, seed=logical_request_seed(identity))
    )
    return wire, identity


def _usage(response: Any) -> dict[str, Any]:
    value = getattr(response, "usage", None)
    if value is None:
        return {}
    if hasattr(value, "model_dump"):
        return dict(value.model_dump())
    return {"value": str(value)}


def _validate_response(response: Any, model: type[Any]) -> tuple[dict[str, Any], tuple[Any, ...]]:
    content = response.choices[0].message.content or ""
    row: dict[str, Any] = {
        "finish_reason": response.choices[0].finish_reason,
        "usage": _usage(response),
        "response_characters": len(content),
        "raw_response_sha256": hashlib.sha256(content.encode("utf-8")).hexdigest(),
        "json_valid": False,
        "pydantic_valid": False,
        "schema_valid": False,
        "reached_token_limit": response.choices[0].finish_reason == "length",
    }
    try:
        parsed_json = json.loads(content)
        row["json_valid"] = True
        parsed = model.model_validate(parsed_json)
        row["pydantic_valid"] = True
        row["schema_valid"] = True
        field = "extracted_entities" if hasattr(parsed, "extracted_entities") else "edges"
        items = tuple(getattr(parsed, field))
        row["item_count"] = len(items)
        return row, items
    except BaseException as exc:
        row["validation_error_type"] = f"{type(exc).__module__}.{type(exc).__qualname__}"
        row["validation_error"] = str(exc)[:500]
        return row, ()


async def run(output_root: Path) -> dict[str, Any]:
    import httpx
    from openai import AsyncOpenAI

    if output_root.exists():
        raise FileExistsError(f"replay root must be fresh: {output_root}")
    output_root.mkdir(parents=True)
    authority = build_authority(ROOT / "mab_quality_v2_final_qa/data/official_5_contexts.json")
    context = authority["contexts"][0]
    full_episode = build_episode_inputs(context)[0]
    historical = json.loads(
        (
            Path("/data/predator/ly/Mem/run/local-qwen3-8b-awq-dualreplica-v1")
            / "root-cause-20260902T24/metadata.json"
        ).read_text(encoding="utf-8")
    )
    entity_names = [str(value) for value in historical["entity_names"]]
    raw_probe = build_structured_edge_extraction_probe(
        episode=full_episode,
        previous_episodes=(),
        namespace=f"membind-{DEPLOYMENT_POLICY.policy_id.casefold()}-raw-replay",
        model=MODEL,
        max_tokens=16384,
        entity_names=entity_names,
    )
    raw_request, raw_identity = _wire_request(
        raw_probe.request,
        context_id=context.context_id,
        source_sequence=0,
        chunk_ordinal=0,
        prompt_name="extract_edges.edge",
    )

    manifest = MAB8192Manifest.from_context(context, dataset_revision=DATASET_REVISION)
    tokenizer_path = Path(
        os.environ.get(
            "MEMBIND_LLM_MODEL_DIR",
            "/data/predator/ly/Mem/models/Qwen3-8B-AWQ",
        )
    ) / "tokenizer.json"
    from tokenizers import Tokenizer

    tokenizer = Tokenizer.from_file(str(tokenizer_path))
    largest = max(manifest.chunks, key=lambda chunk: len(tokenizer.encode(chunk.body).ids))
    probe_episode = SimpleNamespace(
        context_id=largest.context_id,
        source_sequence=0,
        episode_id=largest.chunk_id,
        reference_time=largest.reference_time,
        body=largest.body,
    )
    node_probe = build_structured_extraction_probe(
        episode=probe_episode,
        previous_episodes=(),
        namespace=f"membind-{DEPLOYMENT_POLICY.policy_id.casefold()}-mab8192-replay",
        model=MODEL,
        max_tokens=16384,
    )
    node_request, node_identity = _wire_request(
        node_probe.request,
        context_id=largest.context_id,
        source_sequence=largest.source_sequence,
        chunk_ordinal=largest.chunk_ordinal,
        prompt_name="extract_nodes.extract_message",
    )

    timeout = httpx.Timeout(connect=10.0, read=3600.0, write=3600.0, pool=3600.0)
    client = AsyncOpenAI(
        api_key="membind-local",
        base_url=os.environ.get("NATIVE_LLM_BASE_URL", "http://127.0.0.1:18200/v1"),
        timeout=timeout,
        max_retries=0,
        http_client=httpx.AsyncClient(timeout=timeout, trust_env=False),
    )
    requests_started = 0
    try:
        raw_started = time.time()
        requests_started += 1
        raw_response = await client.chat.completions.create(**raw_request)
        raw_result, _ = _validate_response(raw_response, raw_probe.response_model)
        raw_result.update(
            {
                "prompt_name": "extract_edges.edge",
                "logical_identity": raw_identity,
                "request_sha256": request_hash(raw_request),
                "elapsed_seconds": time.time() - raw_started,
                "historical_messages_sha256": historical["edge_16384"]["messages_sha256"],
            }
        )

        node_started = time.time()
        requests_started += 1
        node_response = await client.chat.completions.create(**node_request)
        node_result, nodes = _validate_response(node_response, node_probe.response_model)
        node_result.update(
            {
                "prompt_name": "extract_nodes.extract_message",
                "logical_identity": node_identity,
                "request_sha256": request_hash(node_request),
                "elapsed_seconds": time.time() - node_started,
            }
        )
        names: list[str] = []
        for node in nodes:
            name = str(getattr(node, "name", "")).strip()
            if name and name not in names:
                names.append(name)
        if len(names) < 2:
            compatibility_edge = {
                "status": "NOT_RUN",
                "reason": "node replay returned fewer than two entities",
            }
        else:
            edge_probe = build_structured_edge_extraction_probe(
                episode=probe_episode,
                previous_episodes=(),
                namespace=f"membind-{DEPLOYMENT_POLICY.policy_id.casefold()}-mab8192-replay",
                model=MODEL,
                max_tokens=16384,
                entity_names=names,
            )
            edge_request, edge_identity = _wire_request(
                edge_probe.request,
                context_id=largest.context_id,
                source_sequence=largest.source_sequence,
                chunk_ordinal=largest.chunk_ordinal,
                prompt_name="extract_edges.edge",
            )
            edge_started = time.time()
            requests_started += 1
            edge_response = await client.chat.completions.create(**edge_request)
            compatibility_edge, _ = _validate_response(edge_response, edge_probe.response_model)
            compatibility_edge.update(
                {
                    "prompt_name": "extract_edges.edge",
                    "logical_identity": edge_identity,
                    "request_sha256": request_hash(edge_request),
                    "elapsed_seconds": time.time() - edge_started,
                }
            )
    finally:
        await client.close()

    def valid(row: Mapping[str, Any]) -> bool:
        return (
            row.get("finish_reason") == "stop"
            and row.get("json_valid") is True
            and row.get("pydantic_valid") is True
            and row.get("schema_valid") is True
            and row.get("reached_token_limit") is False
        )

    compatibility_pass = valid(node_result) and valid(compatibility_edge)
    result = {
        "schema_version": "membind.model-compatibility-replay.v1",
        "decision_context": {
            "hypothesis": "Qwen2.5-7B-Instruct-AWQ official decoding may avoid the Qwen3-8B deterministic length-stop observed on a formal MAB8192 upstream request.",
            "why_existing_evidence_is_insufficient": "The prior P0 replay passed a smaller isolated witness, while the formal P0 attempt deterministically truncated a growing-history node request at 16384 completion tokens.",
            "code_or_config_difference": "Only the pre-registered model deployment and official Qwen2.5 sampling differ; Graphiti messages, JSON schema, max_tokens, adapter and call graph remain unchanged.",
            "expected_observation": "Both node and edge witness responses stop normally and validate as JSON/Pydantic without provider exception or token-limit truncation.",
            "pass_criteria": "finish_reason=stop, JSON/Pydantic/schema valid, and reached_token_limit=false for both MAB8192 witness calls.",
            "fail_criteria": "Any provider exception, malformed response, validation failure, or finish_reason=length on either witness call.",
            "next_decision": "Only PASS authorizes the selected deployment for full-history qualification; FAIL preserves evidence and does not permit schema/retry/repair changes.",
        },
        "status": "PASS" if compatibility_pass else "COMPATIBILITY_FAILED",
        "scope": "DIAGNOSTIC_AND_DEPLOYMENT_GATE_NOT_CONSTRUCTION",
        "provider_request_count": requests_started,
        "provider_retry_count": 0,
        "deployment_policy_id": DEPLOYMENT_POLICY.policy_id,
        "model": MODEL,
        "sampling": SAMPLING,
        "adapter": adapter_identity(),
        "historical_raw_failure_replay": raw_result,
        "mab8192_witness": {
            "context_id": largest.context_id,
            "session_id": largest.session_id,
            "source_sequence": largest.source_sequence,
            "chunk_ordinal": largest.chunk_ordinal,
            "chunk_id": largest.chunk_id,
            "characters": len(largest.body),
            "tokens": len(tokenizer.encode(largest.body).ids),
            "node": node_result,
            "edge": compatibility_edge,
        },
        "selection": DEPLOYMENT_POLICY.policy_id if compatibility_pass else "COMPATIBILITY_FAILED",
        "completed_unix": time.time(),
    }
    result["payload_sha256"] = request_hash(result)
    (output_root / "MODEL_COMPATIBILITY_REPLAY.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    (output_root / "MODEL_COMPATIBILITY_REPLAY.md").write_text(
        "# Model Compatibility Replay\n\n"
        f"Status: `{result['status']}`; selected deployment: `{result['selection']}`.\n\n"
        f"The historical full-session edge request returned `{raw_result.get('finish_reason')}` "
        f"with `{raw_result.get('usage', {}).get('completion_tokens')}` completion tokens. "
        "This row is diagnostic only.\n\n"
        f"The lossless-8192 witness contains `{result['mab8192_witness']['tokens']}` tokenizer "
        f"tokens. Node validation: `{valid(node_result)}`; edge validation: "
        f"`{valid(compatibility_edge)}`. No retry, database write, or embedding call was made.\n",
        encoding="utf-8",
    )
    return result


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output-root", type=Path, required=True)
    args = parser.parse_args()
    result = asyncio.run(run(args.output_root.resolve()))
    print(json.dumps({"status": result["status"], "selection": result["selection"], "root": str(args.output_root.resolve())}, sort_keys=True))
    return 0 if result["status"] == "PASS" else 2


if __name__ == "__main__":
    raise SystemExit(main())
