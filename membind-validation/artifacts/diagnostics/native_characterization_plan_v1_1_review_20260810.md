# Native Characterization Workplan v1.1 Review

<!-- Maintainability: this report explains why the current pointer moved from
v1.0 to v1.1. It is evidence for the plan revision, not an experiment result. -->

## Scope and status

This is an offline review of the supplied problem-first characterization
proposal and the current repository plan. No model, embedding endpoint, Neo4j
database, or remote host was contacted while producing this report.

Authoritative plan after this review:

```text
MemBind_NATIVE_GRAPHITI_CHARACTERIZATION_WORKPLAN_v1.1.md
protocol_id=native-characterization-v1.1
instrumentation_contract_status=specified_not_yet_qualified
```

`MemBind_NATIVE_GRAPHITI_CHARACTERIZATION_WORKPLAN_v1.0.md` remains immutable
history. The four current pointers name v1.1 first and retain the v1.0 link as
historical provenance.

## Overall judgment

The supplied proposal is directionally correct and fixes a real research-order
problem: Native Graphiti must be characterized before a MemBind mechanism is
treated as the answer. It is not sufficient, however, to copy every suggested
threshold or workload convention into a protocol. The revised plan therefore
adopts the scope reductions and weakens claims where the cited literature only
supports measurement practice, not a universal numerical rule.

## Decisions

| Proposal point | Decision | Reason and v1.1 consequence |
|---|---|---|
| C0 should be one bounded Native episode | Adopt | Matches artifact-evaluation kick-the-tires practice. C0 is viability only and MUST NOT grow into H0. |
| Freeze instrumentation scope | Adopt | C1 now has a frozen minimum schema; only a C0-C5 measurement-correctness defect can add telemetry. |
| Treat <=2% as an absolute tracing law | Correct and soften | DistServe's sub-2% number is simulator SLO-attainment error, not tracing overhead. v1.1 uses <=2% ideal, 2-5% conditional, >5% default hard fail, with semantic parity, stability, full reporting, and no G1/G2 interpretation reversal. |
| Keep a Native occupancy/work-volume breakdown | Adopt | Critical-path and interval-union accounting answers the first characterization question without introducing a treatment. |
| Use D0/D1/D2/D3/unknown and p_L/p_U | Adopt | Static source evidence plus dynamic trace and input-ready-at-arrival form a conservative ledger. Unknown remains first-class; an unobserved read is not proof of independence. |
| Add a counterfactual dependency microexperiment | Reject for screening | It expands implementation before the basic signal is known. v1.1 records unknown rather than manufacturing certainty. |
| Make S_8(p_U)<1.2 or S_8(p_L)>=1.5 hard gates | Reject | Amdahl-style `S_C(p)` omits remote capacity, DB contention, batching, instrumentation, and commit ordering. v1.1 reports S_2/S_4/S_8 descriptively. |
| Run both normalized loads and 20/10/5/2 seconds | Reject duplication | One frozen normalized sweep is sufficient for bounded screening; actual seconds are derived after `S_ref` is frozen. |
| Use strict rho/utilization language | Correct | Evolving Graphiti state and finite replay do not establish steady state. v1.1 calls it `rho_proxy` and freezes `S_ref` before E3 outcomes. |
| Add Poisson arrival now | Reject for this screening | Literature uses it when a timestamp-free workload model is needed. The current deterministic open-loop replay is a controlled screening trace and must not be called a real workload distribution. |
| Rename stale metric | Adopt | `post_return_stale_window = max(0, publish_timestamp - caller_return_timestamp)` reflects that Async returns before publish. |
| Interpret one no-failure parallel pass as sufficiency | Reject | A single history/interleaving cannot prove universal safety. v1.1 allows only `NO_NAIVE_PARALLEL_INSUFFICIENCY_OBSERVED`. |
| Stop after the characterization verdict | Adopt | C6 records verdict, supporting observations, and unresolved evidence; it cannot select or authorize M2. |

## Codebase evidence that constrains the plan

The pinned Graphiti audit found that `add_episode()` retrieves previous episode
context before node/edge extraction, then performs candidate search, resolution,
invalidation, attribute/summary work, embeddings, and bulk publication. Node and
edge extraction therefore cannot be declared independent of all state merely
because they do not directly query the latest entity/edge candidate set. The
v1.1 taxonomy distinguishes immutable history-prefix dependence from latest
materialized graph dependence.

The current driver wrapper counts an outer transaction write but not every
`tx.run()` statement inside the transaction. That is a measurement-contract gap,
not a reason to claim a DB write count. C1/C2 must either instrument the actual
transaction boundary or label the count as an outer transaction count.

The existing project runner also includes deterministic candidate ordering and a
process-local embedding cache. v1.1 requires separate `U0` and `U0-S` labels and
symmetric cache lifecycle so those stabilizers cannot silently become Native
Graphiti evidence.

## Literature basis

The following public sources were checked for the methodological claims used in
the revision:

1. DistServe, OSDI 2024, https://www.usenix.org/system/files/osdi24-zhong-yinmin.pdf
   (arXiv:2401.09670). It separates execution time from queueing delay, varies
   request rate, and reports simulator versus hardware SLO-attainment error under
   2%. It does not define a tracing-overhead standard.
2. PagedAttention/vLLM, SOSP 2023, https://arxiv.org/pdf/2309.06180. Its open-loop
   rate experiments show queue and latency growth after capacity is exceeded.
   This supports a capacity-normalized screening sweep, not a requirement to
   add a second arbitrary time grid.
3. Pivot Tracing, SOSP 2015,
   https://cs.brown.edu/people/jcmace/papers/mace15pivot.pdf. Reported overhead
   varies by operation, including values above 2%; this supports measured,
   transparent perturbation checks.
4. The Mystery Machine, OSDI 2014,
   https://www.usenix.org/conference/osdi14/technical-sessions/presentation/chow.
   Its causal/critical-path treatment motivates interval union and happens-before
   aware attribution rather than summing nested spans.
5. SAMC, OSDI 2014,
   https://www.usenix.org/conference/osdi14/technical-sessions/presentation/leesatapornwongsa.
   Its concurrency exploration results support treating one observed violation as
   an existence counterexample and one clean pass as bounded absence of evidence.
6. OSDI 2024 artifact guidance,
   https://www.usenix.org/conference/osdi24/call-for-artifacts. Its small
   getting-started check supports a one-episode C0 before full evaluation.

These papers justify the measurement discipline and interpretation limits. None
of them proves that MemBind is necessary, that a particular `p` fraction is
available, or that a particular speedup threshold is scientifically meaningful.

## TDD evidence

The new document contract was added before the v1.1 workplan (RED first):

```text
tests/test_native_characterization_workplan_v1_1.py
RED: 11 failures (target plan absent), 2026-08-10
GREEN: 11 tests OK, together with v1.0 contract tests: 23 tests OK
```

The failed `pytest` invocations were environment preflights only (`pytest` is
not installed in the project venv); the authoritative test command is the
repository's `unittest` entry point. No live experiment was run.

Final offline verification:

```text
focused: 24/24 OK
full discovery: 596 tests; 588 passed; 7 failed; 1 error
git diff --check: PASS
```

All seven failures and the error are pre-existing R5/R6 state-drift debt: legacy
tests expect replacement-003 and `run_q1_h0-b-post-workload-replacement`, while
the unmodified machine state records replacement-004 and
`run_q1_h0-b-r6-replacement-004`. The R6 builder error is the same mismatch from
the opposite direction. This review did not rewrite state or historical tests.

Evidence hashes after the final run:

```text
workplan_sha256=d4d0435957c5577cf555c296f69763e795a7b5f79ed0f747c6213ccf26eb4433
test_sha256=141372e29918e989971c5084b20f6b7692f9c94bf870fbc83557f3a9f227236f
red_log_sha256=78a1947f782a3f4abca779270d0cfa60f73d9bfcedb28ae949d4c7352e4d9644
focused_green_log_sha256=f4d6812f12d50ef37a4e303ec8b992038e1944b1414fe6508f38f81bdddd92ea
full_regression_log_sha256=181df06a0538732b3f12b658d64361348ddd96144c594508ae39b24587f6a6b1
unchanged_current_state_sha256=fb57c0edb6388c2ae94c6ba338e1671c39fa08e218cfc96566ee4d315b2e231d
```

## Remaining evidence gaps

- The instrumentation implementation is still unqualified; the status is
  explicitly `specified_not_yet_qualified`.
- No C0-C5 scientific result exists yet under v1.1.
- The current full offline suite retains the previously known R5/R6 state-drift
  debt; it must be reported separately from this plan contract and must not be
  repaired by rewriting historical `CURRENT_STATE.json`.
- Any future vLLM outage is infrastructure evidence and must stop the current
  authorized stage, not be interpreted as a research result.
