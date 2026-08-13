# C0-C5 → P1-P3 Reuse Mapping Diagram

> Visual mapping of completed C0-C5 characterization evidence to P1-P3 requirements
> Date: 2026-08-13

---

## Mapping Overview

```
C0-C5 (Native Characterization)          P1-P3 (Baseline Qualification)
════════════════════════════════        ════════════════════════════════

C0: One-episode viability               → [Reuse] Engineering prerequisite
    ✓ 21.72s Native completion            Status: ✓ PASS (informational)

C1: Instrumentation qualification       → [Reuse] P1/P2/P3 instrumentation
    ✓ 1.317% median overhead              Status: ✓ Qualified, report descriptively
    ✓ phase_map.json                      Use: Apply to U0/D0/M0/M1/M2

C2: Native construction breakdown       → [Framework] P1 U0/D0 measurement
    ✓ 188 episodes, 4 histories           Reuse: Harness, telemetry, checkpoint
    ✓ Phase occupancy, work volume        New: Fresh U0 and D0 execution needed
    ✗ Native-only data (not U0/D0)        
                                        → [Framework] P2 M0/M1/M2 measurement
                                          Reuse: Checkpoint format, JSONL streams
                                          New: Oracle capture, M1/M2 mechanisms

C3: Dependency characterization         → [Framework] P1 D0 phase analysis
    ✓ dependency_map.json                 Reuse: Static rules, interval classifier
    ✓ Arrival-ready framework             New: Compute D0-specific bounds
    ✗ 22.92% Native-specific              Note: D0 bounds will differ from Native

C4: Sync vs Async (non-mergeable)       → [Not applicable] P1-P3 scope
    ✗ Diagnostic only                     U0/D0 are both synchronous serial
    ✗ Non-mergeable verification error    P1 is correctness/parity, not scheduling

C5: Serial vs Parallel                  → [Motivation] Problem statement
    ✓ C=8 makespan -51.1%                 Reuse: Checkpoint format for P3
    ✗ Source-order violation at C≥2       New: U0/D0 serial baselines
    ✗ Native-only, not M1/M2              New: M1/M2 mechanisms and evaluation
                                        → [Not applicable] P1 U0/D0 baselines
                                          U0/D0 are serial; C5 is concurrency evidence

Environment (all C0-C5)                 → [Reuse] P1/P2/P3 host stack
    ✓ 64K serving envelope                Status: ✓ Qualified, no re-check needed
    ✓ Embedding fingerprint 5f5a8400...   Bind: Oracle namespace in P2
    ✓ Neo4j 5.26.0 local deployment       Use: Fresh namespaces for U0/D0/M0/M1/M2
    ✓ qwen3-32b-fp8, vLLM 0.26.0
    ✓ 4 calibration histories             Use: Same for U0/D0/M1/M2
```

---

## P1 (V4): U0/D0 Baseline Qualification

```
┌─────────────────────────────────────────────────────────────────────┐
│ P1 (V4): U0/D0 Representativeness Guardrail                        │
├─────────────────────────────────────────────────────────────────────┤
│                                                                     │
│ C0-C5 REUSE:                                                        │
│   ✓ Qualified host stack (C0-C5 environment)                       │
│   ✓ C1 instrumentation (overhead qualified)                        │
│   ✓ C2 measurement harness (phase boundaries, telemetry)           │
│   ✓ C3 dependency framework (static rules, interval classifier)    │
│   ✓ 4 calibration histories (same as C2)                           │
│                                                                     │
│ NEW WORK:                                                           │
│   ✗ U0 execution: 188 episodes, 4 histories                        │
│        ├─ Fresh Neo4j namespace per history                        │
│        ├─ Apply C2 instrumentation                                 │
│        └─ Canonical graph extraction                               │
│                                                                     │
│   ✗ D0 execution: 188 episodes, 4 histories                        │
│        ├─ Deterministic candidate-ordering adapters                │
│        ├─ Fresh Neo4j namespace per history                        │
│        └─ Canonical graph extraction                               │
│                                                                     │
│   ✗ U0 vs D0 comparison:                                           │
│        ├─ Canonical entity/edge F1 (≥0.95 required)                │
│        ├─ Evidence Recall@10 (D0 ≥ U0 - 1pp required)              │
│        ├─ LLM call count (exact match required)                    │
│        └─ Total token ratio ([0.95, 1.05] required)                │
│                                                                     │
│   ✗ D0 phase characterization:                                     │
│        ├─ Apply C3 framework to D0 traces                          │
│        ├─ Compute D0 arrival-ready fraction                        │
│        └─ Freeze DELTA_MS (only after guardrail passes)            │
│                                                                     │
│ GATE: D0 passes guardrail → Proceed to P2                          │
│       D0 fails guardrail → Report with external-validity limit     │
└─────────────────────────────────────────────────────────────────────┘
```

---

## P2 (V5): M1/M2 Concurrency Tuning

```
┌─────────────────────────────────────────────────────────────────────┐
│ P2 (V5): Quality-Feasible M1/M2 Concurrency Tuning                 │
├─────────────────────────────────────────────────────────────────────┤
│                                                                     │
│ PRECONDITION: C6 verdict = PROBLEM_SUPPORTED or PARTIAL            │
│                                                                     │
│ C0-C5 REUSE:                                                        │
│   ✓ Qualified host stack and instrumentation (same as P1)         │
│   ✓ 4 calibration histories                                        │
│   ✓ C5 checkpoint format (per-episode durability, block structure) │
│                                                                     │
│ NEW WORK:                                                           │
│   ✗ M0 oracle design:                                              │
│        ├─ Namespace schema (qualified host manifest binding)       │
│        ├─ LLM response capture protocol                            │
│        ├─ Embedding vector capture protocol                        │
│        └─ Prompt alignment rules                                   │
│                                                                     │
│   ✗ M0 oracle creation:                                            │
│        ├─ 188 episodes, 4 histories                                │
│        ├─ Durable oracle writes (LLM + embedding)                  │
│        └─ Content-addressed namespace                              │
│                                                                     │
│   ✗ M1 mechanism implementation:                                   │
│        ├─ TDD: dependency-aware scheduling                         │
│        ├─ Deterministic adapters (same as D0)                      │
│        └─ Live construction with D1 parallelism                    │
│                                                                     │
│   ✗ M2 mechanism implementation:                                   │
│        ├─ TDD: late-binding replay protocol                        │
│        ├─ Oracle replay (zero live LLM/embedding)                  │
│        └─ Deterministic adapters (same as D0)                      │
│                                                                     │
│   ✗ M1 bounded diagnostic:                                         │
│        ├─ 1 history, M0 oracle replay                              │
│        ├─ Divergence classification (not final semantic claim)     │
│        └─ Diagnostic evidence only                                 │
│                                                                     │
│   ✗ M1/M2 tuning: 4 histories × C={1,2,4,8}                        │
│        ├─ Quality-feasible filtering (correctness/retrieval/       │
│        │   completion/exactly-once guardrails)                     │
│        ├─ Selection objective: min calibration median makespan     │
│        ├─ Tie-break: smallest C                                    │
│        └─ Freeze best-tuned M1 and M2 manifests                    │
│                                                                     │
│ GATE: Best-tuned M1/M2 frozen → Proceed to P3                      │
└─────────────────────────────────────────────────────────────────────┘
```

---

## P3 (V6): Formal Evaluation

```
┌─────────────────────────────────────────────────────────────────────┐
│ P3 (V6): Formal Correctness and Performance Evaluation             │
├─────────────────────────────────────────────────────────────────────┤
│                                                                     │
│ PRECONDITION: P2 best-tuned M1/M2 frozen                           │
│                                                                     │
│ C0-C5 REUSE:                                                        │
│   ✓ Qualified host stack and instrumentation (same as P1/P2)      │
│   ✓ C5 checkpoint format (per-episode durability)                  │
│   ✓ P2 M0 oracle (created in P2)                                   │
│                                                                     │
│ NEW WORK:                                                           │
│   ✗ 8 evaluation instances:                                        │
│        ├─ Split generation from frozen_split_v1_3.json             │
│        ├─ Exposure quarantine (c6853660 excluded)                  │
│        └─ Content-addressed instance IDs                           │
│                                                                     │
│   ✗ Correctness lane: 24 runs                                      │
│        ├─ 8 instances × (M0 capture + M1 replay + M2 replay)       │
│        ├─ Fresh Neo4j namespace per run                            │
│        ├─ M2 8/8 parity gate (zero oracle miss/fallback)           │
│        └─ Canonical graph parity, retrieval guardrail              │
│                                                                     │
│   ✗ Performance lane: 48 runs (only after M2 8/8 parity)           │
│        ├─ 8 instances × 3 methods (D0/M1/M2) × 2 repeats           │
│        ├─ Blocked (question_id, repeat) order, seed 20260806       │
│        ├─ Balanced permutations (6 orders differ by ≤1)            │
│        ├─ Live LLM/embedding (no response replay)                  │
│        └─ Work-volume guardrail: LLM exact, token [0.95, 1.05]     │
│                                                                     │
│   ✗ V7 analysis:                                                   │
│        ├─ Effect size, 95% CI                                      │
│        ├─ Per-workload distribution                                │
│        ├─ Stability warning (repeat gap >10%)                      │
│        └─ GO / INCONCLUSIVE / NO-GO verdict                        │
│                                                                     │
│ NOTE: C5's 196 episodes (4 blocks × 49) are calibration screening, │
│       not formal evaluation. P3 uses 8 held-out evaluation IDs.    │
└─────────────────────────────────────────────────────────────────────┘
```

---

## Reuse Percentage by Stage

```
┌────────┬──────────────────────────────────────┬─────────────┬───────────┐
│ Stage  │ Component                            │ Reuse %     │ Effort    │
├────────┼──────────────────────────────────────┼─────────────┼───────────┤
│ P1 (V4)│ Environment qualification            │ 100% reuse  │ 0 days    │
│        │ C1 instrumentation                   │ 100% reuse  │ 0 days    │
│        │ C2 measurement harness               │ 100% reuse  │ 0 days    │
│        │ C3 dependency framework              │ 100% reuse  │ 0 days    │
│        │ U0 execution                         │ 0% (new)    │ 1 day     │
│        │ D0 execution                         │ 0% (new)    │ 1 day     │
│        │ Canonical graph F1, Recall@10        │ 0% (new)    │ 0.5 day   │
│        │ Guardrail verification               │ 0% (new)    │ 0.5 day   │
│        ├──────────────────────────────────────┼─────────────┼───────────┤
│        │ TOTAL P1                             │ ~60% reuse  │ 2-3 days  │
├────────┼──────────────────────────────────────┼─────────────┼───────────┤
│ P2 (V5)│ Environment + instrumentation        │ 100% reuse  │ 0 days    │
│        │ Calibration histories                │ 100% reuse  │ 0 days    │
│        │ C5 checkpoint format                 │ 100% reuse  │ 0 days    │
│        │ M0 oracle design + creation          │ 0% (new)    │ Variable  │
│        │ M1 mechanism implementation          │ 0% (new)    │ Variable  │
│        │ M2 mechanism implementation          │ 0% (new)    │ Variable  │
│        │ M1 bounded diagnostic                │ 0% (new)    │ Variable  │
│        │ M1/M2 tuning (4 histories × 4 C)     │ 0% (new)    │ Variable  │
│        ├──────────────────────────────────────┼─────────────┼───────────┤
│        │ TOTAL P2                             │ ~40% reuse  │ C6-gated  │
├────────┼──────────────────────────────────────┼─────────────┼───────────┤
│ P3 (V6)│ Environment + instrumentation        │ 100% reuse  │ 0 days    │
│        │ M0 oracle (from P2)                  │ 100% reuse  │ 0 days    │
│        │ C5 checkpoint format                 │ 100% reuse  │ 0 days    │
│        │ 8 evaluation instance split          │ 0% (new)    │ 0.5 day   │
│        │ 24 correctness runs                  │ 0% (new)    │ 2-3 days  │
│        │ 48 performance runs                  │ 0% (new)    │ 3-4 days  │
│        │ V7 analysis                          │ 0% (new)    │ 1 day     │
│        ├──────────────────────────────────────┼─────────────┼───────────┤
│        │ TOTAL P3                             │ ~30% reuse  │ 5-7 days  │
└────────┴──────────────────────────────────────┴─────────────┴───────────┘
```

---

## Critical Path Dependencies

```
C0-C5 Completed ────┐
                    │
                    ├─── C6 Verdict ────┐
                    │                   │
                    │                   ├─ PROBLEM_SUPPORTED ────┐
                    │                   │                        │
                    │                   ├─ PARTIAL ──────────────┤
                    │                   │                        │
                    │                   └─ NOT_SUPPORTED ────┐   │
                    │                                        │   │
                    └────────────────────────────────────────┼───┤
                                                             │   │
                                                             │   v
                                                             │  P1 (V4)
                                                             │  U0/D0 Baseline
                                                             │   │
                                                             │   v
                                                             │  D0 Guardrail
                                                             │   │
                                                             │   ├─ PASS ────┐
                                                             │   │           │
                                                             │   └─ FAIL ────┤
                                                             │               │
                                                             v               v
                                                        P1 Only         P2 (V5)
                                                        Document        M0 Oracle
                                                        Baseline        M1/M2 Mechanisms
                                                                        Concurrency Tuning
                                                                             │
                                                                             v
                                                                        P3 (V6)
                                                                        24 Correctness
                                                                        48 Performance
                                                                             │
                                                                             v
                                                                        V7 Verdict
                                                                        GO/INCONCLUSIVE/NO-GO
```

---

## Summary: What C0-C5 Provides vs What P1-P3 Needs

### Infrastructure Layer (Direct Reuse)
```
C0-C5 ✓ → P1-P3 ✓
    Environment qualification (64K, embedding, Neo4j)
    C1 instrumentation (overhead qualified)
    Phase boundaries and telemetry schema
    Checkpoint format and JSONL streams
    4 calibration histories
```

### Measurement Framework (Adapt and Apply)
```
C0-C5 Framework → P1-P3 New Data
    C2 harness      → U0/D0 execution
    C3 dependency   → D0 phase characterization
    C5 checkpoint   → M0/M1/M2 durability
```

### Experimental Scope (No Direct Reuse)
```
C0-C5 Native → P1-P3 U0/D0/M1/M2
    Native-only evidence cannot substitute for U0/D0 baselines
    No oracle, no M1/M2 mechanisms
    1 screening history ≠ 8 formal evaluation instances
    Problem characterization ≠ solution qualification
```

---

**Conclusion**: C0-C5 delivers robust measurement infrastructure (~60% of P1, ~40% of P2, ~30% of P3), but P1-P3 experimental execution requires fresh baseline runs (U0/D0), oracle creation (M0), mechanism implementation (M1/M2), and formal evaluation (8 instances × 3 methods × correctness+performance).
