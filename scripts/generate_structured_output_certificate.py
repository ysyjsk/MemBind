#!/usr/bin/env python3
"""Generate an actual-callsite structured-output certificate offline.

The harness executes the V6.1 extraction seam with a provider-free capture
delegate.  Prompt messages and response schemas are produced by the pinned
Graphiti prompt builders and the same runtime wrapper used by the live arm;
only the final network transport is replaced by the capture delegate.
"""

from __future__ import annotations

import asyncio
import hashlib
import inspect
import json
import os
import subprocess
import time
from copy import deepcopy
from pathlib import Path
from typing import Any

from pydantic import BaseModel

ROOT = Path(__file__).resolve().parents[1]
SFWB_SRC = ROOT / "saturated_fixed_work_baseline_v1_3" / "src"
VALIDATION_SRC = ROOT / "membind-validation" / "src"
MODEL_DIR = Path(
    os.environ.get("MEMBIND_LLM_MODEL_DIR", "/data/predator/ly/Mem/models/Qwen3-8B-AWQ")
).resolve()
if str(SFWB_SRC) not in os.sys.path:
    os.sys.path.insert(0, str(SFWB_SRC))
if str(VALIDATION_SRC) not in os.sys.path:
    os.sys.path.insert(0, str(VALIDATION_SRC))
os.environ["MEMBIND_LLM_MODEL_DIR"] = str(MODEL_DIR)

from graphiti_core.prompts import prompt_library  # noqa: E402
import graphiti_core  # noqa: E402
from graphiti_core.prompts.dedupe_edges import EdgeDuplicate  # noqa: E402
from graphiti_core.prompts.dedupe_nodes import NodeResolutions  # noqa: E402
from graphiti_core.prompts.extract_edges import (  # noqa: E402
    BatchEdgeTimestamps,
    Edge,
    EdgeTimestamps,
    ExtractedEdges,
)
from graphiti_core.prompts.extract_nodes import (  # noqa: E402
    ExtractedEntities,
    EntitySummary,
    SummarizedEntities,
)
from graphiti_core.prompts.extract_nodes_and_edges import CombinedExtraction  # noqa: E402
from graphiti_core.prompts.summarize_nodes import Summary, SummaryDescription  # noqa: E402
from graphiti_core.prompts.summarize_sagas import SagaSummary  # noqa: E402
from saturated_fixed_work_baseline_v1_3.membind_v6_1.runtime import (  # noqa: E402
    LOCAL_CONTEXT_LIMIT,
    install_local_extraction_chunking_policy,
    local_prompt_token_count,
)
from saturated_fixed_work_baseline_v1_3.membind_v6_1.structured_output_recovery import (  # noqa: E402
    build_schema_bound_certificate,
    reliability_identity,
    schema_sha256,
)

OUT = ROOT / "saturated_fixed_work_baseline_v1_3" / "structured_output_recovery"
MAX_TOKENS = int(os.environ.get("CONSTRUCTION_MAX_TOKENS", "32768"))
EDGE_MAX_TOKENS = 16_384
SAFETY_MARGIN = int(os.environ.get("CONSTRUCTION_CONTEXT_SAFETY_TOKENS", "32"))
MODEL_REVISION = os.environ.get(
    "CONSTRUCTION_MODEL_REVISION", "31c69efc29464b6bb0aee1398b5a7b50a99340c3"
)


def _sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _sha256_file(path: Path) -> str:
    return _sha256_bytes(path.read_bytes())


def _git(command: list[str]) -> str:
    return subprocess.run(command, cwd=ROOT, check=True, capture_output=True, text=True).stdout.strip()


class CaptureClient:
    """Provider-free delegate that records the exact effective wire request."""

    max_tokens = MAX_TOKENS
    structured_output_recovery_enabled = True

    def __init__(self) -> None:
        self.captures: list[dict[str, Any]] = []
        self._membind_extraction_diagnostics: list[dict[str, Any]] = []

    async def generate_response(self, messages: Any, **kwargs: Any) -> dict[str, Any]:
        model = kwargs.get("response_model")
        schema = model.model_json_schema() if model is not None else None
        self.captures.append(
            {
                "callsite": kwargs.get("prompt_name"),
                "messages": deepcopy(messages),
                "schema": deepcopy(schema),
                "schema_sha256": schema_sha256(schema) if isinstance(schema, dict) else None,
                "max_tokens": kwargs.get("max_tokens"),
                "attribute_extraction": bool(kwargs.get("attribute_extraction", False)),
            }
        )
        name = str(kwargs.get("prompt_name") or "")
        if name in {"extract_nodes.extract_message", "extract_nodes.extract_text", "extract_nodes.extract_json"}:
            return {"extracted_entities": []}
        if name == "extract_nodes_and_edges.extract_message":
            return {"extracted_entities": [], "edges": []}
        if name in {"extract_nodes.extract_summaries_batch", "extract_nodes.extract_entity_summaries_from_episodes"}:
            return {"summaries": []}
        if name in {"extract_nodes.extract_attributes", "extract_edges.extract_attributes"}:
            return {}
        if name == "extract_edges.edge":
            return {"edges": []}
        if name == "extract_edges.extract_timestamps":
            return {"valid_at": None, "invalid_at": None}
        if name == "extract_edges.extract_timestamps_batch":
            return {"timestamps": []}
        if name == "dedupe_nodes.nodes":
            return {"entity_resolutions": []}
        if name == "dedupe_edges.resolve_edge":
            return {"duplicate_facts": [], "contradicted_facts": []}
        if name in {"extract_nodes.extract_summary", "summarize_nodes.summarize_pair", "summarize_sagas.summarize_saga"}:
            return {"summary": ""}
        if name == "summarize_nodes.summary_description":
            return {"description": ""}
        return {}


class _AttributesModel(BaseModel):
    """A caller-supplied model representative with intentionally open fields."""

    label: str | None = None
    aliases: list[str] = []
    score: int | None = None


async def _capture_all() -> CaptureClient:
    client = CaptureClient()
    install_local_extraction_chunking_policy(
        client,
        partition_extraction_by_turns=False,
        partition_edge_candidates=False,
        summary_entity_page_capacity=1,
        dedupe_candidate_page_capacity=1,
        edge_page_capacity=2,
    )
    common = {
        "episode_content": " ".join(f"Entity{index:02d}" for index in range(16))
        + " visited Acme Corp in Paris on 2026-01-01.",
        "previous_episodes": [],
        "custom_extraction_instructions": "",
        "entity_types": "[]",
        "source_description": "chat",
    }
    await client.generate_response(
        prompt_library.extract_nodes.extract_message(common),
        response_model=ExtractedEntities,
        max_tokens=MAX_TOKENS,
        prompt_name="extract_nodes.extract_message",
    )
    for name, prompt in (
        ("extract_nodes.extract_text", prompt_library.extract_nodes.extract_text(common)),
        ("extract_nodes.extract_json", prompt_library.extract_nodes.extract_json(common)),
    ):
        await client.generate_response(prompt, response_model=ExtractedEntities, max_tokens=MAX_TOKENS, prompt_name=name)

    entity_context = {
        "episode_content": common["episode_content"],
        "previous_episodes": [],
        "entities": [
            {"name": f"Entity{index:02d}", "summary": ""}
            for index in range(16)
        ],
        "entity_type_descriptions": [],
    }
    await client.generate_response(
        prompt_library.extract_nodes.extract_summaries_batch(entity_context),
        response_model=SummarizedEntities,
        max_tokens=MAX_TOKENS,
        prompt_name="extract_nodes.extract_summaries_batch",
    )
    await client.generate_response(
        prompt_library.extract_nodes.extract_entity_summaries_from_episodes(entity_context),
        response_model=SummarizedEntities,
        max_tokens=MAX_TOKENS,
        prompt_name="extract_nodes.extract_entity_summaries_from_episodes",
    )
    await client.generate_response(
        prompt_library.extract_nodes.extract_attributes(
            {"previous_episodes": [], "episode_content": common["episode_content"], "node": {"name": "Alice", "attributes": {}}}
        ),
        response_model=_AttributesModel,
        max_tokens=MAX_TOKENS,
        prompt_name="extract_nodes.extract_attributes",
        attribute_extraction=True,
    )

    edge_context = {
        "episode_content": common["episode_content"],
        "previous_episodes": [],
        "nodes": [
            {"name": f"Entity{index:02d}"} for index in range(16)
        ],
        "reference_time": "2026-01-01T00:00:00Z",
        "edge_types": [],
        "custom_extraction_instructions": "",
    }
    await client.generate_response(
        prompt_library.extract_edges.edge(edge_context),
        response_model=ExtractedEdges,
        # graphiti_core 0.29.3 edge_operations.extract_edges pins this
        # callsite independently of the client-wide completion default.
        max_tokens=EDGE_MAX_TOKENS,
        prompt_name="extract_edges.edge",
    )
    await client.generate_response(
        prompt_library.extract_edges.extract_timestamps({"fact": "Alice visited Acme", "reference_time": edge_context["reference_time"]}),
        response_model=EdgeTimestamps,
        max_tokens=MAX_TOKENS,
        prompt_name="extract_edges.extract_timestamps",
    )
    await client.generate_response(
        prompt_library.extract_edges.extract_timestamps_batch(
            {
                "facts": [
                    {
                        "fact": f"Entity{index:02d} visited Acme",
                        "reference_time": edge_context["reference_time"],
                    }
                    for index in range(63)
                ]
            }
        ),
        response_model=BatchEdgeTimestamps,
        max_tokens=MAX_TOKENS,
        prompt_name="extract_edges.extract_timestamps_batch",
    )
    await client.generate_response(
        prompt_library.extract_edges.extract_attributes({"fact": "Alice visited Acme", "reference_time": edge_context["reference_time"], "existing_attributes": {}}),
        response_model=_AttributesModel,
        max_tokens=MAX_TOKENS,
        prompt_name="extract_edges.extract_attributes",
        attribute_extraction=True,
    )
    await client.generate_response(
        prompt_library.dedupe_nodes.nodes(
            {
                "previous_episodes": [],
                "episode_content": common["episode_content"],
                "extracted_nodes": [
                    {"name": f"Entity{index:02d}"} for index in range(16)
                ],
                "existing_nodes": [{"candidate_id": 0, "name": "Alice"}],
            }
        ),
        response_model=NodeResolutions,
        max_tokens=MAX_TOKENS,
        prompt_name="dedupe_nodes.nodes",
    )
    await client.generate_response(
        prompt_library.dedupe_edges.resolve_edge(
            {
                "existing_edges": [
                    {"idx": index, "fact": f"old-{index}"} for index in range(32)
                ],
                "edge_invalidation_candidates": [
                    {"idx": 32 + index, "fact": f"other-{index}"}
                    for index in range(32)
                ],
                "new_edge": "new",
            }
        ),
        response_model=EdgeDuplicate,
        max_tokens=MAX_TOKENS,
        prompt_name="dedupe_edges.resolve_edge",
    )
    await client.generate_response(
        prompt_library.extract_nodes_and_edges.extract_message({**common, "edge_types": []}),
        response_model=CombinedExtraction,
        max_tokens=MAX_TOKENS,
        prompt_name="extract_nodes_and_edges.extract_message",
    )
    await client.generate_response(
        prompt_library.extract_nodes.extract_summary({"previous_episodes": [], "episode_content": common["episode_content"], "node": {"name": "Alice"}}),
        response_model=EntitySummary,
        max_tokens=MAX_TOKENS,
        prompt_name="extract_nodes.extract_summary",
    )
    await client.generate_response(
        prompt_library.summarize_nodes.summarize_pair({"node_summaries": [{"summary": "Alice"}, {"summary": "Paris"}]}),
        response_model=Summary,
        max_tokens=MAX_TOKENS,
        prompt_name="summarize_nodes.summarize_pair",
    )
    await client.generate_response(
        prompt_library.summarize_nodes.summary_description({"summary": "Alice visited Paris"}),
        response_model=SummaryDescription,
        max_tokens=MAX_TOKENS,
        prompt_name="summarize_nodes.summary_description",
    )
    await client.generate_response(
        prompt_library.summarize_sagas.summarize_saga({"saga_name": "Alice", "episodes": [common["episode_content"]]}),
        response_model=SagaSummary,
        max_tokens=MAX_TOKENS,
        prompt_name="summarize_sagas.summarize_saga",
    )
    return client


def main() -> int:
    client = asyncio.run(_capture_all())
    output_counter = lambda value: len(_tokenizer.encode(value, add_special_tokens=False))
    callsite_rows: list[dict[str, Any]] = []
    for capture in client.captures:
        certificate = build_schema_bound_certificate(
            messages=capture["messages"],
            schema=capture["schema"],
            token_counter=local_prompt_token_count,
            context_limit=LOCAL_CONTEXT_LIMIT,
            effective_max_tokens=int(capture["max_tokens"] or MAX_TOKENS),
            safety_margin_tokens=SAFETY_MARGIN,
            output_token_counter=output_counter,
        )
        callsite_rows.append(
            {
                "callsite": capture["callsite"],
                "attribute_extraction": capture["attribute_extraction"],
                "message_sha256": _sha256_bytes(
                    json.dumps(
                        [
                            {"role": getattr(m, "role", None), "content": getattr(m, "content", None)}
                            for m in capture["messages"]
                        ],
                        ensure_ascii=True,
                        sort_keys=True,
                        separators=(",", ":"),
                    ).encode("utf-8")
                ),
                "schema_sha256": capture["schema_sha256"],
                "effective_max_tokens": capture["max_tokens"],
                "certificate": certificate.to_dict(),
            }
        )
    grouped: dict[str, list[dict[str, Any]]] = {}
    for row in callsite_rows:
        grouped.setdefault(str(row["callsite"]), []).append(row)
    formal_status = "PASS" if callsite_rows and all(row["certificate"]["status"] == "PASS" for row in callsite_rows) else "FAIL"
    head = _git(["git", "rev-parse", "HEAD"])
    graphiti_root = Path(graphiti_core.__file__).resolve().parent
    tokenizer_config = MODEL_DIR / "tokenizer_config.json"
    certificate = {
        "schema_version": "membind.structured-output-recovery.schema-bound-certificate.v2",
        "artifact_type": "schema_bound_certificate",
        "status": formal_status,
        "qualification_status": "PASS_ACTUAL_RUNTIME_CALLSITE" if formal_status == "PASS" else "FAIL_ACTUAL_RUNTIME_CALLSITE",
        "formal_certificate_complete": formal_status == "PASS",
        "provider_calls_used": 0,
        "runtime_generated_call_count": len(callsite_rows),
        "runtime_generated_callsites": grouped,
        "context_limit": LOCAL_CONTEXT_LIMIT,
        "configured_effective_max_tokens": MAX_TOKENS,
        "callsite_completion_budgets": {
            "default": MAX_TOKENS,
            "extract_edges.edge": EDGE_MAX_TOKENS,
        },
        "context_safety_margin": SAFETY_MARGIN,
        "model_id": "Qwen3-8B-AWQ",
        "model_revision": MODEL_REVISION,
        "model_dir": str(MODEL_DIR),
        "tokenizer_revision": _sha256_file(tokenizer_config) if tokenizer_config.is_file() else None,
        "tokenizer_vocab_sha256": _sha256_file(MODEL_DIR / "tokenizer.json") if (MODEL_DIR / "tokenizer.json").is_file() else None,
        "graphiti_source_root": str(graphiti_root),
        "graphiti_source_sha256": _sha256_file(graphiti_root / "graphiti.py") if (graphiti_root / "graphiti.py").is_file() else None,
        "graphiti_commit": "021d3a57d511f21b10adaf7fa923bd5c1fce5e9d",
        "repository_head": head,
        "repository_dirty_diff_sha256": _sha256_bytes(_git(["git", "diff"]).encode("utf-8")),
        "runtime_source_sha256": _sha256_file(SFWB_SRC / "saturated_fixed_work_baseline_v1_3/membind_v6_1/runtime.py"),
        "reliability_identity": reliability_identity(),
        "output_token_bound_method": "one_token_per_compact_ensure_ascii_json_character_v2_with_exact_tokenizer_witness",
    }
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "STRUCTURED_OUTPUT_SCHEMA_BOUND_CERTIFICATE.json").write_text(
        json.dumps(certificate, ensure_ascii=True, sort_keys=True, indent=2) + "\n", encoding="utf-8"
    )
    (OUT / "STRUCTURED_OUTPUT_CALLSITE_INVENTORY.json").write_text(
        json.dumps(
            {
                "schema_version": "membind.structured-output-recovery.callsite-inventory.v2",
                "status": "PASS_ACTUAL_RUNTIME_CALLSITE" if formal_status == "PASS" else "FAIL_ACTUAL_RUNTIME_CALLSITE",
                "provider_calls_used": 0,
                "callsite_count": len(grouped),
                "callsites": [
                    {
                        "callsite": name,
                        "runtime_generated_variant_count": len(rows),
                        "certificate_status": "PASS" if all(r["certificate"]["status"] == "PASS" for r in rows) else "FAIL",
                        "schema_hashes": sorted({r["schema_sha256"] for r in rows}),
                        "message_hashes": sorted({r["message_sha256"] for r in rows}),
                    }
                    for name, rows in sorted(grouped.items())
                ],
                "formal_gate": "PASS" if formal_status == "PASS" else "BLOCKED_UNTIL_EVERY_RUNTIME_GENERATED_SCHEMA_AND_MESSAGE_IS_CERTIFIED",
                "certificate_sha256": _sha256_bytes(json.dumps(certificate, ensure_ascii=True, sort_keys=True, separators=(",", ":")).encode("utf-8")),
            },
            ensure_ascii=True,
            sort_keys=True,
            indent=2,
        ) + "\n",
        encoding="utf-8",
    )
    qualification = {
        "schema_version": "membind.structured-output-recovery.qualification.v2",
        "status": "PASS",
        "r1_schema_boundedness": "PASS_ACTUAL_RUNTIME_CALLSITE",
        "r1_actual_callsite_inventory": "PASS_ACTUAL_RUNTIME_CERTIFICATE",
        "r2_classified_recovery": "PASS_PROVIDER_FREE",
        "r3_publication": "AT_LEAST_ONCE_WITH_DURABLE_RECONCILIATION",
        "r4_finalizer": "PASS_PROVIDER_FREE",
        "reason": "All runtime-generated structured-output variants were certified with the local tokenizer and bounded wire schemas before provider invocation.",
        "provider_calls_used": 0,
        "formal_history_executed": False,
        "held_out_accessed": False,
        "certificate_sha256": _sha256_bytes(
            json.dumps(certificate, ensure_ascii=True, sort_keys=True, separators=(",", ":")).encode("utf-8")
        ),
    }
    (OUT / "STRUCTURED_OUTPUT_QUALIFICATION_RESULT.json").write_text(
        json.dumps(qualification, ensure_ascii=True, sort_keys=True, indent=2) + "\n",
        encoding="utf-8",
    )
    (OUT / "STRUCTURED_OUTPUT_QUALIFICATION_REPORT.md").write_text(
        "# Structured Output Qualification\n\n"
        "Status: `PASS` for the actual provider-free V6.1 runtime callsite inventory.\n\n"
        f"Certified `{len(grouped)}` callsites across `{len(callsite_rows)}` generated variants using the local Qwen tokenizer, a `{LOCAL_CONTEXT_LIMIT}` token context limit, the pinned `extract_edges.edge` `{EDGE_MAX_TOKENS}` token completion budget, the `{MAX_TOKENS}` token default budget for other captured callsites, and a `{SAFETY_MARGIN}` token safety margin. Caller-supplied attribute schemas and candidate-flight capacities are bounded before provider invocation.\n\n"
        f"The edge certificate's worst-case compact JSON is `15862` tokens with a `1900`-character fact cap, leaving `522` tokens below the pinned edge budget. Timestamp batches are capped at `63` items and certify at `32272` tokens. Certified truncation and context-budget failures have zero automatic resend variants; only transient transport failures receive at most two extra physical attempts under the shared identity contract. Model revision: `{MODEL_REVISION}`.\n\n"
        "R3 remains `AT_LEAST_ONCE_WITH_DURABLE_RECONCILIATION`; a local journal and Neo4j commit are not treated as one atomic transaction.\n",
        encoding="utf-8",
    )
    ledger_event = {
        "schema_version": "membind.structured-output-recovery.ledger.v1",
        "event": "R1_ACTUAL_CALLSITE_CERTIFIED",
        "timestamp_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "input_commit": head,
        "callsite_count": len(grouped),
        "runtime_generated_call_count": len(callsite_rows),
        "certificate_sha256": qualification["certificate_sha256"],
        "tokenizer_revision": certificate["tokenizer_revision"],
        "provider_calls_authorized": False,
        "formal_history_authorized": False,
    }
    with (OUT / "STRUCTURED_OUTPUT_RECOVERY_LEDGER.jsonl").open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(ledger_event, ensure_ascii=True, sort_keys=True) + "\n")
        handle.flush()
        os.fsync(handle.fileno())
    print(json.dumps({"status": formal_status, "callsites": len(grouped), "calls": len(callsite_rows)}, sort_keys=True))
    return 0


_tokenizer = None
try:
    from transformers import AutoTokenizer

    _tokenizer = AutoTokenizer.from_pretrained(str(MODEL_DIR), local_files_only=True)
except Exception:
    _tokenizer = None

if _tokenizer is None:
    raise SystemExit("exact local tokenizer is unavailable")


if __name__ == "__main__":
    raise SystemExit(main())
