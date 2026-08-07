# Current Validation v1.2 Review

Reviewed: 2026-08-07

## Verdict

The v1.2 direction is scientifically stronger and cheaper than continuing the
old debug/characterization queue. In particular, it correctly separates the
observed fact (live embedding vectors are not bitwise stable) from the causal
claim (that drift changed top-K and a downstream prompt), freezes external
model outputs only for correctness, keeps performance live, treats M1
completion order as diagnostic, and stops expansion after the basic pilot.

It is adopted as the authoritative execution overlay with two required
clarifications.

## Clarification 1: V1 Artifact Sufficiency

The two retained source-5 snapshots contain vector hashes, dimensions, norms,
logical state, Python/backend rankings, and candidate membership. They do not
contain raw vectors. Therefore old artifacts alone cannot produce cross-run
cosine, L2, or max-absolute deltas.

Two options were evaluated:

1. Record those metrics as not computable and close V1 from retained evidence.
2. Perform one bounded diagnostic consisting of two six-episode M0 recaptures,
   with the smoke14 LLM cache read-only, zero live LLM calls, fresh Neo4j, new
   run IDs, and source-5-only raw input/vector output.

The plan requires numerical quantification, so option 2 is selected. It does
not authorize a new full smoke or an open-ended determinism investigation.

## Clarification 2: M1 Attribution

The draft used live LLM and embedding output for the M1 semantic smoke while
using a frozen oracle only for M2. That makes an M1 graph difference
inseparable from neural sampling drift. M1 correctness now shares the same M0
LLM/embedding oracle as M2. Its live runs remain unchanged for performance.

This changes the formal nominal count from 64 to 72:

```text
24 correctness = 8 x (M0 capture + M1 replay + M2 replay)
48 performance = 8 x 3 methods x 2 repeats
72 total planned primary runs
```

The eight added M1 runs are read-only model replay and add no live model calls.

## Other Confirmed Clauses

- The embedding item key must bind model identity/revision (or an explicit
  unreported sentinel), dimension, normalization/instruction configuration,
  and exact input bytes; batch composition is not semantic.
- Cross-encoder usage must be audited from calls, not object construction. The
  pinned default construction/retrieval recipes are expected to use RRF and be
  recorded as `not_invoked`.
- V1-v7 stage gates are strict. Correctness must complete before expensive live
  performance runs.
- Performance uses balanced `(question_id, repeat)` method blocks. Clear
  infrastructure replacement applies to the whole block; treatment failures
  remain method results.
- The v1.1 C/rho/Poisson, high-frequency network, and complex telemetry campaign
  is no longer current scope. This is an explicit scope amendment, not an
  inference that those measurements lack scientific value.
- A minimal instrumentation semantic/overhead check and an upstream-vs-current
  deterministic M0 naming guard remain necessary before formal performance;
  they must not grow into the removed characterization campaign.

## TDD Evidence

The synchronization contract was written before the document changes. Its
expected red result is retained at:

```text
artifacts/tdd/current_validation_plan_v12_red_001.log
```

The pre-existing V2 embedding-cache red tests remain intentionally unimplemented
until V1 is closed.
