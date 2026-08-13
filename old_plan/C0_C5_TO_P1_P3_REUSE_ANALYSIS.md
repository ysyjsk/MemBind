# C0-C5 Native Characterization to P1-P3 Baseline Qualification: Reuse Analysis

> Analysis date: 2026-08-13  
> Context: Mapping completed C0-C5 Native Graphiti characterization to P1-P3 (V4-V6) baseline qualification requirements  
> Protocol: `native-characterization-v1.1` → `current-validation-v1.3` P1-P3 stages

## Executive Summary

C0-C5 provides substantial instrumentation, environment, and measurement infrastructure that can be reused for P1-P3, but the experimental scope differs fundamentally. C0-C5 characterized **Native Graphiti alone** to establish the problem; P1-P3 requires **comparative baseline qualification** of U0 (Upstream-Qualified-Graphiti-Serial) versus D0 (Deterministic-Graphiti-Serial), followed by formal M1/M2 correctness and performance evaluation.

**Key finding**: C0-C5's instrumentation qualification (C1), measurement infrastructure (C2), and dependency analysis framework (C3) are directly reusable. The experimental protocols, correctness oracle, and multi-method comparison framework are new P1-P3 work.

---

## 1. What C0-C5 Already Provides

### 1.1 Environment and Infrastructure Qualification (DIRECTLY REUSABLE)

| C0-C5 Component | Evidence | P1-P3 Reuse Status |
|---|---|---|
| **64K serving envelope** | `native_characterization_64k_serving_envelope_20260812.json` | ✓ Direct reuse |
| **Embedding fingerprint** | `embedding_model_fingerprint.json`, SHA256 `5f5a8400...` | ✓ Direct reuse |
| **Neo4j deployment** | `neo4j_daemon_status.json`, Community 5.26.0 | ✓ Direct reuse |
| **vLLM identity** | qwen3-32b-fp8, vLLM 0.26.0, BF16/FP8, `max_model_len=65536` | ✓ Direct reuse |
| **Frozen topology** | Machine A (models), Machine B (Graphiti+Neo4j) | ✓ Direct reuse |

**Rationale**: P1-P3 use the same qualified host stack. C0-C5 already proved the 64K envelope, embedding deployment, and database identity. No re-qualification needed.

### 1.2 Instrumentation Qualification (DIRECTLY REUSABLE)

| C1 Evidence | Result | P1-P3 Status |
|---|---|---|
| **A/A qualification** | 5 paired trace-off/trace-on runs | ✓ Reuse qualification |
| **Median overhead** | 1.317% (frozen threshold ≤2%) | ✓ Below guardrail |
| **Classification** | `clean_pass` | ✓ Overhead report-only |
| **Phase map** | `phase_map.json` with LLM/embedding/DB/publication spans | ✓ Reuse for U0/D0 |

**Artifact**: `/data/predator/ly/MemBind/membind-validation/artifacts/tdd/native_characterization_c1_aa_qualification_20260810.json`

**P1-P3 decision**: The frozen instrumentation contract is `qualified_overhead_report_only`. P1-P3 uses the same phase boundaries and interval-union accounting. No A/A re-qualification needed; report observed overhead as descriptive.

### 1.3 Construction Breakdown (C2/E1) - Framework Reusable, Data Not

**What C2 provides**:
- 188 episodes across 4 calibration histories
- Per-episode telemetry: service latency, phase occupancy, LLM/embedding/DB work volume
- Median service latency 34.72s, p95 116.97s
- LLM transport 99.29% occupancy
- Durable checkpoint format, JSONL streams, event/span/manifest contracts

**P1-P3 reuse**:
- ✓ **Instrumentation harness**: The `add_episode()` wrapper, phase boundaries, telemetry fields, checkpoint format
- ✓ **4 calibration histories**: Same frozen `07741c45`, `b6019101`, `6071bd76`, `a2f3aa27`
- ✗ **C2 timing/volume data**: C2 measured only Native; P1-P3 must independently measure U0 and D0

**Gap**: C2's 188-episode breakdown is Native-only evidence. P1-P3 needs fresh U0 and D0 runs using the same harness.

### 1.4 Dependency Characterization (C3/E2) - Framework and Baseline Reusable

**What C3 provides**:
- Static dependency classification: D0 (episode-only), D1 (immutable history-prefix), D2 (latest graph), D3 (mutation), unknown
- Conservative arrival-ready opportunity: 22.92%
- Structural Amdahl bounds at C={2,4,8}: 1.13x, 1.21x, 1.25x
- Analysis framework: `dependency_map.json`, interval classification, arrival-readiness tracking

**Artifact**: `/data/predator/ly/MemBind/membind-validation/artifacts/native_characterization/e2_dependency_opportunity.json`

**P1-P3 reuse**:
- ✓ **Static dependency map**: The `dependency_map.json` is Graphiti source-code audit, independent of execution
- ✓ **Analysis framework**: Interval classification, arrival-ready computation, structural bounds
- ~ **Quantitative bounds**: C3's 22.92% and Amdahl numbers are Native-specific; P1-P3 can reuse the framework to compute D0's bounds

**Decision**: P1-P3 reuses C3's static dependency rules and analysis code. D0's actual phase occupancy and arrival-ready fraction are measured in P1 (V4), not assumed from C2.

### 1.5 Sync vs Async-Serial (C4/E3) - Diagnostic Only, Not Baseline Evidence

**C4 status**: `incomplete_invalid_non_mergeable` due to verification `TypeError`

**What C4 provides**:
- Qualitative latency/freshness trade-off pattern
- Open-loop schedule framework, normalized load proxies, caller-return vs post-return stale window
- 10/10 workload blocks completed, but final artifact explicitly `mergeable=false`

**P1-P3 applicability**:
- ✗ C4 is non-mergeable and compares Native methods only
- P1-P3's U0/D0 comparison is **not** a sync/async trade-off; both are synchronous serial baselines
- C4's schedule/queueing framework is irrelevant to P1-P3's correctness and iso-workload performance comparison

**Decision**: C4 is retained diagnostic motivation for the broader MemBind problem, but provides no P1-P3 baseline evidence.

### 1.6 Serial vs Parallel (C5/E4) - Problem Evidence, Not Baseline

**What C5 provides**:
- 4 blocks at C={1,2,4,8}, 49 episodes each, history `07741c45`
- Performance: C=8 makespan -51.1%, throughput +104.3%
- Correctness: Direct source-order invariant violation at C={2,4,8}
- Graph/retrieval parity: Confounded (live LLM outputs not replay-fixed)
- Supplemental QA: `accuracy=0.0` for all C, including C=1 (not comparative)

**Artifact**: `/data/predator/ly/MemBind/membind-validation/artifacts/native_characterization/runs/c5-e3867c66ba92e7da/e4_whole_parallel.json`

**P1-P3 applicability**:
- ✗ C5 is Native-only; it does not measure U0 or D0
- ✗ C5's naive whole-update parallelism is not a P1-P3 baseline; U0 and D0 are both **serial**
- P1-P3 correctness lane requires fresh M0 capture and M1/M2 read-only replay with oracle parity; C5 has no oracle

**Decision**: C5 is the problem statement (naive concurrency violates order). P1-P3 baselines are U0/D0 serial; M1/M2 mechanisms are separate future work after P1-P3 qualification.

---

## 2. P1-P3 Requirements vs C0-C5 Coverage

### 2.1 P1 (V4): U0/D0 Representativeness Guardrail

**P1 Requirements**:
1. Instrumentation OFF/ON replay parity (embedding vector identity)
2. U0 = Upstream-Qualified-Graphiti-Serial (+ only provider adapter)
3. D0 = U0 + declared deterministic candidate-ordering adapters
4. 4 calibration histories: completion rate, episode/source coverage, canonical F1, Evidence Recall@10, LLM calls/tokens, latency
5. **Preregistered guardrail**: D0 macro Recall@10 ≥ U0 - 1pp, canonical entity/edge F1 ≥ 0.95, LLM call count equal, token ratio [0.95, 1.05]
6. D0 coarse phase characterization and DELTA_MS freeze (only after guardrail passes)

**C0-C5 Coverage**:
- ✓ **Instrumentation OFF/ON**: C1 A/A is reusable, but P1 requires **embedding vector bitwise identity** check (not phase-occupancy overhead)
- ✓ **Calibration histories**: Same 4 histories
- ✗ **U0 execution**: Not measured; C0-C5 used only Native
- ✗ **D0 execution**: Not measured; C0-C5 used only Native
- ✗ **Canonical graph F1, Recall@10**: Not measured; C5 QA was retrieval-based and returned `accuracy=0.0` without ground-truth graph
- ✗ **Guardrail verification**: Requires fresh U0/D0 runs

**Gap Summary**:
| P1 Requirement | C0-C5 Status | Work Needed |
|---|---|---|
| Instrumentation OFF/ON replay parity | Partial (overhead qualified) | Fresh embedding identity check |
| U0 execution (4 histories) | Not done | Fresh 188-episode U0 run |
| D0 execution (4 histories) | Not done | Fresh 188-episode D0 run |
| Canonical graph comparison | Not done | U0/D0 entity/edge F1 |
| Evidence Recall@10 | Not done | U0/D0 retrieval parity |
| D0 phase characterization | Partial framework (C2/C3) | D0-specific phase breakdown |
| DELTA_MS freeze | Not done | D0 coarse profiling after guardrail |

**Reuse strategy**:
- Use C2's instrumentation harness and calibration histories
- Use C3's dependency framework to compute D0's arrival-ready fraction
- Run fresh U0 and D0 with identical phase instrumentation
- Compute canonical graph intersection/union from final Neo4j state
- Freeze DELTA_MS only after D0 passes guardrail

### 2.2 P2 (V5): Quality-Feasible M1/M2 Concurrency Tuning

**P2 Requirements**:
1. Bounded M1 read-only replay diagnostic (qualified M0 oracle)
2. M1/M2 tuning over C={1,2,4,8} on calibration only
3. **Quality-feasible**: correctness/retrieval/completion/exactly-once guardrails pass
4. **Selection objective**: minimize calibration median makespan; tie-break by smallest C
5. Freeze best-tuned M1 and M2 before P3 (V6)

**C0-C5 Coverage**:
- ✗ **M0 oracle**: Not created; C0-C5 had no correctness replay
- ✗ **M1 implementation**: Not done (M1 is future MemBind mechanism)
- ✗ **M2 implementation**: Not done (M2 is future MemBind mechanism)
- ✗ **Concurrency tuning**: C5 measured Native at C={1,2,4,8}, not M1/M2

**Gap Summary**: P2 is entirely new work. C0-C5 provides no M1/M2 evidence.

### 2.3 P3 (V6): Formal Correctness and Performance Evaluation

**P3 Requirements**:
1. **Correctness lane**: 8 instances × (M0 capture + M1 replay + M2 replay) = 24 runs
2. **Performance lane**: 8 instances × 3 methods × 2 repeats = 48 runs
3. M2 must achieve 8/8 parity with zero oracle miss/fallback before performance lane opens
4. Blocked `(question_id, repeat)` order, balanced permutations, contiguous blocks
5. Work-volume guardrail: LLM call exact match, input/output/embedding ratio [0.95, 1.05]

**C0-C5 Coverage**:
- ✗ **M0/M1/M2 correctness**: Not done
- ✗ **Formal 8-instance evaluation**: C5 used 1 history × 4 concurrency blocks, not 8 unique evaluation instances
- ✗ **Oracle parity**: No oracle exists
- ✗ **Performance blocked order**: C5 used fixed 49-episode history, not balanced evaluation blocks

**Gap Summary**: P3 is entirely new work. C0-C5's 196 C5 episodes (4 blocks × 49) are calibration screening, not formal evaluation.

---

## 3. Structured Reuse Mapping

### 3.1 Direct Reuse (No New Work)

| Component | C0-C5 Artifact | P1-P3 Use |
|---|---|---|
| 64K serving envelope | `native_characterization_64k_serving_envelope_20260812.json` | Host qualification |
| Embedding fingerprint | `embedding_model_fingerprint.json` | Oracle namespace binding |
| Neo4j identity | `neo4j_daemon_status.json` | Environment evidence |
| C1 instrumentation qualification | `native_characterization_c1_aa_qualification_20260810.json` | Overhead report-only |
| Phase map | `phase_map.json` | U0/D0 instrumentation |
| 4 calibration histories | `freeze_reference_aligned_64k.json` | U0/D0/M1/M2 calibration |

### 3.2 Framework Reuse (Apply to New Data)

| Framework | C0-C5 Source | P1-P3 Application |
|---|---|---|
| Instrumentation harness | C2 telemetry/checkpoint/JSONL format | U0/D0 measurement |
| Dependency analysis | C3 `dependency_map.json`, interval classifier | D0 phase characterization |
| Structural Amdahl bounds | C3 arrival-ready computation | D0 opportunity bounds |
| Per-episode durability | C2/C5 checkpoint format | M0/M1/M2 correctness runs |

### 3.3 New P1-P3 Work (Not Covered by C0-C5)

| P1-P3 Requirement | Why Not Reusable | Estimated Scope |
|---|---|---|
| **U0 execution** | C0-C5 measured only Native | 188 episodes, 4 histories |
| **D0 execution** | C0-C5 measured only Native | 188 episodes, 4 histories |
| **U0/D0 canonical graph F1** | C5 had no ground-truth graph | Graph intersection/union per history |
| **U0/D0 Evidence Recall@10** | C5 QA was retrieval-only, no generation | Frozen query trace + top-10 comparison |
| **M0 oracle creation** | C0-C5 had no correctness replay | Namespace design, LLM+embedding capture |
| **M1/M2 implementation** | Future MemBind mechanisms | Separate design/TDD after C6 verdict |
| **M1 read-only replay** | Requires M0 oracle + M1 mechanism | Bounded diagnostic before tuning |
| **M2 read-only replay** | Requires M0 oracle + M2 mechanism | 8/8 parity gate for performance |
| **Concurrency tuning (M1/M2)** | C5 tuned only Native | 4 × C={1,2,4,8} quality-feasible sweep |
| **Formal 8-instance evaluation** | C5 used 1 screening history | 8 evaluation IDs, 24 correctness + 48 performance |
| **Blocked counterbalanced order** | C5 used fixed history | `(question_id, repeat)` blocks, seed 20260806 |

---

## 4. Concrete Reuse Strategy by Stage

### 4.1 P1 (V4) Reuse Strategy

**Reuse**:
1. **Environment**: Qualified 64K envelope, embedding fingerprint, Neo4j identity (no re-qualification)
2. **Instrumentation**: C1 A/A qualification result (report overhead descriptively)
3. **Calibration data**: Same 4 histories from `freeze_reference_aligned_64k.json`
4. **Measurement harness**: C2 instrumentation wrapper, phase boundaries, telemetry fields, checkpoint format
5. **Dependency framework**: C3 `dependency_map.json`, interval classifier, arrival-ready computation

**New work**:
1. **Instrumentation OFF/ON replay parity**: Fresh embedding vector bitwise identity check (V1 retained-artifact analysis is historical only)
2. **U0 execution**: 188 episodes, 4 histories, fresh Neo4j namespaces per history
3. **D0 execution**: 188 episodes, 4 histories, deterministic candidate-ordering adapter applied
4. **Canonical graph comparison**: Final entity/edge sets per history, intersection/union F1
5. **Evidence Recall@10**: Frozen query trace, RRF retrieval, top-10 session ID comparison
6. **Guardrail verification**: D0 Recall@10 ≥ U0 - 1pp, entity/edge F1 ≥ 0.95, LLM exact, token [0.95, 1.05]
7. **D0 phase characterization**: Apply C3 framework to D0 traces, compute D0-specific arrival-ready fraction
8. **DELTA_MS freeze**: Coarse D0 profiling (only after guardrail passes)

**Estimated effort**: ~2-3 days (U0/D0 live runs, canonical graph extraction, guardrail verification, D0 phase analysis)

### 4.2 P2 (V5) Reuse Strategy

**Reuse**:
1. **Environment and instrumentation**: Same as P1
2. **Calibration histories**: Same 4 histories
3. **Checkpoint format**: C5 per-episode durability, block structure

**New work** (depends on C6 verdict and M1/M2 design):
1. **M0 oracle design**: Namespace schema, LLM response + embedding vector capture, prompt alignment
2. **M0 oracle creation**: 188 episodes, 4 histories, durable oracle writes
3. **M1 mechanism implementation**: TDD, dependency-aware scheduling, deterministic adapters
4. **M2 mechanism implementation**: TDD, late-binding protocol, oracle replay
5. **M1 bounded diagnostic**: 1 history, M0 oracle replay, divergence classification (not final semantic claim)
6. **M1 tuning**: 4 histories × C={1,2,4,8}, quality-feasible filtering, minimize calibration median makespan
7. **M2 tuning**: 4 histories × C={1,2,4,8}, quality-feasible filtering, minimize calibration median makespan
8. **Best-tuned freeze**: Manifest, schedule, selection artifact

**Estimated effort**: Depends on C6 verdict and M1/M2 complexity; not estimable from C0-C5 alone.

### 4.3 P3 (V6) Reuse Strategy

**Reuse**:
1. **Environment and instrumentation**: Same as P1/P2
2. **M0 oracle**: Created in P2
3. **Checkpoint format**: C5 durability

**New work**:
1. **8 evaluation instances**: Split generation, exposure quarantine
2. **Correctness lane**: 8 × (M0 capture + M1 replay + M2 replay) = 24 runs, fresh Neo4j per run
3. **M2 8/8 parity gate**: Zero oracle miss/fallback, canonical graph parity, retrieval guardrail
4. **Performance lane**: 8 × 3 methods × 2 repeats = 48 runs, blocked `(question_id, repeat)` order, balanced permutations
5. **Work-volume verification**: Per-method LLM/embedding/token ledgers, [0.95, 1.05] guardrails
6. **Stability warning**: Repeat gap >10% flagged descriptively

**Estimated effort**: ~5-7 days (24 correctness runs, 48 performance runs, parity verification, work-volume analysis)

---

## 5. Gaps and Risks

### 5.1 Critical Gaps

| Gap | Impact | Mitigation |
|---|---|---|
| **No U0/D0 evidence** | Cannot verify D0 representativeness | Fresh P1 U0/D0 runs required |
| **No M0 oracle** | Cannot run M1/M2 correctness | P2 oracle design/creation required |
| **No M1/M2 implementation** | Cannot tune or evaluate | Post-C6 verdict design required |
| **No formal evaluation instances** | Cannot claim 8-instance pilot | P3 split generation required |

### 5.2 Reuse Risks

| Risk | Probability | Mitigation |
|---|---|---|
| **D0 fails guardrail** | Medium | If entity/edge F1 <0.95 or Recall@10 drops >1pp, report D0 as qualified but document external-validity limit |
| **M1 has no quality-feasible C>1** | Low-Medium | Report full speed-quality frontier; Best-Tuned may be C=1 |
| **M2 fails 8/8 parity** | Low-Medium | Blocks performance lane; report correctness divergence |
| **Instrumentation overhead drifts** | Low | C1 established <2% median; report P1/P3 overhead descriptively |

### 5.3 Non-Reusable C0-C5 Evidence

| C0-C5 Component | Why Not Reusable for P1-P3 |
|---|---|
| C2 timing/volume data | Native-only; U0/D0 need independent measurement |
| C3 quantitative bounds (22.92%, Amdahl 1.13x-1.25x) | Native-specific; D0 bounds computed fresh in P1 |
| C4 sync/async comparison | Non-mergeable; U0/D0 are both synchronous serial |
| C5 performance data (C=8 makespan -51.1%) | Native-only; irrelevant to serial U0/D0 comparison |
| C5 correctness violations | Motivates problem; does not qualify U0/D0 baselines |
| C5 QA `accuracy=0.0` | Retrieval-only, no ground truth; P1 needs Recall@10 with frozen query |

---

## 6. Recommended P1-P3 Execution Plan

### Phase 1: P1 (V4) U0/D0 Baseline Qualification (~2-3 days)

1. **Reuse qualified environment**: 64K envelope, embedding fingerprint, Neo4j identity (no new qualification)
2. **Instrumentation OFF/ON parity**: Fresh embedding vector identity check (V1 retained-artifact analysis complete, but verify fresh bitwise identity)
3. **U0 execution**: 188 episodes, 4 histories, C2 harness, fresh Neo4j namespaces
4. **D0 execution**: 188 episodes, 4 histories, deterministic adapters, fresh namespaces
5. **Canonical graph comparison**: Entity/edge intersection/union F1 per history
6. **Evidence Recall@10**: Frozen query trace, RRF top-10, session ID comparison
7. **Guardrail verification**: D0 ≥ U0 - 1pp Recall@10, F1 ≥ 0.95, LLM exact, token [0.95, 1.05]
8. **D0 phase characterization**: Apply C3 framework to D0 traces, compute arrival-ready fraction
9. **DELTA_MS freeze**: Coarse D0 profiling after guardrail passes

### Phase 2: P2 (V5) M1/M2 Tuning (Depends on C6 verdict)

**Precondition**: C6 issues `PROBLEM_SUPPORTED` or `PARTIAL` verdict

1. **M0 oracle design**: Namespace schema, capture protocol, alignment rules
2. **M0 oracle creation**: 188 episodes, 4 histories, durable LLM+embedding capture
3. **M1 mechanism**: TDD implementation, dependency-aware scheduling
4. **M2 mechanism**: TDD implementation, late-binding replay protocol
5. **M1 bounded diagnostic**: 1 history, oracle replay, divergence classification
6. **M1 tuning**: 4 histories × C={1,2,4,8}, quality-feasible filtering, best-tuned selection
7. **M2 tuning**: 4 histories × C={1,2,4,8}, quality-feasible filtering, best-tuned selection
8. **Freeze manifests**: Best-tuned M1/M2, schedule, selection artifact

### Phase 3: P3 (V6) Formal Evaluation (~5-7 days)

1. **Split generation**: 8 evaluation instances, exposure quarantine
2. **Correctness lane**: 24 runs (8 × M0/M1/M2), M2 8/8 parity gate
3. **Performance lane**: 48 runs (8 × 3 methods × 2 repeats), blocked order
4. **Work-volume verification**: LLM/embedding/token ledgers, [0.95, 1.05] guardrails
5. **V7 analysis**: Effect size, 95% CI, GO/INCONCLUSIVE/NO-GO verdict

---

## 7. Summary: Direct Reuse vs New Work

### Direct Reuse (No Modification)

- ✓ 64K serving envelope qualification
- ✓ Embedding fingerprint and Neo4j identity
- ✓ C1 instrumentation qualification (overhead report-only)
- ✓ 4 calibration histories
- ✓ C2 instrumentation harness (phase boundaries, telemetry, checkpoint format)
- ✓ C3 dependency framework (static rules, interval classifier, Amdahl computation)

### Framework Reuse (Apply to New Data)

- ~ C2 instrumentation → U0/D0 execution
- ~ C3 dependency analysis → D0 phase characterization
- ~ C5 checkpoint format → M0/M1/M2 durability

### New Work (Not Covered by C0-C5)

- ✗ U0 execution (188 episodes, 4 histories)
- ✗ D0 execution (188 episodes, 4 histories)
- ✗ U0/D0 canonical graph F1 and Evidence Recall@10
- ✗ D0 guardrail verification
- ✗ M0 oracle design and creation
- ✗ M1/M2 mechanism implementation (depends on C6 verdict)
- ✗ M1/M2 concurrency tuning
- ✗ Formal 8-instance evaluation (24 correctness + 48 performance)

---

## 8. Final Recommendation

**C0-C5 provides a strong foundation**: The qualified environment, instrumentation framework, calibration histories, and measurement infrastructure are directly reusable. However, **P1-P3 requires substantial new work** because C0-C5 characterized only Native Graphiti, while P1-P3 demands U0/D0 comparative baselines and formal M1/M2 evaluation.

**Critical path**:
1. **P1 (V4)**: Reuse C2/C3 framework, run fresh U0/D0, verify guardrail (~2-3 days)
2. **P2 (V5)**: Design M0 oracle, implement M1/M2, tune on calibration (depends on C6 verdict)
3. **P3 (V6)**: Formal 72-run evaluation with correctness and performance lanes (~5-7 days)

**Next immediate action**: Complete C6 verdict using C0-C5 evidence, then gate P1-P3 work on `PROBLEM_SUPPORTED` or `PARTIAL` outcome. If C6 issues `NOT_SUPPORTED`, P2-P3 are not authorized.
