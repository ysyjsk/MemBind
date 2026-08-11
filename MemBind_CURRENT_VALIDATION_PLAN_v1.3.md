# MemBind 当前基础验证执行计划 v1.3

<!-- NATIVE_CHARACTERIZATION_CURRENT_POINTER_START -->
```text
protocol_version=current-validation-v1.3
current_stage=NATIVE_CHARACTERIZATION
status=native_characterization_offline_only
current_blocker=c2_polluted_namespace_cleanup_pending
current_action_scope=native_characterization_offline_only
stage_progress.native_characterization=c0_c1_pass_c2_failed_attempt_invalid_cleanup_tdd_pending
instrumentation_contract_status=qualified
c1_aa_classification=clean_pass
c0_dry_run_passed=true
c0_dry_run_live_request_performed=false
c0_live_passed=true
authorized_live_actions=[]
live_h0_candidate_authorized=false
service_admin_authorized=false
native_characterization_live_authorized=false
next_allowed_action=implement_scoped_c2_cleanup_offline
```
<!-- NATIVE_CHARACTERIZATION_CURRENT_POINTER_END -->

```text
c2_minimal_recovery_contract=membind-validation/EXPERIMENT_PLAN.md#C2_MINIMAL_RECOVERY_POINTER
```

The failed C2 attempt is invalid and non-mergeable. The existing execution plan
is the single source for the exact-group cleanup and replacement-run procedure;
this summary deliberately does not duplicate that contract.

```text
HISTORICAL_SOLUTION_LANE_BELOW=true
```

> **当前 research-priority override**：当前科研执行以
> [`MemBind_NATIVE_GRAPHITI_CHARACTERIZATION_WORKPLAN_v1.1.md`](MemBind_NATIVE_GRAPHITI_CHARACTERIZATION_WORKPLAN_v1.1.md)
> 为准。Machine-searchable status: `current research-priority override`；protocol ID:
> `native-characterization-v1.1`。v1.0 的
> [`MemBind_NATIVE_GRAPHITI_CHARACTERIZATION_WORKPLAN_v1.0.md`](MemBind_NATIVE_GRAPHITI_CHARACTERIZATION_WORKPLAN_v1.0.md)
> 仅作不可变历史版本。
> Frozen entry status: `WORKPLAN_FREEZE=true`；
> `protocol_review_status=closed`；
> `next_allowed_work=C1_instrumentation_implementation`。
> C1 instrumentation 与 C0 已通过；首次 C2 attempt 在 10 个 episode 后因
> structured JSON 解码失败并永久失效。当前唯一恢复点是离线实现上面的单组
> scoped cleanup guard；不得直接续跑或扩展 recovery framework。
> 本文件是 frozen solution-validation lane 的历史 overlay，仅保存 H0/M1/M2
> 历史契约。不得恢复 H0、replacement-004 或 M2 formal
> work，除非 characterization verdict 后另行完成 TDD 状态转换和授权。

> **历史 solution-lane snapshot（以下“当前”仅指冻结时点，不授予权限）**
> **Protocol ID**: `current-validation-v1.3`  
> **文档地位**: 本文件是当前唯一可执行 overlay；v1.2 保留为历史协议。  
> **当前阶段**: `H0 - Host Stack Qualification`  
> **当前状态**: `h0_q1_b_live_only`
> **当前 blocker 分类**: `none`
> **当前动作范围**: `h0_q1_b_live_only`
> **Live gate**: `live_h0_candidate_authorized=true`
> **旧 smoke**: `v3_smoke_003_retired=true`  

本次修订是观察到 Q0 失败之后作出的**追溯性协议修正**，不是原 v1.2 的预注册
内容。Q1/H0-A 的 gate-order 修复、one-shot replacement 和 3/3 qualification 已完成；
随后 Q1/H0-B 在 readiness 之后、首个 workload 之前暴露 Graphiti nominal-client
兼容性缺陷。该缺陷的 one-shot R3 replacement 随后在首个 construction `/version`
readiness 处因 `vllm_unreachable` 中断，仍未进入任何 workload。R4 离线 TDD、
source-bound artifact、透明 decision、bind 和 exact live authorization 随后完成，
但其 replacement-002 在 source 1 的本地 Graphiti embedding interface contract 处
终止。002 已持久化 terminal checkpoint，其 live grant 已撤销；R5 artifact、透明非盲
repair decision、离线 bind 和独立 live authorization 均已完成。当时的 grant 曾仅允许
exact `h0-q1-b-20260810-replacement-003` 从空 checkpoint namespace 完整重跑 H0-B；
该 grant 和后续 replacement-004 均已消费或撤销，现行 lane 不授权 H0 live。旧
attempt 的调用、checkpoint、graph、history 与计数均不得作为候选性能证据、续跑或合并。

### 历史 H0-B recovery 快照（2026-08-10）

<!-- Maintainability: keep this machine-searchable block byte-equivalent in the
proposal, execution plan, and global memory; explanatory prose may differ. -->

H0-A 的有效 replacement 是一个固定 seed bounded canary，不是三个统计独立样本：

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
retry_indices=0,0,0
embedding_calls=0
database_calls=0
```

H0-B attempt `h0-q1-b-20260809-attempt-001` 的 construction、embedding、Neo4j
readiness 和 authorization recheck 均通过，但 workload evidence 全为零：

```text
h0_b_failed_attempt_id=h0-q1-b-20260809-attempt-001
checkpoint_index_sha256=fa6280ede4387775c719abd410478b5e1db358d840a10a69025c5a6cddd48896
classification=harness_compatibility_failure_not_candidate_result
readiness_qualified=true
logical_trial_count=0
http_attempt_count=0
embedding_workload_request_count=0
history_count=0
source_checkpoint_count=0
fresh_graph_count=0
old_attempt_immutable=true
old_attempt_resumable=false
old_and_new_evidence_mergeable=false
```

根因是注入 Graphiti 的对象必须满足 Pydantic `GraphitiClients` 的 nominal type；
修复仅补齐 `H0EmbeddingAdapter` 和 `H0ForbiddenCrossEncoder` 的基类，不改变模型、
prompt、schema、seed、数据或阈值：

```text
Graphiti nominal clients: EmbedderClient + CrossEncoderClient
preworkload_progress=corpus_ready,history_factory_ready,graph_construction_started,graph_construction_ready
artifact_set_id=v1_3_harness_r3
execution_harness_revision=3
index=artifacts/h0_manifest_sets/v1_3_harness_r3/resolved_manifest_index_v1_3_harness_r3.json
execution_source_count=32
replacement_attempt_id=h0-q1-b-20260809-replacement-001
revoke -> r3 TDD/artifact -> transparent decision -> bind offline -> exact one-shot replacement authorize -> 49 sources
connection/timeout/429/5xx -> durable checkpoint -> immediate stop_and_report
startup_monitoring=frequent; stable_monitoring=long_interval; program_output=detailed_segmented
mainline_gpt55_temporary_access=forbidden
```

R3 replacement 的基础设施中断与 R4 恢复绑定如下；本块描述当前恢复节点，前述
R2/R3 块保留为不可变历史：

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

以下 R4 live grant 已通过 dry-run 零写入验证后原子提交，随后被 002 的终态失败
消费并撤销；该块是历史授权事实，不是当前权限或实验结果：

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

replacement-002 的 post-workload harness failure、已撤销 live grant 与已生成 R5
artifact 构成已经完成的离线恢复节点：

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

上述块是授权前历史。R5 decision、bind 与独立 authorization 已分别经过 dry-run
零写入验证并提交；授权本身不是实验结果：

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

replacement-003 随后在 source 6 的并发 construction 请求中出现 vLLM
不可达。以下 stop fence 覆盖上一个已消费授权块的执行含义；它不改写尚待离线关闭的
machine state：

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

程序必须详细分段输出安全的中间计数、阶段和 artifact 绑定。启动初期由 operator/Codex
频繁检查能否进入首个 workload；服务和 checkpoint 流稳定后改用长监听间隔，最终统一
分析持久化文件。任何 connection、timeout、429 或 5xx 都先持久化当前 checkpoint，
然后立即停止并报告，禁止静默重跑。

## 0. 当前唯一结论

历史 blocker ID 仍为 `v3_smoke_002_m0_structured_output_failure`。它被重新归类为
`historical_negative_host_qualification_evidence`，而不是删除或改写：

```text
artifacts/environment/v3_actual_schema_compatibility_probe_20260809_005_fresh_restart.json
sha256=fd1b23026689008ce9a5976581b519c2a7d62fc5c2ea05eb0964f5387e10a041
```

该证据只证明 Q0 的 shared Graphiti-Qwen-vLLM structured-output path 在 M0 entity
extraction 阶段失败。M2 没有开始，Neo4j 与 embedding 没有被调用，因此它不是
MemBind correctness 或 performance 结果。

当前机器合同摘要：

```text
protocol_version=current-validation-v1.3
status=h0_b_post_workload_harness_failure_live_revoked
current_blocker=manifest_contract_failure
current_action_scope=h0_b_post_workload_harness_repair_offline_only
live_h0_candidate_authorized=false
v3_smoke_003_retired=true
```

### 0.1 Q1/H0-A gate-order invalidation 与透明修复

`h0-q1-a-20260809-attempt-001` 在绑定实现的技术检查下完成了 3/3 logical
trials：3 次 completion 均 HTTP 200、`finish_reason=stop`、JSON/Pydantic/semantic
utility 通过、retry=0，且 embedding/DB 调用均为 0。三次固定输入和 seed 的 response
hash 相同；它们不是统计独立样本。该观测只保留为 diagnostic evidence：

```text
attempt_status=invalidated_protocol_gate_order
protocol_qualified=false
candidate_selection_evidence_eligible=false
candidate_advance_authorized=false
automatic_rerun_authorized=false
checkpoint_index_sha256=127c81b39ccd705d7c67dc936e953992d5be97f4065fd56f3655db52d12ad309
```

失效原因与模型输出无关：绑定到该 attempt 的 H0 import chain 会在 state gate 之前
进入 Graphiti 顶层 `load_dotenv()`。后加 bootstrap 不能追溯性修复旧 checkpoint。
旧 checkpoint、报告原始解释 hash 和 `artifacts/h0/**` 必须原地保留，不删除、不移动、
不覆盖，也不得据此进入 H0-B。

科学协议仍为 `current-validation-v1.3`；修复后的执行冻结使用独立 harness revision：

```text
artifact_set_id=v1_3_harness_r2
execution_harness_revision=2
artifact_root=artifacts/h0_manifest_sets/v1_3_harness_r2/
index=artifacts/h0_manifest_sets/v1_3_harness_r2/resolved_manifest_index_v1_3_harness_r2.json
legacy_artifact_root=artifacts/h0/
legacy_tree_mutation_forbidden=true
```

截至 2026-08-09，本轮离线实现已完成但尚未执行 formal resolve/live：H0-A
completion、H0-B/C full-history runner、construction/embedding/Neo4j stage
readiness、fresh graph lifecycle、49/46/44 source checkpoint、H0-B terminal
validator、one-shot non-blind repair admission 及 A→B→C state progression 均已有
RED/GREEN。r2 额外绑定一个 `execution_source_bundle`，逐文件覆盖 31 个主线 H0
本地源文件；当前离线全回归为 `479/479 OK`。该状态不等于 r2 已生成，不授权任何
live 请求，也不构成 H0 结果。

```text
r2_generated_json_file_count: 11
r2_binding_count: 10
r2_execution_source_count: 31
r2_formal_generation_status: pending_after_final_regression
live_replacement_status: forbidden_until_separate_state_transition
```

在生成 r2 manifest 前，必须先通过 TDD 完成 H0-A→B terminal completion validator、
H0-B/C Graphiti/embedding/Neo4j runtime、readiness、resource cleanup 和全部 source
binding。否则后续代码变更会再次使 manifest 失效。之后才能持久化一个明确承认
`decision_result_blind=false` 的 deviation/repair decision，并只授权一次 Q1/H0-A
whole-stage replacement。候选顺序、Q1 spec、calibration input、semantic thresholds、
seed、trial count、request/retry policy 均不得改变；旧、新 3/3 不得合并为 6/6。

若 r2 replacement PASS，只允许新 attempt 的 3/3 进入 H0-B；若 FAIL，Q1 按原规则
失败并进入冻结的 Q2 顺序，禁止再次 protocol-repair rerun。任一连接、429 或 5xx
基础设施问题仍在 durable checkpoint 后立即停止并向 operator 汇报。

## 1. 研究边界与顶会 claim discipline

当前 Pilot 只回答 Graphiti/temporal-KG ingestion 上的三个问题：

1. serial construction 的 expensive state-independent work 占比是否足够大；
2. M2 在冻结 model-derived outputs 后能否保持 D0 的最终语义；
3. 在同一 qualified host stack 和同一 resource cap 下，M2 是否优于 serial 与
   strong tuned whole-update parallel baseline。

当前 Pilot 不支持跨 memory architecture 的一般化。正式合同为：

```text
paper_track_status=future_work_only
current_pilot_does_not_support_general_agent_memory_runtime_claims
cross_architecture_claims_forbidden=true
P1_authorized=false
P2_authorized=false
P3_authorized=false
P4_authorized=false
MemoryArena_role=trace_replay_workload
```

只有 V7 给出 GO，完成 abstraction audit，且至少第二个 architecture 通过薄 adapter
复用同一 scheduler/runtime core 后，才可声称 General Agent Memory Runtime。否则
论文 claim 必须收缩为 Graphiti/temporal-KG runtime。

## 2. 数据隔离与 exposure quarantine

旧 split 不覆盖、不删除：

```text
artifacts/dataset/frozen_split.json
sha256=b40acce61defe0c809636dc9964cbfa8591fafde5e6330b81ed0e8214bcd71f7
```

`c6853660` 原属于旧 evaluation，但已被多次用于 prompt、schema、ordering 和
structured-output 调试。因此：`c6853660 -> quarantined_regression_canary`; it is
never eligible for host candidate selection or formal held-out evaluation。

v1.3 使用：

```text
artifacts/dataset/frozen_split_v1_3.json
sha256=747946a8792422ea35e9d56b864efb1a137cb6eb8a8e16f97808fe86f938c091
generator=src/dataset_v1_3.py
generator_sha256=df7e1f9603699ab899af4a3fb24e4f0e6a432d2342f2d4c2f296cb98a8f1171e
h0_data_scope=calibration_only
evaluation_split_access=false
canary_selection_eligible=false
candidate_selection_metric=first_passing_only
```

四个 calibration ID 保持不变。v1.3 evaluation 通过原始 SHA256(question_id) 规则，
只排除因开发暴露而 quarantine 的 `c6853660`，并加入下一个未观察 ID
`08e075c7`。该替换只由 exposure 与 ID hash 决定，不使用模型、质量或性能结果。
H0 不读取八个 v1.3 evaluation inputs。quarantine canary 只保留历史 Q0 证据；本轮
不执行新的 canary 请求。
独立 generator 验证原始数据与 immutable legacy split 的 SHA256，再重放
上述 exposure-only 替换。旧 `src/dataset.py` 保持不变，避免伪造旧
manifest 记录的 generator hash。

## 3. 单线状态机

```text
H0   offline contracts/harness
     -> separate live-Q1 gate
     -> calibration-only Host Stack Qualification
     -> freeze first qualified shared stack
V2-R qualified correctness-oracle requalification
V3-R one full M0 capture -> one full M2 read-only replay
V4   U0/D0 representativeness + native characterization + DELTA freeze
V5   quality-feasible M1/M2 concurrency tuning
V6   formal Pilot: 24 correctness + 48 live performance = 72 runs
V7   mechanism and GO/INCONCLUSIVE/NO-GO verdict
STOP
```

V1 与旧 V2 的 artifacts 保留为历史完成证据。由于 host/prompt/schema/decoding
identity 可能改变，旧 V2 oracle 不可进入 V2-R。禁止跳阶段。

## 4. H0 candidate registry

Q0 是 immutable historical negative control，不再运行。当前 Q1-Q3 文件是内容
寻址的 `candidate_delta_spec`，共同引用一个 `shared_host_base_spec`；它们
不是完整 runnable manifest。只有 `candidate_diff_from_previous` 列出的
host-request 字段可变。client、prompt、schema、retry implementation、backend、
HTTP pool、model revision、Graphiti commit、embedding 或 DB 行为变化都会使
candidate 无效。live gate 前必须将 base spec 的所有 `unresolved_fields`
解析为精确 hash，再生成完整、内容寻址的 resolved candidate manifest。

<!-- H0_CANDIDATE_REGISTRY_START -->

```text
candidate_order: [Q1, Q2, Q3]
registry_immutable: true
content_address_algorithm: sha256
selection_rule: first_passing
performance_observed_for_selection: false
later_candidates_after_pass: forbidden
candidate_artifact_kind: delta_spec_not_runnable_manifest
shared_host_base_spec: membind-validation/configs/h0/shared_host_base_v1_3.json
shared_host_base_spec_sha256: 8738531ca312657e9e9954a8cfb858be30409283af495c7a40bb16fdf4430ebe
resolved_candidate_manifest_required_before_live: true

Q0_manifest: membind-validation/configs/h0/Q0_historical.json
Q0_manifest_sha256: e8fb9e78a2ec6fa1930a4429e0ef8b89a86c3b1e0dad9e9bae2cd08f9e4aca50

Q1_delta_spec: membind-validation/configs/h0/Q1.json
Q1_delta_spec_sha256: e9646d53b24f25f594bb2de6367838297787da2cf6f7970fa240dfd1df5684ee
Q1_diff_from_Q0: completion_budget_policy_only
q0_to_q1_causal_ab_claim: forbidden
Q1_requested_max_tokens: 16384
Q1_structured_output_mode: json_schema
Q1_temperature: 0.0
Q1_top_p: 1.0

Q2_delta_spec: membind-validation/configs/h0/Q2.json
Q2_delta_spec_sha256: 5a8096419ec05eee799a78454e4d0f7ae34d1d43de774cd0fc2706939852f0ad
Q2_diff_from_Q1: temperature_top_p_top_k_min_p_only
Q2_temperature: 0.7
Q2_top_p: 0.8
Q2_top_k: 20
Q2_min_p: 0

Q3_delta_spec: membind-validation/configs/h0/Q3.json
Q3_delta_spec_sha256: 736c64114a1aec5bcb5ef76d461aac609c711ea0ce010bb037f11c61ba58bdd2
Q3_diff_from_Q2: structured_output_mode_only
Q3_structured_output_mode: json_object
Q3_activation: explicit_after_recorded_Q2_failure
Q3_automatic_fallback: forbidden

requested_request_payload_sha256: required_per_attempt
observed_request_payload_sha256: required_per_attempt
top_k_observed_in_payload: required_for_Q2_Q3
min_p_observed_in_payload: required_for_Q2_Q3
not_sent_by_client_contract: candidate_ineligible_not_a_hidden_variant

schema_upstream_sha256: required
schema_effective_sha256: required
schema_injected_sha256: required_for_Q3
json_object_schema_injection_source: effective_shim_schema
schema_injected_sha256_must_equal_schema_effective_sha256: true
```

<!-- H0_CANDIDATE_REGISTRY_END -->

Q1 的 16384 是 pinned Graphiti `OpenAIGenericClient` constructor default，不是
Qwen 对所有请求的总体输出建议，也不保证每个 prompt 都获得完整 16K。Q2 的采样点
来自 Qwen non-thinking 建议，但没有证据证明它是历史截断的根因修复。Q2/Q3 只有在
`top_k=20` 与 `min_p=0` 被观测为实际 request payload 字段时才 eligible；无法传递
不能被静默降格成另一个未注册候选。

Q0 是在旧 qualification wrapper 下产生的历史失败，Q1 则使用 v1.3
共享 H0 harness；因此 Q0→Q1 不是严格的 causal A/B。
`completion_budget_policy_only` 仅限定 host-request configuration projection，
不授权将 Q1 PASS 单因归于 16K budget。

Q3 是显式候选，不是 Graphiti 自动 fallback。当前代码只在 `json_schema`
response format 上应用 `[0]` shim，而 upstream `json_object` 注入原始 Pydantic
schema；因此 Q3 在 effective-shim injection 的 RED/GREEN 合同实现前不可运行。

所有候选维持 `vLLM 0.26.0` 与 server-side `backend=auto` 的已证明配置。v1.3 只可
写 `configured_backend=auto`、`request_selected_backend=unobserved`，不得由
`response_format=json_schema` 推断 xgrammar/guidance/outlines。更换 backend、context
policy、vLLM 版本或增加 Q4 都需要新协议版本。

## 5. Completion budget、trial、retry 与 infra policy

<!-- H0_BUDGET_RETRY_CONTRACT_START -->

```text
requested_max_tokens=16384
safety_margin_tokens=32
effective_max_tokens=max(0,min(requested_max_tokens,context_limit-prompt_tokens-safety_margin_tokens))

required_attempt_fields:
  requested_max_tokens
  effective_max_tokens
  context_limit
  prompt_tokens
  safety_margin_tokens

effective_max_tokens<=0: context_budget_insufficient
finish_reason=length: candidate_failure
JSON_parse_failure: candidate_failure
Pydantic_validation_failure: candidate_failure

logical_trial_id: one public Graphiti generate_response invocation
http_attempt_id: every underlying HTTP completion request
retry_index: zero-based within one logical trial
trial_seed_policy: fixed_20260806
logical_trial_seed: 20260806
logical_trials_statistically_independent: false
server_observed_seed: required in sanitized request metadata
retry_same_logical_trial: true
retry_is_not_independent_trial: true

candidate_induced_retry: candidate_failure_even_if_later_attempt_parses
infrastructure_failure: requires pre/post health evidence unrelated to candidate
infrastructure_failure_recovery: whole_stage_rerun
no_single_method_or_candidate_selective_rerun: true
protocol_repair_rerun: one_shot_explicit_deviation_only
protocol_repair_decision_result_blind: false
protocol_repair_old_attempt_qualification_reusable: false
protocol_repair_old_and_new_trial_counts_mergeable: false
```

<!-- H0_BUDGET_RETRY_CONTRACT_END -->

40960 context 对已知 32757-token historical prompt 最多只留下 8171 tokens（含 32
token safety），所以不得声称“每个请求恢复完整 16K”。harness 必须先计算并记录
requested/effective budget；不得裁剪 prompt/history。若 context budget 不足，按
candidate failure 记录，而不是临时扩大 context、缩短输入或增加未注册 retry。

每个失败 attempt 都保留。明确外部 infra failure 后，整个受影响 H0 stage 用新
attempt ID 重跑，不能保留部分成功 trial 后只补失败项。若 Q1 失败来自
Q2/Q3 无法改变的 shared invariant（例如 context feasibility 或 unresolved base
manifest），后续候选记为 `not_executed_shared_invariant_failure`，禁止发送必然
无效的请求。所有候选失败或被该 shared failure 阻断后的唯一终态为
`H0_BLOCKED_ALL_PREREGISTERED_CANDIDATES_FAILED`；不得继续试 Q4。

长阶段采用细粒度、内容寻址的安全 checkpoint。checkpoint 保留完整的 sanitized
ledger、计数、hash、failure code 和进度，但不保存 raw prompt/response、凭证或环境
dump。它用于防止进程或 vLLM 中断时丢失诊断证据，不改变上一段的公平性规则：部分
成功不能与重启后的补跑拼成 PASS，受影响 stage 必须使用新 attempt ID 整体重跑。
程序应在每个 checkpoint 输出明确的阶段、候选、segment、累计调用/attempt 数、artifact
路径与 SHA256；连接失败立即停止并向 operator 汇报，不自动尝试后续候选。

```text
checkpoint_granularity_H0_A: per_logical_trial
checkpoint_granularity_H0_B_C: per_source_sequence
partial_evidence_preserved_on_interruption: true
partial_qualification_reusable_after_infra_failure: false
whole_affected_stage_rerun_with_new_attempt_id: true
vllm_connectivity_failure: stop_and_report
automatic_candidate_advance_after_connectivity_failure: false
checkpoint_payload: sanitized_detailed_ledger_counts_hashes_failure_codes
```

## 6. H0 workload 与 semantic utility

H0-A 是 fail-fast engineering canary，不是可靠性置信区间：使用 calibration
`07741c45` 的 source sequence 0，在同一固定 seed 下执行 3 个 repeated logical
trials；它们不是 statistically independent samples。H0-B 完整执行该 history。
H0-C 执行其余 3 个 calibration histories；H0-B+C 合计要求 full history 4/4。

为避免 harness 在 live 后再解释 workload，执行单元、资源边界、graph isolation 与
semantic evidence 在首个 candidate 请求前冻结如下：

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

H0-A 的三个 fresh client 只隔离 client-local state；logical trials 仍写入同一个
stage ledger。H0-B/C 不允许复用或只清理部分 graph。H0-C 任一 history 被确认遭遇
基础设施中断后，三个 histories 都必须在恢复后以同一个新 stage attempt ID 重跑，
不得把旧 attempt 中的完整 history 与新 attempt 拼接成 qualification PASS。

每个 logical call 必须在首个 HTTP attempt 同时满足 HTTP 200、非 length、JSON
parse、Pydantic validation 与 manifest invariants。任何 candidate-induced retry、
OOM、context overflow 或 schema failure 都使该候选失败。3/3 只能表述为 bounded
qualification canary，不能推导 99.x% production reliability。

在任何 candidate live call 前，必须离线建立并 hash
`h0_semantic_guardrail_manifest_v1_3.json`。它只读取 calibration raw inputs，由
人工审计的小型 expected-nonempty canary 和确定性 schema invariants 构成；不得在看
candidate 输出后改动。

<!-- H0_SEMANTIC_UTILITY_START -->

```text
semantic_utility_data_scope: calibration_only
semantic_utility_invariants_data_scope: calibration_only
semantic_utility_invariants_sha256: required_before_live_not_yet_available
semantic_utility_invariants_frozen_before_candidate_execution: true
candidate_outputs_used_to_set_invariants: false
semantic_invariant_manifest: content_addressed_and_frozen_before_candidate_execution
expected_nonempty_call_ids: required_from_blinded_calibration_audit
minimum_entity_count_by_call: required_from_blinded_calibration_audit
minimum_distinct_normalized_entity_name_count_by_call: required_from_blinded_calibration_audit
expected_episode_indices_by_call: exactly_single_zero
json_parse_success: true
pydantic_validation_success: true
nonempty_expected_extractions_must_remain_nonempty: true
entity_names_must_be_nonblank: true
episode_indices_must_equal: [0]
duplicate_normalized_entity_names: forbidden
constant_or_schema_default_only_output: forbidden
valid_empty_or_degenerate_output: qualification_failure
evaluation_data_used_for_semantic_utility: false

full_history_episode_coverage: 100_percent
full_history_source_mapping: exact_episodic_set_and_resolved_edge_attribution
canonical_graph_must_be_nonempty: true
calibration_evidence_recall_at_10_must_be_nonzero_per_history: true
```

<!-- H0_SEMANTIC_UTILITY_END -->

结构合法但空/常量/default-only 的输出必须失败。这个 gate 只拒绝明显退化，不宣称
建立 paper-level quality superiority；正式 U0/D0 representativeness 与 retrieval
guardrail 在 V4 完成。第一个通过 H0-A/B/C 和 semantic utility 的候选立即冻结，
后续 candidate 不运行、不测性能。

## 7. H0 离线 TDD gate

live Q1 前必须依次完成：

1. RED: candidate registry/order/diff、split isolation、context budget、trial/retry、
   payload observation、Q3 effective schema、semantic utility、first-pass stop、all-fail
   stop、manifest immutability、secret-free artifact、state gate；
2. 持久化 RED 命令、失败数与 SHA256；
3. 最小实现，不顺带重写整个 upstream client；
4. focused GREEN；
5. `.venv/bin/python3 -m unittest discover -s tests -q` 全量 GREEN；
6. 审阅 candidate、split、semantic-invariant manifest 及 source hashes；
7. 单独修改 machine state 为 `h0_q1_a_live_only`。

历史 gate-order-invalid H0-A attempt 及其 r2 修复证据继续不可变保留；有效 H0-A
replacement 已 3/3 qualified。当前恢复点是 H0-B pre-workload harness failure：必须按
`revoke -> r3 TDD/artifact -> transparent decision -> bind offline -> exact one-shot
replacement authorize -> 49 sources` 完成专用恢复。任何入口都必须在读取显式项目
credentials、构造 service client、创建 workload checkpoint 或发出模型/DB请求之前
读取并通过 exact `CURRENT_STATE.json` gate。

## 8. Qualified manifest 与 V2-R oracle

H0 PASS 后写 immutable `qualified_host_stack_v1_3.json`，绑定 Graphiti commit、
construction model revision、vLLM version/launch hash、configured backend declaration、
structured mode、effective schema/prompt hashes、sampling、seed、completion policy、
retry、compatibility adapter、HTTP config、embedding fingerprint 和 split hash。

```text
oracle_namespace_content_address_algorithm: sha256
oracle_namespace_binds_qualified_host_manifest: true
h0_formal_oracle_writes: forbidden
old_v2_v3_oracle_reuse: forbidden
correctness_lane: qualified_capture_read_only_replay
performance_lane: live_model_no_response_replay
```

namespace 至少绑定：protocol version、qualified host manifest SHA256、Graphiti commit、
effective prompt/schema hashes、deterministic adapter hash、embedding deployment
fingerprint。禁止 mutable `latest` 和跨 namespace lookup。H0 只能保存 qualification
outputs，不得写 formal oracle。

V2-R 在 fresh Neo4j 上执行 qualified M0 capture -> qualified M0 read-only replay，
要求零 live fallback、相同 namespace 和 graph/retrieval parity。V3-R 使用一个新的
calibration smoke ID 执行 full M0 capture -> M2 replay；旧 `v3_smoke_003` 永久退休，
新 namespace 从 `v3r_smoke_001` 开始。

## 9. U0/D0 与 quality-feasible tuning

<!-- BASELINE_FAIRNESS_START -->

```text
U0=Upstream-Qualified-Graphiti-Serial
D0=Deterministic-Graphiti-Serial

U0_definition: qualified_Graphiti_serial_plus_only_shared_provider_compatibility_adapter
D0_definition: U0_plus_declared_deterministic_candidate_ordering_adapters

concurrency_candidates: [1, 2, 4, 8]
selection_data_scope: calibration_only
quality_feasibility_checked_before_performance_selection: true
selection_objective: minimum_calibration_median_makespan
selection_tie_break: smallest_concurrency
best_tuned_m1: required
best_tuned_m2: required
formal_fixed_cap_label: iso-cap comparison
```

<!-- BASELINE_FAIRNESS_END -->

U0/D0 在四个 calibration histories 比较完成率、episode/source coverage、canonical
entity/edge F1、Evidence Recall@10、LLM calls/tokens 与 latency。预注册 guardrail：
D0 macro Evidence Recall@10 不低于 U0 超过 1 percentage point，canonical entity 与
edge F1 均至少 0.95，LLM call count 相同，total-token ratio 在 `[0.95,1.05]`。未通过
时仍保留并报告 U0/D0；D0 仍叫 Deterministic-Graphiti-Serial，但 external-validity
claim 收缩。

M1/M2 各自在相同四个 calibration histories、相同 arrival trace 上扫描 C。只有
完成、无 protocol failure、满足 correctness/retrieval/exactly-once guardrail 的点才
quality-feasible；在可行点中选择 calibration median makespan 最小者，数值完全相同
时选择较小 C。若 M1 没有并发可行点，报告完整 speed-quality frontier，不把语义
错误但更快的点称为 Best-Tuned。

正式同时报告 M0/M1-C8/M2-C8 的 `iso-cap comparison` 与 Best-Tuned M1/M2。
`iso-cap` 只表示相同 global cap，不代表瞬时 GPU/CPU/DB resource usage 相同；必须
报告实际 utilization/queue/work volume。

## 10. Live performance work-equivalence

<!-- WORK_VOLUME_GUARDRAIL_START -->

```text
reference_method: D0
work_volume_lower_bound: 0.95
work_volume_upper_bound: 1.05
llm_call_ratio: exact_ledger_match_required
input_token_ratio: two_sided_0.95_to_1.05
output_token_ratio: two_sided_0.95_to_1.05
embedding_call_ratio: two_sided_0.95_to_1.05
bounds_frozen_on_calibration_before_evaluation: true
below_lower_bound: performance_confounded
above_upper_bound: performance_confounded
```

<!-- WORK_VOLUME_GUARDRAIL_END -->

每个 method/instance/repeat 按 prompt_name 保存 logical call、HTTP attempt、retry、
input/output tokens、embedding items、finish reason、prompt/response hash ledger。
`live_response_divergence_rate` 只作 descriptive diagnostic，不设事后“显著”阈值。

越界结果仍报告真实 E2E，但只能称 `descriptive_end_to_end_outcome`，不能称同工作量
scheduling speedup。不得用 token-normalized latency 替代原始 E2E latency。网络、
server queue、batching 与 GPU utilization 是 treatment path，不做 RTT subtraction。

## 11. V4-V7 Pilot 合同

V4 只用四个 calibration histories：先做 U0/D0 guardrail，再做 D0 phase
characterization 与 DELTA freeze。不得用 M1/M2 或 evaluation 结果反向改 DELTA。

V5 执行上一节的 quality-feasible concurrency tuning，并在结束后冻结 best M1/M2、
arrival trace、method manifests 和 selection artifact。当前 runtime 仍存在 64-run、
缺 M1 correctness replay、global shuffle 的 implementation debt；必须在 V6 RED/GREEN
gate 中修复为 72-run correctness-first blocked plan，不能把文档当作代码已实现证据。

V6 correctness lane：

```text
8 x (M0 capture + M1 read-only replay + M2 read-only replay) = 24 runs
```

V6 performance lane：

```text
8 x 3 methods x 2 repeats = 48 live runs
```

先完成全部 correctness；M2 必须 8/8 parity 且零 oracle miss/fallback 才开放 live
performance。performance 用 `(question_id,repeat)` blocked counterbalanced order；明确
infra failure 保留旧 block 并用新 ID 重跑整个三方法 block。treatment failure 作为
方法结果，不可洗成 infra。

V7 只输出 GO / INCONCLUSIVE / NO-GO、effect size、95% CI、per-workload 分布、失败
案例和限制。8 instances x 2 repeats 是内部 Pilot，不支持 paper-level P99 claim。

## 12. Paper roadmap（非执行输入）

V7 GO 后才可另立协议：P1 abstraction audit；P2 second memory architecture；P3
MemoryArena physics trace-replay freshness experiment 与受其启发的 live workload；
P4 stronger runtime baselines/load sweep。Agent Memory 先例应准确描述为 capture
per-session timing traces 后按 5 s schedule replay，不得写成其已做 live serving。

参考工作 `Agent Memory: Characterization and System Implications of Stateful Long-Horizon Workloads`, arXiv preprint, 2026。公开 arXiv metadata 尚不能独立证明
任何 IISWC venue/acceptance 状态，因此当前引用不得使用未公开核实的 venue。

## 13. 当前唯一允许动作

只允许：完成 H0-B harness recovery 的 revoke、r3 TDD/artifact、透明 non-blind decision、
offline bind，以及对 exact replacement attempt 的一次性授权。旧 H0-B attempt 不可续跑、
覆盖或与 replacement 的 49-source evidence 合并。生成的测试与 artifact 不含 API key、
Authorization header、raw prompt、raw response 或环境 dump。`.env` 继续 ignored。

禁止：

- 调用 Q1/Q2/Q3 或 embedding endpoint；
- 启动 Neo4j workload；
- 运行旧 `v3_smoke_003`；
- 访问 evaluation inputs 做 host tuning；
- 修改远端服务、backend、context 或模型；
- 执行 V2-R/V3-R/V4/V5/V6/V7/P1-P4；
- 读取、引用或复用 `membind-validation/gpt55_temporary/**`。

远端仍只允许 `ssh zju-liuyi '<forced-command>'` 的
`status/list/read/tail/follow`，范围严格为 `/home/lhx/liuyi/**`，且均为只读。当前
任务不需要也不授权 SSH。
