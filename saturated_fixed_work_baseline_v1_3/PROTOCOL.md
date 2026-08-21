# MemBind Saturated Fixed-Work Construction Baseline v1.3

Status: prospective development protocol. The v1.2 implementation remains the
reusable dataset, runner, instrumentation, canonical projection, QA, and
sealing dependency; its sealed records are read-only history.

## Scientific question

The campaign fixes workload `W`, serving backend `B`, lifecycle `L`, and
measurement `M`. The primary independent variable is execution policy `A`:

* `B0_NATIVE_SERIAL`: await durable completion of each `add_episode` in
  source order before admitting the next episode.
* `B1_NAIVE_WHOLE_UPDATE_ASYNC`: create one task per source-ordered episode,
  gather all tasks with complete exception accounting, and wait for durable
  completion.

Both methods use the same frozen backend and client contracts in
`configs/frozen_backend_v1_3.json` and `configs/frozen_client_v1_3.json`.
Neither method receives a policy-specific decode, retry, backend, worker, or
application concurrency override.

## Workload

The workload is ordered saturated fixed work: no synthetic arrival, no arrival
rate parameter, no think time, and no wall-clock sleep derived from semantic
reference times. The four development histories are `07741c45`,
`b6019101`, `6071bd76`, and `a2f3aa27`; they are permanently marked
`DEVELOPMENT_EXPOSED` and cannot support a held-out claim.

## Backend

The construction endpoint is Qwen3-32B-FP8 on port 8000 and the embedding
endpoint is Qwen3-Embedding-0.6B on port 8001. Exact explicit launch values are
recorded in the frozen backend contract. Values omitted by the launch command
remain pinned-version defaults. Neo4j, Graphiti, and client behavior are held
constant across B0 and B1.

Hardware and process identity can be described in an experimental setup, but
they are not validity predicates for a block. A block is valid from workload,
execution, lifecycle, measurement, completeness, and correctness evidence.

## Common lifecycle

Every block follows the event contract in `BLOCK_LIFECYCLE_CONTRACT.md`:

```text
fresh namespace
  -> backend prepared -> service ready -> fixed disjoint warmup
  -> backend idle -> monotonic timer start -> formal E0
  -> construction -> drain -> last durable acknowledgement
  -> CONSTRUCTION_DURABLE_COMPLETE -> timer stop
  -> validation/canonical projection/seal/QA
```

Preparation, warmup, model loading, canonical projection, correctness checks,
hashing, and QA are outside `T_build`. Retry time caused by the method is
inside `T_build`. A fresh namespace is required for every block.

## Qualification order

The current qualification prefix is the fixed 12-episode sequence
`B0-A -> B0-B -> B1`. It is a readiness check, not the formal table. Future
development execution follows the four histories and eight method-history
blocks; QA is read-only and reuses the existing quality framework.

## Validity and reporting

Each method/history pair reports makespan, throughput, source tokens, LLM
logical calls and transport attempts, LLM tokens, embedding work, database
work, retries, direct semantic violations, publication coverage, canonical
relation, and quality. A B1 semantic or ordering difference is a result to
report. Dropped work, incomplete traces, namespace contamination, runner
failure, or correctness-accounting failure invalidates the attempt.

## Scope boundary

This package does not implement a V5 runtime, change the historical v3.1
implementation, run a provider diagnostic, or select held-out histories. A
source-0 semantic diagnostic is authorized only after the protocol cleanup,
Native Serial certification, and real-seam passive observer qualification all
pass.
