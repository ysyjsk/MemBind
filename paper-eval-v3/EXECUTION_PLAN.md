# Paper Evaluation v3 Execution Plan

Protocol source: `../（主实验）MemBind_PAPER_EVALUATION_WORKPLAN_v3_SMALL_FIRST_FINAL.md`

Protocol SHA256: `4b81c89b33d407f04fc20862a81eab6badba16d0d61d98331cbe188d1bb4f41e`

## Scope

This is a new, isolated lane. Existing C0-C5 contracts and artifacts remain historical evidence and are not rewritten. v3 forbids C6, so this lane never schedules C6.

## Active baseline-to-methodology finalization overlay (2026-08-17)

This overlay is the current execution priority.  It does not authorize M*, a
new namespace, PILOT, FINAL_PAPER_TEST, or any held-out access.

```text
sealed U0 + sealed A0 + running P(C=2)
  -> THREE_BASELINE_RESULTS.json (8/8 live blocks)
  -> read-only graph-quality overlay (3 methods x 4 histories)
  -> sealed development REPORT.json and top-level Markdown report
  -> deterministic METHODOLOGY_DECISION.json bound to REPORT + C3 + C5
  -> complete ../主methodology设计.md from the sealed values
  -> focused and full offline GREEN
  -> STOP; a later method implementation requires separate TDD authority
```

Long live stages run only in `tmux` and preserve per-history checkpoints.  A
vLLM/transport disconnect stops the chain and is reported without changing the
model, context envelope, completion cap, namespace, or retry policy.

The current development suite has unequal arrival timestamp semantics: U0
records arrival immediately before each serial call, while A0/P enqueue a
history burst.  Therefore current P95/P99 freshness is method-local diagnostic
evidence, not a cross-method delta.  Aggregate makespan/goodput remains a
descriptive burst-drain capacity direction for the same 188 episodes.  A later
formal online comparison must use one frozen open-loop arrival trace for every
method.

Methodology finalization follows RED -> focused GREEN -> related/full GREEN.
The pure decision policy is implemented in
`src/paper_eval/methodology_decision.py`; it binds the sealed report and C5
source-order counterexample and never authorizes a paper claim or live method.

## Active graph-quality overlay (2026-08-17)

The running `U0 -> A0 -> P(C=2)` development suite remains the only
construction workload.  Its existing Session Evidence Recall@10 and raw-session
Qwen Reader/Judge result stay immutable and method-common.  The observed U0
`QA=1/4, R@10=4/4` is a development diagnostic locating the failures after
session retrieval; it is not an exact published LongMemEval reproduction.

After all 12 construction graphs are sealed, one isolated read-only overlay
runs in method-major order:

```text
verified sealed namespace
  -> fresh Graphiti top-20 edge + top-20 node BM25/cosine RRF
  -> facts with valid/invalid/expired/reference time + entity summaries
  -> one fixed Qwen temporal-fact Reader request
  -> one fixed LongMemEval-rubric Qwen Judge request
```

The overlay never changes Graphiti construction, scheduling, vLLM parameters,
or construction metrics.  Construction LLM and cross-encoder calls are
fail-fast forbidden in its Graphiti runtime; only the frozen Qwen3 embedding is
available for query-vector retrieval.  Each method/history writes a private
recoverable bundle before its public projection, so an SSH/process interruption
does not resample a completed answer.  `INVALID_OUTPUT` is excluded from the QA
denominator and disclosed.  Until an official GPT-4o Judge or independently
human-qualified substitute is frozen, the result label is
`PROTOCOL_RUBRIC_COMPATIBLE_LOCAL_JUDGE_DIAGNOSTIC`.

Implementation and TDD evidence live in:

```text
src/paper_eval/graph_quality_*.py
src/paper_eval/graphiti_longmemeval_quality.py
src/paper_eval/temporal_fact_reader.py
scripts/run_three_baseline_graph_quality.py
tests/test_graph_quality_*.py
logs/TDD_*GRAPH_QUALITY*20260817.xml
```

## Active baseline-first execution overlay (2026-08-16)

The additive `BASELINE_SUITE_LIGHTWEIGHT_WORKPLAN_v1.0.md` now controls the
next baseline step. It preserves the in-progress Native N2 run, reuses the
already verified U0/A0/P(C=2) live-path evidence instead of repeating long
smokes, and authorizes only a strictly serial development suite
`U0 -> A0 -> P(C=2)`. `M*`, S6 sweeps, PILOT, and held-out evaluation remain
outside this lightweight suite. Where the Native-only overlay below says that
A0/P live work is paused until N3, this newer user-authorized overlay
supersedes that execution-priority clause only; all frozen identities,
artifacts, metric definitions, and fail-closed rules remain unchanged.

The current execution order is superseded by the additive
`NATIVE_BASELINE_FIRST_LIGHTWEIGHT_WORKPLAN_v1.0.md` overlay. The parent v3
protocol and all finalized historical artifacts remain byte-identical and
immutable; this is an execution-priority change, not a protocol/hash rewrite.

Until the overlay produces a terminal `NATIVE_BASELINE_DECISION.json`, no new
live S4/S5/S6/S7-S10 action is authorized. In particular, do not consume S6
authority, create a calibration namespace, or start the 32-cell matrix.

The only active stages are:

```text
N0  read-only identity/service/reuse check
N1  fixed-history U0 gate (reuse S1 unless drift requires one smoke)
N2  U0 Native-only screen over the exact four exposed histories
N3  baseline report + HEALTHY_FOR_NEXT_BASELINE / DIAGNOSE_BEFORE_METHODS
```

N2 is four histories, one serial construction per history, with fresh isolated
namespaces and durable per-episode checkpoints. It records quality diagnostics
(QA and unique-session Evidence Recall@10), construction latency and
goodput, episode terminal accounting, and LLM/embedding/Neo4j work volume.
Quality has no legacy-paper hard threshold; coverage, artifact integrity, and
service/path failures remain hard gates. C2 numbers are descriptive references
only because `C2_U0_REUSE_DECISION.json` records construction revision drift.

Only `HEALTHY_FOR_NEXT_BASELINE` may open a separate, later one-history A0/P*
or M* decision plan. `DIAGNOSE_BEFORE_METHODS` is terminal for this lane.

## S4 validation-boundary overlay (2026-08-15)

`S4_VALIDATION_BOUNDARY_AMENDMENT_v2.0.md` is the controlling S4 interpretation overlay.
It supersedes only the clauses that made full cross-run, candidate-level D0
replay a qualification gate. The parent protocol, historical D0 identity, all
retry-008 artifacts, common evaluation configuration, method identities, and
later statistical rules remain immutable.

Retry-008 is frozen as one real U0 operational canary (`49/49`, PASS) followed
by an incomplete, non-mergeable internal D0 replay at source sequence 7 with
`SIDECAR_CALL_CORRELATION_MISSING`. No retry-009, replay resume, namespace
cleanup, result rewrite, fixed-three activation, or historical D0 PASS is
authorized.

The replacement surfaces are deliberately separate:

```text
RX0_NATIVE_REAL_EXECUTION          headline real-system evidence
TR0_SCHEDULING_TRACE_REPLAY        supporting fixed-demand counterfactual only
FX0_DETERMINISTIC_MECHANISM_FIXTURE production-path transition correctness
REAL_WORKLOAD_CORRECTNESS          direct invariants, semantic metrics, quality
```

TR0 is never headline performance evidence and requires preregistered,
two-policy, low/near-saturation real-system calibration before paper use. FX0
size follows transition coverage rather than a fixed episode count. Exact M*
fixture parity is an S5 method-qualification gate because the production M*
identity is frozen there. All methods still require actual Graphiti execution
for headline performance, direct invariants, semantic comparison, retrieval,
and QA.

This overlay authorizes revised S4/TR0/FX0/S5 offline design only. Model calls,
Neo4j mutation, live S5 work, PILOT, formal evaluation, and advancement of
`runtime/CURRENT_STAGE_STATUS.json` remain unauthorized.

### Revised S4 offline gate (2026-08-15)

The revised offline contracts are sealed in
`artifacts/paper_eval/native/S4_REVISED_OFFLINE_GATE.json` (file SHA256
`5527752c79eaf6fb6b7932bb271f44b534f8d1b6a13762c9c8dce3ba14034e26`).
Its status is `OFFLINE_FRAMEWORKS_QUALIFIED_ONLY`; it does not mark S4 live,
TR0 calibration, FX0 M* parity, or real-workload evaluation complete.

The gate records the following distinctions explicitly:

```text
TR0  implementation qualified only
     real measured trace / replay not sealed or executed
     real-system calibration not satisfied

FX0  harness qualified with a test double only
     adapter receives an oracle-free execution input
     M* production identity and exact parity not executed

REAL  evaluation contract frozen offline
      semantic matching oracle and quality margins not frozen
      no workload result generated
```

The FX0 adapter boundary was tightened after an offline audit: expected status,
canonical state, and publication history remain private to the comparator and
are never passed to `execute_fixture_case`. The artifact verifier also rejects
duplicate case IDs, invalid source sequences, contradictory PASS/error rows,
and unregistered fail-closed results. This is an adapter-correctness repair,
not a method or workload change.

TDD evidence is preserved as four expected RED records, focused GREEN suites
of 17 TR0, 17 FX0, 15 real-workload-contract, and 12 aggregate-gate tests, a
70-test revised-S4 integration run, and a final `959 passed` full offline
regression. The current pointer remains `S3_CONFIGURATION_FROZEN`; the only
next action is `S5_PRODUCTION_METHOD_QUALIFICATION_OFFLINE_DESIGN`.

### S5 offline method qualification (2026-08-15)

The S5 order is frozen to one DEVELOPMENT_EXPOSED history and the minimum
method set needed before a concurrency sweep:

```text
A0, C=1
  -> P*, C=2
  -> M*, C=2 production-path FX0 parity
  -> M*, C=2 smoke
```

`S5_PRODUCTION_METHOD_QUALIFICATION_WORKPLAN_v1.0.md` and
`artifacts/paper_eval/native/S5_METHOD_QUALIFICATION_PLAN.json` authorize only
offline adapter implementation. Existing C4/C5 components may be reused, but
their historical authority, namespaces, schedules, and results may not be
inherited. The existing M2 code remains an exploratory core and is not a
production M* identity.

The additive P* role clarification is sealed in
`artifacts/paper_eval/methods/P_STAR_REAL_WORKLOAD_CORRECTNESS_ROLE_AMENDMENT.json`
(file SHA256
`2734e5adc7852e772e85d61daa0d68056da365213fad0a17bc23532eaa9ccd63`).
U0, A0, and M* retain hard-zero direct-invariant gates. P* must retain complete
accounting and disclose treatment-induced violations with its performance
record; it cannot claim semantics preservation, correctness equivalence, or
quality non-inferiority. Incomplete or corrupt P* telemetry remains an
infrastructure failure and is non-mergeable.

The first A0/P(C=2) adapter core is offline-qualified only. It accepts one
opaque, injected Native construction callable, excludes episode content from
public evidence, proves A0 FIFO/single-worker behavior, and recomputes actual
P(C=2) whole-update interval overlap. Its TDD sequence contains an initial
missing-module RED, a verifier-QA RED, `17 passed` focused GREEN, and a
`1014 passed` full offline regression. This evidence does not freeze the real
Graphiti callable binding, durable store, method identity, live authority, or
any result. Those boundaries and the M* production core remain pending.

The shared M* scheduling core is now also offline-qualified as a mechanism
framework. It runs two prepare workers, binds only one source at a time in
source order, records the latest published prefix and one logical operation
time per source, and poisons/cancels/awaits remaining work on prepare or bind
failure. Its callbacks remain opaque provider boundaries; it does not contain
Graphiti semantic logic. The focused suite is `7 passed`, and the post-fix full
offline regression is `1021 passed`. A durability-hook failure is fail-closed
and raised when a terminal evidence record cannot be trusted. This core is not
yet an M* production identity and cannot authorize FX0 or live work.

The isolated S5 durable attempt store is now framework-qualified as well. It
uses new manifest/event/checkpoint/result schemas, fsyncs each JSONL append,
atomically replaces result/checkpoint files, rejects event/hash/private-field
tampering, refuses existing attempts, and permanently marks failed evidence
`incomplete_non_mergeable` with `resume_authorized=false`. Its focused suite
has `7 passed`, and the full offline regression has `1028 passed`. This store
does not claim DB commit idempotence or in-place resume; a production runner
must bind it to the pinned Graphiti path and fresh-namespace policy before
live S5.

The Native Graphiti callable boundary is separately qualified offline. Its
loader resolves only `graphiti_native.add_episode` and
`graphiti_native.graphiti_episode_kwargs`, and the wrapper calls that exact
`add_episode` object with the shared Graphiti instance and opaque episode. The
focused suite has `7 passed`; the subsequent full offline regression has
`1035 passed`. This records the symbol contract only. The production source
file hash, Graphiti commit, runtime factory, and live preflight are still
unbound and unauthorized.

### S5 production composition and M* FX0 adapter (2026-08-15)

The A0/P(C=2) production composition layer is now offline-qualified in
`src/paper_eval/s5_production_runner.py`. It binds the exact Native callable to
the scheduler adapters and the manifest-first S5 attempt store, refuses an
existing attempt, seals successful evidence, and persists a native failure as
`incomplete_non_mergeable` with `resume_authorized=false`. Its identity records
the pinned Graphiti version/commit, Native symbol paths, U0 factory entrypoint,
source/test hashes, method-specific scheduler policy, runtime-config hash, and
failure policy. It is explicitly `IDENTITY_ONLY_UNQUALIFIED`; no live authority
is inferred from this hash.

The shared M* core now has an explicit one-case FX0 mode that does not claim
prepare overlap while preserving the normal two-worker overlap proof for live
M*. `src/paper_eval/s5_mstar_production_adapter.py` runs that same core through
an oracle-free `Fx0ExecutionCase` boundary and returns only observed canonical
state/publication output. Its tests cover exact parity, registered
fail-closed duplicate handling, private-output rejection, and identity
binding. The callbacks in this checkpoint are controlled offline doubles, so
the M* production identity and exact-parity artifact remain unsealed. The
separate `s5_graphiti_semantic_binding.py` contract now binds and hashes the
pinned Graphiti node/edge extraction, resolution, attribute, pointer, and
`Graphiti._process_episode_data` signatures; its focused suite has `6 passed`.
The actual local Graphiti 0.29.3 installation was read-only inspected and
persisted as `artifacts/paper_eval/native/S5_GRAPHITI_SEMANTIC_API_IDENTITY.json`
with status `OBSERVED_PINNED_LOCAL_INSTALL_NOT_LIVE_AUTHORITY`. The semantic
runtime callback sequence is covered by four additional offline tests.

The latest focused S5 suites are `51 passed`; the current full offline
regression is `1068 passed`, `0 failed`, `0 errors`, `0 skipped`. The durable
M* runner requires the FX0 identity hash and at least two sources, and its
pipeline events can be projected into the common smoke contract. No model,
vLLM, Neo4j, namespace, current-stage pointer, or live authority was touched.
The next action remains wiring this semantic binding into M* with controlled
FX0 providers and then running the production FX0 parity gate before any M*
smoke.

## Current Reader-v2 overlay (2026-08-14)

`NATIVE_READER_V2_QUALIFICATION_WORKPLAN_v1.0.md` is the controlling overlay
for the common Reader after the historical direct-Reader completion returned
`Recall_all@10=1.0` and `QA=0.0` on exposed item `07741c45`. It preserves that
result and every earlier S2 artifact.

The overlay keeps Graphiti construction, Episode BM25/RRF retrieval, K=10,
session materialization, chronological presentation, JSON, both conversation
sides, and the Judge fixed. It versions only the answer Reader to the pinned
LongMemEval repository's recommended single-call recipe:

```text
READING_METHOD=con -> cot=true, con=false
max_tokens=800
one Reader request
```

The selection is explicitly non-blind and occurred after the direct failure.
The disjoint DEVELOPMENT_EXPOSED canary `b6019101` passed exact adapter
compatibility with one search, one Reader, one Judge, zero truncation, and zero
retry. Its QA value is diagnostic and was not a freeze gate. The detailed
result and caveats are in
`NATIVE_READER_V2_QUALIFICATION_RESULT_REPORT_20260814.md`.

`artifacts/paper_eval/native/NATIVE_READER_V2_FREEZE.json` now binds the same
Reader and Judge identities for U0/A0/P*/M*. It authorizes only an S3
configuration update. It does not authorize PILOT, S4, a retrieval/top-k
sweep, graph reconstruction, or any quality claim.

## S3 Native-v2 configuration freeze (2026-08-14)

The Reader-v2 configuration update is complete. The additive artifact
`artifacts/paper_eval/native/NATIVE_BASELINE_V2_FREEZE.json` binds the existing
Native Graphiti construction, Episode BM25/RRF retrieval at K=10, dataset,
role snapshot, Reader-v2, and Judge identities for U0/A0/P*/M*. The historical
`s3_freeze.py` Gate-C implementation and all earlier failed/diagnostic S2
artifacts remain untouched.

This is deliberately a configuration freeze, not a claim that historical S2
quality passed:

```text
configuration_freeze_only       true
s2_quality_pass_claimed          false
quality_estimate_status          NOT_ESTIMATED
s4_live_execution_authorized     false
pilot_execution_authorized       false
```

The proposal to stop expanding the baseline is now frozen as an execution
constraint: do not add an 8-16 item qualification wave, a K sweep, another
benchmark, another baseline, a retrieval redesign, or a model change. Aggregate
Native/MemBind quality belongs in the later disjoint PILOT. The Reader-v2
adapter reuses pinned LongMemEval prompt/materialization semantics; it is not
described as an unmodified upstream Qwen3 runner.

### Baseline-closure interpretation

The Native-v2 baseline lane is closed after the S3 configuration freeze. The
historical direct Reader was a supported, hash-bound LongMemEval path rather
than an invalid ad-hoc reader, and the Reader-v2 canary establishes adapter
compatibility only. It neither estimates aggregate Native quality nor proves
that CoN repairs the historical failed item.

The remaining S4-S6 stages must not be interpreted as further baseline tuning:

```text
S4  deterministic D0 correctness control
S5  one-history A0/P*/M* method smoke
S6  fixed development-only concurrency selection
```

They remain necessary before the disjoint S7 PILOT because they establish the
oracle, semantic-parity, and qualified-method identities needed to interpret a
Native-versus-MemBind result. They may not change Reader, Judge, retrieval,
K=10, dataset, construction model, or the frozen workload. The S4 `1 + fixed
four DEVELOPMENT_EXPOSED histories` scope is a D0 correctness qualification,
not the rejected 8-16-item Native/Reader quality qualification.

S4 capture and replay use different Neo4j isolation namespaces. The pinned
Graphiti language-instruction function is group-ID invariant, but the legacy
prompt oracle includes `group_id` as non-semantic cache metadata. The isolated
S4 adapter therefore projects only that cache-key field to
`__S4_ISOLATED_NAMESPACE__`; the real Graphiti call, messages, system content,
user content, and Neo4j group IDs remain unchanged. This projection is bound by
the S4 production-adapter source and tests and is not available to later
performance treatments.

S0's declared construction repository revision and the revision constant in
the S1-bound runtime source differ. The constant did not enter the model
request and cache was disabled, so prior S1 evidence remains usable, but the
freeze records this as `CONFLICT_DISCLOSED` rather than claiming a current live
attestation. A lightweight service-identity preflight is required before any
S4 live action.

## S2 retrieval-contract amendment

The completed `s2-live-20260814-001` run exposed an interface mismatch: it
ranked Graphiti EntityEdges but labeled the result as LongMemEval
`flat-session` retrieval. The historical run and artifacts remain immutable.
Its numeric retrieval field is now interpreted only as Edge@10-attributed
source-session coverage; official LongMemEval session Recall@10 was not
computed.

Historical recovery code uses an explicit `graphiti_basic_edge` contract,
adapter identity v2, edge-unit metric names, and hashes of the underlying
Graphiti search implementation and recipes. The analysis and bounded
conditional `S2-R0` decision procedure are frozen in
`S2_RETRIEVAL_SURFACE_ANALYSIS_20260814.md`; no `S2-R0` live call is authorized
by this amendment itself.

### v3.1 interpretation overlay

`../MemBind_PAPER_EVALUATION_PROTOCOL_AMENDMENT_v3.1.md` is the controlling
overlay for retrieval units and S2 recovery. It does not mutate the parent
protocol (SHA256
`4b81c89b33d407f04fc20862a81eab6badba16d0d61d98331cbe188d1bb4f41e`)
or any completed artifact.

The historical candidate action was S2-R0: a read-only, episode-only
Graphiti 0.29.3 full-text/RRF probe over the immutable S1 namespace. It may be
authorized only after corpus ID/hash/mapping completeness, exact diagnosis
scope, fresh-config, zero-model-call, and result-sealing tests all pass. S2-R0
uses LongMemEval session-metric semantics but is not the official LongMemEval
retriever implementation. That recovery chain is now sealed historical
evidence; the selected Episode retrieval policy is bound by the S3-v2 freeze.

## Ordered stages

1. **S0**: read-only current-state and reuse audit; produce three finalized JSON artifacts.
2. **S1**: one fixed calibration history (`07741c45`), pinned upstream U0, serial episode smoke with durable event/checkpoint recovery.
3. **S2**: dataset/evaluator alignment, C2 reuse decision, and U0 sanity.
4. **S3**: freeze U0.
5. **S4-S6**: revised S4 offline framework gate, production-method smoke qualification, and development-only concurrency sweep.
6. **S7-S8**: bounded pilot and outcome-independent precision planning.
7. **S9-S10**: freeze and run formal paper evaluation, then write the headline table and stop.

## Detailed execution gates

| Stage | Offline gate | Authorized live work | Durable output | STOP condition |
|---|---|---|---|---|
| S0 | artifact envelope, role-disjointness, secret-scan tests | none | three finalized S0 JSON files | missing or contradictory provenance |
| S1 | dataset binding, namespace rebinding/probe, source-order, failure/resume tests | one `07741c45` U0 construction and one retrieval | per-episode JSONL, atomic checkpoint, final summary | loss, duplicate, order drift, retrieval failure, service disconnect, or namespace mismatch |
| S2 | dataset/evaluator parity and C2 equivalence decision tests | only missing U0/reference sanity allowed by the decision tree | alignment report and `C2_U0_REUSE_DECISION.json` | parity failure or unexplained near-zero sanity result |
| S3 | freeze schema/hash tests | none | `NATIVE_BASELINE_V2_FREEZE.json` | incomplete U0 identity or hidden quality claim |
| S4 | revised boundary, TR0 scheduler, FX0 harness, and real-workload correctness contract tests | no new live action; preserve the completed retry-008 U0 canary and failed historical D0 exactly | amendment plus revised offline gate | any claim inflation, legacy authority reuse, missing test evidence, or input-binding drift |
| S5 | A0/P/M scheduler and invariant-fixture tests | one exposed history per method, sequential stage order | method smoke artifacts | any method-specific hard gate failure; M failure blocks sweep |
| S6 | selection-rule tests | fixed four exposed histories, `C={1,2,4,8}` | `METHOD_SELECTION_FREEZE.json` | M has no qualified concurrency |
| S7 | deterministic role/selection tests | frozen eight-history pilot plus preregistered repeats | pilot manifest/results | correctness or systems signal fails the frozen continuation rule |
| S8 | estimator/bootstrap and outcome-independence tests | none | sample-size plan | available held-out pool cannot meet precision plan |
| S9 | complete freeze validation | none | final manifest/config/statistics freeze | any mutable or overlapping final identity |
| S10 | resume/aggregation/statistics tests | frozen formal blocks only | per-block checkpoints, results, headline table | frozen STOP/failure rule; never add samples based on significance |

No stage may create the next stage's live namespace before its offline gate is green.
S0-S1 use a new `pev3-*` namespace and never modify old C0-C5 namespaces or
`membind-validation/CURRENT_STATE.json`.

## TDD gate for every stage

Before any model or database call:

1. Add/execute a failing offline contract test (RED).
2. Implement the smallest behavior that satisfies it.
3. Run focused GREEN tests.
4. Run the complete offline regression for this lane.
5. Only then perform the stage's authorized live action.

## Durability rules

Events are JSONL records written with flush + `fsync`; checkpoints are written to a temporary file, flushed + `fsync`ed, then atomically replaced. A resume may process only the first not-yet-published source sequence. A non-empty namespace without a matching durable checkpoint fails closed.

## Current execution log

The immutable historical stop ledger remains in `runtime/STAGE_STATUS.json`.
The additive current pointer is `runtime/CURRENT_STAGE_STATUS.json`; all stage
artifacts live below `artifacts/paper_eval/`.

The durable current pointer remains `S3_CONFIGURATION_FROZEN`; it is not
advanced by a failed qualification. S0 and S1 execution evidence passed, while
historical edge/direct-Reader attempts remain immutable. S3-v2 freezes only
the Native/common evaluation configuration and does not estimate quality.

The first valid S4 smoke capture (`retry-004`) completed 49/49 episodes, but
its D0 replay stopped after 2/49 episodes on one `UnexpectedPromptError`.
Diagnosis proved that D0 reordered an unchanged two-node candidate set and
therefore reassigned the position-indexed `candidate_id` values. The failed
replay is `INCOMPLETE_DIAGNOSED_NON_MERGEABLE`; its exact namespace was cleaned
to zero nodes and relationships. The sealed result is documented in
`S4_D0_SMOKE_RESULT_REPORT_20260815.md`.

`S4_CANDIDATE_INDEX_REMAP_AMENDMENT_v1.0.md` records the retry-005 repair. Its
U0 capture passed 49/49 episodes. D0 replay then completed source sequences
`0..6` and failed closed at source sequence 7 with:

```text
CandidateRemapError
AMBIGUOUS_CANDIDATE_IDENTITY
edge invalidation candidate partition
```

The replay used zero live LLM, embedding, fallback, unexpected-oracle, and
cross-encoder calls. Prompt and embedding cache hashes remained identical to
capture. The current prompt-visible edge identity could not establish a unique
capture-to-replay candidate bijection, so positional response translation
correctly refused to guess. Retry-005 is incomplete and non-mergeable. Its
replay namespace is intentionally preserved at 32 nodes and 48 relationships;
no cleanup or new attempt is authorized.

The detailed evidence is in
`S4_D0_REMAP_RETRY_005_FAILURE_REPORT_20260815.md`. The four-history S4
qualification, S5, and PILOT remain unauthorized. The sealed original
qualification plan remains byte-identical and non-authorizing.

An additive qualification-activation module was prepared while capture was
stable. It re-runs the strict retry-005 result verifier and can activate only
the exact sealed fixed-four plan; it continues to deny S5 and PILOT. The full
offline gate is now 632 tests passing. Because retry-005 did not produce
`S4_D0_REMAP_SMOKE_RESULT.json`, no activation artifact was generated.
That activation implementation is retry-005-specific and may not consume a
future retry-006 result. A successful sidecar-aware retry-006 requires an
additive result verifier and activation v2 bound to its new identities. The
sealed fixed four remain four distinct histories total: the smoke history is
reused and only the other three run as fresh qualification blocks.

The bounded next action is frozen in
`S4_EDGE_IDENTITY_DIAGNOSIS_WORKPLAN_v1.0.md`. It first seals the persisted
retry-005 diagnosis, then permits one source-7-only, cache-driven dry run over
the preserved replay prefix under hard network, database-write, publication,
and cache-mutation fences. Because retry-005 never recorded capture-side
internal candidate linkage, that dry run may establish only whether a stable
endpoint-aware logical edge identity is available on the replay prefix; it
cannot retroactively prove a capture/replay bijection.

Only the workplan verdict `SIDECAR_AMENDMENT_JUSTIFIED` may open offline TDD
for a two-sided mechanism: a hash-only capture candidate sidecar plus the same
internal candidate projection at replay before prompt rendering. A
capture-only sidecar is insufficient. If the allowed logical identity remains
non-unique, execution stops without adding rank, position, UUID, group ID,
Neo4j element ID, or `created_at` as identity. Do not clean the failed
namespace, allocate retry-006, relax the fail-closed oracle, or start the
fixed-four qualification automatically.

The bounded retry-005 diagnosis is now complete and sealed in
`S4_EDGE_IDENTITY_DIAGNOSIS_RESULT_REPORT_20260815.md` and
`artifacts/paper_eval/native/S4_EDGE_IDENTITY_DIAGNOSIS_RETRY_005.json`.
It collected all ten source-7 pre-prompt edge calls. Each invalidation
partition contained ten candidates and ten enriched identities, including the
nine partitions whose fact-only projection had one duplicate pair. Pre/post
namespace and cache hashes were exact, with zero network, model, database
write, publication, or cache-write counters. Its verdict is
`SIDECAR_AMENDMENT_JUSTIFIED`, which authorizes only the D3 bilateral-sidecar
offline TDD in the controlling workplan. Retry-005 remains failed and
non-mergeable; its namespace is still preserved at 32 nodes and 48
relationships, and retry-006/fixed-four/S5/PILOT remain unauthorized.

The D3 bilateral-sidecar offline production integration is now complete. The
attempt-scoped runtime records hash-only capture candidate calls, recomputes
the same UUID-independent projection at replay, keeps candidate partitions
structural, translates cached positional decisions only after a unique
bijection, and commits replay consumption only after oracle acknowledgement.
It also blocks capture-prompt/replay-fast-path drift before Graphiti's
`_process_episode_data` publication boundary, reconstructs a validated
checkpoint prefix, and seals/verifies the sidecar after canonical export and
before exact namespace cleanup.

The frozen amendment and evidence are:

```text
S4_BILATERAL_LOGICAL_EDGE_SIDECAR_AMENDMENT_v1.0.md
focused offline gate                         147 passed
complete paper-eval-v3 offline gate          801 passed
retry-006 contract file SHA256
c8c25600d38da62b3560b07ac479f34303cc8337b63205875bb3caef074f7172
```

`artifacts/paper_eval/native/S4_D0_SIDECAR_RETRY_006_CONTRACT.json`
allocates only fresh attempt `006` identities and authorizes only the bounded
read-only preflight. It explicitly keeps live execution, authority
consumption, fixed-four qualification, S5, and PILOT false. No retry-006
namespace, cache, sidecar, preflight artifact, live authority, or phase result
exists yet. The next permitted action is the contract-bound read-only
preflight; a PASS must still be sealed before a distinct single-use live
authority can be created.

## S1 tmux operations

Start or resume the one authorized S1 run with:

```bash
./scripts/run_s1_tmux.sh <run-id> <fresh-namespace>
```

The script refuses a duplicate session. Re-running it after an SSH disconnect with the same IDs resumes the durable prefix; it never starts a second controller or cleans an old namespace. Inspect with `tmux attach -t membind-pev3-s1-<run-id>` or `tmux capture-pane -pt membind-pev3-s1-<run-id>`, and inspect the checkpoint under `artifacts/paper_eval/native/runs/<run-id>/`.

Long live commands run in a detached `tmux` session named for the stage and run
ID. Console output is line-buffered into `logs/`; scientific state is taken only
from the durable JSONL/checkpoint artifacts, not from terminal output.

## S5 production FX0 hardening checkpoint (2026-08-15)

The S5 lane now has a separate non-circular M* core identity and a distinct
production FX0 artifact verifier. The old FX0 artifact remains explicitly
`HARNESS_SELF_TEST_WITH_TEST_DOUBLE_ONLY`; it was not broadened or relabeled.

The offline implementation added:

- explicit controlled logical-operation time on `MStarSource`;
- typed multi-source decoding and independent snapshot evidence;
- an explicit controlled-provider scope around Graphiti semantic callbacks;
- deterministic same-UUID/same-projection coalescing and fail-closed
  same-UUID/different-projection handling;
- an fsync publication journal and a narrowly scoped recovery hook that retries
  only publication journaling after a commit-completed durability gap;
- a production FX0 schema requiring external input bindings, hash-only case
  evidence, execution-shape checks, pinned semantic identity, and exact
  all-false authority.

TDD evidence for this checkpoint:

```text
focused S5/FX0 suites              37 passed
full paper-eval-v3 offline         1088 passed
compileall                         passed
git diff --check                   passed
live model/embedding/Neo4j calls   0 / 0 / 0
```

No production FX0 artifact was sealed. The builder remains fail-closed until
all transition shapes execute through the actual pinned Graphiti runtime,
including at least two attempts for `RETRY_IDEMPOTENCE`; a transition label or
test-double callback cannot satisfy that gate. Therefore the current pointer
remains `S3_CONFIGURATION_FROZEN`, and S5 live authority, M* smoke, PILOT, and
formal execution remain unauthorized.

## S5 pinned Graphiti controlled-fixture checkpoint (2026-08-15)

The next offline TDD step was completed in the isolated `paper-eval-v3/`
lane. `s5_graphiti_controlled_fixture.py` constructs real pinned Graphiti
0.29.3 objects and invokes the installed extraction, resolution, edge,
attribute, pointer, and `_process_episode_data` functions. Only LLM,
embedding, candidate-search, logical-clock, and in-memory transaction
providers are controlled; no external service is contacted.

The fixture now verifies Native call order, default edge-type routing,
canonical node merge, real edge resolution, temporal invalidation, group ID
database clone routing, commit-result shape, and commit-before-publication.
It also detects transaction callback replay and fails closed instead of
claiming retry idempotence without an independent witness.

TDD evidence:

```text
focused controlled fixture      14 passed
combined S5/Graphiti suites      41 passed
full paper-eval-v3 offline       1102 passed, 0 failed, 0 errors, 0 skipped
compileall / git diff --check    passed / passed
live model/embed/Neo4j calls     0 / 0 / 0
```

Detailed evidence is in
`S5_GRAPHITI_CONTROLLED_FIXTURE_OFFLINE_RESULT_20260815.md` and
`logs/TDD_FULL_OFFLINE_GREEN_S5_GRAPHITI_REAL_FIXTURE_20260815.xml`.

This does not generate a production FX0 artifact and does not authorize S5
live execution. The controlled retry witness is bounded to its in-memory
upsert store; production `RETRY_IDEMPOTENCE`, full M* scheduler execution
shape, and the remaining transition inventory are still fail-closed. The
current stage pointer remains `S3_CONFIGURATION_FROZEN`.

### S5 typed-provider adapter checkpoint (2026-08-15)

The controlled Graphiti fixture now has an explicit typed provider scope and
continues to execute only the pinned Graphiti 0.29.3 semantic path offline.
The latest focused controlled-fixture suite is `15 passed`; the current
adapter/semantic/artifact focused selection is `35 passed`; and the complete
paper-eval-v3 offline regression is `1103 passed`, `0 failed`, `0 errors`, and
`0 skipped`. Evidence is persisted in
`logs/TDD_FOCUSED_GREEN_S5_GRAPHITI_TYPED_PROVIDERS_FINAL_20260815.xml` and
`logs/TDD_FULL_OFFLINE_GREEN_S5_GRAPHITI_TYPED_PROVIDERS_20260815.xml`.

The adapter contract also verifies that every case starts from its declared
controlled state, expected outcome fields never enter semantic callbacks, and
an independent snapshot is authoritative over any callback return projection.
This remains adapter compatibility evidence only. The production FX0 artifact
is still absent because the complete transition inventory, scheduler execution
shape, and production-bound retry witness have not all executed through the
pinned Graphiti fixture. Authority remains all-false and the stage remains
`S3_CONFIGURATION_FROZEN`.

### S5 production-adapter typed-provider integration (2026-08-15)

The next bounded TDD step connected the same production adapter to the real
pinned Graphiti controlled fixture. A RED test first demonstrated that the
adapter had no explicit typed-provider conversion boundary. The minimum GREEN
implementation added `controlled_provider_factory`; its output is reused by
case reset, source decoding, prepare, and bind. The production FX0 validator
now requires that factory, while older offline callback tests retain an
identity compatibility path.

Evidence:

```text
RED       logs/TDD_RED_S5_GRAPHITI_PRODUCTION_ADAPTER_INTEGRATION_20260815.xml
GREEN     logs/TDD_GREEN_S5_GRAPHITI_PRODUCTION_ADAPTER_INTEGRATION_20260815.xml
focused   30 passed, 1 warning
full      1106 passed, 0 failed, 0 errors, 0 skipped, 1 warning
full log  logs/TDD_FULL_OFFLINE_GREEN_S5_TYPED_PROVIDER_ADAPTER_20260815.xml
```

The integration observed the actual Graphiti 0.29.3 call sequence, transaction
completion, independent snapshot, and typed provider scope cleanup. This does
not seal a production FX0 artifact: the complete transition inventory,
multi-source scheduler shape, and production-bound retry witness remain
required. No live service or authority bit was touched; the stage remains
`S3_CONFIGURATION_FROZEN`.

### S5 real transaction-retry witness (2026-08-15)

The next RED contract required the production adapter to report a real pinned
Graphiti transaction callback replay, rather than counting only publication
journal recovery. The minimum implementation adds a bounded
`transaction_attempt_count` witness field. It is merged into the execution
attempt count only when the independent controlled fixture reports at least
two attempts; a retry flag without that evidence fails closed.

The two-source integration now observes prepare overlap, source-ordered
publication, and two actual Graphiti transaction commits. The retry case
observes two `execute_write` attempts with identical complete durable-row
projections and one logical publication. Evidence and regression status:

```text
RED       logs/TDD_RED_S5_GRAPHITI_TRANSACTION_RETRY_WITNESS_20260815.xml
GREEN     logs/TDD_GREEN_S5_GRAPHITI_TRANSACTION_RETRY_WITNESS_20260815.xml
focused   42 passed, 1 warning
full      1108 passed, 0 failed, 0 errors, 0 skipped, 1 warning
full log  logs/TDD_FULL_OFFLINE_GREEN_S5_GRAPHITI_RETRY_WITNESS_20260815.xml
```

This remains bounded controlled retry evidence. It does not claim that every
production transition is covered, does not generate a production FX0 artifact,
and does not authorize live S5. All authority bits and the stage pointer remain
unchanged at `S3_CONFIGURATION_FROZEN`.

### S5 prepare-to-bind latest-state witness (2026-08-15)

The pinned Graphiti adapter fixture now covers the state transition between
prepare and bind. After real extraction finishes, the controlled latest-state
provider is changed to a valid new `EpisodicNode`; bind then retrieves and uses
that new state. The witness is derived from the independent retriever
observation, not from an expected outcome supplied to the adapter.

The same execution-shape selection also retains the real compatible duplicate
UUID coalescing, two-source source-order, and transaction-retry cases. The
latest full offline regression is:

```text
1110 passed, 0 failed, 0 errors, 0 skipped, 1 warning
logs/TDD_GREEN_S5_GRAPHITI_PREPARE_BIND_STATE_CHANGE_20260815.xml
logs/TDD_FOCUSED_GREEN_S5_GRAPHITI_EXECUTION_SHAPES_20260815.xml
logs/TDD_FULL_OFFLINE_GREEN_S5_GRAPHITI_EXECUTION_SHAPES_20260815.xml
```

This remains offline transition evidence only. The production FX0 artifact is
still absent, all live/S5 authority remains false, and the stage pointer is
still `S3_CONFIGURATION_FROZEN`.

### S5 independent publication-fault detection (2026-08-15)

The adapter now has a separate publication-fault detector boundary. It reads
only observed durable snapshot/history plus source count; it never receives
expected fixture status/state/history. Silent lost publication, duplicate
publication, and partial two-source publication are injected at the external
history sink and detected as the three registered fail-closed modes. Unknown
detector results are rejected rather than converted into a scientific outcome.

TDD and regression evidence:

```text
RED       logs/TDD_RED_S5_GRAPHITI_PUBLICATION_FAULT_DETECTOR_20260815.xml
GREEN     logs/TDD_GREEN_S5_GRAPHITI_PUBLICATION_FAULT_DETECTOR_20260815.xml
GREEN     logs/TDD_GREEN_S5_GRAPHITI_PUBLICATION_FAULT_MODES_20260815.xml
focused   8 integration/fault tests, 1 warning
full      1114 passed, 0 failed, 0 errors, 0 skipped, 1 warning
full log  logs/TDD_FULL_OFFLINE_GREEN_S5_GRAPHITI_PUBLICATION_FAULTS_FINAL_20260815.xml
```

The production FX0 validator requires an explicit detector, but no artifact is
sealed yet: all transition rows must still be assembled and independently
verified together. No live service, namespace, authority bit, or current
stage pointer was changed.

## S5 production FX0 qualification result (2026-08-16)

The offline production FX0 gate is now complete. A strict source decoder,
case-independent provider factory, seven-provider production hash projection,
single-owner controlled environment, spec-derived fixture bindings, and an
exclusive finalizer were implemented under RED/GREEN tests. The complete
11-row inventory passes exact status/state/history parity through the directly
bound pinned Graphiti 0.29.3 semantic runtime.

Final evidence:

```text
verdict                    PRODUCTION_PATH_EXACT_PARITY_PASS
fixture rows               11
full offline regression    1151 passed, 0 failed/errors/skips
artifact payload SHA256    196ac96bcec7e97fe4ba29bc7ce600fc169bad7f4b825ef7791f12dc1e622722
fixture manifest SHA256    f40981830d02db7c13adf17064ce24ee47e1b2349c4674618cb5c1ff6d4b8a9d
live service calls         0
```

The detailed result is
`S5_GRAPHITI_FX0_PRODUCTION_QUALIFICATION_RESULT_20260816.md`; sealed artifacts
are under `artifacts/paper_eval/native/` with the `20260816` suffix. The
historical non-executed status artifact and current-stage pointer remain
unchanged. This does not issue live authority or bypass the A0/P* smoke order.
