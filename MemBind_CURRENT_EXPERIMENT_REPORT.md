# MemBind Native Graphiti Characterization: Current Experiment Report

> Report snapshot: 2026-08-15 02:07 CST
> Repository: `/data/predator/ly/MemBind`  
> Validation workspace: `/data/predator/ly/MemBind/membind-validation`  
> Authoritative workplan: `MemBind_NATIVE_GRAPHITI_CHARACTERIZATION_WORKPLAN_v1.1.md`  
> Current scope: Native Graphiti C0-C5 characterization plus the isolated paper-eval-v3 Native-v2 and S4 deterministic-control lane. Candidate-aware retry-005 capture completed, while replay failed closed on ambiguous edge candidate identity; all later live stages remain unauthorized.

Unless otherwise stated, every artifact path in this report is relative to the repository root shown above.

## 1. Executive summary

The current evidence supports a coherent but still bounded problem signal:

1. Native Graphiti construction is expensive. Across 188 episodes, median `add_episode()` service latency was 34.72 s and p95 was 116.97 s. LLM transport occupied 99.29% of the measured root interval union; embedding and database occupancy were comparatively small.
2. The construction path contains both state-independent and state-dependent work, but only part of the state-independent work is ready at episode arrival. D1 occupied 61.28% of total time, D2 occupied 38.67%, while the conservative arrival-ready opportunity fraction was 22.92%.
3. The C4 diagnostic run exhibits the expected online trade-off: synchronous execution keeps the post-return stale window at zero but blocks the caller, while Async-Serial returns quickly and shifts the delay into queueing and memory staleness. However, C4 is formally `incomplete_invalid_non_mergeable` because final verification raised a `TypeError`; its summary is diagnostic evidence only.
4. Naive Whole-Update Parallelism improves throughput but does not transparently preserve source publication order. At C=8, makespan fell by 51.1% and throughput rose by 104.3% relative to C=1, but C=2, C=4, and C=8 all produced a direct source-order invariant violation in the fixed screening history.
5. C5 graph and retrieval outputs also diverged from C=1, but live LLM outputs were not replay-fixed. Those parity mismatches are confounded and cannot independently prove a concurrency-induced semantic failure. The direct unconfounded C5 evidence is the source-order counterexample.
6. Supplemental C5 QA produced `accuracy=0.0` for every concurrency, including C=1, and performed no Reader generation. It provides no comparative quality conclusion. The Judge itself passed its separate 14-item synthetic qualification, which establishes only Judge-fixture agreement.

The formal C6 verdict (`PROBLEM_SUPPORTED`, `PARTIAL`, or `NOT_SUPPORTED`) has not been generated. This report therefore summarizes current evidence and does not authorize MemBind/M2 mechanism implementation.

## 2. Scope, exclusions, and claim discipline

The frozen research sequence is:

```text
C0  one-episode Native viability
 -> C1 instrumentation qualification
 -> C2 E1 Native construction breakdown
 -> C3 E2 state-dependency characterization
 -> C4 E3 Native Sync vs Async-Serial
 -> C5 E4 Native Serial vs Whole-Update Parallel
 -> C6 problem verdict and immediate STOP
```

The active research subject is pinned Native Graphiti, not the exploratory MemBind solution. M1/M2, Parallel Compile, Latest-State Bind, ordered commit, the temporary GPT-5.5/GPT-5.4-mini adapters, and their temporary plans are excluded from this report. The C0-C5 wave is bounded screening: it does not claim statistical significance, a real workload distribution, a failure rate, universal safety, or universal insufficiency.

## 3. Experimental environment

The final C2-C5 envelope was:

| Component | Frozen identity/configuration |
|---|---|
| Graphiti | `graphiti-core 0.29.3`, commit `021d3a57d511f21b10adaf7fa923bd5c1fce5e9d` |
| Construction model | `qwen3-32b-fp8`, vLLM `0.26.0`, BF16 compute, FP8 quantization |
| Context envelope | `max_model_len=65536`, YaRN factor `2.0`, original positions `32768`, `rope_theta=1000000` |
| Structured output | `json_schema`, `enable_thinking=false`, requested `max_tokens=16384` |
| Embedding | `qwen3-embedding-0.6b`, vLLM `0.26.0`, 1024 dimensions, BF16, last-token pooling, L2 normalization, no instruction prefix |
| Embedding fingerprint | `5f5a8400eeaa2f07d167d8b5b7e63d615945a8f54f506e02342840cd4e3fe626` |
| Graph store | Neo4j Community `5.26.0`, local non-Docker deployment |
| Cache policy | No prompt cache, no embedding cache, no cross-run cache carry-over |

The one-shot 64K admission probe used 26,024 prompt tokens plus requested `max_tokens=16,384` (42,408-token admission envelope), returned HTTP 200 and valid structured JSON, and produced no context, KV-cache, RoPE, or OOM error. This is a serving qualification, not a model-quality result.

Primary environment records:

- `membind-validation/artifacts/native_characterization/freeze_reference_aligned_64k.json`
- `membind-validation/artifacts/environment/native_characterization_64k_serving_envelope_20260812.json`
- `membind-validation/artifacts/environment/native_characterization_c5_live_preflight_20260813.json`
- `membind-validation/artifacts/environment/embedding_model_fingerprint.json`
- `membind-validation/artifacts/environment/embedding_runtime_identity_evidence.json`
- `membind-validation/artifacts/environment/neo4j_daemon_status.json`

## 4. Test-driven development and execution discipline

Implementation followed RED -> minimal GREEN -> focused regression -> full offline regression -> live authorization. Instrumentation, state transitions, artifact verification, interruption recovery, C4/C5 checkpointing, and Judge qualification all have explicit tests and durable logs.

Representative evidence:

| Area | TDD/process evidence |
|---|---|
| C0 | `membind-validation/artifacts/tdd/native_characterization_c0_red_20260811.log`; `native_characterization_c0_green_20260811.log`; `native_characterization_c0_completion_final_green_20260811.log` |
| C1 | `membind-validation/artifacts/tdd/native_characterization_c1_phase_install_red_005.log`; `native_characterization_c1_phase_install_green_006.log`; `native_characterization_c1_aa_qualification_20260810.json` |
| C2 | `membind-validation/artifacts/tdd/native_characterization_c2_analyzer_contract_intentional_red_20260811.log`; `native_characterization_c2_pre_live_full_offline_green_20260811.log`; `native_characterization_c2_completion_and_verifier_focused_green_20260812.log` |
| C3 | `membind-validation/artifacts/tdd/native_characterization_c3_focused_green_20260812.log`; `native_characterization_c3_completion_focused_green_20260812.log` |
| C4 | `membind-validation/artifacts/tdd/native_characterization_c4_schedule_core_focused_green_20260812.log`; `native_characterization_c4_resume_tdd_green_20260812.log`; `native_characterization_c4_terminal_recovery_tdd_green_20260812.log` |
| C5 | `membind-validation/artifacts/tdd/native_characterization_c5_live_core_intentional_red_20260813.log`; `native_characterization_c5_live_core_targeted_green_20260813.log`; `native_characterization_c5_focused_green_20260813.log`; `native_characterization_c5_post_authorization_full_regression_20260813.log` |
| Judge | `membind-validation/artifacts/tdd/judge_qualification_adversarial_intentional_red_20260812.log`; `judge_qualification_final_focused_sealed_20260813.log`; `judge_qualification_final_impact_sealed_20260813.log` |

The final pre-authorization C5 regression evidence records 84 focused tests, 80 stale-state tests, and 1,090 full offline tests passing. These tests validate implementation and artifact semantics; they are not additional live experimental repetitions.

## 5. C0: bounded Native viability

### Purpose and process

C0 asks only whether one pinned Native Graphiti episode can complete end to end through construction LLM, embedding, and Neo4j. It must remain a lightweight engineering prerequisite rather than grow into a research qualification matrix.

### Result

- Run ID: `c0-d620535ccf0f0f43`
- Status: `pass`
- Completed source sequence: `0`
- `add_episode()` latency: 21.72 s
- Result graph: 1 episode node, 1 episodic edge; no error code
- Interpretation: `engineering_viability_only_not_research_result`

This proves only that the bounded U0 path worked once. It does not establish stability, performance, repeatability, or representativeness.

### Records

- Result manifest: `membind-validation/artifacts/native_characterization/runs/c0-d620535ccf0f0f43/manifest.json`
- Checkpoint: `membind-validation/artifacts/native_characterization/runs/c0-d620535ccf0f0f43/checkpoint.json`
- Live log: `membind-validation/artifacts/tdd/native_characterization_c0_live_20260811.log`

## 6. C1: instrumentation qualification

### Purpose and process

C1 qualified characterization-only wrappers for phase spans, LLM, embedding, database, source attribution, publication boundaries, exception closure, JSONL durability, interval-union accounting, and secret redaction. Semantic parity between trace-off and trace-on was required before overhead classification.

The A/A fixture ran five alternating trace-off/trace-on pairs. This was a deterministic Graphiti-shaped offline fixture, not the C2 workload.

### Result

- Semantic parity: pass
- Pair count: 5
- Median paired overhead: 1.317%
- Min/max paired overhead: -0.369% / 2.588%
- Classification: `clean_pass` under the frozen median <=2% guardrail
- Event-sequence and deterministic state hashes matched the expected semantic surface

Individual paired overheads were 2.588%, 1.317%, 0.853%, 1.559%, and -0.369%. The maximum individual pair exceeding 2% does not overturn the result because the frozen classification statistic is the median paired ratio. The threshold is an internal instrumentation guardrail, not a universal systems-paper rule.

### Records

- Qualification artifact: `membind-validation/artifacts/tdd/native_characterization_c1_aa_qualification_20260810.json`
- Execution output: `membind-validation/artifacts/tdd/native_characterization_c1_aa_execution_20260810.log`
- Focused GREEN: `membind-validation/artifacts/tdd/native_characterization_c1_aa_green_20260810.log`
- Instrumentation phase-map: `membind-validation/artifacts/native_characterization/phase_map.json`

## 7. C2 / E1: Native construction breakdown

### Purpose and process

C2 ran Native Graphiti on four frozen calibration histories using fresh graph namespaces and per-episode durable checkpoints. It recorded total service/publication latency, non-additive phase interval unions, LLM/embedding/database work volume, errors, graph-prefix size, and provenance.

The completed run is `c2-17cdaabd562e9673`. Earlier attempts remain failed/non-mergeable evidence and were not merged into the result. Their failure classes included connection interruption, the old 40,960-token serving-envelope HTTP 400, structured-output JSON decode failures, and validation errors. After the 64K serving envelope was qualified, the completed run restarted from a clean namespace and source sequence 0.

### Completion and workload

- Histories: 4
- Episodes: 188
- Total `add_episode()` interval union: 9,081.84 s (151.36 min)
- Median service latency: 34.72 s
- P95 service latency: 116.97 s
- Median/p95 publication latency: 15.41 ms / 58.63 ms
- Telemetry completeness: complete; no required fields missing
- LLM, embedding, and DB errors in the completed run: 0 / 0 / 0

Per-history results:

| History | Episodes | Interval union | Median service | P95 service | LLM calls | Embedding calls | DB queries |
|---|---:|---:|---:|---:|---:|---:|---:|
| `07741c45` | 49 | 2,458.50 s | 31.27 s | 108.33 s | 566 | 552 | 1,309 |
| `b6019101` | 49 | 2,387.79 s | 34.96 s | 117.17 s | 617 | 626 | 1,569 |
| `6071bd76` | 46 | 2,220.11 s | 32.87 s | 108.74 s | 359 | 346 | 809 |
| `a2f3aa27` | 44 | 2,015.44 s | 40.98 s | 120.27 s | 347 | 332 | 798 |

### Phase occupancy and work volume

The percentages below are interval-union occupancy within the root `add_episode()` interval. Nested phases overlap, so rows must not be summed as independent costs.

| Phase/surface | Occupancy |
|---|---:|
| LLM transport | 99.290% |
| LLM overall | 99.525% |
| Edge extraction | 38.365% |
| Node extraction | 22.915% |
| Node resolution | 19.279% |
| Attributes/summary | 17.302% |
| Edge resolution | 2.090% |
| Candidate search | 0.313% |
| Database | 0.293% |
| Embedding | 0.161% |
| Candidate embedding | 0.111% |
| Publication | 0.044% |

Aggregate work volume:

- 1,889 logical LLM calls and 1,891 transport attempts
- 18,019,175 LLM input tokens and 224,489 output tokens
- 2 LLM retries, with no terminal LLM or transport error
- 1,856 embedding calls over 4,676 texts, dimension 1,024
- 4,485 DB queries, 188 transactions, and 752 writes
- 20,868 candidates and 1,595 candidate-search calls

### Analysis

The primary bottleneck is the LLM-serving path, not local embedding or Neo4j. `llm-transport` includes the complete remote request interval, including server queueing and generation; it must not be interpreted as pure network RTT. Construction cost is also variable: the p95 is more than three times the median. Because episode content and graph prefix co-vary, these traces do not causally attribute latency growth to graph size.

### Records

- Primary result: `membind-validation/artifacts/native_characterization/e1_breakdown.json`
- Run-local result: `membind-validation/artifacts/native_characterization/runs/c2-17cdaabd562e9673/e1_breakdown.json`
- Verified manifest/inventory: `membind-validation/artifacts/native_characterization/runs/c2-17cdaabd562e9673/manifest.json`
- Root checkpoint: `membind-validation/artifacts/native_characterization/runs/c2-17cdaabd562e9673/checkpoint.json`
- Per-history traces/checkpoints: `membind-validation/artifacts/native_characterization/runs/c2-17cdaabd562e9673/blocks/`
- LLM/embedding/DB/event/span streams: the `llm.jsonl`, `embedding.jsonl`, `db.jsonl`, `events.jsonl`, and `spans.jsonl` files in that run directory
- Independent verification: `membind-validation/artifacts/diagnostics/native_characterization_c2-17cdaabd562e9673_verification.json`
- Final live console: `membind-validation/artifacts/tdd/native_characterization_c2-c2-17cdaabd562e9673_live_20260812.log`
- Earlier failure diagnostics: `membind-validation/artifacts/diagnostics/native_characterization_c2-4cc7d0599bbbbdac_serving_envelope_failure.json`; `native_characterization_c2_second_structured_failure_20260811.json`; `native_characterization_c2-2fe3711c62933407_interruption.json`

## 8. C3 / E2: state-dependency characterization

### Purpose and process

C3 reused the C2 trace and combined it with a static Graphiti source audit. Intervals were classified as D0 episode-only, D1 immutable source/history-prefix, D2 latest materialized graph, D3 mutation/publication, or unknown. Arrival readiness was tracked separately: a phase can be state-independent but still unavailable at arrival because it depends on an earlier phase's output.

### Aggregate result

| Dependency class | Fraction of root interval union |
|---|---:|
| D0 | 0.000% |
| D1 | 61.284% |
| D2 | 38.671% |
| D3 | 0.044% |
| Unknown | 0.00038% |

The conservative arrival-ready opportunity was:

```text
p_L = p_U = 0.2291969 (22.92%)
```

The bounds are equal because no unknown interval was eligible to increase `p_U`. Structural Amdahl-style upper bounds were:

| Parallelism C | `S_C(p_L)` / `S_C(p_U)` |
|---:|---:|
| 2 | 1.129x |
| 4 | 1.208x |
| 8 | 1.251x |

Per-history arrival-ready fractions varied substantially: 14.62%, 18.52%, 38.54%, and 21.04%. This heterogeneity is relevant to any later design, but the current wave has no confirmation repetitions.

### Analysis

The trace supports structural separation, but not the simplistic claim that all expensive extraction is immediately parallelizable. Node extraction and previous-context work were verified D1 and arrival-ready. Edge extraction was D1 but depended on extracted-node input and was therefore not arrival-ready. Node resolution, edge resolution, and attributes/summary were D2 because they read or branch on current graph state. Publication was D3.

Thus, the useful opportunity is real but narrower than the raw 61.28% D1 fraction: the conservative directly arrival-ready fraction is 22.92%. The 1.13x-1.25x numbers are structural upper bounds that ignore remote model capacity, batching, contention, ordering, instrumentation, and publication costs. They are not predictions of a final runtime and do not authorize a mechanism.

### Records

- Primary result: `membind-validation/artifacts/native_characterization/e2_dependency_opportunity.json`
- Static dependency rules/evidence: `membind-validation/artifacts/native_characterization/dependency_map.json`
- Phase definitions: `membind-validation/artifacts/native_characterization/phase_map.json`
- C2 source traces: `membind-validation/artifacts/native_characterization/runs/c2-17cdaabd562e9673/blocks/`
- TDD evidence: `membind-validation/artifacts/tdd/native_characterization_c3_focused_green_20260812.log`; `native_characterization_c3_completion_focused_green_20260812.log`

## 9. C4 / E3: Native Sync versus Async-Serial

### Formal status

```text
run_id = c4-8e76fba0288047f9
root status = incomplete_invalid_non_mergeable
completed blocks in summary = 10/10
completed episodes in summary = 490/490
final failure stage = verification
error class = builtins.TypeError
```

All ten workload blocks and their durable block/episode evidence were produced, but final verification failed. The generated `e3_sync_async.json` explicitly has `mergeable=false`. Therefore the following numbers are bounded diagnostic evidence only and must not be cited as a formal mergeable C4 result.

### Process

The experiment used history `07741c45`, 49 episodes, a deterministic open-loop schedule, and five normalized offered-load proxies `{0.5, 0.8, 1.0, 1.2, 1.5}`. Both methods used the same frozen reference service time and interarrival schedule:

- Native-Sync: caller returns at publication.
- Native-Async-Serial: caller returns after durable enqueue; one FIFO worker publishes in source order.

### Diagnostic results

| Load | Interarrival | Sync caller return | Sync max backlog | Async caller return | Async post-return stale | Async max backlog |
|---:|---:|---:|---:|---:|---:|---:|
| 0.5 | 100.347 s | 114.81 s | 6 | 0.176 s | 110.75 s | 6 |
| 0.8 | 62.717 s | 281.72 s | 11 | 0.267 s | 235.82 s | 10 |
| 1.0 | 50.173 s | 468.16 s | 15 | 0.055 s | 401.32 s | 14 |
| 1.2 | 41.811 s | 563.62 s | 18 | 0.0013 s | 543.60 s | 18 |
| 1.5 | 33.449 s | 694.44 s | 25 | 0.0012 s | 690.40 s | 25 |

At load 1.5, backlog at the final arrival was 25 for Sync and 24 for Async; both eventually drained to zero. Across all five loads, Sync's post-return stale window was zero by definition, while Async returned rapidly and left memory invisible for approximately the construction-plus-queue delay.

### Analysis and boundary

The diagnostic pattern is internally consistent with the intended latency/freshness tension: background serialization removes construction from the caller path but does not increase the one-worker construction service rate. As offered load rises, queueing and visibility delay grow sharply. However, because the attempt is non-mergeable, this evidence should be used only to motivate or debug C6 reasoning, not as a publication-ready G3 result. It does not include a frozen query/deadline trace and makes no task-quality claim.

### Records

- Non-mergeable summary: `membind-validation/artifacts/native_characterization/runs/c4-8e76fba0288047f9/e3_sync_async.json`
- Terminal checkpoint/failure: `membind-validation/artifacts/native_characterization/runs/c4-8e76fba0288047f9/checkpoint.json`
- Event stream: `membind-validation/artifacts/native_characterization/runs/c4-8e76fba0288047f9/events.jsonl`
- Schedule and manifest: `membind-validation/artifacts/native_characterization/runs/c4-8e76fba0288047f9/schedule.json`; `manifest.json`
- Resume rollback audit: `membind-validation/artifacts/native_characterization/runs/c4-8e76fba0288047f9/resume_rollback_audit.json`
- Earlier operator checkpoint stop: `membind-validation/artifacts/diagnostics/native_characterization_c4-8e76fba0288047f9_operator_stop_20260812.json`
- Recovery tests: `membind-validation/artifacts/tdd/native_characterization_c4_resume_tdd_green_20260812.log`; `native_characterization_c4_terminal_recovery_tdd_green_20260812.log`

## 10. Judge qualification

### Purpose and result

The supplemental binary Judge was independently qualified on 14 frozen synthetic items before use in C5:

- Judge runtime: `qwen3-32b-fp8` on vLLM `0.26.0`
- Status: `PASS`, complete and mergeable
- Eligible/terminal items: 14/14
- Agreement: 14/14 (100%)
- Confusion matrix: TP=7, TN=7, FP=0, FN=0
- Cohen's kappa: 1.0
- Invalid outputs, retries, and service errors: 0

This establishes agreement only on the frozen synthetic qualification surface. It does not establish general Judge validity and is not itself a Native Graphiti performance or quality result.

### Records

- Qualification summary: `membind-validation/artifacts/judge_qualification/runs/jq-b00a9689796c1e67/qualification_summary.json`
- Frozen fixture: `membind-validation/artifacts/judge_qualification/runs/jq-b00a9689796c1e67/fixture_freeze.json`
- Runtime identity: `membind-validation/artifacts/judge_qualification/runs/jq-b00a9689796c1e67/runtime_identity.json`
- Event stream and checkpoint: `membind-validation/artifacts/judge_qualification/runs/jq-b00a9689796c1e67/events.jsonl`; `checkpoint.json`
- Execution report: `membind-validation/artifacts/diagnostics/judge_qualification_final_test_execution_20260813.md`

## 11. C5 / E4: Naive Whole-Update Parallel

### Purpose and process

C5 compared complete Native Graphiti `add_episode()` units at C={1,2,4,8}. Each block used the same fixed 49-episode history `07741c45`, a fresh namespace, source-ordered dispatch, work-conserving workers, per-episode durable intent/publication records, and a block checkpoint. Concurrency was the intended treatment; graph-producing LLM calls remained live and were not replay-fixed.

The first run was interrupted by a host restart during block 1. Resume logic preserved completed block 0, discarded 42 partial events and 20 partial episode checkpoints for block 1, cleaned only that partial namespace, and restarted block 1 from source sequence 0. The rollback is recorded and the discarded partial prefix is not merged into the final result.

### Completion integrity

- Run ID: `c5-e3867c66ba92e7da`
- Root status: `complete`
- Artifact verifier: `verified`, `mergeable=true`
- Completed blocks: 4/4
- Episode checkpoints: 196
- Events: 392 = 196 intent + 196 publication
- Failure events: 0
- Lost, duplicate, unexpected, or publication-loss episodes: 0
- Service, transaction, and timestamp/temporal invariant errors: 0

### Performance result

| C | Makespan | Throughput | Speedup vs C=1 | Parallel efficiency | Mean visibility lag |
|---:|---:|---:|---:|---:|---:|
| 1 | 2,403.57 s (40.06 min) | 0.02039 ep/s | 1.000x | 100.0% | 1,406.70 s |
| 2 | 1,613.13 s (26.89 min) | 0.03038 ep/s | 1.490x | 74.5% | 815.58 s |
| 4 | 1,266.10 s (21.10 min) | 0.03870 ep/s | 1.898x | 47.5% | 599.56 s |
| 8 | 1,176.49 s (19.61 min) | 0.04165 ep/s | 2.043x | 25.5% | 516.20 s |

At C=8, makespan decreased 51.1%, throughput increased 104.3%, and mean visibility lag decreased 63.3% relative to C=1. Scaling was strongly sublinear: moving from C=4 to C=8 reduced makespan by only 7.1%.

### Correctness and parity result

| C | Source-order invariant | Canonical graph parity vs C=1 | Retrieval parity vs C=1 | Frozen interpretation |
|---:|---|---|---|---|
| 1 | Pass | Pass | Pass | `NO_NAIVE_PARALLEL_INSUFFICIENCY_OBSERVED` |
| 2 | Violation | Mismatch | Mismatch | `DIRECT_INVARIANT_VIOLATION_OBSERVED` |
| 4 | Violation | Mismatch | Mismatch | `DIRECT_INVARIANT_VIOLATION_OBSERVED` |
| 8 | Violation | Mismatch | Mismatch | `DIRECT_INVARIANT_VIOLATION_OBSERVED` |

Retrieval similarity to C=1 was low:

| C | Top-10 set overlap | Rank-biased overlap |
|---:|---:|---:|
| 2 | 0.100 | 0.098 |
| 4 | 0.222 | 0.300 |
| 8 | 0.200 | 0.255 |

The source-order violations are direct evidence under the frozen protocol: all expected episodes were published exactly once, but publication order differed from source order. This is an existence counterexample for the fixed history/interleaving. It shows that naive whole-update concurrency is not a transparent replacement when source-ordered visibility is part of the current semantics.

Canonical graph and retrieval mismatches are weaker evidence. The block artifact explicitly records `live_graph_outputs_fixed=false`; independent live model generations can change the graph trajectory even without the concurrency treatment. Therefore parity divergence is reported as confounded and cannot independently establish a semantic failure or failure rate.

### Supplemental QA

All four QA calls returned `SUCCESS`, with no retry or service error, but every block reported:

```text
accuracy = 0.0
reader_generation_performed = false
headline_interpretation_effect = none
```

Because C=1 is also zero, this surface does not show an accuracy regression caused by concurrency. It is retrieved-evidence answerability without Reader answer generation and should not be used as a task-quality claim.

The Graphiti console emitted source/target-entity-not-found warnings during construction. The final metrics nevertheless report no service or transaction error, and the logs contain no traceback, HTTP context failure, or OOM. These warnings are retained as graph-construction/data-quality diagnostics rather than infrastructure failures.

### Records

- Formal result: `membind-validation/artifacts/native_characterization/runs/c5-e3867c66ba92e7da/e4_whole_parallel.json`
- Root checkpoint: `membind-validation/artifacts/native_characterization/runs/c5-e3867c66ba92e7da/checkpoint.json`
- Full event stream: `membind-validation/artifacts/native_characterization/runs/c5-e3867c66ba92e7da/events.jsonl`
- Per-block and per-episode checkpoints: `membind-validation/artifacts/native_characterization/runs/c5-e3867c66ba92e7da/blocks/`
- Manifest/schedule: `membind-validation/artifacts/native_characterization/runs/c5-e3867c66ba92e7da/manifest.json`; `schedule.json`
- Interruption recovery audit: `membind-validation/artifacts/native_characterization/runs/c5-e3867c66ba92e7da/resume_rollback_audit.json`
- Initial and resumed console logs: `membind-validation/artifacts/native_characterization/c5_live_console_c5-e3867c66ba92e7da.log`; `c5_live_console_resume_c5-e3867c66ba92e7da.log`
- Preflight: `membind-validation/artifacts/environment/native_characterization_c5_live_preflight_20260813.json`

## 12. Cross-experiment analysis

### Observation 1: Native construction is materially expensive

C2 measured a 34.72 s median and 116.97 s p95 per episode. The construction path is overwhelmingly occupied by LLM-serving intervals and has a long tail. This is sufficient to make construction material in online regimes whose interarrival time is of the same order, although the current screening does not establish a real workload arrival distribution.

### Observation 2: Dependency structure creates a bounded opportunity

C3 found a large D1 fraction but a much smaller arrival-ready fraction. This distinction matters: reporting 61.28% as immediately parallelizable would be incorrect. The defensible opportunity is that at least 22.92% of measured root time was verified D0/D1 with input ready at arrival, while significant D2 work remains tied to latest materialized state.

### Observation 3: Removing caller blocking does not remove serial capacity pressure

C4's non-mergeable diagnostics show the qualitative trade-off clearly. Async-Serial returns almost immediately but leaves a post-return stale window that approaches 690 s at load 1.5, while Sync blocks the caller for roughly 694 s. Both see a maximum backlog of 25 at that load. Because C4 verification failed, this observation still needs a clean formal closure or a C6 decision that explicitly treats it as unresolved.

### Observation 4: Coarse concurrency trades semantics for throughput

C5 demonstrates that whole-update concurrency can roughly double throughput, but source-ordered publication already fails at C=2 in the one fixed screening history. This rules out the claim that uncoordinated whole-update parallelism is a transparent serial substitute under the frozen source-order invariant. It does not prove a general failure probability, nor does it yet determine which dependency-aware mechanism is best.

### Current overall assessment

The strongest defensible statement before C6 is:

> On the frozen Native Graphiti stack, construction is slow and LLM-serving dominated; the path contains both arrival-ready state-independent work and substantial latest-state-dependent work. A bounded online diagnostic shows a caller-blocking versus stale-visibility trade-off, and a complete C5 screening shows that naive whole-update concurrency improves throughput but produces a source-order counterexample. These observations motivate a dependency-aware runtime research question, but do not yet select or authorize MemBind's mechanism.

## 13. Limitations and unresolved evidence

1. C2-C5 are bounded screening experiments with no repeated live treatment blocks and no significance or confidence-interval claim.
2. Only Graphiti v0.29.3 and one primary construction/embedding stack were characterized. Generality to Mem0, Zep variants, other models, or other graph stores is unknown.
3. C4 is non-mergeable due to a final verifier `TypeError`; its numbers are diagnostic, not formal C6 evidence without an explicit bounded-use decision.
4. C5 uses one fixed history and one observed interleaving per concurrency. It establishes an existence counterexample, not frequency, repeatability, or universal unsafety.
5. C5 live LLM outputs were not replay-fixed, so graph and retrieval parity differences are confounded.
6. Supplemental C5 QA did not generate answers and yielded zero across every block. It cannot support comparative accuracy claims.
7. LLM transport occupancy includes server queueing and generation and does not isolate network latency, batch scheduling, or model compute.
8. C3 speedup bounds are structural only and ignore service capacity, batching, contention, ordering, and implementation overhead.
9. No frozen online query/deadline trace was used, so the current online experiment makes no task-quality or deadline-miss claim.
10. C6 and `DESIGN_DECISION.md` do not yet exist; no formal problem verdict or post-characterization mechanism authorization has been issued.

## 14. Artifact integrity index

Key file SHA256 values at this report snapshot:

| Artifact | SHA256 |
|---|---|
| C0 checkpoint | `97f5847cf6722f61f3f970f9e2532573d14a3b34317c4adac33f11901ec85a43` |
| C0 manifest | `64712bf07b8ce499159884ff27db3010ac34a8c763a737573e77841b690ef6b0` |
| C1 qualification | `3465a1e3b5a340debe53008111f4391376d7e28e76b7ec4941cbade2374ba328` |
| C2 E1 breakdown | `b06deae7a1387a6705adb5f897c92856fda6f55bebb1c277a39965bdeda952cb` |
| C3 E2 opportunity | `a80ca5a8e763c19eea9d2cde1dbe001425200d04c857384cb862cc65ccf1887f` |
| C4 checkpoint | `efd1259fc9cfc80710582d5ccc67a41a95d10c79a28e6f0787fcfae9336b27d4` |
| C4 non-mergeable summary | `0eccec9d6bde66d9ad194682f78dd9c02eaf50e3d98f29742238b24299b9b956` |
| Judge qualification summary | `31a16da6d8a668517315ec22c5b375f9c494f34dbb5c62428be86c39dd028485` |
| C5 checkpoint | `e91cd9f1278e5b1e1a4d60897918c33dd0ec8f09960b76401562badfd4579b95` |
| C5 formal result | `00ebfe67c13758a02fbb2dcbc94a336de92f88dbe25e666b3e069d7737c3594d` |
| C5 events | `52a69edd8ff94c1eaca5ca00401ccb75e3d4f39dc326364cbdf3e322ead5e849` |

## 15. Current stage and next permitted work

C5 characterization remains complete, verified, and mergeable; C6 remains
unproduced. That characterization lane is retained as historical motivation
rather than being expanded while the separate paper-eval-v3 plan establishes
its common evaluation layer.

The characterization lane remains closed. In the isolated paper-eval-v3 lane,
the Native-v2 configuration is frozen and both S4 retry-004 and retry-005 are
now diagnosed. The current authorized action is narrower:

```text
preserve failed retry-004 and retry-005 as non-mergeable evidence
 -> preserve retry-005 replay namespace for explicit read-only diagnosis
 -> make an explicit offline design decision for ambiguous edge identities
 -> no cleanup or new S4 live attempt before a new sealed authority
 -> do not start four-history qualification, S5, or PILOT
```

Neither the Reader-v2 canary nor a future C6 verdict automatically authorizes
MemBind/M2, PILOT, or method comparison.

## 16. Paper-eval-v3 S2 completion update

The isolated paper-evaluation lane completed one bounded, one-shot Native U0
retrieval -> Reader -> Judge chain on DEVELOPMENT_EXPOSED history `07741c45`.
The policy was Graphiti 0.29.3 Episode BM25/RRF evaluated as LongMemEval
sessions at top 10. It was frozen from benchmark/API semantics, with honest
disclosure that S2-R0 had already been observed; no candidate policy search or
numeric score selection was performed.

### Result

| Surface | Value |
|---|---:|
| Retrieved/gold sessions | 10 / 2 |
| Covered gold sessions | 2 |
| Gold ranks | 2, 1 |
| Evidence Recall@10 (`Recall_all`) | 1.0 |
| QA Accuracy | 0.0 |
| Reader prompt/completion tokens | 27,814 / 13 |
| Reader truncations/retries | 0 / 0 |
| Judge status/parse | SUCCESS / valid NO |

The chain performed exactly one Graphiti search, one Reader request, and one
Judge request; it performed zero construction LLM, embedding, cross-encoder,
mutation, cleanup, or retry operations. Neo4j issued two read-routed requests.

Offline hash analysis showed that the formal ranked-list hash exactly matches
the successful R0 list. The updated gold session ranked first and the prior
state ranked second. Under official chronological Reader presentation, the
prior state appeared at position 1 and the update at position 8. The Reader
output hash exactly matches a known stale-prior-state answer candidate, so the
QA zero is a real stale-state Reader error, not retrieval miss, service
failure, context overflow, Judge parser error, or Judge false negative.

This single development item is not a general quality estimate. It supports
only the bounded statement that Episode retrieval made both relevant sessions
available, while the pinned Qwen3 Reader failed temporal update selection. The
result is `REVIEW_REQUIRED`, `result_mergeable=false`, `s3_ready=false`, and
`s3_authorized=false`. No automatic rerun, prompt/order/top-k tuning, or S3
freeze was performed.

Detailed report:

- `paper-eval-v3/S2_COMPLETION_RESULT_REPORT_20260814.md`

Primary evidence:

- `paper-eval-v3/artifacts/paper_eval/native/runs/s2-completion-20260814-001/S2_COMPLETION_RESULT.json`
- `paper-eval-v3/artifacts/paper_eval/native/runs/s2-completion-20260814-001/events.jsonl`
- `paper-eval-v3/artifacts/paper_eval/native/runs/s2-completion-20260814-001/checkpoint.json`
- `paper-eval-v3/logs/TDD_FULL_OFFLINE_GREEN_S2_COMPLETION_POSTLIVE_20260814.xml` (`377 passed`)

## 17. Paper-eval-v3 Native Reader-v2 update

The direct S2 failure motivated a transparent, non-blind Reader version change.
Pinned LongMemEval source inspection established that its recommended public
`READING_METHOD=con` is a single answer completion with `cot=true`,
`con=false`, and default `max_tokens=800`; it is not the multi-call
`con-separate` path. Graphiti construction, Episode BM25/RRF retrieval, K=10,
session values, chronological presentation, JSON, both conversation sides,
Qwen3-32B, and the Judge were kept fixed.

The preselected DEVELOPMENT_EXPOSED compatibility canary was `b6019101`, the
first remaining item after the exposed direct sample in the frozen calibration
order. Its sealed C2 namespace was used read-only. Because that graph has a
disclosed construction-revision difference from current S1 U0, its numerical
values are adapter diagnostics only.

Result:

| Surface | Value |
|---|---:|
| Compatibility | PASS |
| Retrieved/gold/covered sessions | 10 / 2 / 2 |
| Gold ranks | 1, 2 |
| Evidence Recall@10 | 1.0 |
| QA diagnostic | 1.0 |
| Reader prompt/completion tokens | 26,205 / 131 |
| Reader truncations | 0 |
| Judge status/parse | SUCCESS / YES |

The chain used exactly one Graphiti search, two Neo4j reads, one Reader, and one
Judge request. Construction LLM, embedding, cross-encoder, mutation, cleanup,
and retry counts were all zero. QA was not a compatibility gate: the same
freeze would have been produced for any valid QA=0 or QA=1 response.

The common-policy freeze binds the same Reader and Judge identities for
U0/A0/P*/M*. It authorizes an S3 configuration update only; it does not
authorize PILOT/S4 or make a Native quality claim.

Final TDD state:

```text
Reader-v2 focused tests      74 passed
paper-eval-v3 full offline  451 passed
git diff --check             passed
```

Detailed report and primary artifacts:

- `paper-eval-v3/NATIVE_READER_V2_QUALIFICATION_RESULT_REPORT_20260814.md`
- `paper-eval-v3/artifacts/paper_eval/native/runs/native-reader-v2-canary-20260814-001/NATIVE_READER_V2_RESULT.json`
- `paper-eval-v3/artifacts/paper_eval/native/NATIVE_READER_V2_FREEZE.json`
- `paper-eval-v3/logs/TDD_FULL_OFFLINE_GREEN_NATIVE_READER_V2_RECONCILED_FINAL_20260814.xml`

## 18. Paper-eval-v3 S3 Native baseline v2 freeze

The Native-v2 configuration is now frozen without changing the historical
Gate-C implementation or any failed S2 artifact. The accepted baseline path
keeps Graphiti construction, Episode BM25/RRF retrieval, K=10, dataset,
session adapter, Qwen3-32B, and Judge fixed, while using the already qualified
single-call LongMemEval CoN Reader-v2 for every method.

No 8-16 item qualification wave, K sweep, retrieval redesign, extra benchmark,
extra baseline, or model change was added. The Reader-v2 canary's QA/Recall is
not present in the common method-policy identity and was not a freeze gate.

Frozen common identity:

```text
U0 == A0 == P* == M*
method policy SHA256
5699b88d83ad71de1119930ece69a9176c352ed847ea02be0cacc661b46e79e8
```

The S3 artifact explicitly limits its meaning:

```text
configuration_freeze_only       true
s2_quality_pass_claimed          false
quality_estimate_status          NOT_ESTIMATED
s4_live_execution_authorized     false
pilot_execution_authorized       false
```

A construction revision evidence mismatch is disclosed rather than hidden:
S0's declared revision is `aa55da1...`, while the S1-bound runtime source
constant is `6e2312b...`. The constant did not enter requests and cache was
disabled, so S1 remains usable; a lightweight live identity preflight is
required before S4 live authority.

TDD result:

```text
S3-v2 focused                   24 passed
pre-seal full offline          475 passed
production pointer              2 passed
git diff --check                passed
```

Primary records:

- `paper-eval-v3/S3_NATIVE_BASELINE_V2_FREEZE_RESULT_REPORT_20260814.md`
- `paper-eval-v3/artifacts/paper_eval/native/NATIVE_BASELINE_V2_FREEZE.json`
  (`3e935af2cb353fb59c4cf58ddec7e44a73387f88410d805324636b76daf2d5e6`)
- `paper-eval-v3/runtime/CURRENT_STAGE_STATUS.json`
  (`3cb7edad4bab3ac6fe961a3d9e8768cbb962cf61cf946cb7e0015d74c0edc26d`)

The S3 pointer remains the latest successful stage. A failed S4 qualification
does not advance it.

## 19. Paper-eval-v3 S4 D0 smoke result

The valid retry-004 U0 capture completed all 49 episodes for exposed history
`07741c45`. It resolved 511 logical prompts and 1,206 embedding inputs, with
zero unexpected oracle item, fallback, or cross-encoder call. Capture exported
its canonical graph and exact-cleaned its namespace to `0/0` nodes and
relationships.

D0 replay then completed two episodes and failed closed at source sequence 2:

```text
error class                    UnexpectedPromptError
replay logical prompts         9
replay embedding resolutions  17
live LLM / embedding calls     0 / 0
fallback / cross-encoder       0 / 0
unexpected prompt / embedding  1 / 0
```

The prompt and embedding caches did not change. Independent hash-only
diagnosis proved that `dedupe_nodes.nodes` received the same two candidates in
a different presentation order. D0's stable sort reassigned positional
`candidate_id` values, and the reconstructed sorted capture prompt exactly
matched the failed replay hash. The classification is
`ORDER_ONLY_CANDIDATE_RENUMBERING_CONFIRMED`.

This is a correctness result, not a serving failure. Graphiti's node and edge
dedupe responses contain position-indexed entity/fact references, so blindly
treating reordered prompts as cache hits can silently resolve a different
entity or edge. The replay remains permanently non-mergeable. Its namespace
was exact-cleaned from 4 nodes and 3 relationships to `0/0`; S4 qualification,
S5, and PILOT remain unauthorized.

TDD closure after the diagnosis is `34` focused tests and `551` complete
paper-eval-v3 offline tests passing, with `git diff --check` clean.

Detailed report and primary evidence:

- `paper-eval-v3/S4_D0_SMOKE_RESULT_REPORT_20260815.md`
- `paper-eval-v3/artifacts/paper_eval/native/runs/s4-d0-capture-20260814-004/phase_result.json`
- `paper-eval-v3/artifacts/paper_eval/native/runs/s4-d0-replay-20260814-004/phase_result.json`
- `paper-eval-v3/artifacts/paper_eval/native/runs/s4-d0-replay-20260814-004/DIAGNOSIS_AND_INVALIDATION.json`
- `paper-eval-v3/logs/TDD_FULL_OFFLINE_GREEN_S4_POST_DIAGNOSIS_20260815.xml`

That diagnosis led to candidate-aware retry-005, whose result is recorded in
the next section. Retry-004 remains immutable historical evidence.

## 20. Paper-eval-v3 S4 candidate-remap retry-005

Retry-005 used the same exposed 49-episode history with fresh capture/replay
namespaces, fresh private caches, a new preflight, and a single-use authority.
The candidate oracle kept exact prompt lookup first and allowed semantic
translation only for Graphiti's active positional node- and edge-deduplication
surfaces. Missing members, partition drift, duplicate visible identities, bad
indices, and cache ambiguity were specified to fail closed.

U0 capture passed all 49 episodes:

```text
resolved prompts / embeddings  531 / 1,242
live LLM / embedding calls      532 / 67
unexpected / fallback / cross   0 / 0 / 0
capture namespace cleanup       0 nodes / 0 relationships
```

D0 replay completed source sequences `0..6` and stopped at source sequence 7:

```text
error class / code              CandidateRemapError /
                                AMBIGUOUS_CANDIDATE_IDENTITY
failure surface                 edge invalidation candidate partition
exact / remap prompt hits       44 / 24
node / edge remap hits          6 / 18
resolved prompts / embeddings   77 / 175
live LLM / embedding calls      0 / 0
unexpected / fallback / cross   0 / 0 / 0
```

The prompt and embedding cache hashes exactly matched the capture-sealed
hashes after failure. The current prompt-visible fields could not uniquely
identify duplicated edge invalidation candidates, so translating positional
indices would have risked resolving or invalidating the wrong edge. Stopping
was the required correctness behavior, not a serving failure.

Retry-005 is incomplete and non-mergeable. Its replay namespace is preserved
at 32 nodes and 48 relationships; no cleanup was performed. No smoke PASS
result, qualification activation, qualification namespace, S5 action, or
PILOT action was generated.

The activation-layer TDD added during stable capture wait passed 10 focused
tests and the complete paper-eval-v3 suite now passes 632 tests. This code can
only activate the exact sealed qualification plan after strict smoke PASS, so
it correctly produced no activation artifact for retry-005.

Detailed report and primary evidence:

- `paper-eval-v3/S4_D0_REMAP_RETRY_005_FAILURE_REPORT_20260815.md`
- `paper-eval-v3/artifacts/paper_eval/native/runs/s4-d0-capture-20260815-005/phase_result.json`
- `paper-eval-v3/artifacts/paper_eval/native/runs/s4-d0-replay-20260815-005/phase_result.json`
- `paper-eval-v3/logs/TDD_FULL_OFFLINE_GREEN_S4_RETRY_005_FAILURE_CLOSURE_20260815.xml`

The next step requires an explicit offline design decision: enrich edge
identity with independently captured evidence, adopt a disclosed trace-order
oracle, or reconsider the D0 candidate-presentation control. Do not weaken the
oracle, clean retry-005, or allocate retry-006 automatically.

## 21. Paper-eval-v3 S4 retry-005 edge-identity diagnosis

The bounded follow-up chose the stable logical-identity direction without
changing Native Graphiti, candidate selection/presentation, dataset,
retrieval, Reader, Judge, K, construction model, or workload. Existing
retry-005 evidence first confirmed that nine of ten source-7 edge-resolution
prompts contained the same prompt-visible fact twice; the terminal capture
graph contained two such edges with different directed endpoints.

A source-7-only cache-driven dry run then stopped at Graphiti's pre-prompt
edge boundary and collected all ten calls. Every invalidation partition had
ten candidates and ten distinct enriched logical identities. The result used
no position, rank, runtime UUID, group ID, Neo4j ID, or `created_at`.

```text
verdict                         SIDECAR_AMENDMENT_JUSTIFIED
source-7 calls                  10/10
network / live LLM / embedding  0 / 0 / 0
cross-encoder / DB writes       0 / 0
publication / cache writes      0 / 0
Neo4j reads                     62
pre/post namespace              32 nodes / 48 relationships / 7 episodes
pre/post snapshot hash          exact
pre/post cache hashes           exact
```

This does not prove a retry-005 capture/replay bijection because capture-side
internal candidate linkage was not recorded. It authorizes only offline TDD
for a bilateral capture-sidecar plus replay internal projection. Retry-005
remains incomplete/non-mergeable and preserved; retry-006, fixed-four
qualification, S5, and PILOT remain unauthorized.

TDD closed at 77 focused tests and 709 complete paper-eval-v3 tests passing.

Detailed report and primary evidence:

- `paper-eval-v3/S4_EDGE_IDENTITY_DIAGNOSIS_RESULT_REPORT_20260815.md`
- `paper-eval-v3/artifacts/paper_eval/native/S4_EDGE_IDENTITY_DIAGNOSIS_RETRY_005.json`
- `paper-eval-v3/logs/TDD_FULL_OFFLINE_GREEN_S4_EDGE_IDENTITY_D2_POST_LIVE_20260815.xml`

## 22. Paper-eval-v3 S4 bilateral-sidecar D3 qualification

The diagnosis-supported repair is now implemented and qualified offline
without modifying Native Graphiti or any common evaluation choice. Capture
associates the real namespace-normalized edge prompt hash with a durable,
hash-only internal candidate projection. Replay exposes the same projection
at the same pre-prompt boundary, proves partition-preserving logical identity
equality, translates only positional response references in memory, and
commits consumption only after the replay cache acknowledges the exact
binding.

The implementation excludes candidate position, rank, runtime UUID, group ID,
Neo4j ID, and `created_at` from semantic identity. Exact edge prompt hits do
not bypass the sidecar. A capture-prompt/replay-fast-path asymmetry is blocked
before Graphiti publication, while symmetric no-prompt fast paths create no
artificial record. Checkpoint resume reconstructs only the completed prefix;
future-source capture records, partial sealed prefixes, cache/sidecar drift,
remaining calls, and prepared leases all fail closed.

TDD closure:

```text
focused sidecar/production/contract tests     147 passed
complete paper-eval-v3 offline regression     801 passed
compileall                                    passed
git diff --check                              passed
live LLM / embedding / Neo4j actions          0 / 0 / 0
```

The fresh retry identity is sealed in:

- `paper-eval-v3/S4_BILATERAL_LOGICAL_EDGE_SIDECAR_AMENDMENT_v1.0.md`
- `paper-eval-v3/artifacts/paper_eval/native/S4_D0_SIDECAR_RETRY_006_CONTRACT.json`
  - file SHA256:
    `c8c25600d38da62b3560b07ac479f34303cc8337b63205875bb3caef074f7172`
  - contract SHA256:
    `17df1d5f5b312ccee1a5bf303c0dbc65ffec730f3e37f0f2d261ad98b6dd6008`
- `paper-eval-v3/logs/TDD_FOCUSED_GREEN_S4_SIDECAR_RETRY_006_20260815.xml`
  - SHA256:
    `45238bed22640fdd6e1f81b28b5766a408b15d6bb1803c7841a6d3b191894b22`
- `paper-eval-v3/logs/TDD_FULL_OFFLINE_GREEN_S4_SIDECAR_RETRY_006_20260815.xml`
  - SHA256:
    `2a4858ce7f24694231bc94d95a3746e1a58c815958a2ebf651044f4fa199104c`

The contract allocates fresh attempt `006` run/cache/namespace identities but
authorizes only a bounded read-only preflight. It explicitly leaves live
execution, authority consumption, fixed-four qualification, S5, and PILOT
unauthorized. No retry-006 namespace, cache, candidate sidecar, preflight
artifact, authority, or live result has been created. Retry-005 remains
incomplete/non-mergeable and its `32/48/7` replay prefix remains untouched.
