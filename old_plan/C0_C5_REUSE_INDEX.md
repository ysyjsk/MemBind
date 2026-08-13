# C0-C5 to P1-P3 Reuse Analysis: Document Index

> Date: 2026-08-13  
> Task: Analyze C0-C5 Native Graphiti characterization reuse for P1-P3 baseline qualification  
> Status: ✓ Analysis Complete

---

## Document Overview

This analysis examines how the completed C0-C5 Native Graphiti characterization work can be reused for P1-P3 (V4-V6) baseline qualification in the main experiment plan.

### Three complementary documents were created:

1. **`C0_C5_REUSE_SUMMARY.md`** (7.3K, 160 lines)  
   Executive summary with reuse matrix, effort breakdown, and critical decision points

2. **`C0_C5_TO_P1_P3_REUSE_ANALYSIS.md`** (22K, 395 lines)  
   Detailed analysis covering what C0-C5 provides, P1-P3 requirements, gaps, and reuse strategies

3. **`C0_C5_TO_P1_P3_MAPPING.md`** (22K, 480 lines)  
   Visual mapping diagrams, dependency flows, and component-by-component reuse percentages

---

## Key Findings Summary

### Direct Reuse (~60% of P1, ~40% of P2, ~30% of P3)

**Infrastructure (100% reusable)**:
- ✓ 64K serving envelope qualification
- ✓ Embedding fingerprint (`5f5a8400...`)
- ✓ Neo4j 5.26.0 deployment
- ✓ C1 instrumentation (1.317% overhead, `clean_pass`)
- ✓ Phase boundaries (`phase_map.json`)
- ✓ 4 calibration histories (188 episodes total)

**Measurement Framework (apply to new data)**:
- ✓ C2 instrumentation harness → U0/D0 execution
- ✓ C3 dependency framework → D0 phase characterization
- ✓ C5 checkpoint format → M0/M1/M2 durability

### Critical Gaps (New Work Required)

**P1 (V4) - ~2-3 days**:
- ✗ U0 execution (188 episodes, 4 histories)
- ✗ D0 execution (188 episodes, 4 histories)
- ✗ U0/D0 canonical graph F1 and Evidence Recall@10
- ✗ D0 guardrail verification

**P2 (V5) - C6-gated**:
- ✗ M0 oracle design and creation
- ✗ M1/M2 mechanism implementation
- ✗ M1/M2 concurrency tuning (4 histories × C={1,2,4,8})

**P3 (V6) - ~5-7 days**:
- ✗ 8 evaluation instances (split generation)
- ✗ 24 correctness runs (8 × M0/M1/M2)
- ✗ 48 performance runs (8 × 3 methods × 2 repeats)

---

## Why C0-C5 Evidence Cannot Directly Substitute for P1-P3

| C0-C5 Scope | P1-P3 Requirement | Why Not Reusable |
|---|---|---|
| **Native Graphiti only** | U0/D0 comparative baselines | C0-C5 measured Native alone; U0/D0 are different methods |
| **No oracle** | M0/M1/M2 correctness replay | C0-C5 had no read-only replay protocol |
| **No M1/M2** | M1/M2 mechanisms | C5 measured naive parallelism (problem), not MemBind solution |
| **1 screening history** | 8 formal evaluation instances | C5 used 1 history for bounded screening, not held-out evaluation |
| **Problem statement** | Solution qualification | C0-C5 characterized the problem; P1-P3 qualifies baselines and solutions |

---

## C0-C5 Evidence Summary

| Experiment | Result | P1-P3 Relevance |
|---|---|---|
| **C0** | Native viability (1 episode, 21.72s) | Engineering prerequisite ✓ |
| **C1** | Instrumentation overhead 1.317% median | Qualified for P1-P3 ✓ |
| **C2** | Native: 34.72s median, 99.29% LLM | Problem size; framework reusable ✓ |
| **C3** | 22.92% arrival-ready, Amdahl 1.13-1.25x | Framework reusable; D0 bounds new ✓ |
| **C4** | Sync vs Async trade-off (non-mergeable) | Diagnostic only; not P1-P3 scope |
| **C5** | C=8 makespan -51.1%, source-order violation | Problem evidence; not baseline |

---

## Recommended Execution Plan

### Phase 0: C6 Verdict (IMMEDIATE NEXT STEP)

**Complete C6 problem verdict using C0-C5 evidence**:
- PROBLEM_SUPPORTED → Proceed to P1, then P2/P3
- PARTIAL → Proceed to P1, P2/P3 scope may be reduced
- NOT_SUPPORTED → Only P1 for baseline documentation

**C6 gates all P2-P3 work; do not start P1 until C6 is complete.**

---

### Phase 1: P1 (V4) - U0/D0 Baseline Qualification (~2-3 days)

**Day 1: U0 Execution**
- Reuse: Qualified environment, C1 instrumentation, C2 harness, 4 histories
- Execute: 188 episodes, 4 fresh Neo4j namespaces
- Collect: Phase telemetry, canonical graph, work volume

**Day 2: D0 Execution**
- Reuse: Same infrastructure as U0
- Execute: 188 episodes, 4 fresh namespaces, deterministic adapters
- Collect: Phase telemetry, canonical graph, work volume

**Day 3: Guardrail Verification**
- Compare: U0 vs D0 canonical entity/edge F1 (≥0.95 required)
- Compare: Evidence Recall@10 (D0 ≥ U0 - 1pp required)
- Verify: LLM calls exact match, token ratio [0.95, 1.05]
- Analyze: Apply C3 framework to D0 traces, compute arrival-ready fraction
- Freeze: DELTA_MS (only after guardrail passes)

**Gate**: D0 passes guardrail → Proceed to P2

---

### Phase 2: P2 (V5) - M1/M2 Tuning (C6-gated, effort depends on M1/M2 design)

**Precondition**: C6 verdict = PROBLEM_SUPPORTED or PARTIAL

**Step 1: M0 Oracle (depends on design)**
- Design: Namespace schema, capture protocol
- Execute: 188 episodes, 4 histories, durable LLM+embedding capture

**Step 2: M1/M2 Mechanisms (depends on C6 verdict and complexity)**
- Implement: M1 (dependency-aware scheduling)
- Implement: M2 (late-binding replay)
- Test: TDD green for both mechanisms

**Step 3: M1 Bounded Diagnostic**
- Execute: 1 history, M0 oracle replay
- Classify: Divergence (not final semantic claim)

**Step 4: M1/M2 Tuning**
- Execute: 4 histories × C={1,2,4,8} for each mechanism
- Filter: Quality-feasible points (correctness/retrieval/completion/exactly-once)
- Select: Minimize calibration median makespan, tie-break by smallest C
- Freeze: Best-tuned M1 and M2 manifests

**Gate**: Best-tuned M1/M2 frozen → Proceed to P3

---

### Phase 3: P3 (V6) - Formal Evaluation (~5-7 days)

**Precondition**: P2 best-tuned M1/M2 frozen

**Days 1-3: Correctness Lane**
- Generate: 8 evaluation instances (exposure quarantine)
- Execute: 8 × (M0 capture + M1 replay + M2 replay) = 24 runs
- Verify: M2 8/8 parity (zero oracle miss/fallback, canonical graph parity)

**Gate**: M2 8/8 parity → Performance lane authorized

**Days 4-6: Performance Lane**
- Execute: 8 instances × 3 methods × 2 repeats = 48 runs
- Order: Blocked (question_id, repeat), balanced permutations, seed 20260806
- Verify: Work-volume guardrail (LLM exact, token [0.95, 1.05])

**Day 7: V7 Analysis**
- Compute: Effect size, 95% CI
- Analyze: Per-workload distribution, stability warnings
- Verdict: GO / INCONCLUSIVE / NO-GO

---

## Reuse Strategy by Component

### Environment (100% Direct Reuse)
```
C0-C5 Evidence                      P1-P3 Use
─────────────────────────────────   ──────────────────────────────
64K serving envelope                → No re-qualification needed
Embedding fingerprint 5f5a8400...   → Oracle namespace binding (P2)
Neo4j 5.26.0 local                  → Fresh namespaces per method
qwen3-32b-fp8, vLLM 0.26.0          → Same endpoint for U0/D0/M1/M2
4 calibration histories             → Use for U0/D0/M1/M2
```

### Instrumentation (100% Direct Reuse)
```
C0-C5 Evidence                      P1-P3 Use
─────────────────────────────────   ──────────────────────────────
C1 A/A qualification (1.317%)       → Report overhead descriptively
phase_map.json                      → Apply to U0/D0/M0/M1/M2
Phase boundaries (LLM/embed/DB)     → Same instrumentation points
```

### Measurement Framework (Apply to New Data)
```
C0-C5 Framework                     P1-P3 Application
─────────────────────────────────   ──────────────────────────────
C2 telemetry/checkpoint/JSONL       → U0/D0 execution harness
C3 dependency_map.json              → D0 phase characterization
C3 interval classifier              → D0 arrival-ready computation
C5 per-episode durability           → M0/M1/M2 checkpoint format
```

### Experimental Data (Not Reusable - New Work)
```
C0-C5 Native Data                   P1-P3 Requirement
─────────────────────────────────   ──────────────────────────────
C2: 34.72s Native median            → Fresh U0 and D0 execution
C3: 22.92% Native arrival-ready     → Compute D0-specific bounds
C5: C=8 Native -51.1% makespan      → M1/M2 tuning (not Native)
C5: Native source-order violation   → Problem evidence only
```

---

## Risk Assessment

| Risk | Probability | Impact | Mitigation |
|---|---|---|---|
| **D0 fails guardrail** | Medium | P1 blocks with external-validity limit | Report D0 as qualified but limited |
| **M1 no quality-feasible C>1** | Low-Medium | Report full frontier; Best-Tuned may be C=1 | Accept C=1 if quality-feasible |
| **M2 fails 8/8 parity** | Low-Medium | Blocks performance lane | Report correctness divergence |
| **C6 = NOT_SUPPORTED** | Unknown | P2/P3 not authorized | Only P1 for baseline documentation |

---

## Success Criteria

### P1 Success
- ✓ U0 execution complete (188 episodes, 4 histories)
- ✓ D0 execution complete (188 episodes, 4 histories)
- ✓ D0 passes guardrail (F1≥0.95, Recall@10≥U0-1pp, LLM exact, token [0.95,1.05])
- ✓ D0 phase characterization complete
- ✓ DELTA_MS frozen

### P2 Success
- ✓ M0 oracle created (188 episodes, 4 histories)
- ✓ M1 mechanism implemented and tested
- ✓ M2 mechanism implemented and tested
- ✓ Best-tuned M1 and M2 selected and frozen

### P3 Success
- ✓ M2 achieves 8/8 parity (zero oracle miss)
- ✓ 48 performance runs complete
- ✓ Work-volume guardrail satisfied
- ✓ V7 verdict issued (GO/INCONCLUSIVE/NO-GO)

---

## Conclusion

**C0-C5 provides solid infrastructure**: Environment qualification, instrumentation framework, measurement harness, and calibration data are all reusable.

**P1-P3 requires substantial new experimental work**: Because C0-C5 characterized the problem (Native only), not the solution (U0/D0 baselines and M1/M2 mechanisms).

**Reuse percentage**: ~60% of P1, ~40% of P2, ~30% of P3 can leverage C0-C5 infrastructure.

**Critical path**: C6 verdict → P1 (2-3 days) → P2 (C6-gated) → P3 (5-7 days)

**Next immediate action**: Complete C6 problem verdict before starting P1.

---

## Related Documents

- **Main Experiment Report**: `/data/predator/ly/MemBind/MemBind_CURRENT_EXPERIMENT_REPORT.md`
- **Characterization Workplan**: `/data/predator/ly/MemBind/MemBind_NATIVE_GRAPHITI_CHARACTERIZATION_WORKPLAN_v1.1.md`
- **Validation Plan**: `/data/predator/ly/MemBind/MemBind_CURRENT_VALIDATION_PLAN_v1.3.md`
- **Experiment Plan**: `/data/predator/ly/MemBind/membind-validation/EXPERIMENT_PLAN.md`
- **Global Memory**: `/data/predator/ly/MemBind/membind-validation/GLOBAL_MEMORY.md`

---

**Analysis complete: 2026-08-13**
