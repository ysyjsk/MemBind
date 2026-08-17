# Three-Baseline Sequential Runner Workplan v1.1

> Status: ACTIVE, SIMPLIFIED EXECUTION OVERLAY (2026-08-16)  
> Scope: development-only `U0 / A0 / P(C=2)` execution  
> Parent: `NATIVE_BASELINE_FIRST_LIGHTWEIGHT_WORKPLAN_v1.0.md`

## 1. Decision

The three baseline execution paths have already crossed a real live boundary:

```text
U0       nb-20260816-001 / 07741c45       49/49 completed
A0       s5-a0-20260816-004               49/49 PASS, FIFO, one worker
P(C=2)   s5-p-star-20260816-001           49/49 PASS, two active workers
```

These are stronger path-viability witnesses than another long smoke. They are
reused as the baseline-path canary evidence after their canonical artifacts are
verified. They are not merged as comparable development results because they
were produced by different run envelopes.

The only methods in this suite are:

```text
U0       Native Graphiti Serial
A0       Async-Serial, FIFO single construction worker
P(C=2)   Naive Whole-Update Parallel with two workers
```

`M*` is a proposed method, not a baseline, and is excluded. `P(C=2)` is a
development baseline setting, not a tuned or final-paper `P*` selection.

## 2. Existing live gate

Do not rerun three smoke jobs and do not add a separate integration canary.
The existing completed live attempts already establish that each execution
path reaches the real Graphiti, vLLM, embedding, and Neo4j boundary:

```text
U0       49/49 completed
A0       49/49 PASS, one worker
P(C=2)   49/49 PASS, two workers with observed overlap
```

The current `nb-20260816-001` U0 run owns the common service envelope. No new
competing method may start while that process is active. Its completed four
history artifacts are the U0 results for this run.

## 3. Development suite

One Python command, normally hosted by one parent `tmux` session, performs the
remaining work in this exact order:

```text
verify completed U0 -> A0 -> P(C=2)
```

Each method consumes exactly these exposed histories in order:

```text
07741c45  49 episodes
b6019101  49 episodes
6071bd76  46 episodes
a2f3aa27  44 episodes
```

The Python loop is intentionally small: verify/skip the four existing U0
histories, run four A0 histories, run four P(C=2) histories, then write one
summary. Every newly executed `(method, history)` remains an independent block
with a fresh namespace and an atomic checkpoint. This is the minimum needed to
avoid losing all work after a service disconnect; it is not a new authority or
protocol state machine.

The existing U0 run is referenced with `--reuse-u0-run nb-20260816-001`.
Reuse means reading its completed results; it never means reusing or modifying
its graph namespaces. A0 starts only after the U0 process has exited and all
four U0 histories verify.

## 4. Common evidence

Development blocks share `UNIFIED_OBSERVABILITY_CONTRACT_v1.0.md`:

```text
Level 0: spans, events, llm, embedding, db, graph_work, queue, quality
Level 1: per_episode_metrics
Level 2: per_history_metrics / history_result
Level 3: suite report derived offline
```

Headline names stay frozen to QA Accuracy, Evidence Recall@10, Direct
Violations, P95 Freshness, Successful Goodput, and Makespan. P99 Freshness and
Max Backlog remain predefined secondary metrics. This development suite is
descriptive and does not create a formal paper claim.

U0, A0, and P use the same model, embedding model, Graphiti version, dataset,
retrieval, Reader-v2, Judge, and observable stream schema. A0/P scheduling
reuses `s5_native_method_adapters.run_a0` and `run_p_c2`; no new scheduler is
implemented.

The runner must prove this claim from artifacts. It extracts one common
`quality_identity` from the four sealed U0 results, requires every A0/P block
to match it, and fails before the next block on drift. The post-development
user-only Reader ablation is diagnostic only and is not part of this suite;
see `QA_ACCURACY_DIAGNOSIS_AND_REMEDIATION_20260816.md`.

## 5. TDD and stop rules

Before the sequential command starts A0, tests must prove:

```text
exact method registry: U0, A0, P(C=2); M* rejected
strict method-major serial order
fresh method/history namespaces
completed block verification and skip
incomplete block fail-closed behavior
no secret material in command output or artifacts
one immutable U0 source binding per suite run ID
Reader/Judge identity equality across all three methods
```

If construction vLLM, embedding, or Neo4j becomes unavailable, persist the
current block checkpoint and stop. Do not change the model, context size,
completion cap, scheduler, workload, or namespace in place.

The single launch command is:

```bash
bash scripts/run_baseline_suite_tmux.sh \
  bs-dev-20260816-001 \
  --reuse-u0-run nb-20260816-001
```
