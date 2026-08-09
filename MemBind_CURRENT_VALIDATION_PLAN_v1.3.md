# MemBind 当前基础验证执行计划 v1.3

> **Protocol ID**: `current-validation-v1.3`  
> **文档地位**: 本文件是当前唯一可执行 overlay；v1.2 保留为历史协议。  
> **当前阶段**: `H0 - Host Stack Qualification`  
> **当前状态**: `h0_protocol_accepted_harness_not_implemented`  
> **当前 blocker 分类**: `late_discovered_pre_freeze_host_compatibility_failure`  
> **当前动作范围**: `h0_offline_tdd_and_harness_only`  
> **Live gate**: `live_h0_candidate_authorized=false`  
> **旧 smoke**: `v3_smoke_003_retired=true`  

本次修订是观察到 Q0 失败之后作出的**追溯性协议修正**，不是原 v1.2 的预注册
内容。它授权离线合同、manifest 和 harness 的 TDD 工作，但不授权 Q1 请求、模型
调用、embedding 调用、Neo4j workload 或远端服务修改。live Q1 需要单独的显式
machine-state gate。

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
status=h0_protocol_accepted_harness_not_implemented
current_blocker=late_discovered_pre_freeze_host_compatibility_failure
current_action_scope=h0_offline_tdd_and_harness_only
live_h0_candidate_authorized=false
v3_smoke_003_retired=true
```

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

## 6. H0 workload 与 semantic utility

H0-A 是 fail-fast engineering canary，不是可靠性置信区间：使用 calibration
`07741c45` 的 source sequence 0，在同一固定 seed 下执行 3 个 repeated logical
trials；它们不是 statistically independent samples。H0-B 完整执行该 history。
H0-C 执行其余 3 个 calibration histories；H0-B+C 合计要求 full history 4/4。

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
full_history_source_mapping: exact
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

当前只完成文档合同 RED 与协议同步，harness 尚未实现。因此
`live_h0_candidate_authorized=false`。任何入口都必须读取 `CURRENT_STATE.json` 并在
false 时 fail closed，且必须在发出模型/DB请求之前失败。

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

只允许：在已冻结的 registry 与可重放 split 上实现剩余 H0 离线 TDD
harness，包括 fail-closed state gate、split access enforcement、context budget、
payload/retry ledger、Q3 effective-schema injection、semantic utility 和 shared-base
未决 hash 解析；生成的测试与 artifact 不含 API key、Authorization header、
raw prompt、raw response 或环境 dump。`.env` 继续 ignored。

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
