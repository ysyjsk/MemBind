# V7 Conditional Core Semantics

This document is the P0-P6/P6c/P6b freeze artifact. It is a conditional
theory, not a claim that Graphiti has already discharged every refinement.
The executable reference is `src/.../membind_v7` and has no provider or
Graphiti imports.

## Scope and trace

The state transition is one delta only: `d=1`, `S1 = S0 xor Delta`. A
semantic trace is `G=(V,E,eta,nu,lambda)` where nodes are Input, Read, Pure,
Demand, Response, Control and (M2 only) Plan. Edges are typed data, control,
existence, ordered-collection, environment/oracle, or effect/publication
edges. Async completion order is not an edge unless it enters a prompt,
candidate order, branch, or effect.

`BuildTrace(S,e)` is read-only through the maintained seam and returns
`(tau,z)`. `NativeContinue(S,e,z)` owns the unmodified native tail. M1 uses
that continuation; M2 additionally stages a closed plan and ordered apply.

## Canonical relations

`alpha_equivalent` removes runtime-only UUID fields but retains logical IDs,
ordered collections, prompt-visible projections and effect/idempotency keys.
T6b uses the stronger seam relation `alpha_equivalent_K`, which explicitly
retains every continuation-observable field. Runtime object identity and task
completion timing are not semantic observations.

## Theorem obligations

* **T1 Snapshot soundness.** Every Read node and the seam carry the same
  immutable snapshot token. A state write before the seam is prohibited by A2.
* **T2 Scoped delta completeness.** For supported operator `rho`, every
  observable writer and primitive has a local extractor. `Complete_rho` is
  checked only for the selected operator/region; an unrelated UNKNOWN
  operator cannot poison it. Missing after-values or epochs return UNKNOWN.
* **T3 Read certificate soundness.** `STABLE` implies the fresh read on
  `S0 xor Delta` has the same membership, projection and consumer-visible
  order. Exact key, predicate and guarded full-scan top-k are separate
  certificate classes. Short top-k results have no invented cutoff. BM25,
  hybrid/RRF and ANN remain UNKNOWN without index/statistics/tie/backend
  contracts.
* **T4 Adaptive demand validity.** Existence, binding, semantic predecessor
  context, deterministic builder and canonical request identity must all be
  stable. Previous-episode retrieval is a state-dependent input. A stable
  request does not authorize response replay unless a declaration-backed
  `ReplayAllowed=true` provider contract exists.
* **T5 Propagation and reconvergence.** A finite guarded worklist repairs the
  least affected closure. A repaired node whose canonical output is unchanged
  reconverges and does not dirty its successors. A15 requires a finite
  well-founded measure; cycles or an exhausted bound fail closed.
* **T6 Trace FSC.** For frozen or coupled-legal oracle choices,
  `MaintainTrace(BuildTrace(S0,e),Delta)` is alpha-equivalent to a fresh trace
  on `S0 xor Delta`. All UNKNOWN/ambiguous regions execute fresh.
* **T6b Native continuation congruence.** If authoritative seam states are
  alpha-equivalent, have the same frontier/version, and seam outputs are
  `alpha_equivalent_K`, then every native continuation step observes only the
  declared state/environment/oracle and emits alpha-equivalent effects. The
  proof is step-local and requires a P7 source audit; ordinary alpha equality
  is insufficient when native code observes a UUID, ordered list or effect key.
* **T7/T8 (M2 only).** A plan has an exact predecessor frontier, closed
  preconditions, effects and idempotency key. Apply cannot perform hidden
  reads. A durable receipt is required before frontier advancement; recovery
  ends in complete pre-state or complete post-state, never a split.

## Fail-closed rules

`INVALID` and `UNKNOWN` invalidate only the dependent read/region when the
boundary is known. If the boundary cannot be located, the smallest enclosing
region is fresh. T6b failure blocks M1/M2 or moves the seam; T7/T8 failure
blocks M2 only. Replay contract failure forces a fresh provider response but
does not invalidate an otherwise stable read/request construction.

The conditional theory is frozen only with the assumption use-map,
counterexamples, reference differential, no-treatment CI guard and P7 status
artifact present. It does not license a runtime treatment before R3.
