"""Production callback for the bounded v4 c01 development prefixes.

The callback is deliberately separate from :mod:`runner`: ``runner`` owns the
append-only candidate ledger, while this module owns the sealed v3.1 source
inventory and live block composition.  Constructing the callback is offline;
environment, episode, and State-Cut inputs are loaded only when a READY live
candidate invokes it.
"""

from __future__ import annotations

import asyncio
import hashlib
import inspect
import json
import math
from collections.abc import Callable, Mapping, Sequence
from copy import deepcopy
from pathlib import Path
from typing import Any

from paper_eval.artifacts import payload_sha256, sha256_file
from paper_eval.apc_aligned_baseline import (
    APC_BASELINE_HISTORIES,
    build_apc_aligned_baseline_plan,
)
from paper_eval.membind_v31.certification import StateCutCertification
from paper_eval.membind_v31.live_block import V31LiveHooks
from paper_eval.membind_v31.method_plan import (
    build_membind_v31_live_plan,
    verify_membind_v31_method_plan,
)
from paper_eval.membind_v31.production_executor import ProductionExecutorPaths
from paper_eval.membind_v4.autoresearch import (
    CandidateStore,
    assess_candidate,
    candidate_config,
)
from paper_eval.membind_v4.live_block import (
    V4ProductionLoaders,
    execute_v4_live_block,
    production_v4_loaders,
)


CANDIDATE_HISTORY_ID = "07741c45"
CANDIDATE_SOURCE_COUNTS = (6, 12, 20)
A1_PROTOCOL_AMENDMENT_ID = "A1"
A1_SOURCE_COUNT = 20


class V4ProductionRunnerError(ValueError):
    """A live candidate source, plan, or result drifted from preregistration."""


def _fail(code: str) -> V4ProductionRunnerError:
    return V4ProductionRunnerError(code)


def _read_prior_sealed(path: Path, label: str, schema: str) -> dict[str, Any]:
    try:
        value = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise _fail(f"prior_six_{label}_unreadable") from error
    if not isinstance(value, dict):
        raise _fail(f"prior_six_{label}_invalid")
    body = dict(value)
    digest = body.pop("payload_sha256", None)
    if not isinstance(digest, str) or digest != payload_sha256(body):
        raise _fail(f"prior_six_{label}_payload_hash_mismatch")
    if body.get("schema_version") != schema:
        raise _fail(f"prior_six_{label}_schema_invalid")
    body["payload_sha256"] = digest
    return body


def _read_a1_sealed(path: Path, label: str) -> dict[str, Any]:
    """Read an A1 sidecar without trusting any value outside its seal.

    A1 is deliberately a sidecar amendment.  Keeping this reader separate
    from the prior-six reader prevents an A1 artifact from being accepted as
    evidence for the original six-to-twelve protocol.
    """

    try:
        value = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise _fail(f"a1_{label}_unreadable") from error
    if not isinstance(value, dict):
        raise _fail(f"a1_{label}_invalid")
    body = dict(value)
    digest = body.pop("payload_sha256", None)
    if not isinstance(digest, str) or digest != payload_sha256(body):
        raise _fail(f"a1_{label}_payload_hash_mismatch")
    expected_schema = {
        "audit": "membind.paper-eval-v4.a1-opportunity-audit.v1",
        "amendment": "membind.paper-eval-v4.a1-protocol-amendment.v1",
    }[label]
    if body.get("schema_version") != expected_schema:
        raise _fail(f"a1_{label}_schema_invalid")
    body["payload_sha256"] = digest
    return body


def _a1_value(body: Mapping[str, object], *names: str) -> object:
    """Return the first present value from a small, documented alias set."""

    for name in names:
        if name in body:
            return body[name]
    # The amendment JSON keeps immutable references under ``sealed_reference``
    # to distinguish them from the prose/decision fields.  Read only this
    # explicitly named container (and the equivalent ``identity`` container),
    # never arbitrary user-controlled descendants.
    for container_name in ("sealed_reference", "identity", "bindings"):
        nested = body.get(container_name)
        if isinstance(nested, Mapping):
            for name in names:
                if name in nested:
                    return nested[name]
    return None


def _a1_bound_values(body: Mapping[str, object], *names: str) -> tuple[object, ...]:
    """Collect duplicate identity fields so conflicting copies cannot hide."""

    values: list[object] = []
    for name in names:
        if name in body:
            values.append(body[name])
    for container_name in ("sealed_reference", "identity", "bindings"):
        nested = body.get(container_name)
        if isinstance(nested, Mapping):
            for name in names:
                if name in nested:
                    values.append(nested[name])
    return tuple(values)


def _a1_count(body: Mapping[str, object], *names: str) -> object:
    value = _a1_value(body, *names)
    if value is not None:
        return value
    nested = body.get("opportunity_counts")
    if isinstance(nested, Mapping):
        for name in names:
            if name in nested:
                return nested[name]
    prefixes = body.get("prefix_opportunity_counts")
    if isinstance(prefixes, Mapping):
        for name in names:
            if name in prefixes:
                return prefixes[name]
    # Generated audit revisions may keep each prefix as an object carrying a
    # named ``potential_opportunity_count``.  Walk only mappings (never raw
    # strings/lists) to remain deterministic while accepting that stable
    # presentation shape.
    wanted = set(names)
    def walk(value: object) -> object:
        if isinstance(value, Mapping):
            for key, item in value.items():
                if key in wanted:
                    if isinstance(item, Mapping):
                        for count_key in (
                            "potential_opportunity_count",
                            "opportunity_count",
                            "count",
                        ):
                            if count_key in item:
                                return item[count_key]
                    else:
                        return item
                found = walk(item)
                if found is not None:
                    return found
        return None
    return walk(body)


def _a1_linear_percentile(values: Sequence[int], probability: float) -> float:
    if not values:
        raise _fail("a1_measurement_reference_invalid")
    ordered = sorted(values)
    index = (len(ordered) - 1) * probability
    lower = math.floor(index)
    upper = math.ceil(index)
    if lower == upper:
        return float(ordered[lower])
    weight = index - lower
    return float(ordered[lower] + (ordered[upper] - ordered[lower]) * weight)


def _a1_measurement_reference(audit: Mapping[str, object]) -> dict[str, object] | None:
    """Verify the optional sealed backend reference added by A1 TDD.

    Minimal synthetic sidecars used to test plan admission predate these raw
    measurements, so absence is represented explicitly.  A real 20-source
    live result requires the returned reference and fails closed otherwise.
    """

    development = audit.get("development_reference_0_19")
    if not isinstance(development, Mapping):
        return None
    rows = development.get("source_rows")
    llm = development.get("llm_reference")
    if not isinstance(rows, Sequence) or isinstance(rows, (str, bytes)) or not isinstance(llm, Mapping):
        return None
    service: list[int] = []
    for row in rows:
        value = row.get("service_latency_ns") if isinstance(row, Mapping) else None
        if isinstance(value, bool) or not isinstance(value, int) or value < 0:
            raise _fail("a1_measurement_reference_invalid")
        service.append(value)
    if len(service) != A1_SOURCE_COUNT:
        raise _fail("a1_measurement_reference_invalid")
    frontier_p95 = _a1_linear_percentile(service, 0.95)
    if development.get("frontier_p95_service_ns") != frontier_p95:
        raise _fail("a1_measurement_reference_invalid")
    makespan = development.get("makespan_ns")
    freshness_p95 = development.get("freshness_ns_p95")
    token_count = llm.get("useful_token_count")
    throughput = llm.get("useful_token_throughput_tokens_per_second")
    llm_path = llm.get("absolute_path")
    llm_hash = llm.get("file_sha256")
    if (
        isinstance(makespan, bool)
        or not isinstance(makespan, int)
        or makespan <= 0
        or isinstance(freshness_p95, bool)
        or not isinstance(freshness_p95, (int, float))
        or float(freshness_p95) <= 0
        or isinstance(token_count, bool)
        or not isinstance(token_count, int)
        or token_count <= 0
        or isinstance(throughput, bool)
        or not isinstance(throughput, (int, float))
        or not math.isfinite(float(throughput))
        or float(throughput) <= 0
        or not isinstance(llm_path, str)
        or not isinstance(llm_hash, str)
    ):
        raise _fail("a1_measurement_reference_invalid")
    if not math.isclose(
        float(throughput), token_count / (makespan / 1e9), rel_tol=1e-12
    ):
        raise _fail("a1_measurement_reference_invalid")
    if sha256_file(Path(llm_path)) != llm_hash:
        raise _fail("a1_measurement_reference_trace_drift")
    return {
        "frontier_p95_service_ns": frontier_p95,
        "useful_token_throughput_tokens_per_second": float(throughput),
        "makespan_ns": makespan,
        "freshness_ns_p95": float(freshness_p95),
        "llm_trace_absolute_path": str(Path(llm_path).resolve()),
        "llm_trace_file_sha256": llm_hash,
    }


def verify_a1_protocol_amendment(
    audit_path: Path,
    amendment_path: Path,
    *,
    canonical_plan: Mapping[str, object] | None = None,
    history_id: str = CANDIDATE_HISTORY_ID,
) -> dict[str, object]:
    """Verify the sealed A1 opportunity audit and amendment sidecar.

    The verifier intentionally accepts only the one registered 20-source
    prefix.  It is not a generic source-window mechanism: any missing A1
    identity or mismatch with the sealed canonical plan fails closed before a
    candidate namespace can be created.
    """

    audit = _read_a1_sealed(Path(audit_path), "audit")
    amendment = _read_a1_sealed(Path(amendment_path), "amendment")

    def identity(body: Mapping[str, object], label: str) -> None:
        amendment_id = _a1_value(
            body,
            "protocol_amendment_id",
            "amendment_id",
            "amendment",
        )
        if amendment_id != A1_PROTOCOL_AMENDMENT_ID:
            raise _fail(f"a1_{label}_identity_drift")
        selected_history = _a1_value(body, "history_id", "representative_history_id")
        if selected_history != history_id:
            raise _fail(f"a1_{label}_history_drift")
        selected_count = _a1_value(
            body,
            "development_source_count",
            "source_count",
            "prefix_source_count",
        )
        # The audit describes the complete 49-source input in some generated
        # revisions, while the amendment describes the authorized 20-source
        # development prefix.  Accept only those two explicitly registered
        # cardinalities (never an arbitrary source window).
        if label == "audit":
            if selected_count not in {49, A1_SOURCE_COUNT}:
                raise _fail(f"a1_{label}_source_count_invalid")
        elif selected_count != A1_SOURCE_COUNT:
            raise _fail(f"a1_{label}_source_count_invalid")

    identity(audit, "audit")
    identity(amendment, "amendment")

    # The audit is useful only if it proves the advertised exposure.  Support
    # the flat and nested spellings used by the generated JSON artifact, but
    # never silently accept a missing count.
    expected_counts = (
        (0, ("sources_0_5", "prefix_0_5", "source_0_5", "0..5"), 0),
        (0, ("sources_0_11", "prefix_0_11", "source_0_11", "0..11"), 0),
        (0, ("sources_0_19", "prefix_0_19", "source_0_19", "0..19"), 7),
        (0, ("full_49", "full_workload", "full_source_count_49", "full"), 22),
    )
    for _unused, names, expected in expected_counts:
        value = _a1_count(audit, *names)
        if value != expected:
            raise _fail("a1_audit_opportunity_counts_invalid")
    first = _a1_value(
        audit,
        "first_opportunity_source",
        "first_potential_opportunity_source",
    )
    if first != 12:
        raise _fail("a1_audit_first_opportunity_invalid")

    # Bind to the same history trace and source inventory as the production
    # plan.  These fields are mandatory in the amendment/audit contract; the
    # aliases accommodate the human-readable sidecar's stable JSON names.
    audit_trace = _a1_value(
        audit,
        "arrival_trace_sha256",
        "arrival_trace_identity",
        "history_arrival_trace_sha256",
    )
    amendment_trace = _a1_value(
        amendment,
        "arrival_trace_sha256",
        "arrival_trace_identity",
        "history_arrival_trace_sha256",
    )
    for label, body, names in (
        (
            "audit",
            audit,
            ("arrival_trace_sha256", "arrival_trace_identity", "history_arrival_trace_sha256"),
        ),
        (
            "amendment",
            amendment,
            ("arrival_trace_sha256", "arrival_trace_identity", "history_arrival_trace_sha256"),
        ),
    ):
        bound = _a1_bound_values(body, *names)
        if bound and len({value for value in bound if isinstance(value, str)}) > 1:
            raise _fail(f"a1_{label}_arrival_trace_identity_drift")
    if not isinstance(audit_trace, str) or not isinstance(amendment_trace, str):
        raise _fail("a1_arrival_trace_identity_missing")
    if audit_trace != amendment_trace:
        raise _fail("a1_arrival_trace_identity_drift")
    audit_inventory = _a1_value(
        audit,
        "source_inventory_sha256",
        "source_manifest_sha256",
        "source_inventory_identity",
    )
    amendment_inventory = _a1_value(
        amendment,
        "source_inventory_sha256",
        "source_manifest_sha256",
        "source_inventory_identity",
    )
    for label, body in (("audit", audit), ("amendment", amendment)):
        bound = _a1_bound_values(
            body,
            "source_inventory_sha256",
            "source_manifest_sha256",
            "source_inventory_identity",
        )
        if bound and len({value for value in bound if isinstance(value, str)}) > 1:
            raise _fail(f"a1_{label}_source_inventory_identity_drift")
    if not isinstance(audit_inventory, str) or not isinstance(amendment_inventory, str):
        raise _fail("a1_source_inventory_identity_missing")
    if audit_inventory != amendment_inventory:
        raise _fail("a1_source_inventory_identity_drift")
    audit_binding = amendment.get("audit_binding")
    if isinstance(audit_binding, Mapping):
        if audit_binding.get("payload_sha256") != audit["payload_sha256"]:
            raise _fail("a1_audit_binding_drift")
        if audit_binding.get("file_sha256") != sha256_file(Path(audit_path)):
            raise _fail("a1_audit_binding_drift")
    measurement_reference = _a1_measurement_reference(audit)

    audit_execution = _a1_value(
        audit,
        "execution_identity_sha256",
        "execution_identity",
    )
    amendment_execution = _a1_value(
        amendment,
        "execution_identity_sha256",
        "execution_identity",
    )
    audit_shared_execution = _a1_value(
        audit,
        "shared_execution_envelope_sha256",
        "execution_envelope_sha256",
    )
    amendment_shared_execution = _a1_value(
        amendment,
        "shared_execution_envelope_sha256",
        "execution_envelope_sha256",
    )
    for label, body in (("audit", audit), ("amendment", amendment)):
        # ``execution_identity`` and ``shared_execution_envelope`` are two
        # distinct bindings when both are present; only reject duplicate
        # spellings within each identity family.
        identity_values = _a1_bound_values(
            body, "execution_identity_sha256", "execution_identity"
        )
        envelope_values = _a1_bound_values(
            body, "shared_execution_envelope_sha256", "execution_envelope_sha256"
        )
        if (
            any(not isinstance(value, str) for value in identity_values)
            or any(not isinstance(value, str) for value in envelope_values)
            or len(set(identity_values)) > 1
            or len(set(envelope_values)) > 1
        ):
            raise _fail(f"a1_{label}_execution_identity_drift")
    if audit_execution is not None or amendment_execution is not None:
        if not isinstance(audit_execution, str) or not isinstance(amendment_execution, str):
            raise _fail("a1_execution_identity_missing")
        if audit_execution != amendment_execution:
            raise _fail("a1_execution_identity_drift")
    if audit_shared_execution is not None or amendment_shared_execution is not None:
        if not isinstance(audit_shared_execution, str) or not isinstance(amendment_shared_execution, str):
            raise _fail("a1_execution_envelope_identity_missing")
        if audit_shared_execution != amendment_shared_execution:
            raise _fail("a1_execution_envelope_identity_drift")

    if canonical_plan is not None:
        try:
            canonical = verify_membind_v31_method_plan(canonical_plan)
        except ValueError:
            raise _fail("canonical_plan_invalid") from None
        canonical_trace = canonical["arrival_traces"][history_id][
            "history_arrival_trace_sha256"
        ]
        canonical_traces = {canonical_trace}
        if isinstance(canonical.get("arrival_trace_sha256"), str):
            canonical_traces.add(canonical["arrival_trace_sha256"])
        if audit_trace not in canonical_traces:
            raise _fail("a1_arrival_trace_identity_drift")
        # The complete source inventory is represented by the canonical
        # source-manifest hash.  Older plans use ``source_manifest_sha256``;
        # permit the per-history inventory hash when that is what the audit
        # sealed, but always require an equality check when available.
        canonical_inventory = canonical.get("source_manifest_sha256")
        if isinstance(canonical_inventory, str) and audit_inventory != canonical_inventory:
            raise _fail("a1_source_inventory_identity_drift")
        canonical_execution = canonical.get("shared_execution_envelope_sha256")
        if audit_shared_execution is not None and isinstance(canonical_execution, str):
            if audit_shared_execution != canonical_execution:
                raise _fail("a1_execution_envelope_identity_drift")
        invariants = amendment.get("policy_invariants")
        if isinstance(invariants, Mapping):
            expected_invariants = {
                "candidate_id": "c01",
                "policy": "IDLE_SLOT_VALIDATED_SPEC",
                "compile_workers": canonical.get("compile_workers"),
                "lookahead": canonical.get("lookahead"),
                "global_llm_admission_k": canonical.get("global_llm_admission_k"),
                "speculation_distance": 1,
                "prompt_schema_model_backend_unchanged": True,
                "node_resolve_semantics_unchanged": True,
                "publication_order_unchanged": True,
            }
            for field, expected in expected_invariants.items():
                if field in invariants and invariants[field] != expected:
                    raise _fail("a1_policy_invariant_drift")
        prefix = amendment.get("source_prefix")
        if prefix is not None and prefix != "0..19":
            raise _fail("a1_source_prefix_invalid")

    return {
        "protocol_amendment_id": A1_PROTOCOL_AMENDMENT_ID,
        "history_id": history_id,
        "source_count": A1_SOURCE_COUNT,
        "audit_absolute_path": str(Path(audit_path).resolve()),
        "audit_file_sha256": sha256_file(Path(audit_path)),
        "audit_payload_sha256": audit["payload_sha256"],
        "amendment_absolute_path": str(Path(amendment_path).resolve()),
        "amendment_file_sha256": sha256_file(Path(amendment_path)),
        "amendment_payload_sha256": amendment["payload_sha256"],
        "arrival_trace_sha256": audit_trace,
        "source_inventory_sha256": audit_inventory,
        "execution_identity": audit_execution,
        "shared_execution_envelope_sha256": audit_shared_execution,
        "measurement_reference": deepcopy(measurement_reference),
    }


# Concise public alias used by offline qualification callers.
verify_a1_amendment = verify_a1_protocol_amendment


def verify_prior_six_reduction(
    reduction_path: Path,
    *,
    candidate_id: str,
    history_id: str,
) -> dict[str, object]:
    """Verify the sealed six-source admission proof for a 12-source run."""

    path = Path(reduction_path)
    root = path.parent
    reduction = _read_prior_sealed(
        path,
        "reduction",
        "membind.paper-eval-v4.candidate-reduction.v1",
    )
    candidate = _read_prior_sealed(
        root / "candidate.json",
        "candidate",
        "membind.paper-eval-v4.candidate.v1",
    )
    summary = _read_prior_sealed(
        root / "summary.json",
        "summary",
        "membind.paper-eval-v4.summary.v1",
    )
    if (
        candidate.get("candidate_id") != candidate_id
        or summary.get("candidate_id") != candidate_id
        or reduction.get("candidate_id") != candidate_id
        or candidate.get("source_count") != 6
        or summary.get("source_count") != 6
        or reduction.get("source_count") != 6
    ):
        raise _fail("prior_six_candidate_identity_drift")
    if candidate.get("status") != "COMPLETED":
        raise _fail("prior_six_candidate_not_completed")
    config = candidate_config(candidate_id)
    if any(
        candidate.get(field) != config.get(field)
        for field in ("policy", "global_k", "speculation_distance", "phase_complementary")
    ):
        raise _fail("prior_six_policy_drift")
    if summary.get("history_id") != history_id:
        raise _fail("prior_six_history_drift")
    if (
        summary.get("status") != "PASS"
        or summary.get("runner_mode") != "live"
        or reduction.get("status") != "PASS"
    ):
        raise _fail("prior_six_status_invalid")
    decision = reduction.get("decision")
    if not isinstance(decision, Mapping) or decision.get("decision") != "EXTEND_TO_12":
        raise _fail("prior_six_decision_invalid")
    mechanism = reduction.get("mechanism")
    performance = reduction.get("performance")
    if not isinstance(mechanism, Mapping) or not isinstance(performance, Mapping):
        raise _fail("prior_six_evidence_invalid")
    mechanism_fields = (
        "qualified_node_resolve_count",
        "speculation_launch_count",
        "exact_validation_completed_count",
        "semantic_hit_count",
        "semantic_miss_count",
        "overlap_count",
        "hidden_critical_time_ns",
        "direct_violation_count",
    )
    if any(mechanism.get(field, 0) != summary.get(field, 0) for field in mechanism_fields):
        raise _fail("prior_six_mechanism_evidence_drift")
    if "freshness_p95_ratio" not in performance or "makespan_ratio" not in performance:
        raise _fail("prior_six_performance_evidence_invalid")
    recomputed = assess_candidate(
        {
            **dict(summary),
            **{field: mechanism.get(field, 0) for field in mechanism_fields},
            "freshness_p95_ratio": performance.get("freshness_p95_ratio"),
            "makespan_ratio": performance.get("makespan_ratio"),
        }
    )
    if recomputed != dict(decision):
        raise _fail("prior_six_decision_drift")
    return {
        "candidate_id": candidate_id,
        "history_id": history_id,
        "source_count": 6,
        "decision": "EXTEND_TO_12",
        "absolute_path": str(path.resolve()),
        "file_sha256": sha256_file(path),
        "payload_sha256": reduction["payload_sha256"],
        "candidate_payload_sha256": candidate["payload_sha256"],
        "summary_payload_sha256": summary["payload_sha256"],
    }


def build_v4_candidate_plan(
    canonical_plan: Mapping[str, object],
    *,
    candidate_id: str,
    source_count: int,
    candidate_root: Path,
    protocol_amendment: str | None = None,
    a1_audit_path: Path | None = None,
    a1_amendment_path: Path | None = None,
    # Short aliases make the sidecar contract convenient for offline callers
    # while retaining the explicit A1 names above for the CLI/runner.
    audit_path: Path | None = None,
    amendment_path: Path | None = None,
) -> dict[str, object]:
    """Derive a fresh verified v3.1 plan for the registered c01 prefixes.

    The 20-source prefix exists only under protocol amendment A1 and must be
    accompanied by both sealed sidecars.  The original six/twelve paths do
    not consult A1, preserving their sealed identity and admission rules.
    """

    if candidate_id != "c01":
        raise _fail("candidate_policy_not_implemented")
    if (
        isinstance(source_count, bool)
        or not isinstance(source_count, int)
        or source_count not in CANDIDATE_SOURCE_COUNTS
    ):
        raise _fail("candidate_source_count_invalid")
    selected_audit = a1_audit_path if a1_audit_path is not None else audit_path
    selected_amendment = (
        a1_amendment_path if a1_amendment_path is not None else amendment_path
    )
    if source_count == A1_SOURCE_COUNT:
        if protocol_amendment != A1_PROTOCOL_AMENDMENT_ID:
            raise _fail("a1_protocol_amendment_required")
        if selected_audit is None or selected_amendment is None:
            raise _fail("a1_audit_amendment_required")
    elif protocol_amendment is not None or selected_audit is not None or selected_amendment is not None:
        raise _fail("a1_protocol_amendment_unexpected")
    try:
        canonical = verify_membind_v31_method_plan(canonical_plan)
    except ValueError:
        raise _fail("canonical_plan_invalid") from None
    a1_binding: dict[str, object] | None = None
    if source_count == A1_SOURCE_COUNT:
        a1_binding = verify_a1_protocol_amendment(
            selected_audit,  # type: ignore[arg-type]
            selected_amendment,  # type: ignore[arg-type]
            canonical_plan=canonical,
            history_id=CANDIDATE_HISTORY_ID,
        )
    root = Path(candidate_root).resolve()
    try:
        inventory = {
            history: list(canonical["history_source_sha256s"][history])
            for history in APC_BASELINE_HISTORIES
        }
        inventory[CANDIDATE_HISTORY_ID] = inventory[CANDIDATE_HISTORY_ID][
            :source_count
        ]
        baseline = build_apc_aligned_baseline_plan(
            run_id=canonical["baseline_run_id"],
            history_source_sha256s=inventory,
            interarrival_ns=canonical["interarrival_ns"],
            execution_envelope_sha256=canonical[
                "shared_execution_envelope_sha256"
            ],
            service_reference_ns=canonical["service_reference_ns"],
            normalized_offered_load=canonical["normalized_offered_load"],
        )
    except (KeyError, TypeError, ValueError):
        raise _fail("candidate_baseline_projection_invalid") from None
    digest = hashlib.sha256(
        (
            f"{canonical['payload_sha256']}\0{candidate_id}\0{source_count}\0{root}"
        ).encode("utf-8")
    ).hexdigest()
    try:
        plan = build_membind_v31_live_plan(
            run_id=f"membind-v31-v4-ar-{digest[:24]}",
            verified_baseline_plan=baseline,
            methodology_sha256=canonical["methodology_sha256"],
            workplan_sha256=canonical["workplan_sha256"],
        )
        verified = verify_membind_v31_method_plan(plan)
    except (KeyError, TypeError, ValueError):
        raise _fail("candidate_plan_derivation_invalid") from None
    expected_offsets = canonical["arrival_traces"][CANDIDATE_HISTORY_ID][
        "arrival_offsets_ns"
    ][:source_count]
    block = verified["blocks"][0]
    if (
        block.get("method") != "MemBind"
        or block.get("history_id") != CANDIDATE_HISTORY_ID
        or block.get("source_count") != source_count
        or verified["arrival_traces"][CANDIDATE_HISTORY_ID][
            "arrival_offsets_ns"
        ]
        != expected_offsets
        or verified["compile_workers"] != canonical["compile_workers"]
        or verified["lookahead"] != canonical["lookahead"]
        or verified["global_llm_admission_k"]
        != canonical["global_llm_admission_k"]
        or block["namespace"] == canonical["blocks"][0]["namespace"]
    ):
        raise _fail("candidate_plan_identity_drift")
    if source_count == A1_SOURCE_COUNT:
        # Keep the amendment binding visible in the fresh plan without
        # changing the v3.1 method-plan schema.  The binding is carried in a
        # sidecar field that the v3.1 verifier intentionally ignores only if
        # it is absent; use the plan's existing payload seal below instead.
        # We therefore expose it to callers through a private, non-method
        # result only after verifying all method fields remain unchanged.
        # (The runner stores the binding in the candidate result.)
        if a1_binding is None:  # defensive; branch above always initializes it
            raise _fail("a1_binding_missing")
    return verified


def build_v4_candidate_live_runner(
    *,
    paths: ProductionExecutorPaths | None = None,
    loaders: V4ProductionLoaders | None = None,
    base_hooks_factory: Callable[[], V31LiveHooks] | None = None,
    factorized_adapter_factory: Callable[[object, StateCutCertification], object]
    | None = None,
    execute_block: Callable[..., object] = execute_v4_live_block,
    prior_six_reduction_path: Path | None = None,
    protocol_amendment: str | None = None,
    a1_audit_path: Path | None = None,
    a1_amendment_path: Path | None = None,
    audit_path: Path | None = None,
    amendment_path: Path | None = None,
) -> Callable[..., Mapping[str, object]]:
    """Build the live callback accepted by :func:`run_candidate`.

    The formal source inventory is verified when this factory is built.  Live
    inputs remain lazy so a failed service preflight cannot initialize Graphiti
    or create a namespace.
    """

    selected_paths = (
        ProductionExecutorPaths.from_repository(Path(__file__).resolve().parents[4])
        if paths is None
        else paths
    )
    if not isinstance(selected_paths, ProductionExecutorPaths):
        raise _fail("production_paths_invalid")
    selected_loaders = (
        production_v4_loaders(selected_paths) if loaders is None else loaders
    )
    if not isinstance(selected_loaders, V4ProductionLoaders):
        raise _fail("production_loaders_invalid")
    if not callable(execute_block):
        raise _fail("execute_block_invalid")
    try:
        canonical = verify_membind_v31_method_plan(
            selected_loaders.load_plan(selected_paths.control_root)
        )
    except ValueError:
        raise _fail("canonical_plan_invalid") from None
    loaded: dict[str, object] = {}

    def context() -> tuple[
        Mapping[str, str],
        StateCutCertification,
        Mapping[str, Sequence[object]],
    ]:
        if not loaded:
            env = selected_loaders.load_env(selected_paths.env_file)
            certification = selected_loaders.load_certification(
                selected_paths.freeze_paths
            )
            episodes = selected_loaders.load_episodes(
                selected_paths.development_input, canonical
            )
            if (
                not isinstance(env, Mapping)
                or any(
                    not isinstance(key, str) or not isinstance(value, str)
                    for key, value in env.items()
                )
                or not isinstance(certification, StateCutCertification)
                or tuple(episodes) != tuple(canonical["histories"])
            ):
                raise _fail("production_context_invalid")
            loaded.update(
                env=dict(env), certification=certification, episodes=episodes
            )
        return (  # type: ignore[return-value]
            loaded["env"],
            loaded["certification"],
            loaded["episodes"],
        )

    def run_candidate_prefix(**kwargs: object) -> Mapping[str, object]:
        store = kwargs.get("store")
        history_id = kwargs.get("history_id")
        source_count = kwargs.get("source_count")
        if not isinstance(store, CandidateStore):
            raise _fail("candidate_store_invalid")
        if history_id != CANDIDATE_HISTORY_ID:
            raise _fail("candidate_history_invalid")
        if source_count != store.source_count:
            raise _fail("candidate_source_identity_drift")
        prior_six_binding: dict[str, object] | None = None
        if source_count == 12:
            if prior_six_reduction_path is None:
                raise _fail("prior_six_reduction_required")
            prior_six_binding = verify_prior_six_reduction(
                prior_six_reduction_path,
                candidate_id=store.candidate_id,
                history_id=history_id,
            )
        elif prior_six_reduction_path is not None:
            raise _fail("prior_six_reduction_unexpected")
        selected_audit = a1_audit_path if a1_audit_path is not None else audit_path
        selected_amendment = (
            a1_amendment_path if a1_amendment_path is not None else amendment_path
        )
        plan = build_v4_candidate_plan(
            canonical,
            candidate_id=store.candidate_id,
            source_count=source_count,  # type: ignore[arg-type]
            candidate_root=store.root,
            protocol_amendment=protocol_amendment,
            a1_audit_path=selected_audit,
            a1_amendment_path=selected_amendment,
        )
        a1_binding: dict[str, object] | None = None
        if source_count == A1_SOURCE_COUNT:
            # build_v4_candidate_plan has already checked the sidecars.  Keep
            # the exact hashes in the public result so reduction can bind the
            # amendment without putting them into the method-plan payload.
            a1_binding = verify_a1_protocol_amendment(
                selected_audit,  # type: ignore[arg-type]
                selected_amendment,  # type: ignore[arg-type]
                canonical_plan=canonical,
                history_id=history_id,
            )
        block_indices = [
            index
            for index, block in enumerate(plan["blocks"])
            if block["method"] == "MemBind"
            and block["history_id"] == CANDIDATE_HISTORY_ID
        ]
        if len(block_indices) != 1:
            raise _fail("candidate_block_invalid")
        block_index = block_indices[0]
        block = plan["blocks"][block_index]
        env, certification, episodes = context()
        selected_episodes = episodes[CANDIDATE_HISTORY_ID][:source_count]
        if len(selected_episodes) != source_count:
            raise _fail("candidate_episode_prefix_invalid")
        hooks = base_hooks_factory() if base_hooks_factory is not None else None
        produced = execute_block(
            verified_plan=plan,
            block_index=block_index,
            episodes=selected_episodes,
            env=env,
            block_root=store.root / "block",
            state_cut_certification=certification,
            compile_workers=int(plan["compile_workers"]),
            lookahead=int(plan["lookahead"]),
            stream_id=CANDIDATE_HISTORY_ID,
            namespace_override=None,
            base_hooks=hooks,
            factorized_adapter_factory=factorized_adapter_factory,
        )
        result = asyncio.run(produced) if inspect.isawaitable(produced) else produced
        if not isinstance(result, Mapping):
            raise _fail("candidate_block_result_invalid")
        performance = result.get("performance")
        telemetry = result.get("telemetry")
        freshness = (
            performance.get("freshness_ns")
            if isinstance(performance, Mapping)
            else None
        )
        if (
            result.get("status") != "PASS"
            or result.get("run_id") != plan["run_id"]
            or result.get("history_id") != CANDIDATE_HISTORY_ID
            or result.get("namespace") != block["namespace"]
            or result.get("source_count") != source_count
            or result.get("direct_violation_count") != 0
            or not isinstance(performance, Mapping)
            or isinstance(freshness, (str, bytes))
            or not isinstance(freshness, Sequence)
            or len(freshness) != source_count
            or not isinstance(telemetry, Mapping)
            or telemetry.get("persistent_write_count") != 0
        ):
            raise _fail("candidate_block_result_invalid")
        publication_sequences = list(range(source_count))
        if result.get("publication_source_sequences") not in (None, publication_sequences):
            raise _fail("candidate_publication_order_invalid")
        event_rows = telemetry.get("events")
        llm_failed = telemetry.get("llm_failed_count", 0)
        if not isinstance(llm_failed, int):
            llm_failed = 0
        if isinstance(event_rows, Sequence) and not isinstance(event_rows, (str, bytes)):
            llm_failed = max(
                llm_failed,
                sum(
                    1
                    for event in event_rows
                    if isinstance(event, Mapping)
                    and event.get("event_type") in {"llm_failed", "llm_request_failed"}
                ),
            )
        wrong_version_reuse = telemetry.get("wrong_version_reuse_count", 0)
        if not isinstance(wrong_version_reuse, int) or isinstance(wrong_version_reuse, bool):
            wrong_version_reuse = sum(
                1
                for event in event_rows
                if isinstance(event, Mapping)
                and event.get("event_type")
                in {"wrong_version_reuse", "version_mismatch", "wrong_version"}
            ) if isinstance(event_rows, Sequence) and not isinstance(event_rows, (str, bytes)) else 0
        hidden_critical_time = result.get(
            "hidden_critical_time_ns", telemetry.get("hidden_critical_time_ns")
        )
        frontier_p95_service = result.get(
            "frontier_p95_service_ns", performance.get("frontier_p95_service_ns")
        )
        useful_throughput = result.get(
            "useful_token_throughput_tokens_per_second",
            performance.get("useful_token_throughput_tokens_per_second"),
        )
        frontier_ratio: object = result.get("frontier_p95_service_ratio")
        useful_ratio: object = result.get("useful_token_throughput_ratio")
        freshness_ratio: object = result.get("freshness_p95_ratio")
        makespan_ratio: object = result.get("makespan_ratio")
        if source_count == A1_SOURCE_COUNT:
            reference = (
                a1_binding.get("measurement_reference")
                if isinstance(a1_binding, Mapping)
                else None
            )
            values = (
                hidden_critical_time,
                frontier_p95_service,
                useful_throughput,
                performance.get("p95_freshness_ns"),
                performance.get("makespan_ns"),
            )
            if not isinstance(reference, Mapping) or any(
                isinstance(value, bool)
                or not isinstance(value, (int, float))
                or not math.isfinite(float(value))
                or float(value) < 0
                for value in values
            ):
                raise _fail("a1_live_measurement_evidence_invalid")
            denominators = (
                reference.get("frontier_p95_service_ns"),
                reference.get("useful_token_throughput_tokens_per_second"),
                reference.get("freshness_ns_p95"),
                reference.get("makespan_ns"),
            )
            if any(
                isinstance(value, bool)
                or not isinstance(value, (int, float))
                or not math.isfinite(float(value))
                or float(value) <= 0
                for value in denominators
            ):
                raise _fail("a1_live_measurement_reference_invalid")
            frontier_ratio = float(frontier_p95_service) / float(denominators[0])
            useful_ratio = float(useful_throughput) / float(denominators[1])
            freshness_ratio = float(performance["p95_freshness_ns"]) / float(
                denominators[2]
            )
            makespan_ratio = float(performance["makespan_ns"]) / float(
                denominators[3]
            )
        else:
            # Preserve the original six/twelve contract; those sealed paths
            # predate A1 raw-metric attribution and remain isolated from this
            # amendment.
            hidden_critical_time = 0 if hidden_critical_time is None else hidden_critical_time
            frontier_ratio = 1.0 if frontier_ratio is None else frontier_ratio
            useful_ratio = 1.0 if useful_ratio is None else useful_ratio
            freshness_ratio = 1.0 if freshness_ratio is None else freshness_ratio
            makespan_ratio = 1.0 if makespan_ratio is None else makespan_ratio
        return {
            "schema_version": "membind.paper-eval-v4.candidate-live-result.v1",
            "status": "PASS",
            "stream_id": CANDIDATE_HISTORY_ID,
            "source_count": source_count,
            "publication_source_sequences": publication_sequences,
            "publication_durable_count": source_count,
            "llm_failed_count": llm_failed,
            "wrong_version_reuse_count": wrong_version_reuse,
            "publication_order_violation_count": 0,
            "persistent_speculative_write_count": int(
                telemetry.get("persistent_write_count", 0) or 0
            ),
            "hidden_critical_time_ns": hidden_critical_time,
            "useful_token_throughput_tokens_per_second": useful_throughput,
            "useful_token_throughput_ratio": useful_ratio,
            "frontier_p95_service_ns": frontier_p95_service,
            "frontier_p95_service_ratio": frontier_ratio,
            "freshness_p95_ratio": freshness_ratio,
            "makespan_ratio": makespan_ratio,
            "direct_violation_count": 0,
            "performance": deepcopy(dict(performance)),
            "telemetry": deepcopy(dict(telemetry)),
            "prior_six_binding": deepcopy(prior_six_binding),
            "protocol_amendment": protocol_amendment,
            "a1_binding": deepcopy(a1_binding),
            "admission_observation": deepcopy(
                result.get("admission_observation")
            ),
            "output_artifacts": {
                "block_root": str((store.root / "block").resolve()),
                "candidate_plan_payload_sha256": plan["payload_sha256"],
                "candidate_plan_run_id": plan["run_id"],
                "v4_block_result_payload_sha256": result.get("payload_sha256"),
            },
        }

    return run_candidate_prefix


__all__ = [
    "CANDIDATE_HISTORY_ID",
    "CANDIDATE_SOURCE_COUNTS",
    "A1_PROTOCOL_AMENDMENT_ID",
    "A1_SOURCE_COUNT",
    "V4ProductionRunnerError",
    "build_v4_candidate_live_runner",
    "build_v4_candidate_plan",
    "verify_a1_protocol_amendment",
    "verify_a1_amendment",
    "verify_prior_six_reduction",
]
