# Current Validation v1.2 Retained-Closure Review

Reviewed: 2026-08-07

## Verdict

The user's revised direction is adopted. It improves causal precision while
reducing work that cannot change the pilot decision.

The central correction is that a new embedding run cannot recover the raw
vectors from the retained smoke14 runs. Re-sampling would quantify a third and
fourth execution, not the historical divergence. V1 therefore closes only from
retained artifacts and explicitly records unavailable numerical fields.

## Adopted Changes

1. V1 is a read-only `retained-artifact closure`. It makes no model, Neo4j, or
   network calls and produces no raw-vector artifact.
2. Cross-run cosine, L2, max component difference, component-change count, and
   exact per-cosine-query ranking deltas are recorded as
   `not_computable_from_retained_artifacts` where the required bytes/vectors or
   correlation keys were not retained.
3. The strongest V1 claim is that equal retained logical graph content can have
   different 1024-dimensional float-sequence hashes. This rejects live embedding
   as a bitwise correctness oracle. It does not establish that numerical drift
   caused a later prompt divergence.
4. M1 oracle misses become `completed_with_divergence` with outcome
   `execution_path_divergence`. They prove a changed state-dependent trajectory,
   not a final graph error. M2 oracle misses remain correctness failures and
   block performance.
5. H3 is split into H3a execution equivalence and H3b practical sufficiency.
   The existing GO threshold still requires final graph/retrieval evidence or
   insufficient M1 live performance; an M1 oracle miss alone does not silently
   change that threshold.
6. Internal method ID `M0` is retained for artifact compatibility. Its report
   label is `Deterministic-Graphiti-Serial`, explicitly identifying the shared
   candidate-ordering adapter. The current pilot does not run an upstream naming
   guardrail.
7. The instrumentation gate is a short, frozen-oracle microbenchmark covering
   M0 and M2 with four counterbalanced OFF/ON pairs per method. Per-method
   overhead above 5% blocks formal performance. The value is a preregistered
   pilot engineering limit, not a conference-wide standard.
8. Embedding cache identity uses an endpoint-reported revision or an
   operator-supplied immutable deployment fingerprint. A served alias, URL, or
   behavioral probe cannot be presented as a checkpoint fingerprint.
9. Cross-encoder status is measured from actual `rank()` calls. Object
   construction is not evidence of use.
10. The 72-run correctness-first plan, balanced 16 performance blocks,
    whole-block infrastructure replacement, treatment-failure retention, real
    LAN E2E timing, and no result-directed third repeat remain in force.

## Retained-Evidence Constraints

- The source-state `logical_graph_hash` in the old forensic artifacts includes
  embedding metadata and is not a semantic-only hash. The V1 analyzer must strip
  embedding hash/dimension/norm before comparing logical state.
- Entity/edge states can be paired by deterministic logical keys plus occurrence
  ordinal. Exact original HTTP embedding request bytes were not retained and
  must not be inferred from logical text.
- Query events cannot be paired by array index because concurrent completion
  controls append order. Cosine queries also cannot be paired by search-vector
  hash because that hash identifies the output being investigated, not its
  semantic input.
- Full-text events can be paired by source sequence, query hash, normalized query
  hash, limit, and threshold. Cosine evidence is limited to aggregate multiset
  observations unless a retained exact input correlation key exists.
- The source-8 unexpected prompt is separate downstream evidence. It demonstrates
  a prompt candidate change, not its embedding cause.

## TDD Evidence

The contract tests were extended before the plan edits.

```text
artifacts/tdd/current_validation_plan_retained_closure_red_003.log
artifacts/tdd/current_validation_plan_retained_closure_red_004.log
artifacts/tdd/current_validation_plan_retained_closure_green_005.log
```

The final focused result is 18 tests passed. The existing formal scheduler is
still the old 64-run implementation by design; `CURRENT_STATE.json` forbids V6
implementation until its stage. V6 will replace it through a separate red-test
cycle rather than an out-of-order code change.

## Next Authorized Action

Implement a pure, offline, tested V1 retained-artifact analyzer and write:

```text
artifacts/diagnostics/embedding_nondeterminism_source5.json
```

Only after that artifact passes its schema, provenance, and claim-boundary gates
may `CURRENT_STATE.json` advance to V2.
