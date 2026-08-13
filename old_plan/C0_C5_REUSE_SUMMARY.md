# C0-C5 to P1-P3 Reuse Strategy: Executive Summary

> Date: 2026-08-13  
> Task: Map completed C0-C5 Native characterization to P1-P3 baseline qualification

---

## Key Finding

C0-C5 provides **strong infrastructure reuse** (environment, instrumentation, measurement framework) but **limited experimental reuse** because C0-C5 characterized Native Graphiti alone, while P1-P3 requires comparative U0/D0 baseline qualification.

---

## Reuse Matrix

### ✓ Direct Reuse (No New Work)

| Component | C0-C5 Evidence | Impact |
|---|---|---|
| **Environment** | 64K envelope, embedding fingerprint, Neo4j 5.26.0 | No re-qualification needed |
| **C1 Instrumentation** | 1.317% median overhead, `clean_pass` | Report overhead descriptively in P1-P3 |
| **Phase boundaries** | `phase_map.json` (LLM/embed/DB/publication) | Apply same instrumentation to U0/D0 |
| **Calibration data** | 4 histories (188 episodes total) | Use for U0/D0/M1/M2 |
| **Checkpoint format** | C2/C5 per-episode durability, JSONL streams | Apply to U0/D0/M0/M1/M2 |

### ~ Framework Reuse (Adapt to New Data)

| Framework | C0-C5 Source | P1-P3 Application |
|---|---|---|
| **Measurement harness** | C2 telemetry, phase occupancy, work volume | Measure U0 and D0 with same instrumentation |
| **Dependency analysis** | C3 `dependency_map.json`, interval classifier | Compute D0-specific arrival-ready fraction |
| **Structural bounds** | C3 Amdahl computation (22.92% → 1.13-1.25x) | Framework reusable; D0 bounds are new data |

### ✗ New Work (Not Covered by C0-C5)

| P1-P3 Stage | Requirement | Why Not Reusable | Effort Estimate |
|---|---|---|---|
| **P1 (V4)** | U0 execution | C0-C5 measured Native only | 188 episodes, ~1 day |
| **P1 (V4)** | D0 execution | C0-C5 measured Native only | 188 episodes, ~1 day |
| **P1 (V4)** | U0/D0 canonical graph F1 | No ground-truth comparison in C5 | Graph extraction + F1 |
| **P1 (V4)** | Evidence Recall@10 | C5 QA was retrieval-only, no generation | Frozen query trace + top-10 |
| **P1 (V4)** | D0 guardrail | D0 ≥ U0-1pp, F1≥0.95, token [0.95,1.05] | New verification |
| **P2 (V5)** | M0 oracle | C0-C5 had no correctness replay | Oracle design + creation |
| **P2 (V5)** | M1/M2 mechanisms | Future MemBind solution | Post-C6 verdict design |
| **P2 (V5)** | Concurrency tuning | C5 tuned Native, not M1/M2 | 4 histories × C={1,2,4,8} |
| **P3 (V6)** | 8-instance evaluation | C5 used 1 screening history | 24 correctness + 48 performance |

---

## Critical Gaps

1. **No U0/D0 evidence**: C0-C5 measured only Native. P1 requires fresh U0 and D0 runs to verify D0 representativeness.
2. **No M0 oracle**: P2/P3 correctness lanes require M0 capture with read-only M1/M2 replay. C0-C5 had no oracle.
3. **No M1/M2 implementation**: C5 measured naive whole-update parallelism (problem statement), not the MemBind mechanisms.
4. **No formal evaluation**: C5 used 1 screening history; P3 requires 8 evaluation instances with blocked order.

---

## What C0-C5 Evidence Shows

| Experiment | Result | P1-P3 Relevance |
|---|---|---|
| **C0** | Native end-to-end viability (1 episode, 21.72s) | ✓ Engineering prerequisite passed |
| **C1** | Instrumentation overhead 1.317% median | ✓ Qualified for U0/D0 |
| **C2** | Native: 34.72s median, 116.97s p95; 99.29% LLM | Problem size established; U0/D0 need fresh data |
| **C3** | 22.92% arrival-ready, Amdahl 1.13-1.25x | Framework reusable; D0 bounds computed fresh |
| **C4** | Sync vs Async latency/freshness trade-off | Diagnostic only; U0/D0 are both synchronous serial |
| **C5** | C=8 makespan -51.1%, but source-order violation | Problem evidence; not a P1-P3 baseline |

---

## Recommended P1-P3 Strategy

### Phase 1: P1 (V4) — U0/D0 Baseline Qualification (~2-3 days)

**Reuse**:
- ✓ Qualified environment (64K, embedding, Neo4j)
- ✓ C1 instrumentation (report overhead descriptively)
- ✓ C2 measurement harness (phase boundaries, telemetry, checkpoint)
- ✓ C3 dependency framework (static rules, interval classifier)
- ✓ 4 calibration histories

**New work**:
1. Fresh U0 execution: 188 episodes, 4 histories
2. Fresh D0 execution: 188 episodes, 4 histories, deterministic adapters
3. Canonical graph comparison: Entity/edge F1 per history
4. Evidence Recall@10: Frozen query trace, RRF top-10
5. Guardrail verification: D0 ≥ U0-1pp, F1≥0.95, LLM exact, token [0.95,1.05]
6. D0 phase characterization: Apply C3 framework to D0 traces
7. DELTA_MS freeze: Coarse D0 profiling (after guardrail passes)

### Phase 2: P2 (V5) — M1/M2 Tuning (Depends on C6 verdict)

**Precondition**: C6 issues `PROBLEM_SUPPORTED` or `PARTIAL`

**New work**:
1. M0 oracle design: Namespace schema, capture protocol
2. M0 oracle creation: 188 episodes, 4 histories
3. M1 mechanism: TDD implementation, dependency-aware scheduling
4. M2 mechanism: TDD implementation, late-binding replay
5. M1 bounded diagnostic: 1 history, oracle replay, divergence classification
6. M1/M2 tuning: 4 histories × C={1,2,4,8}, quality-feasible filtering
7. Best-tuned freeze: Manifests, schedule, selection artifact

### Phase 3: P3 (V6) — Formal Evaluation (~5-7 days)

**New work**:
1. 8 evaluation instances: Split generation, exposure quarantine
2. Correctness lane: 24 runs (8 × M0/M1/M2), M2 8/8 parity gate
3. Performance lane: 48 runs (8 × 3 methods × 2 repeats), blocked order
4. Work-volume verification: LLM/embedding/token ledgers, [0.95,1.05]
5. V7 analysis: Effect size, 95% CI, GO/INCONCLUSIVE/NO-GO

---

## Effort Breakdown

| Stage | Reuse % | New Work | Estimated Effort |
|---|---|---|---|
| **P1 (V4)** | ~60% (environment + instrumentation) | U0/D0 execution + guardrail | 2-3 days |
| **P2 (V5)** | ~40% (checkpoint format + histories) | M0 oracle + M1/M2 + tuning | Depends on C6 + M1/M2 design |
| **P3 (V6)** | ~30% (oracle + checkpoint) | 72-run formal evaluation | 5-7 days |

---

## Critical Decision Point

**C6 verdict gates P2-P3 work**:
- `PROBLEM_SUPPORTED` → Proceed to P1, then P2/P3 with full M1/M2
- `PARTIAL` → Proceed to P1, P2/P3 scope may be reduced
- `NOT_SUPPORTED` → P2/P3 not authorized; only P1 U0/D0 for baseline documentation

**Next immediate action**: Complete C6 verdict using C0-C5 evidence before starting P1.

---

## Summary: What C0-C5 Gives Us

**Infrastructure (Direct Reuse)**:
- ✓ Qualified host stack (64K envelope, embedding fingerprint, Neo4j)
- ✓ Instrumentation framework (C1 overhead qualified, phase boundaries)
- ✓ Measurement harness (C2 telemetry, checkpoint format, JSONL streams)
- ✓ Dependency analysis (C3 static rules, interval classifier, Amdahl framework)
- ✓ 4 calibration histories (188 episodes total)

**Evidence (Reference Only)**:
- Native construction is expensive (34.72s median, 99.29% LLM)
- 22.92% arrival-ready, structural Amdahl 1.13-1.25x (Native-specific)
- Naive C=8 improves throughput 104.3% but violates source order (problem statement)

**Gaps (New Work Required)**:
- U0/D0 comparative baseline execution and guardrail verification
- M0 oracle design and creation
- M1/M2 mechanism implementation (post-C6)
- M1/M2 concurrency tuning
- Formal 8-instance evaluation (24 correctness + 48 performance)

---

**Conclusion**: C0-C5 provides solid measurement infrastructure (60% of P1, 40% of P2, 30% of P3), but P1-P3 experimental scope requires substantial new work because C0-C5 characterized the problem (Native only), not the solution baselines (U0/D0) or mechanisms (M1/M2).
