#!/usr/bin/env python3
"""Run the isolated, offline MSEG design qualification.

This command is intentionally a read-only qualification boundary.  It builds
small in-memory contracts, audits one existing sealed request trace, and
writes a separate design-only report.  It never imports a runtime runner,
starts a service, contacts a backend, or changes a sealed artifact.
"""

from __future__ import annotations

import argparse
import json
import sys
from enum import Enum
from pathlib import Path
from typing import Any


PROJECT = Path(__file__).resolve().parents[1]
SRC = PROJECT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from paper_eval.artifacts import atomic_write_json, payload_sha256  # noqa: E402
from paper_eval.membind_v4.mseg.offline_qualification import (  # noqa: E402
    QualificationCase,
    gate_real_trace,
    qualify_synthetic,
)
from paper_eval.membind_v4.mseg.reducer import (  # noqa: E402
    audit_llm_trace_observability,
)
from paper_eval.membind_v4.mseg.semantic_contract import (  # noqa: E402
    EffectContract,
    EffectKind,
    OperatorType,
    SemanticContract,
    SemanticOperator,
    StateContract,
    Visibility,
)
from paper_eval.membind_v4.mseg.semantic_evidence import (  # noqa: E402
    AdapterProvenance,
    CertificationLevel,
    EffectJournalEntry,
    ExecutionEvidence,
    PublicationEvidence,
)


HISTORY_ID = "07741c45"
DEFAULT_TRACE = (
    PROJECT
    / "artifacts/paper_eval/membind_v4/mseg/q0"
    / "membind-v31-opt-w4-q0-20260820-001/llm.jsonl"
)
DEFAULT_OUTPUT_ROOT = (
    PROJECT / "design_only/mseg_qualification_20260820"
)


def _contract(*, entity: str, publication: bool) -> SemanticContract:
    namespace = "synthetic-history"
    return SemanticContract(
        contract_id="synthetic.mseg.v0",
        operator_type=(
            OperatorType.PUBLICATION if publication else OperatorType.RESOLUTION
        ),
        state=StateContract.bound(
            namespace=namespace,
            version="m-1",
            read_scope={f"entity:{entity}"},
        ),
        effect=EffectContract.write(
            namespace=namespace,
            kind=EffectKind.UPDATE,
            scope={f"entity:{entity}"},
        ),
        visibility=(
            Visibility.PUBLISHED_STATE
            if publication
            else Visibility.PRIVATE_INTERMEDIATE
        ),
        atomic=True,
        idempotent=True,
        retry_safe=True,
        publication_boundary=publication,
    )


def _operator(*, instance_id: str, entity: str, publication: bool) -> SemanticOperator:
    return SemanticOperator(
        instance_id=instance_id,
        semantic_identity=f"synthetic-identity:{instance_id}",
        evidence_ids=(f"evidence:{instance_id}",),
        contract=_contract(entity=entity, publication=publication),
        control_predecessors=frozenset(),
    )


def _evidence(operator: SemanticOperator, *, publication: bool) -> ExecutionEvidence:
    namespace = operator.contract.effect.namespace
    entity_scope = operator.contract.effect.scope
    journal = EffectJournalEntry(
        effect_id=f"effect:{operator.instance_id}",
        operator_instance_id=operator.instance_id,
        kind=operator.contract.effect.kind,
        namespace=namespace,
        scope=entity_scope,
        committed=publication,
        transaction_id=(f"tx:{operator.instance_id}" if publication else None),
        timestamp_ns=20,
        durable=publication,
    )
    publication_evidence = None
    if publication:
        publication_evidence = PublicationEvidence(
            publication_id=f"publication:{operator.instance_id}",
            operator_instance_id=operator.instance_id,
            predecessor_version="m-1",
            published_version="m-2",
            durable=True,
            timestamp_ns=30,
            frontier_position=1,
        )
    state = operator.contract.state
    return ExecutionEvidence(
        instance_id=operator.instance_id,
        semantic_identity=operator.semantic_identity,
        state_version=state.version,
        read_scope=state.read_scope,
        provenance=AdapterProvenance(
            adapter_id="synthetic-adapter",
            backend_name="synthetic",
            backend_version="0",
            contract_id=operator.contract.contract_id,
            schema_fingerprint="synthetic-schema-v0",
            source_fingerprint="synthetic-source-v0",
            level=CertificationLevel.VALIDATED,
        ),
        effect_journal=(journal,),
        publication=publication_evidence,
        terminal=True,
        child_identity_complete=True,
        hidden_effects_possible=False,
    )


def _synthetic_cases() -> tuple[QualificationCase, ...]:
    # The first two operators are reorderable private work on disjoint keys.
    # The third checks the separate durable-publication certification path.
    definitions = (
        ("private-left", "left", False),
        ("private-right", "right", False),
        ("durable-publication", "published", True),
    )
    return tuple(
        QualificationCase(
            label=instance_id,
            operator=(operator := _operator(
                instance_id=instance_id,
                entity=entity,
                publication=publication,
            )),
            evidence=_evidence(operator, publication=publication),
        )
        for instance_id, entity, publication in definitions
    )


def _enum_value(value: object) -> object:
    return value.value if isinstance(value, Enum) else value


def _result_payload(result: object) -> dict[str, object]:
    return {
        "status": _enum_value(result.status),  # type: ignore[attr-defined]
        "codes": list(result.codes),  # type: ignore[attr-defined]
    }


def _synthetic_payload(result: object) -> dict[str, object]:
    return {
        "decision": _enum_value(result.decision),  # type: ignore[attr-defined]
        "status_counts": dict(result.status_counts),  # type: ignore[attr-defined]
        "reorder_counts": dict(result.reorder_counts),  # type: ignore[attr-defined]
        "case_results": {
            label: _result_payload(case_result)
            for label, case_result in result.case_results.items()  # type: ignore[attr-defined]
        },
        "reasons": list(result.reasons),  # type: ignore[attr-defined]
    }


def _markdown(payload: dict[str, Any]) -> str:
    synthetic = payload["synthetic"]
    real_trace = payload["real_trace"]
    decision = payload["decision"]
    lines = [
        "# MSEG Design Qualification",
        "",
        "This is an isolated offline qualification record. It does not authorize a runtime policy.",
        "",
        "## Synthetic contract gate",
        "",
        f"- decision: `{synthetic['decision']}`",
        f"- status counts: `{json.dumps(synthetic['status_counts'], sort_keys=True)}`",
        f"- reorder counts: `{json.dumps(synthetic['reorder_counts'], sort_keys=True)}`",
        "- private operators use the same state version and disjoint effect scopes.",
        "- the publication case has an exact durable effect journal and publication record.",
        "",
        "## Real trace gate",
        "",
        f"- source: `{real_trace['trace_path']}`",
        f"- request count: `{real_trace['observability']['request_count']}`",
        f"- MSEG recovered: `{real_trace['mseg_recovered']}`",
        "- the trace is read-only; no request is sent to an LLM or database.",
        "",
        "## Decision",
        "",
        f"- status: `{decision['status']}`",
        f"- reasons: `{json.dumps(decision['reasons'], sort_keys=True)}`",
        f"- live authorized: `{decision['live_authorized']}`",
        f"- new scheduler authorized: `{decision['new_scheduler_authorized']}`",
        "",
        "No live service was contacted.",
        "",
        "The real trace remains below the exact state/effect/publication observability gate; this result does not imply absence of an unobserved opportunity.",
        "",
    ]
    return "\n".join(lines)


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--output-root",
        type=Path,
        default=DEFAULT_OUTPUT_ROOT,
        help="separate directory for design-only JSON and Markdown outputs",
    )
    parser.add_argument(
        "--trace",
        type=Path,
        default=DEFAULT_TRACE,
        help="existing sealed llm.jsonl to audit without executing it",
    )
    parser.add_argument("--history-id", default=HISTORY_ID)
    return parser.parse_args()


def main() -> int:
    args = _parse_args()
    trace = args.trace if args.trace.is_absolute() else (Path.cwd() / args.trace)
    output_root = (
        args.output_root
        if args.output_root.is_absolute()
        else (Path.cwd() / args.output_root)
    )
    if not trace.is_file():
        raise SystemExit(f"trace_not_found:{trace}")

    cases = _synthetic_cases()
    synthetic = qualify_synthetic(
        cases,
        reorder_pairs=(("private-left", "private-right"),),
    )
    observability = audit_llm_trace_observability(trace, history_id=args.history_id)
    real_trace = {
        "trace_path": str(trace),
        "history_id": args.history_id,
        "mseg_recovered": bool(observability.get("mseg_recovered") is True),
        "observability": observability,
    }
    gate = gate_real_trace(synthetic, observability)
    decision = {
        "status": _enum_value(gate.decision),
        "reasons": list(gate.reasons),
        "synthetic_decision": _enum_value(gate.synthetic_decision),
        "live_authorized": bool(gate.live_authorized),
        "new_scheduler_authorized": False,
    }
    body: dict[str, Any] = {
        "schema_version": "membind.paper-eval-v4.mseg-design-qualification.v1",
        "analysis_mode": "OFFLINE_READ_ONLY",
        "scope": {
            "network_calls": 0,
            "services_started": 0,
            "persistent_writes": 0,
            "sealed_artifacts_modified": False,
            "runtime_policy_modified": False,
        },
        "synthetic": _synthetic_payload(synthetic),
        "real_trace": real_trace,
        "decision": decision,
    }
    body["payload_sha256"] = payload_sha256(body)
    atomic_write_json(output_root / "MSEG_DESIGN_QUALIFICATION.json", body)
    markdown_payload = dict(body)
    markdown_payload.pop("payload_sha256", None)
    output_root.mkdir(parents=True, exist_ok=True)
    (output_root / "MSEG_DESIGN_QUALIFICATION.md").write_text(
        _markdown(markdown_payload), encoding="utf-8"
    )
    print(json.dumps(decision, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
