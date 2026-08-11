# MemBind Global Memory

<!-- NATIVE_CHARACTERIZATION_CURRENT_POINTER_START -->
```text
protocol_version=current-validation-v1.3
current_stage=NATIVE_CHARACTERIZATION
status=native_characterization_offline_only
current_blocker=c2_json_object_validation_failure_stop_no_fallback
current_action_scope=native_characterization_offline_only
stage_progress.native_characterization=c0_c1_pass_c2_json_object_validation_failure_after_7_completed
instrumentation_contract_status=qualified_overhead_report_only
c1_aa_classification=clean_pass
c0_dry_run_passed=true
c0_dry_run_live_request_performed=false
c0_live_passed=true
authorized_live_actions=[]
live_h0_candidate_authorized=false
service_admin_authorized=false
native_characterization_live_authorized=false
next_allowed_action=report_c2_json_object_partial_diagnostic_and_await_decision
```
<!-- NATIVE_CHARACTERIZATION_CURRENT_POINTER_END -->

```text
c2_minimal_recovery_contract=membind-validation/EXPERIMENT_PLAN.md#C2_MINIMAL_RECOVERY_POINTER
lightweight_execution_decision=artifacts/diagnostics/native_characterization_lightweight_execution_decision_20260811.md
latest_partial_diagnostic=artifacts/diagnostics/native_characterization_c2_json_object_partial_diagnostic_20260811.json
latest_partial_report=artifacts/diagnostics/native_characterization_lightweight_decision_and_partial_result_report_20260811.md
```

The lightweight execution decision makes instrumentation overhead a reported,
non-blocking perturbation measure while retaining semantic parity and timing
correctness as hard requirements. It freezes further qualification work and
permits no live action by itself; the machine pointer above remains authoritative
until the single necessary C2 transition is explicitly applied.

Both failed C2 attempts remain invalid and non-mergeable. The replacement run
`c2-723261287e32e182` stopped after the same ten completed episodes. Its exact
polluted namespace has since been verified empty. The single derived
`json_object` freeze completed seven fresh episodes and then failed Pydantic
validation in edge resolution. The prefix is diagnostic only and the current
pointer revokes all live work; `EXPERIMENT_PLAN.md#C2_MINIMAL_RECOVERY_POINTER`
retains the historical failure hashes without authorizing another recovery.
The failed fresh namespace currently contains 56 nodes and 67 relationships;
it is polluted, non-reusable, and not authorized for another cleanup.

```text
HISTORICAL_SOLUTION_LANE_BELOW=true
```

This file is the compact memory for the validation mainline. It records the
authoritative resume point and the boundaries that future agents must preserve.

## Current research-priority override

The current research priority is the independent
`../MemBind_NATIVE_GRAPHITI_CHARACTERIZATION_WORKPLAN_v1.1.md`
(`native-characterization-v1.1`). It is the current research-priority override
and starts with Native Graphiti construction characterization. The prior
`../MemBind_NATIVE_GRAPHITI_CHARACTERIZATION_WORKPLAN_v1.0.md` remains an
immutable historical plan. The old H0/M1/M2/
MemBind solution lane is frozen exploratory prototype/history: do not resume H0,
replacement-004, M2 formalization, or live solution validation from this pointer.
The frozen entry markers remain `WORKPLAN_FREEZE=true` and
`protocol_review_status=closed`. Execution initially qualified C1, passed C0,
and permanently invalidated the first C2 attempt after a structured-output
failure at episode 10. Its exact namespace was cleared, but the fresh
replacement failed at the same boundary and exposed that failed-episode and
other frozen telemetry fields were not durably captured. No further protocol
review or experiment-surface expansion is authorized. The current state revokes
all live action and permits only the bounded offline measurement-correctness
repair and explicit `json_object` deviation assessment.
Historical checkpoints, hashes, failures, credential fences, remote
forced-command limits, and `gpt55_temporary/**` exclusion remain unchanged.
The persisted reset rationale and TDD status are in
`artifacts/diagnostics/native_characterization_research_reset_20260810.md`.
The v1.1 literature review and scope decisions are in
`artifacts/diagnostics/native_characterization_plan_v1_1_review_20260810.md`.
Current status is
`instrumentation_contract_status=measurement_correctness_repair_pending`; C0
live viability passed but is not a scientific result. C2 has no completed block
and therefore has produced no valid characterization result yet. The two
ten-episode prefixes are failure diagnostics only and must never be merged.

<!-- Maintainability: keep this pointer short; detailed characterization rules
live only in the authoritative workplan to prevent document drift. -->

## Historical solution-lane validation memory

- `CURRENT_STATE.json` is the machine-readable authority. Active protocol:
  `current-validation-v1.3`; its exact current resume point is the pointer above.
  The H0 grants and attempt records below are immutable historical evidence, not
  executable authority. `v3_smoke_003_retired=true` remains historical state.
- `EXPERIMENT_PLAN.md` is the execution-facing plan for the frozen vLLM/Qwen
  validation lane.
- `../MemBind_CURRENT_VALIDATION_PLAN_v1.3.md` is the authoritative
  human-readable overlay for stage ordering and gates. v1.2 is immutable
  historical protocol evidence.
- Historical blocker ID `v3_smoke_002_m0_structured_output_failure` and
  `artifacts/environment/v3_actual_schema_compatibility_probe_20260809_005_fresh_restart.json`
  remain `historical_negative_host_qualification_evidence`. Reclassification
  never changes the raw failed events or their hashes.
- The sanitized forced-command evidence in
  `artifacts/environment/v3_construction_runtime_evidence_20260809.json` proves
  the restarted V1 engine is configured with structured-output backend `auto`
  and had no generation in the startup-log snapshot. The single authorized
  fresh-runtime probe `005_fresh_restart` has since reproduced the exact
  historical truncation. The immediate action scope is now
  historical `blocked_waiting_for_explicit_protocol_deviation` state. v1.3
  supersedes its action gate but preserves all sanitized evidence.
- Q1/H0-A attempt `h0-q1-a-20260809-attempt-001` technically completed 3/3
  fixed-seed checks but is `invalidated_protocol_gate_order`. It is diagnostic
  only: protocol qualification, candidate-selection eligibility, H0-B advance,
  and automatic rerun are all false. The immutable checkpoint index SHA256 is
  `127c81b39ccd705d7c67dc936e953992d5be97f4065fd56f3655db52d12ad309`.
- At that historical point, the next work was the exact offline H0-B
  post-workload recovery sequence recorded below. The valid H0-A replacement is
  preserved; the terminal r2,
  interrupted r3, and terminal replacement-002 attempts are never resumed or
  merged. R5 has been generated offline but is not yet repair-bound or live-authorized.
- Offline harness-r2 implementation is now complete through H0-A/B/C execution,
  three-service stage readiness, per-source checkpoints, H0-B terminal validation,
  one-shot repair admission, and A-to-B-to-C state progression. The first full
  discovery regression is 479/479 green. Formal r2 generation and every live
  request remain forbidden until the final regression/evidence bind and explicit
  state transition.
- r2 source binding includes `execution_source_bundle`: 31 explicit mainline and
  transitive local Python sources, 11 generated JSON artifacts total, and 10
  runtime bindings. Any bound-source edit requires a new r2 resolve before live.
- Live H0, V2-R, V3-R, V4-V7, P1-P4, and future work remain forbidden.
- Mainline construction uses Qwen3-32B-FP8 through vLLM 0.26.0 at the frozen
  internal endpoint. Embedding uses the frozen Qwen3-Embedding-0.6B endpoint.
  Its verified deployment dtype is BF16; the old FP16 config placeholder is
  invalid.

### Historical H0-B recovery ledger

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

The preceding block is immutable R4 authorization history. The live grant was
consumed by replacement-002 and revoked. The following block is the completed
R5 offline-recovery history:

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

R5 generation alone was not a repair decision, state bind, or live authorization.
The transparent decision, offline bind, and independent authorization have now
each passed a zero-write dry-run and been committed. Replacement-003 must begin
from empty segments; none of replacement-002's workload evidence can qualify or
merge. The authorization itself is not an experiment result:

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

Replacement-003 then encountered construction vLLM unreachability during the
concurrent source-6 workload. The runner misclassified the terminal event, so
the stop fence below supersedes the execution meaning of the consumed live bit;
the state still needs a new offline, source-bound closure transition:

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

The H0-A 3/3 observation is a bounded fixed-seed canary. The H0-B r2 failure
contains zero model workload evidence and is not candidate performance data.
Detailed safe program segments are persisted; monitoring is frequent at startup
and uses longer intervals only after stable checkpoint progress.

## Protocol v1.3 invariants

- `c6853660` is an exposure-quarantined historical regression canary. It never
  participates in candidate selection or held-out formal evaluation. H0 uses
  only the four calibration IDs from `frozen_split_v1_3.json`. That manifest is
  replayed by `src/dataset_v1_3.py`; the immutable v1.2 generator is untouched.
- Q0 is never rerun. Q1/Q2/Q3 are immutable content-addressed delta specs over
  `configs/h0/shared_host_base_v1_3.json`, not runnable manifests. All unresolved
  base hashes must be closed into a resolved manifest before a live gate.
  Selection is first-passing and never observes performance.
- Q0-Q3 keep `seed=20260806`. H0-A performs three repeated bounded trials, not
  statistically independent samples. Q0 and Q1 use different qualification
  wrappers, so their result is not a causal budget-only A/B comparison.
- Every request records requested/effective completion budget. Q2/Q3 must prove
  `top_k/min_p` entered the payload. Q3 is explicit and must inject the
  effective shim schema, not the unmodified upstream schema.
- Parse/Pydantic success alone is insufficient. A pre-candidate,
  calibration-only semantic-invariant manifest rejects valid-but-empty,
  constant, blank-name, duplicate-name, or source-invalid output.
- H0 checkpoint granularity is frozen: per logical trial for H0-A and per
  `source_sequence` for H0-B/H0-C. Checkpoints retain sanitized detailed ledgers,
  counts, hashes, failure codes, progress, artifact paths, and SHA256 values;
  they never retain raw prompts/responses, credentials, or environment dumps.
- Infrastructure interruption preserves partial evidence for diagnosis but
  never for qualification reuse. After recovery, the whole affected H0 stage
  uses a new attempt ID. vLLM connectivity failure means stop-and-report; it
  must not silently retry or automatically advance to the next candidate.
- H0-A is exactly three fresh-client public `extract_nodes` calls sharing one
  stage ledger, with zero database or embedding calls. H0-B/C use an asserted
  clean graph per history and no LLM warm-up. Graph nonempty means entity count
  greater than zero; source mapping includes episodic-set and resolved-edge
  attribution, and Recall@10 uses the first ten unique session IDs from at most
  ten RRF edges. Any H0-C infrastructure interruption reruns all three histories
  under one new attempt ID.
- `src/h0_bootstrap.py` must run before every H0 Graphiti import, force
  `PYTHON_DOTENV_DISABLED=1` and `GRAPHITI_TELEMETRY_ENABLED=false`, and remain in
  the execution source hash set. Explicit project credential reads occur only
  after the exact live gate.
- Legacy `artifacts/h0/**` is path-bound to the invalid attempt and must not be
  moved, deleted, or overwritten. The repaired set is
  `artifact_set_id=v1_3_harness_r2`, `execution_harness_revision=2`, rooted at
  `artifacts/h0_manifest_sets/v1_3_harness_r2/`.
- A Q1 repair rerun is non-blind because the old 3/3 technical outcome is known.
  It can be authorized once only after a durable deviation record proves no
  change to candidate order/spec, calibration input, thresholds, seed/trials,
  or request/retry policy. Old and new trials never combine into a pass.
- H0 cannot write formal oracles. V2-R creates a content-addressed namespace
  bound to the qualified host manifest. Correctness uses capture/read-only
  replay; performance uses the same live model stack with response replay off.
- U0/D0 representativeness and quality-feasible M1/M2 tuning occur only after
  V3-R. C8 is an `iso-cap` comparison, not a claim of equal observed usage.
- The current Pilot does not support a General Agent Memory Runtime claim.
  P1-P4 remain `authorized=false` until a V7 GO and a new protocol.

<!-- Maintainability: detailed H0 checkpoint semantics live in the authoritative
v1.3 overlay; this file intentionally keeps only the resume-critical invariants. -->

## Mainline exclusion fence

- `gpt55_temporary/**` and every GPT-5.5/LabForge compatibility artifact are
  quarantined, out-of-scope history. Mainline work MUST NOT import, execute,
  test, cite, or copy code or evidence from that tree.
- Temporary GPT-5.5 results are not V3/V4/V5/V6/V7 evidence and do not satisfy
  any construction-model, correctness, performance, or service-health gate.
- Do not inspect the quarantined implementation for design guidance while
  executing the mainline. Its only relevant fact is that it is excluded.
- A mainline run that selects a non-vLLM construction provider is protocol
  invalid and must fail closed before model or database mutation.

## Restricted model-host access

- All remote inspection uses exactly `ssh zju-liuyi '<forced-command>'`.
- `remote_scope: /home/lhx/liuyi/**`; never access, probe, or modify any other
  `/home/lhx` path or any system directory.
- `allowed_forced_commands: status/list/read/tail/follow` and all are read-only.
- An ordinary shell, forced-command bypass, privilege expansion, and `/proc`
  access are forbidden.
- Remote write permission is absent. If a task genuinely requires a change
  under the allowed scope, report it first and wait for an explicit extension
  to the restricted script; never work around the restriction.

## Known V3 attempts

- v3_smoke_001 is an interrupted V3 attempt caused by remote model shutdown. It is not a correctness result.
- Do not reuse partial v3_smoke_001 prompt cache, embedding cache, or trace files for a pass claim.
- The pause report is artifacts/diagnostics/v3_smoke_001_pause_report_20260808.md.
- `v3_smoke_002` failed in M0 because the structured response was truncated at
  both permitted completion budgets. M2 did not start, and this is not an M0/M2
  semantic-parity result.
- Its blocker ID remains `v3_smoke_002_m0_structured_output_failure`.
- Do not reuse the partial `v3_smoke_002` prompt or embedding cache. Its failure
  report is
  `artifacts/diagnostics/v3_smoke_002_failure_report_20260809.md` with SHA256
  `060e59eeb5e68015f8b0a022b5e266e19be15dd16dcac7fe240e7c20e8a5b09e`.
- Do not start `v3_smoke_003` until frozen-protocol service compatibility is
  demonstrated or an explicit protocol deviation is approved.
- Offline diagnosis proves four bitwise-identical `2048 -> 8192` truncation
  trajectories and an unbounded `ExtractedEntities.extracted_entities` array;
  it does not prove the deployed guided-decoding backend or a schema root cause.
- The redacted diagnosis artifacts are
  `artifacts/diagnostics/v3_smoke_002_structured_failure_diagnosis_20260809.md`
  and
  `artifacts/diagnostics/v3_smoke_002_structured_failure_diagnostic_20260809.json`.
- Metadata attempt02 used an invalid proxy route and is not service evidence.
  Direct attempt03 proves vLLM 0.26.0, the expected alias/root/max context, and
  healthy version/models/health endpoints; `/server_info` is disabled.
- After the service restoration,
  `artifacts/environment/v3_vllm_metadata_probe_20260809_attempt04_restored.json`
  again passed the direct version/models/health checks while `/server_info`
  remained 404. Classification:
  `service_restored_backend_config_unavailable`. No generation endpoint was
  called, so the backend evidence gate did not change.
- The first source-0/source-1 controls bypassed the public `generate_response`
  wrapper and omitted its language instruction. The resulting construction
  runtime-drift artifact and proposed blocker
  `v3_construction_runtime_identity_drift_after_smoke_002` are an invalidated
  diagnostic, not a current blocker.
- Corrected artifact
  `artifacts/environment/v3_actual_schema_compatibility_probe_20260809_004_reclassified.json`
  proves exact historical truncation reproduction without another model call.
- The single authorized fresh-runtime artifact
  `artifacts/environment/v3_actual_schema_compatibility_probe_20260809_005_fresh_restart.json`
  again reproduces all four exact `2048 -> 8192` pairs at 5795 prompt tokens.
  Post-request evidence is in
  `artifacts/environment/v3_post_compatibility_runtime_evidence_20260809.json`;
  the detailed result is in
  `artifacts/diagnostics/v3_fresh_runtime_compatibility_failure_report_20260809.md`.
- `v3_smoke_003` is permanently retired rather than reopened under v1.3. The
  historical next state was `blocked_waiting_for_explicit_protocol_deviation`;
  the active v1.3 state now permits only offline H0 TDD. A new correctness smoke
  uses a V3-R namespace only after H0 and V2-R pass.

## Maintenance notes

- New diagnostic scripts should include a module docstring explaining whether they are mainline or temporary.
- New tests should include a short class docstring when they protect protocol boundaries rather than ordinary implementation details.
- Diagnostics may persist short credential fingerprints but must never persist raw API keys or Authorization headers.
