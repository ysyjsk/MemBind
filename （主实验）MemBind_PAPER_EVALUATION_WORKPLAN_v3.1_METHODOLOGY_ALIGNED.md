# MemBind Paper-Level Evaluation Workplan v3.1
## Methodology-Aligned / Small-First / Reuse-Aware / TDD-Frozen

> Date: 2026-08-17  
> Status: ACTIVE DEVELOPMENT EXECUTION PLAN  
> Method authority: `MemBind_FINAL_METHODOLOGY_v3.1_FROZEN.md`  
> Current data role: `DEVELOPMENT_EXPOSED` only  
> Current terminal deliverable: one aligned development main table plus one
> mechanism-ablation table. This document does not authorize PILOT or
> FINAL_PAPER_TEST access.

This file replaces the former v3.0 execution plan. Historical v3.0 artifacts
remain immutable evidence; they do not define the v3.1 method. If this file and
the frozen methodology disagree, the methodology controls and execution stops
until this workplan is versioned again.

---

# 1. Research Question and Claim Boundary

The primary question is:

> Under the same Graphiti, construction LLM, embedding model, Neo4j, serving
> backend, source stream, arrival trace, and resource envelope, can MemBind
> expose and overlap only adapter-certified evidence-bound work while binding
> every mutable-state operation to its exact predecessor memory version,
> thereby reducing freshness latency and increasing successful construction
> goodput without changing source-order construction semantics or downstream
> memory quality?

The method is defined as:

```text
Arrival Eligibility
  -> immutable Evidence Snapshot / Evidence Fence
  -> State-Cut Compilation
  -> immutable state-unbound Prepared Artifact
  -> bounded Prepared Reorder Buffer
  -> frontier-first Version-Bound Bind
  -> source-ordered Publication
```

The experiment harness persists hash-bound Prepared Artifacts, lifecycle
events and publication checkpoints so an interrupted attempt can be classified
without inventing missing outcomes. That durability is a measurement/recovery
property of this evaluation harness, not a crash-consistency claim or a
correctness premise of the MemBind method.

Only after this semantic path is qualified may legal Compile requests be
ordered by backend-granularity prefix affinity. Cache affinity is a secondary
mechanism; vLLM APC itself is not a MemBind contribution.

The current run produces development evidence only. It cannot be described as
a held-out final-paper result, a significance test, or a generality result for
all memory architectures.

---

# 2. Method Surfaces

## 2.1 Headline methods

The development headline table contains:

| Method | Role | Stateful semantics |
|---|---|---|
| U0-aligned | pinned upstream Graphiti serial reference | Native source order |
| A0-aligned | Native whole-update FIFO async-serial | Native source order |
| P(C=2)-aligned | naive concurrent whole-update reference | violations retained as outcomes |
| MemBind | final v3.1 policy selected before its measured four-history run | v3.1 source-order contract |

The current APC-aligned baseline run already owns the first three rows. They
must not be rerun merely to make orchestration look uniform. They may be
reused only after their complete artifact chain verifies under Section 16.

## 2.2 Required mechanism surface

The minimum mechanism ablation is separate from the headline table:

```text
U0
  -> MemBind-Barrier
  -> MemBind-FIFO
  -> MemBind
```

- `MemBind-Barrier`: same State-Cut, W, K_LLM, arrival trace, prompts and
  semantic work, but no new Compile admission while frontier Bind is active.
- `MemBind-FIFO`: frontier requests have priority; residual capacity remains
  work-conserving for FIFO Compile requests.
- `MemBind`: identical to MemBind-FIFO except legal Compile candidates may be
  ordered using frozen cache-affinity metadata.

Barrier is required only on one representative development history for
performance/freshness diagnosis. It does not repeat full QA. FIFO versus
MemBind is the only valid cache-affinity comparison.

## 2.3 Names that are no longer acceptable

The following are historical or incomplete identities and must not be
silently relabelled as the v3.1 final method:

```text
M2 exploratory prototype
S5 M* historical adapter
MemBind-v1 node-only with EdgeExtract inside Bind
C=1 / W=1 runner without request-priority semantics
```

They may supply tested components, never final result rows.

---

# 3. Existing Work: Reuse, Repair, or Historical Only

## 3.1 Reuse without rerunning

The following components are reusable if current hashes and public identities
verify:

| Existing result/component | Reuse |
|---|---|
| C1 instrumentation qualification | overhead evidence and phase-span rules |
| C2 raw trace schemas | spans/events/LLM/embedding/DB/checkpoint patterns |
| C3 dependency analysis | motivation and candidate State-Cut evidence |
| `membind_v1/source_log.py` | contiguous hash-bound source inventory |
| `membind_v1/evidence_fence.py` | immutable source-only fence and timestamp-tie fail-closed rule |
| `membind_v1/frontier.py` | ordered bind/publish state machine and poison state |
| `membind_v1/store.py` | append-only durable lifecycle and crash classification |
| `membind_v1/admission.py` | real request-level inflight accounting scaffold |
| `membind_v1/graphiti_adapter.py` | capability-restricted prepare scaffold, Native suffix shapes, duplicate UUID rule |
| `membind_v1/aligned_*` | common arrival/source identities, fresh blocks, offline reduction patterns |
| Quality Evaluation v1 | common read-only retrieval/Reader/Judge overlay |
| APC-aligned baseline lane | U0/A0/P(C=2) development inputs after terminal verification |

Duplicate canonical UUID handling remains:

```text
same UUID + same canonical projection      -> deterministic coalescing
same UUID + conflicting canonical projection -> fail closed
```

## 3.2 Reuse only after repair and new identity

The existing MemBind-v1 code requires the following methodology repairs:

1. EvidenceFence creation must occur from the arrived source prefix, not from
   an all-history capability prepared before wall-clock arrival.
2. Prepared artifacts must support the complete certified Compile region and
   bind its `CertificationRecord` identity.
3. EdgeExtract may move into Compile only after a fail-closed qualification
   proves zero persistent mutable-state reads/writes and zero undeclared side
   effects. If it fails, it stays in Bind.
4. The runner must implement a bounded ROB for configurable W and concurrent
   evidence work, while preserving one frontier state writer.
5. All real construction LLM calls must pass through one request-level
   frontier-first admission controller, not an episode-worker semaphore.
6. Direct violation events, safe-work metrics, ROB occupancy, frontier wait,
   and Compile/Bind work volume must be durable.
7. The final method identity must bind the v3.1 methodology hash and cannot
   reuse the historical `MemBind-v1 node-only` label.

## 3.3 Historical evidence only

The following stay read-only and never fill a new comparable result cell:

```text
C2 timing aggregates
C4 async history
C5 historical performance/correctness outcome
old U0/A0/P runs with different arrival semantics
old M*/M2 namespaces and artifacts
previous node-only MemBind smoke/results, if any
```

No current-state pointer, old protocol freeze, namespace, or sealed payload is
rewritten by this plan.

---

# 4. Data and Workload Contract

## 4.1 Current development inventory

Only these already exposed histories are authorized now:

```text
07741c45  49 episodes
b6019101  49 episodes
6071bd76  46 episodes
a2f3aa27  44 episodes
```

The exact LongMemEval-S cleaned source manifest, question IDs, session IDs,
timestamps, answer-session IDs, question/answer hashes and renderer revision
must be hash-bound before any new namespace exists.

## 4.2 Same arrival semantics for every method

Every method uses the same pre-generated open-loop `ArrivalTrace`:

```text
Freshness_i = Publication_i - Arrival_i
```

U0 must not redefine arrival as service start. Backlog in U0 is a result, not
instrumentation noise.

Current aligned development point:

```text
native service reference = 50.173429214 s
normalized offered load  = 1.2
inter-arrival             = 41.811191012 s
```

MemBind blocks must bind the exact baseline plan's source-manifest hash,
arrival-trace hash, history trace hashes, and inter-arrival value. Recreating
an equivalent-looking trace with a different hash is not accepted.

## 4.3 Arrival safety

The benchmark may know the frozen manifest, but the method capability must not
expose an unarrived episode to Compile. At time `t`, only sources satisfying
`arrival_i <= t` enter the ready set. Evidence Snapshot `S_i` is built from the
arrived, source-legal prefix at that boundary.

Any `Compile-before-arrival` or future-evidence read is a direct hard
violation, invalidates the MemBind block, and cannot be converted into a
successful performance result.

## 4.4 Formal load regimes, not active yet

Before PILOT or FINAL_PAPER_TEST, freeze at least:

```text
light load             rho ~= 0.5
near saturation        rho ~= 0.9-1.0
overload/burst          rho > 1.0
```

The current `rho=1.2` run is a development stress point. It does not satisfy a
future multi-load formal evaluation by itself. Load points are secondary rows,
not post-hoc tuning candidates.

---

# 5. Frozen Shared Backend Envelope

All comparable policies share:

```text
Graphiti                  0.29.3 pinned code/API identity
construction model        qwen3-32b-fp8
vLLM                      0.26.0
max model length          65536
YaRN factor               2.0
requested max_tokens      16384
structured output         json_schema
embedding model           qwen3-embedding-0.6b
embedding dimension       1024
Neo4j                     local pinned instance/config
Graphiti max coroutines   8
global LLM admission      same K_LLM for all methods
APC/chunked-prefill        same server configuration
GPU/model/backend          same deployment
```

The development configuration is frozen exactly as:

```text
compile workers `C = 2`
lookahead `W = 2`
global LLM admission `K_LLM = 2`
bind workers = 1
prefix-match granularity `G = 16 tokens`
decode-context parallel size `DCP = 1`
```

`G` and `DCP` are bound to the pinned vLLM 0.26.0 source revision and the
observed launch configuration; neither value may be inferred from a later
performance result. U0/A0/P(C=2)/MemBind-Barrier/MemBind-FIFO/MemBind all use
the same cache-isolation policy. Every comparable block receives a unique fresh
request cache salt. Thus prior-block cache identities are ineligible even when
the physical vLLM engine remains resident:

```text
cross_block_prefix_identity_reuse = false
cross_block_warm_inheritance = false
within_block_prefix_reuse = true
physical_cache_reset_claimed = false
```

This is logical request-identity isolation, not a claim that physical cache
memory was reset. Development cache/APC results remain `OBSERVATIONAL`.

Public result artifacts never contain API keys, database passwords, prompts,
raw responses, or episode text.

The execution-envelope artifact must additionally record:

```text
model revision / served model ID
max_model_len and RoPE/YaRN identity
vLLM scheduler, APC and chunked-prefill configuration
GPU memory budget
backend prefix-match granularity evidence
cache-isolation/warmup policy
PublishedReadContract status
single-writer namespace ownership
no external writer assertion
publish-completeness mechanism
hidden post-Publish task check
```

For the current Graphiti workload, retrieval/QA occurs only after published
checkpoints. Unless an independently qualified read gate exists, the claim is
limited to construction-to-construction source-order serializability; no new
transaction-level query atomicity is claimed.

---

# 6. State-Cut Operator Contract

Each declared operator has:

```text
dependency class: EVIDENCE_BOUND | STATE_BOUND
effect class:     PURE | STATE_READ | STATE_WRITE | PUBLISH
stream_id
source_sequence
operator/code identity
allowed inputs and upstream outputs
declared persistent read/write/effect sets
```

The Graphiti candidate map is:

```text
Candidate Compile region
  NodeExtract
  EdgeExtract, only if separately certified

Bind region
  NodeResolve
  ResolveEdgePointers
  EdgeResolve
  Attribute/Summary
  Temporal Invalidation
  Persistence
  Publication acknowledgement
```

The final Compile region is the maximal subset of this declared graph that
actually passes certification. Methodology does not authorize moving an
operator merely because it would improve performance.

## 6.1 CertificationRecord

Each candidate Compile operator produces a hash-bound record containing:

```text
backend/adapter/version and code revision
prompt/schema/config identities
rendered-request template identity
allowed evidence inputs
forbidden persistent-state APIs
declared persistent read/write sets
external-side-effect declaration
qualification trace digest
persistent read count
persistent write count
undeclared state-facing call count
future-evidence access count
verdict
```

Qualification instruments the Graph Driver, retrieval/search APIs, persistent
writes, and other adapter-declared mutable-state entry points. A nonzero
forbidden count yields `STATE_CUT_CERTIFICATION_FAILURE`; the operator remains
STATE_BOUND. There is no silent fallback inside a measured formal block.

Runtime retains a lightweight guard. A certified Compile operator that touches
persistent state invalidates the current block and revokes that certification
for the next protocol version.

## 6.2 Prepared Artifact

A Prepared Artifact may contain only immutable evidence-derived values:

```text
raw extracted nodes
raw extracted edges, only when EdgeExtract is certified
pure index maps/intermediates
source/evidence hashes
operator/prompt/schema/config hashes
CertificationRecord hashes
```

It must not contain resolved persistent identity, current candidates, mutable
edge versions, invalidation results, partial commits, database handles, or
credentials. It is canonical, self-hashing, durable and exclusive per source.

---

# 7. Runtime Contract

## 7.1 Prepared ROB and frontier

For each history stream:

```text
frontier = first unpublished source sequence
eligible speculation set = arrived sources in [frontier, frontier + W]
bind worker count = 1
writer lease count = 1 per namespace
```

Compile may complete out of order. Only the frontier source may start Bind,
and Bind_i starts only after durable Publish_{i-1}. Publish_i acknowledges all
declared state effects and joined state-mutating tasks before advancing the
frontier.

Durable source lifecycle:

```text
INTENT_DURABLE
  -> PREPARE_RUNNING
  -> PREPARED_DURABLE
  -> BIND_RUNNING
  -> COMMIT_RETURNED
  -> PUBLICATION_DURABLE
```

A crash after `COMMIT_RETURNED` is `AMBIGUOUS_COMMIT_POISONED`; it is never
resumed in place.

These names describe the experiment harness disposition. Paper correctness is
defined by evidence, predecessor-state, effect and publication ordering in
Section 8, not by a new transactional log or crash-recovery protocol. Existing
append-only/checkpoint code is reused; this workplan does not authorize
building a general recovery subsystem. Harness persistence overhead remains
inside the measured block and is disclosed rather than subtracted post hoc.

## 7.2 Frontier-first request admission

`K_LLM` limits actual construction LLM requests, including nested Graphiti
Node/Edge extract, resolve and summary calls. Its permit unit is one actual outbound construction-model transport attempt. Every retry or HTTP transport
attempt reacquires admission independently; a logical Graphiti
`generate_response` call may therefore contain multiple admitted attempts and
must not hold one logical-call permit across its retries. Worker count is not
accepted as proof of model concurrency. The observed actual transport-attempt
inflight count must never exceed `K_LLM`.

Policy:

1. A ready frontier STATE_BOUND LLM request receives the next free permit.
2. Already admitted requests are not preempted.
3. Remaining capacity is work-conserving for legal Compile requests.
4. Compile cannot reserve or monopolize permits ahead of a waiting frontier
   request.
5. Every dispatch records request class, source, operator, wait interval,
   active count, configured K and observed maximum inflight.

## 7.3 Runtime knobs

The implementation supports explicit:

```text
compile_workers C
per-stream lookahead W
global request limit K_LLM
bind_workers = 1
policy = Barrier | FIFO | CacheAffine
```

The current development method configuration is frozen to `C = W = K_LLM = 2`,
`bind_workers = 1`, `G = 16 tokens`, and `DCP = 1` before any measured
mechanism block, using only offline qualification and the already exposed
Native characterization. V5 is not a C/W/K sweep and does not authorize
changing those values after observing live Barrier/FIFO/MemBind performance.
No PILOT/held-out outcome is used. W, K and C are implementation knobs, not
paper novelty.

The terminal method plan must contain these numeric values, the predeclared
representative history `07741c45`, and this exact cache-affinity ordering:

```text
longest completed-provider LCP_G
-> response-completion recency surrogate
-> current-ready-cohort LCP_G sum
-> source-sequence/FIFO tie break
```

Live code accepts no separate CLI override for any of them. Response completion
is a conservative observable surrogate for provider prefill completion, not a
claim of backend-confirmed KV residency.

---

# 8. Correctness Contract

MemBind targets source-order serializability with evidence equivalence:

```text
Compile_i starts only after Arrival_i
Compile_i reads only E_i and legal S_i
Compile_i has no persistent mutable-state effect
Bind_i observes exact M_{i-1}
state writes are source ordered
publication is source ordered and exactly once
all declared Bind_i effects finish before Publish_i
```

Direct hard violations are event-witnessed, not inferred from a final graph:

```text
compile_before_arrival
future_evidence_access
state_cut_certification_failure
mutable_state_read_or_write_inside_certified_compile
bind_before_prior_publish
wrong_predecessor_version
out_of_order_publish
duplicate_publish
lost_publish
interleaved_ordered_state_effect
hidden_state_mutation_after_publish
unpublished_partial_read, only if PublishedReadContract is enabled
hidden_fallback
```

P(C=2) keeps measured violations as scientific outcomes. Infrastructure or
telemetry corruption remains non-mergeable for every method.

Execution validity and semantic outcome are independent dimensions:

```text
artifact_status = SEALED_VALID | INVALID_INFRA | INCOMPLETE
semantic_status = SAFE | VIOLATION_OBSERVED | NOT_APPLICABLE
```

The running baseline producer's historical `status=PASS` means only that its
block completed and sealed its declared measurements. The V3 verifier projects
that legacy field to `artifact_status=SEALED_VALID`, then derives
`semantic_status` from the measured direct-violation ledger. In particular,
P(C=2) may be `SEALED_VALID + VIOLATION_OBSERVED`; this is an admissible
scientific result and must not be discarded. Service loss, missing telemetry,
namespace contamination, checkpoint/hash drift or incomplete coverage yield
`INVALID_INFRA`/`INCOMPLETE` and remain non-mergeable. MemBind requires
`SEALED_VALID + SAFE`.

## 8.1 Deterministic/captured qualification

Before live quality/performance claims, freeze a canonical state projection
bound to backend, adapter, schema and projection versions. In a captured or
deterministic execution require:

```text
same rendered inputs
same oracle/captured outputs
same semantic work contract
same Prepared Artifact values
canonical state parity after every published checkpoint
zero oracle miss/fallback
zero certification failure
```

Live LLM sampling does not require bitwise graph equality. Live correctness is
the evidence/version/effect/publication trace contract plus retrieval/QA
non-degradation.

---

# 9. Unified Observability and Metrics

Level 0 raw traces are the source of truth:

```text
spans.jsonl
events.jsonl
llm.jsonl
embedding.jsonl
db.jsonl
graph_work.jsonl
queue.jsonl
resource.jsonl
direct_violations.jsonl
certification_records.jsonl
```

Deterministic offline reduction produces:

```text
Level 1 per_episode_metrics.jsonl
Level 2 per_history_metrics.json
Level 3 development_main_table.json/.md
```

## 9.1 Headline metrics

```text
QA Accuracy
Evidence Recall@10
Direct Violations
P95 Arrival-to-Publication Freshness
Successful Construction Goodput
Construction Makespan
```

QA remains `NQ` when the common graph-native quality protocol is degenerate.
For this development run, `NQ` does not block performance characterization,
but no quality-preservation or quality-non-degradation claim is made from an
invalid denominator. Any later final-paper Claim C requires a qualified common
quality evaluator and a valid denominator.

## 9.2 Predeclared secondary metrics

```text
P50/P90/P95/P99/max freshness
queue delay distribution
mean/P95/max backlog
queue area
drain time
attempted/completed/publication throughput
LLM request throughput and observed max inflight
LLM busy time
DB and embedding interval union
frontier wait time
speculative-ready time
Prepared ROB occupancy
overlap ratio
source-turn construction rate
source-input-token construction rate
per-history makespan speedup versus U0
per-history goodput ratio versus U0
per-history P95 freshness reduction versus U0
```

Source-normalized rates are deterministic offline projections only. A source
turn is one message in the frozen raw LongMemEval session, and source input
tokens are computed from each rendered `Episode.body` with the pinned Qwen
tokenizer, `add_special_tokens=false`, before live execution. Their per-history
counts and tokenizer/renderer identities are hash-bound in the workload
complexity freeze; they are never inferred from a model response. Because
successful construction goodput is already episodes/s, it is not relabelled as
turns/s. The four paired history effects are reported as individual values plus
median/geometric mean and range; four exposed histories do not authorize a
significance test.

## 9.3 Safe-work opportunity metrics

Report:

```text
rho_C_req     = Compile LLM calls / all construction LLM calls
rho_C_prompt  = Compile prompt tokens / all construction prompt tokens
rho_C_service = sum Compile service time / sum all service time, when reliable
```

Service time uses per-request service totals, not overlapped wall time.

## 9.4 Work-volume fairness

Every block reports by Compile/Bind and total:

```text
LLM calls, prompt/output tokens, retries and HTTP attempts
embedding calls/items/batches
DB reads/writes/transactions/conflicts
extracted/resolved/new/duplicate nodes
extracted/resolved/new/duplicate/invalidated edges
candidate counts and graph prefix before/after
```

If speedup coincides with reduced semantic work, the result is disclosed and
is not called a pure scheduling speedup.

## 9.5 Retrieval metrics

Reuse one ranked retrieval result to derive, where deterministic:

```text
Recall@1 / @3 / @5 / @10
MRR
nDCG@10
first relevant rank
```

No extra retrieval or Judge request is made solely for diagnostics. Undefined
temporal metrics are `NOT_AVAILABLE`, never filled with a post-hoc rule.

---

# 10. Cache and Prefill Evidence

Cache-affinity correctness never depends on cache residency. The scheduler
uses only legal ready Compile requests and never waits for a future cohort or
delays a ready frontier request.

The causal comparison is `MemBind-FIFO` versus `MemBind`, with identical:

```text
State-Cut, W, K_LLM and arrival trace
rendered request multiset and semantic work
model/backend/APC/chunked-prefill
cache salt / tenant identity
cache initial-state or documented warmup policy
```

The same `REQUEST_CACHE_SALT` isolation mechanism also applies to every U0,
A0, P(C=2), Barrier, FIFO and MemBind block: the salt is unique and fresh per
block, is never inherited across blocks, and permits only within-block prefix
reuse. A resident engine is allowed, but earlier block entries cannot match a
new block's identity. No method receives a different warm-cache policy.

Capture exact rendered-request hashes, exact token sequences or content-safe
token-prefix identities, and the backend's effective prefix-match granularity
G. Do not hardcode G from a guessed physical block size.

Report three separate levels:

1. structural granularity-aligned shared-prefix potential;
2. Schedule-Eligible Reusable Prefix Tokens from completed prefill providers;
3. realized backend cached-prefix tokens.

Derived diagnostics include:

```text
PrefixReuseEfficiency
recomputed/uncached prefill tokens
token APC hit rate
prefill latency and TTFT
```

Aggregate APC hit rate alone cannot support the cache claim. Because the
current shared vLLM lacks a verified reset-prefix-cache endpoint, the first
development table labels cache effects `OBSERVATIONAL` unless a dedicated
instance with controlled reset/restart or identical warmup is established.
An absent cache benefit is reported as a secondary negative result and does
not invalidate the core State-Cut/runtime result.

---

# 11. TDD Protocol

No live method run starts before all applicable offline tests are green.

Required order:

```text
RED  arrival eligibility and no future-evidence capability
GREEN source/fence implementation

RED  operator contract and CertificationRecord fail-closed tests
GREEN certification + runtime guard

RED  Prepared Artifact node/edge identity, immutability and tamper tests
GREEN durable artifact implementation

RED  ROB W, out-of-order Compile, frontier-only Bind and crash tests
GREEN semantic runtime core

RED  request-level frontier priority, work conservation, K bound and cancel tests
GREEN Barrier/FIFO/cache-affine admission

RED  Graphiti adapter shapes and deterministic node/edge State-Cut parity tests
GREEN production adapter

RED  baseline-artifact binding, shared trace, fresh namespace and reduction tests
GREEN live planner, runner and main-table reducer

focused GREEN
related GREEN
full offline GREEN
persist JUnit logs and SHA256 evidence
```

Tests must include exception paths, task cancellation, observer failure,
ambiguous commit, duplicate UUID compatibility/conflict, artifact tampering,
wrong predecessor, early Compile, hidden fallback, and secret rejection.

An implementation change after a live smoke requires rerunning its focused and
full offline suites before creating a new live attempt.

## 11.1 - Bounded autoresearch development probe (non-mergeable)

Before spending the full four-history MemBind budget, the implementation may
use one small, explicitly bounded autoresearch loop. This follows the useful
part of the autoresearch workflow: one controlled change at a time, a fixed
measurement budget, an append-only result ledger, and keep/discard decisions
made against a predeclared metric rather than intuition. It is an engineering
screen, not a new method, a parameter sweep, or a paper result.

The probe is fixed to a fixed 12-episode prefix:

```text
history                 07741c45 (predeclared representative history)
source prefix           first 12 episodes, source_sequence 0..11
arrival trace            the exact frozen prefix of the formal trace
backend envelope        the same construction/embedding/Neo4j identities
method knobs             C = W = K_LLM = 2 cannot be changed
cache boundary          G = 16, DCP = 1, one Bind worker
input/evaluator         no prompt, schema, source, arrival-trace or evaluator changes
candidate budget        maximum of three candidate iterations per probe run
```

The first row is the unchanged implementation reference. The rule is a new
namespace and cache salt for every candidate: each candidate must receive a
new attempt ID, fresh Graphiti namespace and new request cache salt;
an interrupted or failed candidate is never resumed in place. Every row is
written immediately to a private `results.tsv` with at least:

```text
candidate_id  parent_code_sha256  code_sha256  status
artifact_status  semantic_status  p95_freshness_ns  makespan_ns
observed_max_inflight  direct_violations  description
```

`status` is one of `keep / discard / crash`. A row is eligible for `keep` only
when it has complete coverage, zero direct violations, no hidden fallback,
observed inflight no greater than `K_LLM`, and valid self-hashed artifacts.
The performance comparison reports both p95 freshness and makespan; a
candidate is not kept merely because one noisy number improved. The default
engineering rule is non-regression within 5% on both metrics plus at least a
5% improvement on one metric, or a correctness-preserving removal of a
measured deterministic overhead with no metric regression. Otherwise it is
`discard`, while preserving its evidence for diagnosis.

Only transparent implementation work inside the already certified State-Cut
runtime is eligible, such as removing an accidental extra serialization point,
fixing an unnecessary blocking operation, or correcting a telemetry/adapter
path that changes measured work. The probe may not change the operator map,
State-Cut certification, source evidence, prompts, schema, model, embedding,
arrival schedule, evaluator, cache-affinity order, C/W/K, or correctness
contract. It may not add a heuristic after seeing a performance result.

probe artifacts are non-mergeable and excluded from the main table. If the
short prefix shows ordinary or negative benefit, first write a diagnostic
record identifying queue wait, compile overlap, bind/frontier wait, transport
admission, backend cache observation, and durable-artifact overhead; then make
at most one contract-preserving change per iteration. If no candidate passes,
retain the unchanged implementation and report the negative development
signal. After any candidate is kept, rerun the full offline suite as well as
the focused and related suites, regenerate the six V0 artifacts and
source-bound live plan, and
only then create a fresh formal smoke/main-method attempt. The probe never
authorizes held-out access or final-table merge.

---

# 12. Active Execution Stages

## V0 - Methodology/reuse freeze

Outputs:

```text
artifacts/paper_eval/membind_v31/V31_REUSE_AUDIT.json
artifacts/paper_eval/membind_v31/V31_EXECUTION_ENVELOPE.json
artifacts/paper_eval/membind_v31/V31_WORKLOAD_COMPLEXITY.json
```

`V31_REUSE_AUDIT.json` is the V0 offline source/hash/reuse audit only. It never
claims that the running APC baseline is terminal or accepted. It is part of
the six-file offline freezer output. `V31_WORKLOAD_COMPLEXITY.json` contains
only content-free per-history counts and the renderer/tokenizer identities
needed for the two source-normalized offline rates.

The six-file V0 freezer never emits `V31_METHOD_PLAN.json`. After V1/V2 pass, a
separate deterministic materializer may emit that plan from the already
verified baseline `PLAN.json`, methodology/workplan hashes, frozen source and
arrival identities, and shared execution envelope. Its explicit scope is:

```text
LIVE_EXECUTION_AUTHORIZED_BASELINE_MERGE_PENDING
```

The live plan contains no guessed or placeholder baseline-acceptance hash.

`V31_BASELINE_ACCEPTANCE.json` is emitted only at V3, after the exact APC run is
terminal and its complete artifact chain has passed the V3 verifier. It is not
emitted by the offline freezer and is not a premise of MemBind semantic
qualification or live execution. `V31_CONTROL_COMMIT.json` binds the unchanged
live plan to that later acceptance and is the only final-table merge authority.

They bind this workplan, frozen methodology, code identities, data manifest,
baseline run ID and exact expected baseline payload chain.

## V1 - Offline semantic implementation

Implement P0 semantic core first:

```text
Arrival Gate
Evidence Fence at arrival
operator map and certification
Prepared Artifact
Prepared ROB/frontier
Version-Bound Bind
publish completeness / ordered publication
direct violation ledger
```

Then implement P1 scheduling:

```text
global request admission
Barrier
frontier-first FIFO work conservation
bounded C/W/K
```

P2 cache-affine ordering is implemented only after P0/P1 are green and cannot
weaken their semantics.

## V2 - Deterministic qualification

Use captured/fake provider outputs and the pinned Graphiti semantic symbols to
verify every published checkpoint. Freeze:

```text
STATE_CUT_CERTIFICATION.json
CANONICAL_PROJECTION_FREEZE.json
DETERMINISTIC_SERIALIZABILITY_RESULT.json
```

If EdgeExtract does not qualify, retain it in Bind, update the method/operator
identity before live execution, and continue with the smaller certified cut.
Do not change Graphiti to force a larger cut.

## V3 - Accept or reject the running baseline lane

Wait for exactly:

```text
tmux: apc-aligned-pipeline-20260817-001
run:  apc-baseline-dev-20260817-001
```

Do not attach an additional writer or mutate its namespace while it runs. A
recoverable process pause at an already durable checkpoint is permitted only
by the operator-priority rule below; it does not publish or accept the partial
baseline. After the exact process resumes and exits, verify:

```text
12/12 construction blocks artifact_status = SEALED_VALID
4 histories x U0/A0/P(C=2)
188 terminal episodes per method
all block payload/hash/manifest/checkpoint chains
same source and arrival trace hashes
same shared execution envelope and K
fresh namespace identity per block
correctness checker = MEASURED
semantic_status derived independently from direct violations
quality overlay terminal and hash-bound
no incomplete attempt included
```

Any failure stops the merge. Completed blocks remain immutable and may only be
reused by a separately validated resume policy already encoded in that lane.
On PASS, V3 atomically materializes or verifies:

```text
V31_BASELINE_ACCEPTANCE.json
V31_METHOD_PLAN.json  # byte-identical source-bound live plan; never rewritten
V31_CONTROL_COMMIT.json
```

The commit marker is the publication point for the acceptance/plan pair and
the final merge authority. The
earlier `V31_REUSE_AUDIT.json` is not upgraded or reinterpreted as terminal
authority.

The baseline-acceptance lane and MemBind implementation qualification are
logically independent: a semantic implementation does not become correct
because a baseline reducer passes. By explicit operator priority on 2026-08-18,
the running APC process may be recoverably paused at a durable checkpoint once
all offline gates pass. V4 and the four main-method blocks may then run while
the shared backend is otherwise idle. The exact APC process is resumed after
those four blocks. Baseline acceptance blocks final merge, not V4-V6 live
execution. No incomplete baseline artifact is read as a scientific result.

## V4 - Three-episode live smoke

After V1/V2 PASS, a verified source-bound `V31_METHOD_PLAN.json`, recoverably
paused or terminal baseline writer, an otherwise idle shared backend, and
read-only service checks:

```text
history 07741c45
first 3 episodes
fresh smoke namespace
unique fresh request cache salt for this block
final selected MemBind identity
same K/backend envelope
```

PASS requires exact source coverage, zero hard violation, observed request
inflight <= K, correct frontier order, all Prepared Artifacts verified, and a
post-publication visibility witness. Failure seals the attempt
`FAILED_NON_REUSABLE`; use a new run ID after a code/config fix.

## V5 - Main-method-first development execution

After the smoke and (when services are available) the bounded autoresearch
probe have either sealed or been explicitly recorded as unavailable, blocks
0-3 are the final MemBind policy on:

```text
07741c45
b6019101
6071bd76
a2f3aa27
```

Run these four complete blocks after the V4 smoke. Once all four seal, resume
the exact paused APC process before doing any further measured block. This is
an operator scheduling decision made before MemBind live results, not a
performance-dependent method selection. Every MemBind block uses:

```text
exact baseline source and arrival trace identities
same public backend envelope and global K
fresh namespace and cache salt
unique fresh cache salt with no cross-block warm inheritance
one durable checkpoint per publication
tmux with unbuffered append-only log
```

Hard gate:

```text
zero MemBind direct hard violations
zero hidden fallback
deterministic qualification identity unchanged
complete work-volume accounting or an explicit NOT_AVAILABLE diagnostic
```

No positive speedup threshold is required; negative or neutral results are
preserved.

## V6 - Deferred one-history mechanism gate

After the APC baseline/Quality lane later completes and the backend is idle
again, reuse the already sealed representative MemBind block and execute only
the remaining two full-history blocks on `07741c45`:

```text
MemBind-Barrier
MemBind-FIFO
```

Use the same offline-frozen configuration and fresh namespaces. This stage
verifies that the policy differences actually occur; it does not select or
tune the final MemBind configuration from live performance. It is descriptive,
not a significance test. Cache metrics remain observational. Each block has a
unique cache salt and cannot inherit cross-block prefix identity.

V5 plus V6 therefore yield six full-history blocks total in this frozen order:

```text
blocks 0-3: MemBind on all four histories
blocks 4-5: Barrier + FIFO on representative history
```

They yield exactly four comparable MemBind main-table blocks, not a duplicate
representative run and not another 16-block baseline rerun.

## V7 - Quality, reduction and stop

After all MemBind construction blocks seal:

1. run the same read-only Quality Evaluation v1 overlay;
2. verify no construction mutation or new construction-model call occurred;
3. reduce baseline plus MemBind blocks offline;
4. write JSON, CSV/Markdown table and detailed report;
5. stop before PILOT/held-out access.

The development table is accepted only if every row has common data, arrival,
runtime, evaluator and quality identities. Since baseline methods and MemBind
are not temporally counterbalanced in this resumed development lane, the report
must disclose possible run-order/backend-time drift. A later formal run must
use a balanced block order across all methods.

---

# 13. Live Service and Long-Run Policy

Long jobs run in detached `tmux`, with `PYTHONUNBUFFERED=1`, `set -o pipefail`,
append-only `tee`, per-publication JSONL and per-block atomic checkpoints.

Before live smoke, perform one read-only check in this order:

```text
construction /v1/models
embedding /v1/models
Neo4j RETURN 1
vLLM running/queued requests = 0 before measured block
```

Do not print credentials. If construction vLLM, embedding vLLM or Neo4j is
unreachable, stop and report the failing layer. Do not change model, context,
completion cap, structured mode, W/K/C, arrival trace, or namespace to make a
failed block pass.

Infrastructure disconnect policy:

```text
persist last durable checkpoint
seal current attempt incomplete/non-mergeable
do not clean/reuse its namespace automatically
restart with a new attempt ID after service recovery
```

Treatment-induced failures such as deadlock, transaction conflict,
wrong-version bind or inability to drain are scientific outcomes and are not
deleted as infrastructure failures.

---

# 14. Artifact Layout

```text
artifacts/paper_eval/
  apc_aligned_baseline/runs/apc-baseline-dev-20260817-001/
    ... immutable U0/A0/P(C=2) input chain ...

  membind_v31/
    V31_REUSE_AUDIT.json
    V31_BASELINE_ACCEPTANCE.json
    V31_METHOD_PLAN.json
    V31_CONTROL_COMMIT.json
    V31_EXECUTION_ENVELOPE.json
    V31_WORKLOAD_COMPLEXITY.json
    STATE_CUT_CERTIFICATION.json
    CANONICAL_PROJECTION_FREEZE.json
    DETERMINISTIC_SERIALIZABILITY_RESULT.json
    autoresearch/<probe-run-id>/
      PROBE_AUTHORIZATION.json
      PROBE_METHOD_PLAN.json
      BASELINE_PREFIX_REFERENCE.json
      results.tsv
      candidates/<candidate-id>/
        block/result.json
        CANDIDATE_DECISION.json
    runs/<run-id>/
      PLAN.json
      SMOKE_GATE.json
      blocks/<block-id>/
        manifest.json
        events.jsonl
        spans.jsonl
        llm.jsonl
        embedding.jsonl
        db.jsonl
        graph_work.jsonl
        queue.jsonl
        direct_violations.jsonl
        certification_records.jsonl
        checkpoint.json
        result.json

  aligned_main_table/runs/<table-run-id>/
    INPUT_BINDINGS.json
    PER_HISTORY_RESULTS.jsonl
    MECHANISM_ABLATION.json
    DEVELOPMENT_MAIN_TABLE.json
    DEVELOPMENT_MAIN_TABLE.csv
    DEVELOPMENT_MAIN_TABLE.md
    EXPERIMENT_REPORT.md
```

Every final artifact includes schema version, run/block ID, code and protocol
hashes, source/arrival/runtime identities, terminal status and a self-hash.

---

# 15. Development Main Table

The first table is:

| Method | QA Acc | Evidence R@10 | Direct Violations | P95 Freshness | Goodput | Makespan |
|---|---:|---:|---:|---:|---:|---:|
| U0-aligned | | | | | | |
| A0-aligned | | | | | | |
| P(C=2)-aligned | | | | | | |
| MemBind | | | 0 required | | | |

Table notes must state:

```text
DEVELOPMENT_EXPOSED, four histories, 188 episodes per method
history is the experimental unit
pooled episode quantiles are descriptive
common open-loop arrival trace and K_LLM
P(C=2) is an unsafe parallel reference
QA NQ when the common protocol is degenerate
cache results observational unless reset/control is proven
baseline and MemBind temporal run order was not fully counterbalanced
not a final held-out or statistical-significance table
```

The mechanism table reports Barrier/FIFO/MemBind performance, freshness,
request concurrency, frontier wait, safe-work fractions and cache/prefill
diagnostics on the representative development history.

---

# 16. Exact Baseline Acceptance Contract

The baseline run can be reused only when its verifier proves:

```text
run_id = apc-baseline-dev-20260817-001
methods exactly U0-aligned, A0-aligned, P(C=2)-aligned
histories exactly 07741c45, b6019101, 6071bd76, a2f3aa27
source counts exactly 49, 49, 46, 44 per method
12 unique fresh namespaces
all result/checkpoint/manifest payload hashes valid
complete contiguous terminal coverage
all performance fields derived from lifecycle events
correctness checker status MEASURED
quality results bound to the same namespaces and plan
no failure/disposition artifact invalidates the run
```

Baseline APC metrics may be reported, but P(C=2) is excluded from the
MemBind-FIFO versus MemBind cache-causality comparison because wrong-version
state can change its request multiset.

---

# 17. External Literature and Public-Code Audit

External browsing is not required to implement pure runtime invariants, but
the following audits are explicit preconditions before converting development
results into paper claims. Each audit pins URL, repository commit/release,
retrieval date and relevant file/hash.

1. Official LongMemEval cleaned data and evaluator: confirm task routing,
   session evidence semantics, Reader/Judge rubric and exact revision.
2. Graphiti v0.29.3 source: confirm actual `add_episode` call graph, previous
   episode retrieval semantics, EdgeExtract inputs, async mutation lifetime and
   publication boundary. Code evidence, not blog descriptions, controls the
   adapter.
3. vLLM 0.26.0 official code/docs: confirm request metrics, cached-token
   accounting, APC/cache-salt behavior, chunked prefill, effective prefix-match
   granularity and whether a safe cache reset exists.
4. Agentix, DistServe, Sarathi-Serve and NanoFlow artifacts: verify shared
   resource-envelope, open-loop load, goodput/tail-latency and ablation
   practices used to justify the systems evaluation structure.
5. LongMemEval/Graphiti-family/A-Mem/MemoryAgentBench public reproduction code:
   distinguish exact numeric reproduction from protocol-aligned local-stack
   evaluation and document construction/evaluation decoupling.

No online source may override the pinned local Graphiti/vLLM code actually used
by the experiment. Conflicts are recorded rather than silently reconciled.

---

# 18. Stop Conditions

Stop and diagnose when any of the following occurs:

```text
frozen methodology/workplan/hash identity mismatch
baseline terminal artifact chain invalid
construction/embedding/Neo4j unavailable at live gate
future-evidence capability or Compile-before-arrival
no certifiable evidence-bound operator
certified Compile touches persistent mutable state
deterministic/captured checkpoint parity failure
wrong predecessor, out-of-order or duplicate/lost publication
hidden state mutation after Publish
global request inflight exceeds K_LLM
secret/private content enters public artifacts
MemBind smoke incomplete or hard-violation count > 0
quality/runtime identity differs across comparable rows
```

Do not rescue a failed method by increasing sample size, changing load,
weakening correctness, moving operators without certification, changing model
limits, or adding a new cache heuristic after seeing measured results.

---

# 19. Immediate Execution Order

```text
1. Freeze this v3.1 workplan and hashes.
2. Audit current reusable code and running APC baseline read-only; this produces
   no terminal baseline-acceptance authority.
3. RED -> GREEN arrival/fence, certification, artifact, ROB/frontier,
   request-admission, adapter and reducer tests.
4. Run focused, related and full offline regressions; persist JUnit/hash evidence.
5. Materialize the source-bound live plan; it grants live execution only and
   contains no baseline-acceptance placeholder.
6. Recoverably pause the APC process at a durable checkpoint and confirm the
   shared backend is idle; do not seal its incomplete artifacts as results.
7. Perform one read-only construction/embedding/Neo4j readiness check.
8. Run the three-episode final-method smoke in tmux.
9. Run the bounded autoresearch development probe on the fixed 12-episode
   representative prefix; keep all probe artifacts non-mergeable.
10. If a candidate is kept, rerun focused/related/full offline tests, regenerate
   V0 and the source-bound plan, then run the smoke again with a fresh attempt.
11. Run the four complete MemBind development blocks (plan blocks 0-3) in tmux
   with per-publication checkpoints.
12. Resume the exact APC process. After its baseline/Quality chain is terminal,
    verify it and emit V31_BASELINE_ACCEPTANCE.json.
13. When the backend is idle again, run representative Barrier/FIFO blocks 4-5.
14. Bind the unchanged live plan and acceptance with V31_CONTROL_COMMIT.json.
15. Run the four new MemBind units in the common read-only quality overlay.
16. Reduce U0/A0/P(C=2)/MemBind into JSON/CSV/Markdown main table and report.
17. STOP before PILOT, FINAL_PAPER_TEST, second backend or new heuristic.
```

This order is intentionally small-first and reuse-aware. It does not repeat
completed baseline work, but it also does not let historical partial methods
stand in for the frozen v3.1 architecture.

Before any later final-paper evaluation is authorized, a separate versioned
plan must freeze: held-out histories; balanced/counterbalanced method order;
light, saturation and overload arrival points; history-level paired confidence
analysis; a qualified common quality evaluator; and a dedicated or otherwise
cache-controlled APC experiment if the backend-locality Claim E is retained.
None of those future gates expands this development execution.
