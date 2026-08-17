"""Hash-bound development main-table contract for MemBind-v1.

The historical U0/P(C=2) records are useful characterization evidence, but
their arrival semantics are not a valid cross-method freshness comparison.
This module therefore keeps those references separate from a later fresh,
aligned U0/P(C=2)/MemBind-v1 table.  It is deliberately pure and offline: it
does not start a runner, touch a namespace, or mutate any sealed artifact.
"""

from __future__ import annotations

import hashlib
import math
import re
from collections.abc import Mapping, Sequence
from copy import deepcopy
from typing import Any

from ..artifacts import payload_sha256


SCHEMA_VERSION = "membind.paper-eval-v3.membind-v1-development-main-table.v1"
HISTORICAL_REFERENCE_STATUS = (
    "FROZEN_REFERENCE_NOT_CROSS_METHOD_FRESHNESS_COMPARABLE"
)
GRAPH_NATIVE_PROTOCOL_DEGENERATE = "NQ_GRAPH_NATIVE_PROTOCOL_DEGENERATE"
NUMERICALLY_COMPARABLE = "NUMERICALLY_COMPARABLE"
ALIGNED_METHODS = ("U0-aligned", "P(C=2)-aligned", "MemBind-v1 node-only")
_HISTORICAL_METHODS = ("U0", "P(C=2)")
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_RUN_ID = re.compile(r"^main-table-[a-z0-9][a-z0-9-]{2,63}$")

# These are the only development artifacts that this first table may cite.
# A subsequent re-run must use a new artifact/identity rather than resealing
# or replacing any of these pinned inputs.
_PINNED_ARTIFACT_PAYLOADS = {
    "three_baseline": "7c087a2368724f2f8cfb0f8e17cd5d2f54684e51b3cfb9203a0f6dc04eff4ef0",
    "development_report": "ba060bd48fb933319b522ef5196c003919b2a0c0d2a81c3eb9f00f4b264e9c62",
    "graph_quality_overlay": "15bd92d9f8393a3614d8764cdb71752e59f0e0668bc2f5ccb1746df8dad31953",
    "methodology_decision": "50a76d29ff973b67465940af94d3bc9c3814db04bad2774b4ea834b78ed22f4d",
    "final_methodology_envelope": "fdce14ca14af82e1f393663bcf822a3153cecbe86c93375a231ab71bcdddec1f",
}
_PINNED_METHODOLOGY_DOCUMENT_SHA256 = (
    "1daa14b633a814bb6674260b617f7ac92356b8b238cb6f8df52e6d0a7e65cb37"
)


class MainTableError(ValueError):
    """A provenance, alignment, or rendering contract failed closed."""


def _mapping(value: object, *, field: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise MainTableError(f"{field} is invalid")
    return value


def _sequence(value: object, *, field: str) -> Sequence[Any]:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes)):
        raise MainTableError(f"{field} is invalid")
    return value


def _text(value: object, *, field: str) -> str:
    if not isinstance(value, str) or not value:
        raise MainTableError(f"{field} is invalid")
    return value


def _sha(value: object, *, field: str) -> str:
    result = _text(value, field=field)
    if _SHA256.fullmatch(result) is None:
        raise MainTableError(f"{field} is invalid")
    return result


def _nonnegative_int(value: object, *, field: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise MainTableError(f"{field} is invalid")
    return value


def _positive_int(value: object, *, field: str) -> int:
    result = _nonnegative_int(value, field=field)
    if result < 1:
        raise MainTableError(f"{field} is invalid")
    return result


def _number(value: object, *, field: str) -> float:
    if (
        isinstance(value, bool)
        or not isinstance(value, (int, float))
        or not math.isfinite(float(value))
    ):
        raise MainTableError(f"{field} is invalid")
    return float(value)


def _probability(value: object, *, field: str) -> float:
    result = _number(value, field=field)
    if not 0.0 <= result <= 1.0:
        raise MainTableError(f"{field} is invalid")
    return result


def _verify_payload(
    value: Mapping[str, Any], *, label: str, expected_payload_sha256: str
) -> None:
    stored = _sha(value.get("payload_sha256"), field=f"{label} payload SHA256")
    body = {key: item for key, item in value.items() if key != "payload_sha256"}
    if stored != payload_sha256(body):
        raise MainTableError(f"{label} payload seal mismatch")
    if stored != expected_payload_sha256:
        raise MainTableError(f"{label} payload is not the pinned artifact")


def _expect_identity(
    value: Mapping[str, Any], *, label: str, schema_version: str
) -> None:
    if value.get("schema_version") != schema_version or value.get("status") != "PASS":
        raise MainTableError(f"{label} identity is invalid")


def _historical_rows(report: Mapping[str, Any]) -> list[dict[str, Any]]:
    methods = _mapping(report.get("methods"), field="development report methods")
    if tuple(report.get("method_order", ())) != ("U0", "A0", "P(C=2)"):
        raise MainTableError("development report method inventory is invalid")
    if set(methods) != {"U0", "A0", "P(C=2)"}:
        raise MainTableError("development report method inventory is invalid")
    result: list[dict[str, Any]] = []
    for method in _HISTORICAL_METHODS:
        row = _mapping(methods.get(method), field=f"historical {method} row")
        freshness = _mapping(row.get("freshness_ns"), field=f"historical {method} freshness")
        result.append(
            {
                "method": method,
                "episode_count": _positive_int(
                    row.get("episode_count"), field=f"historical {method} episode count"
                ),
                "qa_accuracy_macro": _probability(
                    row.get("qa_accuracy_macro"), field=f"historical {method} QA"
                ),
                "graph_native_qa_accuracy": _probability(
                    row.get("graph_quality_qa_accuracy"),
                    field=f"historical {method} graph-native QA",
                ),
                "evidence_recall_at_10_macro": _probability(
                    row.get("evidence_recall_at_10_macro"),
                    field=f"historical {method} Evidence Recall@10",
                ),
                "p95_freshness_ns": _positive_int(
                    freshness.get("p95"), field=f"historical {method} P95 freshness"
                ),
                "p99_freshness_ns": _positive_int(
                    freshness.get("p99"), field=f"historical {method} P99 freshness"
                ),
                "makespan_ns": _positive_int(
                    row.get("makespan_ns"), field=f"historical {method} makespan"
                ),
                "successful_goodput_episodes_per_second": _number(
                    row.get("successful_goodput_episodes_per_second"),
                    field=f"historical {method} goodput",
                ),
            }
        )
    return result


def _verify_final_envelope_chain(
    final_envelope: Mapping[str, Any], *, methodology_document_sha256: str
) -> None:
    sources = _mapping(final_envelope.get("sources"), field="final envelope sources")
    source_keys = {
        "three_baseline": "three_baselines",
        "development_report": "development_report",
        "graph_quality_overlay": "graph_quality_overlay",
        "methodology_decision": "methodology_decision",
    }
    for pinned_key, source_key in source_keys.items():
        source = _mapping(sources.get(source_key), field=f"final envelope {source_key}")
        if source.get("payload_sha256") != _PINNED_ARTIFACT_PAYLOADS[pinned_key]:
            raise MainTableError("final envelope source payload binding is invalid")
    document = _mapping(
        final_envelope.get("methodology_document"), field="final envelope methodology"
    )
    if (
        document.get("file_sha256") != methodology_document_sha256
        or document.get("status") != "DESIGN_COMPLETE"
        or document.get("deterministic_render_verified") is not True
    ):
        raise MainTableError("final envelope methodology binding is invalid")
    checks = _mapping(final_envelope.get("cross_checks"), field="final envelope checks")
    if not all(
        checks.get(key) is True
        for key in (
            "json_payload_seals_verified",
            "run_identity_chain_verified",
            "methodology_decision_binding_verified",
        )
    ):
        raise MainTableError("final envelope cross checks are invalid")


def bind_sealed_historical_references(
    *,
    baseline_suite: Mapping[str, Any],
    development_report: Mapping[str, Any],
    graph_quality_overlay: Mapping[str, Any],
    methodology_decision: Mapping[str, Any],
    final_methodology_envelope: Mapping[str, Any],
    methodology_document: str,
) -> dict[str, Any]:
    """Create a read-only, fail-closed binding to the sealed development lane.

    The result contains historical U0/P(C=2) descriptive rows only.  It never
    authorizes a new freshness delta because their original arrival timestamps
    were intentionally different across execution modes.
    """

    baseline = _mapping(baseline_suite, field="three-baseline artifact")
    report = _mapping(development_report, field="development report")
    overlay = _mapping(graph_quality_overlay, field="graph-quality overlay")
    decision = _mapping(methodology_decision, field="methodology decision")
    final = _mapping(final_methodology_envelope, field="final methodology envelope")
    document_text = _text(methodology_document, field="methodology document")
    document_sha = hashlib.sha256(document_text.encode("utf-8")).hexdigest()
    if document_sha != _PINNED_METHODOLOGY_DOCUMENT_SHA256:
        raise MainTableError("methodology document is not the pinned document")

    _verify_payload(
        baseline,
        label="three-baseline",
        expected_payload_sha256=_PINNED_ARTIFACT_PAYLOADS["three_baseline"],
    )
    _verify_payload(
        report,
        label="development report",
        expected_payload_sha256=_PINNED_ARTIFACT_PAYLOADS["development_report"],
    )
    _verify_payload(
        overlay,
        label="graph-quality overlay",
        expected_payload_sha256=_PINNED_ARTIFACT_PAYLOADS["graph_quality_overlay"],
    )
    _verify_payload(
        decision,
        label="methodology decision",
        expected_payload_sha256=_PINNED_ARTIFACT_PAYLOADS["methodology_decision"],
    )
    _verify_payload(
        final,
        label="final methodology envelope",
        expected_payload_sha256=_PINNED_ARTIFACT_PAYLOADS[
            "final_methodology_envelope"
        ],
    )
    _expect_identity(
        baseline,
        label="three-baseline",
        schema_version="membind.paper-eval-v3.three-baseline-report.v1",
    )
    _expect_identity(
        report,
        label="development report",
        schema_version="membind.paper-eval-v3.development-baseline-report.v1",
    )
    _expect_identity(
        overlay,
        label="graph-quality overlay",
        schema_version="membind.paper-eval-v3.graph-quality-report.v1",
    )
    _expect_identity(
        decision,
        label="methodology decision",
        schema_version="membind.paper-eval-v3.methodology-decision.v1",
    )
    _expect_identity(
        final,
        label="final methodology envelope",
        schema_version="membind.paper-eval-v3.final-methodology-envelope.v1",
    )
    if report.get("data_role") != "DEVELOPMENT_EXPOSED" or report.get(
        "heldout_data_accessed"
    ) is not False:
        raise MainTableError("development report data boundary is invalid")
    if baseline.get("run_id") != report.get("suite_run_id"):
        raise MainTableError("historical suite run binding is invalid")
    if overlay.get("overlay_run_id") != report.get("overlay_run_id"):
        raise MainTableError("historical overlay run binding is invalid")
    bindings = _mapping(decision.get("input_bindings"), field="decision input bindings")
    if (
        bindings.get("report_payload_sha256")
        != _PINNED_ARTIFACT_PAYLOADS["development_report"]
        or bindings.get("suite_run_id") != baseline.get("run_id")
        or bindings.get("overlay_run_id") != overlay.get("overlay_run_id")
    ):
        raise MainTableError("methodology decision historical binding is invalid")
    _verify_final_envelope_chain(final, methodology_document_sha256=document_sha)

    body: dict[str, Any] = {
        "schema_version": "membind.paper-eval-v3.membind-v1-historical-reference.v1",
        "historical_reference_status": HISTORICAL_REFERENCE_STATUS,
        "data_role": "DEVELOPMENT_EXPOSED",
        "heldout_data_accessed": False,
        "cross_method_freshness_delta_authorized": False,
        "artifact_payload_bindings": deepcopy(_PINNED_ARTIFACT_PAYLOADS),
        "methodology_document_sha256": document_sha,
        "rows": _historical_rows(report),
    }
    body["payload_sha256"] = payload_sha256(body)
    return body


def _verify_historical_references(value: Mapping[str, Any]) -> Mapping[str, Any]:
    reference = _mapping(value, field="historical references")
    stored = _sha(
        reference.get("payload_sha256"), field="historical references payload SHA256"
    )
    body = {key: item for key, item in reference.items() if key != "payload_sha256"}
    if stored != payload_sha256(body):
        raise MainTableError("historical references payload seal mismatch")
    if (
        reference.get("schema_version")
        != "membind.paper-eval-v3.membind-v1-historical-reference.v1"
        or reference.get("historical_reference_status") != HISTORICAL_REFERENCE_STATUS
        or reference.get("cross_method_freshness_delta_authorized") is not False
        or reference.get("artifact_payload_bindings") != _PINNED_ARTIFACT_PAYLOADS
        or reference.get("methodology_document_sha256")
        != _PINNED_METHODOLOGY_DOCUMENT_SHA256
    ):
        raise MainTableError("historical references identity is invalid")
    rows = _sequence(reference.get("rows"), field="historical reference rows")
    if tuple(
        _mapping(row, field="historical reference row").get("method") for row in rows
    ) != _HISTORICAL_METHODS:
        raise MainTableError("historical reference method inventory is invalid")
    return reference


def _validated_aligned_rows(
    rows: Sequence[Mapping[str, Any]],
) -> tuple[list[dict[str, Any]], dict[str, Any], str]:
    observed = tuple(
        _text(_mapping(row, field="aligned row").get("method"), field="aligned method")
        for row in rows
    )
    if observed != ALIGNED_METHODS:
        raise MainTableError("aligned method inventory is invalid")

    normalized: list[dict[str, Any]] = []
    identities: list[tuple[str, str, str, str, int]] = []
    statuses: list[str] = []
    for raw in rows:
        row = _mapping(raw, field="aligned row")
        method = _text(row.get("method"), field="aligned method")
        if row.get("execution_status") != "COMPLETED":
            raise MainTableError("aligned execution status is invalid")
        if row.get("validity_status") != "VALID":
            raise MainTableError("aligned validity status is invalid")
        quality_status = _text(row.get("quality_status"), field="aligned quality status")
        if quality_status not in {
            NUMERICALLY_COMPARABLE,
            GRAPH_NATIVE_PROTOCOL_DEGENERATE,
        }:
            raise MainTableError("aligned quality status is invalid")
        metrics = _mapping(row.get("metrics"), field=f"{method} metrics")
        normalized.append(
            {
                "method": method,
                "execution_status": "COMPLETED",
                "validity_status": "VALID",
                "quality_status": quality_status,
                "metrics": {
                    "qa_accuracy": _probability(
                        metrics.get("qa_accuracy"), field=f"{method} QA"
                    ),
                    "evidence_recall_at_10": _probability(
                        metrics.get("evidence_recall_at_10"),
                        field=f"{method} Evidence Recall@10",
                    ),
                    "direct_violations": _nonnegative_int(
                        metrics.get("direct_violations"),
                        field=f"{method} direct violations",
                    ),
                    "p95_arrival_to_publication_ns": _positive_int(
                        metrics.get("p95_arrival_to_publication_ns"),
                        field=f"{method} P95 arrival-to-publication",
                    ),
                    "p99_arrival_to_publication_ns": _positive_int(
                        metrics.get("p99_arrival_to_publication_ns"),
                        field=f"{method} P99 arrival-to-publication",
                    ),
                    "successful_goodput_episodes_per_second": _number(
                        metrics.get("successful_goodput_episodes_per_second"),
                        field=f"{method} goodput",
                    ),
                    "makespan_ns": _positive_int(
                        metrics.get("makespan_ns"), field=f"{method} makespan"
                    ),
                    "max_backlog": _nonnegative_int(
                        metrics.get("max_backlog"), field=f"{method} max backlog"
                    ),
                },
            }
        )
        identities.append(
            (
                _text(row.get("aligned_run_id"), field="aligned run id"),
                _sha(row.get("arrival_trace_sha256"), field="arrival trace SHA256"),
                _sha(row.get("source_manifest_sha256"), field="source manifest SHA256"),
                _sha(
                    row.get("shared_execution_envelope_sha256"),
                    field="shared execution envelope SHA256",
                ),
                _positive_int(
                    row.get("global_llm_admission_k"),
                    field="global LLM admission K",
                ),
            )
        )
        statuses.append(quality_status)

    first = identities[0]
    if any(identity[0] != first[0] for identity in identities[1:]):
        raise MainTableError("aligned run identity is inconsistent")
    if any(identity[1] != first[1] for identity in identities[1:]):
        raise MainTableError("arrival trace is inconsistent")
    if any(identity[2] != first[2] for identity in identities[1:]):
        raise MainTableError("source manifest is inconsistent")
    if any(identity[3] != first[3] for identity in identities[1:]):
        raise MainTableError("shared execution envelope is inconsistent")
    if first[4] != 2 or any(identity[4] != 2 for identity in identities[1:]):
        raise MainTableError("global LLM admission K must equal 2")
    if len(set(statuses)) != 1:
        raise MainTableError("aligned quality status is inconsistent")

    return (
        normalized,
        {
            "aligned_run_id": first[0],
            "arrival_trace_sha256": first[1],
            "source_manifest_sha256": first[2],
            "shared_execution_envelope_sha256": first[3],
            "global_llm_admission_k": first[4],
        },
        statuses[0],
    )


def build_development_main_table(
    *,
    main_table_run_id: str,
    historical_references: Mapping[str, Any],
    aligned_rows: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    """Build the first development-only main table from aligned fresh rows.

    Fresh comparable rows must be exactly U0-aligned, P(C=2)-aligned, and
    MemBind-v1 node-only.  They must share the arrival trace, source manifest,
    execution envelope, and global LLM admission K=2.
    """

    if _RUN_ID.fullmatch(main_table_run_id) is None:
        raise MainTableError("main table run id is invalid")
    historical = _verify_historical_references(historical_references)
    rows = _sequence(aligned_rows, field="aligned rows")
    normalized, identity, quality_status = _validated_aligned_rows(rows)
    body: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "status": "PASS",
        "main_table_run_id": main_table_run_id,
        "data_role": "DEVELOPMENT_EXPOSED",
        "heldout_data_accessed": False,
        "claim_boundary": "DEVELOPMENT_ONLY_QUALITY_PROTOCOL_BLOCKED_NOT_FINAL_HELDOUT",
        "historical_reference_status": HISTORICAL_REFERENCE_STATUS,
        "historical_reference_payload_sha256": historical["payload_sha256"],
        "aligned_identity": identity,
        "quality_comparison_status": quality_status,
        "aligned_comparative_rows": normalized,
    }
    body["payload_sha256"] = payload_sha256(body)
    return body


def _verify_main_table(value: Mapping[str, Any]) -> Mapping[str, Any]:
    table = _mapping(value, field="main table")
    stored = _sha(table.get("payload_sha256"), field="main table payload SHA256")
    body = {key: item for key, item in table.items() if key != "payload_sha256"}
    if stored != payload_sha256(body):
        raise MainTableError("main table payload seal mismatch")
    if (
        table.get("schema_version") != SCHEMA_VERSION
        or table.get("status") != "PASS"
        or table.get("data_role") != "DEVELOPMENT_EXPOSED"
        or table.get("heldout_data_accessed") is not False
        or table.get("historical_reference_status") != HISTORICAL_REFERENCE_STATUS
    ):
        raise MainTableError("main table identity is invalid")
    if _RUN_ID.fullmatch(_text(table.get("main_table_run_id"), field="main table run id")) is None:
        raise MainTableError("main table run id is invalid")
    rows = _sequence(table.get("aligned_comparative_rows"), field="aligned rows")
    aligned_identity = _mapping(table.get("aligned_identity"), field="aligned identity")
    # Reusing the row validator ensures a renderer cannot make an invalid table
    # look valid merely because its outer payload was resealed.
    reconstructed_rows = []
    for row in rows:
        reconstructed_rows.append(
            {
                **_mapping(row, field="aligned row"),
                "aligned_run_id": aligned_identity.get("aligned_run_id"),
                "arrival_trace_sha256": aligned_identity.get("arrival_trace_sha256"),
                "source_manifest_sha256": aligned_identity.get("source_manifest_sha256"),
                "shared_execution_envelope_sha256": aligned_identity.get(
                    "shared_execution_envelope_sha256"
                ),
                "global_llm_admission_k": aligned_identity.get(
                    "global_llm_admission_k"
                ),
            }
        )
    normalized, identity, quality = _validated_aligned_rows(reconstructed_rows)
    if list(rows) != normalized or table.get("aligned_identity") != identity:
        raise MainTableError("main table aligned projection is invalid")
    if table.get("quality_comparison_status") != quality:
        raise MainTableError("main table quality projection is invalid")
    _sha(
        table.get("historical_reference_payload_sha256"),
        field="historical reference payload SHA256",
    )
    return table


def _seconds(value: int | float) -> str:
    return f"{float(value) / 1_000_000_000:.3f}"


def render_development_main_table_markdown(table: Mapping[str, Any]) -> str:
    """Render a deterministic, reviewer-facing development-only projection."""

    verified = _verify_main_table(table)
    quality_status = verified["quality_comparison_status"]
    lines = [
        "# MemBind-v1 Development Main Table",
        "",
        "**Status:** development-only; quality-protocol-blocked; not a final held-out paper table.",
        "",
        "The frozen U0/P(C=2) records remain a historical reference because their "
        "arrival timestamp semantics differ. The aligned rows below are the only "
        "cross-method comparison lane.",
        "",
        "## Historical Reference",
        "",
        f"`{HISTORICAL_REFERENCE_STATUS}`",
        "",
        "The historical rows are hash-bound separately and cannot support a cross-method "
        "freshness delta or speedup claim.",
        "",
        "## Fresh Aligned Comparative Table",
        "",
        f"- Aligned run: `{verified['aligned_identity']['aligned_run_id']}`",
        f"- Arrival trace SHA256: `{verified['aligned_identity']['arrival_trace_sha256']}`",
        f"- Source manifest SHA256: `{verified['aligned_identity']['source_manifest_sha256']}`",
        f"- Shared execution envelope SHA256: `{verified['aligned_identity']['shared_execution_envelope_sha256']}`",
        f"- Global LLM admission K: `{verified['aligned_identity']['global_llm_admission_k']}`",
        f"- Quality comparison status: `{quality_status}`",
        "",
        "| Method | Graph-native QA | Evidence Recall@10 | Direct violations | P95 arrival-to-publication (s) | P99 arrival-to-publication (s) | Goodput (ep/s) | Makespan (s) | Max backlog |",
        "|---|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for row in verified["aligned_comparative_rows"]:
        metrics = row["metrics"]
        qa = (
            GRAPH_NATIVE_PROTOCOL_DEGENERATE
            if quality_status == GRAPH_NATIVE_PROTOCOL_DEGENERATE
            else f"{metrics['qa_accuracy']:.3f}"
        )
        lines.append(
            f"| {row['method']} | {qa} | {metrics['evidence_recall_at_10']:.3f} | "
            f"{metrics['direct_violations']} | "
            f"{_seconds(metrics['p95_arrival_to_publication_ns'])} | "
            f"{_seconds(metrics['p99_arrival_to_publication_ns'])} | "
            f"{metrics['successful_goodput_episodes_per_second']:.6f} | "
            f"{_seconds(metrics['makespan_ns'])} | {metrics['max_backlog']} |"
        )
    if quality_status == GRAPH_NATIVE_PROTOCOL_DEGENERATE:
        lines.extend(
            [
                "",
                "Graph-native QA is rendered as `NQ_GRAPH_NATIVE_PROTOCOL_DEGENERATE`; "
                "it is not numerically comparable until the quality protocol is repaired "
                "and re-qualified in a separately identified lane.",
            ]
        )
    lines.extend(
        [
            "",
            "## Claim Boundary",
            "",
            "This table is development-only and does not supersede the sealed historical "
            "methodology verdict. It cannot be used as a final held-out paper table or as "
            "evidence that memory quality is non-regressive while the graph-native quality "
            "protocol remains degenerate.",
            "",
        ]
    )
    return "\n".join(lines)


__all__ = [
    "ALIGNED_METHODS",
    "GRAPH_NATIVE_PROTOCOL_DEGENERATE",
    "HISTORICAL_REFERENCE_STATUS",
    "MainTableError",
    "NUMERICALLY_COMPARABLE",
    "SCHEMA_VERSION",
    "bind_sealed_historical_references",
    "build_development_main_table",
    "render_development_main_table_markdown",
]
