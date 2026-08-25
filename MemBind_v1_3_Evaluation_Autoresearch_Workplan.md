# MemBind v1.3 单数据集三方主实验：MemoryAgentBench Autoresearch + TDD 最终 Workplan

> **文件地位**：这是本项目唯一的主实验执行计划。Agent 不得再创建平行 workplan、数据集 track、替代 protocol 或“先做另一个 benchmark”的支线。
>
> **唯一正式数据集**：MemoryAgentBench（MAB）Accurate Retrieval 中 `source == "longmemeval_s*"` 的**官方完整 5 个 context**。
>
> **唯一正式主实验**：同一份冻结 workload manifest 分别运行 B0 Native Serial、B1 Naive Whole-Update Async、MemBind V6；每个方法都在自己的 fresh namespace 上完成同样的 555 个 session 构建，并在 durable seal 后回答同一组 300 道官方 QA。性能、并发正确性、质量守卫、freshness、queue/backlog 和 work accounting 全部来自这一数据集、这一协议、这一组运行。

---

## 0. 一页执行摘要

### 0.1 本轮要回答的唯一科学问题

在冻结的 `saturated_fixed_work_baseline_v1_3` 上，如何公平比较：

- **B0 Native Serial**：有序参考执行；
- **B1 Naive Whole-Update Async**：放松顺序的并发参考；
- **MemBind V6**：希望在保持规定顺序语义的同时提高性能；

并让最终 QA 回到它合理的位置——**端到端质量守卫**，而不是并发正确性证明。

主张链必须拆开：

1. **工作相同**：三方接收完全相同、顺序相同、内容相同的输入；
2. **构建完成**：每个输入被提交且恰好完成一次，结果已 durable；
3. **语义契约成立**：B0/V6 满足规定的顺序与 V6 replay/binding refinement；
4. **性能结果**：在上述 gate 成立的 block 上比较构建 makespan；
5. **质量未见退化**：在 sealed graph 上运行同一批官方 QA，报告总体与预定义切片；
6. **机制解释**：queue、freshness、backlog、调用量和 trace 只用于解释原因。

QA 相同并不能证明并发历史正确；B1 即使 QA 不下降，也只能说明该 QA 集没有暴露其顺序差异。

### 0.2 唯一数据与实验单位

冻结数据资产：

| 字段 | 冻结值 |
|---|---|
| Benchmark | MemoryAgentBench Accurate Retrieval |
| Hugging Face dataset | `ai-hyz/MemoryAgentBench` |
| Revision | `7ea066982b140a19337e17e60d45d4076e042faf` |
| Source filter | 精确等于 `longmemeval_s*` |
| 本地候选文件 | `mab_quality_v2_final_qa/data/official_5_contexts.json` |
| 本地文件 SHA-256 | `97fd80207f3419fc57c3684db824334224546d6bdd62c17ef52cd116eec9ffc8` |
| Context 数 | 5 |
| Session 数 | 111、107、116、111、110，共 555 |
| QA 数 | 每个 context 60，共 300 |

**一个 context 是一个独立 workload unit 和 namespace。** 一个官方 session 映射为一个 `EpisodeInput`。不得把 5 个 context 拼成一个图，不得把单个 session 人工切块，不得为 QA checkpoint 改写 arrival。

### 0.3 最小到正式的执行路径

Agent 必须逐级运行、小步定位，但 gate 失败不是停止命令：

1. 离线 authority、adapter、schema 和 reducer 测试；
2. provider-free 的 2-session 测试夹具；
3. 真实 context 0 的 1/2/8-session B0 探针；
4. 同一 8-session 前缀的 B0/B1/V6 micro-triad；
5. 必要时 16、32-session triad；
6. context 0 完整三方构建，先跑预冻结的 6-QA smoke，再跑其全部 60 QA；
7. 5 个 context × 3 方法的 fresh first pass，共 15 个 block；
8. 冻结实现和配置后，5 个 context × 3 方法 × 3 fresh repeats，共 45 个正式 block、2700 条 QA-result row。

前缀与 micro-run 只能标记为 `ENGINEERING_DIAGNOSTIC`，物理排除在正式 reducer 之外。

---

## 1. 范围、冻结项与非目标

### 1.1 仓库与协议起点

执行前在真实机器 `/data/predator/ly/MemBind` 记录：

- `git rev-parse HEAD`；
- `git status --short`；
- Python、依赖、Graphiti、数据库、模型和 endpoint 身份；
- `saturated_fixed_work_baseline_v1_3` 的配置、runner 与现有 artifact schema。

本计划评审时所见代码快照为 `2832d94b56db72fcf993154bde47e16b31ade724`，但它不是对执行机器的猜测替代。若实际 HEAD 不同，Agent 先做只读 diff 和影响说明，然后仍以用户指定的最新代码为执行基线；不得静默切回旧 commit。

### 1.2 必须冻结的内容

以下内容不得根据实验结果调整：

- B0 的 Native Serial 执行方式；
- B1 的 Naive Whole-Update Async 执行方式；
- MemBind V6 的 FrontierExecutor、provider、binder、capture/replay 算法和调度逻辑；
- v1.3 的 saturated、fixed-work、zero-offset arrival 定义；
- 三方 worker/concurrency 配置及资源配额；
- Graphiti、LLM、embedding、database 的版本和 endpoint；
- session 到 EpisodeInput 的冻结 renderer；
- QA prompt、judge、scorer、解码参数和失败规则；
- 正式数据的 5 个 context、全部 555 sessions、全部 300 QA；
- primary/correctness/guard/diagnostic 的指标角色；
- 正式重复数、arm order 和统计单位。

### 1.3 允许的最小改动

只允许做 evaluation plumbing：

- 把固定数据 manifest 接到现有 v1.3 runner；
- 统一三方 instrumentation 和 raw artifact；
- 补 lifecycle、order、binding、work-accounting 字段与 validator；
- 让现有 QA runner 只读地连接已 sealed namespace；
- 补 outer campaign orchestrator、seal 和 reducer；
- 补测试、fixture、错误分类和恢复逻辑。

这些改动不得改变任何方法实际看到的输入、arrival、并发策略或内部算法。

### 1.4 明确非目标

本轮不做：

- 不重新设计 benchmark；
- 不新增 synthetic concurrency workload；
- 不把 LoCoMo、MemOps、AgentMemBench、PersonaMem 或 MAB 其他 task 并列为实验 track；
- 不用多个数据集分别承担性能、correctness 与 QA；
- 不按结果挑 context、QA 类型、repeat 或 metric；
- 不用 graph exact equality 代替语义契约；
- 不把 B1 称为 performance ceiling；
- 不把 QA 不下降写成并发正确性；
- 不为获取更好结果修改冻结方法。

---

## 2. 为什么最终只选这个数据集

### 2.1 选择结论

主实验采用 MAB Accurate Retrieval 的 `longmemeval_s*` 完整 5-context component。它的核心结构是**一段长记忆只构建一次、随后回答多道问题**。这直接修复原 LongMemEval 在本项目中的结构性低效：约 40–50 个 episode 构建后通常只有一道 QA，导致 QA 成本高、覆盖低，也无法在同一 graph 上观察足够多的 current-state/temporal 问题。

MAB 的每个 context 有 107–116 个 session 和 60 道 QA，既能形成真实的饱和构建负载，也能在同一 sealed graph 上覆盖六类问题。因而一个数据集可以同时提供：

- 固定工作量的构建性能；
- 来自同一 episode stream 的 ordered/refinement trace；
- 官方总体 QA；
- 官方 knowledge-update 切片作为 current-state 相关质量守卫；
- 官方 temporal-reasoning 切片作为时间推理质量守卫；
- 同一运行的 queue、freshness、backlog 与 work accounting。

### 2.2 官方 QA 类型分布

| 官方问题类型 | 数量 | 在本文中的角色 |
|---|---:|---|
| `multi-session` | 75 | 多 session 综合质量守卫 |
| `temporal-reasoning` | 75 | 时间推理质量守卫 |
| `knowledge-update` | 45 | current-state 相关质量守卫 |
| `single-session-user` | 45 | 单 session 用户事实质量守卫 |
| `single-session-assistant` | 30 | 单 session 助手事实质量守卫 |
| `single-session-preference` | 30 | 偏好记忆质量守卫 |
| **总计** | **300** | 官方总体质量守卫 |

`knowledge-update` 与 `temporal-reasoning` 是数据集原生、预定义的类型，不是为了让 MemBind 获利而新发明的指标。但它们仍然只是端到端 QA guard；它们不替代 trace-level correctness。

### 2.3 其他候选为何不进入可执行计划

| 候选 | 优点 | 不作为本轮唯一主数据集的原因 |
|---|---|---|
| 原始 LongMemEval | 已有适配，长对话真实 | 一次约 40–50 episode 通常只有 1 QA，正是当前问题 |
| LoCoMo | 1,986 QA，Graphiti 有公开适配 | 缺少明确、预定义的 knowledge-update/current-state slice；容易继续只比较宽泛最终 QA |
| MemOps | 有 operation/state gold | 当前映射每样本仅约 3 episodes，若扩成饱和 workload 需拼接或切块，会改变 workload |
| AgentMemBench | 有 retrieval、conflict、concurrency 任务 | 多种任务是不同生成 workload；concurrency 使用独立 group，违反“一份 workload 承担所有角色” |
| PersonaMem | 有 checkpoint/end-index 结构 | 需要中间 drain、checkpoint 或重建，改变冻结 arrival/构建流程 |
| MAB FactConsolidation | 有显式 consolidation 场景 | 每个设定接近 n=1，且会成为第二套数据，不适合作为本轮三方主实验 |

这些候选只保留为数据集选择依据，不得生成脚本入口、运行矩阵或附加结果表。

### 2.4 完整 5-context authority 与已知缺陷

现有仓库曾因第 5 个 context 中问题 `0ddfec37_abs` 的一个 declared gold session 无法在 common public context 中定位，而生成 `DECLARED_4_CONTEXT_INVENTORY_20260819.json` 并排除整个 context。正式主实验**不得沿用这个 4-context authority**，原因是：

- 第 5 个 common context 本身存在且可完整构建；
- 该问题是局部 gold provenance 映射缺陷，不是 construction context 缺失；
- 排除整个 context 会把“完整官方 component”变成事后子集；
- 官方 answer-based QA 仍可执行，受影响的是证据定位类诊断字段。

处理规则：

- construction validity：5/5 context 均有效；
- official answer QA：仍报告全部 300 道；
- 对 `0ddfec37_abs` 的 evidence/provenance 诊断标记 `PARTIAL_GOLD_MAPPING`；
- 无法可靠计算的 evidence recall/MRR/nDCG 字段填 `null`，不得填 0；
- 不得删除该题、删除第 5 个 context，或把 4-context 子集称为 full。

---

## 3. 单一 Dataset → Workload 契约

### 3.1 Authority freeze

先生成不可变的 `dataset_authority.json`，至少包含：

```json
{
  "benchmark": "MemoryAgentBench",
  "task": "Accurate Retrieval",
  "source_filter": "longmemeval_s*",
  "hf_dataset": "ai-hyz/MemoryAgentBench",
  "hf_revision": "7ea066982b140a19337e17e60d45d4076e042faf",
  "local_file_sha256": "97fd80207f3419fc57c3684db824334224546d6bdd62c17ef52cd116eec9ffc8",
  "context_count": 5,
  "session_counts": [111, 107, 116, 111, 110],
  "total_sessions": 555,
  "qa_per_context": 60,
  "total_qa": 300,
  "authority_status": "FULL_OFFICIAL_COMPONENT"
}
```

任何字段不匹配时，Agent 不应直接退出整个研究，而应：保留失败 artifact → 定位数据版本/解析差异 → 查官方 revision 与 schema → 修 adapter/test → 重跑 authority check。不得自动切换数据集或缩成 4 个 context。

### 3.2 Construction projection

正式构建只能读取每个样本的 common public `context`。`metadata.haystack_sessions` 是逐问题结构，含 `has_answer` 等 gold 信息，**禁止**作为 construction 输入。

每个 official session 按原顺序映射为一个 `EpisodeInput`：

- `source_sequence`：context 内从 0 开始的连续整数；
- `episode_id`：由 dataset revision、context id、source_sequence 产生的稳定 ID；
- `reference_time`：保留官方 session timestamp；
- `body`：固定 serializer 对 session 中每条消息按原 role/content 输出；
- `arrival_offset_s`：全部为 0，保持 v1.3 saturated arrival；
- `context_id`：只用于 namespace/配对，不进入模型正文；
- 不含 question、answer、question_type、gold session ID、`has_answer` 或其他 gold/private 字段。

注意：event time 与 arrival time 是两个概念。官方 timestamp 保存在 `reference_time` 中表达记忆时间；所有请求在 formal start 可立即提交，维持 zero-offset saturated workload。

### 3.3 Workload manifest

每个 context 生成一个方法无关的 `workload_manifest.jsonl` 和一个 canonical hash。Hash 至少覆盖：

- dataset authority identity；
- context identity；
- 每个 episode 的 source_sequence；
- reference_time；
- canonical role/content body；
- arrival_offset；
- episode 数量和顺序。

Hash **不得**覆盖 method、namespace、run id、wall-clock 时间或随机临时目录，确保 B0/B1/V6 的 workload hash 可严格相等。

正式 block 必须验证：

```text
hash(B0 manifest) == hash(B1 manifest) == hash(V6 manifest)
```

且每一方都满足 `source_sequence == [0, 1, ..., N-1]`。

### 3.4 Namespace 与 graph 生命周期

- 一个 context × 一个 method × 一个 repeat = 一个 fresh namespace；
- namespace 在 preflight 前不存在，或明确为空；
- 任何失败重试使用新的 attempt namespace；
- 不得把失败 namespace 清空后伪装为原 attempt 重跑；
- 构建开始后不得插入 QA；
- 最后一项 `PUBLICATION_DURABLE` 后生成 `construction_seal`；
- QA runner 只能读取 sealed namespace；
- QA 前后都记录 graph/canonical state hash 或只读证明，确保 QA 没有写入。

### 3.5 QA projection

每个 context 的 60 道官方 QA 全部运行。QA manifest 预先冻结：

- question id；
- context id；
- question type；
- question text hash；
- reference answer hash；
- judge/scorer version；
- prompt template hash；
- 预定义 6-question smoke 子集。

Smoke 子集在看任何方法结果前选定，每个官方类型恰好一题；它只验证执行链，不进入正式统计。完整 60 QA 必须复用同一个 sealed graph，不得为 smoke 或 full QA 重新构建。

---

## 4. 三方方法与公平性契约

### 4.1 语义角色

| 方法 | 正式标签 | 解释 |
|---|---|---|
| B0 Native Serial | `ORDERED_REFERENCE` | 原生逐 update 顺序执行的参考 |
| B1 Naive Whole-Update Async | `RELAXED_ORDER_REFERENCE` | 相同工作、允许 whole-update 重叠的并发参考 |
| MemBind V6 | `ORDERED_REFINEMENT` | 以 capture/bind/replay 保持规定顺序的并发方法 |

B1 是必要对照，用于展示“直接并发”的性能和可观察历史；它不是理论上界，也不能因发生 inversion 就被描述成错误。正式文字只能说其语义类别放松了 B0/V6 要求的 order contract。

### 4.2 每个 block 的共同阶段

```text
PREFLIGHT
  → FORMAL_START
  → SUBMIT(all zero-offset episodes)
  → method-specific execution
  → PUBLICATION_DURABLE(all episodes)
  → CONSTRUCTION_SEAL
  → QA_READ_ONLY
  → QA_SEAL
  → REDUCE
```

三方的测量窗口必须完全一致：

```text
T_build = max(PUBLICATION_DURABLE timestamp) - FORMAL_START timestamp
```

不能把 B0 测到 API return、B1 测到 task join、V6 测到 replay submit；共同终点必须是结果对后续只读 QA 可见且 durable。

### 4.3 配置公平性

每个三方配对 block 必须具有相同：

- workload hash；
- Graphiti/DB/LLM/embedding 版本；
- model 和解码配置；
- machine/resource class；
- concurrency/worker 配额中协议原本规定的可比设置；
- timeout/retry policy；
- instrumentation level；
- QA manifest、prompt、judge 与 scorer。

配置差异由 reducer 直接将该配对标记为 `CONFIG_MISMATCH`，不得人工解释后纳入主表。

---

## 5. 并发正确性：不用最终 QA 代替历史验证

### 5.1 Correctness 的对象

由于 Graphiti/LLM 可能非确定，正式目标不是要求 B0 和 V6 生成字节完全相同的 graph，而是验证：

1. 输入工作严格相同；
2. lifecycle 完整且无漏做/重复做；
3. B0/V6 的外部可观察 publication 顺序满足契约；
4. V6 capture 到 replay 之间存在一一对应、身份绑定和内容绑定；
5. V6 不从 replay 路径再次访问外部 transport；
6. QA 作为 sealed state 上的独立 quality guard。

这是 contract-preserving trace refinement，而非 graph-bitwise equivalence。

### 5.2 通用 fixed-work/lifecycle gate

对每个 block 验证：

- `submitted_count == expected_episode_count`；
- `completed_count == expected_episode_count`；
- 每个 `source_sequence` 恰好出现一次；
- 每个 episode 恰好有一个 terminal durable publication；
- 没有未知 episode、重复 terminal、未终止 task；
- FORMAL_START 早于所有正式 submit/native enter；
- construction seal 晚于所有 durable publication；
- QA 开始晚于 construction seal；
- QA 前后 graph state 未改变。

这些属于 correctness gate，而不是 diagnostic。

### 5.3 Ordered execution predicate

对 B0 与 V6，按 `source_sequence` 对相邻 update 检查：

```text
NATIVE_ENTER(i) > PUBLICATION_DURABLE(i-1),  for every i > 0
```

若真实代码把语义提交点定义在更精确的 event 上，允许使用经代码与文献审计后冻结的同义事件，但三方必须一致记录，且不得在看结果后改变。Validator 输出：

- `ordered_pair_count`；
- `order_violation_count`；
- 第一个 violation 的两个 episode、时间戳和原始 event index；
- `order_contract_status ∈ {PASS, FAIL, NOT_REQUIRED, INVALID_TRACE}`。

B0/V6 要求 `PASS`。B1 为 `NOT_REQUIRED`，同时报告其 observed inversions 作为 diagnostic。

### 5.4 V6 exact binding/refinement gate

每次 V6 captured request 与 replay consumption 必须有可审计的一一映射。最低字段：

- `source_sequence`；
- `callsite`；
- `ordinal_within_episode`；
- `request_identity_hash`；
- `prepared_response_hash`；
- `native_request_hash`；
- `capture_count`；
- `consume_count`；
- `match_status`；
- `external_transport_attempted_during_replay`。

每个绑定必须满足：

```text
capture_count == 1
consume_count == 1
match_status == EXACT_MATCH
external_transport_attempted_during_replay == false
```

并验证集合级双射：无 orphan capture、无 orphan replay、无跨 episode/ordinal 错绑、无 duplicate consume。该 gate 是 MemBind 特有 correctness/refinement 证据；它不能用 QA 替代。

### 5.5 Graph 状态比较的地位

可生成节点数、边数、canonical entity/edge summaries、状态 hash 与 diff，但只作为 diagnostic。原因是：

- LLM 与抽取可能非确定；
- 同义实体/边可能有不同字面表示；
- graph exact equality 既可能过严，也可能漏掉历史错误。

不得把 graph diff 单独升级为 correctness gate；也不得因 graph 数量相同宣称语义等价。

### 5.6 状态维度必须分离

每个 block 必须分别输出：

- `artifact_status`：文件完整、seal/hash 可验证；
- `contract_status`：fixed work、lifecycle、order；
- `refinement_status`：V6 binding/replay；B0/B1 为 N/A；
- `quality_status`：QA 是否完整有效；
- `inclusion_status`：是否可进入哪一张表。

一个维度失败不得覆盖其他维度。例如 QA provider 失败时，性能与 correctness artifact 仍可有效；但该 block 的 QA 值为 null，不能写 0。

---

## 6. 指标分层与主表

### 6.1 Primary performance endpoint

唯一 primary performance endpoint：

```text
T_build = last PUBLICATION_DURABLE - FORMAL_START
```

主要效应量按 context 和 repeat 配对：

```text
delta(method, context, repeat)
  = log(T_build(B0, context, repeat) / T_build(method, context, repeat))
```

同时展示更易读的：

```text
speedup_vs_B0 = T_build(B0) / T_build(method)
```

`durable_goodput = expected_episode_count / T_build` 为 secondary performance metric；因为 fixed work 下它与 makespan 是代数对应关系，不能被当作第二个独立胜利条件。

### 6.2 Correctness gates

进入 primary performance aggregation 前必须通过：

- artifact/seal integrity；
- dataset/workload/config identity；
- fixed-work parity；
- common lifecycle validity；
- B0/V6 order contract；
- V6 exact binding/refinement contract。

B1 的 order 是 `NOT_REQUIRED`，因此它可进入 B1 descriptive performance；但必须同时展示 observed inversion diagnostics，避免读者误解其语义与 B0/V6 相同。

### 6.3 Quality guards

Quality guard 使用 MAB 官方 QA/evaluator，不引入“有利于 MemBind”的新综合指标：

- official overall accuracy：300 道；
- equal-context macro accuracy：先算每个 context，再五个 context 等权；
- `knowledge-update`：45 道，current-state 相关 guard；
- `temporal-reasoning`：75 道，temporal guard；
- 其余四类逐类报告；
- invalid/missing QA 计入 completeness，但 score 为 null，不计作错误答案 0。

表述只允许为：“在该官方 QA 集及其预定义切片上未检测到质量下降/观察到差异。”不得写“QA 证明并发正确”。

### 6.4 Diagnostics

以下只用于机制解释与故障定位：

- submission/native-enter/publication queue delay；
- per-episode freshness/visibility latency；
- backlog time series、峰值与面积；
- B1 observed inversion count/depth；
- LLM request、embedding item、DB write 数；
- capture/replay/transport attempt 数；
- retries、timeouts、provider errors；
- nodes/edges/canonical graph diff；
- 若上游 evaluator 支持，Recall/MRR/nDCG evidence diagnostics。

任何 diagnostic 不得替代 primary、correctness gate 或 quality guard。尤其不能在看结果后挑一个 queue/backlog 指标作为 headline。

### 6.5 Work accounting

每方最少报告：

- expected/submitted/completed episode；
- native update enters；
- LLM logical requests 与 physical transport attempts；
- embedding batches/items；
- DB transaction/write calls（以当前 instrumentation 能可靠定义者为准）；
- V6 capture、consume、replay、transport-bypass；
- retry/timeout/failure；
- token/调用成本（若 provider 提供可靠 usage）。

Work accounting 的第一职责是排除“少做工作所以更快”。因调用级语义可能不同，除 fixed-work/lifecycle 外的差异先作为机制证据；只有 trace 链条能支持因果解释时才写机制结论。

### 6.6 正式主表

主表只使用同一 MAB campaign：

| Method | Semantic class | Valid contexts/blocks | Order/refinement | T_build | Speedup vs B0 | Durable goodput | QA overall | KU QA | Temporal QA |
|---|---|---:|---|---:|---:|---:|---:|---:|---:|
| B0 | ORDERED_REFERENCE |  | PASS / N/A |  | 1.00× |  |  |  |  |
| B1 | RELAXED_ORDER_REFERENCE |  | NOT_REQUIRED |  |  |  |  |  |  |
| V6 | ORDERED_REFINEMENT |  | PASS / PASS |  |  |  |  |  |  |

另设同数据集的 mechanism/work table；不是第二实验：

| Method | Queue p50/p95 | Freshness p50/p95 | Peak backlog | LLM logical | Transport | Embedding items | DB writes | Inversions |
|---|---:|---:|---:|---:|---:|---:|---:|---:|

### 6.7 统计单位与不确定性

- 独立 workload 单位是 5 个 context，不是 300 道 question；
- 三次 repeat 是运行噪声重复，不把 `5 × 3 = 15` 伪装成 15 个独立数据样本；
- 300 道 QA 同属 5 个 context，不能当 300 个 iid sample；
- 同时报告 official micro 与 equal-context macro；
- 不确定性采用 context-clustered 方法或对 5 个 context 的 paired effect 做透明 bootstrap/permutation，并展示所有 context-level effect；
- 样本很小时重点报告 effect size、区间和原始 context 点，不依赖显著性星号；
- 不得用 question-level naive bootstrap 制造狭窄区间。

三次 repeat 的 arm order 预先平衡：

```text
orders = [
  [B0, B1, V6],
  [B1, V6, B0],
  [V6, B0, B1]
]
order_index = (context_index + repeat_index) mod 3
```

每个 context 在 3 次 repeat 中，每个方法恰好各占一次第一、第二、第三位置。

---

## 7. 最小代码改动清单

文件名可按仓库现有布局微调，但职责不可合并得不可审计，也不可侵入冻结算法。

### 7.1 Dataset/workload 层

新增或整理：

- `workload_contract.py`
  - 方法无关的 `EpisodeInput`、manifest、canonical hash；
  - 输入次序、timestamp、body、arrival 的验证；
  - formal/diagnostic scope 标记。
- `mab_main_dataset.py`
  - 只加载完整 `longmemeval_s*` 5-context component；
  - construction 与 QA projection 明确分离；
  - gold-leak prevention；
  - 已知 provenance 缺陷标记；
  - 固定 6-question smoke manifest。

优先复用现有：

```text
mab_quality_v2_final_qa/src/mab_quality_v2_final_qa/
  contracts.py
  dataset_adapter.py
  live_adapters.py
  runner.py
  reducer.py
```

但必须移除“4-context 即 full authority”的正式路径。旧文件可保留为历史 artifact，不得被新 reducer 接受。

### 7.2 Runner 层

对现有 B0/B1 runner 做最小泛化：

- 接收 `WorkloadContract`，而非只接受旧 LongMemEval 特定输入；
- 保持 B0/B1 内部 schedule、arrival 和 worker 行为不变；
- 发出共同 lifecycle event；
- 返回 block artifact 引用，不在 runner 内计算论文结论。

对 V6：

- 添加类似 `run_v6_workload_async(workload, config)` 的薄入口；
- 旧 wrapper 保持兼容；
- 不修改 FrontierExecutor、provider/binder、capture/replay 或调度算法；
- 只把既有事件标准化映射到共同 trace schema。

### 7.3 QA 层

添加 `run_mab_qa_on_sealed_namespace(...)`：

- 输入 construction seal 与 frozen QA manifest；
- 开始前验证 namespace、context、method、repeat、workload hash；
- 禁止任何写操作；
- 逐题写 append-only result；
- 可从中断处恢复尚未完成的题，但不得覆盖已有不同结果；
- QA 前后写 read-only state proof；
- 输出 completeness、invalid reason、official score 与 type。

### 7.4 Orchestrator 层

新增一个 `three_way_campaign.py` 或等价入口，唯一职责是：

- 读取唯一 dataset authority；
- 生成 context workload；
- 按冻结 order 启动 B0/B1/V6；
- 每个 block 用 fresh namespace；
- construction seal 后才启动 QA；
- 维护 attempt ledger；
- 不在失败时自动切换数据、方法或 metric。

命令行只需要 scale/scope/arm/context/repeat 等工程参数。正式 mode 必须拒绝：

- dataset ID 非冻结值；
- context 子集不是完整 0..4；
- prefix workload；
- 未冻结 QA manifest；
- mixed commits/configs。

### 7.5 Validator/reducer 层

Reducer 必须：

- 只接受 dataset identity 精确匹配，不接受模糊前缀；
- 拒绝 4-context authority、mixed dataset、prefix、unsealed、incomplete work；
- 保留各 status 维度，不用一个 `valid=false` 抹平原因；
- 先生成 block-level report，再配对聚合；
- QA invalid 写 null；
- context 为 cluster，repeat 为 within-context noise；
- 生成主表、work table、context-level plot data 和 exclusion ledger。

---

## 8. 测试驱动开发清单

每个改动先写会失败的测试（RED），再做最小实现（GREEN），最后只做不改变行为的整理（REFACTOR）。不得先写一大段新 runner 再补测试。

### 8.1 Dataset authority tests

1. 精确得到 5 contexts；
2. session counts 为 `[111, 107, 116, 111, 110]`，总计 555；
3. 每 context 60 QA，总计 300；
4. 六类 QA 数量为 75/75/45/45/30/30；
5. 本地文件 hash 与 pinned revision 一致；
6. `0ddfec37_abs` 被保留并标记 `PARTIAL_GOLD_MAPPING`；
7. 任何 4-context inventory 在 formal mode 被拒绝；
8. 任何非 `longmemeval_s*` source 在 formal mode 被拒绝。

### 8.2 Gold leakage tests

1. construction body 中不存在 `has_answer`；
2. 不存在 question、reference answer、gold session ID；
3. 修改 gold metadata 不改变 workload hash；
4. 修改 role/content、timestamp 或 sequence 必须改变 workload hash；
5. `metadata.haystack_sessions` 无法被 construction loader 调用。

### 8.3 Workload identity tests

1. 不同 namespace/method/run id 的同一 workload hash 相同；
2. B0/B1/V6 manifest byte-canonical 内容相同；
3. 漏 episode、重复 episode、重排、正文变化都失败；
4. prefix manifest 只能标记 `ENGINEERING_DIAGNOSTIC`；
5. diagnostic artifact 无法进入 formal reducer。

### 8.4 Lifecycle/makespan tests

1. `T_build` 精确使用 FORMAL_START 与最后 PUBLICATION_DURABLE；
2. API return、task join 或 QA end 不能误作终点；
3. 缺 terminal event、duplicate terminal、未知 episode 失败；
4. seal 在 durable publication 前生成失败；
5. QA 在 construction seal 前启动失败；
6. QA 写入导致前后 state proof 不同则 quality block invalid。

### 8.5 Order validator tests

构造最小 trace 覆盖：

- 完全顺序 → B0/V6 PASS；
- 一个 overlap → 精确报告第一处 witness；
- 时间戳相等/缺失 → INVALID_TRACE；
- B1 同一 inversion → NOT_REQUIRED + diagnostic count；
- event 日志乱序但带 monotonic event index → validator 仍正确；
- 跨 context event 混入 → artifact invalid。

### 8.6 V6 binding tests

1. capture/consume 1:1 exact match → PASS；
2. missing capture；
3. missing consume；
4. duplicate consume；
5. ordinal/callsite mismatch；
6. request/prepared-response/native-request hash mismatch；
7. replay 外部 transport attempt；
8. 跨 episode 误绑定；
9. 全部失败案例输出最早、最小 witness。

### 8.7 QA/reducer tests

1. 300 道与每类数量验证；
2. 受影响 provenance 字段为 null，而非 0；
3. judge/provider 失败时 QA score null，performance status 不被清零；
4. 6-question smoke 不能混入正式 300 题统计；
5. 重复恢复不能覆盖已 seal 的题；
6. mixed config/commit/dataset/context subset 被拒绝；
7. micro 与 equal-context macro 正确；
8. uncertainty 按 context cluster，不按 question iid；
9. 三次 arm order 对每个 context 完全平衡。

---

## 9. Autoresearch 工作方式：失败后继续探索，而不是停机

### 9.1 每一轮固定循环

每轮只解决一个可证伪问题：

1. **Inspect**：查看最新 raw artifact 和最早异常 event；
2. **Hypothesize**：写一句可证伪假设；
3. **RED**：增加能稳定复现该问题的最小测试；
4. **Probe**：用当前数据 schema 的最小规模运行；
5. **Localize**：输出最早 witness，不只看聚合报错；
6. **Repair**：只改 evaluation plumbing；
7. **Regress**：目标测试 + 邻近测试 + 旧兼容测试；
8. **Scale once**：只上调一个规模档；
9. **Ledger**：记录假设、证据、改动、结果与下一步。

### 9.2 Gate 的正确用法

Gate 是“证据分类器”，不是“触发即结束”的死循环开关。

失败后按类型路由：

- 数据/schema 失败 → 缩到单条记录，核对 pinned authority 与官方 schema；
- endpoint/provider 失败 → 最小健康探针，区分 auth、quota、model、network、timeout；
- lifecycle 失败 → 2-session fixture 对齐事件边界；
- order 失败 → 提取最早相邻 witness，判断 instrumentation 误差还是方法真实违反；
- binding 失败 → 单 request/callsite/ordinal 回放；
- QA 失败 → 保留 construction seal，单题复现 judge/scorer；
- reducer 失败 → 用已 seal fixture 离线修复，不重跑昂贵构建。

真实方法违反不得通过改 validator 掩盖。记录负结果后，Agent 仍可完成其他方法、其他 context 和 diagnostics，使研究得到可解释结论。

### 9.3 防止无效循环

- 同一 failure fingerprint 第二次出现，必须增加新观测字段或提出不同假设；
- 连续两个假设被证伪，暂停扩大规模，查官方文档/顶会原文与上游实现；
- 连续失败时回退到更小规模，不回退到另一数据集；
- 不因结果不理想修改 primary metric、QA slice、context 或方法；
- 每次 scale-up 只增加一个变量：session 数、方法数、context 数或 repeat 数；
- 长任务定期写 heartbeat/progress artifact，但不得以 timeout 猜测失败。

### 9.4 Autoresearch ledger 模板

```json
{
  "cycle_id": "CYCLE-0001",
  "scope": "context0-prefix8-v6",
  "observation": "...",
  "hypothesis": "...",
  "falsifier": "...",
  "test_added": "...",
  "earliest_witness": "...",
  "change_scope": "evaluation-only",
  "result": "supported|rejected|inconclusive",
  "next_single_scale_step": "...",
  "method_or_protocol_changed": false
}
```

---

## 10. 逐级执行阶梯

### A0 — Authority 与代码冻结（无 live 调用）

完成条件：

- 记录实际 repo HEAD/status/env；
- dataset authority 5/555/300/type counts/hash 全通过；
- 明确第 5 context 已知 provenance 缺陷；
- 生成 frozen config、renderer hash、QA manifest；
- 写 experiment charter：唯一数据、三方法、指标角色、统计单位、正式规模。

若不通过：进入 adapter/authority autoresearch，不启动 live；不得改换 dataset。

### A1 — Offline adapter + reducer TDD

执行第 8 节所有无需 provider 的测试。重点确认：

- construction 从 common context 投影；
- QA/gold 与 construction 隔离；
- method-independent hash；
- formal reducer 拒绝 prefix/4-context/mixed config；
- status 分离与 null 传播正确。

### A2 — Provider-free 2-session fixture

使用与 MAB schema 相同的两个 session fixture，通过 fake native/provider 验证：

- B0/B1/V6 共同 lifecycle；
- makespan 边界；
- ordered/inversion 识别；
- V6 binding 双射；
- construction seal → read-only QA seal。

这里测试 orchestration 和 instrumentation，不测试模型质量。

### A3 — 真实 MAB context 0 的 1/2/8-session B0 探针

依次运行 1、2、8 session，且每档只在上一档能产出完整 artifact 后启动。检查：

- Graphiti/provider/DB 真实链路；
- session renderer 与 timestamp；
- durable publication event；
- work accounting 是否齐全；
- prefix8 的耗时是否适合 triad。

所有 artifact 标记 `ENGINEERING_DIAGNOSTIC`。

### A4 — 同一 prefix8 的 micro-triad

同一个 prefix8 manifest 依次运行 B0/B1/V6：

- workload/config hash 完全相同；
- B0/V6 order validator 有效；
- B1 inversion 能被观察但不被错误 gate；
- V6 binding/refinement 完整；
- 三方 T_build 使用同一边界。

这一步的目标是暴露三方接口不对称，不用于速度结论。

### A5 — 16/32-session 单次扩容

只有当 prefix8 无法暴露 backlog/并发或需要验证长链事件时才运行 16，然后 32。每次只上调一档；如果 16 已足够验证问题，不机械运行 32。

### A6 — 第一个完整 context triplet

使用 context 0 的完整 111 sessions：

1. 按预先确定 order 运行 B0；
2. 生成并验证 construction seal；
3. 在该 sealed graph 上跑 6-QA smoke；
4. smoke 链路有效后跑全部 60 QA；
5. 用 fresh namespace 对 B1、V6 重复同样流程；
6. reducer 生成第一张完整三方 block report。

若某方失败，不重新构建已成功方。定位失败方的最小 witness；恢复/重试仍用 fresh attempt namespace并保留失败 artifact。

### A7 — 第二个完整 context 资格运行

选固定 context index 1，不按结果挑选。目标是确认 context 0 的成功不是长度/内容偶然，并验证 arm order 的第二种排列。完成后必须能展示两组原始 paired effect，而不是只给 aggregate。

### A8 — Full-5 first pass

运行 5 contexts × 3 arms = 15 个 fresh block，每个 block 全量 QA：

- contexts 固定为 0..4；
- 每 context 各 60 QA；
- 不因第 5 context 的 provenance 缺陷跳过；
- 输出主表草案、每 context effect、exclusion ledger；
- 任何失败只影响明确 status，不删除整个 campaign。

First pass 用于发现跨 context 问题，不作为通过调参后只留成功结果的 publication set。

### A9 — Publication campaign

在 first pass 后只修 evaluation/instrumentation defect，并再次冻结 commit/config。随后运行：

```text
5 contexts × 3 methods × 3 fresh repeats = 45 blocks
45 blocks × 60 QA = 2700 QA result rows
```

要求：

- 全部 fresh namespace；
- arm order 按第 6.7 节平衡；
- 每个 attempt 留存；
- 失败的正式 block 若属于外部瞬时故障，整 block 在新 namespace 重试；
- 预定义 retry policy 内的所有 attempt 都进入 ledger；
- 不选择性删除慢 run、负 speedup 或 QA 下降 run。

---

## 11. 前几个小时的实际节奏

这是方向而非硬超时；真实 provider 延迟更长时继续观察并记录，不要一次性发起全量任务。

| 时间 | 目标 | 最大 live 规模 | 产物 |
|---|---|---:|---|
| 0–0.5 h | A0 authority/config freeze | 0 | authority、charter、hash |
| 0.5–1.5 h | A1 offline TDD | 0 | tests、fixtures、reducer proof |
| 1.5–2.5 h | A2 + A3 | 1/2/8 sessions | common trace、first live artifact |
| 2.5–4 h | A4/A5 | 8，必要时 16/32 | micro-triad、earliest witnesses |
| 4–6+ h | A6 | 一个完整 context × 3 | first full triplet、6→60 QA |
| 后续 | A8 | 15 blocks | full-5 first pass |
| 冻结后 | A9 | 45 blocks | publication artifacts |

Agent 的“几个小时目标”是**跑通一个完整 context 的三方构建 + 全部 60 QA + reducer**。若环境足够快，再推进 15-block first pass；不以牺牲证据完整性换取表面全量。

---

## 12. Artifact 与 seal 规范

### 12.1 每个 construction block 必备

```text
block/
  dataset_authority.json
  workload_manifest.jsonl
  workload_manifest.sha256
  frozen_config.json
  environment.json
  preflight.json
  raw_events.jsonl
  native_trace.jsonl
  transport_trace.jsonl
  request_identity.jsonl
  replay_binding.jsonl
  work_inventory.json
  lifecycle_validation.json
  order_validation.json
  refinement_validation.json
  graph_diagnostics.json
  metrics.json
  construction_seal.json
```

非 V6 的 `replay_binding/refinement` 明确为 N/A，不能假造空 PASS。

### 12.2 每个 QA overlay 必备

```text
qa/
  qa_manifest.jsonl
  qa_runtime_config.json
  graph_state_before.json
  qa_results.jsonl
  qa_failures.jsonl
  graph_state_after.json
  quality_summary.json
  qa_seal.json
```

### 12.3 Seal

Seal 至少包含：

- block/campaign identity；
- repo commit 与 dirty diff hash；
- dataset/workload/config hashes；
- namespace 和 method；
- context/repeat/attempt；
- 所有成员文件 hash；
- status；
- sealed_at；
- parent construction seal（QA seal）。

Reducer 只读取 seal 引用的文件，忽略目录里未被 seal 的临时结果。

### 12.4 Campaign ledger

Campaign 维护 append-only ledger：

- planned block；
- attempt id；
- namespace；
- start/end/heartbeat；
- completion或 failure class；
- retry relation；
- seal path/hash；
- inclusion status 和原因。

Agent 可以生成机器 artifact 和最终结果文件，但不得再生成第二份 workplan。

---

## 13. 现有 artifacts 能承担什么证据

| 现有资产 | 可以承担 | 不可以承担 |
|---|---|---|
| 旧 LongMemEval B0/B1 runs | runner 语义、事件字段和回归参考 | 新主表数值、MAB 速度/QA 结论 |
| MemOps artifacts | 说明“最终 QA 可能看不见状态操作错误”的动机、测试灵感 | 本轮第二 correctness dataset、正式指标 |
| V5/V6 artifacts | binder/replay 字段、机制回归、兼容测试 | 当前环境的 headline speedup 或完整 correctness |
| 现有 MAB data/adapter/tests | 数据 authority 与 QA 适配实现起点 | 未经新 seal 的正式结果 |
| 现有 MAB live attempts | endpoint/problem fingerprint、故障定位 | 论文主结果；现有尝试未形成完整 final result |
| `DECLARED_4_CONTEXT_INVENTORY_20260819.json` | 记录历史 authority 决策及已知缺陷 | 正式 full dataset 定义 |

**只有 fresh、完整 5-context、同一冻结配置下的 B0/B1/V6 artifacts 能进入本轮主实验结论。**

---

## 14. 故障处理与恢复规则

### 14.1 Dataset/authority

- hash/count 不符：核对 pinned revision、下载缓存和解析；不自动用最新 floating revision；
- 第 5 context gold provenance 缺陷：按第 2.4 节标记，不删除；
- 发现新的局部 gold 映射问题：answer QA 照常，证据诊断 null + ledger；
- common construction context 真正缺失：保留证据，查官方 issue/source；这是少数可暂停正式 campaign 的 authority 问题，但不能偷偷换 dataset。

### 14.2 Provider/endpoint

- 先跑最小健康探针区分网络、鉴权、quota、model name 和 payload；
- 重试沿用预定义 backoff/上限；
- transient formal failure 整 block 新 namespace 重试；
- 不从失败中途继续构建并与 clean block 混合，除非 runner 的恢复语义已事先测试且 artifact 明确记录；
- 不因 endpoint 慢就降低某一方法工作量。

### 14.3 超长 session/context

- 先定位 tokenizer/provider/Graphiti 的真实限制；
- 可修正 adapter 的无损序列化 bug；
- 不得人工拆 session、删除消息、摘要输入或拼接 context；
- 若官方单 session 超出当前系统不可处理的硬限制，形成明确 scientific limitation，而不是隐式改 workload。

### 14.4 Order/refinement failure

- 输出最早 witness 与相关 raw trace；
- 先用 fixture 排除 clock/event/schema bug；
- 若确认方法真实违反，记录 FAIL，继续收集其他 context、性能与 QA，以得到可解释负结果；
- 不修改冻结 V6 算法来“通过 gate”，除非用户另开方法研发阶段；那将是新版本而非本计划内 v1.3/V6 主实验。

### 14.5 QA failure

- construction seal 保持有效；
- 单题/单 judge 最小复现；
- QA invalid 写 null；
- 恢复只补未完成题并保留失败 attempt；
- 不因 QA 相同或下降回头选择数据/题型；
- B1 QA 与 B0/V6 相同是完全合法结果，不改变其 relaxed-order 标签。

### 14.6 性能负结果

MemBind 比 B0 慢、speedup 小、不同 context 方向不一，都属于正式结果。Agent 应用 queue/work trace 解释瓶颈，但不得：

- 更换 primary metric；
- 删除慢 context/repeat；
- 用 goodput 与 makespan重复计票；
- 把 B1 速度当作 MemBind 必须达到的上界；
- 修改 frozen method 后仍称同一实验。

---

## 15. 完成状态，而不是单一“过/停”按钮

Campaign 使用累计里程碑：

- `DATASET_FROZEN`：完整 5-context authority 与 tests 完成；
- `MICRO_TRIAD_COMPLETE`：同一真实 prefix 的三方 artifact/validator 打通；
- `FIRST_FULL_TRIPLET_COMPLETE`：一个完整 context × 三方 × 60 QA；
- `MAIN_PASS_COMPLETE`：5 contexts × 3 方法 = 15 blocks；
- `PUBLICATION_COMPLETE`：5 × 3 × 3 = 45 blocks，统计与表格完成；
- `SCIENTIFIC_NEGATIVE_COMPLETE`：即使 V6 correctness/performance/quality 假设未获支持，证据完整且可复现。

只有以下情形允许暂停等待外部决定：

- 数据许可或官方 authority 无法解决；
- provider/数据库在多个不同假设与最小探针后仍客观不可用；
- 发现必须修改 frozen method/protocol 才能继续；
- 资源需求明显超出用户授权。

普通测试失败、gate 失败、负 speedup、QA 无差异都不是停止条件。

---

## 16. Agent 每阶段必须回答的问题

### Dataset

- 是否确实是 pinned `longmemeval_s*` 完整 5 contexts？
- 是否构建只读取 common public context？
- 是否每 session 恰好一个 EpisodeInput，顺序/timestamp/body 未改变？
- 是否任何 QA/gold 信息泄漏到 construction？

### Fairness

- 三方 workload/config hash 是否相同？
- namespace 是否 fresh？
- makespan 是否同起点、同 durable 终点？
- 是否存在方法特有的 instrumentation overhead 不对称？

### Correctness

- fixed work 是否恰好完成？
- B0/V6 order predicate 是否通过？
- V6 capture/replay 是否一一 exact bind 且无 replay transport？
- 若失败，最早 witness 是什么？

### Quality

- QA 是否只在 seal 后运行且只读？
- 300 道及六类是否完整？
- invalid 是否是 null 而非 0？
- 是否避免把 QA 无差异写成 correctness？

### Statistics/claims

- 是否按 context 配对和聚类？
- 是否展示所有 context effect 与 failed attempts？
- 是否把 repeats 当噪声重复而非独立 n？
- 是否没有做 dataset/context/metric shopping？

---

## 17. 允许与禁止的论文表述

### 17.1 允许（以实际结果为条件）

- “在冻结的 MAB Accurate Retrieval `longmemeval_s*` 完整 5-context workload 上，MemBind V6 满足预定义 ordered/refinement contracts。”
- “相对 Native Serial，MemBind 在共同 durable-completion makespan 上取得 X 的配对效应。”
- “Naive Async 作为 relaxed-order reference 展示了 Y 的性能及 Z 个 observed inversions。”
- “在同一 sealed graph 的 300 道官方 QA、45 道 knowledge-update 与 75 道 temporal-reasoning 问题上，未检测到/观察到质量差异。”
- “Work-accounting/queue trace 与该性能变化一致”，前提是 trace 链完整。

### 17.2 禁止

- “QA 相同证明 MemBind/Naive Async 正确”；
- “B1 是性能上界”；
- “B1 有 inversion，所以答案一定错误”；
- “B0 与 V6 graph 完全语义等价”，除非另有足够证明；
- “300 道 QA 是 300 个独立样本”；
- “4/5 contexts 是完整官方数据集”；
- “我们评测了完整 MemoryAgentBench suite”；
- “结果推广到所有 memory system/所有 workload”；
- 把旧 LongMemEval、MemOps、V5/V6 数字混进新主表；
- 跨数据集拼接一条 headline 证据链。

---

## 18. 文献依据与 Agent 调研入口

以下来源用于冻结评价原则；Agent 遇到新问题时优先读原文与官方实现，不凭印象发明指标。

### Memory benchmark / Graphiti

- MemoryAgentBench, ICLR 2026：<https://arxiv.org/html/2507.05257>
- 官方仓库：<https://github.com/HUST-AI-HYZ/MemoryAgentBench>
- 官方数据：<https://huggingface.co/datasets/ai-hyz/MemoryAgentBench>
- Graphiti LongMemEval graph-building evaluation：<https://github.com/getzep/graphiti/blob/main/tests/evals/eval_e2e_graph_building.py>
- LongMemEval, ICLR 2025：<https://arxiv.org/abs/2410.10813>
- LoCoMo, ACL 2024：<https://aclanthology.org/2024.acl-long.747/>
- PersonaMem, COLM 2025：<https://arxiv.org/abs/2504.14225>
- AgentMemBench 官方仓库：<https://github.com/awslabs/agent-memory-benchmark>
- MemOps 官方仓库：<https://github.com/MemTensor/MemOps>

### Ordered/stateful/deterministic evaluation

- [Calvin（SIGMOD 2012）](https://dl.acm.org/doi/10.1145/2213836.2213838)：用预定顺序/执行契约与吞吐分别论证，支持“语义 gate 与性能分开”。
- [Aria（PVLDB 2020）](https://www.vldb.org/pvldb/vol13/p2047-lu.pdf)：把 deterministic concurrency-control 契约与性能伸缩分开验证。
- [Elle（OSDI 2020）](https://www.usenix.org/conference/osdi20/presentation/kingsbury)：强调从执行 history 检查语义，而不是从最终应用答案猜测正确性。
- [MillWheel（PVLDB 2013）](https://www.vldb.org/pvldb/vol6/p1033-akidau.pdf)：强调 precise lifecycle、durability/low-watermark 与端到端延迟边界。

这些系统并不要求采用本项目自创的“正确性分数”。本计划因此采用：冻结契约 → 完整 trace/history → 机械 validator → 性能 endpoint → 独立质量 guard 的证据结构。

---

## 19. 最终交付清单

Agent 最终必须交付：

1. 完整 5-context dataset authority 与 frozen manifests；
2. B0/B1/V6 共同 lifecycle 和 method-specific trace；
3. 45 个正式 construction block 及 seals；
4. 2700 条正式 QA-result row 及 seals；
5. block/status/exclusion/retry ledger；
6. correctness/refinement validator 输出和最早 witnesses；
7. primary main table；
8. 同一数据集的 mechanism/work table；
9. context-level paired effects 与 context-clustered uncertainty；
10. 一份结论说明，严格遵守第 17 节 claim boundary。

最终验收的核心不是“V6 必须赢”，而是：**在完全相同的一份 MAB workload 上，三方工作的公平性可验证，ordered/refinement correctness 有 trace 证据，性能使用共同 durable makespan，QA 只承担官方端到端质量守卫；无论结果正负，都能形成可复现、可审稿的顶会级证据链。**
