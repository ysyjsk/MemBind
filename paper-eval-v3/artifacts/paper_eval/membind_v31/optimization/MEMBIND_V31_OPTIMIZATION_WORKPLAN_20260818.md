# MemBind v3.1 Optimization Lane Workplan

Status: `STOPPED_AFTER_W4_NO_READY_WORK`  
Created: 2026-08-18  
Scope: scheduler/admission characterization and one bounded W=4 pilot

Terminal execution record: `MEMBIND_V31_W4_PILOT_RESULT_20260818.md`.
The sole authorized pilot completed successfully but observed no legal-ready
Compile backlog excluded by W=2; no further live W expansion is authorized by
this lane.

This document is a separate execution lane. It does not amend
`MemBind_FINAL_METHODOLOGY_v3.1_FROZEN.md`, the formal v3.1 workplan,
`V31_METHOD_PLAN.json`, any old attempt, the formal reducer, or the paper main
table.

## Research question

The incomplete v3.1 trace shows that a ready-pool or admission bottleneck is
not identifiable from transport records alone. The first question is therefore
observability, not a new scheduling mechanism:

```text
Does W=2 leave legal Compile work outside the lookahead window?
Does the coordinator expose legal ready work while compile capacity is idle?
Does the LLM admission gate have actual waiters when K is under-filled?
```

`Snapshot Resolve`, OCC/MVCC, read-set validation, selective repair, prompt
changes, JSON repair, and token-cap changes are out of scope.

## Frozen pilot identity

The only live candidate authorized by this lane is:

```text
pilot_run_id:       membind-v31-opt-w4-20260818-001
attempt_id:         membind-v31-opt-w4-20260818-001-attempt-001
history:            07741c45
source_sequences:   0..11
compile_workers:    2
lookahead:          4
bind_workers:       1
global K:           2
policy:             FRONTIER_FIRST_CACHE_AFFINITY
prefix unit:        16
DCP:                1
artifact status:    DIAGNOSTIC_ONLY_NON_MERGEABLE
```

The contract derives a fresh namespace and cache salt from the parent formal
plan hash. Shared source lineage, arrival offsets, model, embedding, Graphiti,
Neo4j, prompt, schema, and execution envelope may be reused as inputs only;
formal cache salts, namespaces, prepared artifacts, traces, checkpoints, and
results may not be reused.

## TDD gates

1. RED: reject W/worker/K drift, parent-plan/source/arrival drift, reused
   namespace/cache salt, existing output root, unsafe telemetry, and malformed
   sealed artifacts.
2. GREEN: verify coordinator snapshots distinguish legal-ready, reserved,
   active, Prepared ROB, and outside-window work; verify `BIND_DISPATCHED` is
   not reported as `READY_TO_BIND`.
3. GREEN: verify admission snapshots distinguish active/waiting Compile and
   Frontier requests and preserve monotonic content-safe sequence numbers.
4. GREEN: verify arrival-task validation failures wake the coordinator and
   produce a terminal non-mergeable checkpoint rather than deadlocking.
5. GREEN: run the independent pilot executor fixture with 12 sources, durable
   lifecycle/queue/prepared artifacts, and failure checkpointing.
6. GREEN: run the related and full offline suites before any service call.

Required commands:

```bash
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=paper-eval-v3/src \
  paper-eval-v3/.venv/bin/pytest -q \
  paper-eval-v3/tests/test_membind_v31_*.py
```

## Telemetry contract

Scheduler and admission snapshots are routed to the pilot-only `queue.jsonl`;
they never enter lifecycle `events.jsonl`. `llm.jsonl` remains request-level
transport telemetry. The offline analyzer writes `QUEUE_DIAGNOSTIC.json` and
keeps these distinctions:

```text
legal_ready > 0 and active_compile < workers
    -> work-conservation candidate

legal_ready == 0 and active_compile < workers and outside_window > 0
    -> W-limited candidate

active < K and waiting > 0
    -> admission/policy candidate

active < K and waiting == 0
    -> request-generation/local-uninstrumented candidate
```

No category is promoted to a scientific claim without the corresponding
snapshot evidence.

## Live boundary and stop rules

After all offline gates are green, perform exactly one read-only namespace probe
through the qualified hooks. The pilot is started only in a fresh `tmux` session
with the dedicated CLI:

```bash
tmux new -s membind-v31-opt-w4-20260818-001
cd /data/predator/ly/MemBind
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=paper-eval-v3/src \
  paper-eval-v3/.venv/bin/python \
  paper-eval-v3/scripts/run_membind_v31_w4_pilot.py \
  --run-id membind-v31-opt-w4-20260818-001
```

The command has no resume mode. Any partial root is permanently non-reusable;
the next attempt needs a new run id. Stop immediately and persist `FAILURE.json`
plus the latest checkpoint on namespace non-freshness/escape, source or arrival
drift, malformed/truncated provider output, HTTP/transport failure, OOM/KV/RoPE
error, missing completion evidence, publication/predecessor mismatch, direct
violation, observed K>2, prepared-artifact mismatch, or checkpoint failure.

Even a complete pilot is diagnostic-only and cannot be merged into the formal
main table. The next decision is made offline from `QUEUE_DIAGNOSTIC.json`,
`result.json`, and the sealed artifacts:

```text
no legal ready work beyond W=2 -> stop W=4 lane;
ready work but no performance gain -> inspect service/DB/Bind bottleneck;
correctness-safe diagnostic gain -> propose a separate, explicitly authorized
                                  follow-up lane.
```
