# MemBind Native Graphiti Construction Characterization Workplan v1.1

> **Document status**: current research-priority override  
> **Protocol ID**: `native-characterization-v1.1`  
> **Scope**: Native Graphiti problem characterization only  
> **Architecture gate**: Native Graphiti only  
> **TDD rule**: RED -> minimal GREEN -> focused -> full offline regression -> dry-run -> live canary  
> **Instrumentation status**: `instrumentation_contract_status=specified_not_yet_qualified`  
> **Revision relation**: this document supersedes v1.0 for future characterization actions; v1.0 remains immutable historical rationale.

<!-- Maintainability: this is the only authoritative C0-C6 characterization
plan. Historical solution-validation documents retain their evidence, but must
not be used to add experiments or mechanisms to this bounded screening plan. -->

## 0. Review verdict and research boundary

The review's central correction is accepted: first characterize Native Graphiti,
then decide whether there is a systems problem, and only later consider a design.
This plan does not assume that MemBind, Parallel Compile, Late Bind, ordered
commit, selective repair, or OCC is the answer. Existing M1/M2 code remains a
frozen exploratory prototype and is excluded from C0-C6.

The characterization asks only:

1. Where does Native Graphiti construction spend wall-clock time and work?
2. Which intervals depend on an immutable episode/source prefix, the latest
   materialized entity/edge graph, or mutation/publication state?
3. Does a controlled online replay expose a blocking/freshness/backlog tension?
4. What happens when complete native `add_episode()` calls run concurrently?
5. Do these observations support a new systems problem, only partial evidence,
   or a stop?

Negative and alternative results are valid. Database/index work, model serving
or batching, ordinary asynchronous queuing, a simpler whole-update baseline, or
no publishable problem can all be the correct conclusion. C0-C6 may not be used
to retrofit evidence for the existing prototype.

No live model, embedding, database, or remote-host action is authorized merely
by editing this document. The old live grant remains revoked for this lane until
the repository's separate offline authorization transition has passed its own
TDD contracts.

## 1. Fixed object, data, and screening envelope

### 1.1 Primary object

The primary object is `U0: Upstream-Qualified-Graphiti-Serial`, pinned to
Graphiti `v0.29.3`, source commit
`021d3a57d511f21b10adaf7fa923bd5c1fce5e9d`, the frozen Qwen construction and
embedding deployment identities, local Neo4j, and one symmetric prompt,
decoding, retry, cache, HTTP, database-pool, and machine envelope.

The project's deterministic candidate-ordering adapter and process-local
`CachingCountingEmbedder` are not silently called upstream Graphiti. If needed,
they are a separately labeled guardrail:
`U0-S: Project-Stabilized-Graphiti-Serial`. Cache state is cleared or recorded
at every run boundary, and no method gets asymmetric carry-over.

Secrets and raw semantic content remain outside all artifacts. Never persist an
API key, `.env` contents, Authorization headers, raw prompts, raw responses, or
raw database query parameters. The `gpt55_temporary/**` lane is excluded.

### 1.2 Data and workload boundary

- Use only pre-frozen calibration/development histories. Do not inspect held-out
  or exposure-quarantined histories.
- Use fresh, content-addressed graph namespaces and preserve source order.
- Freeze history IDs, episode prefixes, block order, service identities,
  configuration, cache policy, and schemas before reading the relevant outcome.
- Treat a history/run as the analysis unit. Episodes in an evolving graph are
  dependent observations, not statistical replicates.
- Describe the finite deterministic open-loop schedule as a controlled replay,
  not a measured real-world arrival process or a steady-state queue.

### 1.3 Bounded screening matrix

The first signal-finding wave remains deliberately small:

```text
E1/E2: 4 calibration histories x 1 shared Native trace = 4 blocks
E3:    2 methods x 5 frozen loads x 1 screening repetition = 10 blocks
E4:    4 concurrency settings x 1 fixed history x 1 screening pass = 4 blocks
```

E1 and E2 reuse the same trace. Every block uses a fresh graph namespace and
ends with a durable checkpoint. This screening wave makes no significance,
failure-rate, universal-safety, or real-workload claim. Any confirmation run
requires a new plan frozen before confirmation outcomes are observed.

## 2. Stage state machine and hard stop

```text
C0  one-episode Native viability
 -> C1 instrumentation qualification
 -> C2 E1 Native construction breakdown
 -> C3 E2 state-dependency characterization
 -> C4 E3 Native Sync vs Async-Serial
 -> C5 E4 Native Serial vs Whole-Update Parallel
 -> C6 problem verdict and immediate STOP
```

At every arrow, failed tests, missing evidence, service errors, or process
interruption stop the stage and preserve the latest checkpoint. Infrastructure
failure is not a scientific negative result. There is no silent retry over the
same run namespace.

## 3. C0 - one bounded Native episode only

C0 is one bounded Native Graphiti episode only. Its sole pass condition is:

> The same frozen U0 stack completes one bounded Native Graphiti episode end to
> end, with LLM, embedding, and Neo4j all returning successfully.

This is engineering viability only and not a research result. It records only
the necessary sanitized environment and service identity. C0 MUST NOT grow into
H0: no candidate selection, no structured-output matrix, no additional canary,
and no qualification workload. One successful invocation does not prove
stability, performance, representativeness, or repeatability.

## 4. C1 - minimal instrumentation qualification

The current codebase has a measurement contract, not a qualified implementation:

```text
instrumentation_contract_status=specified_not_yet_qualified
```

Wrappers must be characterization-only and must preserve Graphiti prompts,
schemas, bound arguments, call order, retry behavior, parsed results, returned
values, exception types, and graph state. They must use monotonic clocks, close
spans on return and exception, isolate concurrent episode context, append and
flush atomically, and never capture content or secrets.

The minimum frozen telemetry is:

- parent/child phase spans with run, episode, source, status, and error code;
- LLM prompt name, timing, token counts, retry count, and status;
- embedding timing, call/text counts, dimension, and status;
- database operation class, timing, transaction boundary, and status;
- source attribution, candidate counts, graph-prefix size, and publication
  boundary;
- inclusive, exclusive, interval-union, and critical-path accounting.

The telemetry scope is frozen after C1. Only a measurement-correctness bug in
C0-C5 may add a contract or field. Curiosity, future mechanism evaluation, or
the possibility that another metric could be useful is not sufficient.

### 4.1 TDD qualification

Qualification requires RED and GREEN fixtures for nested and concurrent spans,
context restoration, exception closure, source attribution, LLM/embedding/DB
wrappers, transaction-internal operations, secret redaction, JSONL durability,
and interval-union calculations. Focused integration must demonstrate semantic
parity between trace-off and trace-on for a deterministic episode fixture.

The A/A gate uses five alternating trace-off/trace-on pairs and reports the
paired distribution rather than only an aggregate mean:

```text
<=2%: ideal target and ordinary pass
2-5%: conditional screening pass
>5%: default hard fail; repair instrumentation and re-test
```

The `2-5%: conditional screening pass` is legal only when semantic parity holds,
overhead is stable across alternating pairs, the overhead is fully reported,
and sensitivity analysis shows no phase ranking or G1/G2 interpretation change.
These are project guardrails, not a universal systems-paper threshold. In
particular, DistServe's <2% result is simulator accuracy against hardware SLO
attainment, not a tracing-overhead rule. A percentage below 5% cannot override
a semantic mismatch or a conclusion reversal.

## 5. C2 / E1 - Native construction breakdown

C2 executes U0 only. No comparison method is added in C2. For every episode it
records total `add_episode` service/publication latency and the minimum phase
and work-volume contract defined in C1.

The first primary result has this shape:

```text
Native Graphiti construction

Phase                         Wall-clock occupancy
previous-context              ...
node extraction               ...
candidate embedding           ...
candidate search              ...
node resolution               ...
edge extraction               ...
edge resolution               ...
invalidation/update           ...
attributes/summary            ...
publication                   ...
```

Report LLM / embedding / DB work-volume beside wall-clock occupancy: calls,
tokens, retries, embedding texts/dimensions, candidate counts, queries,
transactions, writes, and errors. Inclusive time, exclusive time, interval
union, sum-of-work, and critical-path occupancy remain separate; nested or
overlapping spans are never added directly to infer total latency.

Report per-history and natural-prefix median/p95 descriptively. Graph size and
episode content co-vary, so this stage does not claim that graph size causally
produced a latency trend.

## 6. C3 / E2 - state-dependency characterization

E2 combines static source audit and the dynamic E1 trace. The neutral evidence
classes remain:

| Class | Meaning |
|---|---|
| `D0 episode-only` | only the arriving episode and immutable local inputs |
| `D1 immutable source/history-prefix` | immutable ordered source or previous-episode prefix reconstructable from the source log |
| `D2 latest materialized graph` | reads or branches on current committed entity/edge graph state |
| `D3 mutation/publication` | mutates graph state or defines the visible publication frontier |
| `unknown` | evidence is incomplete or conflicting |

For each interval, persist exact source evidence, observed history/latest-graph
read sets, write/publication evidence, input-ready-at-arrival status, timing,
classification, and confidence. Extraction is not automatically independent:
Graphiti extraction uses previous episodes. A dynamic trace that did not observe
a read does not prove independence.

Compute conservative interval-union bounds:

```text
p_L = union(verified D0/D1 intervals whose inputs are ready at arrival) / T_total
p_U = p_L + union(potentially independent unknown intervals) / T_total
S_C(p) = 1 / ((1-p) + p/C)
```

Report `p_L`, `p_U`, and `S_2`, `S_4`, `S_8` as descriptive structural upper
bounds only. They ignore remote service capacity, batching, database contention,
instrumentation, ordering, and publication costs. They have no hard scientific
speedup threshold and cannot authorize a mechanism. There is no counterfactual
dependency microexperiment in this screening plan; uncertain phases remain
`unknown`.

## 7. C4 / E3 - Native Sync vs Async-Serial

The existing arrivals-plus-one-worker runner is Async-Serial, not the synchronous
baseline. E3 therefore compares:

- `Native-Sync`: caller returns at the episode publication boundary;
- `Native-Async-Serial`: caller returns after durable enqueue acknowledgement,
  while one FIFO worker publishes in source order.

Both methods use the same U0 service path and absolute arrival schedule. Before
any E3 treatment outcome is observed, calculate and freeze:

```text
S_ref = mean U0 service time from one frozen U0 calibration on the exact E3 history
lambda = 1 / interarrival
rho_proxy = lambda * S_ref
rho_proxy in {0.5, 0.8, 1.0, 1.2, 1.5}
interarrival = S_ref / rho_proxy
```

This is the only E3 load sweep. `S_ref` is shared by both methods and is never
recomputed after observing method outcomes. actual seconds are a derived result
column, not a second treatment grid. Because service time evolves with graph
prefix, `rho_proxy` is a finite-trace offered-load proxy rather than strict
steady-state utilization. The schedule is a controlled deterministic open-loop
replay and cannot establish a real workload distribution.

Use fixed timestamp boundaries and calculate:

```text
signed_publish_after_return = publish_timestamp - caller_return_timestamp
post_return_stale_window = max(0, publish_timestamp - caller_return_timestamp)
```

Measure caller/API-return latency, construction service time, queue wait,
arrival-to-visible latency, post-return stale window, backlog time series,
backlog AUC, maximum backlog, backlog at final arrival, drain time, throughput,
errors, and checkpoint loss. Async-Serial may move work off the caller path but
does not increase the one-worker construction service capacity. Without a
pre-frozen query/deadline trace, E3 makes no task-quality claim.

## 8. C5 / E4 - Naive Whole-Update Parallel

Compare complete Native `add_episode()` execution at `C={1,2,4,8}` using one
fixed history and one screening pass. Concurrency is the only treatment; model,
embedding, HTTP, database, cache, graph initialization, and resource envelope
remain symmetric. Persist checkpoints after every concurrency block.

Measure makespan, service throughput, visibility lag, work counts, service
errors, lost/duplicate episodes, transaction errors, source and temporal
invariants, publication loss, canonical graph parity, retrieval parity, and
execution-path evidence. A deterministic fixture/replay lane establishes path
and invariant behavior; live graph differences with unfixed model outputs stay
confounded.

The only legal screening interpretations are:

1. `DIRECT_INVARIANT_VIOLATION_OBSERVED`: a direct invariant violation is an
   existence counterexample for this history/interleaving, not a failure-rate or
   universality estimate.
2. `OUTCOME_INSTABILITY_OR_CONFOUNDED`: outcome instability / confounded when
   model variation or other uncontrolled evidence prevents causal attribution.
3. `NO_NAIVE_PARALLEL_INSUFFICIENCY_OBSERVED`: no insufficiency was observed in
   one fixed history and one screening pass; this is absence of evidence, not a
   safety or sufficiency theorem.

An oracle miss alone establishes trajectory divergence, not a semantic failure.
A lost/duplicate episode, transaction failure, source-order violation, temporal
invariant violation, or publication loss is direct evidence. If the simple
baseline performs well and respects the bounded guardrails, the current data do
not establish the need for a more complex runtime.

## 9. C6 - problem verdict and immediate STOP

After C2-C5 artifacts are complete, interpret four observation questions:

- `G1 Important cost`: is Native construction material in the documented
  controlled online regime, with an effect size and uncertainty?
- `G2 Structural separation`: do `p_L/p_U` and absolute interval time show a
  practically meaningful D0/D1 versus D2/D3 structure, without an arbitrary
  speedup cutoff?
- `G3 Online tension`: under the frozen open-loop replay, is there measurable
  caller blocking, backlog, or visibility-lag tension?
- `G4 Naive parallel observation`: which of the three bounded C5 interpretations
  was observed?

The sole final verdict is `PROBLEM_SUPPORTED`, `PARTIAL`, or `NOT_SUPPORTED`.
No individual threshold or single C5 pass decides it automatically. In
particular, G1-G3 evidence combined with
`NO_NAIVE_PARALLEL_INSUFFICIENCY_OBSERVED` is normally `PARTIAL`: the cost and
online problem may exist, but this screening did not rule out the simpler
baseline.

C6 immediately stops. `DESIGN_DECISION.md` contains only the verdict,
supporting observations, and unresolved evidence. It does not design, select,
or authorize a mechanism. Any post-characterization mechanism comparison needs
a separately reviewed, test-driven plan; it is not a continuation hidden in C6.

## 10. TDD, checkpoints, and interruption behavior

Every code or report change follows:

```text
RED -> minimal GREEN -> focused -> full offline regression -> dry-run -> live canary
```

Required contracts are limited to measurement correctness in C0-C5: wrapper
parity and exception behavior, context isolation, time accounting, dependency
ledger, freeze validation, deterministic scheduling, FIFO/durable enqueue,
timestamp metrics, backlog integration, no-loss/duplicate and invariant checks,
checkpoint/resume isolation, report generation, hashing, and secret scanning.

During a later authorized live run, emit detailed sanitized per-episode progress
and append+flush before acknowledging the episode checkpoint. Monitor startup
and the first checkpoint frequently; lengthen the interval only after stable
progress. A vLLM, embedding, network, Neo4j, or process interruption means:
checkpoint, stop, classify as infrastructure, and report immediately. Never
convert incomplete work into a speedup or negative research finding.

## 11. Frozen artifact and contract surface

The complete artifact surface is:

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
creation command, sanitized environment identity, and SHA256. Tested analyzers
generate the two Markdown reports from immutable inputs.

The current freeze permits no new authority layer, no candidate registry, no
paper-level run planner, no oracle namespace, no formal split system, and no
future-work artifact. Only an actual C0-C5 measurement-correctness defect may
change the contract, and that change requires its own RED/GREEN evidence.

## 12. Evidence basis and interpretation limits

The plan revision follows established systems methodology without claiming that
any paper supplies a universal numeric rule:

- DistServe (OSDI 2024) separates execution and queueing and validates its
  simulator against hardware across request rates. Its reported sub-2% result
  concerns SLO-attainment prediction error.
- PagedAttention/vLLM (SOSP 2023) shows queue growth and latency amplification
  once offered rate exceeds capacity, supporting a pre-frozen load sweep.
- Pivot Tracing (SOSP 2015) reports workload-dependent tracing overhead ranging
  from small to well above 2%, supporting measured perturbation and transparent
  reporting rather than a universal cutoff.
- The Mystery Machine (OSDI 2014) motivates causality/critical-path accounting
  and cautions against inferring dependencies from sparse traces.
- SAMC (OSDI 2014) shows why concurrency bugs can require specific event
  sequences, supporting the asymmetric interpretation of one observed violation
  versus one pass with no violation.
- The OSDI 2024 artifact guidance distinguishes a small getting-started check
  from full evaluation, supporting the bounded C0 viability check.

All E1-E4 results remain specific to the frozen Graphiti version, histories,
services, machine envelope, and finite screening schedule. Confirmation,
generalization to another memory system, real-workload prevalence, and any
publication-level mechanism evaluation are explicitly out of scope.

## 13. Immediate offline implementation order

1. Make the v1.1 document contract GREEN and persist focused evidence.
2. Implement the minimal characterization-only state transition offline.
3. Add RED fixtures for the frozen C1 instrumentation contract.
4. Implement only enough instrumentation to satisfy those tests.
5. Run focused and full offline regression, then a dry-run.
6. Only after explicit live authorization, perform the single C0 episode.

No live request is part of this plan-revision turn.
