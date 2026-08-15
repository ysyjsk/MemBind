# S5 Production Runner Offline Result

Date: 2026-08-15

## Scope

This checkpoint qualifies only the thin offline composition boundary in
`src/paper_eval/s5_production_runner.py`. It binds the already-tested A0 and
P(C=2) scheduler adapters to the pinned `graphiti_native` callable boundary
and the manifest-first `S5AttemptStore`. The runner receives the Graphiti
object and callables from its caller; it does not load environment files,
construct a model client, contact vLLM, access Neo4j, create a namespace, or
grant a live authority.

M* is intentionally not promoted by this checkpoint. The runner rejects M*
with `mstar_requires_fx0_production_adapter` until the same production core is
bound to an oracle-free FX0 path and its exact parity gate passes.

## TDD Evidence

The test-first sequence was:

```text
RED       1 collection error: s5_production_runner was absent
focused  GREEN 10 passed
composition regression 59 passed
full offline regression before the M* adapter: 1045 passed, 0 failed,
0 errors, 0 skipped

After the M* FX0 adapter/core checkpoint, the current full offline regression
is 1051 passed, 0 failed, 0 errors, 0 skipped; see
`logs/TDD_FULL_OFFLINE_GREEN_S5_MSTAR_ADAPTER_20260815.xml`.
```

Evidence files:

```text
logs/TDD_RED_S5_PRODUCTION_RUNNER_20260815.xml
logs/TDD_FOCUSED_GREEN_S5_PRODUCTION_RUNNER_20260815.xml
logs/TDD_FULL_OFFLINE_GREEN_S5_PRODUCTION_RUNNER_20260815.xml
logs/TDD_FOCUSED_GREEN_S5_PRODUCTION_RUNNER_V2_20260815.xml
logs/TDD_FULL_OFFLINE_GREEN_S5_PRODUCTION_RUNNER_V2_20260815.xml
```

The tests cover pinned Graphiti version/commit and Native symbol identity,
recursive private-field rejection, identity drift, exact binding invocation,
A0 single-worker FIFO publication, P(C=2) real overlap, fresh-attempt refusal,
durable result sealing, and non-resumable failed attempts.

## Boundary And Authority

The identity records method-specific concurrency/scheduler policy, Graphiti
0.29.3 and commit `021d3a57d511f21b10adaf7fa923bd5c1fce5e9d`, the
`graphiti_native` module and symbol names, the U0 factory entrypoint, source and
test hashes for the scheduler and durable store, a sealed runtime-config hash,
the pinned Graphiti semantic-API signature hash,
and a failure policy (`incomplete_non_mergeable`, `resume_authorized=false`,
fresh attempt required, no DB idempotence claim). It is explicitly marked
`IDENTITY_ONLY_UNQUALIFIED`: hashing an exploratory implementation does not
promote it to a live-qualified method. Its SHA-256 is recomputed before a
runner can start.

Every runner attempt is a fresh directory. Durable events are appended through
`S5AttemptStore` before the result is sealed. A fail-closed adapter result is
persisted as `incomplete_non_mergeable`; no in-place resume is authorized.

The new QA test also drives a native failure through the complete path and
checks that the durable result is `incomplete_non_mergeable` with
`resume_authorized=false`; this is a failure-classification check, not a live
Graphiti result.

This is an offline framework qualification only. The current S5 live
authority remains false and the current stage pointer is unchanged. The next
bounded implementation task is the M* production adapter plus FX0 exact parity;
no model or Neo4j action is implied by this checkpoint.
