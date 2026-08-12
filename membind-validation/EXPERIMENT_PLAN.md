# MemBind Basic Validation: Current Validation v1.3 Execution Plan

<!-- NATIVE_CHARACTERIZATION_CURRENT_POINTER_START -->
```text
protocol_version=current-validation-v1.3
current_stage=NATIVE_CHARACTERIZATION
status=native_characterization_c4_offline_only
current_blocker=none
current_action_scope=native_characterization_c4_offline_only
stage_progress.native_characterization=c2_c3_complete_c4_offline_tdd_pending
instrumentation_contract_status=qualified_overhead_report_only
c1_aa_classification=clean_pass
c0_dry_run_passed=true
c0_dry_run_live_request_performed=false
c0_live_passed=true
reference_alignment_decision=artifacts/diagnostics/native_characterization_reference_alignment_decision_20260811.md
reference_alignment_decision_sha256=e367529c381fd93b957a6ba1a69c064217fa4d190e62fa1250d784b751bd8904
reference_aligned_freeze=artifacts/native_characterization/freeze_reference_aligned_64k.json
reference_aligned_freeze_sha256=3b086ace7841bccc2479f2043f0767b4ab9ea3d4fd74459ce65ae5cccfb0b3b0
interrupted_c2_attempt=c2-2fe3711c62933407
interruption_classification=infrastructure_interruption
interruption_error_code=openai.APIConnectionError
interruption_completed_episode_count=9
interruption_failed_source_sequence=9
interruption_attempt_valid=false
interruption_attempt_mergeable=false
interruption_resume_allowed=false
interruption_semantic_attempt_consumed=false
interruption_report=artifacts/diagnostics/native_characterization_c2-2fe3711c62933407_interruption.json
interruption_report_sha256=be1922abfbe9887e633228000b371b92a342daba63f43d4f0408ddcf9bf7a986
interruption_checkpoint=artifacts/native_characterization/runs/c2-2fe3711c62933407/checkpoint.json
interruption_checkpoint_sha256=2010f6eecf82d1cab8706cd5136445c08175b3ddf9e1e1d11b8ec5f16a3735b8
interruption_outer_log=artifacts/tdd/native_characterization_c2-2fe3711c62933407_live_20260811.log
interruption_outer_log_sha256=3a453f968c6cb5b30a3ae198ac4ec79a569f8993d5a2b5e2e9ab5c32f6f646e1
serving_envelope_failed_c2_attempt=c2-4cc7d0599bbbbdac
serving_envelope_failure_error_code=openai.BadRequestError
serving_envelope_failure_completed_episode_count=10
serving_envelope_failure_completed_block_count=0
serving_envelope_failure_failed_source_sequence=10
serving_envelope_failure_attempt_valid=false
serving_envelope_failure_attempt_mergeable=false
serving_envelope_failure_resume_allowed=false
serving_envelope_failure_prefix_merge_allowed=false
serving_envelope_failure_report=artifacts/diagnostics/native_characterization_c2-4cc7d0599bbbbdac_serving_envelope_failure.json
serving_envelope_failure_report_sha256=c92ddb5b1c8b4fb20cb048816668a5d0e03516439524cd9a78f0906b2a14355f
serving_envelope_failure_checkpoint=artifacts/native_characterization/runs/c2-4cc7d0599bbbbdac/checkpoint.json
serving_envelope_failure_checkpoint_sha256=4fc29a435790c55e17c8d4966203fc39784237100131475e82993dc2bf5df120
serving_envelope_failure_outer_log=artifacts/tdd/native_characterization_c2-4cc7d0599bbbbdac_live_20260811.log
serving_envelope_failure_outer_log_sha256=68544c5a79be0e30ca6a97da54baa7916aeb1c94913d2cd1ad00af202c8de81f
serving_envelope_64k_status=64K_ENVELOPE_PASS
serving_envelope_64k_evidence=artifacts/environment/native_characterization_64k_serving_envelope_20260812.json
serving_envelope_64k_evidence_sha256=724f9bbfdf49cbf0e07def5c5fae619dcbd7b322f8a513b5c5cb8217c524b341
serving_envelope_64k_actual_prompt_tokens=26024
serving_envelope_64k_requested_max_tokens=16384
serving_envelope_64k_max_model_len=65536
cleanup_target_attempt=c2-4cc7d0599bbbbdac
cleanup_target_group=nc-e1e2-400b9b78c2c218df
cleanup_source_freeze=artifacts/native_characterization/freeze_reference_aligned.json
cleanup_source_freeze_sha256=cea700f73f7dc942deeb49195e0a3ca235c35ec51a1c06fdab0edd94738330a7
cleanup_planned_evidence=artifacts/native_characterization/c2_cleanup/c2-4cc7d0599bbbbdac.json
cleanup_execution_status=verified_empty
cleanup_evidence=artifacts/native_characterization/c2_cleanup/c2-4cc7d0599bbbbdac.json
cleanup_evidence_sha256=d52d65fc985753863b0437e3940085a7986f6902acba697f9175af7d391df08e
cleanup_evidence_payload_sha256=c721cc0da76cc5544cff1dc0e4342d05a5b647d4e82b741cdca770a5de5004a6
cleanup_pre_node_count=51
cleanup_pre_relationship_count=89
cleanup_post_node_count=0
cleanup_post_relationship_count=0
final_full_regression=artifacts/tdd/native_characterization_c2_interruption_final_full_offline_regression_20260811.log
final_full_regression_sha256=439cb3b8779b8514efd4a07ddd2b5b10f60706eb918a22e5f00b184175b6e25c
final_full_regression_test_count=793
recovery_focused_tests=artifacts/tdd/native_characterization_c2_64k_recovery_focused_green_20260812.log
recovery_focused_tests_sha256=c489f17752ddd5052627b0df07a49831b3d3eac62795f17defd6af869b006c4b
recovery_focused_test_count=50
fresh_c2_start_source_sequence=0
fresh_c2_resume_allowed=false
fresh_c2_attempts_remaining=0
completed_c2_run=c2-17cdaabd562e9673
completed_c2_episode_count=188
completed_c2_block_count=4
completed_c2_manifest_sha256=f03276ef88bfdc8062967db504514c83d941d37f929a8dbca5c37fab7aa69417
completed_c2_checkpoint_sha256=bee2e1a0e2130d6c9f3f579829680b64a3b732b814b7a09a2115f28042e42235
completed_c2_e1_breakdown_sha256=b06deae7a1387a6705adb5f897c92856fda6f55bebb1c277a39965bdeda952cb
c2_verification=artifacts/diagnostics/native_characterization_c2-17cdaabd562e9673_verification.json
c2_verification_sha256=67e4a5a59b1b2c32427516b067f477975673ae9b366d21d32324bb45da531b01
c2_verification_payload_sha256=d2f7ba19ebd372b67dc1f90661c7cb72b83984524fbdf3d02cea29ed9b010eaf
c2_completion_source_state_sha256=90e2af7e89a644422d915a80de2ca9a98d684766a738adca260e345938f8e0ae
c2_completion_focused_tests=artifacts/tdd/native_characterization_c2_completion_and_verifier_focused_green_20260812.log
c2_completion_focused_tests_sha256=519ad67f25f0c4973221640b0af5b9caa24a9661287e133fe567c053bfedf359
c2_completion_focused_test_count=13
completed_c3_run=c2-17cdaabd562e9673
c3_dependency_map=artifacts/native_characterization/dependency_map.json
c3_dependency_map_sha256=7fde0235a4110bf83383b68df15827c518bbf448fbd1e4e1d780c8efe06af398
c3_dependency_map_payload_sha256=e5f53ed575030f2acb7024e7913808c524c71e2db88853632bbe935caa4904ac
c3_e2_artifact=artifacts/native_characterization/e2_dependency_opportunity.json
c3_e2_sha256=a80ca5a8e763c19eea9d2cde1dbe001425200d04c857384cb862cc65ccf1887f
c3_e2_payload_sha256=7adc924db06e33e319d973a9b6ceaf402866bda4ea38a8755d3781f2ca86449f
c3_analyzer_source_sha256=dc0956070081d4017068878350edfc768508d6cf40389c14d2fb7e5f81ee703c
c3_completion_source_state_sha256=f86e33d0434bb267599e2c562ea3f319910c50f4949ff8b420655bd585db6e59
c3_completion_focused_tests=artifacts/tdd/native_characterization_c3_completion_focused_green_20260812.log
c3_completion_focused_tests_sha256=022178a7a892cbbf5a0970108bc01391560cbd613446333a5db19adb181b884c
c3_completion_focused_test_count=12
c3_episode_count=188
c3_history_count=4
c3_interval_count=1504
c3_T_total_ns=9081843769634
c3_p_L=0.2291969234941911
c3_p_U=0.2291969234941911
c3_S2=1.1294310624004833
c3_S4=1.2075802604205235
c3_S8=1.2508557542912377
authorized_live_actions=[]
live_h0_candidate_authorized=false
service_admin_authorized=false
native_characterization_live_authorized=false
next_allowed_action=build_native_characterization_e3_harness_offline
```
<!-- NATIVE_CHARACTERIZATION_CURRENT_POINTER_END -->

<!-- C2_MINIMAL_RECOVERY_POINTER_START -->
```text
c2_recovery_scope=single_frozen_group_only
failed_attempt_id=c2-efb58c477f12adf6
failed_attempt_error=json.decoder.JSONDecodeError
failed_attempt_completed_episodes=10
failed_attempt_valid=false
failed_attempt_mergeable=false
replacement_resume_allowed=false
prior_c2_live_grant=consumed_by_failed_attempt
polluted_group_id=nc-e1e2-400b9b78c2c218df
parser_fix_status=focused_green
parser_fix_log=artifacts/tdd/native_characterization_c2_json_extraction_green_20260811.log
parser_fix_log_sha256=92cf5aa75a17512cdb9b164a12d5ca34431c95ee004cacd2d08cab23a8641f27
cleanup_primitive=graphiti.clear_data(driver,group_ids=[target_group])
cleanup_allowlist_source=artifacts/native_characterization/freeze.json
cleanup_target_binding=target_group==polluted_group_id==freeze.screening.e1_e2.block_order[0].graph_namespace
cleanup_rejects=none,empty,multiple,other_frozen,non_frozen
cleanup_requires_explicit_operator_authorization=true
cleanup_helper_status=focused_green
cleanup_helper_source_path=src/native_characterization_c2_cleanup.py
cleanup_helper_source_sha256=8a356d514240b8b1ca983c602fcd3b37364b5a91dd985ba01f6ad650542cb1d4
cleanup_helper_test_path=tests/test_native_characterization_c2_cleanup.py
cleanup_helper_test_sha256=37b92adc8f21be63a854fb0cce44ddcdfdac1265a3bc90257b3e2007462791a5
cleanup_helper_red_log=artifacts/tdd/native_characterization_c2_cleanup_intentional_red_20260811.log
cleanup_helper_red_log_sha256=01db21896fc5696f8eb52198bbd6cac6ba4ace2c98595625e861b6eb674f9b7e
cleanup_helper_focused_log=artifacts/tdd/native_characterization_c2_cleanup_focused_green_20260811.log
cleanup_helper_focused_log_sha256=4d0a1b81b78f9b4003831fd624162a9042212154ea37f09b29732cb02a889585
c2_reauthorization_status=c2_only_live_authorized
c2_reauthorization_source_path=src/native_characterization_c2_reauthorization.py
c2_reauthorization_source_sha256=730fc474e2bac106eb6f1734c4a7feb232f7b845f75fbda80443f4f89c484eb3
c2_reauthorization_test_path=tests/test_native_characterization_c2_reauthorization.py
c2_reauthorization_test_sha256=8ef0383cab28c6df5e2b5705ddb1813dc259eb922fabd9ecb67a7748e33f6cda
c2_reauthorization_red_log=artifacts/tdd/native_characterization_c2_reauthorization_intentional_red_20260811.log
c2_reauthorization_red_log_sha256=6505a60c2b5901b85fc199b370cac801507876801e8888905b26eee2dd5fb511
c2_reauthorization_exit_progress_red_log=artifacts/tdd/native_characterization_c2_reauthorization_exit_progress_red_20260811.log
c2_reauthorization_exit_progress_red_log_sha256=a938adee3a744e28828c6efa6ee365e3a272d9eecaada1b836407a0a279483b3
c2_reauthorization_integrated_green_log=artifacts/tdd/native_characterization_c2_cleanup_reauthorization_integrated_green_20260811.log
c2_reauthorization_integrated_green_sha256=277a0945b258de079294c73c06621b390100a811a0b59558144610527afeb17c
c2_reauthorization_buffered_stdout_red_log=artifacts/tdd/native_characterization_c2_reauthorization_buffered_stdout_intentional_red_20260811.log
c2_reauthorization_buffered_stdout_red_sha256=163179b486a9b3ed58c043fcd7fae5bdd4cd687cfba406a85d511aa420724597
c2_reauthorization_buffered_stdout_green_log=artifacts/tdd/native_characterization_c2_reauthorization_buffered_stdout_focused_green_20260811.log
c2_reauthorization_buffered_stdout_green_sha256=421ed1bdb40c2f6a16b3b1d929a626608d3213316e584ffac75b95b3c97ee7c5
pre_full_focused_initial_status=red_stale_historical_fixture_contracts
pre_full_focused_initial_log=artifacts/tdd/native_characterization_c2_cleanup_pre_full_focused_regression_20260811.log
pre_full_focused_initial_log_sha256=17d4a30b68d489feaa0456d62b4bb65cced112fd5443155d2a459f2b07c058b0
historical_fixture_repair_scope=tests_only_no_production_contract_change
qualification_fixture_green_sha256=8cce6484d4dc5d6819d3314617abff5a30901f9b8f6543adc1cfdb7975e05021
evidence_finalization_fixture_green_sha256=529702849cf29e2f073c2dca40760ebe834e0a2b7869397870fbbbb62d6e4f99
workplan_current_historical_green_sha256=70ee07f1820cf75162198ff2905aaf8e34bd3fb45c1a6afed3889a3aa51200eb
pre_full_focused_status=green_151_plus_14_plus_13
pre_full_focused_log=artifacts/tdd/native_characterization_c2_cleanup_pre_full_focused_green_20260811.log
pre_full_focused_log_sha256=dbe13e4fb6909ebbeea98a3fd310f3f5a662dd0b247b02e285712d991c515853
post_cleanup_node_count_required=0
post_cleanup_relationship_count_required=0
cleanup_execution_status=verified_empty
cleanup_evidence_path=artifacts/native_characterization/c2_cleanup/c2-efb58c477f12adf6.json
cleanup_evidence_sha256=9e2738a037ce330f4c176633b2424a8065a30e544396a2f4cff5c70d17b7e83b
cleanup_evidence_payload_sha256=7cfbf833b872fca28ecd66aafd2bc9b81fe77dbe5992080b96aa70925b7cc62c
cleanup_pre_node_count=51
cleanup_pre_relationship_count=89
cleanup_post_node_count=0
cleanup_post_relationship_count=0
reauthorization_receipt_path=artifacts/native_characterization/c2_cleanup/c2-efb58c477f12adf6.reauthorization.json
reauthorization_receipt_sha256=9ba9bef91bc5cbf2b445edb7f0e53ba9c2f38f270dd10046689c38617ed10f79
reauthorized_state_sha256=c6aae8cfeda8f2eeec74b218455a7b2a1dcfc89099bab0a69367ab50c79e5671
post_cleanup_live_transition=reuse_existing_c2_only_gate
replacement_start_source_sequence=0
replacement_run_id_policy=fresh_c2_16hex
structured_output_second_failure_action=stop_and_assess_json_object
workplan_v1_1_modified=false
freeze_modified=false
new_recovery_framework_allowed=false
pre_stream_fix_full_regression_status=green_727_diagnostic_not_final_binding
pre_stream_fix_full_regression_path=artifacts/tdd/native_characterization_c2_cleanup_final_full_offline_regression_20260811.log
pre_stream_fix_full_regression_sha256=07abf574ad14fe3edb1e6586a2dd53670126fd25cef7fd46502839422ec6055a
post_stream_fix_focused_status=green_152_plus_14_plus_13
post_stream_fix_focused_path=artifacts/tdd/native_characterization_c2_cleanup_post_stream_fix_focused_green_20260811.log
post_stream_fix_focused_sha256=801be2c942818e621ce1a2994202c1726602349558c184fd8506ae1f79cda5ba
final_full_regression_path=artifacts/tdd/native_characterization_c2_cleanup_replacement_final_full_offline_regression_20260811.log
final_full_regression_sha256=cfc430724b952ac470abe9263521f1d5e22edfad26848cc9f93154b58dd0eb1f
full_offline_regression_after_parser_and_cleanup=green_728
second_failed_attempt_id=c2-723261287e32e182
second_failed_attempt_error=json.decoder.JSONDecodeError
second_failed_attempt_completed_episodes=10
second_failed_attempt_completed_blocks=0
second_failed_attempt_valid=false
second_failed_attempt_mergeable=false
second_failed_attempt_resume_allowed=false
second_failed_attempt_cleanup_authorized=false
second_failed_attempt_json_object_authorized=false
second_failed_attempt_report_path=artifacts/diagnostics/native_characterization_c2_second_structured_failure_20260811.json
second_failed_attempt_report_payload_sha256=131925c9dd853f2bd88774ad5692c74c5f9022493e1958b0f253949c0ff008f6
second_failed_attempt_report_sha256=df9f369e68a5b131b2f70d05e4e2e58a95eb86602a3e8fe30d0ef6f3bf218cf7
second_failed_attempt_state_sha256=c00dcf25e392a289803045545df6e549967a3ad3a3a17a5f9520f22a2626e69c
second_failed_attempt_failed_episode_trace_persisted=false
second_failed_attempt_same_completed_boundary_as_prior=true
structured_output_current_mode=json_schema
structured_output_automatic_mode_switch=false
measurement_correctness_repair_pending=true
current_live_grant_revoked=true
```
<!-- C2_MINIMAL_RECOVERY_POINTER_END -->

This remains bounded recovery history, not a new protocol or subsystem. Both
failed attempts and their reports remain immutable and contribute no C2 result.
The lightweight execution decision has since closed qualification work, the
second polluted namespace has been precisely cleaned and verified empty, and a
single derived `json_object` freeze was tried once. It completed seven episodes
before a Pydantic validation failure in edge resolution. The current pointer
revokes all experiment live authority and permits only the exact scoped cleanup
of that latest polluted namespace. No additional compatibility candidate, C0
rerun, retry, or fallback stack is allowed. A C2-only grant may be issued only
after fresh cleanup evidence verifies exact 0/0 post-counts.

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
> C1 初始 qualification 与 C0 已通过；首次 C2 attempt 在 10 个 episode 后因
> structured JSON 解码失败并永久失效。replacement
> `c2-723261287e32e182` 从 episode 0 重跑后又在相同完成边界失败，且失败
> episode telemetry 未持久化。全部 live grant 已撤销；当前只允许离线
> measurement-correctness RED/GREEN 与 `json_object` protocol-deviation 分析。
> 本文件不得启动 H0/M1/M2、replacement-004、cleanup、数据库管理或任何 live action。

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
