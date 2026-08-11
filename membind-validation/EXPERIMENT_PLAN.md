# MemBind Basic Validation: Current Validation v1.3 Execution Plan

<!-- NATIVE_CHARACTERIZATION_CURRENT_POINTER_START -->
```text
protocol_version=current-validation-v1.3
current_stage=NATIVE_CHARACTERIZATION
status=native_characterization_offline_only
current_blocker=none
current_action_scope=native_characterization_offline_only
stage_progress.native_characterization=c0_pass_c2_runner_tdd_pending
instrumentation_contract_status=qualified
c1_aa_classification=clean_pass
c0_dry_run_passed=true
c0_live_request_performed=false
authorized_live_actions=[]
live_h0_candidate_authorized=false
service_admin_authorized=false
native_characterization_live_authorized=false
next_allowed_action=implement_c2_runner_offline
```
<!-- NATIVE_CHARACTERIZATION_CURRENT_POINTER_END -->

```text
HISTORICAL_SOLUTION_LANE_BELOW=true
```

<!-- Historical solution-lane snapshot retained below for legacy offline
contracts; it grants no current H0 or characterization live authority. -->
```text
current_stage: H0
status: h0_q1_b_live_only
current_blocker: none
current_action_scope: h0_q1_b_live_only
```

> **当前 research-priority override**：Native Graphiti characterization 的权威
> workplan 是 [`MemBind_NATIVE_GRAPHITI_CHARACTERIZATION_WORKPLAN_v1.1.md`](../MemBind_NATIVE_GRAPHITI_CHARACTERIZATION_WORKPLAN_v1.1.md)，
> protocol ID 为 `native-characterization-v1.1`；Machine-searchable status:
> `current research-priority override`。旧版
> [`MemBind_NATIVE_GRAPHITI_CHARACTERIZATION_WORKPLAN_v1.0.md`](../MemBind_NATIVE_GRAPHITI_CHARACTERIZATION_WORKPLAN_v1.0.md)
> 保留为不可变历史版本。
> Frozen entry status: `WORKPLAN_FREEZE=true`；
> `protocol_review_status=closed`；
> `next_allowed_work=C1_instrumentation_implementation`。
> C1 已完成 qualification，C0 dry-run 已通过且没有 live request；当前恢复点以
> 文件顶部 machine-readable pointer 和 `CURRENT_STATE.json` 为准。
> 本文件是 frozen solution-validation lane 的执行镜像，不得启动 H0/M1/M2 或
> replacement-004；C0 live grant 仍须在 operator 启动两项 vLLM 服务后单独授权。

This preserves the concise historical execution overlay for
`../MemBind_CURRENT_VALIDATION_PLAN_v1.3.md`. The Native characterization v1.1
workplan and the current pointer above control task order.
`../MemBind_basic_validation_experiment.md` remains the source for frozen models,
data, methods, metrics, and decision thresholds.

The following block is an immutable historical H0 snapshot, not the current
execution pointer:

```text
protocol_version: current-validation-v1.3
current_stage: H0
status: h0_q1_b_live_only
current_blocker: none
current_action_scope: h0_q1_b_live_only
live_h0_candidate_authorized=true
v3_smoke_003_retired=true
next_allowed_action: run_q1_h0-b-post-workload-replacement
forbidden_until_pass: live-H0/V2-R/V3-R/V4/V5/V6/V7/P1/P2/P3/P4/future_work
```

Current recovery evidence and order are frozen here so a resume cannot confuse
the valid H0-A result with the pre-workload H0-B harness failure:

```text
current_recovery_stage=h0_b_harness_recovery_r3_one_shot_replacement
historical_r2_evidence_preserved=true
h0_a_replacement_attempt_id=h0-q1-a-20260809-replacement-001
checkpoint_index_sha256=91c202b2494a690483a345fb73d04733c8f68b9c980edef8caa46565868438f7
runtime_definition_sha256=ada353cf5a418005e06ed5b9549d277b8c72a4aa08aec278d242e4df65f74739
terminal_result_sha256=f5315092bc3942cbd1ced6d3673730d17aa65f7483f5f20c1be97de705dc5227
trial_response_sha256[0]=a84685fc62c8c82f8f59e62d4c3cbbc9772e7fe3c99e026b3eaeeb4dbfe6703e
trial_response_sha256[1]=a84685fc62c8c82f8f59e62d4c3cbbc9772e7fe3c99e026b3eaeeb4dbfe6703e
trial_response_sha256[2]=a84685fc62c8c82f8f59e62d4c3cbbc9772e7fe3c99e026b3eaeeb4dbfe6703e
logical_trials=3/3
http_attempts=3/3
json_parse=3/3
pydantic_validation=3/3
semantic_utility=3/3
h0_b_failed_attempt_id=h0-q1-b-20260809-attempt-001
checkpoint_index_sha256=fa6280ede4387775c719abd410478b5e1db358d840a10a69025c5a6cddd48896
classification=harness_compatibility_failure_not_candidate_result
logical_trial_count=0
http_attempt_count=0
embedding_workload_request_count=0
history_count=0
source_checkpoint_count=0
fresh_graph_count=0
old_attempt_immutable=true
old_attempt_resumable=false
old_and_new_evidence_mergeable=false
Graphiti nominal clients: EmbedderClient + CrossEncoderClient
preworkload_progress=corpus_ready,history_factory_ready,graph_construction_started,graph_construction_ready
artifact_set_id=v1_3_harness_r3
execution_harness_revision=3
index=artifacts/h0_manifest_sets/v1_3_harness_r3/resolved_manifest_index_v1_3_harness_r3.json
execution_source_count=32
revoke -> r3 TDD/artifact -> transparent decision -> bind offline -> exact one-shot replacement authorize -> 49 sources
connection/timeout/429/5xx -> durable checkpoint -> immediate stop_and_report
startup_monitoring=frequent; stable_monitoring=long_interval; program_output=detailed_segmented
mainline_gpt55_temporary_access=forbidden
```

```text
current_recovery_stage=h0_b_infrastructure_rerun_r4_offline_binding
h0_b_interrupted_attempt_id=h0-q1-b-20260809-replacement-001
checkpoint_index_sha256=7305c1ff2c5790223bb22a0ad8a3e6749c3752950164641eb5a546cfe8aa4553
classification=infrastructure_interruption_not_candidate_result
stop_reason=vllm_unreachable
construction_version_probe_attempt_count=1
model_workload_http_attempt_count=0
logical_trial_count=0
embedding_workload_request_count=0
history_count=0
source_checkpoint_count=0
fresh_graph_count=0
interrupted_attempt_resumable=false
partial_qualification_reusable=false
old_and_new_evidence_mergeable=false
artifact_set_id=v1_3_harness_r4
execution_harness_revision=4
index=artifacts/h0_manifest_sets/v1_3_harness_r4/resolved_manifest_index_v1_3_harness_r4.json
index_sha256=a08b3f704c9680476990f24edc239d4af50ced39edcf9aae0d529b5ed14332d7
execution_source_count=32
replacement_attempt_id=h0-q1-b-20260810-replacement-002
revoke consumed r3 grant -> R4 TDD/artifact -> transparent infrastructure decision -> bind offline -> exact one-shot replacement-002 authorize
```

```text
current_recovery_stage=h0_b_infrastructure_rerun_r4_live_authorized
status=h0_q1_b_live_only
current_blocker=none
current_action_scope=h0_q1_b_live_only
live_h0_candidate_authorized=true
authorized_live_actions=h0_candidate
authorized_h0_candidate_id=Q1
authorized_stage_attempt_id=h0-q1-b-20260810-replacement-002
r4_decision_sha256=ec0c8b6c6d10c0a69e8a4fb3793ccb47f865f00668b58e2c9cce02bd5a2b5a8d
r4_tdd_evidence_sha256=316769827a48b940dc6cb33ca4284c9244aafef8e45a8046f2977fd00d5e87a1
state_sha256=558c93b76a0b9b8056d01efa5e013ab5992f767eb6c7047739925f39040690d1
replacement_checkpoint_exists=false
next_allowed_action=run_q1_h0-b-infrastructure-rerun
```

The preceding block is the consumed and revoked R4 authorization history. The
following block is the completed terminal-failure and pre-bind R5 history:

```text
current_recovery_stage=h0_b_post_workload_harness_repair_r5_offline_only
h0_b_post_workload_failed_attempt_id=h0-q1-b-20260810-replacement-002
checkpoint_index_sha256=e2187d3e101459e9c9a873d8dffb3fbcc858d139833f7f392eedff1c2c78c665
failure_segment_sha256=689285595818aac01f008cb279d3a71cdb084abe35dd79e04e23e93d9d3eadd5
source_checkpoint_sha256=1cdb5b70c86790d144179e855143018d2a97cd32d9e9fc70d5c1e218cd88211c
classification=local_execution_harness_interface_contract_not_candidate_result
workload_reached=true
logical_trial_count=6
http_attempt_count=6
embedding_workload_request_count=4
source_checkpoint_count=1
old_attempt_resumable=false
old_and_new_evidence_mergeable=false
artifact_set_id=v1_3_harness_r5
execution_harness_revision=5
index=artifacts/h0_manifest_sets/v1_3_harness_r5/resolved_manifest_index_v1_3_harness_r5.json
index_sha256=3f41f7520255a1ab64e9ee34efebaccbb05a1d580b7a390057ced0f02b3d13dd
execution_source_count=32
r5_status=offline_resolved_not_live_authorized
status=h0_b_post_workload_harness_failure_live_revoked
current_blocker=manifest_contract_failure
current_action_scope=h0_b_post_workload_harness_repair_offline_only
live_h0_candidate_authorized=false
authorized_live_actions=none
authorized_h0_candidate_id=none
h0_live_gate=forbidden
replacement_attempt_id=h0-q1-b-20260810-replacement-003
next_allowed_action=prepare_h0_b_post_workload_harness_repair
```

R5 generation alone did not constitute a repair decision, state bind, or live
authorization. Those separate gates have now completed after zero-write dry
runs. No trial, checkpoint, graph, or history from replacement-002 may qualify
or be merged into 003; the authorization itself is not an experiment result:

```text
current_recovery_stage=h0_b_post_workload_harness_repair_r5_live_authorized
status=h0_q1_b_live_only
current_blocker=none
current_action_scope=h0_q1_b_live_only
live_h0_candidate_authorized=true
authorized_live_actions=h0_candidate
authorized_h0_candidate_id=Q1
authorized_stage_attempt_id=h0-q1-b-20260810-replacement-003
artifact_set_id=v1_3_harness_r5
execution_harness_revision=5
index_sha256=3f41f7520255a1ab64e9ee34efebaccbb05a1d580b7a390057ced0f02b3d13dd
r5_decision_sha256=98841771c9ccf35fca6526e36295cb5f1439c256332a47a62ffac87693cc0084
r5_tdd_evidence_sha256=cb2b6d8a2e56f4ee207dbaf538da2c5c273dc701977b013fefb7af482207b89a
decision_result_blind=false
prior_model_workload_output_observed=true
repair_required_independent_of_model_response_content=true
old_attempt_qualification_reusable=false
old_and_new_trial_counts_mergeable=false
resume_failed_attempt_allowed=false
state_sha256=e4c376bdb4559140d2380144c76bc33579c694d90cf098330cb4ede9b462c6c3
replacement_checkpoint_exists=false
next_allowed_action=run_q1_h0-b-post-workload-replacement
```

Replacement-003 then encountered construction vLLM unreachability in the
concurrent source-6 workload. This stop fence supersedes the execution meaning
of the consumed authorization above without rewriting the pending machine-state
closure:

```text
current_recovery_stage=h0_b_replacement_003_infrastructure_stop_pending_offline_closure
terminal_attempt_id=h0-q1-b-20260810-replacement-003
terminal_checkpoint_index_sha256=0b813ee7c9f4940e6981398520bf823ced3544ff540f66e03a8181ead5622a76
recorded_terminal_status=candidate_failed
recorded_failure_code=candidate_qualification_failure
evidence_classification=infrastructure_interruption_misclassified_as_candidate_qualification_failure
construction_vllm_unreachable_count=7
wire_request_observation_failure_count=3
incomplete_concurrent_attempt_count=2
retry_count=0
source_checkpoint_count=6
candidate_selection_continuation_allowed=false
current_state_live_grant_consumed=true
live_execution_allowed=false
next_allowed_action=stop_and_report_then_offline_tdd
replacement_003_report_sha256=218b062834ed66e4bbdf6b65ecb405c5c17ce7c3889360534f2bec484c43a6ac
```

<!-- Maintainability: the recovery ledger mirrors the authoritative v1.3 plan.
Update it only from durable checkpoint, decision, manifest, and TDD evidence. -->

The active revision accepts a pre-freeze Host Stack Qualification stage and now
authorizes only the exact Q1/H0-B replacement-003 attempt. H0 is calibration-only, content-addressed,
first-passing, and performance-blind. Q1/Q2/Q3 are exact candidate delta specs
under `configs/h0/` and share a content-addressed base spec. They are not
runnable manifests: every unresolved client/prompt/schema/HTTP/retry hash must
be closed before a separate live gate. Valid-but-empty output fails the semantic
utility gate. Q3 is explicit and remains forbidden until its injected schema
hash equals the effective `[0]` shim schema hash.

The first Q1/H0-A attempt (`h0-q1-a-20260809-attempt-001`) technically completed
three of three fixed-seed trials with HTTP 200, non-length completion, successful
JSON/Pydantic/semantic checks, zero retries, and zero embedding/database calls.
It is nevertheless `invalidated_protocol_gate_order`: the bound import chain
could execute Graphiti's top-level `load_dotenv()` before the state gate. Its
checkpoint remains immutable diagnostic evidence and is ineligible for protocol
qualification, candidate selection, candidate advancement, or automatic rerun.
The machine live grant has been explicitly revoked.

The scientific protocol remains `current-validation-v1.3`. The repaired
execution set is separately identified as `v1_3_harness_r2` under
`artifacts/h0_manifest_sets/v1_3_harness_r2/`; the legacy `artifacts/h0/**` tree
must remain byte- and path-stable. Before generating r2, complete and source-bind
all H0-A/B/C runtime, completion validation, readiness, and cleanup code. A later
Q1 replacement requires a transparent, non-blind, one-shot deviation decision,
a new attempt ID, and a whole-stage rerun. Old and new trials are never combined.

Offline implementation checkpoint (2026-08-09): the complete H0-A/B/C runner,
full-stack one-shot readiness, per-source durability, full-history terminal
validation, repair admission, and A-to-B-to-C state transitions are implemented.
The r2 graph now contains 11 generated JSON artifacts and 10 runtime bindings,
including an explicit bundle of 31 mainline/transitive local source files. The
first post-implementation discovery regression is 479/479 green. Formal r2
generation, repair-decision persistence, and live authorization remain pending;
this checkpoint is not an H0 result.

The historical single authorized frozen public-path probe was consumed under
v1.2. Artifact
`artifacts/environment/v3_actual_schema_compatibility_probe_20260809_005_fresh_restart.json`
reproduces all four historical `2048 -> 8192` truncation pairs byte-for-byte at
the historical 5795 prompt tokens. The sanitized post-request restricted-log
evidence records 8/8 HTTP 200 completions and no server error, so connectivity
is not the blocker.
The configured structured-output backend remains `auto`, while the
request-selected backend is still unobserved.
It remains `historical_negative_host_qualification_evidence`; the historical
blocker ID is `v3_smoke_002_m0_structured_output_failure`. No live H0, full
smoke, construction service change, V2-R, V3-R, V4, V5, V6, V7, or paper-stage
execution is authorized.

Machine-readable state is in `CURRENT_STATE.json`. Historical smoke failures do
not create new tasks; their retained evidence index is
`artifacts/history/SMOKE_HISTORY.md`.

Remote model-host inspection is restricted to
`ssh zju-liuyi '<forced-command>'` with
`remote_scope: /home/lhx/liuyi/**` and
`allowed_forced_commands: status/list/read/tail/follow`. These operations are
read-only. An ordinary shell, access outside that scope, forced-command bypass,
and privilege expansion are forbidden. Remote write permission must be
explicitly reported as necessary and enabled in the restricted script before
any modification is attempted.

`v3_smoke_002` failed during M0 structured extraction after both frozen
completion budgets ended with `finish_reason=length`; M2 never started. Its
prompt and embedding caches are partial and must not be reused. The immutable
failure report is
`artifacts/diagnostics/v3_smoke_002_failure_report_20260809.md`. A new live
attempt is forbidden until offline evidence demonstrates service compatibility
under the unchanged protocol, or an explicit protocol deviation is approved.

Offline diagnosis is persisted in
`artifacts/diagnostics/v3_smoke_002_structured_failure_diagnosis_20260809.md`
and the redacted machine-readable analysis in
`artifacts/diagnostics/v3_smoke_002_structured_failure_diagnostic_20260809.json`.
It proves four identical `2048 -> 8192` truncation trajectories and identifies
the installed extraction schema's unbounded `extracted_entities` array, without
claiming a backend root cause. The corrected no-generation metadata probe is
`artifacts/environment/v3_vllm_metadata_probe_20260809_attempt03.json`; direct
version/models/health checks pass, while `/server_info` is not enabled. The
first source-0/source-1 probes incorrectly called the private generation method
and omitted the public wrapper's language instruction; their `-43` token result
and runtime-drift diagnosis are invalidated. The corrected public path reports
the historical 5795 prompt tokens and reproduces all four exact
`2048 -> 8192` failure pairs byte-for-byte. The current blocker is therefore
the confirmed structured-output failure. `v3_smoke_003` remains forbidden until
the deployed backend/config is evidenced and the same public-path probe parses,
or an explicit protocol deviation is approved.

After the service was restored, the read-only direct metadata probe
`artifacts/environment/v3_vllm_metadata_probe_20260809_attempt04_restored.json`
again passed version/models/health but `/server_info` remained 404. Its
classification is `service_restored_backend_config_unavailable`: availability
is restored, while the current `evidence_collection_only` gate is unchanged.
No generation or compatibility probe was run.

Restricted startup evidence is now persisted as
`artifacts/environment/v3_construction_runtime_evidence_20260809.json`. It
proves the configured backend is `auto` and the restarted log snapshot contains
no generation. Classification:
`configured_backend_auto_fresh_service_no_generation_observed`. The authorized
probe against that fresh runtime then reproduced the historical truncation in
`005_fresh_restart`; the detailed conclusion is persisted in
`artifacts/diagnostics/v3_fresh_runtime_compatibility_failure_report_20260809.md`.
The actual request-selected backend remains unobserved. Further live execution
requires an explicit protocol deviation or an evidenced service-side correction.

## Frozen Topology

Machine A serves both OpenAI-compatible model endpoints:

- construction: http://10.87.5.247:8000/v1/, model qwen3-32b-fp8;
- embedding: http://10.87.5.247:8001/v1, model qwen3-embedding-0.6b;
- credentials are loaded only from the ignored project `.env` file.

Machine B runs Graphiti v0.29.3 at commit `021d3a5`, the replay driver, and
Neo4j Community 5.26 locally without Docker. The construction runtime is vLLM
0.26.0 with `max_model_len=40960`; the embedding dimension is 1024. M0, M1,
and M2 share the same endpoints, prompts, schema, decoding, database settings,
and construction concurrency cap of 8. Batch Invariance remains off/default.
`M0` remains the internal method ID for artifact compatibility; its report label
is `Deterministic-Graphiti-Serial`, meaning Graphiti v0.29.3 with the same
deterministic candidate-ordering adapter applied to M0/M1/M2. It is not claimed
to be untouched upstream Graphiti.

The previous `2048 -> 8192`, temperature 0, top_p 1 and `json_schema` rules are
the Q0 historical failure contract, not the v1.3 final host configuration.
Q1-Q3 request 16384 but use the frozen context-safe effective-budget formula;
Q2/Q3 must prove `top_k/min_p` entered the request payload; Q3 must inject the
effective `[0]` schema. The first candidate passing all calibration reliability
and semantic-utility gates is frozen symmetrically for M0/M1/M2. Input prompts
are never truncated.

## TDD Protocol

Every code or instrumentation change follows this sequence:

1. Add the smallest contract or regression test.
2. Run it and persist the expected red output under `artifacts/tdd/`.
3. Implement only the behavior required by that test.
4. Run the focused green tests.
5. Run `.venv/bin/python3 -m unittest discover -s tests -q` before a live gate.
6. Persist the test command, counts, result, and relevant artifact hashes.

No live stage starts while its unit/integration gate is red. A failed live run
is immutable and a replacement always uses a new run ID.

The gate-order-invalid Q1 attempt is a disclosed protocol-repair case, not an
infrastructure failure. A replacement is permitted only after an immutable
decision records that the prior 3/3 outcome was observed, that the repair would
be required regardless of that outcome, and that candidate order/spec, input,
thresholds, seed, trial count, and request/retry policy remain unchanged. The
one-shot admission is consumed by the new whole-stage attempt.

## H0 Segmented Execution and Checkpoints

H0 uses content-addressed, safe checkpoints so a process or vLLM interruption
does not erase diagnostic evidence. H0-A checkpoints after each logical trial;
H0-B and H0-C checkpoint after each completed `source_sequence`. Each checkpoint
must emit the stage, candidate, segment, cumulative logical-call/HTTP-attempt/
retry counts, sanitized detailed ledgers, hashes, failure codes, artifact paths,
and SHA256 values. It must not contain raw prompts/responses, credentials,
Authorization headers, or an environment dump.

Partial evidence is retained after an infrastructure interruption but is never
qualification-reusable: successful segments from an interrupted attempt cannot
be combined with post-recovery segments to claim PASS. After independently
evidenced infrastructure recovery, the whole affected H0 stage is rerun under a
new attempt ID. A vLLM connectivity failure stops the current stage and is
reported to the operator immediately; it never silently retries or automatically
advances Q1 to Q2 or Q2 to Q3.

```text
checkpoint_granularity_H0_A: per_logical_trial
checkpoint_granularity_H0_B_C: per_source_sequence
checkpoint_payload: sanitized_detailed_ledger_counts_hashes_failure_codes
partial_evidence_preserved_on_interruption: true
partial_qualification_reusable_after_infra_failure: false
whole_affected_stage_rerun_with_new_attempt_id: true
vllm_connectivity_failure: stop_and_report
automatic_candidate_advance_after_connectivity_failure: false
```

The live harness also freezes the exact execution and semantic-evidence units:

```text
h0_a_execution_unit: direct_extract_nodes_public_call
h0_a_db_and_embedding_calls: zero
h0_a_client_lifecycle: fresh_per_repeated_trial_shared_stage_ledger
h0_b_c_graph_isolation: fresh_asserted_clean_graph_per_history
h0_qualification_llm_warmup_calls: forbidden
canonical_graph_nonempty_definition: entity_count_gt_zero
full_history_source_mapping: exact_episodic_set_and_resolved_edge_attribution
evidence_recall_at_10_definition: first_10_unique_session_ids_from_at_most_10_rrf_edges
h0_c_infrastructure_rerun_scope: all_three_histories_new_attempt_id
```

H0-A uses three fresh clients but one shared stage ledger and performs no graph or
embedding work. H0-B/C assert a fresh empty graph for every history and make no
extra LLM warm-up calls. An infrastructure interruption anywhere in H0-C requires
all three histories to restart under one new stage attempt ID; completed histories
from the interrupted attempt remain diagnostic evidence only.

<!-- Maintainability: this section mirrors the normative checkpoint contract in
../MemBind_CURRENT_VALIDATION_PLAN_v1.3.md; change the overlay first and keep this
execution summary and GLOBAL_MEMORY.md synchronized. -->

## Single-Line State Machine

```text
H0 Host Stack Qualification: offline contracts/harness
  -> separate live-Q1 gate
  -> freeze first calibration-qualified shared stack
  -> V2-R Correctness oracle requalification
  -> V3-R Full M0/M2 correctness smoke
  -> V4 U0/D0 guardrail + calibration + minimal profiling
  -> V5 quality-feasible M1/M2 concurrency tuning
  -> V6 Formal evaluation (24 correctness + 48 performance)
  -> V7 Analysis + validation verdict
  -> STOP
```

Skipping a stage is prohibited. P1-P4 are future-work-only and unauthorized.
The closed V1/V2 artifacts and the old V3 failure remain historical evidence;
they are not skipped or relabeled as v1.3 passes.
Historical stage label: `V1 Correctness nondeterminism closure`.

## V1 Closed Contract

The existing source-5 snapshots prove that some equal logical edge texts and
query paths have different vector hashes, while logical graph size and
full-text hashes remain stable. They do not contain raw vectors, so they cannot
alone produce cross-run cosine, L2, or max-absolute deltas.

V1 is a retained-artifact closure. Reuse the old artifacts for logical keys,
embedding hashes/dimensions/norms, saved membership/order, and prompt evidence.
The old vectors cannot be reconstructed; a new live sample would be a different
execution and would not recover them. V1 therefore must not call either model,
Neo4j, or any live service, and must not run a six-episode or full trace.

Persist the unavailable numerical fields exactly as
`not_computable_from_retained_artifacts` for cross-run cosine, L2, and
max-absolute difference. Claim exact input equality only where retained bytes or
an exact input hash support it; otherwise record `not_available`. The conclusion
must distinguish bitwise variation from an established or unestablished effect
on ranking/top-K/prompt construction.

Required output:

```text
artifacts/diagnostics/embedding_nondeterminism_source5.json
```

V1 ends after this single analysis whether or not numerical drift changes
top-K. Live embedding bitwise identity is not a later correctness prerequisite.

## V2 Model Oracle

Correctness freezes every model-derived output used by the frozen path:

```text
M0 capture: LLM response + embedding vector
M1 read-only replay: same M0 oracle
M2 read-only replay: same M0 oracle
```

LLM or embedding misses always stop before live fallback. An M1 miss is an
`execution_path_divergence` with lifecycle status `completed_with_divergence`;
its `final_semantic_parity = not_evaluable_due_to_oracle_miss`, so it does not
claim a final graph error. An M2 miss is a correctness failure and blocks live
performance. Neo4j state, candidate retrieval, entity/edge resolution,
invalidation, and commits remain live on each method's fresh graph and are never
replayed. M0 diagnostics align by prompt name, source sequence, invocation
ordinal, and call-site identity where available, not by a vague nearest record.

The embedding namespace contains served model ID plus either an
endpoint-reported revision or an operator-supplied immutable deployment
fingerprint, along with dimension, dtype, pooling, normalization/instruction,
and input-transform configuration. Its source manifest is
`artifacts/environment/embedding_model_fingerprint.json`; an alias, URL, or
behavioral probe cannot masquerade as checkpoint identity. Batch composition is
excluded from the item key, so `create(["x"])` and `create_batch(["x"])` share
the exact single-item UTF-8 record. The verified deployment dtype is BF16;
`configs/base.yaml` must not retain the historical FP16 placeholder.

Cross-encoder status is **expected not invoked; measurement decides**. Audit the
actual `rank()` path over construction and frozen final retrieval, persist
`rank_call_count` and safe input hashes to
`artifacts/diagnostics/model_oracle_audit.json`, and write `not_invoked` only if
the count is zero. A nonzero count blocks V2 until that oracle is frozen.

### Historical bounded V2 pilot boundary

The following v1.2 integration is completed historical evidence. Its oracle
namespace cannot be reused after H0 changes the qualified host identity:

```text
M0 capture -> M0 read-only replay
```

Run it with:

```text
.venv/bin/python src/replay_driver.py v2-oracle-integration \
  --attempt v2_oracle_integration_001
```

The pilot uses one fixed single-episode integration instance and checks zero
replay LLM/embedding calls, zero cross-encoder calls, fresh Neo4j cleanup, equal
graph/retrieval outputs, and unchanged prompt/embedding cache hashes. It does
not run M1 and does not substitute for V3's full M0 -> M2 smoke. A runtime
namespace field without actual remote argv, startup-log, or deployed-config
evidence remains in `unresolved_fields` and blocks the live gate; local templates
and external checkpoint references are provenance, not runtime proof.

## V3-R, V4, and V5 Gates

V3-R starts only after H0 and V2-R pass. It uses a new calibration smoke ID `v3r_smoke_001`,
never the exposed historical canary or `v3_smoke_003`. It runs
one qualified M0 capture followed by one qualified M2 read-only replay and
requires zero oracle miss/fallback, exactly-once/source mapping, canonical graph
parity, retrieval guardrail, and fresh-database cleanup. M1 does not run in V3-R.

V4 first runs the instrumentation OFF/ON replay gate, then the required
`U0=Upstream-Qualified-Graphiti-Serial` versus
`D0=Deterministic-Graphiti-Serial` representativeness guardrail on all four
calibration histories. Only after that gate is retained does D0 produce the
coarse phase characterization and frozen DELTA_MS. V4 records total,
extraction, embedding/search, resolution/invalidation, DB publication,
unclassified intervals, and basic LLM/embedding/DB work counts. It does not run
the future paper-level load/Poisson sweep.

V5 first performs the bounded M1 read-only replay diagnostic using the qualified
M0 oracle. A first miss is `completed_with_divergence`, not a final semantic
claim. It then tunes both M1 and M2 over `C={1,2,4,8}` on calibration only. A
point is quality-feasible only if its correctness, retrieval, completion, and
exactly-once guardrails pass. Among feasible points, minimize calibration median
makespan and choose smaller C on an exact tie. The fixed C8 comparison is
reported as `iso-cap`; actual work and utilization remain measured outcomes.

The shared deterministic presentation contracts remain frozen across every
method and lane:

```text
logical_content_ascending_before_top_k
logical_node_content_ascending_before_top_k
logical_content_ascending_after_top_k
logical_content_ascending_before_candidate_id
```

They may not be extended to chase another correctness miss unless a separately
approved protocol deviation proves upstream physical instability requires it.

## V6 Formal Lanes

Correctness lane:

```text
8 instances x (M0 capture + M1 replay + M2 replay)
= 24 correctness runs
```

Performance lane:

```text
8 instances x 3 methods x 2 repeats
= 48 live runs
```

The formal total is 72 runs. Correctness timings are never reported as
performance. Performance runs use live LLM and live embedding, start with a
cold per-run application embedding cache, and never use the persistent oracle.
They do not require cross-run bitwise-identical embedding vectors.

Performance order uses balanced blocks keyed by `(question_id, repeat)`, with
M0/M1/M2 placed close in wall-clock time under seed 20260806. A clear
infrastructure failure retains and invalidates the old primary block; after
recovery the executor MUST rerun the entire three-method block with a new block
ID. A treatment-induced overload or method failure remains a method result and
is never relabeled infrastructure noise.

All 24 correctness runs finish first. The live lane starts only after M2 reaches
8/8 parity with zero oracle miss/fallback; M1 correctness divergence remains an
outcome and does not block performance. Each of the 16 performance blocks is
contiguous, and the six method permutations differ in use count by at most one.
Infrastructure replacement blocks are appendix-only; primary statistics use the
first complete eligible block. Both repeats retain and report their preregistered
relative gap; each gap above 10% receives a descriptive `stability_warning`, and
the count/rate is reported. It does not trigger a result-directed third repeat.

Live E2E timing retains the real same-LAN path under `NO_PROXY`, the same
endpoints, lightweight pre-run endpoint health, known service-ownership status,
and transport-failure recording. It performs no RTT subtraction and does not
restore the historical 100/20 probe or high-frequency telemetry campaign.

## V7 Verdict

The final report answers only deterministic-Graphiti bottleneck, M2 semantic parity, M2 live
performance, and whether M1 is sufficient. M1 semantic evidence comes from the
same frozen M0 oracle; M1 live speed comes from the performance lane.
Execution-path divergence and final semantic divergence are reported separately;
completion/source-order changes alone establish neither.

Write `artifacts/final/VALIDATION_REPORT.md`, select GO, INCONCLUSIVE, or NO-GO
using the frozen thresholds, and stop. No later mechanism is implemented by
this plan.
