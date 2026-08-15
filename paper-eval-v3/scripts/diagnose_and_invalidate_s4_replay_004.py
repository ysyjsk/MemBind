#!/usr/bin/env python3
"""Seal the order-only replay miss diagnosis and clean its exact namespace."""

from __future__ import annotations

import asyncio
import copy
import json
import re
import subprocess
import sys
from pathlib import Path

from paper_eval import PROTOCOL_VERSION
from paper_eval.artifacts import finalize_envelope, payload_sha256, sha256_file
from paper_eval.s1_live import S1LiveAdapter
from paper_eval.s4_authority import _write_exclusive
from paper_eval.s4_preflight_production import load_s4_preflight_env


PROJECT = Path(__file__).resolve().parents[1]
ROOT = PROJECT.parent
LEGACY = ROOT / "membind-validation"
NATIVE = PROJECT / "artifacts/paper_eval/native"
RUN_DIR = NATIVE / "runs/s4-d0-replay-20260814-004"
CACHE = PROJECT / "runtime/private/s4-d0-07741c45-20260814-004/prompt.jsonl"
DATASET = Path(
    "/data/predator/ly/Mem/data/raw/longmemeval-cleaned/longmemeval_s_cleaned.json"
)
NAMESPACE = "pev3-s4-d0-replay-20260814-004"
TARGET_HASH = "ea52679ceffdf0aa7a07ea8fa513fa22391bc7f47f9b0c2a6d7313d142cebb2a"
OUTPUT = RUN_DIR / "DIAGNOSIS_AND_INVALIDATION.json"


def _node_key(value: dict) -> tuple[str, ...]:
    name = str(value.get("name", ""))
    labels = tuple(
        sorted(
            (str(item) for item in value.get("entity_types", []) or []),
            key=lambda item: (item.casefold(), item),
        )
    )
    summary = str(value.get("summary", ""))
    attributes = {
        key: child
        for key, child in value.items()
        if key not in {"candidate_id", "name", "entity_types", "summary"}
    }
    return (
        name.casefold(),
        name,
        json.dumps(labels, ensure_ascii=False, separators=(",", ":")),
        summary.casefold(),
        summary,
        json.dumps(
            attributes,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            default=str,
        ),
    )


def _safe_diagnosis() -> dict:
    source = str(LEGACY / "src")
    if source not in sys.path:
        sys.path.insert(0, source)
    from dataset import build_episodes
    from response_cache import PromptParts, compute_prompt_hash

    phase_result = json.loads(
        (RUN_DIR / "phase_result.json").read_text(encoding="utf-8")
    )
    runtime = phase_result["payload"]["runtime_evidence"]
    if (
        phase_result["payload"]["error_class"] != "UnexpectedPromptError"
        or runtime["unexpected_prompt_count"] != 1
        or runtime["live_llm_calls"] != 0
        or runtime["live_embedding_calls"] != 0
        or runtime["live_fallback_count"] != 0
    ):
        raise RuntimeError("replay failure no longer matches the prompt-miss diagnosis")

    dataset = json.loads(DATASET.read_text(encoding="utf-8"))
    instance = next(item for item in dataset if item["question_id"] == "07741c45")
    episode = build_episodes(instance)[2]
    records = [
        json.loads(line)
        for line in CACHE.read_text(encoding="utf-8").splitlines()
        if line
    ]
    matches = [
        record
        for record in records
        if record["prompt_parts"]["decoding_config"].get("prompt_name")
        == "dedupe_nodes.nodes"
        and episode.body in record["prompt_parts"]["user_prompt"]
    ]
    if len(matches) != 1:
        raise RuntimeError("capture prompt diagnosis is not unique")
    record = matches[0]
    parts = record["prompt_parts"]
    user_prompt = parts["user_prompt"]
    section = re.search(
        r"<EXISTING ENTITIES>\n(.*?)\n</EXISTING ENTITIES>",
        user_prompt,
        re.DOTALL,
    )
    if section is None:
        raise RuntimeError("dedupe prompt existing-entity section is missing")
    original = json.loads(section.group(1))
    ordered = sorted(copy.deepcopy(original), key=_node_key)
    for candidate_id, candidate in enumerate(ordered):
        candidate["candidate_id"] = candidate_id
    sorted_prompt = (
        user_prompt[: section.start(1)]
        + json.dumps(ordered, ensure_ascii=False)
        + user_prompt[section.end(1) :]
    )
    sorted_parts = PromptParts(
        model_revision=parts["model_revision"],
        decoding_config=parts["decoding_config"],
        structured_output_schema=parts["structured_output_schema"],
        system_prompt=parts["system_prompt"],
        user_prompt=sorted_prompt,
    )
    sorted_hash = compute_prompt_hash(sorted_parts)
    if sorted_hash != TARGET_HASH or original == ordered:
        raise RuntimeError("stable candidate ordering does not reproduce replay miss")
    normalized_candidates = []
    for candidate in original:
        selected = dict(candidate)
        selected.pop("candidate_id", None)
        normalized_candidates.append(selected)
    candidate_set_sha = payload_sha256(
        sorted(normalized_candidates, key=lambda value: json.dumps(value, sort_keys=True))
    )
    resolutions = record.get("parsed_response", {}).get("entity_resolutions", [])
    nonnegative = sum(
        int(resolution.get("duplicate_candidate_id", -1)) >= 0
        for resolution in resolutions
    )
    return {
        "failure_class": "UnexpectedPromptError",
        "failure_source_sequence": 2,
        "prompt_name": "dedupe_nodes.nodes",
        "capture_prompt_hash": record["prompt_hash"],
        "replay_prompt_hash": TARGET_HASH,
        "stable_sorted_prompt_hash": sorted_hash,
        "candidate_count": len(original),
        "candidate_set_sha256": candidate_set_sha,
        "candidate_set_equal": True,
        "candidate_order_changed": True,
        "stable_sort_exactly_reproduces_replay_hash": True,
        "cached_resolution_count": len(resolutions),
        "cached_nonnegative_candidate_id_count": nonnegative,
        "classification": "ORDER_ONLY_CANDIDATE_RENUMBERING_CONFIRMED",
        "general_replay_requirement": (
            "CANDIDATE_ID_AWARE_RESPONSE_REMAP_OR_CAPTURE_ORDER_REPLAY"
        ),
    }


async def _run(driver: object) -> dict:
    from graphiti_core.utils.maintenance.graph_data_operations import clear_data

    diagnosis = _safe_diagnosis()
    adapter = S1LiveAdapter(NAMESPACE)
    before = await adapter.namespace_state(driver)
    await clear_data(driver, group_ids=[NAMESPACE])
    after = await adapter.namespace_state(driver)
    if int(after["node_count"]) or int(after["relationship_count"]):
        raise RuntimeError("replay exact cleanup left live state")
    payload = {
        "schema_version": "membind.paper-eval-v3.s4-replay-diagnosis.v1",
        "stage": "S4",
        "run_id": "s4-d0-replay-20260814-004",
        "namespace": NAMESPACE,
        "status": "INCOMPLETE_DIAGNOSED_NON_MERGEABLE",
        "diagnosis": diagnosis,
        "evidence_file_sha256": {
            "phase_result": sha256_file(RUN_DIR / "phase_result.json"),
            "checkpoint": sha256_file(RUN_DIR / "checkpoint.json"),
            "events": sha256_file(RUN_DIR / "events.jsonl"),
            "prompt_cache": sha256_file(CACHE),
            "deterministic_search_source": sha256_file(
                LEGACY / "src/deterministic_search.py"
            ),
            "graphiti_node_operations_source": sha256_file(
                LEGACY
                / ".venv/lib/python3.12/site-packages/graphiti_core/utils/maintenance/node_operations.py"
            ),
        },
        "pre_cleanup": {
            "node_count": int(before["node_count"]),
            "relationship_count": int(before["relationship_count"]),
        },
        "cleanup": {
            "scope": "EXACT_GROUP_ID_ONLY",
            "group_ids": [NAMESPACE],
            "global_cleanup_used": False,
            "post_cleanup_node_count": int(after["node_count"]),
            "post_cleanup_relationship_count": int(after["relationship_count"]),
        },
        "mergeable": False,
        "qualification_authorized": False,
        "s5_authorized": False,
        "pilot_execution_authorized": False,
    }
    git_commit = subprocess.check_output(
        ["git", "rev-parse", "HEAD"], cwd=ROOT, text=True
    ).strip()
    artifact = finalize_envelope(
        payload=payload,
        protocol_version=PROTOCOL_VERSION,
        git_commit=git_commit,
        run_id="s4-d0-replay-diagnosis-20260814-004",
    )
    _write_exclusive(OUTPUT, artifact)
    return artifact


def main() -> None:
    source = str(LEGACY / "src")
    if source not in sys.path:
        sys.path.insert(0, source)
    from graphiti_core.driver.neo4j_driver import Neo4jDriver

    env = load_s4_preflight_env(LEGACY / ".env")
    driver = Neo4jDriver(
        env["NEO4J_URI"], env["NEO4J_USER"], env["NEO4J_PASSWORD"]
    )

    async def execute() -> dict:
        try:
            return await _run(driver)
        finally:
            await driver.close()

    artifact = asyncio.run(execute())
    print(
        json.dumps(
            {
                "status": artifact["payload"]["status"],
                "diagnosis": artifact["payload"]["diagnosis"],
                "pre_cleanup": artifact["payload"]["pre_cleanup"],
                "cleanup": artifact["payload"]["cleanup"],
                "artifact_file_sha256": sha256_file(OUTPUT),
            },
            sort_keys=True,
        )
    )


if __name__ == "__main__":
    main()
