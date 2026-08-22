"""Append-only V5 extension and reducer contracts."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Mapping

FORMAL_HISTORIES = ("07741c45", "b6019101", "6071bd76", "a2f3aa27")
BASELINE_METHODS = ("B0_NATIVE_SERIAL", "B1_NAIVE_WHOLE_UPDATE_ASYNC")
V5_METHOD = "V5_VERSIONED_ORACLE_HOIST"


class CampaignContractError(ValueError):
    pass


def _hash_json(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode()).hexdigest()


def _read_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise CampaignContractError(f"artifact unreadable: {path}") from exc


def verify_baseline_reference(baseline_root: str | Path) -> dict[str, Any]:
    root = Path(baseline_root).resolve()
    seal = root / "formal_run_seal.json"
    results = root / "qualification" / "baseline_results.json"
    if not seal.is_file() or not results.is_file():
        raise CampaignContractError("baseline formal seal and baseline_results are required")
    seal_body = _read_json(seal)
    result_body = _read_json(results)
    if seal_body.get("status") != "FORMAL_RUN_SEALED":
        raise CampaignContractError("baseline formal seal status is invalid")
    if result_body.get("status") != "PASS":
        raise CampaignContractError("baseline qualification status is invalid")
    decisions = result_body.get("qa_history_decisions")
    if (
        not isinstance(decisions, list)
        or len(decisions) != len(FORMAL_HISTORIES)
        or any(
            not isinstance(decision, Mapping)
            or decision.get("contract_status") != "PASS"
            for decision in decisions
        )
    ):
        raise CampaignContractError("baseline QA qualification is invalid")
    rows = result_body.get("rows", result_body.get("results", result_body.get("blocks", []))) if isinstance(result_body, Mapping) else []
    keys = {(str(row.get("history_id")), str(row.get("method"))) for row in rows if isinstance(row, Mapping)}
    expected = {(history, method) for history in FORMAL_HISTORIES for method in BASELINE_METHODS}
    if keys != expected or len(rows) != 8:
        raise CampaignContractError("baseline coverage is not exactly B0/B1 x 4 histories")
    return {
        "schema_version": "membind.v5.baseline-reference.v1",
        "baseline_root": str(root),
        "formal_run_seal_sha256": hashlib.sha256(seal.read_bytes()).hexdigest(),
        "baseline_results_sha256": hashlib.sha256(results.read_bytes()).hexdigest(),
        "baseline_seal_identity": seal_body.get("schema_version", seal_body.get("run_id")),
        "methods": list(BASELINE_METHODS),
        "histories": list(FORMAL_HISTORIES),
        "baseline_root_mutated": False,
    }


def validate_v5_rows(rows: Iterable[Mapping[str, Any]]) -> dict[str, Any]:
    selected = [dict(row) for row in rows]
    expected = {(history, V5_METHOD) for history in FORMAL_HISTORIES}
    keys = {(str(row.get("history_id")), str(row.get("method"))) for row in selected}
    if keys != expected or len(selected) != len(expected):
        raise CampaignContractError("V5 extension coverage must be exactly four histories")
    for row in selected:
        if row.get("method") != V5_METHOD:
            raise CampaignContractError("invalid V5 method identity")
        if row.get("canonical_exact_match") is not True:
            raise CampaignContractError("V5 canonical correctness gate failed")
        start = row.get("timer_start_ns", row.get("t0_ns"))
        stop = row.get("timer_stop_ns", row.get("t_durable_complete_ns"))
        final_publication = row.get("final_publication_ns", row.get("publication_durable_ns", stop))
        if not all(isinstance(value, int) for value in (start, stop, final_publication)) or not (start <= final_publication <= stop):
            raise CampaignContractError("V5 build timer boundary invalid")
        if row.get("semantic_work_after_final_publication"):
            raise CampaignContractError("semantic work occurred after final publication")
        if not isinstance(row.get("trace_envelope_count"), int) or not isinstance(row.get("episode_count"), int) or row.get("episode_count") <= 0 or row.get("trace_envelope_count") != row.get("episode_count"):
            raise CampaignContractError("source trace envelope coverage invalid")
    return {"status": "PASS", "history_count": 4, "method": V5_METHOD, "rows": selected}


def reduce_extension(baseline_rows: Iterable[Mapping[str, Any]], v5_rows: Iterable[Mapping[str, Any]]) -> dict[str, Any]:
    baseline = [dict(row) for row in baseline_rows]
    expected_baseline = {(history, method) for history in FORMAL_HISTORIES for method in BASELINE_METHODS}
    baseline_keys = {(str(row.get("history_id")), str(row.get("method"))) for row in baseline}
    if baseline_keys != expected_baseline or len(baseline) != 8:
        raise CampaignContractError("baseline rows must remain exactly eight sealed rows")
    v5 = validate_v5_rows(v5_rows)["rows"]
    return {
        "schema_version": "membind.v5.extension-reducer.v1",
        "baseline_methods": list(BASELINE_METHODS),
        "extension_method": V5_METHOD,
        "histories": list(FORMAL_HISTORIES),
        "rows": baseline + v5,
        "baseline_unchanged": True,
    }


def validate_block_timer_and_traces(
    *,
    timer_start_ns: int,
    timer_stop_ns: int,
    final_publication_ns: int,
    source_trace_envelopes: Iterable[Mapping[str, Any]],
    episode_count: int,
    semantic_work_after_final_publication: bool = False,
) -> dict[str, Any]:
    envelopes = list(source_trace_envelopes)
    if not timer_start_ns <= final_publication_ns <= timer_stop_ns:
        raise CampaignContractError("timer boundary is not FORMAL_START -> durable completion")
    if semantic_work_after_final_publication:
        raise CampaignContractError("semantic work after final PUBLICATION_DURABLE")
    sequences = [int(envelope.get("source_sequence", -1)) for envelope in envelopes]
    if len(envelopes) != episode_count or sorted(sequences) != list(range(episode_count)) or len(set(sequences)) != episode_count:
        raise CampaignContractError("one complete trace envelope per source is required")
    for envelope in envelopes:
        phases = {str(span.get("phase")) for span in envelope.get("spans", [])}
        if not {"PREPARE", "NATIVE"} <= phases:
            raise CampaignContractError("trace envelope missing PREPARE/NATIVE")
        for span in envelope.get("spans", []):
            if span.get("phase") in {"PREPARE", "NATIVE"}:
                start = span.get("start_ns")
                end = span.get("end_ns")
                if not isinstance(start, int) or not isinstance(end, int) or not timer_start_ns <= start <= end <= timer_stop_ns:
                    raise CampaignContractError("semantic trace span outside build timer")
    return {"status": "PASS", "build_makespan_ns": timer_stop_ns - timer_start_ns, "trace_envelope_count": len(envelopes)}
