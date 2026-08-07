# MemBind 基础验证实验规范

> 文档状态：Pilot Protocol v1.0  
> 目标：以单一 backend、单一数据集、单一 backbone，直接判断 Semantic Late Binding 是否值得继续研究。  
> 本协议只验证核心 idea，不验证跨 backend 通用性、复杂调度、容错或完整线上部署。

---

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

在相同硬件、模型、prompt、数据库和并发额度下，MemBind 相比 Graphiti 原生串行执行：

- 降低每个 episode 的 P95 `arrival_to_publish_ms`；
- 降低每条 history 的总构建 makespan；
- 不增加超过 5% 的 LLM 输入/输出 token。

### H2：语义假设

MemBind 在所有 episode 提交完成后，应与 Graphiti 原生串行执行得到相同的 canonical semantic graph：

- episode 无丢失、无重复；
- canonical entity 集合一致；
- canonical relation/fact 集合一致；
- temporal validity / invalidation 一致；
- retrieval 结果与原生串行一致或近似一致；
- LongMemEval-S evidence-session recall 不下降。

### H3：必要性假设

粗粒度并发执行完整 `add_episode()` 虽可能更快，但可能改变 state-dependent binding/consolidation 的结果；MemBind 应在接近其性能的同时，保持原生语义。

如果粗粒度并发与原生串行在所有评测实例上完全一致且性能不差于 MemBind，则本实验不能证明 Late Binding 的必要性。

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
- Docker tag：`neo4j:5.26-community`
- 第一次拉取后记录镜像 digest 到 `artifacts/environment/manifest.json`。
- 每个 `(question_id, method, repeat)` 使用独立空数据库。
- 禁止跨实验复用图状态。

### 3.3 Construction backbone

- 模型：`Qwen/Qwen3-32B-FP8`
- Hugging Face revision：`6e2312b85c2ae9a31f629f24493b79d8b02eab1a`
- 推理框架：vLLM `v0.26.0`（用户于 2026-08-07 明确批准替代原协议的 `v0.23.0`；本次执行对 M0/M1/M2 统一冻结该版本）
- 模式：`enable_thinking=false`
- 结构化输出：必须使用 vLLM guided JSON 或 Graphiti 当前 structured-output schema。
- 所有方法必须使用完全相同的 Graphiti prompt、schema 和模型参数。

固定解码参数：

```yaml
temperature: 0.0
top_p: 1.0
max_tokens: 2048
seed: 20260806
```

若 Qwen3/vLLM 在 `temperature=0` 下无法稳定返回合法结构化输出，可将 `temperature` 改为 `0.01`，但必须：

1. 所有方法统一修改；
2. 将变更记录到 protocol deviation；
3. 启用按完整 prompt hash 共享的 response cache。

### 3.4 Embedding model

- 模型：`Qwen/Qwen3-Embedding-0.6B`
- dtype：FP16 或 BF16；一旦选择必须冻结。
- 相同输入必须通过 embedding cache 返回同一向量。
- 禁止不同方法使用不同 embedding model 或维度。

### 3.5 硬件

固定使用：

- 2 × NVIDIA RTX PRO 6000；
- GPU 0：启动一个 Qwen3-32B-FP8 vLLM construction server；
- construction server 的 `max_num_seqs=8`，实验总 construction LLM 并发额度为 8；
- GPU 1：独占运行 Qwen3-Embedding-0.6B embedding server；
- 两个服务在全部方法中保持相同，不允许动态迁移或共享 GPU；
- Neo4j、replay driver 和 Graphiti runtime 使用同一台 CPU 主机。

必须记录：

```text
GPU 型号与显存
NVIDIA driver
CUDA
Python
PyTorch
vLLM
Graphiti commit
Neo4j image digest
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

### 4.2 冻结划分

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

即：

- calibration：4 个实例；
- evaluation：8 个实例；
- 其余实例在本 pilot 中不使用。

必须将选中的 `question_id`、原始数据文件 SHA256 和筛选脚本版本写入：

```text
artifacts/dataset/frozen_split.json
```

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

### M0：Native-Serial

Graphiti 原生参考执行：

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

目的：隔离调度语义，判断 M2 是否与 M0 等价。

步骤：

1. 对每个 evaluation instance 运行一次 M0，并记录所有完整 prompt、raw response、parsed response 和 token usage；
2. 使用完整 prompt hash 建立只读 response cache；
3. 运行 M2-replay；
4. M2 遇到与 M0 完全相同的 prompt 时，必须复用相同 response；
5. M2 遇到 M0 从未产生的 prompt 时，立即标记 `unexpected_prompt=true`，不得调用 live model 补齐；
6. `unexpected_prompt` 表明 M2 的状态或调用语义已偏离 M0，correctness run 判定失败；
7. Correctness lane 不用于报告性能。

Cache key：

```text
sha256(
    model_revision
    + decoding_config
    + structured_output_schema
    + system_prompt
    + user_prompt
)
```

Correctness lane 共：

```text
8 M0 capture runs + 8 M2 replay runs = 16 runs
```

### 6.2 Performance lane：live model、禁用 response cache

目的：测量真实 Compile 并发带来的端到端收益。

规则：

1. M0、M1、M2 均真实调用同一个 live model server；
2. 禁用应用级 prompt/response cache；
3. 允许 vLLM 自身正常的 KV/prefix cache，但三种方法配置必须完全一致；
4. embedding cache 仅允许复用完全相同文本的 embedding，并且三种方法规则一致；
5. 每个 evaluation instance、每种方法重复 2 次；
6. 性能结果仅来自该 lane。

---

## 7. Open-loop 到达协议

### 7.1 校准 arrival interval

先在 4 个 calibration instances 上运行 M0 Native-Serial。

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

加上 correctness lane 的 16 runs，正式 evaluation 共 64 runs。

执行顺序必须使用固定随机化：

```python
random.Random(20260806).shuffle(run_plan)
```

每个 run 前：

- 清空数据库；
- 创建索引和约束；
- 等待模型服务稳定；
- 执行一个不计入结果的 warm-up episode；
- 重置所有 runtime counters。

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
P99 arrival_to_publish_ms
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
```

同时报告 M1 parity 数量。

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
8. M1 在至少 1 个 evaluation instance 上出现以下之一：
   - canonical graph divergence；
   - retrieval evidence recall 下降；
   - source-order violation。

第 8 条用于证明 Late Binding 相对“直接并发完整 update”的必要性。

### INCONCLUSIVE

出现以下任意情况则判定为 inconclusive，修复实验平台后重跑：

- structured output parse success < 99.5%；
- 任一方法失败 run 比例 > 5%；
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

Agent 必须严格按以下顺序执行，不得提前跑正式实验。

### Phase 0：环境 Gate

1. checkout Graphiti commit `021d3a5`；
2. 启动 Neo4j；
3. 启动两个 Qwen3-32B-FP8 replicas；
4. 启动 embedding 服务；
5. 验证 structured output；
6. 跑 Graphiti quickstart；
7. 保存环境 manifest。

通过条件：

```text
连续 20 次结构化抽取 parse success = 20/20
Graphiti add_episode/search smoke test 通过
数据库清理和重建测试通过
```

### Phase 1：数据 Gate

1. 下载 cleaned LongMemEval-S；
2. 计算 SHA256；
3. 按固定规则生成 split；
4. 验证所有 session 有时间戳和内容；
5. 生成 episode 输入；
6. 检查未来 session 不进入当前输入。

### Phase 2：Native Reference

1. 实现 M0；
2. 在 4 个 calibration 实例运行；
3. 冻结 `DELTA_MS`；
4. 在 1 个 evaluation 实例完成端到端 smoke；
5. 保存原生 canonical graph 和 retrieval 结果。

### Phase 3：Whole Parallel

1. 实现 M1；
2. 在同一个 smoke 实例运行；
3. 确认最大并发不超过 8；
4. 输出 parity 与 source-order 诊断。

### Phase 4：MemBind-GO

1. 定位 Graphiti 当前 extraction 与 resolution 的准确源码边界；
2. 抽取 `semantic_compile()`；
3. Compile 输出不得含已绑定 UUID；
4. 实现 source-ordered bind/commit；
5. 通过全部单元测试；
6. 在同一 smoke 实例达到 M0 canonical parity。

### Phase 5：正式评测

1. 生成 correctness lane 16-run 计划与 performance lane 48-run 计划；
2. 固定随机顺序；
3. 执行全部 runs；
4. 任何失败必须记录，不得静默重跑；
5. 如需重跑，使用新的 run_id 并保留失败 artifact。

### Phase 6：分析

1. 生成 episode 和 instance 指标；
2. 做配对 bootstrap；
3. 生成 canonical parity；
4. 生成 retrieval guardrail；
5. 根据第 13 节自动输出 GO / INCONCLUSIVE / NO-GO。

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

1. **是否保持原生语义？**  
   M2 correctness lane 的 canonical graph parity、retrieval parity、`unexpected_prompt` 和 episode exactly-once。

2. **是否明显加速？**  
   P95 arrival-to-publish、makespan、drain time 与置信区间。

3. **为什么不能直接并发完整 update？**  
   M1 的性能与语义偏差；若无偏差，明确说明 Late Binding 的必要性未被证明。

4. **是否值得继续？**  
   严格根据 Go/No-Go 条件给出结论，不得使用主观措辞替代指标。

---

## 19. 本实验成功后才允许的下一步

只有判定为 GO 后，才进入下一阶段：

```text
Predicate validation
Selective rebind / continuation rerun
Conflict-domain ordered commit
Visibility-frontier scheduling
第二 backend instrumentation
```

在本实验完成前，不实现上述机制。

---

## 20. 本次执行拓扑与 TDD 覆盖

本次执行采用远程模型服务、本机 Graphiti 和本机 Neo4j 的固定拓扑：

- construction endpoint：http://10.87.5.247:8000/v1/，served model 为 qwen3-32b-fp8（2026-08-06 主机重启后 `/v1/models` 返回的精确 ID，底层 checkpoint 仍按本实验 manifest 记录）；
- embedding endpoint：http://10.87.5.247:8001/v1，served model 为 qwen3-embedding-0.6b；
- 两个 endpoint 使用同一个 API key，密钥只放在实验目录未跟踪的 .env 文件；
- Graphiti、replay driver、Neo4j Community 5.26 在本机运行，不使用 Docker；
- M0、M1、M2 必须使用相同的远程 endpoint、模型、解码参数、网络路径和并发上限；
- 所有计时在本机使用 time.monotonic_ns()，不能使用模型服务器时间。
- 固定请求预算仍为 max_tokens=2048；若受约束 JSON 在该上限被截断并解析失败，三个方法统一只允许一次 max_tokens=8192 的有界补请求，两个请求都计入 LLM call/token 指标，并在最终报告中列为 protocol deviation；该上限由 smoke04 的 4096 响应仍以 finish_reason=length 截断这一持久化证据确定；
- 本实验每次 extraction 只包含一个 current episode，因此三个方法和 correctness cache hash 共用的 JSON schema 将 `episode_indices` 严格约束为单元素 `[0]`；Graphiti prompt 与事实字段不变，此约束修复了 vLLM 在无界整数数组中持续输出 `0,1,2,...` 直至 length 截断的问题；
- 主机第一次重启后的 construction context window 曾为 32768；若 vLLM 因声明的 completion budget 越界返回 400，先用同一完整 prompt 的 1-token probe 从成功响应 usage 获取精确 input tokens，再以“context 上限 - 精确 input tokens - 32”作为有界重发预算；probe 与重发都计量，禁止裁剪输入；
- 完整 `smoke06` 证明历史 32768 context 不足以执行冻结输入：M0 source sequence 19 的原生 Graphiti node-resolution prompt 精确占用 32757 tokens，在安全余量前也只剩 11 completion tokens；保留的 `diagnostic_context_cap_005` 给出 primary 2048 预算所需最小 context 34837、完整 overflow 8192 预算所需 40981；因此远端最低门槛冻结为 `max_model_len>=40960`，禁止通过裁剪 history 或改写 prompt 绕过；
- 2026-08-07 用户将远端 `max_model_len` 恢复为 40960，并明确批准本次协议统一使用 vLLM 0.26.0 替代原定的 0.23.0；代码、`.env`、配置和 gate 期望值均冻结为 0.26.0，历史 blocker 与失败 attempt 继续保留并由新的成功 gate 标记为 resolved；
- Graphiti 的 RRF 仍负责选择 edge-resolution top-K 候选集合；M0/M1/M2 在候选进入 LLM prompt 并获得连续 idx 前统一采用 `logical_content_ascending_after_top_k`，即按 fact、relation 和 temporal logical content 规范化已选集合，并让 score 随 edge 同步移动。该规则不改变 top-K membership 或 cutoff，只消除 fresh graph 的随机 UUID / Neo4j 物理返回顺序对完整 prompt hash 的影响，correctness 与 performance lane 均启用；
- correctness replay 的任何 cache miss 仍立即失败且禁止 live fallback；同时必须把 prompt name、五个组件 hash、请求 PromptParts 和最近 M0 cache record 持久化到 `artifacts/unexpected_prompts/`，API key 不得进入诊断 artifact；

正式实验前必须按以下测试驱动顺序执行：

1. 先新增或修改不变量测试，覆盖数据 split、evidence fence、future evidence、cache replay、source order、exactly-once 和 canonical parity；
2. 在没有启动任何服务时，运行全部单元测试并保持通过；
3. 分别执行 construction / embedding endpoint contract smoke，校验 model id、结构化输出和 embedding dimension；
4. 启动本机 Neo4j，执行索引创建、清库、warm-up、search、再次清库的隔离测试；
5. 只在 smoke instance 达到 M0 parity 后冻结 DELTA_MS；
6. 最后生成并冻结 64-run 随机计划，再执行 correctness lane 和 performance lane。

任何 TDD gate 失败都必须停在当前阶段，不能提前跑正式 evaluation。

本次 smoke 失败证据必须保留：`smoke01` 因 Graphiti 的 16384 输出预算与 24577+ 输入 token 超过远端 40960 context 而失败，随后增加 2048 clamp；`smoke02` 因 2048-token edge JSON 在字符串中截断而失败；`smoke03` 暴露 DB instrumentation 参数冲突；`smoke04` 及其 diagnostic replay 证明 4096 补请求仍以 finish_reason=length 截断，因此一次性补请求上限调整为 8192；`smoke05` 暴露重启后 32768 context 的动态 completion budget 兼容问题；`smoke06` 则证明 source 19 的完整 prompt 本身已占 32757 tokens，32k 服务无法产生有效结构化输出；`smoke07` 在 40960/0.26.0 下完整成功捕获 46 episodes 和 702 条 prompt records，但首次 M2 load 暴露 U+2028 被 `splitlines()` 错当 JSONL 边界；`smoke08` 在修复 ASCII-LF framing 后证明 M2 previous episodes 错误地 newest-first 呈现，source 2-45 共 44 条只读 miss 均未调用 live model；`smoke09` 在 chronological fix 后推进到 source 1 bind，并证明同一 edge candidate 集合因 fresh Neo4j/RRF 物理顺序不同而产生不同 idx/hash，由此冻结三个方法共享的 `logical_content_ascending_after_top_k`。后续 attempt 必须使用新 run_id，不能覆盖这些失败 artifact。
