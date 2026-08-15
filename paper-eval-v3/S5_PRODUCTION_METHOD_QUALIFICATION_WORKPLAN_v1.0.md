# S5 Production Method Qualification Workplan v1.0

Date: 2026-08-15

Status: frozen offline design. This plan does not authorize a model call,
Neo4j read/write, namespace creation, method smoke, PILOT, formal run, or
current-stage pointer update.

Controlling inputs:

- `../（主实验）MemBind_PAPER_EVALUATION_WORKPLAN_v3_SMALL_FIRST_FINAL.md`
- `S4_VALIDATION_BOUNDARY_AMENDMENT_v2.0.md`
- `artifacts/paper_eval/native/S4_REVISED_OFFLINE_GATE.json`
- `runtime/CURRENT_STAGE_STATUS.json`

## 1. Objective

S5 qualifies actual production entry points before the development-only
concurrency sweep. It must not promote old characterization code or the M2
exploratory prototype by renaming or hashing it.

The frozen order is:

```text
A0 one-history smoke
  -> P(C=2) one-history smoke
  -> M(C=2) FX0 production-path exact parity
  -> M(C=2) one-history smoke
  -> only then consider S6 offline design
```

All three live candidates use history `07741c45` (49 episodes), whose role is
`DEVELOPMENT_EXPOSED`. This is adapter/mechanism qualification, not a quality
estimate.

## 2. Common Native Base

Every method must use the same production U0 base:

```text
native_characterization_runtime.build_u0_graphiti_from_env
graphiti_native.graphiti_episode_kwargs
graphiti_native.add_episode
Graphiti 0.29.3 @ 021d3a57d511f21b10adaf7fa923bd5c1fce5e9d
```

The following are forbidden in S5:

```text
prompt/response replay
candidate stabilizer
semantic cache modification
resolution or invalidation modification
retrieval, Reader, Judge, K, dataset, model, or embedding change
legacy D0 authority/result/namespace reuse
```

The old `experiment_runner.py` default is not the S5 entry because its
historical path may install deterministic stabilizers and uses M0/M1/M2
identities.

## 3. A0 Candidate

Reusable components:

```text
native_characterization_c4_async.py
native_characterization_c4_live.py
native_characterization_c4_artifacts.py
```

The old C4 ten-block grid, authority, namespace, and result are not reused.
S5 requires a thin entry point bound to the common Native runtime and exactly
one FIFO worker.

Hard checks:

```text
durable enqueue fsync precedes caller return
FIFO admission and service
one worker
same U0 add_episode call
source-order publication
caller-return and publication timestamps are distinct fields
coverage 100%, lost=0, duplicate=0, order violations=0
```

Any A0 adapter, durability, accounting, direct invariant, or infrastructure
failure stops S5.

## 4. P(C=2) Candidate

Reusable components:

```text
native_characterization_c5_live_core.py
native_characterization_c5_live.py
native_characterization_c5.py
```

The old C5 four-concurrency grid, authority, namespaces, and result are not
reused. The S5 entry executes complete upstream `Graphiti.add_episode()` calls
with concurrency exactly two.

Hard adapter checks:

```text
two whole-update service intervals actually overlap
intent/publication accounting complete
telemetry complete
violation checker active
coverage/lost/duplicate/order observations persisted
```

A direct invariant violation or transaction conflict is a valid scientific
outcome for naive parallelism and does not by itself stop progression to M*.
It must remain in the result and later main table. Infrastructure, adapter,
telemetry, or incomplete-accounting failures stop S5.

## 5. M(C=2) Candidate

Current reusable candidate fragments:

```text
graphiti_membind.GraphitiMemBindRuntime
latest_state_bind.SourceOrderedCommitter
semantic_compile evidence-fenced artifact types
```

These remain `EXPLORATORY_CORE_NOT_PRODUCTION`. Before a production identity
can be frozen, TDD must establish:

```text
durable intent -> DB commit -> publication journal boundaries
terminal poison and deterministic recovery after bind/commit failure
idempotent retry, including commit-completed/journal-missing failure
no lost, duplicate, or partial publication
same mechanism core for live and FX0
oracle-free FX0 execution input
controlled providers only at LLM/embed/time/storage-query boundaries
Graphiti extraction/resolution/invalidation/commit code still executes
created_at / bind-time logical-clock semantics match the declared Native oracle
group_id and database routing semantics match Native
Graphiti private API signatures and pinned commit are hash-bound
```

`execute_fixture_case` may not branch on a transition name to construct an
expected state. It must inject controlled providers into the same production
core, run that core, and return its observed canonical state/publication
journal. Expected status/state/history remain private to the FX0 comparator.

M(C=2) live smoke is forbidden until a separate S5 FX0 production artifact
passes every required transition. Any FX0, direct invariant, fallback,
publication, or completeness failure stops S5 and blocks S6.

## 6. TDD Order

```text
RED method registry
-> focused method-registry GREEN
-> full paper-eval-v3 offline GREEN
-> implement A0/P thin adapters under RED/GREEN
-> implement M* journal/core/provider hooks under RED/GREEN
-> bind Graphiti private API signatures
-> run FX0 against the frozen M* production identity
-> full offline GREEN
-> build method-specific preflight and single-use authority
-> A0 smoke -> P(C=2) smoke -> M(C=2) smoke
```

Offline tests install model/network/Neo4j tripwires. No live action is inferred
from a GREEN unit test or from this workplan.

## 7. Durability And Failure Rules

Every live method uses a fresh `pev3-s5-*` namespace and a unique run ID.
Before a live call, its method-specific single-use authority is consumed
durably. Intent, enqueue, publication, failure, and per-episode checkpoints use
append/fsync or temporary-file/fsync/atomic-replace semantics.

On disconnect or failure:

```text
persist failure location and error class
persist completed episode prefix and token envelope
mark attempt incomplete/non-mergeable
do not start the next method
do not clean or resume without a separately tested recovery authority
```

## 8. Current Verdict

```text
A0 production identity   NONE_FROZEN
P(C=2) identity          NONE_FROZEN
M* production identity   NONE_FROZEN
M* FX0 exact parity      NOT_EXECUTED
S5 live                  NOT_AUTHORIZED
PILOT / formal           NOT_AUTHORIZED
```

The next action is only `S5_ADAPTER_IMPLEMENTATION_AND_OFFLINE_TESTS`.

## 9. Offline implementation checkpoint (2026-08-15)

The A0/P(C=2) composition boundary is now implemented and offline-tested in
`src/paper_eval/s5_production_runner.py`. It binds the exact Native callable to
the qualified scheduler adapters and the S5 durable attempt store. Its method
identity is hash-sealed, records method-specific concurrency and failure policy,
and remains `IDENTITY_ONLY_UNQUALIFIED`; an identity hash never promotes the
exploratory M2 implementation.

The shared M* core now has an explicit FX0 single-case mode, and
`src/paper_eval/s5_mstar_production_adapter.py` supplies an oracle-free adapter
boundary. This adapter is still exercised only with controlled callback
doubles. It is not a Graphiti production identity and it does not authorize an
FX0 production-parity result or a live smoke.

TDD evidence and scope are persisted in:

```text
S5_PRODUCTION_RUNNER_OFFLINE_RESULT_20260815.md
S5_MSTAR_PRODUCTION_ADAPTER_OFFLINE_RESULT_20260815.md
```

The next bounded step is to bind the M* callbacks to pinned Graphiti semantic
operations and write the production-path FX0 parity artifact. Until that gate
passes, the S5 order stops before M* live smoke and before any concurrency
sweep.

The semantic binding prerequisite is now present in
`src/paper_eval/s5_graphiti_semantic_binding.py`. It lazily verifies the exact
Graphiti 0.29.3 extraction, resolution, attribute, pointer, and private commit
symbols and seals a canonical signature projection hash. Its tests are
offline-only; the hash still does not authorize a model call, Neo4j mutation,
or M* smoke.

`src/paper_eval/s5_graphiti_mstar_semantics.py` now wires those symbols into the
Native semantic order (`extract -> resolve nodes -> resolve pointers/edges ->
attributes -> _process_episode_data`) with one logical operation time carried
from prepare to commit. The focused semantic-runtime suite is five tests, and
the observed local Graphiti identity is persisted at
`artifacts/paper_eval/native/S5_GRAPHITI_SEMANTIC_API_IDENTITY.json`. This is
still a read-only identity observation; a production FX0 parity artifact and
live authority remain pending.

`src/paper_eval/s5_mstar_production_runner.py` composes the M* pipeline with
the durable attempt store, requires an FX0-bound M* identity, and refuses
single-source runs so live overlap cannot be silently omitted. The common
smoke projection is implemented in `s5_method_smoke_contract.py`. These are
offline composition checks only; they do not authorize the M* smoke.

The pre-hardening offline checkpoint for the prior implementation wave was
`1068 passed`,
`0 failed`, `0 errors`, `0 skipped` in
`logs/TDD_FULL_OFFLINE_GREEN_S5_FINAL_20260815.xml`. The next action is a
production-path FX0 exact-parity run with controlled providers; no live action
is implied by this regression.

## 10. Additive hardening checkpoint (2026-08-15)

The implementation order above was continued with a non-circular M* core
identity, typed multi-source adapter evidence, explicit controlled-provider
scope, same-UUID projection compatibility handling, and a fsync publication
journal/recovery hook. The legacy FX0 self-test contract remains unchanged.

The separate production artifact verifier is now implemented, but no
production FX0 artifact is sealed. Its execution-shape gate requires actual
source-order/state-change evidence and at least two attempts for
`RETRY_IDEMPOTENCE`; a transition label, callback double, or recomputed hash
cannot satisfy it. The current-stage pointer and every live authority remain
unchanged. Latest TDD evidence is `37` focused S5/FX0 tests and `1088` full
offline tests, all green; this is still offline design/qualification work and
does not authorize M* smoke.
