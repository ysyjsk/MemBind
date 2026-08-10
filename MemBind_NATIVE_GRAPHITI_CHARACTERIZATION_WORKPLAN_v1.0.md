# MemBind Native Graphiti Construction Characterization Workplan v1.0

> **Document status**: current research-priority override
> **Protocol ID**: `native-characterization-v1.0`
> **Scope**: Native Graphiti construction characterization only
> **Architecture gate**: Native Graphiti only
> **TDD rule**: RED -> minimal GREEN -> focused -> full offline regression -> dry-run -> live canary
> **Current lane**: solution lane frozen; existing M1/M2/MemBind code remains an exploratory prototype and is not part of the current problem-definition experiments.

<!-- Maintainability: this file is the authoritative research-order pointer. The
solution-validation documents remain immutable historical/prototype material;
do not copy their H0 state machine into this workplan. -->

## 0. Decision in one page

The research order is reset. The current question is not whether the existing
MemBind mechanism passes its formal protocol. The current question is:

> Does a pinned Native Graphiti construction path contain an important,
> repeatable, measurable dependency structure in which expensive work can be
> prepared from episode/source information while a smaller suffix requires the
> latest materialized entity/edge graph, and does that structure create an
> online freshness or blocking problem?

This is a falsifiable characterization hypothesis, not a commitment to
`Parallel Compile`, `Late Bind`, `Source-Ordered Commit`, OCC, or any other
mechanism. The mechanism, if any, is selected only after the characterization
data. A result of `NOT_SUPPORTED`, or a result that points to database/index,
LLM-serving/batching, ordinary asynchronous queuing, or another direction, is a
valid scientific outcome.

The working hypothesis is deliberately stated as **昂贵但不依赖 latest
materialized graph state**: expensive work may coexist with a state-dependent
suffix. This only motivates **dependency-aware execution** as a question; the
final direction is **由 characterization 数据决定**. It is valid to conclude
**不支持该研究问题**, and the plan does **不自动选择 M2**.

The old solution-validation lane is retained but frozen:

- `MemBind_CURRENT_VALIDATION_PLAN_v1.3.md` and the v1.3 fairness protocols are
  historical/frozen solution-validation overlays, not the current research
  priority.
- H0 recovery, V2-R/V3-R, M1/M2 formalization, replacement-004, and any
  publication-grade correctness run are not resumed by this plan.
- No historical checkpoint, decision, hash, failure classification, or live
  output is deleted, rewritten, merged, or relabeled as characterization data.
- Before any new live characterization action, an offline TDD state transition
  must explicitly close the old solution-lane live grant and issue a narrowly
  scoped characterization grant. A document pointer is not live authorization.

## 1. Research boundaries and fixed identity

### 1.1 Primary object

The primary object is **U0: Upstream-Qualified-Graphiti-Serial**, pinned to
Graphiti `v0.29.3`, source commit `021d3a5`, with the same Qwen construction and
embedding service identities, local Neo4j deployment, prompt/decoding policy,
HTTP policy, and resource envelope throughout a characterization block.

U0 must be an actual Native Graphiti path. The existing project runner's
deterministic candidate-ordering adapter and in-process
`CachingCountingEmbedder` are not silently called untouched upstream. If they
are needed for a representativeness guardrail, use the stable label
`U0-S: Project-Stabilized-Graphiti-Serial` and report it separately from U0. The
historical method ID `D0` is not reused for this lane. Cache state is cleared or explicitly recorded at
each run boundary; no method receives asymmetric cache carry-over.

The remote vLLM endpoints and embedding fingerprint are read from the existing
ignored environment/configuration contract. This document never records an API
key, `.env` content, Authorization header, raw prompt, raw response, or raw
query parameters.

### 1.2 Data and workload boundary

- Use only pre-frozen calibration/development histories for this exploratory
  phase. Do not read held-out or exposure-quarantined evaluation histories.
- Start every breakdown/dependency history from a fresh, content-addressed
  graph state and preserve the exact episode order.
- Freeze history IDs, episode prefixes, model/embedding identities, prompt and
  decoding settings, cache policy, HTTP/DB pool policy, and artifact schema
  before observing E3/E4 outcomes.
- A finite synthetic open-loop trace is a controlled workload replay. It must
  not be described as a real-world arrival distribution or steady-state proof
  without an external workload trace.
- The old `gpt55_temporary/**` lane is permanently excluded from this mainline.

### 1.3 Questions

1. **RQ-C1**: Where is the Native `add_episode()` critical path spent, and how
   do wall time, work, tokens, embeddings, candidate counts, DB operations,
   retries, and errors change along a natural history prefix?
2. **RQ-C2**: Which operation intervals read the previous-episode history,
   latest materialized entity/edge graph, or publication state? What conservative
   opportunity bound remains after source and dynamic evidence are combined?
3. **RQ-C3**: Under a frozen controlled offered load, does Native Sync trade
   foreground blocking for freshness while Async-Serial returns early and
   exposes a measurable stale window/backlog?
4. **RQ-C4**: Does naive whole-`add_episode()` parallelism increase capacity, and
   does it cause direct invariant failure, execution-path divergence, stochastic
   outcome instability, or no meaningful problem at all?
5. **RQ-C5**: Which next direction is supported by the observations: a
   dependency-aware runtime, DB/index optimization, LLM serving/batching,
   ordinary async scheduling, OCC/validation, another mechanism, or stopping?

## 2. Stage state machine

The current research stages are deliberately independent of the old H0/M1/M2
state machine:

```text
C0 Lightweight native-stack viability
  -> C1 Instrumentation qualification (TDD + overhead gate)
  -> C2 Experiment 1: Native construction breakdown
  -> C3 Experiment 2: state-dependency characterization
  -> C4 Experiment 3: Native Sync vs Async-Serial
  -> C5 Experiment 4: Native Serial vs Whole-Update Parallel
  -> C6 problem verdict and stop
```

The terminal stage label is **C6 - Problem verdict and stop**.

### 2.1 Bounded screening wave

The first live wave is a signal-finding characterization pilot, not a paper
evaluation. Its exact history IDs and block order are content-addressed in
`freeze.json` before the first outcome is read:

```text
E1/E2: 4 calibration histories x 1 pass = 4 shared-trace blocks
E3: 2 methods x 5 frozen loads x 1 screening repetition = 10 blocks
E4: 4 concurrency settings x 1 screening repetition = 4 blocks
```

E1 and E2 reuse the same Native trace, so dependency analysis does not duplicate
model calls. E3 and E4 use one deterministically selected calibration/development
history, fresh graph namespaces, a seeded block order, and a checkpoint after
every block. The screening wave makes **no significance claim**. If it produces
a supported signal, repeats and any additional histories require a **new
confirmation plan** frozen before confirmation data. Screening histories and
outcomes are **not reused as held-out formal evaluation**.

At every arrow, a failed focused test, missing source evidence, or infrastructure
failure stops the stage and preserves a checkpoint. A vLLM connection failure,
timeout, HTTP 429/5xx, embedding failure, Neo4j failure, or process interruption
is immediately checkpointed and reported as infrastructure evidence; it is not
silently converted to a scientific negative result or retried over the same run.

### C0 - Lightweight native-stack viability

C0 is an engineering prerequisite, not a result. It verifies only that the
pinned U0 path can execute one bounded episode with the frozen construction and
embedding services and local Neo4j. It records service identities, model
configuration, Graphiti source identity, cache policy, and a sanitized one-episode
readiness artifact. It does not select candidates, qualify M2, or claim a
performance result.

Because the machine-readable historical state still describes the old H0 live
grant, C0 first requires an offline state-transition contract that sets the
research priority to `native_characterization_only`, revokes the old solution
grant, and creates a new characterization namespace. This transition must pass
RED/GREEN/full regression before any service request.

### C1 - Instrumentation qualification

Instrumentation is characterization-only and must not change Graphiti prompts,
schemas, call order, retry behavior, graph state, or response parsing. Do not
edit `site-packages`; use an idempotent install/uninstall wrapper around the
actual bound symbols in `graphiti_core.graphiti` and the project clients.

The qualification order is:

1. RED contracts for phase context inheritance/restoration, nested and concurrent
   spans, exception closure, wrapper return/exception parity, source mapping,
   secret redaction, JSONL flush, and interval accounting.
2. Minimal GREEN implementation with fake LLM, embedding, and driver fixtures.
3. Focused integration tests covering one complete Native episode and concurrent
   episode attribution.
4. A deterministic trace-off/trace-on A/A overhead test with five alternating
   pairs. Offline deterministic overhead must be <=2% to pass; 2-5% is a warning
   requiring optimization and re-test; >5% fails the gate. Live A/A results are
   diagnostic only and must not be used to tune a treatment.
5. Full offline regression, dry-run, then one bounded Native canary.

The raw trace stores sanitized fields only: phase name, source location,
monotonic start/end, parent span, episode/run ID, prompt name, token counts,
embedding text count/dimension, DB operation class, status, and error code. It
never stores prompt/response/query contents or secrets.

## 3. Neutral phase and dependency contract

### 3.1 Four evidence classes

No phase is labeled `compile` or `bind` before evidence. Each operation interval
is classified conservatively as one of:

| Class | Meaning | Typical evidence to verify |
|---|---|---|
| `D0 episode-only` | Uses only the arriving episode and local immutable inputs | no history/graph read; input ready at arrival |
| `D1 immutable source/history-prefix` | Uses an immutable ordered source or previous-episode snapshot that can be reconstructed from the source log | exact history read set and source availability |
| `D2 latest materialized graph` | Reads or branches on the current committed entity/edge graph | graph read-set, candidate result, resolution/invalidation dependency |
| `D3 mutation/publication` | Mutates graph state or defines the publication frontier | transaction/write-set and commit evidence |

`unknown` is a first-class result. “No read was observed” alone cannot prove
independence; **未观察到 read 不能单独证明 independent**. Only source-audit evidence, dynamic read evidence, and
`input-ready-at-arrival` evidence together can place time in the conservative
independent lower bound. Extraction must not be unconditionally called
state-independent: Graphiti reads previous episodes. In particular, the
distinction is between independence from the **latest materialized entity/edge
graph** and independence from **all state**.

因此 **不能无条件把 extraction 标为 state-independent**；它至少要经过
history-prefix 证据和动态 trace 的联合审计。

### 3.2 Pinned Graphiti phase map to audit

The phase map is a measurement target, not a pre-written result:

1. previous-context retrieval;
2. node/entity extraction;
3. candidate embedding;
4. node candidate search and resolution;
5. relation/edge extraction;
6. edge pointer binding and candidate search;
7. edge resolution, timestamp handling, and invalidation;
8. node attributes/summary hydration and embeddings;
9. episode/entity/edge persistence and commit;
10. optional community update.

The static audit records exact Graphiti source locations and distinguishes
semantic dependency from the predecessor order imposed by the current native
implementation. For example, relation extraction consumes extracted nodes and
previous episodes but not necessarily resolved entity UUIDs; its native
predecessor must not be mistaken for a latest-graph dependency. The dynamic
trace records actual DB reads/writes, LLM prompt names and durations, embedding
calls, candidate counts, and transaction boundaries.

### 3.3 Time accounting

Every phase has `inclusive duration` and exclusive duration. Parent/child spans use a
monotonic clock and are closed on both return and exception. Exclusive duration
subtracts the interval union of direct children; overlapping async calls are
not double-counted. Phase wall-clock, sum-of-work, and critical-path occupancy
are reported separately. Nested durations cannot be directly added to claim a
total. All phase attribution remains correct under concurrent episodes.

Stable accounting labels are `parent/child span`; nested durations **不能直接相加**.

## 4. Experiment 1 - Native construction breakdown

**Object**: U0 only; U0-S is a separately labeled representativeness guardrail.

For every episode, append and flush a record containing:

- total `add_episode` service and publication latency;
- previous-context retrieval;
- node/entity extraction and relation/edge extraction;
- candidate embedding, vector/hybrid search, node and edge resolution;
- temporal invalidation, attributes/summary hydration, and optional community
  work;
- DB query/search/write/transaction/commit intervals and counts;
- LLM calls, prompt names, input/output token counts, retries, and errors;
- embedding call count, text count, dimension, duration, and errors;
- candidate counts, graph/episode prefix size, and sanitized source IDs.

The primary descriptive table reports per-history and per-prefix median, p95,
and interval-union fractions. Episode records are not treated as independent
statistical samples: the history/run is the analysis unit. Graph size and
episode content co-vary naturally, so this experiment reports association and
does not claim a causal graph-size effect.

## 5. Experiment 2 - State-dependency characterization

Run a static source audit and dynamic trace on the same fresh U0 histories.
For each operation, persist:

- exact source file/function/line evidence;
- history read set and latest materialized graph read set;
- mutation/write and publication evidence;
- whether the required input was ready at episode arrival;
- `D0/D1/D2/D3/unknown` classification and evidence strength;
- inclusive, exclusive, and interval-union time.

Report:

```text
T_D0, T_D1, T_D2, T_D3, T_unknown
p_L = union(verified D0/D1 and input-ready intervals) / T_total
p_U = p_L + union(unknown intervals that could be independent) / T_total
```

The lower bound `p_L` is conservative; `p_U` is an uncertainty bound, not a
prediction. A finite-concurrency screening bound is reported only as a structural
upper bound:

```text
S_C(p) = 1 / ((1-p) + p/C)
S_C(p)=1/((1-p)+p/C)
```

It ignores remote service capacity, DB contention, instrumentation cost, and
commit ordering. It therefore cannot by itself authorize a mechanism.

## 6. Experiment 3 - Native Sync vs Async-Serial

The existing `run_native_serial` helper is an arrivals-plus-single-worker
Async-Serial runner; it is not a synchronous caller baseline. Add a separate
characterization runner with identical FIFO single-worker publication:

- **Native-Sync**: the caller returns only at the episode's publish boundary;
- **Native-Async-Serial**: the caller returns after a durable enqueue ack, while
  the same single worker publishes in source order.

The two methods use the same absolute open-loop arrival trace and the same U0
service path. Before E3 outcomes are observed, freeze the normalized sweep:

The runner persists the `absolute open-loop schedule` before execution.

```text
rho_proxy = mean_native_service / interarrival
rho in {0.5, 0.8, 1.0, 1.2, 1.5}
```

The user-readable sensitivity mapping `{20, 10, 5, 2} seconds` may be reported
as a secondary pre-registered view, but the points cannot be selected or changed
after seeing E3 results. In particular, do not change the points after observing
E3. A finite synthetic workload remains a controlled replay, not a real workload
claim.

The frozen mapping is also recorded as **20/10/5/2 seconds**; **看 E3 结果后不得改点**.

Measure caller blocking/API-return latency, queue wait, arrival-to-visible,
publish-to-return stale window, backlog time series and AUC, maximum backlog,
backlog at final arrival, drain time, service throughput, errors, and checkpoint
loss. Async-Serial does not improve the underlying single-worker construction
service capacity; its intended observable difference is earlier caller return
and a stale window. Without a pre-frozen query/deadline trace, do not claim task
quality degradation from visibility lag.

In short: **Async-Serial 不提高 construction service capacity**.
The stable metric labels are `post-return stale window`, `backlog AUC`, and
`drain time`; this remains a `finite synthetic workload`.

## 7. Experiment 4 - Naive Whole-Update Parallel

Compare U0 Native Serial against complete `add_episode()` parallelism at
`C={1,2,4,8}` on a fixed history and a pre-frozen near-knee arrival trace. The
concurrency increase is the treatment; do not artificially force every method to
the same instantaneous in-flight count. Use the same model, embedding, HTTP,
DB, cache, and machine envelope. Record service throughput, makespan, visibility
lag, server queue/429/OOM, retries, direct invariants, source/temporal ordering,
canonical graph and retrieval parity, repeated-run stability, and all model/DB
work.

The stable guardrail labels are `canonical graph parity` and `retrieval parity`.

Use two explicitly separated evidence lanes:

- deterministic fixture/replay lane for execution-path and invariant auditing;
- live lane for performance, where model-derived graph mismatch is labeled
  `outcome instability` or `confounded` unless the model output is fixed.

An oracle miss proves trajectory divergence only. A direct lost/duplicate episode,
transaction failure, source-order violation, temporal invariant violation, or
publication loss is a direct error. Do not pre-write that Whole-Update Parallel
will fail; if it is fast and satisfies the guardrails, the simpler baseline is
preferred and Late Bind necessity is not established.

The result must not be pre-written (**不能预先声称**) and must preserve the
possibility of choosing **更简单的 baseline**. A direct invariant failure is
reported as **direct invariant violation**, not inferred from an oracle miss.

## 8. Decision gates and legal outcomes

The gates are interpreted only after C2-C5 artifacts are complete:

- **G1 - Important cost**: construction cost is material in a documented target
  online regime. If no real arrival trace exists, label the result controlled
  online replay rather than claiming a real workload.
- **G2 - Structural opportunity**: `p_L/p_U` show a nontrivial, evidence-backed
  independent opportunity. If `S_8(p_U) < 1.2`, stop dependency-aware splitting;
  if `S_8(p_L) >= 1.5`, record strong structural opportunity; the interval is
  conditional and does not prove a method.
- **G3 - Online tension**: a pre-registered plausible load produces sustained
  backlog or arrival-to-visible amplification and a measurable Sync blocking vs
  Async stale-window tradeoff.
- **G4 - Naive-parallel insufficiency**: Whole-Update Parallel cannot satisfy the
  required capacity and semantic guardrails together. If it can, use the simpler
  baseline.

The final verdict is one of `PROBLEM_SUPPORTED`, `PARTIAL`, or `NOT_SUPPORTED`.
G1-G4 do not automatically authorize M2. `PROBLEM_SUPPORTED` authorizes a new
design comparison plan, not a preselected implementation. `PARTIAL` identifies
the missing evidence. `NOT_SUPPORTED` records a valid stop or a different system
direction. In all cases, the raw artifacts and hashes remain immutable.

明确地说：**G1-G4 不会自动授权 M2**.

## 9. TDD and live-execution protocol

Every instrumentation, runner, analyzer, and report change follows:

```text
RED -> minimal GREEN -> focused -> full offline regression -> dry-run -> live canary
```

Required RED/GREEN contracts include:

- phase-map source coverage and neutral labels;
- nested/concurrent context inheritance and exception span closure;
- LLM prompt-name/duration/token schema, embedding create/batch duration, and
  DB query/write/transaction timing without content capture;
- idempotent install/uninstall and instrumented/uninstrumented call-order/output
  parity;
- interval-union, exclusive-time, critical-path, and phase-fraction accounting;
- state-read ledger, source-prefix availability, input-ready-at-arrival, and
  conservative unknown classification;
- atomic per-episode append+flush and per-history/run checkpoints;
- absolute open-loop schedule, Sync publish return, Async durable-enqueue return,
  FIFO, queue/backlog/stale-window accounting;
- concurrency attribution, no-loss/duplicate detection, source/temporal
  invariant checks, and trajectory-vs-outcome classification;
- history-level summaries, `p_L/p_U` and `S_C` golden calculations, freeze
  manifest, artifact hash and secret-scan contracts.

Live execution is segmented and resumable only through a new run namespace:

1. frequent monitoring during startup and the first checkpoint;
2. longer monitoring intervals only after stable checkpoint progress;
3. JSONL append+flush after every episode and checkpoint after every history;
4. independent summary and error files for E1/E2/E3/E4;
5. any vLLM/network/embedding/Neo4j interruption means checkpoint, immediate
   stop-and-report, and no silent continuation.

The stop fence is also written literally as **vLLM unreachable** -> checkpoint ->
**immediate stop-and-report**. The output contract is **每 episode append + flush**
and **每 history checkpoint**.

No artifact may contain `.env`, API keys, Authorization headers, raw prompts,
   raw responses, or raw query parameters. The temporary `gpt55_temporary/**`
   branch is not imported, executed, or used as evidence.

## 10. Predeclared confounds and interpretation limits

Each run manifest records or controls the following before its result is read:

- **remote vLLM queue/GPU sharing** and service-side batching can change client
  latency when concurrency changes; record available queue telemetry and never
  call client latency pure GPU compute;
- **network jitter** is part of the deployed end-to-end path, but paired blocks,
  randomized block order, endpoint identity, and error telemetry must make it
  visible rather than subtracting it from the primary metric;
- remote **prefix cache**, client response/embedding cache, HTTP connection pool,
  and cross-run cache carry-over must be symmetric and recorded;
- **Neo4j page cache/index warmness**, database size, index identity, connection
  pool, and fresh-graph cleanup can bias DB/search measurements;
- **graph size and episode content** co-vary along natural prefixes, so their
  association is descriptive until a content-controlled treatment exists;
- **model nondeterminism**, structured-output retries, and service batching can
  cause live trajectory/outcome variation; fixture parity and live stability are
  separate evidence;
- **instrumentation overhead**, nested-span double counting, transaction-internal
  `tx.run` undercounting, clock source, and concurrent attribution can bias the
  breakdown and therefore require the C1 gate;
- a **finite transient** open-loop replay is not a steady-state queueing result,
  and synthetic load is not evidence that a real agent workload has that arrival
  process;
- Sync/Async completion boundaries must not change definitions between methods;
  failure-induced missing work is not a speedup;
- the **history/run is the analysis unit**; episodes within one evolving graph
  are dependent observations and are not used as pseudo-replicates;
- histories, load points, concurrency, retry policy, and verdict thresholds are
  frozen before the corresponding result is observed; no result-dependent
  cherry-picking or asymmetric rerun is allowed.

## 11. Artifact contract

The characterization namespace is independent of the old H0/M1/M2 trees:

```text
artifacts/native_characterization/
  freeze.json
  phase_map.json
  dependency_map.json
  runs/<run_id>/manifest.json
  runs/<run_id>/spans.jsonl
  runs/<run_id>/llm.jsonl
  runs/<run_id>/embedding.jsonl
  runs/<run_id>/db.jsonl
  runs/<run_id>/events.jsonl
  runs/<run_id>/checkpoint.json
  runs/<run_id>/errors.jsonl
  e1_breakdown.json
  e2_dependency_opportunity.json
  e3_sync_async.json
  e4_whole_parallel.json
  CHARACTERIZATION_REPORT.md
  DESIGN_DECISION.md
```

Every generated artifact records schema version, run ID, source/config hashes,
creation command, sanitized environment identity, and SHA256. The report is
generated only by tested analyzers from immutable run artifacts; it is never
hand-edited to improve a verdict.

## 12. Immediate implementation order

The next coding steps are small and offline:

1. make the document contract GREEN and persist its focused result;
2. add the offline solution-lane-freeze/characterization-only state contract;
3. add phase-span and interval-union unit tests with fake clients;
4. implement the minimum instrumentation and rerun focused tests;
5. add U0/U0-S identity and cache-policy freeze artifacts;
6. add fake Native Sync/Async and Whole-Update scheduling fixtures;
7. run full offline regression and only then request/perform the C0 live canary.

No live model request is part of this workplan creation turn.
