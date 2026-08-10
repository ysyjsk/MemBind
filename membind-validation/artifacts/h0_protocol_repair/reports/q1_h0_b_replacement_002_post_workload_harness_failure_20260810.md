# H0-B Replacement-002 Post-Workload Harness Failure

The authorized H0-B whole-stage attempt
`h0-q1-b-20260810-replacement-002` is terminal with status
`candidate_failed`, failure code `manifest_contract_failure`, and failure stage
`history_workload`. This report classifies that terminal label as a local
execution-harness failure, not Q1 model-quality evidence.

## Observed facts

All construction and embedding readiness checks, the authorization recheck,
the corpus check, history-factory construction, and fresh graph construction
completed successfully. Source sequence 0 then persisted a durable checkpoint.
The attempt stopped during source sequence 1 and persisted a terminal failure
segment and checkpoint index.

The terminal safe ledger contains six logical construction trials and six HTTP
attempts. All six received status 200, parsed as JSON, passed Pydantic
validation, and passed semantic-utility validation. There were no retries. The
runtime evidence records four successful embedding workload requests, one
fresh graph, one closed graph, zero cleanup failures, and zero cross-encoder
rank calls.

The source-1 safe ledger also contains the call key
`ungrouped:1:dedupe_nodes.nodes`. This is a directly observed tracking anomaly,
but it is not asserted to be the terminal cause of replacement-002.

The terminal checkpoint index is
`artifacts/h0_runs/h0/checkpoints/h0-q1-b-20260810-replacement-002/index.json`
with SHA-256
`e2187d3e101459e9c9a873d8dffb3fbcc858d139833f7f392eedff1c2c78c665`.
The source-0 checkpoint SHA-256 is
`1cdb5b70c86790d144179e855143018d2a97cd32d9e9fc70d5c1e218cd88211c`,
and the terminal failure-segment SHA-256 is
`689285595818aac01f008cb279d3a71cdb084abe35dd79e04e23e93d9d3eadd5`.

## High-confidence causal inference

Replacement-002 was bound to execution harness r4. Its immutable execution
source bundle binds `src/h0_embedding.py` to SHA-256
`3a8e70acdf8de2ced070a6916846b879d8e380e22b4fd3f06cd74def9e2fe50e`.
The installed `graphiti-core` version is `0.29.3`; its abstract embedder
interface permits `list[str]`, and its EntityNode, CommunityNode, and LLM
utility paths pass one text as a single-element list.

A content-free offline MockTransport probe invoked the r4-bound adapter with
that Graphiti input shape. It reproduced `single_input_invalid` before any
transport request. The probe is
`artifacts/diagnostics/h0_q1_b_replacement_002_embedding_contract_offline_probe_20260810_002.log`
with SHA-256
`06b255f8450852c31afce839d13bedad97f32857c86ac204e86fc6857cb06a3e`.
Together with the four successful embedding events before the next unique
Graphiti embedding boundary, this supports a high-confidence classification of
`local_embedding_adapter_interface_contract_failure_before_transport`.

This repair is independent of model response content and does not require a
scientific-configuration change. The six successful construction calls show
that the construction endpoint produced protocol-valid responses; they do not
turn this incomplete attempt into candidate performance evidence.

## Residual uncertainty

The terminal failure segment did not persist an exception subtype, sanitized
exception message, or traceback. The exact exception is therefore not proven
by the terminal checkpoint alone. The causal classification relies jointly on
the source-bound r4 adapter, installed Graphiti interfaces and call paths,
counter progression, and direct offline reproduction.

The observed `ungrouped` dedupe key is a second, latent harness-contract risk.
It requires a RED-first correction before another live attempt, but this report
does not claim that it caused the replacement-002 termination. No eventual Q1
qualification result can be inferred from this failed run.

## Qualification exclusion

Replacement-002 cannot qualify Q1. Its six model calls, four embedding calls,
source-0 checkpoint, trial counts, graph, and history evidence must not be
copied, accumulated, or merged with a later attempt. The attempt cannot be
resumed or rerun under the same stage attempt ID. Only the already-qualified
H0-A terminal binding may be reused.

## Replacement-003 recovery requirements

Recovery must be transparent and non-blind because model workload output was
observed. A new decision must record `decision_result_blind=false`,
`prior_model_workload_output_observed=true`,
`repair_required_independent_of_model_response_content=true`,
`scientific_configuration_unchanged=true`,
`old_attempt_qualification_reusable=false`,
`old_and_new_trial_counts_mergeable=false`,
`resume_failed_attempt_allowed=false`, and
`one_shot_whole_stage_replacement=true`.

The consumed replacement-002 live grant must first be revoked with an offline
tested, atomic state transition. The repair must then be source-bound as the
new `v1_3_harness_r5` / revision 5 artifact set, while r4 remains unchanged.
Admission must bind all three preceding H0-B terminal histories and allow only
the exact new attempt ID `h0-q1-b-20260810-replacement-003`. The new attempt
must begin with an empty segment directory and a fresh graph and history.

TDD gates must cover the Graphiti `create([text])` contract, fail-closed
multi-element input, sanitized infrastructure mapping through the list path,
content-independent interface evidence, the no-group dedupe call key, consumed
grant revocation, exact replacement admission, tamper and duplicate rejection,
zero-write dry runs, atomic commit, concurrent state drift, r5 identity, and r4
immutability. Focused GREEN and a full offline regression are required before
r5 binding or any live authorization.

This report does not authorize a live rerun. The machine-readable report is
`artifacts/h0_protocol_repair/reports/q1_h0_b_replacement_002_post_workload_harness_failure_20260810.json`.
