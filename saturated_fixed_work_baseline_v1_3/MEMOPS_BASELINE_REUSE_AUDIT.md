# CURRENT_BASELINE_REUSE_AUDIT

## Source of truth

The v1.3 protocol and frozen backend/client contracts are authoritative. The
v1.2 package is a legal implementation dependency only where v1.3 imports it
explicitly; its sealed artifacts remain read-only.

1. **B0 entry:** `formal_baseline.run_formal_baseline_async()` builds a v1.2
   `FormalBlock` with `Method.B0_NATIVE_SERIAL`, then calls v1.2
   `execute_live_block()`. The schedule implementation is
   `saturated_fixed_work_baseline_v1_2.schedules.execute_native_serial`.
2. **B1 entry:** the same v1.3 function builds a block with
   `Method.B1_NAIVE_WHOLE_UPDATE_ASYNC` and calls the same
   `execute_live_block()`. The schedule implementation is
   `execute_naive_whole_update_async`; no v1.3 scheduler is copied.
3. **Graphiti call chain:** v1.3 `build_v13_live_dependencies()` composes the
   pinned runtime, recorder, instrumentation, measurement, canonical exporter,
   and episode source. `execute_live_block()` constructs the v1.2
   `GraphitiNativeAdapter`, whose `add_episode()` forwards the existing
   `EpisodeInput` fields to Graphiti 0.29.3 `Graphiti.add_episode()`.
4. **Workload object:** the formal path consumes v1.2
   `saturated_fixed_work_baseline_v1_2.contracts.EpisodeInput` with
   `history_id`, `session_id`, zero-based contiguous `source_sequence`,
   `source_hash`, `reference_time`, complete `body`, and `namespace`.
5. **Namespace/lifecycle/artifact:** `FormalBlock.namespace` is fresh per
   method/sample/run/attempt. `execute_live_block()` owns the common lifecycle,
   `AttemptStore`-compatible append-only journal, `native_trace.jsonl`,
   canonical graph, seal, and completeness checks.
6. **QA separation:** construction seals first. The v1.2 production QA lane
   opens a separate read-only runtime and reports zero construction/write calls;
   QA runs after construction and cannot mutate the namespace.
7. **Legal v1.2 reuse:** `EpisodeInput`, `FormalBlock`, `execute_live_block`,
   `GraphitiNativeAdapter`, `build_local_qa_components`, the retrieval/reader/
   judge composition, `AttemptStore` compatibility shell, and the existing
   B0/B1 schedules are all imported by v1.3 today.
8. **MemOps insertion point:** only the dataset/workload boundary is adapted.
   MemOps JSON is parsed and frozen into the existing `EpisodeInput` and
   gold-blind QA projections. No B0/B1 execution semantics, Graphiti adapter,
   instrumentation, or scheduler is reimplemented.

## Reference-time policy

MemOps has no absolute timestamps. The adapter maps sorted official
`segment_index` order to `2000-01-01T00:00:00Z + source_sequence minutes`.
This mapping is deterministic, gold-blind, monotonic, and identical for B0 and
B1. It is not derived from old/new values or QA answers.
