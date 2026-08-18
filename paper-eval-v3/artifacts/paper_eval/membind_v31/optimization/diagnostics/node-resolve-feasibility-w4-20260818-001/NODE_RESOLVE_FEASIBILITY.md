# NodeResolve Speculation Feasibility Audit

Status: `DIAGNOSTIC_ONLY`; this artifact cannot authorize or merge a live run.

## Decision

`D2_BOUNDARY_FEASIBLE_DATA_INSUFFICIENT`

The implementation boundary can support validated speculation, but the current immutable trace lacks operator-level semantic-call/state evidence needed to estimate reuse.

The probe validates the semantic-call contract offline. It does not infer a reuse rate from transport spans.

## Graphiti Source Boundary

- verdict: `NODE_RESOLVE_BOUNDARY_FEASIBLE`
- candidate materialization separate: `true`
- LLM execution separate: `true`
- LLM stage persistent-effect free: `true`
- source SHA-256: `14fc92a462bf7f1dd9b70d10a88e27e36a0ddc1594dc18381888209de7137fb4`

## Existing Trace Audit

- trace files: `3`
- rows scanned: `1599`
- verdict: `D2_DATA_INSUFFICIENT`
- missing fields: `operator_identity, state_version, candidate_order, candidate_binding, semantic_call_fingerprint`

Required fields are operator identity, predecessor state version, candidate ordering/binding, and semantic-call fingerprint.
Without them, an existing Compile/FRONTIER transport trace cannot establish NodeResolve stability.

## Phase Context (Non-attributable)

- observed Bind fraction: `0.363669`
- this is an upper bound/context only; NodeResolve is not isolated in the current trace

## Next Evidence Needed

1. Capture one content-free operator record per NodeResolve call with the required fields.
2. Persist per-prefix state/candidate materialization so stale and exact calls can be paired.
3. Run the offline reducer; only `D2_REUSE_POTENTIAL_SUPPORTED` may justify a separate V4 pilot.

Artifact payload SHA-256: `385aba60d3d2ed3f55871f090f4e7c4d3f26455fc24571dd2a665c426edd6b0c`
