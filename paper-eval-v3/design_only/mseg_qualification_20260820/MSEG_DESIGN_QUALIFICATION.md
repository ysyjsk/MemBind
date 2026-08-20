# MSEG Design Qualification

This is an isolated offline qualification record. It does not authorize a runtime policy.

## Synthetic contract gate

- decision: `GO_OFFLINE_CERTIFIED`
- status counts: `{"CERTIFIED_PRIVATE": 2, "CERTIFIED_PUBLISHABLE": 1, "INVALID": 0, "OPAQUE": 0}`
- reorder counts: `{"CERTIFIED": 1, "CONFLICT": 0, "UNKNOWN": 0}`
- private operators use the same state version and disjoint effect scopes.
- the publication case has an exact durable effect journal and publication record.

## Real trace gate

- source: `/data/predator/ly/MemBind/paper-eval-v3/artifacts/paper_eval/membind_v4/mseg/q0/membind-v31-opt-w4-q0-20260820-001/llm.jsonl`
- request count: `193`
- MSEG recovered: `False`
- the trace is read-only; no request is sent to an LLM or database.

## Decision

- status: `STOP_REAL_TRACE_INSUFFICIENT_OBSERVABILITY`
- reasons: `["operator_identity_missing", "operator_ready_materialization_timing_missing", "memory_version_evidence_missing", "dependency_and_effect_scope_missing", "deterministic_operator_trace_missing", "persistent_effect_and_publication_trace_missing"]`
- live authorized: `False`
- new scheduler authorized: `False`

No live service was contacted.

The real trace remains below the exact state/effect/publication observability gate; this result does not imply absence of an unobserved opportunity.
