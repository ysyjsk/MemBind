# V4-MSEG-Q0 Fine-Grained Causal Telemetry Qualification

```text
STATUS: FAIL_INSTRUMENTATION_QUALIFICATION
MEASUREMENT_ONLY: yes
NEW_MECHANISM_AUTHORIZED: no
NEW_SCHEDULER_AUTHORIZED: no
BASELINE_SEALED: yes
BASELINE_ROOT: /data/predator/ly/MemBind/paper-eval-v3/artifacts/paper_eval/membind_v31/optimization/pilots/membind-v31-opt-w4-20260818-001
Q0_ROOT: /data/predator/ly/MemBind/paper-eval-v3/artifacts/paper_eval/membind_v4/mseg/q0/membind-v31-opt-w4-q0-20260820-001
POST_Q0_ACTION: STOP_V4_FINE_GRAINED
BLOCKING_REASONS: published_state_parity_failed, request_count_parity_failed, request_kind_parity_failed, semantic_input_token_parity_failed
```

Q0 changes observability only. It does not change Graphiti prompts, schemas,
model/backend, arrival offsets, compile workers, lookahead, bind workers,
request admission K, scheduler, dependency policy, or persistence semantics.

`client running`, vLLM batch membership, and GPU execution remain distinct;
the Q0 trace makes no backend-batch claim. Read scope remains
`NOT_OBSERVABLE` unless Graphiti directly exposes exact candidate IDs. Final
resolved UUIDs are effect evidence, not a read set.

The sealed W=4 pilot remains immutable. A PASS authorizes only offline MSEG
reconstruction and O1/O2/O3/O4 replay; it does not authorize a mechanism live
run. A FAIL stops fine-grained claims at this qualification boundary.
