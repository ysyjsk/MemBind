# MemBind 基础验证实验规范

> **当前 research-priority override**：研究主线已切换到
> [`MemBind_NATIVE_GRAPHITI_CHARACTERIZATION_WORKPLAN_v1.0.md`](MemBind_NATIVE_GRAPHITI_CHARACTERIZATION_WORKPLAN_v1.0.md)。
> Machine-searchable status: `current research-priority override`.
> 本文件及 v1.3 overlay 现在保留为 frozen solution-validation / exploratory
> prototype material；M1/M2/MemBind、H0 recovery 和 replacement-004 不属于当前
> characterization 执行。任何 live characterization 之前必须先通过离线 TDD
> 状态转换关闭旧 solution-lane grant；历史 artifact 不改写、不合并。

> 文档状态：Pilot Protocol v1.1（已合并 Characterization / Fairness Addendum）
> 当前执行覆盖：`MemBind_CURRENT_VALIDATION_PLAN_v1.3.md`（CURRENT VALIDATION PLAN v1.3，protocol `current-validation-v1.3`）是当前唯一执行 overlay；v1.2 原文只作历史协议。与旧 Phase/characterization 顺序冲突时，以 v1.3 的 H0→V2-R→V7 单线状态机为准。
> 当前恢复点：`H0 / Q1 H0-B replacement-003 live-only`；002 的 live grant 已撤销，
> R5 artifact、透明非盲 repair decision、离线 bind 与独立 authorization 已完成，
> 状态为 `h0_q1_b_live_only`，动作范围为 `h0_q1_b_live_only`，当前 blocker 为
> `none`，`live_h0_candidate_authorized=true`，
> `v3_smoke_003_retired=true`。
> 历史 ID `v3_smoke_002_m0_structured_output_failure` 与
> `artifacts/environment/v3_actual_schema_compatibility_probe_20260809_005_fresh_restart.json`
> 保留为 `historical_negative_host_qualification_evidence`，不改写 raw failure。
> 目标：以单一 backend、单一数据集、单一 backbone，直接判断 Semantic Late Binding 是否值得继续研究。  
> 本协议只验证核心 idea，不验证跨 backend 通用性、复杂调度、容错或完整线上部署。
> 升级原则：v1.1 补充系统 characterization、公平性和环境噪声控制；除本文明确标为“替换”的条款外，v1.0 的 correctness、冻结数据划分、模型与 Graphiti 版本、Evidence Fence 以及 Go/No-Go 阈值保持不变。

---

## 当前 H0-B recovery ledger（2026-08-10）

<!-- Maintainability: this machine-searchable block is mirrored in the current
plan, execution plan, and global memory. H0-B attempt-001 is harness evidence,
not candidate/model performance evidence. -->

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

上一个代码块记录已消费并撤销的 R4 历史授权。以下是已经完成的 R5 离线恢复节点；
R5 生成本身当时并不构成 repair bind 或 live authorization：

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

002 已进入 workload，但其 6 次 construction 调用、4 次 embedding 请求、source-0
checkpoint、graph 与 history 均不得用于候选 qualification，也不得复制或合并到 003。
R5 decision、bind 与独立 authorization 已分别 dry-run 后提交；授权本身不是实验结果：

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

replacement-003 随后在 source 6 的并发 construction 请求中出现 vLLM 不可达。
以下 stop fence 覆盖上一个已消费授权块的执行含义；Q1 不能判负，Q2 不能启动：

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

H0-A replacement 的 3/3 是固定 seed bounded canary，不是独立统计样本。H0-B
attempt-001 在 readiness 后、首个 workload 前失败，因而 0 请求证据只能支持 harness
兼容性修复。程序输出保留详细安全分段；启动时频繁检查，稳定后延长监听间隔。
连接、timeout、429 或 5xx 必须先 checkpoint，再立即停止并报告。

## 1. 唯一研究问题

本实验只回答：

> 对 Graphiti 的连续 episode 构建，将“从固定 evidence prefix 生成逻辑 entity/relation delta”的阶段提前并行执行，并将“绑定到当前实体/边、执行失效与提交”的阶段延迟到最新已提交图状态，能否在保持原生串行语义的同时，降低 episode 的 arrival-to-publish latency？

不回答：

- MemBind 是否适用于所有 Agent Memory；
- conflict-aware 并行是否有效；
- visibility-frontier scheduler 是否有效；
- fault tolerance 是否完整；
- MemBind 是否优于 MemForest、A-Mem、EverMemOS；
- 是否适合生产环境。

---

## 2. 核心假设

### H1：性能假设

在相同硬件、模型、prompt、数据库和并发额度下，MemBind 相比 M0
`Deterministic-Graphiti-Serial`：

- 降低每个 episode 的 P95 `arrival_to_publish_ms`；
- 降低每条 history 的总构建 makespan；
- 不增加超过 5% 的 LLM 输入/输出 token。

### H2：语义假设

MemBind 在所有 episode 提交完成后，应与 M0
`Deterministic-Graphiti-Serial` 得到相同的 canonical semantic graph：

- episode 无丢失、无重复；
- canonical entity 集合一致；
- canonical relation/fact 集合一致；
- temporal validity / invalidation 一致；
- retrieval 结果与原生串行一致或近似一致；
- LongMemEval-S evidence-session recall 不下降。

### H3a：Execution equivalence

粗粒度并发执行完整 `add_episode()` 可能改变 state-dependent Graphiti operation
的输入或 trajectory。M1 在冻结 M0 model oracle 时出现
`execution_path_divergence`，只证明执行路径分叉；不得自动写成最终 graph 错误。

### H3b：Practical sufficiency

粗粒度并发可能无法同时提供与 M0 相同的最终 semantic guarantee 和与 M2 相当的
live performance。只有完整 M1 replay 后的 graph/invalidation/retrieval divergence
才能证明 final semantic result 改变；性能结论只来自 live performance lane。

如果粗粒度并发与 M0 在所有评测实例上完全一致且性能不差于 MemBind，则本实验
不能证明 Late Binding 的必要性。

---

## 3. 固定实验栈

### 3.1 Backend

- 系统：Graphiti
- 版本：`v0.29.3`
- Git commit：`021d3a5`
- 必须从该 commit 建立实验分支，禁止使用 `main`。
- 必须保留该版本已有的 ingestion 优化，不允许回退到旧版 Graphiti。

### 3.2 图数据库

- 数据库：Neo4j Community 5.26
- 部署：实验主机本机直接运行，不使用 Docker；记录二进制版本、配置摘要和服务
  地址到 `artifacts/environment/manifest.json`。
- 每个 `(question_id, method, repeat)` 使用独立空数据库。
- 禁止跨实验复用图状态。

### 3.3 Construction backbone

- 模型：`Qwen/Qwen3-32B-FP8`
- Hugging Face revision：`6e2312b85c2ae9a31f629f24493b79d8b02eab1a`
- 推理框架：vLLM `v0.26.0`（用户于 2026-08-07 明确批准替代原协议的 `v0.23.0`；本次执行对 M0/M1/M2 统一冻结该版本）
- 模式：`enable_thinking=false`
- 结构化输出、sampling 与 completion policy：由 v1.3 H0 的首个通过候选冻结；
  Q1/Q2 使用 `json_schema`，Q3 是显式 `json_object` compatibility candidate。
- 所有方法必须使用完全相同的 Graphiti prompt、schema 和模型参数。

历史 Q0 解码参数（失败证据；不是 v1.3 最终 host config）：

```yaml
temperature: 0.0
top_p: 1.0
max_tokens: 2048
seed: 20260806
```

禁止临时改为 `temperature=0.01` 或增加未注册 candidate。H0 只按内容寻址的
shared-base + Q1→Q2→Q3 delta specs 执行 first-passing qualification，选择只用
calibration、只判 qualification，不看 M2 或 performance。当前 specs 不是
runnable manifests；live 前必须解析 client/prompt/schema/HTTP/retry 等所有
共享字段的 hash。H0 PASS 后，M0/M1/M2 的 exact shared host manifest 冻结；
任何变化都需要新 protocol version。Q0 与 Q1 使用不同世代的 qualification
wrapper，不构成 causal A/B；Q1-Q3 与 Q0 均保持固定 `seed=20260806`。

### 3.4 Embedding model

- 模型：`Qwen/Qwen3-Embedding-0.6B`
- dtype：BF16（已由部署进程、模型配置与
  `artifacts/environment/embedding_model_fingerprint.json` 交叉核实并冻结）。
- 禁止不同方法使用不同 embedding model 或维度。
- Correctness lane：M0 按 canonical single-item embedding input capture exact vector；M1/M2 对同一输入只做只读 replay。Embedding cache miss 立即失败，禁止 live fallback。
- correctness embedding namespace 至少包含 served model ID、endpoint-reported
  revision 或 operator-supplied immutable deployment fingerprint、dimension、dtype、
  pooling、normalization/instruction/input-transform configuration；身份来源写入
  `artifacts/environment/embedding_model_fingerprint.json`，不能猜测 checkpoint
  revision，也不能仅以 served alias/URL 冒充 immutable fingerprint。
- item key 绑定 exact single-item UTF-8 input bytes；单 item 是 semantic key，API
  batch composition 不是，因此 `create(["x"])` 与 `create_batch(["x"])` 共用记录。
- Performance lane：M0/M1/M2 使用相同 live embedding server、dimension 和 run 内 exact-text cache policy；每个 run 冷启动应用级 embedding cache，不要求跨 run bitwise-identical vectors。

### V2 pilot boundary (historical completed evidence)

该 v1.2 有界 integration 已完成并仅作历史 evidence；v1.3 因 qualified host
identity 将变化，不得将该 oracle/cache 直接复用为 V2-R。当时的边界为：

```text
M0 capture -> M0 read-only replay
```

入口固定为：

```text
.venv/bin/python src/replay_driver.py v2-oracle-integration \
  --attempt v2_oracle_integration_001
```

该 pilot 使用一个固定单 episode integration instance，只验证 LLM + embedding
oracle 的 capture/replay、零 live fallback、fresh Neo4j cleanup、cross-encoder
audit 和 cache 不可变性；它不替代 V3 的 M0 -> M2，也不运行 M1。任何 manifest
runtime 字段无法从实际远端 argv、启动日志或部署配置证明时必须写入
`unresolved_fields` 并在 live gate 前 fail closed，不能用本地模板或官方参考值
冒充实际 dtype/pooling/normalization 配置。

### 3.5 硬件

当前用户批准的固定拓扑替代原草案的双专业卡假设：

- construction 与 embedding 均使用远端、内网 OpenAI-compatible vLLM endpoint；
- construction endpoint 固定为 `http://10.87.5.247:8000/v1/`，vLLM 0.26.0，
  `max_model_len=40960`，实验总 construction LLM 并发额度为 8；
- embedding endpoint 固定为 `http://10.87.5.247:8001/v1`，dimension=1024；
- Graphiti、replay driver 和本机直接运行的 Neo4j 使用同一实验主机；本机 RTX 3090
  是用户批准的本地 GPU，不再要求原草案的双专业卡，当前远端模型请求的
  计时资源对 M0/M1/M2 完全相同；
- endpoint、路由和服务配置在全部方法间保持相同，运行期间不得动态迁移。

必须记录：

```text
GPU 型号与显存
NVIDIA driver
CUDA
Python
PyTorch
vLLM
Graphiti commit
Neo4j binary version + config hash
模型 revision
CPU 型号
内存容量
```

---

## 4. 数据集选择

### 4.1 唯一数据集

使用：

```text
longmemeval_s_cleaned.json
```

只保留：

```text
question_type == "knowledge-update"
question_id 不以 "_abs" 结尾
```

选择该子集的原因：

- history session 按时间排序；
- 同一主题或实体会出现旧信息与新信息；
- 能自然触发 entity binding、relation update 和 temporal invalidation；
- 最终问题可检查更新后的记忆是否正确；
- 每个实例包含约数十个 session，足以形成连续构建队列。

### 4.2 冻结划分与 v1.3 exposure quarantine

不要人工挑选样本。

执行以下确定性规则：

```python
eligible = [
    x for x in data
    if x["question_type"] == "knowledge-update"
    and not x["question_id"].endswith("_abs")
]
eligible.sort(
    key=lambda x: sha256(x["question_id"].encode()).hexdigest()
)
calibration = eligible[:4]
evaluation = eligible[4:12]
```

上述规则生成的 v1.2 split 原样保留：

- calibration：4 个实例；
- evaluation：8 个实例；
- 其余实例在本 pilot 中不使用。

必须将选中的 `question_id`、原始数据文件 SHA256 和筛选脚本版本写入：

```text
artifacts/dataset/frozen_split.json
```

v1.3 发现 `c6853660` 已被多轮 prompt/schema/ordering/structured-output 调试暴露，
因此不能继续把它称为 held-out evaluation。不得覆盖旧 split；新增：

```text
artifacts/dataset/frozen_split_v1_3.json
h0_data_scope=calibration_only
evaluation_split_access=false
canary_selection_eligible=false
candidate_selection_metric=first_passing_only
generator_script=src/dataset_v1_3.py
```

v1.3 保持原四个 calibration IDs，将 `c6853660` 放入
compatibility/development quarantine，并按相同 SHA256(question_id) 顺序加入下一个
未观察 ID `08e075c7`，保持八个 evaluation instances。该规则只看 exposure 与 ID
hash，不看模型、quality、latency 或 MemBind 结果。H0 只能读取 calibration；
quarantined canary 只保留 Q0 历史证据，不参与 candidate selection。
新 split 由独立的 v1.3 generator 校验原始数据 hash 与 immutable legacy split
hash，再只按 exposure quarantine 和原 SHA256(question_id) 顺序派生。旧
`src/dataset.py` 不改动，以保留 v1.2 manifest 记录的脚本 hash。

### 4.3 Session 输入规则

对每个实例：

1. 使用 `haystack_sessions` 的完整 session；
2. 按数据中的时间顺序执行；
3. 不删除 filler session；
4. 不只保留 evidence session；
5. 每个 LongMemEval instance 使用独立 `group_id`；
6. session 内多轮消息连接为一个 Graphiti episode；
7. episode body 必须保留 role 标记；
8. episode reference time 使用对应 `haystack_dates`；
9. 不允许将未来 session 内容加入当前 Compile prompt。

Episode 文本格式固定为：

```text
[USER] <turn 1 user content>
[ASSISTANT] <turn 1 assistant content>
[USER] <turn 2 user content>
[ASSISTANT] <turn 2 assistant content>
...
```

---

## 5. 三个实验条件

所有条件使用相同输入、相同模型服务、相同数据库配置和相同最大 LLM 并发额度。

### M0：Deterministic-Graphiti-Serial

`M0` 是为兼容已有代码、run ID 和 artifact 保留的内部 method ID；公开报告名为
`Deterministic-Graphiti-Serial`。它使用 Graphiti v0.29.3，并与 M1/M2 一样安装
公共 deterministic candidate-ordering adapter；不声称等同 untouched upstream。

串行参考执行：

```text
for episode in source_order:
    await graphiti.add_episode(episode)
```

约束：

- 下一 episode 可以按 open-loop trace 到达并进入本地等待队列；
- 但 Graphiti 每次只执行一个完整 `add_episode()`；
- 必须等待当前 episode 完成后才执行下一个；
- 这是正确性 reference。

### M1：WholeUpdate-Parallel-C8

粗粒度并发：

```text
最多同时执行 8 个原生完整 add_episode()
```

约束：

- 不拆分 Graphiti 内部阶段；
- 不强制完成顺序等于 source order；
- 不增加额外修复；
- 每个任务仍使用原生 `add_episode()`；
- 用于检验“直接并发完整 update 是否已经足够”。

### M2：MemBind-GO-C8

最小 MemBind 原型：

```text
Evidence Fence
    → Parallel Semantic Compile
    → Latest-State Bind
    → Global Source-Ordered Commit
```

本 pilot 中只实现全局顺序 Bind/Commit，不实现 conflict-aware commit。

#### 5.3.1 Evidence Fence

episode 到达时立即写入只读 source log，并分配：

```text
stream_id
source_sequence
source_hash
reference_time
```

Compile 只能读取：

- 当前 episode；
- source sequence 小于当前 episode 的原始 episode 文本；
- Graphiti 原生 extraction 所需的 schema 和 prompt；
- 不得读取当前 mutable entity/edge 图状态。

#### 5.3.2 Semantic Compile

Compile 输出必须是未绑定物理 UUID 的逻辑 artifact：

```text
candidate entities
candidate relations/facts
candidate temporal fields
source episode mapping
source_sequence
prompt_hash
response_hash
```

允许并行：

- 最多 8 个 in-flight Compile LLM requests；
- 同一实例的多个 episode 可同时 Compile；
- 不同实例不在同一次 run 中混合。

禁止在 Compile 中执行：

- 查询现有 canonical entity UUID；
- 查询现有 edge UUID；
- 决定旧 edge 是否失效；
- 更新 entity summary；
- 写入正式图。

#### 5.3.3 Latest-State Bind

按 `source_sequence` 从小到大处理已完成 Compile artifact。

Bind 阶段必须在当前最新 committed Graphiti state 上执行原生逻辑：

```text
entity candidate retrieval
entity resolution
edge candidate retrieval
edge resolution
old-edge invalidation
state-dependent attribute/summary update
```

不得复用 Compile 启动时的 graph candidate result。

#### 5.3.4 Global Source-Ordered Commit

- episode `i` 的 Bind/Commit 必须在 episode `i-1` 提交后开始；
- episode `i+1` 即使 Compile 先完成，也不得越过 episode `i` 发布；
- 一个 episode 对应的 episode/entity/edge/invalidation 更新完成后，才记录 `publish_time`；
- 数据库写入失败时，本 pilot 直接判定该 run 失败，不做复杂恢复。

---

## 6. 两条独立评测链

不能用预热 LLM response cache 测性能，因为 MemBind 的主要收益正来自重叠昂贵 Compile 调用。实验必须严格拆成 correctness lane 与 performance lane。

### 6.1 Correctness lane：确定性 artifact replay

目的：冻结实际参与当前路径的 model-derived outputs，隔离调度语义，判断 M1/M2 是否与 M0 等价。model-oracle replay = LLM response + embedding vector。

步骤：

1. 对每个 evaluation instance 运行一次 M0，capture 所有完整 prompt、raw/parsed response、token usage，以及每个 canonical single-item embedding input 的 exact vector；
2. 建立只读 LLM response cache 和只读 embedding vector cache；
3. 对同一个 M0 oracle 分别运行 M1 read-only replay 与 M2 read-only replay；
4. cache hit 必须逐元素复用 captured output；任何 unexpected prompt 或 embedding
   input 都立即停止且禁止 live fallback；M1 记
   `completed_with_divergence`/`execution_path_divergence`，M2 记 correctness failure；
5. replay 的 live LLM calls 和 live embedding calls 必须均为 0，不得 fallback；
6. Neo4j graph state、candidate retrieval、resolution、invalidation 和 DB commit 不得 replay，必须在 M1/M2 各自的 fresh graph 上真实执行；
7. 静态/trace audit 若证明 frozen construction/retrieval path 未调用 cross encoder，记录 `not_invoked`；若实际调用，则把它纳入同一 model oracle；
8. Correctness lane 不用于报告性能。

Cache key：

```text
sha256(
    protocol_version
    + qualified_host_manifest_sha256
    model_revision
    + decoding_config
    + structured_output_schema
    + system_prompt
    + user_prompt
)
```

v1.3 correctness oracle namespace 必须内容寻址，并至少绑定 protocol、qualified
host manifest、Graphiti commit、effective prompt/schema hashes、deterministic
adapter hash 与 embedding deployment fingerprint。H0 禁止写 formal oracle；旧 V2/V3
cache 只作历史 artifact，禁止跨 namespace lookup 或 mutable `latest` alias。

Embedding key：

```text
sha256(
    served embedding model ID
    + endpoint-reported revision OR operator-supplied immutable deployment fingerprint
    + embedding dimension
    + dtype/pooling/normalization/instruction/input-transform configuration
    + exact single-item input bytes
)
```

M1 oracle miss 证明的是 state-dependent execution trajectory 已分叉，不等于已证明
最终 graph divergence；必须保存 first divergent source、oracle 类型、此前 hit count、
graph-state digest 和可取得的 prompt/candidate diff。M1 完整命中 oracle 后才比较
canonical graph/invalidation/retrieval。M2 oracle miss 阻断 formal performance。

Correctness lane 共：

```text
8 M0 capture + 8 M1 read-only replay + 8 M2 read-only replay
= 24 correctness runs
```

### 6.2 Performance lane：live model、禁用 response cache

目的：测量真实 Compile 并发带来的端到端收益。

规则：

1. M0、M1、M2 均真实调用同一个 live model server；
2. 禁用应用级 prompt/response cache；
3. 采用 hot engine + cold cross-run prefix state：每个 measured run 前验证并重置 vLLM prefix cache，run 内允许自然 prefix reuse；
4. 每个 measured run 从空 embedding cache 开始，run 内仅允许 exact-text reuse；
5. 每个 evaluation instance、每种方法重复 2 次；
6. 性能结果仅来自该 lane；
7. 每个 measured run 使用 V4 frozen minimal lifecycle，不得引用历史 characterization lifecycle 来恢复
   100/20 network probe 或高频 telemetry campaign。

---

## 7. Open-loop 到达协议

### 7.1 校准 arrival interval

先在 4 个 calibration instances 上运行 M0
`Deterministic-Graphiti-Serial`。

对所有成功 episode 计算：

```text
native_episode_service_ms
```

定义：

```text
DELTA_MS = median(native_episode_service_ms)
```

将 `DELTA_MS` 四舍五入到最近的 100 ms，并冻结到：

```text
artifacts/calibration/arrival_interval.json
```

禁止根据 M1 或 M2 结果重新选择 DELTA。

### 7.2 Evaluation replay

对每个 evaluation instance：

```text
episode 0 在 t=0 到达
episode 1 在 t=DELTA_MS 到达
episode 2 在 t=2*DELTA_MS 到达
...
```

open-loop 含义：

- driver 按绝对时间提交 episode；
- 不等待前一个 episode 完成；
- 若系统来不及处理，episode 进入该方法自己的等待队列；
- 三个方法使用相同 arrival timestamps。

Performance lane 中，每个方法、每个实例重复 2 次：

```text
8 instances × 3 methods × 2 repeats = 48 live runs
```

加上 correctness lane 的 24 runs，正式 evaluation 共 72 runs。

v1.1 明确将 performance lane 的 `global shuffle` 条款替换为
`blocked randomization`：每个 `block = (question_id, repeat)` 包含 M0、M1、M2，
用 seed `20260806` 在六种排列上均衡轮转。Correctness capture 必须仍先于对应 replay。

每个 measured run 使用 CURRENT VALIDATION v1.3 V4 frozen minimal lifecycle；
不得从历史 characterization lifecycle 重新引入 100/20 network probe 或高频 telemetry campaign。
正式数据 prompt 不得在 prefix-cache reset 后用于 warm-up。

---

## 8. 时间定义

所有时间使用单机 `time.monotonic_ns()`。

每个 episode 必须记录：

```text
arrival_time
queue_enter_time
compile_start_time
compile_end_time
bind_start_time
bind_end_time
commit_start_time
commit_end_time
publish_time
```

派生指标：

```text
arrival_to_publish_ms = publish_time - arrival_time
queue_wait_ms = first_work_start - arrival_time
compile_ms = compile_end_time - compile_start_time
bind_commit_ms = publish_time - bind_start_time
```

每个 instance：

```text
makespan_ms = final_publish_time - first_arrival_time
drain_ms = final_publish_time - final_arrival_time
```

M0 和 M1 若没有显式 Compile/Bind 边界，至少记录：

```text
add_episode_start
add_episode_end
publish_time
```

---

## 9. 主指标

### 9.1 主性能指标

第一主指标：

```text
P95 arrival_to_publish_ms
```

统计单元：LongMemEval instance，而不是单个 episode。

做法：

1. 每个 `(instance, method, repeat)` 内计算 episode-level P95；
2. 对 2 次 repeat 取均值，同时保留两次原始值；
3. 在 8 个 instance 上做配对比较。

第二主指标：

```text
instance makespan_ms
```

### 9.2 主正确性指标

```text
canonical_graph_parity
```

M2 相对 M0 必须逐 instance 比较。

### 9.3 次要指标

```text
P50 arrival_to_publish_ms
P99 arrival_to_publish_ms（仅 descriptive trace metric，不作正式 tail claim）
drain_ms
construction throughput
LLM input tokens
LLM output tokens
LLM call count
embedding call count
DB query count
DB write count
```

### 9.4 M1 必要性指标

```text
whole_update_parallel_divergence_rate
```

用于判断粗粒度并发是否改变最终结果。

---

## 10. Canonical Graph 比较

Graphiti内部 UUID、创建时间和数据库返回顺序不得直接用于比较。

### 10.1 Canonical entity

每个 entity 转换为：

```json
{
  "group_id": "...",
  "name": "normalized lowercase name",
  "labels": ["sorted", "labels"],
  "summary": "whitespace-normalized summary",
  "attributes": {"sorted": "semantic attributes only"}
}
```

排除：

```text
uuid
created_at
updated_at
database internal id
embedding binary/value
```

### 10.2 Canonical relation/edge

每条 relation 转换为：

```json
{
  "source_entity_key": "...",
  "target_entity_key": "...",
  "relation_type": "...",
  "fact": "whitespace-normalized fact",
  "valid_at": "normalized timestamp or null",
  "invalid_at": "normalized timestamp or null",
  "expired_at": "normalized timestamp or null",
  "attributes": {"sorted": "semantic attributes only"},
  "source_episode_sequence": 0
}
```

### 10.3 比较结果

每个实例输出：

```text
entity_exact_match
edge_exact_match
entity_set_precision/recall/F1
edge_set_precision/recall/F1
canonical_graph_hash
```

`canonical_graph_parity=true` 当且仅当：

```text
entity_exact_match == true
and edge_exact_match == true
and episode count matches
and source episode mapping matches
```

如果 summary 字段因模型非确定性无法严格一致，必须先检查 prompt cache 是否命中；不得直接从 parity 定义中删除 summary。

---

## 11. Retrieval Guardrail

构建完成后，对 LongMemEval 原始 question 执行 Graphiti 的固定检索路径。

固定：

```text
top_k = 10
search config = Graphiti v0.29.3 默认 hybrid edge search
query text = 原始 question
```

每个返回结果必须保留 source episode ID。

计算：

```text
Evidence Recall@5
Evidence Recall@10
retrieved episode set overlap with M0
rank-biased overlap with M0
```

Gold evidence 使用 LongMemEval 提供的：

```text
answer_session_ids
```

本 pilot 不引入额外 LLM judge；最终 answer accuracy 不作为 Go/No-Go 主指标，避免 judge calibration 干扰核心 runtime 验证。

---

## 12. 统计方法

### 12.1 性能比较

主比较：

```text
M2 vs M0
```

辅助比较：

```text
M1 vs M0
M2 vs M1
```

对每个 instance 使用配对值。

报告：

```text
geometric mean speedup
median speedup
95% cluster bootstrap CI
```

Bootstrap：

```text
resampling unit = question_id
bootstrap samples = 10,000
seed = 20260806
```

### 12.2 正确性比较

正确性不做均值容忍：

```text
M2 canonical_graph_parity 必须为 8/8
M1 canonical_graph_parity 逐 instance 报告，来源必须是同一 M0 model oracle 的只读 replay
```

---

## 13. Go / No-Go 判据

### GO

只有同时满足以下条件，才继续开发 predicate validation、selective repair、conflict-aware commit 和第二 backend：

1. `M2 vs M0` 的 instance-level geometric mean makespan speedup ≥ 1.5×；
2. `M2 vs M0` 的 P95 `arrival_to_publish_ms` 至少下降 30%；
3. P95 speedup 的 95% bootstrap CI 下界 > 1.0×；
4. M2 canonical graph parity = 8/8；
5. M2 Evidence Recall@10 不低于 M0 超过 1 个百分点；
6. M2 LLM 总 token 相对 M0 增幅 ≤ 5%；
7. M2 没有 episode 丢失、重复发布或 source-order 越界；
8. M1 的必要性证据至少满足以下之一：
   - 在冻结的 M0 model oracle 下，至少 1 个 evaluation instance 出现 canonical graph divergence；
   - 在冻结的 M0 model oracle 下，至少 1 个 evaluation instance 出现 retrieval evidence recall 下降；
   - M1 live performance 未满足本节第 1–3 条中至少一条，而 M2 满足全部第 1–3 条。

第 8 条用于证明 Late Binding 相对“直接并发完整 update”的必要性。M1 completion/source-order 仅为 diagnostic，不能单独作为 semantic failure 或 necessity evidence。
M1 `execution_path_divergence` 单独支持 H3a，但不自动满足第 8 条；若因 oracle
miss 无法跑到最终状态，必须报告
`final_semantic_parity = not_evaluable_due_to_oracle_miss`，不能把它伪装成
canonical graph divergence。

### INCONCLUSIVE

出现以下任意情况则判定为 inconclusive，修复实验平台后重跑：

- structured output parse success < 99.5%；
- infrastructure/protocol-invalid failure run 比例 > 5%；treatment failure 不得归入 infrastructure failure rate，也不得用平台 INCONCLUSIVE 洗掉；
- response cache 在相同 prompt 上返回不同内容；
- 数据库状态未完全隔离；
- GPU 服务发生 OOM；
- 模型、prompt、Graphiti commit 在运行期间变化；
- 8 个评测实例中少于 7 个完成全部 2 次 live 重复。

### NO-GO / 收缩方向

以下任一情况成立，应停止扩展通用 MemBind：

1. M2 canonical parity 无法达到 100%；
2. M2 speedup < 1.2×；
3. M1 与 M0 完全一致且性能不差于 M2；
4. Compile 实际无法与 graph-state binding 分离，必须读取最新实体/边状态；
5. M2 需要明显增加 LLM 调用或 token 才能维持质量。

若 M2 有明显加速但只在 Graphiti 成立，应将工作收缩为 Graphiti/temporal-KG ingestion 优化，而不是宣称通用 Agent Memory runtime。

---

## 14. 实现目录

必须使用以下结构：

```text
membind-validation/
├── README.md
├── pyproject.toml
├── uv.lock
├── configs/
│   ├── base.yaml
│   ├── native_serial.yaml
│   ├── whole_parallel_c8.yaml
│   └── membind_go_c8.yaml
├── src/
│   ├── dataset.py
│   ├── replay_driver.py
│   ├── graphiti_native.py
│   ├── graphiti_membind.py
│   ├── semantic_compile.py
│   ├── latest_state_bind.py
│   ├── response_cache.py
│   ├── tracing.py
│   ├── canonicalize_graph.py
│   ├── retrieval_eval.py
│   └── statistics.py
├── tests/
│   ├── test_split_freeze.py
│   ├── test_evidence_fence.py
│   ├── test_no_future_evidence.py
│   ├── test_source_order_commit.py
│   ├── test_exactly_once_publish.py
│   ├── test_prompt_cache.py
│   └── test_graph_canonicalization.py
└── artifacts/
    ├── environment/
    ├── dataset/
    ├── calibration/
    ├── traces/
    ├── graphs/
    ├── retrieval/
    ├── statistics/
    └── final/
```

---

## 15. 必须生成的 artifact

### 环境

```text
artifacts/environment/manifest.json
artifacts/environment/pip_freeze.txt
artifacts/environment/nvidia_smi.txt
artifacts/environment/docker_images.json
```

### 数据

```text
artifacts/dataset/source_sha256.txt
artifacts/dataset/frozen_split.json
```

### 校准

```text
artifacts/calibration/native_episode_latency.parquet
artifacts/calibration/arrival_interval.json
```

### 每次 run

```text
artifacts/traces/<run_id>.jsonl
artifacts/graphs/<run_id>.canonical.json
artifacts/retrieval/<run_id>.json
```

### 最终

```text
artifacts/final/run_manifest.parquet
artifacts/final/episode_metrics.parquet
artifacts/final/instance_metrics.parquet
artifacts/final/graph_parity.csv
artifacts/final/retrieval_metrics.csv
artifacts/final/statistical_summary.json
artifacts/final/figure_p95_latency.pdf
artifacts/final/figure_makespan.pdf
artifacts/final/figure_parity.pdf
artifacts/final/VALIDATION_REPORT.md
```

---

## 16. 执行顺序

当前执行严格由 CURRENT VALIDATION PLAN v1.3 的单线状态机决定；旧 Phase 0→6 与
v1.2 V1→V7 只保留为历史背景。H0 只先授权 offline contracts/harness；live Q1 必须
经过单独 machine-state gate。

```text
H0 Host Stack Qualification（offline TDD -> separate live gate -> first passing freeze）
→ V2-R Correctness oracle requalification
→ V3-R Full M0/M2 correctness smoke
→ V4 U0/D0 representativeness + calibration + minimal profiling
→ V5 Quality-feasible M1/M2 concurrency tuning
→ V6 Formal evaluation (24 correctness + 48 performance = 72 runs)
→ V7 Analysis + validation verdict
→ STOP
```

禁止跳阶段。每个代码或 instrumentation 变更都必须先红测、再实现、再定向
转绿、最后全量回归；每个失败 attempt 保留并使用新 run_id 替代。
历史 v1.2 阶段标签 `V1 Correctness nondeterminism closure` 仅用于对齐已保留
artifact，不是 v1.3 的当前恢复点。

---

## 17. 禁止事项

Agent 不得：

- 改写 Graphiti extraction prompt 以适配 MemBind；
- 给 MemBind 使用更大的并发额度；
- 给 M0 使用更慢的模型或不同数据库；
- 从 LongMemEval history 中删除 filler session；
- 根据结果手工挑选 evaluation instances；
- 用未来 episode 构建当前 Compile artifact；
- 在 Compile 阶段读取当前 graph candidate/entity UUID；
- 跳过 canonical parity，只比较 QA；
- 因 summary 不一致而事后删除 summary 比较；
- 将失败 run 静默排除；
- 在看到 evaluation 结果后调整 `DELTA_MS`；
- 将本 pilot 结果直接宣称为跨 Agent Memory 通用性证据。

---

## 18. 最终报告只回答四个问题

`VALIDATION_REPORT.md` 必须按以下顺序回答：

1. **是否保持 deterministic serial reference 语义？**  
   M2 correctness lane 的 canonical graph parity、retrieval parity、`unexpected_prompt` 和 episode exactly-once。

2. **是否明显加速？**  
   P95 arrival-to-publish、makespan、drain time 与置信区间。

3. **直接并发完整 update 是否 execution-equivalent 且 practically sufficient？**  
   M1 oracle miss 只记 execution-path divergence；最终 graph/retrieval 差异和 live
   性能分别报告。若无差异且性能不差，明确说明 Late Binding 的必要性未被证明。

4. **是否值得继续？**  
   严格根据 Go/No-Go 条件给出结论，不得使用主观措辞替代指标。

---

## 19. 当前执行范围边界

未来机制已移到 `FUTURE_WORK.md`，不属于 CURRENT VALIDATION PLAN v1.3 的输入。
当前 Agent 在 V7 报告后停止，不读取或执行 future-work 文件。

当前 Pilot 结果不得声称 General Agent Memory Runtime。P1 architecture abstraction
audit、P2 second host、P3 MemoryArena timing-trace replay/live extension、P4 paper-level
baseline/load matrix 均为 `authorized=false`，只在 V7 GO 后由新协议决定。

---

## 20. 本次执行拓扑与 TDD 覆盖

本次执行采用远程模型服务、本机 Graphiti 和本机 Neo4j 的固定拓扑：

- construction endpoint：http://10.87.5.247:8000/v1/，served model 为 qwen3-32b-fp8（2026-08-06 主机重启后 `/v1/models` 返回的精确 ID，底层 checkpoint 仍按本实验 manifest 记录）；
- embedding endpoint：http://10.87.5.247:8001/v1，served model 为 qwen3-embedding-0.6b；
- 两个 endpoint 使用同一个 API key，密钥只放在实验目录未跟踪的 .env 文件；
- Graphiti、replay driver、Neo4j Community 5.26 在本机运行，不使用 Docker；
- M0、M1、M2 必须使用相同的远程 endpoint、模型、解码参数、网络路径和并发上限；
- 内部 method ID `M0` 的公开名称固定为 `Deterministic-Graphiti-Serial`，不声称 untouched upstream semantics；
- 所有计时在本机使用 time.monotonic_ns()，不能使用模型服务器时间。
- 历史 Q0 请求预算为 `2048 -> 8192`，并已在 public path 上失败；它只保留为
  `historical_negative_host_qualification_evidence`。v1.3 Q1-Q3 的
  `requested_max_tokens=16384` 来自 pinned Graphiti constructor default；每次实际
  budget 固定按
  `min(requested_max_tokens, context_limit-prompt_tokens-32)` 计算并同时记录
  requested/effective 值。禁止裁剪输入、无限增大 budget 或临时增加 retry；
- 本实验每次 extraction 只包含一个 current episode，因此三个方法和 correctness cache hash 共用的 JSON schema 将 `episode_indices` 严格约束为单元素 `[0]`；Graphiti prompt 与事实字段不变，此约束修复了 vLLM 在无界整数数组中持续输出 `0,1,2,...` 直至 length 截断的问题；
- 历史 1-token context probe 只作 Q0 诊断。H0 harness 必须在每个 attempt 中显式
  记录 prompt tokens、context limit、安全余量和 effective budget；不得把 probe 或
  retry 计为独立 logical trial；candidate-induced retry 即 qualification failure。
  H0-A 的三次 repeated logical trials 共用固定 `seed=20260806`，不得称为
  statistically independent samples；
- H0 长阶段采用内容寻址的细粒度安全 checkpoint：H0-A 在每个 logical trial 完成后
  checkpoint，H0-B/H0-C 在每个 `source_sequence` 完成后 checkpoint。每个 checkpoint
  输出阶段、候选、segment、累计 logical-call/HTTP-attempt/retry 计数、sanitized detailed
  ledger、hash、failure code、artifact 路径与 SHA256；禁止保存 raw prompt/response、
  Authorization/API key 或环境 dump；
- checkpoint 只用于保留中断前的诊断证据。若 pre/post health evidence 将故障分类为
  infrastructure failure，部分 evidence 必须保留，但不得与恢复后的补跑拼接为
  qualification PASS；整个受影响 H0 stage 必须使用新 attempt ID 重跑。vLLM 连接失败
  必须立即停止并向 operator 汇报，禁止静默重试或自动推进到 Q2/Q3；
- H0-A 的执行单元固定为公开 `extract_nodes` direct call：三个 repeated trials 各用
  fresh client、共享一个 stage ledger，且不得调用 Neo4j 或 embedding。H0-B/C 每个
  history 必须使用经断言为空的新 graph，不得增加 LLM warmup；nonempty 定义为
  `entity_count > 0`，完整 source mapping 同时核对 episodic set 与 resolved-edge
  attribution，Evidence Recall@10 只统计最多 10 条 RRF edges 中依次出现的前 10 个
  unique session IDs。H0-C 任一基础设施中断要求三个 histories 用新 attempt ID 整体
  重跑，旧 attempt 仅保留诊断证据；
- Q1/H0-A attempt `h0-q1-a-20260809-attempt-001` 的三次固定 seed 技术检查均成功，
  但因绑定实现可能在 state gate 前进入 Graphiti 顶层 `load_dotenv()`，整体状态固定为
  `invalidated_protocol_gate_order`。旧 checkpoint 只作诊断，不能算 H0-A PASS、不能
  选择 candidate、不能推进 H0-B，也不能自动重跑；
- 修复不改变科学协议或 Q1：`current-validation-v1.3`、候选顺序、spec、input、threshold、
  seed/trial、request/retry 均保持不变。旧 `artifacts/h0/**` 原地不可变；完整 H0-A/B/C
  runtime 先经离线 TDD 冻结，再生成 `artifacts/h0_manifest_sets/v1_3_harness_r2/`。
  replacement 必须由披露旧 3/3 观测的 one-shot deviation 单独授权，使用新 attempt ID
  整阶段重跑，旧、新 trial 永不合并；
- 完整 `smoke06` 的 32757-token prompt 证明 40960 context 在 32-token safety 后最多
  提供 8171 completion tokens，不能声称所有 request 都恢复完整 16K；budget
  insufficiency 必须显式失败，禁止通过裁剪 history 或改写 prompt 绕过；
- 2026-08-07 用户将远端 `max_model_len` 恢复为 40960，并明确批准本次协议统一使用 vLLM 0.26.0 替代原定的 0.23.0；代码、`.env`、配置和 gate 期望值均冻结为 0.26.0，历史 blocker 与失败 attempt 继续保留并由新的成功 gate 标记为 resolved；
- Graphiti edge RRF 的 Neo4j BM25 / cosine 源查询统一采用 `logical_content_ascending_before_top_k`：在外层数据库 `LIMIT` 前，对相同 score 按 fact、relation、valid/invalid temporal fields 和 source/target name 排序，禁止 UUID 进入 secondary key，从而稳定送入最终 RRF top-K 的 ranked inputs。Neo4j full-text procedure 会在此外层排序前执行自身的内部 limit，因此 procedure cutoff 恰有并列项仍是必须由下一次 correctness smoke 验证的 residual risk，不得宣称已被理论消除；
- Graphiti node dedupe 的 Neo4j cosine 候选查询统一采用 `logical_node_content_ascending_before_top_k`：在每个 extracted entity 的 15-candidate `LIMIT` 前，对相同 score 按不含 UUID 的 name、summary 和 labels 排序，再由已有的 prompt-level node canonicalization 分配 `candidate_id`；
- Graphiti 的 RRF 仍负责选择 edge-resolution top-K 候选集合；M0/M1/M2 在候选进入 LLM prompt 并获得连续 idx 前统一采用 `logical_content_ascending_after_top_k`，即按 fact、relation 和 temporal logical content 规范化已选集合，并让 score 随 edge 同步移动。该规则不改变 top-K membership 或 cutoff，只消除 fresh graph 的随机 UUID / Neo4j 物理返回顺序对完整 prompt hash 的影响，correctness 与 performance lane 均启用；
- Graphiti 的 node semantic search 仍负责选择并去重 node-resolution 候选集合；M0/M1/M2 在 Graphiti 分配 `candidate_id` 前统一采用 `logical_content_ascending_before_candidate_id`，按 prompt 可见的 name、labels、summary、attributes 建立不含 UUID 的逻辑顺序，prompt 与 `candidate_id -> node` 映射必须共用同一个排序后列表；
- correctness replay 的任何 cache miss 都立即停止且禁止 live fallback；M0 对照按
  `prompt_name + source_sequence + invocation ordinal`（可取得时再加 call-site
  identity）确定性对齐，禁止用“最近记录”猜测；把组件 hash、安全的请求诊断和
  对齐 diff 持久化到 `artifacts/unexpected_prompts/`，API key 不得进入 artifact；

正式实验前必须按以下测试驱动顺序执行：

1. 保留失效 attempt/checkpoint，完成 dotenv/telemetry bootstrap、撤权与 artifact
   disposition 的 RED/GREEN；
2. 先为 H0-A→B completion validator、H0-B/C runtime/readiness/lifecycle/cleanup、r2
   namespace immutability 和 one-shot admission 新增 RED，再做最小实现和 focused GREEN；
3. 完整回归 GREEN 后生成 `v1_3_harness_r2`，持久化透明 deviation/repair decision，
   再以独立 machine-state transition 只开放一次新 Q1/H0-A；文档更新不得调用服务；
4. 只有新 Q1/H0-A attempt 合格才依次执行 H0-B/H0-C；连接、429 或 5xx 在 durable
   checkpoint 后立即停止汇报，不自动推进；
5. H0 首个候选通过并 freeze 后，重新做 V2-R oracle 与 V3-R correctness；
6. V4 完成 U0/D0 guardrail 和 calibration，V5 完成 quality-feasible tuning；
7. 修复当前 code 中 stale 64-run/global-shuffle implementation，生成并冻结 72-run
   correctness-first blocked plan：先完成 24 correctness
   runs；只有 M2 8/8 且 oracle miss/fallback 为 0，才执行 48 live performance
   runs。M1 `completed_with_divergence` 是 treatment outcome，不阻断 performance。

任何 TDD gate 失败都必须停在当前阶段，不能提前跑正式 evaluation。

当前离线执行记录（2026-08-09）：第 2 项的 H0-A/B/C 执行链、三服务 readiness、
逐 source checkpoint、terminal validation、one-shot repair admission 与 A→B→C state
progression 已实现；r2 通过 `execution_source_bundle` 绑定 31 个主线本地源文件，首轮
完整回归 `479/479 OK`。正式 r2 写入、repair decision 写入、machine-state live grant
及任何模型/embedding/Neo4j workload 仍未执行，因此这里不报告 H0 PASS 或实验收益。

<!-- Maintainability: H0 checkpoint/failure semantics are normative in the current
v1.3 overlay; keep this proposal summary synchronized without copying implementation
details or weakening CURRENT_STATE.json live authorization. -->

历史 smoke/diagnostic 的完整失败链已移到
`membind-validation/artifacts/history/SMOKE_HISTORY.md`。这些 artifact 仍不可覆盖，
但历史记录不再决定当前待办；当前阶段、blocker 和唯一下一步只读取
`membind-validation/CURRENT_STATE.json`。

---

## 21. v1.1 Characterization、Fairness 与环境噪声控制（历史背景；非当前执行计划）

本节保留 v1.1 设计的可追溯背景。CURRENT VALIDATION PLAN v1.3 将当前执行改为
H0→V7，并明确禁止大规模 load sweep、复杂 telemetry 和额外 network campaign；
所以下列 v1.1 子节不得再被解释为当前 Agent 队列。只有 v1.3 明确重新授权的
§21.5 `C={1,2,4,8}` quality-feasible tuning、§21.7 U0/D0 guardrail 和 live work-volume
ledger 在对应 V4/V5 阶段生效；这些阶段目前仍被 H0 gate 禁止。

### 21.1 执行前置条件与停止规则

1. 当前及历史 smoke attempt 必须自然结束并保留所有 artifact；任何替代 attempt 使用新的 attempt/run ID。
2. correctness smoke PASS 之前，禁止加入 characterization instrumentation，也禁止启动 calibration、load sensitivity、concurrency sweep 或 formal lane。
3. 每一个新增埋点、网络 gate、cache lifecycle、blocked schedule 和统计派生量都必须遵循“先写红测试、确认失败、实现、转绿、全量回归”的 TDD 顺序。
4. characterization 只能解释机制，不能事后选择 instance、arrival、并发度、primary outcome 或 Go/No-Go 阈值。
5. instrumentation、cache、network、server、fairness 任一 gate 失败时，停止在当前阶段；不得以删除异常结果或只重跑一个方法来“修复” gate。

### 21.2 指标分层与统一记录合同

v1.0 的 primary outcomes 保持不变：P95 `arrival_to_publish_ms`、instance `makespan_ms`、canonical graph parity 和 Evidence Recall@10。P99 仅作 descriptive trace metric。

每个 run 必须记录以下 cost/fairness guardrails：

```text
llm_input_tokens, llm_output_tokens, llm_call_count
embedding_call_count, http_request_count
db_query_count, db_write_count
structured_retry_count, transport_retry_count
bytes_sent, bytes_received
```

`transport_retry_count` 在 performance lane 原则上必须为 0；冻结的 structured-output bounded retry 必须计入调用、token 和时间。HTTP 5xx、429、timeout、OOM、DB error 和 transport error 不得静默丢弃。

M0 native characterization 必须按 pinned Graphiti `v0.29.3 / 021d3a5` 的真实函数边界记录 phase span。逻辑 phase 至少包括：

```text
add_episode
source_context_prepare, node_extract, edge_extract
node_embedding, edge_embedding
node_candidate_search, node_resolve_llm
edge_candidate_search, edge_resolve_llm
invalidation, attribute_or_summary_update, db_publication
```

每个 span 至少包含：`run_id`、`question_id`、`method`、`episode_sequence`、`span_id`、`parent_span_id`、`phase`、`semantic_class`、`start_ns`、`end_ns`、`duration_ns`、`status`、`exception_class`。`semantic_class` 只能是 `compile_eligible`、`bind_state_dependent`、`commit` 或 `other`，并在 formal performance 前冻结到 `artifacts/characterization/phase_map.json`。

嵌套 span 禁止 duration double-count。报告同时给 inclusive duration 与
exclusive/interval-union duration（interval union，区间并集）；`F_compile`、
`F_bind`、`F_commit` 用区间并集除以 `T_add_episode`，并报告未分类 gap。

每个 construction LLM request 记录：`prompt_name`、完整 prompt hash、request id、client send/first-byte/done 时间、observed latency、input/output tokens、finish reason、structured/transport retry index、提交/完成时 inflight 数和 response hash。无法从 vLLM 取得 server-side 时间时不得虚构 compute time，只报告 client latency、run-level server metrics、GPU telemetry 与 network probes。

每个 episode 记录 DB/search 聚合：`previous_episode_lookup`、`entity_candidate_search`、`edge_candidate_search`、`write_publication` 的 query count 和 latency，以及 candidate counts、graph node/edge count before/after。M2 另记录 `compiled_ready_count`、`compile_inflight_count`、`bind_busy`、`source_frontier`、`arrival_queue_depth` 时间序列，并派生 ready-queue mean/P95/max、frontier stall 和 utilization。

### 21.3 远程网络、vLLM 状态与资源公平

E2E `arrival_to_publish_ms` 和 makespan 继续包含真实远程 API 路径；禁止 RTT subtraction。campaign 开始前，construction 与 embedding endpoint 各执行 100 次不含 inference 的 lightweight `/models` probe，使用实验相同 NIC、路由和 proxy 环境，保存 `artifacts/environment/network_baseline.json`，包括 n、success、median、P95、P99、MAD、route 和 proxy 状态。

每个 measured run 在计时区间之外执行 pre/post 各 20 次 probe，保存 `artifacts/network/<run_id>.json`。任一阶段 success < 100%，或 probe median 满足：

```text
baseline_med + 5 * max(baseline_mad, 0.1 ms)
```

即标记 `network_unstable=true`，同时报告 P95，不自动删除结果。baseline 本身不稳定时停止 formal lane。内网 endpoint 必须进入 `NO_PROXY/no_proxy` 或确认不使用 proxy。

正式 run 前必须确认 construction server running/waiting request 为 0、无第三方 workload、无 restart 和 thermal/power throttling；若无法确认，写入 `shared_server=true` 并暂停 performance gate。以约 1 s 采样（先过 overhead gate）保存 GPU utilization、显存、功耗、clock、temperature、vLLM running/waiting/KV usage 到 `artifacts/telemetry/<run_id>.parquet`。server queue、batching 和 GPU utilization 是 treatment 路径的一部分，不从主 latency 中扣除。

M0/M1/M2 共享同一 checkpoint/revision、vLLM 0.26.0/config、GPU、schema、decode、global LLM cap=8、embedding endpoint/dimension、Neo4j/index、HTTP client/pool/timeouts/keep-alive、network route、telemetry rate 和 DB pool。公平性是 same resource envelope，不是强行让瞬时 utilization 相同；M0 的真实串行约束不得人为并行化，M2 的并行收益不得归一化。

### 21.4 Cache、warm-up、DB 和错误生命周期

性能 lane 的状态定义是 **hot engine + cold cross-run application/prefix state + natural within-run reuse**：

1. 服务启动后只用 synthetic warm-up 预热 CUDA/kernel/runtime；等待 server running/waiting=0。
2. 调用 pinned vLLM `0.26.0` 的 `reset_prefix_cache` endpoint，并验证返回值；endpoint 不可用时使用已测试的等价隔离方式，不能假设成功。
3. 清空/rebuild 本 run logical DB，验证 node/edge count=0、索引/约束 ready。
4. 清空本 run embedding cache，建立正式 HTTP client pool，做一次不计时 health preconnect。
5. 完成 pre-run network gate 后才开始 measured trace；正式数据 prompt 不得用于 cache reset 后 warm-up。
6. run drain 到 client requests 完成且 vLLM running/waiting=0，再停止 telemetry、执行 post-run network gate、导出 graph 和 flush artifact。

Neo4j 不重启、不清 Linux page cache；保持 hot DB engine + cold logical graph。每 run 删除前一 run 逻辑数据，验证 node/edge=0，重建统一 indexes/constraints，并使用相同 connection-pool 配置。

错误分类固定为：

- protocol-approved semantic retry：所有方法一致，计入 call/token/time；
- infrastructure failure：connection/DNS/route、server restart、无关 host failure；不静默 retry，标记 `infra_failed=true`；
- treatment-induced failure：方法自身并发引起的 429/5xx、OOM、DB conflict/deadlock；计入该方法结果，不按 infra 删除。

### 21.5 Calibration、characterization 与强 baseline

correctness smoke PASS 后，先对 4 个冻结 calibration instances 运行带 tracing 的 M0，生成 service-time distribution（mean、p25、p50、p75、p90、p95、std、CV、SCV），以 native median 冻结 formal `DELTA_MS`，不得看 M1/M2 后调整。

Phase 4.5 只使用一个预先冻结的 calibration instance 做：

- M1 与 M2 的 C1/C2/C4/C8 sensitivity；报告 makespan、P95 freshness、GPU/vLLM queue、frontier stall 和可用的 parity/retrieval；
- M0 vs M2-C8 的 `rho≈0.5/1.0/1.5` load sensitivity，其中 `DELTA = 2.0×/1.0×/0.67× median_service`；
- 相同 fixed RNG seed 的小型 Poisson arrival replay（Gamma 仅在预算允许且主 Pilot 值得继续时增加）。

必须输出 `best_m1_concurrency_on_calibration` 与 `best_m2_concurrency_on_calibration`。
只有 correctness/retrieval/exactly-once quality-feasible 的点才进入速度选择；按
calibration median makespan 最小化，完全相同时选较小 C。formal primary 仍保留
M1-C8/M2-C8 `iso-cap` 比较；若 M1-C4 更快且 quality-feasible，报告
Best-Tuned-M1，但不得冒充全量 paired primary baseline。

### 21.6 Blocked randomization、重复稳定性与 run lifecycle

performance 的 run 单位替换为 `block = (question_id, repeat)`，每个 block 包含 M0、M1、M2。固定 seed `20260806` 在六种 method permutation 上平衡轮转：`M0-M1-M2`、`M1-M2-M0`、`M2-M0-M1`、`M0-M2-M1`、`M2-M1-M0`、`M1-M0-M2`。block 内三方法共享相近 wall-clock 区间，但每次 run 仍执行 cache/DB/network isolation。

若 block 内任一方法明确 infra/network failure：保留旧 artifact，生成新 block ID，**entire block** 的 M0/M1/M2 全部重跑；旧 block 不进 primary paired statistics，但进入 failure appendix。若是 treatment-induced failure，则保留并计入该方法，不得当作环境异常。

2 repeats 完成后预注册计算 P95 与 makespan 的 `relative_repeat_gap = |x1-x2|/mean(x1,x2)`。若超过 25% 的 `(instance, method)` gap >10%，设置 `stability_inconclusive=true`；若增加第三 repeat，必须对全部 8 instances×3 methods 使用新完整 blocks，禁止按结果方向补跑。统计 resampling unit 始终是 question_id；单 instance 的 P99 只作描述性指标。

每个 measured run 的状态机固定为：

```text
PRECHECK → SERVER_IDLE_GATE → CACHE_RESET → DB_RESET → NETWORK_PRE_GATE
→ TELEMETRY_START → MEASURED_OPEN_LOOP_RUN → DRAIN_TO_ZERO
→ TELEMETRY_STOP → NETWORK_POST_GATE → DB_CANONICAL_EXPORT
→ ARTIFACT_FLUSH → RUN_FINALIZE
```

### 21.7 Instrumentation overhead 与 deterministic normalization guardrail

Instrumentation 使用 `time.monotonic_ns()`，span 先写内存 buffer，run 结束批量 flush；telemetry 低频采样，critical path 禁止 per-span fsync 或 pretty JSON。用 deterministic response replay/fake model 交替运行 M0 instrumentation OFF×5 与 ON×5，定义：

```text
median(on) / median(off) - 1
```

上限为 2%（`instrumentation_overhead_limit: 0.02`）；2–5% 是 warning，>5% 是 fail，不能进入 formal performance。

为审计 deterministic adapter 对 upstream representativeness 的影响，在 4 个
calibration instances 上运行 `U0=Upstream-Qualified-Graphiti-Serial` 与
`D0=Deterministic-Graphiti-Serial`，比较 canonical graph、entity/edge F1、Evidence
Recall@10、LLM calls/tokens 和 makespan。若预注册 guardrail 未通过，保留两者并
收缩 external-validity claim；D0 名称不随结果改写。
历史 v1.1 文本中的 `Upstream-Native-Serial` / `Deterministic-Native-Serial` 仅为
旧 alias，v1.3 报告统一使用 U0/D0 名称。

performance live lane 对相同 logical prompt 记录 response hash、finish reason、
logical/HTTP/retry ledger 和 token usage，派生 descriptive
`live_response_divergence_rate`。相对 D0 的 call ledger 不一致，或 input/output token、
embedding item ratio 超出双侧 `[0.95,1.05]`，标记
`performance_confounded=true`；结果仍报告为 descriptive E2E，但不能解释为同
工作量 scheduling speedup，也不能解释为 correctness failure。

### 21.8 v1.1 新增 artifact 与冻结文件

除第 15 节既有 artifact 外，必须生成：

```text
artifacts/environment/network_baseline.json
artifacts/environment/server_capability.json
artifacts/environment/cache_reset_contract.json
artifacts/characterization/phase_map.json
artifacts/characterization/native_phase_spans.parquet
artifacts/characterization/native_llm_requests.parquet
artifacts/characterization/native_db_operations.parquet
artifacts/characterization/native_phase_summary.json
artifacts/characterization/concurrency_sensitivity.parquet
artifacts/characterization/load_sensitivity.parquet
artifacts/characterization/instrumentation_overhead.json
artifacts/characterization/upstream_normalization_guardrail.json
artifacts/characterization/CHARACTERIZATION_REPORT.md
artifacts/characterization/freeze.json
artifacts/telemetry/<run_id>.parquet
artifacts/network/<run_id>.json
artifacts/final/mechanism_metrics.parquet
```

`freeze.json` 必须固定 phase map hash、instrumentation code hash、network baseline hash、cache/HTTP/DB pool policy、formal `DELTA_MS`、method configs 和 blocked run blocks。API key 只能存在未跟踪 `.env`，禁止进入上述任何 artifact。

### 21.9 严格执行顺序与报告扩展

v1.1 的完整 TDD/实验顺序为：

```text
correctness smoke PASS
→ instrumentation contract red tests
→ span/network/cache/lifecycle implementation
→ full unit regression
→ instrumentation overhead gate
→ live cache-reset contract
→ 100-probe network baseline
→ upstream normalization guardrail
→ 4 calibration M0 characterization
→ freeze DELTA_MS + phase_map
→ C sensitivity
→ load/Poisson sensitivity
→ characterization freeze
→ blocked formal plan
→ correctness lane
→ performance lane
→ instance-level paired statistics
→ original GO/INCONCLUSIVE/NO-GO + mechanism_verdict
```

以上为历史 v1.1 设计，不再授权任何 live run。当前是否可以进入下一阶段只由
`CURRENT_STATE.json` 和 v1.3 gate 决定；正式计划为 24 correctness runs +
48 live performance runs = 72 runs，最终只输出 GO / INCONCLUSIVE / NO-GO。
