# NodeResolve D2 Evidence Workplan v1

Status: `DIAGNOSTIC_LANE_ONLY`

This workplan is an independent feasibility/evidence lane for the proposed
validated NodeResolve speculation mechanism. It does not amend or authorize
the frozen MemBind v3.1 methodology, and it does not make the current W=4
pilot or any failed attempt mergeable.

## 1. Falsifiable Hypothesis

For a fixed Graphiti 0.29.3 construction workload and fixed LLM/embedding/DB
envelope:

```text
speculate NodeResolve on an older materialized state
    + validate the exact semantic request at the predecessor state
    + reuse only on an exact fingerprint match
```

can reduce NodeResolve service work and eventually reduce freshness latency,
without changing the serial reference state transition or final graph.

The hypothesis is rejected if semantic drift is frequent, if the added
speculation/validation work exceeds avoided exact work, or if the exact-state
oracle detects any state-effect difference.

## 2. Evidence Boundary

The current Graphiti source audit is positive:

```text
NODE_RESOLVE_BOUNDARY_FEASIBLE
```

`_collect_candidate_nodes` and `_resolve_with_llm` are separate stages, and
the LLM stage has no direct persistent database effect. This establishes an
implementation insertion point only; it is not a workload reuse result.

The current W=4 trace is insufficient:

```text
D2_BOUNDARY_FEASIBLE_DATA_INSUFFICIENT
```

It lacks operator identity, candidate order/binding, exact state version,
and semantic-call fingerprint. The existing Bind fraction is context only;
it must not be reported as NodeResolve cost.

## 3. D2-B Capture Contract

Add an isolated diagnostic observer around NodeResolve. The observer must be
content-free and must not alter prompts, schemas, ordering, or Graphiti state.
For every speculative and exact materialization, persist:

```text
run_id
source_sequence
predecessor_state_version
state_projection_sha256
operator_identity_sha256
candidate_order
candidate_uuid_and_canonical_projection_sha256
extracted_node_mapping
candidate_binding_context_sha256
rendered_request_sha256
token_sequence_sha256
response_schema_sha256
model_and_decoding_identity_sha256
semantic_call_fingerprint
prompt_tokens
service_span_ns
response_finish_reason
completion_tokens
```

After exact validation, persist only projections needed for the oracle:

```text
response_projection_sha256
resolved_uuid_map_sha256
duplicate_pair_projection_sha256
state_parity
validation_overhead_ns
```

Raw prompts, raw model responses, credentials, and private episode content do
not belong in the public diagnostic artifact.

## 4. D2-C Offline Replay

Use one development history first, then the remaining development histories
only if the first history has complete evidence. For each source `i`, construct
the pair:

```text
speculative: NodeResolve(E_i, M_(i-1))
exact:       NodeResolve(E_i, M_i)
```

The serial reference must remain authoritative. Candidate order, UUID,
canonical projection, extracted-node mapping, response schema, model identity,
and decoding identity are part of the semantic fingerprint. A mismatch never
gets repaired or force-reused.

The exact-state oracle checks, in order:

1. parsed response shape and finish status;
2. extracted-node to canonical-UUID mapping;
3. duplicate-pair projection;
4. state transition projection after applying the exact response;
5. final graph projection for the bounded prefix.

## 5. Pre-Registered Effectiveness Gate

For each paired call, let `H_i` indicate fingerprint reuse. The service-work
comparison is:

```text
baseline_work
  = sum(exact_service_span_i)

speculative_path_work
  = sum(speculative_service_span_i)
  + sum(validation_overhead_i)
  + sum(exact_service_span_i for H_i = false)

net_saved_service_work
  = baseline_work - speculative_path_work
  = avoided_exact_work - speculative_work - validation_work
```

No fixed 82%, 40%, or speedup threshold is used in this screening gate. The
decision is based on complete evidence and the sign of measured net work:

```text
D2_DATA_INSUFFICIENT
    required pair/oracle/overlap fields are missing

D2_UNSAFE
    any reused pair fails exact-state parity

D2_LOW_REUSE_POTENTIAL
    evidence is complete and net_saved_service_work <= 0

D2_REUSE_POTENTIAL_SUPPORTED
    evidence is complete, parity passes, and net_saved_service_work > 0

D2_REUSE_POTENTIAL_HIGH_BUT_NO_OVERLAP
    all eligible pairs reuse and net work is positive, but no useful
    scheduler overlap is observed; this supports semantic reuse only,
    not an end-to-end latency claim
```

`overlap_exposed_ns` is recorded separately. Service-work savings must never
be presented as wall-clock speedup without scheduler overlap evidence.

## 6. V4-A Bounded Pilot Gate

Only `D2_REUSE_POTENTIAL_SUPPORTED` may authorize a separate pilot. The pilot
must use:

```text
fresh run ID
fresh namespace
fresh cache salt
one development history/prefix first
one-version-ahead speculation only
same v3.1 LLM K, model, schema, arrival trace, and DB envelope
```

The pilot remains `NON_MERGEABLE` until a separate methodology amendment.
It must compare the exact serial reference against the current v3.1 path and
the new speculation path under the same source workload.

Primary pilot measurements:

```text
construction makespan
P50/P95/P99 freshness
goodput
NodeResolve service work
speculation overhead
fallback rate
state-parity violations
final graph parity
```

The pilot must stop on any direct semantic violation, incomplete operator
telemetry, provider failure, or unexplained response truncation. It must not
change `W`, `K`, completion cap, prompt/schema, or introduce Snapshot/OCC,
MVCC, selective repair, or semantic caching in this lane.

## 7. Current Decision and Next Action

Current decision:

```text
D2_BOUNDARY_FEASIBLE_DATA_INSUFFICIENT
```

The next engineering action is to add the content-free D2-B operator
observer and an offline serial-state replay fixture, using TDD before any
network request. A live V4 pilot is not yet justified by the existing W=4
transport trace.

Supporting implementation and audit artifacts:

- `src/paper_eval/membind_v31/node_resolve_speculation.py`
- `tests/test_membind_v31_node_resolve_speculation.py`
- `artifacts/paper_eval/membind_v31/optimization/diagnostics/node-resolve-feasibility-w4-20260818-001/`
