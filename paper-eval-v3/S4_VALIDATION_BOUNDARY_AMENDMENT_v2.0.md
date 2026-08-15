# S4 Validation Boundary Amendment v2.0

Date: 2026-08-15

Status: controlling, additive, and frozen before any revised-S4 or method
result is observed.

Parent protocol:
`../（主实验）MemBind_PAPER_EVALUATION_WORKPLAN_v3_SMALL_FIRST_FINAL.md`

Parent protocol SHA256:
`4b81c89b33d407f04fc20862a81eab6badba16d0d61d98331cbe188d1bb4f41e`

Legacy S4 workplan:
`S4_D0_EXECUTION_WORKPLAN_v1.0.md`

Legacy S4 workplan SHA256:
`bab29baec9d83dcb2ce4310e9694774d9efc0278f23a069a0a2716dea26d5c62`

## 1. Decision

This amendment supersedes only the parent and legacy-workplan clauses that
make a full, cross-run, candidate-level Graphiti replay an S4 qualification
gate. It does not alter completed evidence, the frozen dataset, Graphiti,
construction model, embedding model, Reader, Judge, retrieval policy, K, data
roles, method definitions, or later statistical rules.

The decision is:

```text
FULL_INTERNAL_D0_REPLAY_RETIRED_AS_QUALIFICATION_BOUNDARY
```

We do not redefine historical D0. `D0_READ_ONLY_REPLAY` remains the identity
of the attempted internal replay and retry-008 remains incomplete and
non-mergeable. The replacement scheduling control receives a new identity:

```text
TR0_SCHEDULING_TRACE_REPLAY
```

No retry-009 is authorized. Retry-008 must not be resumed, cleaned, rewritten,
or converted into a PASS.

## 2. Why The Boundary Changes

The paper claim concerns workload-level performance and externally observable
memory semantics. It does not claim that two Graphiti executions must produce
the same ordinal sequence of internal candidate-resolution calls, runtime
UUIDs, or candidate membership hashes.

Retries 004 through 008 exposed positional drift, ambiguous logical edge
identity, compatible duplicate runtime UUIDs, and finally cross-run call
correlation drift. These failures show that the internal dynamic resolution
trajectory is not a stable public or semantic interface. Continuing to repair
that trajectory would build a deterministic Graphiti replay engine rather
than qualify MemBind's claimed execution mechanism.

The change does not weaken the required evidence. It separates four questions
that the old D0 gate conflated:

```text
real system performance             RX0_NATIVE_REAL_EXECUTION
fixed-work scheduling effect        TR0_SCHEDULING_TRACE_REPLAY
mechanism-level exact correctness   FX0_DETERMINISTIC_MECHANISM_FIXTURE
real-workload semantics and quality REAL_WORKLOAD_CORRECTNESS
```

## 3. Frozen Retry-008 Interpretation

### U0 capture

The real Native Graphiti path completed all 49 episodes:

```text
status                         PASS
mergeable                      true
completed episodes             49 / 49
live LLM calls                 532
live embedding calls           67
candidate sidecar records      178
candidate sidecar appends      178
fallback/unexpected/rejection  0
canonical graph SHA256
ab076234fabef2b94bbd6d8a1815aa4aa8f97f0509086f53675689fe16c24e09
exact-group cleanup            0 nodes / 0 relationships
```

This is one complete, auditable operational canary for the real Native path.
It is not publication-grade repeated baseline stability, and its timing is not
headline performance evidence because candidate-sidecar capture added work.

### D0 replay

The old internal replay completed source sequences 0 through 6 and failed
closed at source sequence 7:

```text
status                         INCOMPLETE
mergeable                      false
error class                    CandidateSidecarError
error code                     SIDECAR_CALL_CORRELATION_MISSING
live LLM / embedding calls     0 / 0
sidecar consumed / remaining   20 / 158
persisted namespace state      32 nodes / 48 relationships
```

The phase result records no cleanup and the checkpoint declares the retained
namespace state. This is persisted evidence, not a new live Neo4j attestation.
The policy is `DO_NOT_CLEAN_OR_RESUME`.

No final retry-008 smoke result exists and no qualification activation V3
exists. Therefore retry-008 did not qualify historical D0 or authorize the
fixed-three qualification.

## 4. Revised Validation Lanes

### 4.1 RX0_NATIVE_REAL_EXECUTION

RX0 is actual Graphiti execution with the frozen model, embedding, Neo4j,
workload, Reader, Judge, retrieval policy, and K. Instrumentation must be
passive and its overhead reported.

Fresh Native, A0, P*, and M* executions are the only source of headline
makespan, goodput, freshness, backlog, resource, retrieval, and QA results.
Retry-008 capture is an operational canary and development trace source only.

### 4.2 TR0_SCHEDULING_TRACE_REPLAY

TR0 is a pure, fixed-demand scheduling counterfactual. It may hold arrivals,
dependency metadata, work counts, token counts, and explicitly modeled
service demand constant to isolate scheduler behavior. It is supporting-only
and is not headline performance evidence or a semantic correctness oracle.

Observed LLM wall-clock intervals must not silently be treated as
policy-independent service demand. Autoregressive generation latency is
endogenous to output length, continuous batching, queueing, KV-cache pressure,
concurrency, and the evaluated schedule. Graph work and database demand may
also change with materialized state.

Before TR0 can support a paper claim, its calibration rule and acceptance
threshold must be frozen without looking at calibration results. Calibration
must compare replay with real execution for Native and at least one changed
policy, at a low-load and a near-saturation point, using makespan, p50/p95
freshness, peak backlog, backlog AUC, goodput, and resource occupancy. No
threshold from another paper is inherited automatically.

### 4.3 FX0_DETERMINISTIC_MECHANISM_FIXTURE

FX0 runs the production Graphiti/MemBind mechanism path. It may replace only
controlled nondeterminism such as LLM responses, embeddings, logical time,
initial graph state, and candidate sets. It must not implement a simplified
parallel algorithm beside the production implementation.

Fixture size is determined by transition coverage, not by a frozen 3--5
episode count. Exact canonical logical-state and publication-history parity
must cover at least:

```text
entity alias/canonical merge
compatible duplicate-UUID coalescing
conflicting duplicate-UUID fail closed
relation resolution
temporal invalidation/update
state change between prepare and bind
source-ordered publication
retry/idempotence
lost, duplicate, and partial-publication detection
```

Random runtime UUIDs, physical Neo4j IDs, and uncontrolled wall-clock fields
are not bytewise semantic identities. Canonicalization must be specified
before comparison. FX0 proves only the transitions it covers and does not
provide a performance claim.

The M* production mechanism does not yet have a frozen S5 identity. Therefore
the full FX0 parity gate belongs to M* method qualification in S5; S4 may
authorize and implement its offline fixture framework but cannot claim M*
correctness before that method exists.

### 4.4 REAL_WORKLOAD_CORRECTNESS

Every method must execute real Graphiti on the common workload. Direct hard
invariants require:

```text
episode/source coverage                  100%
lost                                     0
duplicate                                0
source/publication-order violations      0
visibility/publication violations        0
temporal/provenance violations           0
```

Semantic graph comparison requires a preregistered canonical matching oracle
and reports matched precision/recall, unmatched nodes/edges, and temporal-state
differences. Aggregate node or edge counts are descriptive diagnostics and do
not establish parity.

Quality requires paired per-history Evidence Recall@10 and QA accuracy,
confidence intervals, and non-inferiority or equivalence margins frozen before
results. Similar point estimates or a nonsignificant test do not establish
equivalence.

## 5. Methodological Basis

This split follows established systems methodology while retaining the
limits those systems state explicitly:

- Firmament, OSDI 2016, Section 7.1, combines trace-driven simulation using
  scheduler code with a physical-cluster evaluation and documents missing
  trace constraints. <https://www.usenix.org/system/files/conference/osdi16/osdi16-gog.pdf>
- AlpaServe, OSDI 2023, Sections 5--6.1, validates its simulator against real
  execution across two policies and multiple SLO scales before using it for
  larger studies. Its predictable, non-autoregressive forward-pass assumption
  does not transfer directly to Qwen generation. The reported 2% result is not
  adopted as this project's threshold.
  <https://www.usenix.org/system/files/osdi23-li-zhuohan.pdf>
- FoundationDB, SIGMOD 2021, runs production database code under controlled
  network, disk, time, and randomness with invariant assertions. It also says
  deterministic simulation is not reliable for performance problems, which
  are measured in production. <https://www.foundationdb.org/files/fdb-paper.pdf>
- Sparrow, SOSP 2013, uses simulation beyond testbed scale but retains real
  deployment evaluation and says scale performance ultimately requires real
  execution. <https://people.eecs.berkeley.edu/~matei/papers/2013/sosp_sparrow.pdf>

These precedents support trace replay as a controlled explanatory tool and a
production-path deterministic fixture as a correctness tool. They do not
support replacing real-system performance or real-workload quality evidence.

## 6. Authority After This Amendment

This amendment authorizes only:

```text
revised S4 offline design/tests
TR0 offline design/tests
FX0 offline design/tests
S5 offline design/tests
```

It does not authorize:

```text
model calls
Neo4j mutation or namespace cleanup
TR0 or FX0 live execution
S5 live execution
PILOT or formal evaluation
current-stage pointer advancement
```

The durable current pointer remains `S3_CONFIGURATION_FROZEN` until a later,
separately tested and sealed authority advances it. Legacy candidate-sidecar,
candidate projection, and fixed-three qualification code remains available as
non-main-path, non-authorizing development infrastructure. Its D0, remap,
sidecar, edge-diagnosis, fixed-three, authority, consumption, result, and
activation schemas have `inheritance_allowed=false`: TR0 and FX0 must be
qualified under new identities and cannot inherit an old live authority.

## 7. Immediate TDD Order

```text
RED boundary/evidence tests
  -> amendment builder and verifier
  -> focused GREEN
  -> full paper-eval-v3 offline GREEN
  -> seal boundary artifact
  -> TR0 pure scheduling contract/tests
  -> FX0 transition fixture contract/tests
  -> S5 production-method smoke authority, separately
```

No model or database call occurs in this amendment step.
