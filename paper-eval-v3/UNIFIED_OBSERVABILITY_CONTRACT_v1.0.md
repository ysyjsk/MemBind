# Unified Observability Contract v1.0

Status: FROZEN FOR NATIVE-FIRST SCREENING (2026-08-16)

This contract defines the evidence shape shared by the Native U0 baseline and
any later A0, P*, or M* method. It is an observability contract, not a new
workload, correctness protocol, or statistical-analysis plan. The existing
C1/C2 artifacts remain immutable historical evidence; their passive span
implementation may be reused, but their numeric results are not silently
merged into the Native baseline.

## 1. Metric tiers

The paper headline table is frozen to six metrics:

```text
QA Accuracy
Evidence Recall@10
Direct Violations
P95 Arrival-to-Publish Freshness
Successful Goodput
Makespan
```

Two secondary systems metrics are preregistered:

```text
P99 Arrival-to-Publish Freshness
Max Backlog
```

All phase, token, retry, embedding, database, graph-work, queue, resource,
and tail-distribution fields are diagnostic/explanatory metrics. They may
explain a headline result, but cannot be promoted to a primary claim after
looking at results without an explicit protocol amendment.

The metric names above are the common paper schema. Values produced by this
four-history `DEVELOPMENT_EXPOSED` Native screen remain descriptive baseline
diagnostics; calling a field a headline metric does not turn N2 into a formal
paper claim or an independent-sample significance test.

The fixed definitions are:

```text
freshness                 = publication_ts - arrival_ts
P95/P99 freshness         = nearest-rank quantile over successful episodes
makespan                  = max(terminal_ts) - min(arrival_ts), per history
successful goodput        = published * 1e9 / makespan_ns, episodes/second
direct violations         = exact observed invariant-violation count
max backlog               = max observed queue depth, only when a real queue exists
tail amplification        = P99 / P50 when P50 > 0 (diagnostic only)
```

Failed/censored episodes are reported by exact terminal accounting and are
never imputed as successful latency samples. Paper-level aggregation must
retain the history as the experimental unit; pooled episode distributions are
descriptive and must not be presented as independent-history significance.

For the strict serial U0 Native baseline, `max_backlog` is recorded as
`NOT_APPLICABLE_SERIAL_BASELINE` unless a real queue is present. A value of
zero in a serial run is not evidence of online capacity or SLO compliance.
Queue-area, concurrency, and drain-time metrics become meaningful in A0/P*/M*
and use the same schema.

## 2. Evidence levels

Level 0 is the source of truth. Higher levels are deterministic projections and
must never require another model, Reader, Judge, or database call:

```text
Level 0  raw sanitized streams
         spans.jsonl, events.jsonl, llm.jsonl, embedding.jsonl,
         db.jsonl, graph_work.jsonl, queue.jsonl, quality.jsonl,
         resource.jsonl (optional, non-blocking)
             |
Level 1  per_episode_metrics.jsonl
             |
Level 2  per_history_metrics.json
             |
Level 3  paper_metrics.json
```

Every durable row carries the common identity:

```text
run_id, history_id, question_id, episode_id,
source_sequence, method, repeat_id
```

`source_sequence` is zero-based and contiguous within a history. For the
LongMemEval history workload, `question_id` equals `history_id`; this is an
explicit field rather than an inferred substring of `episode_id`.

## 3. Lifecycle timestamps

Completed episodes use monotonic timestamps with this ordering:

```text
arrival_ts <= enqueue_ts <= service_start_ts <= publication_ts <= terminal_ts
```

The runner records all available timestamps; the reducer derives:

```text
queue_delay       = service_start - arrival
service_latency   = publication - service_start
freshness_latency = publication - arrival
terminal_latency  = terminal - arrival
```

Publication means the externally observable memory publication boundary. It is
not interchangeable with an internal Graphiti phase that happens to be named
"publication". Failed or interrupted attempts retain their durable terminal
classification and are non-mergeable; they are never silently treated as a
zero-latency success.

## 4. Span and work semantics

Each real phase span records `start_ns`, `end_ns`, `phase`, `operation_class`,
`status`, and `parent_span_id`. Nested phase durations must not be summed as a
wall-clock total. Reducers report interval union and, where available,
critical-path contribution and overlap ratio.

LLM operation records expose only content-safe metadata:

```text
logical_call_id, transport_attempt_id, phase/prompt_name,
start/end, input_tokens, output_tokens, finish_reason (optional),
structured_output_valid (optional), retry_count,
transport_attempt_count, status/error_class (optional)
```

Embedding records contain operation timing, item/text count, dimension,
retry/transport counts when available, and status. Database records distinguish
read/query, write, and transaction operations and include timing and status.
Raw prompts, questions, answers, references, Cypher, request bodies, and raw
responses never enter a public evidence stream.

Graph-work records should include graph-prefix node/relationship counts before
and after the episode when this can be obtained without changing Graphiti's
execution path. Candidate and invalidation counts are diagnostic. Detailed
semantic counts (`new_nodes`, `merged_nodes`, `new_edges`, and so on) are
optional until the pinned Graphiti boundary exposes them without invasive
instrumentation; missing fields are `NOT_CAPTURED`, not fabricated zeros.

## 5. Queue and resource streams

Queue events, when applicable, carry timestamp, queue depth, active work,
published frontier, and observed concurrency. From them the offline reducer
derives mean/P95/max backlog, queue-area, drain time, and overlap ratio.

U0 has no offered-load sweep and no concurrent workers. It may emit a single
serial-baseline marker, but must not manufacture a queue time series. Resource
sampling (vLLM, CPU/RSS, Neo4j) is optional and low frequency; it must never
block or alter the Native critical path. Missing resource samples are reported
as unavailable diagnostics.

## 6. Quality and retrieval evidence

Quality and retrieval are paired with each history and are computed from the
frozen Reader/Judge/retrieval path. Public artifacts store hashes, lengths,
parsed labels/ranks, status, latency, token counts, and error classes. Private
access-controlled local evidence may retain raw question/answer/reference,
ranked results, gold mapping, or judge output for audit, but it is not part of
mergeable public evidence. No diagnostic metric may trigger a new Judge call.

## 7. U0 acceptance boundary

Before Native U0 starts, the focused reducer/schema tests and the related
offline regression must be green. U0 must produce complete terminal accounting,
common identity, lifecycle timestamps, sanitized operation/work streams, and
per-episode checkpoints. Instrumentation is accepted when semantic parity is
preserved; an overhead warning in the 2--5% range is reported rather than used
to grow an optimization loop, while overhead above 5% blocks the run.

The first Native screen is descriptive. It does not claim statistical
significance, exact numeric reproduction of C2, or MemBind benefit. A healthy
baseline unlocks a separate later plan; an incomplete or unexplained baseline
stops method expansion.
