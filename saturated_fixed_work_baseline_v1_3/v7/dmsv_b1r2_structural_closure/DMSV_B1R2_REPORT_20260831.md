# DMSV B1R2 Structural Closure Report

`INPUT_COMMIT=37871aae8193d994a1642605e3a705712dd786e1`
`PREREG_COMMIT=5031f10dcd37df1f6f199ee1125e1fae1760d580`
`PREREG_SHA256=109be8244da388106a47574c14e4002b3f0a53ad1a07530e9faf31aea3e72fb4`
`FINAL_STATE=BLOCKED_STRUCTURAL_CLOSURE_INCOMPLETE`

## State vector

`PHASE2B_STATE=BLOCKED`
`BASE_VIEW_STATUS=BLOCKED_NO_PROVEN_PATH`
`SEMANTIC_ROOT_STATUS=SEMANTIC_ROOT_V6_SPECIFIC`
`NODE_REQUEST_STATUS=DIRTY_WITNESS_INCOMPLETE`
`NATIVE_LOCALIZATION_STATUS=UNPROVEN`
`ARTIFACT_PORTABILITY_STATUS=SELF_CONTAINED_SANITIZED`
`MAIN_TRACK_CANDIDATE=false`
`B2_AUTHORIZED=false`
`B3_AUTHORIZED=false`
`PHASE3A_AUTHORIZED=false`
`PHASE3B_AUTHORIZED=false`
`LIVE_AUTHORIZED=false`
`TOPK_MAINTAINER_AUTHORIZED=false`

## Workplan and existing evidence

Section 50 of `workplan_v7.md` is the current authoritative B1R2 entry. The
preregistration was committed before any result artifact. Existing Phase 2B
closure, sensitivity matrix, correction report, prior causal witness, scope
audit and repair ledger were reused without rerunning their experiments. The
prior BaseView blocker remains unchanged: `BV-NATIVE=FAIL`,
`BV-VERSIONED=UNKNOWN`, `BV-PERSISTENT=UNKNOWN`.

## Semantic root

`strip_certified_previous_context` applies only to the four extraction
callsites; it does not include `dedupe_nodes.nodes`. However, removing previous
context changes upstream extraction inputs, while the Node adapter still passes
state-dependent `previous_episodes` into the native Graphiti prompt. The root is
therefore `SEMANTIC_ROOT_V6_SPECIFIC`, not Native-equivalent. B0 remains the
headline performance baseline.

## Eligible population and theorem

One non-held-out source-4 development pair was inspected. It has a real state
version transition, previous-window/projection change and changed Node request
digest. It fails closed because E2, E3, E4, E5, E6, E7, E8, E10, E11, E12 and
E13 are `UNKNOWN`; no complete eligible pair exists. Counts are therefore:

`candidate=1; complete=0; dirty=0; stable=0; unknown/ineligible=1`.

The previous-window result is recorded as a conditional static theorem covering
both non-full and full windows. Selector miss, reference-time miss, `last_n=0`,
ties, identical projections, omitted calls, epoch changes and V6 context removal
remain explicit counterexamples or unresolved obligations. L1 sensitivity is
reused; L2-L5 are not claimed. No dirty rate or structural ALWAYS_DIRTY theorem
is computed.

## Native localization

Graphiti's native adapter merges unresolved candidates and sends one joint
`dedupe_nodes.nodes` prompt with a joint `NodeResolutions` response. No native
per-node binding or partial-batch seam is exposed by the inspected code. Batch
splitting, previous-context removal, prompt/schema rewrites and assumed output
equivalence would require `NEW_ALGORITHM_IDENTITY_REQUIRED`. Because L4 is not
established, the final state is not the stronger structural NULL.

## Self-contained evidence

`DMSV_B1R2_SELF_CONTAINED_WITNESSES.jsonl` contains only opaque UUID digests,
request/projection digests, state versions and provenance metadata. It contains
no raw episode content, prompt, model output, credentials or absolute path.
The original external observer is referenced only by basename, schema and
content SHA-256. Evidence-required clean-checkout reproduction is performed by
the B1R2 test suite against this repository-local bundle.

## Stop

Provider calls: `0`. Database writes: `0`. Held-out histories accessed: `false`.
No B2/B3, Phase 3A/3B, live treatment, extraction experiment, Top-K maintainer,
batch split or scheduler search was executed. This report is the terminal B1R2
result for the current identity.
