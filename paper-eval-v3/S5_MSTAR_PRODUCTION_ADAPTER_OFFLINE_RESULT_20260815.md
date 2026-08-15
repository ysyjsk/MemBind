# S5 M* Production Adapter Offline Result

Date: 2026-08-15

## Scope

This checkpoint adds the oracle-free M* adapter boundary for the FX0 lane. It
uses the same `s5_mstar_pipeline` for prepare scheduling, logical-operation
timestamps, source-ordered bind, publication evidence, and poison/cancellation
behavior. The adapter receives only `Fx0ExecutionCase` plus the five declared
controlled providers; expected status, canonical state, and publication
history remain private to the FX0 comparator.

The fixture-only single-source mode is explicit: it keeps the configured
prepare concurrency at two but does not claim a two-worker overlap for a
one-case correctness fixture. The normal M* mode retains the overlap proof.
This prevents a tiny FX0 case from being mistaken for a performance result.

No Graphiti semantic callback, model client, vLLM service, Neo4j database,
namespace, live authority, or current-stage pointer was accessed or changed.

## TDD Evidence

Initial adapter/core focused tests:

```text
23 passed, 0 failed
```

Follow-on focused suites add 7 semantic-binding tests, 5 Graphiti semantic
runtime tests, 3 durable M* runner tests, and 2 smoke-projection tests.

Covered contracts include:

- fixture-mode M* core behavior without weakening normal overlap checks;
- oracle-free adapter input and exact state/publication parity through FX0;
- registered conflicting-duplicate fail-closed propagation;
- recursive private output rejection;
- frozen production-path identity binding and source-sequence rejection.

The pinned semantic binding/runtime adds a read-only contract for the exact
Graphiti sequence `extract -> resolve nodes -> resolve pointers/edges ->
attributes -> _process_episode_data`, with the same logical operation time
carried from prepare to commit. The local Graphiti identity is persisted in
`artifacts/paper_eval/native/S5_GRAPHITI_SEMANTIC_API_IDENTITY.json`; its status
is explicitly `OBSERVED_PINNED_LOCAL_INSTALL_NOT_LIVE_AUTHORITY`.

The durable M* runner and smoke telemetry projection are also implemented and
tested. The runner requires an M* identity with an FX0 parity hash and at least
two sources, so the live overlap proof cannot be silently skipped; the
projection reuses the common smoke contract without exposing episode content.

Full paper-evaluation offline regression after this checkpoint:

```text
1051 passed, 0 failed, 0 errors, 0 skipped before semantic-binding addition;
the current post-binding regression is 1068 passed, 0 failed, 0 errors, 0
skipped.
```

Evidence:

```text
logs/TDD_FULL_OFFLINE_GREEN_S5_MSTAR_ADAPTER_20260815.xml
logs/TDD_FULL_OFFLINE_GREEN_S5_GRAPHITI_SEMANTIC_BINDING_20260815.xml
logs/TDD_FULL_OFFLINE_GREEN_S5_GRAPHITI_SEMANTIC_BINDING_V2_20260815.xml
logs/TDD_FULL_OFFLINE_GREEN_S5_MSTAR_RUNNER_20260815.xml
logs/TDD_FULL_OFFLINE_GREEN_S5_FINAL_20260815.xml
```

## Boundary

This is an adapter/core offline qualification only. The semantic callbacks in
the tests are controlled doubles; therefore this checkpoint does not freeze an
M* production identity, does not create an FX0 production-parity artifact, and
does not authorize the M* smoke. The next required step is to bind the adapter
to the pinned Graphiti extraction/resolution/invalidation/commit path, then run
the production-path FX0 exact-parity gate. A live M* authority remains false.

## Additive hardening checkpoint (2026-08-15)

The follow-on offline work stayed in the isolated S5 lane. It added the
non-circular M* core identity, explicit controlled logical-operation time,
typed multi-source execution evidence, an explicit provider scope around the
pinned semantic runtime, compatible duplicate-UUID coalescing, and a fsync
publication journal with post-commit publication recovery. A bind callback's
returned state/history can no longer override an independent snapshot.

The legacy FX0 self-test artifact contract was not modified. The separate
`s5_mstar_fx0_artifact.py` verifier requires external fixture/input bindings,
pinned semantic identity, hash-only case evidence, and all-false authority.
No production parity artifact was generated: the builder still rejects any
transition whose execution shape lacks real retry/recovery evidence. This is
intentional and keeps `RETRY_IDEMPOTENCE` from becoming a label-only pass.

Latest evidence:

```text
focused S5/FX0 suites       37 passed
full paper-eval-v3          1088 passed
compileall                  passed
git diff --check            passed
live model/embedding/Neo4j  0 / 0 / 0
```

The current-stage pointer is still `S3_CONFIGURATION_FROZEN`; A0/P/M* live
authority, FX0 exact parity, PILOT, and formal execution remain false.
