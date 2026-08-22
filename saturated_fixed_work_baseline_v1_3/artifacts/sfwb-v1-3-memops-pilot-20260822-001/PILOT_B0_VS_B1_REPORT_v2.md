# MemOps B0 vs B1 Pilot

Pilot: `sfwb-v1-3-memops-pilot-20260822-001`; samples: `5`; live status: B0 `LIVE_COMPLETE`, B1 `LIVE_COMPLETE`.

This is an append-only paired pilot report. It reads sealed artifacts only; it does not create a formal B1 gate result and does not authorize V5.

## Main Table

| Metric | B0 Native Serial | B1 Naive Whole-Update Async |
|---|---:|---:|
| completed / sealed | 5 | 5 |
| makespan aggregate (s) | 373.082175208 | 246.670716614 |
| makespan P50 (s) | 68.658505541 | 48.347451032 |
| makespan P95 (s) | 111.66030891579999 | 61.1390247682 |
| source throughput (tokens/s) | 38.31059468877439 | 57.9436432349862 |
| semantic goodput (samples/s) | 0.010721498548597044 | 0.016215949971310768 |
| LLM logical calls | 109 | 248 |
| LLM transport attempts | 109 | 248 |
| LLM input tokens | 200371 | 224580 |
| DB writes | 15 | 15 |
| embedding items | 489 | 898 |
| official QA all-correct | 5 | 5 |
| current-state PASS | 4 | 4 |
| publication complete | 5 | 5 |
| read-only QA PASS | 5 | 5 |
| semantic PASS | 4 | 4 |
| stale-value errors | 0 | 0 |
| feeder await count | 15 | 0 |
| active concurrency max | 1 | 3 |
| queue delay | NOT_DIRECTLY_RECORDED | NOT_DIRECTLY_RECORDED |
| backlog | NOT_DIRECTLY_RECORDED (proxies retained) | NOT_DIRECTLY_RECORDED (proxies retained) |

Service latency fields below are existing per-sample p50/p95/p99 summaries; they are not pooled call-level percentiles.

| Service summary | B0 | B1 |
|---|---:|---:|
| LLM p50 sample-summary mean (s) | 1.403004 | 1.268792 |
| LLM p95 sample-summary mean (s) | 21.638753 | 14.436633 |
| LLM p99 sample-summary mean (s) | 24.187750 | 33.450019 |
| DB p50 sample-summary mean (s) | 0.004705 | 0.007596 |
| DB p95 sample-summary mean (s) | 0.054110 | 0.143302 |
| Embedding p50 sample-summary mean (s) | 0.017920 | 0.022570 |
| Embedding p95 sample-summary mean (s) | 0.038634 | 0.072950 |

## Paired Samples

| Sample | B0 state | B1 state | B0 semantic | B1 semantic | Paired | B0 makespan s | B1 makespan s | B0 calls | B1 calls | B0/B1 canonical hash equal | Semantic diff |
|---|---|---|---:|---:|---|---:|---:|---:|---:|---|---|
| A01__Update | PASS | PASS | True | True | PP | 68.659 | 31.847 | 17 | 20 | False | +17/-9 edges; +18/-12 entities |
| A05__Update | PASS | AMBIGUOUS | True | False | PF | 121.347 | 48.218 | 33 | 56 | False | +30/-22 edges; +33/-20 entities |
| A13__Update | PASS | PASS | True | True | PP | 72.914 | 48.347 | 20 | 61 | False | +34/-17 edges; +35/-9 entities |
| A14__Update | PASS | PASS | True | True | PP | 60.215 | 55.779 | 20 | 54 | False | +24/-10 edges; +30/-10 entities |
| A28__Update | FAIL | PASS | False | True | FP | 49.948 | 62.479 | 19 | 57 | False | +36/-9 edges; +23/-7 entities |

## QA and Publication

All 10 blocks completed and were `VALIDATED_SEALED`; every QA evaluation made zero graph writes and zero construction calls. Official QA was 2/2 for every sample under both methods. Current-state inspection is stricter than Reader correctness: B0 A28 is `FAIL`, while B1 A05 is `AMBIGUOUS` and B1 A28 is `PASS`.

## Interpretation

Paired outcomes: PP=3, PF=1, FP=1, FF=0.

The pilot is operationally reproducible: both existing baseline entry points ran the same five frozen workloads and produced complete sealed artifacts. It is not a causal semantic comparison: every sample has a normalized canonical semantic diff and B1 LLM work differs from B0, while each method was run once under stochastic LLM service. The paired table therefore records outcomes, but does not attribute the differences to async scheduling alone.

Queue delay and true backlog are not present in the current v1.3 block schema. Drain tail and active-concurrency fields are retained as labeled proxies, never relabeled as queue delay/backlog.

No V5, scheduler, Graphiti, QA, Judge, or qualification predicate was changed by this report generation.
