# Why P(C=4) Was Slower In The C246 Smoke

Scope: the completed C246 aligned smoke for history `07741c45`. This is a
diagnosis of sealed artifacts, not a new live run and not a population-level
conclusion.

## Short Answer

`P(C=4)` was configured with four simultaneous whole-update workers, but the
same block still had a global LLM admission limit of `K=2`. Four stateful
Graphiti updates therefore interleaved while only two LLM requests could be
active. In this run, that interleaving did not preserve U0/P(C=2)'s per-episode
work shape: it generated more LLM requests, substantially more output tokens,
more embedding work, and a larger graph. The remaining two LLM slots became a
contention point, and a few long-running sources amplified the queue.

This is why the result should not be described as a simple C=4 implementation
being intrinsically slower. It is an observed interaction between update
concurrency, the fixed admission cap, stateful graph construction, and a
small-sample service tail.

## Evidence

### Same block contract, different update concurrency

All three blocks used the same arrival trace, model/embedding identities,
execution envelope, and `global_llm_admission_k=2`. The observed update
concurrency was 1, 2, and 4 for U0, P(C=2), and P(C=4), respectively.

| Method | Update workers | LLM admission K | Completed LLM requests | Nodes | Relationships |
|---|---:|---:|---:|---:|---:|
| U0-aligned | 1 | 2 | 531 | 290 | 438 |
| P(C=2)-aligned | 2 | 2 | 580 | 300 | 490 |
| P(C=4)-aligned | 4 | 2 | 644 | 347 | 571 |

The C=4 block consequently did 21.3% more admitted requests than U0 and
11.0% more than P(C=2), while materializing 19.7% more nodes than U0 and
15.7% more nodes plus 16.5% more relationships than P(C=2). This is already
enough to reject a pure
"same-work, different-scheduler" interpretation for this smoke.

### Model and embedding work expanded

| Method | Generation tokens | Prompt tokens | Embedding prompt tokens | Generation throughput | Prompt throughput | Prefix-cache hit rate |
|---|---:|---:|---:|---:|---:|---:|
| U0-aligned | 68,311 | 4,276,044 | 22,065 | 25.87 tok/s | 1,619.43 tok/s | 0.289 |
| P(C=2)-aligned | 71,181 | 4,446,969 | 23,799 | 34.46 tok/s | 2,153.11 tok/s | 0.332 |
| P(C=4)-aligned | 150,451 | 4,593,913 | 27,116 | 43.83 tok/s | 1,338.27 tok/s | 0.272 |

P(C=4) generated about 2.2x U0's output tokens and 2.1x P(C=2)'s output
tokens. Its embedding prompt volume was 23% above U0 and 14% above P(C=2).
The higher raw generation throughput did not compensate for the extra model
work and lower prompt throughput.

### Queue and long-tail amplification

| Method | Makespan | Median service latency | P95 service latency | Max service latency | Median queue delay | P95 freshness |
|---|---:|---:|---:|---:|---:|---:|
| U0-aligned | 2,636.846 s | 33.14 s | 95.34 s | 572.22 s | 656.59 s | 758.071 s |
| P(C=2)-aligned | 2,062.754 s | 38.94 s | 145.31 s | 908.02 s | 25.99 s | 226.156 s |
| P(C=4)-aligned | 3,430.577 s | 138.63 s | 753.47 s | 2,786.98 s | 137.02 s | 1,396.098 s |

The C=4 block's source 8 was the largest service outlier at 2,786.98 s. The
same source sequence was also the worst service outlier for U0 (572.22 s) and
P(C=2) (908.02 s), so this is partly a hard/workload-specific episode. C=4
made the tail much worse and added other long sources (38, 37, 39), which is
consistent with contention and state-dependent work amplification rather than
one isolated measurement typo.

## What We Can And Cannot Infer

Supported by the artifact:

1. C=4 had four overlapping update intervals but only two globally admitted
   LLM requests.
2. C=4 performed materially more LLM, embedding, and graph work.
3. C=4 had higher median and tail service latency, larger queue/freshness,
   and a 30.1% longer makespan than U0 in this 49-episode block.
4. The C=4 block also recorded one source-publication-order violation, so its
   speed result is not a clean correctness-qualified throughput point.

Not supported:

- that concurrency 4 is universally slower;
- that the slowdown is caused by one specific Graphiti internal operation;
- that C=4 would remain slower with a matched admission limit, matched graph
  trajectory, or repeated histories;
- a formal speedup or scaling curve from this single C246 history.

## Practical Interpretation

The immediate bottleneck is the mismatch between update concurrency and LLM
admission: `4` whole updates competing for `2` LLM slots. The next bottleneck
is stateful graph-work amplification under interleaving, visible in request
counts, token volume, embedding volume, and graph size. The final contributor
is the long-tail episode that becomes much more expensive under that
contention.

Before making a C=4 scaling claim, the plan should run repeated, matched-work
blocks with the same admission policy and record per-stage counters (LLM
calls/tokens, embedding calls/tokens, DB operations, retries, and graph node /
edge deltas). C=4 should then be judged on both makespan and freshness, with
the source-publication-order violation reported separately.
