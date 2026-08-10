# Q1 H0-B replacement-003 infrastructure misclassification

## Disposition

`h0-q1-b-20260810-replacement-003` is infrastructure-inconclusive. It is not a
Q1 candidate-performance failure and does not authorize Q2.

The runner persisted `candidate_qualification_failure` and exited with code 20,
but the same immutable failure segment records seven construction calls as
`vllm_unreachable` and two additional calls as incomplete. This satisfies the
operator stop rule. No retry or additional service probe was issued.

## Durable progress

| Observation | Value |
|---|---:|
| Readiness | construction, embedding, Neo4j, authorization all passed |
| Completed source checkpoints | 6 (`07741c45-000` through `07741c45-005`) |
| Construction logical trials / HTTP attempts | 35 / 35 |
| HTTP 200 | 26 |
| JSON / Pydantic / semantic success | 23 / 23 / 23 |
| Wire-observation failures after HTTP 200 | 3 |
| `vllm_unreachable` attempts | 7 |
| Incomplete concurrent attempts | 2 |
| Retry count | 0 |
| Embedding workload requests | 44 |
| Fresh / closed graphs | 1 / 1 |
| Cross-encoder calls | 0 |

The interruption occurred in source sequence 6 during twelve concurrent
`dedupe_edges.resolve_edge` calls. Suffixes 0-2 reached HTTP 200 but failed the
wire-observation contract, suffixes 3-9 recorded `vllm_unreachable`, and
suffixes 10-11 remained incomplete when the task group stopped.

## Classification defect

The concurrent task group exposed two failure classes. A qualification exception
won exception propagation before the infrastructure exceptions, so the generic
terminal handler called `mark_candidate_failure`. The resulting fields
`candidate_selection_may_continue=true` and `requires_whole_stage_rerun=false`
are unsafe for scientific progression because they contradict the persisted
transport evidence.

The classification uses only content-independent request outcomes and hashes;
it does not inspect model response content.

## Recovery fence

- Do not resume or rerun replacement-003.
- Do not merge any of its calls, source checkpoints, graph, history, or counts
  into a later qualification attempt.
- Do not advance to Q2 from the recorded `candidate_selection_may_continue` bit.
- Do not reuse the still-present live grant in `CURRENT_STATE.json`; it was
  consumed by this terminal attempt.
- The only next work is offline RED-first TDD for concurrent failure precedence
  and a fail-closed transition that closes the consumed R5 grant. Any later live
  attempt requires a new source-bound harness revision and an explicit one-shot
  authorization.

## Evidence hashes

```text
checkpoint_index_sha256=0b813ee7c9f4940e6981398520bf823ced3544ff540f66e03a8181ead5622a76
failure_segment_sha256=d1fad184dec05c3e32907c142382d9d1dd3b5655f2042205b201da3b21d2b732
failure_evidence_sha256=54d4e9f9bbd27ab7d53e4efcbd191c097368623fe67b51dc25f1d3dac66e8ad8
live_log_sha256=adf687a3a73f8acf100b5be561b2b471878b4e7fe696bf2c3200878501fea24e
current_state_sha256=e4c376bdb4559140d2380144c76bc33579c694d90cf098330cb4ede9b462c6c3
```

No secret, raw prompt, or raw response is persisted in this report.
