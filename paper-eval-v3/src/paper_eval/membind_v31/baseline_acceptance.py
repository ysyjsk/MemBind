"""Read-only acceptance of the exact APC-aligned development baseline.

The running APC lane is an external producer.  This module never writes into
that lane: it either returns ``NOT_TERMINAL`` for a well-formed in-progress
run, or fail-closed verifies the complete plan, lifecycle, correctness, and
Quality v1 chain before emitting an in-memory sealed acceptance document.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Mapping, Sequence
from copy import deepcopy
from pathlib import Path
from typing import Any

from paper_eval.apc_aligned_baseline import (
    APC_BASELINE_HISTORIES,
    APC_BASELINE_METHODS,
    derive_apc_aligned_performance,
    lifecycle_rows_from_events,
    summarize_direct_violations,
    verify_apc_aligned_baseline_plan,
)
from paper_eval.apc_quality_targets import (
    build_apc_quality_target_manifest,
    verify_apc_quality_target_manifest,
)
from paper_eval.artifacts import payload_sha256
from paper_eval.membind_v1.aligned_artifacts import inspect_aligned_block_artifacts
from paper_eval.quality_evaluation_v1_suite import (
    decide_u0_freeze,
    summarize_quality_v1,
)


EXPECTED_BASELINE_RUN_ID = "apc-baseline-dev-20260817-001"
EXPECTED_HISTORY_COUNTS = {
    "07741c45": 49,
    "b6019101": 49,
    "6071bd76": 46,
    "a2f3aa27": 44,
}
EXPECTED_EPISODES_PER_METHOD = 188
BLOCK_RESULT_SCHEMA = "membind.paper-eval-v3.apc-aligned-baseline-block-result.v1"
QUALITY_REPORT_SCHEMA = "membind.paper-eval-v3.quality-v1-report.v1"
ACCEPTANCE_SCHEMA = "membind.paper-eval-v3.membind-v31-baseline-acceptance.v1"
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_QUALITY_SLUG = {"U0": "u0", "A0": "a0", "P(C=2)": "pc2"}


class BaselineAcceptanceError(ValueError):
    """The baseline cannot be used by the v3.1 lane."""


def _fail(code: str) -> BaselineAcceptanceError:
    return BaselineAcceptanceError(code)


def _sha(value: object, code: str) -> str:
    if not isinstance(value, str) or _SHA256.fullmatch(value) is None:
        raise _fail(code)
    return value


def _read_json(path: Path, code: str, *, require_seal: bool = True) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        raise _fail(code) from None
    if not isinstance(value, dict):
        raise _fail(code)
    if require_seal:
        stored = _sha(value.get("payload_sha256"), f"{code} hash invalid")
        body = {key: child for key, child in value.items() if key != "payload_sha256"}
        if stored != payload_sha256(body):
            raise _fail(f"{code} hash mismatch")
    return value


def _not_terminal(*, completed: int, reason: str) -> dict[str, object]:
    # Deliberately unsealed: an in-progress observation is not an artifact and
    # must never be confused with authorization for the v3.1 live lane.
    return {
        "schema_version": ACCEPTANCE_SCHEMA,
        "status": "NOT_TERMINAL",
        "run_id": EXPECTED_BASELINE_RUN_ID,
        "completed_block_count": completed,
        "reason": reason,
    }


def _verify_plan(run_root: Path) -> dict[str, Any]:
    if not (run_root / "PLAN.json").is_file():
        raise _fail("baseline plan missing")
    raw = _read_json(run_root / "PLAN.json", "baseline plan")
    sources = raw.get("history_source_sha256s")
    if not isinstance(sources, Mapping):
        raise _fail("baseline source inventory invalid")
    # APC's original pure verifier expects insertion order, while its durable
    # writer intentionally serializes mappings with sorted keys.  Normalize
    # only this representational detail before invoking the original verifier;
    # dictionary equality and the stored canonical payload hash are unchanged.
    raw["history_source_sha256s"] = {
        history: sources.get(history) for history in APC_BASELINE_HISTORIES
    }
    try:
        plan = verify_apc_aligned_baseline_plan(raw)
    except ValueError as error:
        raise _fail(f"baseline plan invalid: {error}") from None
    if plan.get("run_id") != EXPECTED_BASELINE_RUN_ID:
        raise _fail("baseline run id invalid")
    if tuple(plan.get("methods", ())) != APC_BASELINE_METHODS:
        raise _fail("baseline method inventory invalid")
    if tuple(plan.get("histories", ())) != APC_BASELINE_HISTORIES:
        raise _fail("baseline history inventory invalid")
    sources = plan.get("history_source_sha256s")
    if not isinstance(sources, Mapping) or {
        history: len(sources.get(history, ())) for history in APC_BASELINE_HISTORIES
    } != EXPECTED_HISTORY_COUNTS:
        raise _fail("baseline source count invalid")
    blocks = plan.get("blocks")
    if not isinstance(blocks, list) or len(blocks) != 12:
        raise _fail("baseline block inventory invalid")
    expected_pairs = {
        (method, history)
        for history in APC_BASELINE_HISTORIES
        for method in APC_BASELINE_METHODS
    }
    pairs = {(row.get("method"), row.get("history_id")) for row in blocks}
    namespaces = [row.get("namespace") for row in blocks]
    if pairs != expected_pairs or len(namespaces) != len(set(namespaces)):
        raise _fail("baseline fresh namespace inventory invalid")
    return plan


def _block_result(
    *, run_root: Path, plan: Mapping[str, Any], block_index: int
) -> dict[str, Any] | None:
    block = plan["blocks"][block_index]
    block_root = run_root / "blocks" / f"block-{block_index:02d}"
    result_path = block_root / "APC_ALIGNED_BLOCK_RESULT.json"
    if not block_root.exists():
        return None
    # A block without its immutable terminal result is owned by the running
    # producer.  Do not race its append-only events/checkpoint pair: report it
    # as pending and inspect the chain only after the result publication point.
    if not result_path.is_file():
        return None
    required = (block_root / "manifest.json", block_root / "events.jsonl", block_root / "checkpoint.json")
    if not all(path.is_file() for path in required):
        raise _fail(f"baseline block {block_index} terminal artifact incomplete")
    try:
        inspected = inspect_aligned_block_artifacts(block_root)
    except ValueError as error:
        raise _fail(f"baseline block {block_index} artifact invalid: {error}") from None
    checkpoint = inspected["checkpoint"]
    if checkpoint.get("terminal_status") == "INCOMPLETE_NON_MERGEABLE":
        raise _fail(f"baseline block {block_index} terminal failure")
    if (
        checkpoint.get("terminal_status") != "COMPLETED"
        or checkpoint.get("complete_coverage") is not True
        or checkpoint.get("completed_source_prefix") != block["source_count"] - 1
    ):
        raise _fail(f"baseline block {block_index} terminal coverage invalid")
    result = _read_json(result_path, f"baseline block {block_index} result")
    expected_identity = {
        "schema_version": BLOCK_RESULT_SCHEMA,
        "status": "PASS",
        "run_id": EXPECTED_BASELINE_RUN_ID,
        "block_index": block_index,
        "method": block["method"],
        "history_id": block["history_id"],
        "namespace": block["namespace"],
        "episode_count": block["source_count"],
        "plan_payload_sha256": plan["payload_sha256"],
    }
    if any(result.get(key) != expected for key, expected in expected_identity.items()):
        raise _fail(f"baseline block {block_index} result binding invalid")
    manifest = inspected["manifest"]
    manifest_identity = {
        "aligned_run_id": EXPECTED_BASELINE_RUN_ID,
        "block_index": block_index,
        "method": block["method"],
        "history_id": block["history_id"],
        "namespace": block["namespace"],
        "source_sha256s": plan["history_source_sha256s"][block["history_id"]],
        "source_manifest_sha256": block["source_manifest_sha256"],
        "arrival_trace_sha256": block["arrival_trace_sha256"],
        "history_arrival_trace_sha256": block["history_arrival_trace_sha256"],
        "shared_execution_envelope_sha256": block["shared_execution_envelope_sha256"],
        "global_llm_admission_k": block["global_llm_admission_k"],
        "plan_payload_sha256": plan["payload_sha256"],
        "plan_block_sha256": payload_sha256(block),
    }
    if any(manifest.get(key) != expected for key, expected in manifest_identity.items()):
        raise _fail(f"baseline block {block_index} manifest binding invalid")
    live = result.get("live")
    if not isinstance(live, Mapping) or live.get("status") != "PASS":
        raise _fail(f"baseline block {block_index} runtime binding invalid")
    live_bindings = {
        "block_index": block_index,
        "method": block["method"],
        "history_id": block["history_id"],
        "namespace": block["namespace"],
        "source_count": block["source_count"],
        "source_manifest_sha256": block["source_manifest_sha256"],
        "arrival_trace_sha256": block["arrival_trace_sha256"],
        "history_arrival_trace_sha256": block["history_arrival_trace_sha256"],
        "shared_execution_envelope_sha256": block["shared_execution_envelope_sha256"],
    }
    for key, expected in live_bindings.items():
        if live.get(key) != expected:
            label = "arrival trace" if "arrival_trace" in key else "runtime"
            raise _fail(f"baseline block {block_index} {label} binding invalid")
    if live.get("global_llm_admission_k") != plan["global_llm_admission_k"]:
        raise _fail(f"baseline block {block_index} global LLM admission binding invalid")
    if live.get("execution_identity_sha256") != manifest.get("execution_identity_sha256"):
        raise _fail(f"baseline block {block_index} runtime identity binding invalid")
    initial = live.get("initial_namespace")
    if not isinstance(initial, Mapping) or dict(initial) != {
        "node_count": 0,
        "relationship_count": 0,
        "episode_names": [],
    }:
        raise _fail(f"baseline block {block_index} fresh namespace invalid")
    cache = result.get("cache_isolation")
    if (
        not isinstance(cache, Mapping)
        or cache.get("mechanism") != "REQUEST_CACHE_SALT"
        or cache.get("cache_salt_sha256") != block["cache_salt_sha256"]
        or cache.get("cross_block_prefix_identity_reuse") is not False
    ):
        raise _fail(f"baseline block {block_index} cache isolation binding invalid")

    lifecycle = lifecycle_rows_from_events(
        inspected["events"], method=block["method"], source_count=block["source_count"]
    )
    expected_performance = derive_apc_aligned_performance(lifecycle)
    if result.get("performance") != expected_performance:
        raise _fail(f"baseline block {block_index} lifecycle performance drift")
    publications = [
        event for event in inspected["events"] if event.get("event_type") == "PUBLICATION_DURABLE"
    ]
    visibility: dict[int, bool] = {}
    for event in publications:
        telemetry = event.get("telemetry")
        source = event.get("source_sequence")
        if (
            isinstance(source, bool)
            or not isinstance(source, int)
            or not isinstance(telemetry, Mapping)
            or not isinstance(telemetry.get("visibility_confirmed"), bool)
            or source in visibility
        ):
            raise _fail(f"baseline block {block_index} correctness visibility invalid")
        visibility[source] = bool(telemetry["visibility_confirmed"])
    correctness = result.get("correctness")
    if not isinstance(correctness, Mapping) or correctness.get("checker_status") != "MEASURED":
        raise _fail(f"baseline block {block_index} correctness not MEASURED")
    graph_counts = correctness.get("graph_observation_counts")
    try:
        expected_correctness = summarize_direct_violations(
            expected_source_count=block["source_count"],
            publication_source_sequences=[int(event["source_sequence"]) for event in publications],
            visibility_by_source=visibility,
            graph_counts=graph_counts,
        )
    except ValueError as error:
        raise _fail(f"baseline block {block_index} correctness invalid: {error}") from None
    if dict(correctness) != expected_correctness:
        raise _fail(f"baseline block {block_index} correctness drift")
    return result


def _verify_phase(run_root: Path) -> dict[str, Any] | None:
    path = run_root / "PHASE_RESULT.json"
    if not path.is_file():
        return None
    phase = _read_json(path, "baseline phase result")
    if (
        phase.get("status") != "PASS"
        or phase.get("phase") != "full"
        or phase.get("run_id") != EXPECTED_BASELINE_RUN_ID
        or phase.get("completed_block_indices") != list(range(12))
    ):
        raise _fail("baseline phase terminal result invalid")
    return phase


def _verify_quality(
    *, run_root: Path, quality_root: Path, results: Sequence[Mapping[str, object]]
) -> dict[str, Any] | None:
    targets_path = run_root / "QUALITY_TARGETS.json"
    report_path = quality_root / "QUALITY_EVALUATION_V1_RESULTS.json"
    if not targets_path.is_file() or not report_path.is_file():
        return None
    targets_raw = _read_json(targets_path, "quality target manifest")
    try:
        targets = verify_apc_quality_target_manifest(targets_raw)
    except ValueError as error:
        raise _fail(f"quality target manifest invalid: {error}") from None
    expected_targets = build_apc_quality_target_manifest(
        run_id=EXPECTED_BASELINE_RUN_ID, block_results=results
    )
    if targets != expected_targets:
        raise _fail("quality target construction binding invalid")
    report = _read_json(report_path, "quality report")
    if (
        report.get("schema_version") != QUALITY_REPORT_SCHEMA
        or report.get("status") != "PASS"
        or report.get("construction_rerun") is not False
        or report.get("construction_latency_includes_quality") is not False
    ):
        raise _fail("quality report terminal status invalid")
    quality_run_id = report.get("run_id")
    if not isinstance(quality_run_id, str) or quality_root.name != quality_run_id:
        raise _fail("quality run id binding invalid")
    quality_identity = report.get("quality_identity")
    runtime_identity = report.get("runtime_identity")
    if not isinstance(quality_identity, Mapping) or not isinstance(runtime_identity, Mapping):
        raise _fail("quality runtime identity invalid")
    runtime_sha = payload_sha256(runtime_identity)
    rows: list[dict[str, Any]] = []
    for target in targets["targets"]:
        slug = _QUALITY_SLUG[target["method"]]
        unit_root = quality_root / "units" / slug / target["history_id"]
        public_paths = sorted(unit_root.glob("attempt-*/public.json"))
        if len(public_paths) != 1:
            raise _fail("quality terminal unit coverage invalid")
        public = _read_json(public_paths[0], "quality public unit")
        expected = {
            "overlay_run_id": quality_run_id,
            "method": target["method"],
            "history_id": target["history_id"],
            "namespace_sha256": hashlib.sha256(
                str(target["namespace"]).encode("utf-8")
            ).hexdigest(),
            "construction_result_sha256": target["construction_result_sha256"],
            "runtime_identity_sha256": runtime_sha,
            "quality_identity": quality_identity,
        }
        for key, wanted in expected.items():
            if public.get(key) != wanted:
                label = (
                    "quality construction binding"
                    if key in {"namespace_sha256", "construction_result_sha256"}
                    else "quality identity binding"
                )
                raise _fail(f"{label} invalid")
        if public.get("runtime_identity") != runtime_identity:
            raise _fail("quality runtime identity binding invalid")
        rows.append(public)
    try:
        expected_summary = summarize_quality_v1(rows)
        expected_u0 = decide_u0_freeze(rows[:4])
    except ValueError as error:
        raise _fail(f"quality result reduction invalid: {error}") from None
    if report.get("summary") != expected_summary:
        raise _fail("quality summary drift")
    u0 = report.get("u0_decision")
    if not isinstance(u0, Mapping) or any(
        u0.get(key) != value for key, value in expected_u0.items()
    ) or u0.get("decision") != "FREEZE_QUALITY_EVALUATION_V1":
        raise _fail("quality U0 decision invalid")
    return report


def verify_apc_baseline_acceptance(
    run_root: Path, *, quality_root: Path | None
) -> dict[str, Any]:
    """Verify the exact baseline without mutating or sealing running state."""

    root = Path(run_root)
    if root.name != EXPECTED_BASELINE_RUN_ID:
        raise _fail("baseline run directory identity invalid")
    plan = _verify_plan(root)
    if (root / "FAILURE.json").exists() or (root / "DISPOSITION.json").exists():
        raise _fail("baseline failure or disposition artifact present")
    preflight_path = root / "PREFLIGHT.json"
    if not preflight_path.is_file():
        return _not_terminal(completed=0, reason="PREFLIGHT_PENDING")
    preflight = _read_json(preflight_path, "baseline preflight")
    if preflight.get("status") != "PASS":
        raise _fail("baseline preflight invalid")
    execution_identity = _sha(
        preflight.get("execution_identity_sha256"), "baseline runtime identity invalid"
    )

    results: list[dict[str, Any]] = []
    for block_index in range(12):
        result = _block_result(run_root=root, plan=plan, block_index=block_index)
        if result is None:
            return _not_terminal(
                completed=len(results), reason=f"BLOCK_{block_index:02d}_PENDING"
            )
        live = result["live"]
        if live.get("execution_identity_sha256") != execution_identity:
            raise _fail(f"baseline block {block_index} runtime identity drift")
        results.append(result)
    if _verify_phase(root) is None:
        return _not_terminal(completed=12, reason="PHASE_RESULT_PENDING")
    if quality_root is None:
        return _not_terminal(completed=12, reason="QUALITY_PENDING")
    quality = _verify_quality(run_root=root, quality_root=Path(quality_root), results=results)
    if quality is None:
        return _not_terminal(completed=12, reason="QUALITY_PENDING")

    totals = {
        method: sum(
            int(result["episode_count"])
            for result in results
            if result["method"] == method
        )
        for method in APC_BASELINE_METHODS
    }
    if set(totals.values()) != {EXPECTED_EPISODES_PER_METHOD}:
        raise _fail("baseline terminal episode coverage invalid")
    semantic_verdicts = {
        method: {
            "direct_violations": sum(
                int(result["correctness"]["direct_violations_total"])
                for result in results
                if result["method"] == method
            ),
        }
        for method in APC_BASELINE_METHODS
    }
    for value in semantic_verdicts.values():
        value["semantic_status"] = (
            "SAFE" if value["direct_violations"] == 0 else "VIOLATION_OBSERVED"
        )
    body: dict[str, Any] = {
        "schema_version": ACCEPTANCE_SCHEMA,
        "status": "PASS",
        "artifact_status": "SEALED_VALID",
        "semantic_verdicts": semantic_verdicts,
        "run_id": EXPECTED_BASELINE_RUN_ID,
        "completed_block_count": 12,
        "terminal_episode_count_per_method": EXPECTED_EPISODES_PER_METHOD,
        "plan_payload_sha256": plan["payload_sha256"],
        "source_manifest_sha256": plan["source_manifest_sha256"],
        "arrival_trace_sha256": plan["arrival_trace_sha256"],
        "shared_execution_envelope_sha256": plan["shared_execution_envelope_sha256"],
        "global_llm_admission_k": plan["global_llm_admission_k"],
        "execution_identity_sha256": execution_identity,
        "block_result_payload_sha256s": [result["payload_sha256"] for result in results],
        "quality_run_id": quality["run_id"],
        "quality_report_payload_sha256": quality["payload_sha256"],
        "quality_identity_sha256": payload_sha256(quality["quality_identity"]),
        "quality_runtime_identity_sha256": payload_sha256(quality["runtime_identity"]),
    }
    body["payload_sha256"] = payload_sha256(body)
    return body


__all__ = [
    "ACCEPTANCE_SCHEMA",
    "BaselineAcceptanceError",
    "EXPECTED_BASELINE_RUN_ID",
    "EXPECTED_EPISODES_PER_METHOD",
    "EXPECTED_HISTORY_COUNTS",
    "verify_apc_baseline_acceptance",
]
