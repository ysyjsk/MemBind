# Q1 H0-B replacement-003 concurrent failure root cause

## Conclusion

The replacement-003 terminal event is an infrastructure interruption that the
R5 concurrent harness misclassified as `candidate_qualification_failure`. It is
not evidence that Q1 failed host-stack qualification.

This conclusion is content-independent. It uses the sanitized attempt ledger,
call keys, exception-class control flow, checkpoint hashes, and source-bound
code. No model response body was inspected.

## Causal chain

1. Source sequence 6 entered Graphiti bulk edge deduplication and launched 12
   concurrent `dedupe_edges.resolve_edge` calls. Graphiti uses
   `semaphore_gather`, which delegates to `asyncio.gather` without collecting
   all sibling exceptions or imposing infrastructure-failure precedence.

2. All completion calls in one history share one `H0WireObserver`. Each request
   snapshots `len(observer.events)` and later requires the shared length to be
   exactly `snapshot + 1`. Concurrent siblings append to the same list, so this
   length-delta condition does not identify the event belonging to one request.

3. The first three concurrent edge-dedupe responses reached HTTP 200, but
   sibling event appends made the length-delta check fail. R5 recorded
   `wire_request_observation_failure` and raised `H0QualificationError` for each.

4. The next seven sibling calls recorded `failure_class=vllm_unreachable` and
   raised the infrastructure exception type. The final two ledger entries were
   still incomplete when first-exception unwinding and event-loop shutdown
   occurred.

5. `asyncio.gather` propagated one of the earlier qualification exceptions to
   the phase runner. There is no persisted traceback, so the artifact cannot
   distinguish whether suffix 0, 1, or 2 propagated first; that uncertainty does
   not affect the infrastructure disposition.

6. The live runner classifies only the propagated top-level exception. It did
   not recheck the durable ledger for a higher-priority infrastructure failure,
   so it invoked `mark_candidate_failure` rather than the infrastructure stop
   transition.

7. The controller consequently returned exit 20 rather than exit 75 and wrote
   `candidate_selection_may_continue=true` and
   `requires_whole_stage_rerun=false`. Those two implications contradict the
   transport evidence and have no scientific validity.

## Source locations

```text
.venv/lib/python3.12/site-packages/graphiti_core/utils/bulk_utils.py:545
    launches bulk edge-dedupe calls

.venv/lib/python3.12/site-packages/graphiti_core/helpers.py:123
    semaphore_gather / asyncio.gather behavior

src/h0_graphiti_adapter.py:229
    shared H0WireObserver construction

src/h0_runtime.py:1377
    per-call observer_start length snapshot

src/h0_runtime.py:1381
    infrastructure exception recording

src/h0_runtime.py:1405
    wire-observation qualification exception

src/h0_phase_runner.py:636
    primary exception propagation

src/h0_full_history_live.py:516
    exception-to-failure-code mapping

src/h0_full_history_live.py:830
    infrastructure versus candidate terminal branch

src/h0_runtime.py:2654
    candidate-failure terminal fields

src/h0_control.py:635
    exit 75 versus exit 20 mapping
```

## Frozen evidence

```text
checkpoint_index_sha256=0b813ee7c9f4940e6981398520bf823ced3544ff540f66e03a8181ead5622a76
failure_segment_sha256=d1fad184dec05c3e32907c142382d9d1dd3b5655f2042205b201da3b21d2b732
live_log_sha256=adf687a3a73f8acf100b5be561b2b471878b4e7fe696bf2c3200878501fea24e
logical_trial_count=35
http_attempt_count=35
http_200_count=26
qualified_http_json_pydantic_semantic_count=23
wire_request_observation_failure_count=3
construction_vllm_unreachable_count=7
incomplete_concurrent_attempt_count=2
retry_count=0
source_checkpoint_count=6
embedding_workload_request_count=44
fresh_graph_count=1
closed_graph_count=1
cleanup_failure_count=0
cross_encoder_rank_call_count=0
```

## Required RED-first R6 contracts

1. Associate a request with its wire event by a stable per-request identity;
   shared-list length deltas are forbidden under concurrency.
2. If any sibling records connection, timeout, 429, or 5xx, deterministic
   terminal classification must prefer infrastructure interruption regardless
   of exception completion order.
3. Before snapshotting the attempt ledger, cancel-and-drain or await-and-collect
   every sibling so no attempt remains incomplete.

Any production-source change invalidates R5 source binding. A future live run
therefore requires an R6 artifact set, atomic closure of the consumed
replacement-003 grant, a transparent non-blind whole-stage replacement
decision, a new exact attempt ID, and an empty checkpoint namespace.

No secret, raw prompt, or raw response is persisted in this report.
