"""Deterministically bind the main methodology document to sealed evidence.

The renderer is intentionally pure: it performs no filesystem, database, or
network I/O.  A finalized document carries the hash of the pending template,
which makes a same-input rerun byte-idempotent while rejecting later drift.
"""

from __future__ import annotations

import hashlib
import math
import re
from collections.abc import Mapping, Sequence
from typing import Any

from .artifacts import payload_sha256


METHODS = ("U0", "A0", "P(C=2)")
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_FINAL_BINDING = re.compile(
    r"^> Renderer template SHA256: `(?P<template>[0-9a-f]{64})`\n"
    r"^> Methodology decision run: `(?P<run>[^`]+)`\n"
    r"^> Methodology decision payload SHA256: `(?P<payload>[0-9a-f]{64})`[ \t]*$",
    re.MULTILINE,
)


class MethodologyDocumentError(ValueError):
    """The document or one of its sealed inputs failed closed."""


def _mapping(value: object, *, field: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise MethodologyDocumentError(f"{field} is invalid")
    return value


def _sequence(value: object, *, field: str) -> Sequence[Any]:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes)):
        raise MethodologyDocumentError(f"{field} is invalid")
    return value


def _text(value: object, *, field: str) -> str:
    if not isinstance(value, str) or not value:
        raise MethodologyDocumentError(f"{field} is invalid")
    return value


def _sha(value: object, *, field: str) -> str:
    selected = _text(value, field=field)
    if _SHA256.fullmatch(selected) is None:
        raise MethodologyDocumentError(f"{field} is invalid")
    return selected


def _integer(value: object, *, field: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise MethodologyDocumentError(f"{field} is invalid")
    return value


def _number(value: object, *, field: str) -> float:
    if (
        isinstance(value, bool)
        or not isinstance(value, (int, float))
        or not math.isfinite(float(value))
    ):
        raise MethodologyDocumentError(f"{field} is invalid")
    return float(value)


def _verify_seal(value: Mapping[str, Any], *, label: str) -> str:
    stored = _sha(value.get("payload_sha256"), field=f"{label} payload seal")
    body = {key: item for key, item in value.items() if key != "payload_sha256"}
    if stored != payload_sha256(body):
        raise MethodologyDocumentError(f"{label} payload seal mismatch")
    return stored


def _validate_report(report: Mapping[str, Any]) -> dict[str, Mapping[str, Any]]:
    if (
        report.get("schema_version")
        != "membind.paper-eval-v3.development-baseline-report.v1"
        or report.get("status") != "PASS"
        or report.get("data_role") != "DEVELOPMENT_EXPOSED"
        or report.get("heldout_data_accessed") is not False
    ):
        raise MethodologyDocumentError("report status or identity is invalid")
    if tuple(_sequence(report.get("method_order"), field="report method order")) != METHODS:
        raise MethodologyDocumentError("report method identity drift")
    raw_methods = _mapping(report.get("methods"), field="report methods")
    if set(raw_methods) != set(METHODS):
        raise MethodologyDocumentError("report method identity drift")

    methods: dict[str, Mapping[str, Any]] = {}
    for method in METHODS:
        row = _mapping(raw_methods[method], field=f"{method} report row")
        if _integer(row.get("episode_count"), field=f"{method} episode count") != 188:
            raise MethodologyDocumentError(f"{method} episode identity drift")
        if _number(
            row.get("successful_goodput_episodes_per_second"),
            field=f"{method} goodput",
        ) < 0:
            raise MethodologyDocumentError(f"{method} goodput is invalid")
        freshness = _mapping(row.get("freshness_ns"), field=f"{method} freshness")
        if _number(freshness.get("p95"), field=f"{method} P95 freshness") < 0:
            raise MethodologyDocumentError(f"{method} freshness is invalid")
        if _number(freshness.get("p99"), field=f"{method} P99 freshness") < 0:
            raise MethodologyDocumentError(f"{method} freshness is invalid")
        if _number(row.get("makespan_ns"), field=f"{method} makespan") <= 0:
            raise MethodologyDocumentError(f"{method} makespan is invalid")
        recall = _number(
            row.get("evidence_recall_at_10_macro"),
            field=f"{method} evidence recall",
        )
        if not 0 <= recall <= 1:
            raise MethodologyDocumentError(f"{method} evidence recall is invalid")
        valid = _integer(
            row.get("graph_quality_valid_judge_count"),
            field=f"{method} valid Judge count",
        )
        invalid = _integer(
            row.get("graph_quality_invalid_judge_count"),
            field=f"{method} invalid Judge count",
        )
        if valid < 0 or invalid < 0 or valid + invalid != 4:
            raise MethodologyDocumentError(f"{method} Judge denominator is invalid")
        graph_qa = row.get("graph_quality_qa_accuracy")
        if graph_qa is not None and not 0 <= _number(
            graph_qa, field=f"{method} graph QA"
        ) <= 1:
            raise MethodologyDocumentError(f"{method} graph QA is invalid")
        direct = row.get("direct_violations")
        if direct is not None and _integer(
            direct, field=f"{method} direct violations"
        ) < 0:
            raise MethodologyDocumentError(f"{method} direct violations are invalid")
        statuses = tuple(
            _text(item, field=f"{method} direct violation status")
            for item in _sequence(
                row.get("direct_violations_statuses"),
                field=f"{method} direct violation statuses",
            )
        )
        if not statuses:
            raise MethodologyDocumentError(
                f"{method} direct violation statuses are empty"
            )
        backlog = row.get("max_backlog")
        if backlog is not None and _integer(
            backlog, field=f"{method} max backlog"
        ) < 0:
            raise MethodologyDocumentError(f"{method} max backlog is invalid")
        _text(row.get("max_backlog_status"), field=f"{method} max backlog status")
        if _integer(
            row.get("observed_max_active_calls"),
            field=f"{method} max active calls",
        ) < 0:
            raise MethodologyDocumentError(f"{method} max active calls are invalid")
        if type(row.get("overlap_observed")) is not bool:
            raise MethodologyDocumentError(f"{method} overlap is invalid")
        workers = tuple(
            _integer(item, field=f"{method} worker count")
            for item in _sequence(
                row.get("configured_worker_counts"),
                field=f"{method} worker counts",
            )
        )
        if not workers or any(item < 1 for item in workers):
            raise MethodologyDocumentError(f"{method} worker counts are invalid")
        work = _mapping(row.get("work_volume"), field=f"{method} work volume")
        for key in (
            "llm_logical_calls",
            "llm_input_tokens",
            "llm_output_tokens",
            "embedding_calls",
            "embedding_items",
            "db_operations",
            "db_transactions",
            "candidate_count",
        ):
            if _integer(work.get(key), field=f"{method} {key}") < 0:
                raise MethodologyDocumentError(f"{method} work volume is invalid")
        graph = _mapping(row.get("final_graph"), field=f"{method} final graph")
        for key in ("node_count_sum", "relationship_count_sum"):
            if _integer(graph.get(key), field=f"{method} {key}") < 0:
                raise MethodologyDocumentError(f"{method} final graph is invalid")
        methods[method] = row
    return methods


def _validate_decision(
    decision: Mapping[str, Any], report: Mapping[str, Any]
) -> Mapping[str, Any]:
    if (
        decision.get("schema_version")
        != "membind.paper-eval-v3.methodology-decision.v1"
        or decision.get("status") != "PASS"
        or decision.get("scope") != "DEVELOPMENT_EXPOSED_DESCRIPTIVE_ONLY"
    ):
        raise MethodologyDocumentError("decision status or identity is invalid")
    bindings = _mapping(decision.get("input_bindings"), field="decision bindings")
    for key in (
        "report_run_id",
        "native_run_id",
        "suite_run_id",
        "overlay_run_id",
    ):
        if _text(bindings.get(key), field=f"decision {key}") != _text(
            report.get(key), field=f"report {key}"
        ):
            raise MethodologyDocumentError("cross-artifact identity drift")
    if _sha(
        bindings.get("report_payload_sha256"),
        field="decision report payload SHA256",
    ) != _sha(report.get("payload_sha256"), field="report payload SHA256"):
        raise MethodologyDocumentError("cross-artifact identity drift")
    for key in (
        "report_file_sha256",
        "c5_file_sha256",
        "c5_payload_sha256",
        "c5_events_file_sha256",
        "characterization_file_sha256",
        "characterization_payload_sha256",
    ):
        _sha(bindings.get(key), field=f"decision {key}")
    _text(bindings.get("c5_run_id"), field="decision C5 run id")
    _text(decision.get("decision_run_id"), field="decision run id")
    for field in (
        "actual_decision_matrix_cell",
        "problem_verdict",
        "mechanism_status",
        "paper_claim_status",
        "live_method_status",
    ):
        value = _text(decision.get(field), field=f"decision {field}")
        if "PENDING" in value:
            raise MethodologyDocumentError(f"decision {field} is unresolved")
    boundaries = _mapping(
        decision.get("comparison_boundaries"),
        field="decision comparison boundaries",
    )
    if set(boundaries) != {
        "freshness",
        "makespan_goodput",
        "resource_comparability",
        "semantic_parity",
        "statistical_claim",
    }:
        raise MethodologyDocumentError("decision comparison boundary identity drift")
    for key, value in boundaries.items():
        if "PENDING" in _text(value, field=f"decision boundary {key}"):
            raise MethodologyDocumentError("decision comparison boundary is unresolved")
    return bindings


def _template_sha256(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _replace_exact(value: str, old: str, new: str, *, finalized: bool) -> str:
    if value.count(old) != 1:
        kind = "finalized generated block drift" if finalized else "template anchor drift"
        raise MethodologyDocumentError(kind)
    return value.replace(old, new, 1)


def _score(value: object) -> str:
    return "N/A" if value is None else f"{_number(value, field='score'):.3f}"


def _result_row(method: str, row: Mapping[str, Any]) -> str:
    freshness = _mapping(row["freshness_ns"], field=f"{method} freshness")
    valid = _integer(
        row["graph_quality_valid_judge_count"], field=f"{method} valid Judge count"
    )
    invalid = _integer(
        row["graph_quality_invalid_judge_count"],
        field=f"{method} invalid Judge count",
    )
    statuses = ", ".join(
        _text(item, field=f"{method} direct violation status")
        for item in _sequence(
            row["direct_violations_statuses"],
            field=f"{method} direct violation statuses",
        )
    )
    direct = row["direct_violations"]
    direct_text = "N/A" if direct is None else str(_integer(direct, field="direct"))
    return (
        f"| {method} | 188 | "
        f"{_number(row['successful_goodput_episodes_per_second'], field='goodput'):.6f} | "
        f"{_number(freshness['p95'], field='P95') / 1_000_000_000:.3f} | "
        f"{_number(row['makespan_ns'], field='makespan') / 1_000_000_000:.3f} | "
        f"{_number(row['evidence_recall_at_10_macro'], field='recall'):.3f} | "
        f"{_score(row['graph_quality_qa_accuracy'])} ({valid}/{valid + invalid} valid) | "
        f"{direct_text} ({statuses}) |"
    )


def _diagnostic_row(method: str, row: Mapping[str, Any]) -> str:
    freshness = _mapping(row["freshness_ns"], field=f"{method} freshness")
    work = _mapping(row["work_volume"], field=f"{method} work volume")
    graph = _mapping(row["final_graph"], field=f"{method} final graph")
    workers = ",".join(
        str(_integer(item, field=f"{method} worker count"))
        for item in _sequence(
            row["configured_worker_counts"], field=f"{method} workers"
        )
    )
    backlog = row["max_backlog"]
    backlog_text = "N/A" if backlog is None else str(
        _integer(backlog, field=f"{method} max backlog")
    )
    backlog_status = _text(
        row.get("max_backlog_status"), field=f"{method} max backlog status"
    )
    overlap = str(row["overlap_observed"]).lower()
    return (
        f"| {method} diagnostics | "
        f"{_number(freshness['p99'], field='P99') / 1_000_000_000:.3f} | "
        f"{backlog_text} ({backlog_status}) | {row['observed_max_active_calls']} | {overlap} | "
        f"{workers} | {work['llm_logical_calls']} | "
        f"{work['llm_input_tokens']}/{work['llm_output_tokens']} | "
        f"{work['embedding_calls']}/{work['embedding_items']} | "
        f"{work['db_operations']}/{work['db_transactions']} | "
        f"{work['candidate_count']} | {graph['node_count_sum']} | "
        f"{graph['relationship_count_sum']} |"
    )


def _replacement_pairs(
    report: Mapping[str, Any],
    decision: Mapping[str, Any],
    methods: Mapping[str, Mapping[str, Any]],
    bindings: Mapping[str, Any],
    *,
    template_sha256: str,
) -> tuple[tuple[str, str], ...]:
    decision_run_id = _text(decision["decision_run_id"], field="decision run id")
    decision_payload = _sha(
        decision["payload_sha256"], field="decision payload SHA256"
    )
    report_run_id = _text(report["report_run_id"], field="report run id")
    native_run_id = _text(report["native_run_id"], field="native run id")
    suite_run_id = _text(report["suite_run_id"], field="suite run id")
    overlay_run_id = _text(report["overlay_run_id"], field="overlay run id")

    pending_status = "> Status: `EVIDENCE_PENDING`"
    final_status = (
        "> Status: `DESIGN_COMPLETE`\n"
        f"> Renderer template SHA256: `{template_sha256}`\n"
        f"> Methodology decision run: `{decision_run_id}`\n"
        f"> Methodology decision payload SHA256: `{decision_payload}`"
    )
    pending_intro = (
        "本文档已经移除“先设计完整方法、再寻找支持证据”的旧顺序。当前结构、源码边界、\n"
        "> TDD gate 和证伪规则可以先冻结；三基础 baseline 与 graph-native overlay 的数值、\n"
        "> 最终研究裁决和是否进入方法实现，必须等待 sealed development report 后填写。"
    )
    final_intro = (
        "本文档已经移除“先设计完整方法、再寻找支持证据”的旧顺序。结构、源码边界、\n"
        "> TDD gate 和证伪规则与 sealed development report/decision 确定性绑定；本文的\n"
        "> development 裁决不自动授权 live 方法实现或论文结论。"
    )
    pending_evidence = """```text
Native U0 run                         PENDING_SEALED_REPORT
Three-baseline suite                 PENDING_SEALED_REPORT
Graph-native quality overlay         PENDING_SEALED_REPORT
Development aggregate report         PENDING_SEALED_REPORT
Native characterization C2/C3        SEALED
Whole-update C5 counterexample        SEALED
```"""
    final_evidence = f"""```text
Native U0 run                         `{native_run_id}`
Three-baseline suite                 `{suite_run_id}`
Graph-native quality overlay         `{overlay_run_id}`
Development aggregate report         `{report_run_id}`
Native characterization C2/C3        SEALED file=`{bindings['characterization_file_sha256']}` payload=`{bindings['characterization_payload_sha256']}`
Whole-update C5 counterexample        SEALED run=`{bindings['c5_run_id']}` file=`{bindings['c5_file_sha256']}` payload=`{bindings['c5_payload_sha256']}` events=`{bindings['c5_events_file_sha256']}`
Methodology decision                  SEALED run=`{decision_run_id}` payload=`{decision_payload}`
```"""
    pending_bindings = """```text
report_run_id                         PENDING
native_run_id                         PENDING
suite_run_id                          PENDING
overlay_run_id                        PENDING
development report file SHA256        PENDING
development report payload SHA256     PENDING
methodology_decision_run_id            PENDING
methodology decision payload SHA256    PENDING
C5 run_id                              PENDING
C5 file SHA256                         PENDING
C5 payload SHA256                      PENDING
C5 events file SHA256                  PENDING
characterization file SHA256           PENDING
characterization payload SHA256        PENDING
```"""
    final_bindings = f"""```text
report_run_id                         `{report_run_id}`
native_run_id                         `{native_run_id}`
suite_run_id                          `{suite_run_id}`
overlay_run_id                        `{overlay_run_id}`
development report file SHA256        `{bindings['report_file_sha256']}`
development report payload SHA256     `{bindings['report_payload_sha256']}`
methodology_decision_run_id            `{decision_run_id}`
methodology decision payload SHA256   `{decision_payload}`
C5 run_id                              `{bindings['c5_run_id']}`
C5 file SHA256                         `{bindings['c5_file_sha256']}`
C5 payload SHA256                      `{bindings['c5_payload_sha256']}`
C5 events file SHA256                  `{bindings['c5_events_file_sha256']}`
characterization file SHA256           `{bindings['characterization_file_sha256']}`
characterization payload SHA256        `{bindings['characterization_payload_sha256']}`
```"""
    pending_rows = """| U0 | 188 | PENDING | PENDING | PENDING | PENDING | PENDING | PENDING |
| A0 | 188 | PENDING | PENDING | PENDING | PENDING | PENDING | PENDING |
| P(C=2) | 188 | PENDING | PENDING | PENDING | PENDING | PENDING | PENDING |"""
    final_rows = "\n".join(_result_row(method, methods[method]) for method in METHODS)
    pending_diagnostics = """| U0 diagnostics | PENDING | PENDING | PENDING | PENDING | PENDING | PENDING | PENDING | PENDING | PENDING | PENDING | PENDING | PENDING |
| A0 diagnostics | PENDING | PENDING | PENDING | PENDING | PENDING | PENDING | PENDING | PENDING | PENDING | PENDING | PENDING | PENDING |
| P(C=2) diagnostics | PENDING | PENDING | PENDING | PENDING | PENDING | PENDING | PENDING | PENDING | PENDING | PENDING | PENDING | PENDING |"""
    final_diagnostics = "\n".join(
        _diagnostic_row(method, methods[method]) for method in METHODS
    )
    pending_states = """```text
actual decision-matrix cell = PENDING_SEALED_REPORT
problem_verdict       = PENDING_SEALED_REPORT
mechanism_status      = CANDIDATE_ONLY
paper_claim_status    = NOT_AUTHORIZED
live_method_status    = NOT_AUTHORIZED

freshness_comparison
  = NOT_CROSS_METHOD_COMPARABLE_CURRENT_ARRIVAL_SEMANTICS

makespan_goodput_comparison
  = DESCRIPTIVE_BURST_DRAIN_DEVELOPMENT_CAPACITY

resource_comparability
  = NOT_ESTABLISHED_UNIFIED_REQUEST_ADMISSION_ABSENT

semantic_parity
  = NOT_AUTHORIZED_LIVE_MODEL_OUTPUTS_NOT_CAPTURE_REPLAY_FIXED

statistical_claim
  = NOT_AUTHORIZED_NO_REPEATS_DEVELOPMENT_ONLY
```"""
    boundaries = _mapping(
        decision["comparison_boundaries"], field="decision comparison boundaries"
    )
    final_states = f"""```text
actual decision-matrix cell = `{decision['actual_decision_matrix_cell']}`
problem_verdict       = `{decision['problem_verdict']}`
mechanism_status      = `{decision['mechanism_status']}`
paper_claim_status    = `{decision['paper_claim_status']}`
live_method_status    = `{decision['live_method_status']}`

freshness_comparison
  = `{boundaries['freshness']}`

makespan_goodput_comparison
  = `{boundaries['makespan_goodput']}`

resource_comparability
  = `{boundaries['resource_comparability']}`

semantic_parity
  = `{boundaries['semantic_parity']}`

statistical_claim
  = `{boundaries['statistical_claim']}`
```"""
    pending_artifacts = f"""等待自动链生成后绑定：

```text
paper-eval-v3/artifacts/paper_eval/baseline_suite/runs/
  bs-dev-20260816-001/THREE_BASELINE_RESULTS.json
paper-eval-v3/artifacts/paper_eval/graph_quality_overlay/runs/
  gq-dev-20260817-001/GRAPH_QUALITY_RESULTS.json
paper-eval-v3/artifacts/paper_eval/development_report/runs/
  report-dev-20260817-001/REPORT.json
MemBind_THREE_BASELINE_DEVELOPMENT_EXPERIMENT_REPORT_20260817.md
```"""
    final_artifacts = f"""本轮 finalizer 已绑定：

```text
paper-eval-v3/artifacts/paper_eval/baseline_suite/runs/
  {suite_run_id}/THREE_BASELINE_RESULTS.json
paper-eval-v3/artifacts/paper_eval/graph_quality_overlay/runs/
  {overlay_run_id}/GRAPH_QUALITY_RESULTS.json
paper-eval-v3/artifacts/paper_eval/development_report/runs/
  {report_run_id}/REPORT.json
paper-eval-v3/artifacts/paper_eval/methodology_finalization/runs/
  {decision_run_id}/METHODOLOGY_DECISION.json
MemBind_THREE_BASELINE_DEVELOPMENT_EXPERIMENT_REPORT_20260817.md
```

绑定摘要：report payload `{bindings['report_payload_sha256']}`；decision payload
`{decision_payload}`；C5 file `{bindings['c5_file_sha256']}`；characterization file
`{bindings['characterization_file_sha256']}`。"""
    pending_close = (
        "最终转为 `DESIGN_COMPLETE` 前，必须写入上述 report identities、真实三方法数值、\n"
        "graph-quality denominator、实际 decision-matrix cell，并使\n"
        "`tests/test_final_methodology_document.py` 从 RED 转为 GREEN。"
    )
    final_close = (
        "本文档已写入上述 report identities、真实三方法数值、graph-quality denominator \n"
        "和 actual decision-matrix cell。`DESIGN_COMPLETE` 只表示 development methodology \n"
        "文档完整；live method 与 paper claim 仍服从上面的未授权状态和 TDD gate。"
    )
    return (
        (pending_status, final_status),
        (pending_intro, final_intro),
        (pending_evidence, final_evidence),
        (pending_bindings, final_bindings),
        (pending_rows, final_rows),
        (pending_diagnostics, final_diagnostics),
        (pending_states, final_states),
        (pending_artifacts, final_artifacts),
        (pending_close, final_close),
    )


def _restore_finalized_template(
    document: str,
    report: Mapping[str, Any],
    decision: Mapping[str, Any],
    methods: Mapping[str, Mapping[str, Any]],
    bindings: Mapping[str, Any],
) -> str:
    match = _FINAL_BINDING.search(document)
    if match is None:
        raise MethodologyDocumentError("finalized binding drift")
    if (
        match.group("run") != decision["decision_run_id"]
        or match.group("payload") != decision["payload_sha256"]
    ):
        raise MethodologyDocumentError("finalized evidence binding drift")
    template_hash = match.group("template")
    restored = document
    for old, new in reversed(
        _replacement_pairs(
            report,
            decision,
            methods,
            bindings,
            template_sha256=template_hash,
        )
    ):
        restored = _replace_exact(restored, new, old, finalized=True)
    if _template_sha256(restored) != template_hash:
        raise MethodologyDocumentError("finalized template drift")
    return restored


def restore_methodology_template(
    document: str,
    report: Mapping[str, Any],
    decision: Mapping[str, Any],
) -> str:
    """Verify a finalized document and recover its exact pending template."""

    if not isinstance(document, str) or not document:
        raise MethodologyDocumentError("methodology document is invalid")
    selected_report = _mapping(report, field="report")
    selected_decision = _mapping(decision, field="decision")
    _verify_seal(selected_report, label="report")
    _verify_seal(selected_decision, label="decision")
    methods = _validate_report(selected_report)
    bindings = _validate_decision(selected_decision, selected_report)
    if "> Status: `DESIGN_COMPLETE`" not in document:
        raise MethodologyDocumentError("methodology document is not finalized")
    return _restore_finalized_template(
        document,
        selected_report,
        selected_decision,
        methods,
        bindings,
    )


def render_methodology_document(
    document: str,
    report: Mapping[str, Any],
    decision: Mapping[str, Any],
) -> str:
    """Render a pending document or verify an already-finalized document."""

    if not isinstance(document, str) or not document:
        raise MethodologyDocumentError("methodology document is invalid")
    selected_report = _mapping(report, field="report")
    selected_decision = _mapping(decision, field="decision")
    _verify_seal(selected_report, label="report")
    _verify_seal(selected_decision, label="decision")
    methods = _validate_report(selected_report)
    bindings = _validate_decision(selected_decision, selected_report)

    pending = "> Status: `EVIDENCE_PENDING`" in document
    finalized = "> Status: `DESIGN_COMPLETE`" in document
    if pending == finalized:
        raise MethodologyDocumentError("methodology status or template drift")

    if pending:
        template_hash = _template_sha256(document)
        result = document
        for old, new in _replacement_pairs(
            selected_report,
            selected_decision,
            methods,
            bindings,
            template_sha256=template_hash,
        ):
            result = _replace_exact(result, old, new, finalized=False)
        if "PENDING" in result or "EVIDENCE_PENDING" in result:
            raise MethodologyDocumentError("template contains unresolved fields")
        return result

    restored = _restore_finalized_template(
        document,
        selected_report,
        selected_decision,
        methods,
        bindings,
    )
    rerendered = render_methodology_document(restored, selected_report, selected_decision)
    if rerendered != document:
        raise MethodologyDocumentError("finalized document drift")
    return document


__all__ = [
    "METHODS",
    "MethodologyDocumentError",
    "render_methodology_document",
    "restore_methodology_template",
]
