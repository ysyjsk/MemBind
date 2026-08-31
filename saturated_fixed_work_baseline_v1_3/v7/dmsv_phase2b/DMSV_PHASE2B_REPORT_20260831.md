# MemBind DMSV Phase 2B Report

Date: 2026-08-31  
Final state: `BLOCKED`  
Scope: provider-free B0/B1 closure only; B4 stop condition reached.

## Executive decision

The current DMSV identity is **not** a `MAIN_TRACK_CANDIDATE`. The existing
V6 timeline does not provide a generally timely BaseView, and Graphiti's
dominant `dedupe_nodes.nodes` batch request changes when mutable state or any
other tested logical input changes. B2/B3 were therefore not authorized or
executed. This report makes no online speedup claim and does not reject future
work under a new semantic boundary.

## Audited identity

- Commit/branch: `f91a0500beb87d5013644442e135e6d3afb4507c` / `main`.
- Remote: `origin/HEAD` and `origin/main` resolve to the same commit.
- Graphiti: `0.29.3`.
- Profile: `local-qwen3-8b-awq-dualreplica-v1`.
- B0: `NATIVE_SERIAL/d6e9e240c3ce`, fixed and not rerun.
- Workplan: `workplan_v7.md` with the DMSV Phase 2B freeze section.
- Frozen workplan SHA-256: `7fc39e6d8dff3eda48c948683294f4a109c35f2f317ea4eda4c1171a565e6330`.

## BaseViewAvailability closure

| Path | Verdict | Evidence |
|---|---|---|
| `BV-NATIVE` | `FAIL` for a general path | Existing recovered 29 pair clocks show only `1/29` ready before predecessor publication; authoritative publication cannot be delayed to create a window. |
| `BV-VERSIONED` | `UNKNOWN` | No artifact proves consistent snapshot/epoch binding, lifecycle/GC, failure handling, initial materialization, or per-delta maintenance cost. |
| `BV-PERSISTENT` | `UNKNOWN` | No artifact proves cross-source query coverage, staleness bounds, maintenance/storage/GC, or timely availability. |

Aggregate: `BLOCKED`, because no BaseView route is proven legal and timely. The
result is evidence-bounded: it does not claim versioned/persistent designs are
mathematically impossible.

## Dominant request closure

The provider-free matrix calls the actual Graphiti 0.29.3 prompt builder. Each
of the following changed the complete canonical request digest:

`candidate payload`, `Top-K order`, `Top-K membership`, `previous_episodes`,
`unresolved membership/batch shape`, `current episode`, `model epoch`,
`config epoch`, `schema epoch`, `index epoch`.

Therefore `Top-K IDs equal` is not a valid reuse certificate. The current native
batch call has no proven localization seam that preserves the original call
boundary and B0 semantics. Independent closure verdict:
`DMSV_DOMINANT_CALL_UNAVOIDABLE`.

## Phase decision table

| Stage | Status | Reason |
|---|---|---|
| B0 preregistration | `PASS` | Contract, oracle, unknown/fallback and forbidden actions frozen. |
| B1 BaseView closure | `BLOCKED` | BV-NATIVE fails general timing; versioned/persistent proof missing. |
| B1 dominant request closure | `PASS_PROVIDER_FREE` / unavoidable | Full request closure changes under all tested mutations. |
| B2 Top-K maintainer TDD | `NOT_EXECUTED` | Requires `MAIN_TRACK_CANDIDATE`; not authorized. |
| B3 affectedness/economics TDD | `NOT_EXECUTED` | Requires B2 exactness and main-track candidate. |
| B4 seal | `COMPLETE` | This report and artifacts seal the negative/blocked decision. |

## Integrity and forbidden-action proof

Provider calls: `0` in this Phase 2B run. Database writes: `0`. Held-out
histories read: `false`. No B0/Frozen V6 file was modified. No live Graphiti
treatment, Phase 3A/3B observer, scheduler search, Top-K maintainer, or full
evaluation was started. Existing 2-source corrective observer output remains
diagnostic-only and is not used as Phase 2B evidence.

## Verification results

The new DMSV provider-free tests pass (`3 passed`). The relevant DMSV and
corrective observer/window/cross-snapshot regression slice also passes (`160 passed`, one
existing Pydantic deprecation warning). Python compilation and `git diff --check`
pass. The full repository suite reports `718 passed, 7 failed`; all seven
failures occur before test logic in pre-existing historical source-hash freeze
bindings (`development_campaign`, `engineering_observer_runtime`, and strict/
selected campaign tests). The frozen files' expected hashes predate the current
worktree and were not rewritten to hide this drift; this is retained as an
engineering readiness warning, not converted into a DMSV scientific pass.

## Minimum falsifiable release conditions

To reopen DMSV under a new identity, a future design must first provide a
provider-free or explicitly authorized minimal artifact that proves one BV path
is timely, snapshot-safe, lifecycle-safe, and economically accounted. It must
also preserve or legally localize the complete native `dedupe_nodes.nodes`
request; changing the Graphiti call boundary requires a new algorithm identity
and fresh quality/differential qualification. Until those conditions are met,
the current state remains `BLOCKED`, and no Phase 3 authorization exists.
