"""Deterministic decision policy for the development methodology.

This module performs no filesystem or network I/O.  It converts the sealed
three-baseline report and the earlier C5 screening result into a bounded
methodology verdict.  In particular, it never authorizes a paper claim or a
live method run.
"""

from __future__ import annotations

import math
import re
from collections.abc import Mapping, Sequence
from typing import Any

from .artifacts import payload_sha256


METHODS = ("U0", "A0", "P(C=2)")
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_DECISION_RUN_ID = re.compile(r"^methodology-[a-z0-9][a-z0-9-]{2,63}$")


class MethodologyDecisionError(ValueError):
    """The sealed evidence is invalid or insufficient for a decision."""


def _mapping(value: object, *, field: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise MethodologyDecisionError(f"{field} is invalid")
    return value


def _sequence(value: object, *, field: str) -> Sequence[Any]:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes)):
        raise MethodologyDecisionError(f"{field} is invalid")
    return value


def _text(value: object, *, field: str) -> str:
    if not isinstance(value, str) or not value:
        raise MethodologyDecisionError(f"{field} is invalid")
    return value


def _sha(value: object, *, field: str) -> str:
    text = _text(value, field=field)
    if _SHA256.fullmatch(text) is None:
        raise MethodologyDecisionError(f"{field} is invalid")
    return text


def _integer(value: object, *, field: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise MethodologyDecisionError(f"{field} is invalid")
    return value


def _number(value: object, *, field: str) -> float:
    if (
        isinstance(value, bool)
        or not isinstance(value, (int, float))
        or not math.isfinite(float(value))
    ):
        raise MethodologyDecisionError(f"{field} is invalid")
    return float(value)


def _verify_payload_seal(value: Mapping[str, Any], *, label: str) -> str:
    stored = _sha(value.get("payload_sha256"), field=f"{label} payload SHA256")
    body = {key: item for key, item in value.items() if key != "payload_sha256"}
    if stored != payload_sha256(body):
        raise MethodologyDecisionError(f"{label} payload seal mismatch")
    return stored


def _validate_report(report: Mapping[str, Any]) -> dict[str, Mapping[str, Any]]:
    if report.get("schema_version") != (
        "membind.paper-eval-v3.development-baseline-report.v1"
    ) or report.get("status") != "PASS":
        raise MethodologyDecisionError("report identity is invalid")
    if report.get("data_role") != "DEVELOPMENT_EXPOSED":
        raise MethodologyDecisionError("report data role is invalid")
    if report.get("heldout_data_accessed") is not False:
        raise MethodologyDecisionError("report accessed held-out data")
    if tuple(_sequence(report.get("method_order"), field="method order")) != METHODS:
        raise MethodologyDecisionError("report method inventory drift")

    history_order = tuple(
        _text(item, field="history id")
        for item in _sequence(report.get("history_order"), field="history order")
    )
    if len(history_order) != 4 or len(set(history_order)) != len(history_order):
        raise MethodologyDecisionError("report history inventory drift")

    raw_methods = _mapping(report.get("methods"), field="report methods")
    if set(raw_methods) != set(METHODS):
        raise MethodologyDecisionError("report method inventory drift")

    methods: dict[str, Mapping[str, Any]] = {}
    for method in METHODS:
        row = _mapping(raw_methods[method], field=f"{method} report row")
        if _integer(row.get("history_count"), field=f"{method} history count") != 4:
            raise MethodologyDecisionError("report history count drift")
        episode_count = _integer(
            row.get("episode_count"), field=f"{method} episode count"
        )
        if episode_count != 188:
            raise MethodologyDecisionError("report episode count drift")
        histories = _sequence(row.get("histories"), field=f"{method} histories")
        history_rows = [
            _mapping(item, field=f"{method} history row") for item in histories
        ]
        observed_order = tuple(
            _text(item.get("history_id"), field=f"{method} history id")
            for item in history_rows
        )
        if observed_order != history_order:
            raise MethodologyDecisionError(f"{method} history identity drift")
        makespan_ns = _number(row.get("makespan_ns"), field=f"{method} makespan")
        goodput = _number(
            row.get("successful_goodput_episodes_per_second"),
            field=f"{method} goodput",
        )
        if makespan_ns <= 0 or not math.isclose(
            goodput,
            episode_count / (makespan_ns / 1_000_000_000),
            rel_tol=1e-12,
            abs_tol=1e-15,
        ):
            raise MethodologyDecisionError(f"{method} goodput/makespan drift")
        history_makespan_sum = sum(
            _number(item.get("makespan_ns"), field=f"{method} history makespan")
            for item in history_rows
        )
        if not math.isclose(
            makespan_ns,
            history_makespan_sum,
            rel_tol=0.0,
            abs_tol=0.0,
        ):
            raise MethodologyDecisionError(f"{method} aggregate makespan drift")
        methods[method] = row
    return methods


def _quality_usable(methods: Mapping[str, Mapping[str, Any]]) -> bool:
    for method in METHODS:
        row = methods[method]
        if (
            _integer(
                row.get("graph_quality_valid_judge_count"),
                field=f"{method} valid Judge count",
            )
            != 4
            or _integer(
                row.get("graph_quality_invalid_judge_count"),
                field=f"{method} invalid Judge count",
            )
            != 0
        ):
            return False
        accuracy = row.get("graph_quality_qa_accuracy")
        if accuracy is None:
            return False
        observed = _number(accuracy, field=f"{method} graph QA")
        if not 0.0 <= observed <= 1.0:
            raise MethodologyDecisionError(f"{method} graph QA is invalid")
    return (
        _number(
            methods["U0"].get("graph_quality_qa_accuracy"),
            field="U0 graph QA",
        )
        > 0.0
    )


def _history_makespans(row: Mapping[str, Any], *, method: str) -> dict[str, float]:
    result: dict[str, float] = {}
    for value in _sequence(row.get("histories"), field=f"{method} histories"):
        item = _mapping(value, field=f"{method} history row")
        history_id = _text(item.get("history_id"), field=f"{method} history id")
        result[history_id] = _number(
            item.get("makespan_ns"), field=f"{method} history makespan"
        )
    return result


def _c5_direct_counterexample(c5: Mapping[str, Any]) -> bool:
    if c5.get("schema_version") != (
        "membind.native-characterization-e4-whole-parallel.v1"
    ) or c5.get("status") != "complete" or c5.get("stage") != "C5/E4":
        raise MethodologyDecisionError("C5 identity is invalid")
    if _integer(c5.get("completed_block_count"), field="C5 block count") != 4:
        raise MethodologyDecisionError("C5 block inventory drift")
    blocks = _sequence(c5.get("block_results"), field="C5 blocks")
    if len(blocks) != 4:
        raise MethodologyDecisionError("C5 block inventory drift")
    by_concurrency: dict[int, Mapping[str, Any]] = {}
    for value in blocks:
        block = _mapping(value, field="C5 block")
        if block.get("status") != "complete":
            raise MethodologyDecisionError("C5 block status drift")
        metrics = _mapping(block.get("metrics"), field="C5 metrics")
        concurrency = _integer(metrics.get("concurrency"), field="C5 concurrency")
        if concurrency in by_concurrency:
            raise MethodologyDecisionError("C5 concurrency inventory drift")
        by_concurrency[concurrency] = block
    if set(by_concurrency) != {1, 2, 4, 8}:
        raise MethodologyDecisionError("C5 concurrency inventory drift")
    c2 = by_concurrency[2]
    direct = tuple(
        _text(item, field="C5 direct evidence")
        for item in _sequence(c2.get("direct_evidence"), field="C5 direct evidence")
    )
    return (
        c5.get("overall_interpretation")
        == "DIRECT_INVARIANT_VIOLATION_OBSERVED"
        and c2.get("interpretation") == "DIRECT_INVARIANT_VIOLATION_OBSERVED"
        and "source-order invariant violation" in direct
    )


def _classify(
    *, quality_usable: bool, capacity_signal: bool, direct_counterexample: bool
) -> tuple[str, str, str]:
    if not quality_usable:
        return (
            "BLOCKED_QUALITY_PROTOCOL",
            "BLOCKED_QUALITY_PROTOCOL",
            "NO_METHOD_SELECTED",
        )
    if capacity_signal and direct_counterexample:
        return (
            "CAPACITY_SIGNAL_WITH_DIRECT_INVARIANT_COUNTEREXAMPLE",
            "PROBLEM_SUPPORTED_FOR_BOUNDED_NODE_ONLY_PROTOTYPE",
            "NODE_ONLY_CANDIDATE",
        )
    if capacity_signal:
        return (
            "CAPACITY_SIGNAL_WITHOUT_OBSERVED_INSUFFICIENCY",
            "REASSESS_MEMBIND_NECESSITY",
            "NO_METHOD_SELECTED",
        )
    if direct_counterexample:
        return (
            "NO_CAPACITY_SIGNAL_WITH_DIRECT_INVARIANT_COUNTEREXAMPLE",
            "PERFORMANCE_MOTIVATION_INSUFFICIENT",
            "STOP_OR_REFRAME_AS_SERVING_PROBLEM",
        )
    return (
        "NO_CAPACITY_SIGNAL_WITHOUT_OBSERVED_INSUFFICIENCY",
        "STOP_NO_SYSTEM_SIGNAL",
        "NO_METHOD_SELECTED",
    )


def build_methodology_decision(
    *,
    decision_run_id: str,
    report: Mapping[str, Any],
    c5_result: Mapping[str, Any],
    report_file_sha256: str,
    c5_file_sha256: str,
    characterization_file_sha256: str,
    characterization_payload_sha256: str,
    c5_events_file_sha256: str,
) -> dict[str, Any]:
    """Build the bounded development decision from sealed input mappings."""

    selected_report = _mapping(report, field="report")
    selected_c5 = _mapping(c5_result, field="C5 result")
    selected_run_id = _text(decision_run_id, field="decision run id")
    if _DECISION_RUN_ID.fullmatch(selected_run_id) is None:
        raise MethodologyDecisionError("decision run id is invalid")
    report_payload_sha = _verify_payload_seal(selected_report, label="report")
    c5_payload_sha = _verify_payload_seal(selected_c5, label="C5")
    methods = _validate_report(selected_report)
    quality_usable = _quality_usable(methods)
    u0_graph_quality_non_degenerate = (
        _number(
            methods["U0"].get("graph_quality_qa_accuracy"),
            field="U0 graph QA",
        )
        > 0.0
    )
    direct_counterexample = _c5_direct_counterexample(selected_c5)

    u0_history = _history_makespans(methods["U0"], method="U0")
    p_history = _history_makespans(methods["P(C=2)"], method="P(C=2)")
    if tuple(p_history) != tuple(u0_history):
        raise MethodologyDecisionError("P(C=2) history identity drift")
    paired_wins = sum(
        p_history[history_id] < u0_history[history_id]
        for history_id in u0_history
    )
    p_row = methods["P(C=2)"]
    p_work_ratios = dict(
        _mapping(
            p_row.get("work_volume_ratio_vs_u0"),
            field="P(C=2) work volume ratios",
        )
    )
    p_capacity_signal = (
        p_row.get("overlap_observed") is True
        and _integer(
            p_row.get("observed_max_active_calls"),
            field="P(C=2) observed max active calls",
        )
        >= 2
        and _number(p_row.get("makespan_ns"), field="P(C=2) makespan")
        < _number(methods["U0"].get("makespan_ns"), field="U0 makespan")
    )

    a0_row = methods["A0"]
    a0_backlog = _integer(a0_row.get("max_backlog"), field="A0 max backlog")
    a0_burst_backlog_observed = (
        a0_backlog > 0
        and _integer(
            a0_row.get("observed_max_active_calls"),
            field="A0 observed max active calls",
        )
        == 1
        and a0_row.get("overlap_observed") is False
    )

    cell, problem_verdict, mechanism_status = _classify(
        quality_usable=quality_usable,
        capacity_signal=p_capacity_signal,
        direct_counterexample=direct_counterexample,
    )
    body: dict[str, Any] = {
        "schema_version": "membind.paper-eval-v3.methodology-decision.v1",
        "status": "PASS",
        "decision_run_id": selected_run_id,
        "scope": "DEVELOPMENT_EXPOSED_DESCRIPTIVE_ONLY",
        "input_bindings": {
            "report_run_id": _text(
                selected_report.get("report_run_id"), field="report run id"
            ),
            "native_run_id": _text(
                selected_report.get("native_run_id"), field="native run id"
            ),
            "suite_run_id": _text(
                selected_report.get("suite_run_id"), field="suite run id"
            ),
            "overlay_run_id": _text(
                selected_report.get("overlay_run_id"), field="overlay run id"
            ),
            "report_file_sha256": _sha(
                report_file_sha256, field="report file SHA256"
            ),
            "report_payload_sha256": report_payload_sha,
            "c5_run_id": _text(selected_c5.get("run_id"), field="C5 run id"),
            "c5_file_sha256": _sha(c5_file_sha256, field="C5 file SHA256"),
            "c5_payload_sha256": c5_payload_sha,
            "c5_events_file_sha256": _sha(
                c5_events_file_sha256, field="C5 events file SHA256"
            ),
            "characterization_file_sha256": _sha(
                characterization_file_sha256,
                field="characterization file SHA256",
            ),
            "characterization_payload_sha256": _sha(
                characterization_payload_sha256,
                field="characterization payload SHA256",
            ),
        },
        "classification_policy": {
            "quality_protocol_usable": (
                "for every method: valid_judge_count == 4, "
                "invalid_judge_count == 0, graph QA is numeric, and U0 has "
                "at least one correct graph-native QA result"
            ),
            "capacity_signal": (
                "P overlap observed, max active updates >= 2, aggregate "
                "makespan < U0, and goodput is therefore directionally higher "
                "for the same episode count; paired-history wins are diagnostic"
            ),
            "direct_counterexample": (
                "sealed C5 overall and C=2 block both report a source-order "
                "direct invariant violation"
            ),
        },
        "observations": {
            "quality_protocol_usable": quality_usable,
            "u0_graph_quality_non_degenerate": (
                u0_graph_quality_non_degenerate
            ),
            "p_capacity_signal": p_capacity_signal,
            "p_paired_history_makespan_wins": paired_wins,
            "p_paired_history_count": len(u0_history),
            "p_aggregate_makespan_ratio_vs_u0": (
                _number(p_row.get("makespan_ns"), field="P(C=2) makespan")
                / _number(methods["U0"].get("makespan_ns"), field="U0 makespan")
            ),
            "p_work_volume_ratio_vs_u0": p_work_ratios,
            "c5_direct_counterexample": direct_counterexample,
            "c5_model_nondeterminism_assessment": (
                "GRAPH_RETRIEVAL_PARITY_CONFOUNDED_"
                "DIRECT_ORDER_EVIDENCE_UNAFFECTED"
            ),
            "a0_burst_backlog_observed": a0_burst_backlog_observed,
        },
        "comparison_boundaries": {
            "freshness": (
                "NOT_CROSS_METHOD_COMPARABLE_CURRENT_ARRIVAL_SEMANTICS"
            ),
            "makespan_goodput": (
                "DESCRIPTIVE_BURST_DRAIN_DEVELOPMENT_CAPACITY"
            ),
            "resource_comparability": (
                "NOT_ESTABLISHED_UNIFIED_REQUEST_ADMISSION_ABSENT"
            ),
            "semantic_parity": (
                "NOT_AUTHORIZED_LIVE_MODEL_OUTPUTS_NOT_CAPTURE_REPLAY_FIXED"
            ),
            "statistical_claim": "NOT_AUTHORIZED_NO_REPEATS_DEVELOPMENT_ONLY",
        },
        "actual_decision_matrix_cell": cell,
        "problem_verdict": problem_verdict,
        "mechanism_status": mechanism_status,
        "paper_claim_status": "NOT_AUTHORIZED_DEVELOPMENT_ONLY",
        "live_method_status": "NOT_AUTHORIZED",
    }
    return {**body, "payload_sha256": payload_sha256(body)}


__all__ = [
    "METHODS",
    "MethodologyDecisionError",
    "build_methodology_decision",
]
