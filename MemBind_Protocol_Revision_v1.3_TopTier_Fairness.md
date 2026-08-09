# MemBind Protocol Revision v1.3
## 面向系统顶会公平性与可复现性的 Host Qualification + Correctness + Performance 协议

**文档性质**：经独立逻辑审计、代码清点和公开来源核验后采纳的协议修订说明；规范执行合同见 `MemBind_CURRENT_VALIDATION_PLAN_v1.3.md`  
**目标**：解决 `v3_smoke_002_m0_structured_output_failure` 暴露出的协议层问题，同时保持 MemBind 核心研究问题、控制变量、公平性与可证伪性。  
**适用时间**：2026-08-09  
**核心原则**：**先证明 host stack 可用，再冻结；先冻结，再比较 runtime。**

**机器状态**：`current-validation-v1.3` / `h0_protocol_accepted_harness_not_implemented` /
`h0_offline_tdd_and_harness_only` / `live_h0_candidate_authorized=false` /
`v3_smoke_003_retired=true`。本文件不授权 live H0。

---

# 0. 执行摘要

当前 V3 失败不应被解释为 MemBind mechanism failure。

当前事实是：

```text
Graphiti v0.29.3
    × Qwen3-32B-FP8
    × vLLM 0.26.0
    × 当前自定义 structured-output request path
    × temperature=0 / top_p=1
    × 2048 -> 8192 completion budget
    × json_schema
```

在 M0 的实体抽取阶段已经无法可靠完成结构化输出；M2、Neo4j state-dependent Bind、并发调度等核心机制尚未进入可评测阶段。

因此当前真正缺失的是：

> **Pre-freeze Host Stack Qualification（宿主栈资格测试）**

而不是继续增加第 N 个 V3 forensic probe。

当前协议的问题是执行顺序：

```text
旧协议：
选定 host/model/provider 配置
    ↓
立即冻结
    ↓
建立 correctness oracle
    ↓
正式 smoke
    ↓
才发现 host stack 本身不兼容
    ↓
协议又禁止修改 host 配置
    ↓
死锁
```

建议修改为：

```text
新协议：
H0 Host Stack Qualification
    ↓ PASS
Freeze Qualified Host Stack
    ↓
Correctness Oracle Qualification
    ↓
M0 -> M2 Correctness
    ↓
Native Characterization
    ↓
Baseline Tuning
    ↓
Formal Performance
```

这里的 H0 **不是为了让 MemBind 获得优势而调参**，而是为了确定一个所有方法共同使用、能够承载 Graphiti 原生语义的 host configuration。

这符合已有系统论文的公平性实践：

- ContextPilot（MLSys 2026）明确对 baseline 参数做调优，并尽量与各 baseline 论文的最佳设置对齐，而不是故意使用弱配置。
- Pie（SOSP 2025）为了隔离架构差异，让 Pie/vLLM/SGLang 使用相同 FlashInfer backend，并在 baseline 中复现相同 high-level workflow、做 best-effort 优化。
- Agentix（NSDI 2026）不仅比较默认 vLLM，还加入 `vLLM-opt` 和 MLFQ，避免把“默认配置差”误认为新 runtime abstraction 的收益。
- Agent Memory Characterization（arXiv preprint, 2026）面对 local model / memory-system interface 不兼容时，允许**最小、显式、对任务必要的 compatibility adaptations**；例如 A-Mem 在 local backbone 下改用 plain-text prompt variant，而不是坚持原 JSON-schema path 直到实验失败。公开证据当前不能独立支持 IISWC venue/acceptance 状态，因此不作该声明。

**无法保证任何审稿人“零质疑”**，但下面的协议把最典型的 fairness / cherry-picking / baseline validity / reproducibility 风险都前置控制，并留下可审计 artifact。

## 0.1 审计后的裁决矩阵

| 意见 | 裁决 | 进入 v1.3 的必要修改 |
|---|---|---|
| 把当前失败归为 pre-freeze host compatibility failure | 修改后采纳 | 明确这是看到 Q0 失败后的追溯性修订；历史 blocker ID 永久保留 |
| 新增 H0，首个通过候选立即冻结 | 修改后采纳 | 当前使用内容寻址的 shared-base + delta specs；live 前解析为完整 manifest；不得测完所有候选后比较性能 |
| Q1→Q2→Q3 | 修改后采纳 | context-safe effective budget；Q2 必须观测 `top_k/min_p` 入 payload；Q3 必须显式启用并注入 effective shim schema |
| `c6853660` 作 H0 canary | 拒绝 | 它属于旧 evaluation 且已被反复调试；只作历史 Q0 证据并 exposure quarantine |
| 3/3 canary 证明可靠 | 拒绝该推论 | 仅作 bounded engineering fail-fast gate，不作 production reliability 统计声明 |
| parse/schema valid 即 H0 PASS | 拒绝 | 必须增加 calibration-only semantic utility gate，拒绝合法但空/常量/退化输出 |
| 新 oracle namespace | 采纳 | namespace 内容寻址并绑定 qualified host manifest；H0 不得写 formal oracle |
| correctness replay / performance live | 强采纳 | correctness 隔离模型采样；performance 保留真实 model/queue/batching path |
| U0/D0 双基线 | 修改后采纳 | 定义 exact adapter boundary 与预注册 representativeness guardrail |
| M1/M2 concurrency tuning | 修改后采纳 | 先判 quality-feasible，再按冻结目标选择；C8 称 `iso-cap` 而非 `iso-resource` |
| 8 instances × 2 repeats | Pilot 采纳 | 只支撑内部 GO/NO-GO，不支撑 paper-level P99/tail claim |
| P1-P4 扩展 | roadmap 保留、当前暂缓 | V7 GO 前全部 `authorized=false` |
| 当前宣称通用 Agent Memory Runtime | 拒绝 | 第二 architecture 之前只声称 Graphiti/temporal-KG runtime result |

---

# 1. 当前 V3 blocker 的正式重新分类

历史 blocker ID 永久保留为：

```text
v3_smoke_002_m0_structured_output_failure
```

协议层 classification 另记为：

```text
late_discovered_pre_freeze_host_compatibility_failure
```

而不是：

```text
MemBind correctness failure
Graphiti semantic failure
M2 failure
```

理由：

1. 失败发生于 M0 structured entity extraction；
2. M2 尚未执行；
3. 无 Neo4j/embedding 调用参与该次 blocker；
4. 8/8 HTTP 请求成功，服务本身可用；
5. 2048 和 8192 输出均以 `finish_reason=length` 截断；
6. 重启后的 fresh runtime 可逐字节复现；
7. 因此目前只能证明冻结 provider path 无法承载该代表性 Graphiti structured request。

这是观察 Q0 失败后作出的**追溯性 protocol correction**，不得写成原 v1.2 已经
预注册 H0。`historical_blocker=v3_smoke_002_m0_structured_output_failure` 与
`current_blocker=late_discovered_pre_freeze_host_compatibility_failure` 同时存在，
分别表示不可变事件身份与当前方法学分类。

**旧 V3 artifact 必须保留，禁止覆盖、删除或把 raw failure 改写成成功。**

它在论文/内部报告中的意义是：

> “原始预冻结配置未通过 host compatibility qualification，因此不进入正式实验。”

---

# 2. 文献与 upstream 对当前问题的直接启示

## 2.1 Agent Memory Characterization：允许最小 compatibility adaptation

该工作不是假设：

> 每个 memory system 原始 prompt/interface × 任意 local model 都天然兼容。

它明确指出，为避免把 prompt-format mismatch 当成 memory quality 问题，会进行最小 task/interface adaptation：

- A-Mem local backbone 使用 plain-text prompt variant；
- API backbone 保留 JSON-schema prompt；
- Letta local model 限制重复 tool calls 并轻调 system prompt；
- Mem0 仅在需要时做 task-aware extractor adaptation。

因此对于 MemBind：

> **先做共享 host compatibility qualification，再冻结正式配置，是合理且有同行先例的。**

不合理的是：

> 为了“严格”，先冻结一个未经 qualification 的 local structured-output path，然后在发现不兼容后禁止任何 correction。

---

## 2.2 Graphiti upstream：local provider structured output 本来就存在兼容性分支

Graphiti `OpenAIGenericClient` 在 pinned v0.29.3 / commit `021d3a5` 中：

```text
default max_tokens = 16384
structured_output_mode:
    json_schema
    json_object
```

其中：

- `json_schema`：请求 provider 执行 schema adherence；在 vLLM/llama.cpp 上意图为
  constrained decoding，但 Graphiti 对 OpenAI proper 未设置 `strict:true`，不能
  无条件宣称所有 provider 都执行严格 constrained decoding；
- `json_object`：Graphiti 将 Pydantic schema 注入 prompt，由 provider 返回普通 JSON。

Graphiti pinned README at commit `021d3a5` 也明确说明：

> 某些 local/OpenAI-compatible provider 接受 `json_schema` 请求但不能可靠执行时，可使用 `json_object` fallback。

因此：

```text
json_object
```

不是为了 MemBind 临时发明的 hack，而是 Graphiti 官方支持的 compatibility path。
但它是**显式配置候选**，Graphiti 不会在 `json_schema` 失败后自动切换 mode。

---

## 2.3 当前 MemBind client 已经偏离 upstream Graphiti path

当前 `membind-validation/src/graphiti_native.py` 中：

```text
QwenVLLMClient
```

重写了 `_generate_response()`，并额外固定：

```yaml
temperature: 0.0
top_p: 1.0
seed: 20260806
max_tokens: 2048
overflow_retry: 8192
enable_thinking: false
structured_output_mode: json_schema
```

还对 schema 做：

```text
episode_indices == [0]
```

约束。

这些修改全部统一用于 M0/M1/M2，所以**方法间公平性目前没有直接被破坏**；但它会产生另一个 reviewer 风险：

> “你的所谓 Graphiti baseline 是否仍代表 upstream Graphiti？”

因此 v1.3 必须把：

- **within-study causal fairness**
- **upstream representativeness**

分成两个独立问题控制。

---

## 2.4 Qwen3 官方 non-thinking recommendation 与当前配置不同

Qwen3-32B-FP8 官方 model card 对：

```text
enable_thinking=False
```

推荐：

```yaml
temperature: 0.7
top_p: 0.8
top_k: 20
min_p: 0
```

当前：

```yaml
temperature: 0.0
top_p: 1.0
```

不是官方推荐 operating point。

这**不能证明**当前截断一定由 temperature=0 导致。

但这足以说明：

> decoding policy 应该属于 **Host Qualification 的候选配置维度**，而不应在 compatibility qualification 之前永久冻结。

---

## 2.5 vLLM `structured_outputs.backend=auto` 不适合在根因未明时充当不可变事实

vLLM 文档明确说明：

```text
backend="auto"
```

会根据 request 和 backend library 支持情况做 opinionated choice，行为可能随 release 改变。

因此正式可复现实验中：

- 如果最终使用 schema-constrained decoding，应该记录并尽量冻结明确 backend；
- 如果保持 `auto`，至少必须冻结 vLLM version、完整 launch config，并明确报告“backend selection is delegated to vLLM auto”。

**不能从 `response_format=json_schema` 推断实际 backend。**

---

# 3. Protocol v1.3 的第一原则：Qualification 与 Evaluation 必须严格分离

## 3.1 Qualification 可以调什么

H0 只允许调整**所有方法共享的 host compatibility 参数**：

```text
structured_output_mode
decoding configuration
completion budget
structured-output backend（如服务侧允许）
最小 compatibility shim
```

禁止：

```text
只给 M2 改参数
只给 M2 改 prompt
只给 M2 改 schema
只给 M2 增加 retry
根据 M2 speedup 反向选择 provider config
根据 evaluation result 选择 host config
```

---

## 3.2 Evaluation 一旦开始就重新冻结

H0 PASS 后产生：

```text
artifacts/environment/qualified_host_stack_v1.3.json
```

至少绑定：

```text
Graphiti commit
construction model revision
vLLM version
vLLM launch-config hash
structured_output_mode
structured backend / auto declaration
temperature
top_p
top_k（Q2/Q3 必须观测为实际发送）
min_p（Q2/Q3 必须观测为实际发送）
seed policy
enable_thinking
requested/effective max_tokens policy
retry policy
schema hash
compatibility shim hash
HTTP client config hash
```

之后：

> M0 / M1 / M2 必须全部使用同一个 qualified stack。

任何修改都创建新 protocol version，不允许覆盖 v1.3 结果。

---

# 4. 新增 H0：Host Stack Qualification

## 4.1 H0 的目标

只回答：

> **Graphiti v0.29.3 能否通过一个有 upstream/vendor 依据的 local Qwen3/vLLM interface configuration，可靠完成代表性 construction workload？**

H0 不回答：

- MemBind 是否更快；
- M2 是否正确；
- M1 是否会 divergence；
- 哪个配置速度最快。

因此 H0 **不得用性能选择配置**。

---

# 5. H0 采用“预注册顺序 fallback”，禁止 configuration fishing

为了避免：

> “试 20 组参数，挑一个对 Ours 最有利的”

采用**first-passing sequential qualification**。

历史配置记为 `Q0`，已经失败，无需再次运行：

```text
Q0 Historical Frozen Control
json_schema
temperature=0
top_p=1
2048 -> 8192
current compatibility shim
backend=auto
=> FAILED
```

之后按预注册顺序测试。

---

## Q1：恢复 pinned Graphiti constructor completion cap

只优先修正当前最明显偏离 Graphiti upstream 的 completion budget：

```yaml
structured_output_mode: json_schema
temperature: 0.0
top_p: 1.0
requested_max_tokens: 16384
effective_max_tokens: min(16384, context_limit - prompt_tokens - 32)
enable_thinking: false
```

除内容寻址 delta spec 中声明的 completion-budget policy 外，Q1-Q3
共享的 host-request configuration projection 必须逐字段相同。Q0 来自旧
qualification wrapper，Q1 来自 v1.3 H0 harness，所以两者不是严格 causal
A/B，不得将 Q1 PASS 单因归于 16K budget。live 前必须把 shared base
spec 的 client/prompt/schema/HTTP/retry 等未决 hash 全部解析，并组装为
完整 runnable manifest；删除“尽量保持”这种不可执行表述。

理由：

- pinned Graphiti `OpenAIGenericClient` constructor 默认 `max_tokens=16384`；
- 当前自定义 `2048 -> 8192` 小于 upstream local-model default；
- 当前 failure 恰好是 `finish_reason=length`。

**注意**：

40960 context 不能为所有已知 prompt 提供完整 16K：历史 32757-token prompt 在 32
token safety 后最多只剩 8171。因此每次都必须同时记录 requested 与 effective
budget，且不得声称“所有请求恢复完整 16K”。如果 effective budget 不足或 response
仍以 `finish_reason=length` 结束，不能继续无限增大 completion budget。

这时判定 Q1 fail，进入 Q2。

---

## Q2：采用 Qwen 官方 non-thinking decoding

```yaml
structured_output_mode: json_schema
temperature: 0.7
top_p: 0.8
top_k: 20
min_p: 0
requested_max_tokens: 16384
enable_thinking: false
```

理由：

> 这是 model vendor 对 non-thinking Qwen3 的公开推荐 operating point。

Qwen model card 的原词是 `suggest`；它没有证明该 sampling point 能修复本次
structured truncation。若当前 client 无法传递 `top_k/min_p`：

- 记录 `not_sent_by_client_contract`；
- Q2 判为 ineligible，而不是静默形成只改 temperature/top_p 的隐藏候选；
- 先用 TDD 实现最薄 payload adapter，并观测 sanitized request payload；
- 不得同时进行大规模 client refactor。

---

## Q3：Graphiti 官方 `json_object` fallback

如果 Q2 仍不能稳定完成：

```yaml
structured_output_mode: json_object
Qwen decoding: 与 Q2 完全相同且 top_k/min_p 已观测入 payload
requested_max_tokens: 16384
enable_thinking: false
```

由 upstream Graphiti：

```text
effective shim schema injection into prompt
+
JSON object response
```

完成 structured extraction。

理由：

> Graphiti pinned source/README 将 `json_object` 作为 local/OpenAI-compatible provider
> 对 `json_schema` 不可靠时可显式选择的 fallback；它不是失败后的 automatic switch。

Q3 只有在 `schema_injected_sha256 == schema_effective_sha256` 时 eligible。当前代码
的 `[0]` shim 只改 response-format schema，而 upstream `json_object` 注入原始
Pydantic schema；因此先写 RED contract 并完成最小修复，不能直接运行 Q3。

如果 Q3 PASS：

- 正式实验统一使用 Q3；
- 不能宣称“vLLM native json_schema constrained decoding”是正式实验条件；
- 论文写成“Graphiti-supported JSON-object compatibility mode”。

---

# 6. Schema 修改的处理规则

当前已有：

```text
episode_indices -> exactly [0]
```

的 schema shim。

Graphiti upstream prompt 本身说明：

> single episode processing 时 `episode_indices` 应为 `[0]`。

因此该 shim具有明显语义依据，但仍然属于：

> **compatibility adaptation，而不是 untouched upstream。**

v1.3 规定：

1. H0 必须记录 `schema_upstream_sha256`、`schema_effective_sha256`；Q3 还记录
   `schema_injected_sha256`，并要求 injected 等于 effective；
2. 该 shim 对 M0/M1/M2 一致；
3. 禁止为了当前 failure 给 `extracted_entities` 人为添加拍脑袋的 `maxItems=K`；
4. 如果最终必须增加任何会限制实体/事实数量的 bound 才能运行，则必须：
   - 单独 protocol deviation；
   - 给出 dataset-independent K 的依据；
   - 做 quality/equivalence guardrail；
   - 不得将其悄悄称为“纯 runtime change”。

**首选正式配置**：

> 不新增改变 Graphiti semantic capacity 的 schema bound。

---

# 7. Host Qualification workload：不能只用那个已知失败 request

已知失败 request `c6853660` 属旧 evaluation 且已被反复调试，只能保留为 Q0 的
historical regression evidence，不能参与 Q1-Q3 选择。

否则容易被质疑：

> 针对一个失败 case 过拟合配置。

建议三阶段：

## H0-A：Regression canary

固定 calibration canary：

```text
question: 07741c45
source_sequence: 0
```

每个候选配置做：

```text
3 logical trials
```

要求 3/3：

```text
HTTP 200
finish_reason != length
JSON parse success
Pydantic validation success
no server error
zero candidate-induced retry
semantic canary invariants pass
```

一个 logical trial 是一次 public Graphiti invocation；每个底层 HTTP attempt 都有
独立 ID，retry 仍属于原 trial，不能冒充独立成功样本。三个 repeated
trials 全部使用固定 `seed=20260806`，以保持 Q0→Q3 request seed policy
一致；它们不是 statistically independent samples。
3/3 只是 bounded engineering gate，不支持 99.x% reliability claim。

---

## H0-B：完整 calibration history

从**原协议已冻结的 calibration set**选择预先固定的 1 个完整 LongMemEval instance。

固定为 `07741c45`。

必须跑完整 M0 ingestion。

要求：

```text
100% structured calls parse + schema valid
no context overflow
no OOM
no finish_reason=length
no unexplained live fallback
calibration-only semantic utility guardrail pass
```

---

## H0-C：其余三个 calibration instances

对其余三个 frozen calibration instances 各执行一次完整 M0；与 H0-B 聚合为四个，
不重复选择性保留 H0-B 的成功 run。

要求：

```text
structured parse success = 100%
successful full histories = 4/4
episode/source coverage = 100%
valid but empty/default-only output = failure
Evidence Recall@10 > 0 for every calibration history
```

在任何 candidate request 前，必须从 calibration raw input 建立内容寻址的
`h0_semantic_guardrail_manifest_v1_3.json`。其中的小型人工 canary gold 与
expected-nonempty call index 必须在看 candidate 输出前冻结。至少拒绝：应非空却空、
blank entity name、`episode_indices != [0]`、重复 normalized entity、以及所有 call
都返回同一常量/schema-default object。parse + Pydantic valid 只是必要条件，不是
充分条件；否则 Q3 可能让 degenerate baseline “稳定成功”，而后续 replay 无法发现
host-mode quality 已经退化。

H0-C PASS 后：

> 立即冻结第一个通过全部 H0-A/B/C 的候选配置。

不得继续测试后面的候选配置去找更快的配置。

这就是避免 cherry-picking 的关键。

---

# 8. Qualification 与正式 Evaluation 数据隔离

H0：

```text
只能用于 calibration / compatibility
```

Formal evaluation 使用 exposure-clean v1.3 split：

```text
artifacts/dataset/frozen_split_v1_3.json
evaluation excludes quarantined c6853660 and adds next hash-ranked unseen 08e075c7
generator = src/dataset_v1_3.py
generator policy = legacy split hash validation + exposure-only quarantine + original SHA256 ID order
```

旧 `src/dataset.py` 不修改，以保留 v1.2 split 中的历史脚本 hash。v1.3 manifest
必须记录独立 generator 的 version 与 SHA256，并能从原始数据和 immutable
legacy split 逐字段重放。人工编辑出正确 ID 列表不等于可复现 split。

禁止：

- 看 formal evaluation 结果后回到 H0 换配置；
- 因某个 evaluation sample failure 改 sampling/schema；
- 删除难 case。

如果 formal evaluation 中再次发生 host-level structured failure：

```text
classification = infrastructure_or_host_stack_failure
```

进入预注册失败规则，而不是再次 tuning。

---

# 9. H0 结束后的 cache/oracle 处理

**当前旧 provider config 产生的 model oracle 不能继续作为新正式 correctness oracle。**

因为 cache key/语义环境已经变化。

H0 PASS 后必须：

```text
oracle namespace = sha256(
  protocol_version
  + qualified_host_manifest_sha256
  + graphiti_commit
  + effective_prompt_schema_hashes
  + deterministic_adapter_hash
  + embedding_deployment_fingerprint
)
new LLM oracle namespace
new embedding oracle namespace
```

H0 不得写 formal oracle；旧 records 可以底层去重保存，但禁止跨 namespace lookup，
也禁止 mutable `latest` alias。

旧 V2/V3 artifact：

```text
retained historical diagnostic evidence
```

不可复用为正式结果。

---

# 10. V2-R：Correctness Oracle Requalification

当前 capture/replay 思路应当保留。

这是现有协议中最值得保留的设计之一。

重新执行：

```text
Qualified M0 capture
    ↓
Qualified M0 read-only replay
```

验证：

```text
same qualified provider manifest
0 live fallback
fresh Neo4j
same prompt/cache key
same embedding key
```

然后再进入：

```text
M0 capture
    ↓
M2 read-only replay
```

---

# 11. 为什么 correctness 继续使用 Capture/Replay，而不是强求 temperature=0

Correctness 的目标是：

> 判断 execution organization 是否改变 semantic state。

LLM/embedding 本身不是本实验研究对象。

因此正确控制方式是：

```text
M0:
sample once from qualified model stack
    ↓
capture exact model-derived outputs

M1/M2:
read-only replay
    ↓
live DB / candidate search / resolution state
```

这样把模型随机性排除在 correctness causal comparison 之外。

所以：

> **不再要求 Qwen 必须通过 temperature=0 提供 bitwise determinism。**

这与当前协议已经采用的 model oracle 思路一致，只是把 host configuration 先 qualification。

---

# 12. Performance lane：仍然必须 live model，禁止 response replay

Performance lane 保持：

```text
M0 / M1 / M2
    ↓
same live construction endpoint
same live embedding endpoint
same qualified model config
same client
same HTTP pool
same graph DB config
same global resource cap
```

禁止 response cache。

因为：

> MemBind 的核心收益正来自重叠真实 expensive construction calls。

---

# 13. Performance lane 的 live stochastic drift 处理

如果 qualified Qwen config 使用 sampling：

不同 schedule 可能改变 batching / floating-point trajectory / response sampling。

因此按 method/instance/repeat/prompt_name 必须记录：

```text
prompt_hash
response_hash
input_tokens
output_tokens
finish_reason
logical_call_count
HTTP_attempt_count
structured_retry_count
embedding_item_count
```

并报告：

```text
live_response_divergence_rate
live_work_volume_ratio
```

`live_response_divergence_rate` 只作 descriptive diagnostic，不允许用事后定义的
“显著差异”改变结论。work-equivalence 预注册为相对 D0 的双侧 guardrail：LLM
logical-call ledger 必须一致，input/output token 与 embedding-item ratio 均落在
`[0.95,1.05]`。低于或高于该范围都标记 `performance_confounded=true`。

规则：

```text
如果不同方法 call ledger 不一致或任一双侧 work-volume bound 越界
    ↓
performance result 标记 confounded
```

而不是：

```text
把随机 token 差异当作 runtime speedup
```

Correctness 仍只看 replay lane。

越界的真实 E2E 仍完整报告，但只能称 descriptive end-to-end outcome，不能称
同工作量 scheduling speedup；不得用 token-normalized latency 替代原始 E2E。

---

# 14. Batch Invariance：继续 OFF 是合理的，但要说明原因

正式 performance lane：

```text
Batch Invariance = OFF / normal serving path
```

可以保留。

理由不是“默认就不用管”，而是：

> 本工作研究 concurrency/scheduling；vLLM batching、queueing 和 GPU utilization 是 treatment 的真实组成部分。如果为了获得 bitwise determinism 打开会改变 batch execution semantics 的模式，可能反而把需要测量的系统效应消掉。

控制 correctness 的手段已经是：

```text
model oracle capture/replay
```

因此无需使用 Batch Invariance 代替 correctness control。

论文必须明确：

```text
Correctness: deterministic replay
Performance: production-style normal batching
```

两条 lane 不混用。

---

# 15. `backend=auto` 的正式规则

如果最终配置是：

```text
json_schema
```

v1.3 的 Q1-Q3 delta specs 及 shared base 已冻结当前已证明的
`backend=auto`，不允许在 candidate
中途顺手改 backend。正式报告必须把它列为 reproducibility limitation。若希望将
backend 显式固定，必须在 Q1 前发布新协议/新候选 registry；不能把 service-side
change 偷渡成 Q1 的 completion-budget-policy diff。

若因为受限服务权限无法设置：

1. 记录 `backend=auto`；
2. 冻结 vLLM exact version；
3. 冻结 launch argv/config hash；
4. 报告这是 reproducibility limitation；
5. 不声称知道实际 per-request selected backend。

如果最终使用：

```text
json_object
```

provider 不承担 Pydantic schema constrained decoding，backend 对 schema correctness 的作用减弱，但仍必须记录完整 launch config。

远程服务如需修改：

> 继续遵守 restricted SSH/write contract，不能绕过权限。

---

# 16. 当前 M0 的 baseline validity：必须维持“双基线”概念

当前 M0 带：

```text
deterministic candidate-ordering adapter
```

因此公开名称：

```text
Deterministic-Graphiti-Serial
```

是正确的。

但最终论文为了避免 reviewer 质疑：

> “你是不是把 Graphiti 改弱/改慢以后再打它？”

应保留一个 **Upstream Representativeness Guardrail**：

```text
U0 = Upstream-Qualified Graphiti Serial
D0 = Deterministic-Graphiti-Serial
```

在 calibration 上比较：

```text
construction latency
LLM calls/tokens
retrieval quality
canonical semantic overlap
```

U0 精确定义为 qualified Graphiti serial 加所有方法共享的 provider compatibility
adapter，但不含 deterministic candidate-ordering adapters；D0 在 U0 上只增加已声明
且内容寻址的 ordering adapters。预注册 guardrail 为：4/4 完成、episode/source
coverage 一致、canonical entity/edge F1 均至少 0.95、D0 macro Evidence Recall@10
相对 U0 不下降超过 1 percentage point、LLM call count 相同、token ratio 位于
`[0.95,1.05]`。

如果上述 guardrail 通过：

> D0 可以作为 causal correctness/performance baseline，并明确说明 adapter 只为稳定物理 tie/order。

如果不一致：

> D0 仍固定称 Deterministic-Graphiti-Serial，论文必须同时报告 U0，并收缩
> upstream representativeness claim；不得在结果后改成含糊的 Native 名称。

---

# 17. 不建议现在删除 M1；应该把它升级为 strong tuned baseline

当前：

```text
M1 = WholeUpdate-Parallel-C8
```

是非常重要的反方：

> “为什么不直接把完整 add_episode 并发执行？”

但正式公平性必须避免：

> 故意固定一个不合适的 C8 使它表现差。

参考 ContextPilot 的 baseline tuning，以及 Agentix 的 `vLLM-opt + MLFQ` baseline ladder：

Calibration 阶段预注册：

```text
C ∈ {1, 2, 4, 8}
```

分别测：

```text
M1
M2
```

先做 quality-feasibility：candidate 必须完成、无 protocol failure、满足各自
correctness/retrieval/exactly-once guardrail，才可进入速度选择。冻结：

```text
best_m1_concurrency_on_calibration
best_m2_concurrency_on_calibration
selection objective = minimum calibration median makespan
exact tie break = smaller C
```

正式结果同时报告：

1. **Iso-cap comparison**
   ```text
   M0
   M1-C8
   M2-C8
   ```

2. **Best-tuned baseline comparison**
   ```text
   Best-Tuned M1
   vs
   Best-Tuned M2
   ```

`iso-cap` 只表示相同 global concurrency cap，不声称实际 CPU/GPU/DB utilization
相同。若 M1 无 quality-feasible concurrent point，报告完整 speed-quality frontier，
不得把语义错误但更快的 M1 称为 Best-Tuned。这样可以防止 reviewer 指控 M1 是
strawman，同时不以错误结果换速度。

---

# 18. Agentix 对 MemBind baseline 设计的直接启示

Agentix 没有只比较：

```text
vLLM vs Agentix
```

而是：

```text
vLLM
vLLM-opt
MLFQ
Agentix
```

含义：

```text
原始系统
    ↓
充分优化的原始系统
    ↓
不使用新 abstraction 的强 generic scheduler
    ↓
Ours
```

MemBind 当前可以先形成：

```text
D0 Deterministic Graphiti Serial
M1 Best-Tuned Whole-Update Parallel
M2 MemBind
```

Pilot GO 后再审计是否存在合理的：

```text
Strong-General Stateful Executor
```

例如 classical OCC / generic DAG executor。

**不要为了补 baseline 数量，把 MemForest / A-Mem 等不同 memory algorithm 塞进 runtime baseline。**

不同 memory architecture 属于未来 workload/host axis，不是 runtime axis。

---

# 19. 当前 Pilot workload 可以保留，但论文级 workload 要分层

## 19.1 Pilot

当前 LongMemEval-S knowledge-update：

> 继续用于核心 mechanism feasibility。

当前 deterministic open-loop：

```text
t_i = i * DELTA
```

可以保留。

它的作用是：

- 控制 workload randomness；
- 形成可配对 backlog；
- 测 `arrival_to_publish`；
- 验证 Parallel Compile / Ordered Bind 是否值得继续。

---

## 19.2 Paper-level trace-replay anchor 与未来 live workload

Pilot GO 以后，正式论文应增加：

> MemoryArena physics split 的 timing-trace replay freshness workload。

已有 Agent Memory Characterization 使用：

```text
20 multi-session tasks
each subtask = retrieve-act-write session
captured per-session timing traces
controlled 5-second inter-session replay schedule
Qwen3-32B FP8
Qwen3-Embedding-0.6B
```

因此最有依据的 trace-replay anchor 是：

```text
gap = 5 s
```

先复现 trace-replay；若 MemBind 另做真正 live online serving，应明确写成受该工作
启发的新扩展，而不是声称完全复现其执行方式。对比：

```text
Native -> latency / fresh
Async -> lower blocking / stale
```

再加入 MemBind。

---

## 19.3 Load sweep

5 秒只代表一个 operating point。

正式 systems evaluation 应追加预注册 load sweep，例如：

```text
rho ≈ 0.5
rho ≈ 0.75
rho ≈ 1.0
rho ≈ 1.25
rho ≈ 1.5
```

其中：

```text
rho_proxy = median Native service time / inter-arrival interval
```

或使用固定 arrival rate。

参考：

- Agentix：从已有 program workload 抽完整 program，用 Poisson process 生成 arrivals；
- DistServe：数据无真实 timestamp 时用 Poisson arrivals 并扫 request rate；
- 经典 LLM serving 论文：比较不同 offered load 下的 latency / throughput / saturation。

---

# 20. Paper-level runtime matrix

最终如果 Runtime abstraction 在多架构源码审计中成立：

```text
                           Runtime / Execution Policy
Memory Architecture       Native-Best   Async-Serial   Strong-General   Ours
----------------------------------------------------------------------------
Graphiti
Mem0
A-Mem
```

其中：

### Architecture axis

```text
Graphiti / Mem0 / A-Mem
```

是不同 memory construction workload / host program。

### Runtime axis

才是：

```text
Native
Async
Generic execution baseline
Ours
```

不能把：

```text
MemForest
```

放在 runtime column，因为它改变了上层 memory algorithm / representation。

---

# 21. 正式在线 baseline 至少要区分两种“异步”

未来 paper 不要只写模糊的 `Async`。

建议：

## Async-Serial

```text
session arrives
    ↓
enqueue whole original update
    ↓
single ordered background worker
```

回答：

> “直接后台化不就行了吗？”

它保留原算法顺序，但 query 可能 stale。

## WholeUpdate-Parallel

当前 M1：

```text
多个完整 update 同时执行
```

回答：

> “直接粗粒度并发不就行了吗？”

它可能带来 state-dependent trajectory divergence。

两者研究问题不同，不应混用一个 `Async` 标签。

---

# 22. Metrics 分层

## Pilot primary

继续保留：

```text
P95 arrival_to_publish
makespan
canonical_graph_parity
Evidence Recall@10
```

## Mechanism

```text
Compile-eligible critical-path fraction
state-dependent Bind fraction
Compile hiding ratio
ready-artifact queue
source-frontier stall
LLM/embedding utilization
```

## Paper-level online

新增：

```text
staleness(q)
= query 时未完成 persistence / semantic publication 的 prior sessions 数

stale-query ratio
P50/P95 semantic-current latency
backlog depth
drain time
sustainable update rate
```

可进一步定义：

```text
Freshness-SLO Goodput
= 单位时间内，在 freshness deadline 前完成 publication 的有效更新数
```

这类指标比简单 API enqueue latency 更能回答 MemBind 的系统问题。

---

# 23. 统计与重复：Pilot 与 Paper 必须区分

当前：

```text
8 evaluation instances
2 repeats
paired cluster bootstrap
```

作为：

> **内部 GO/NO-GO pilot**

可以保留。

但不应把它包装成最终 paper-level tail-latency evidence。

尤其：

```text
P99
```

继续只能 descriptive。

最终论文：

- MemoryArena 使用完整 20 physics tasks；
- load point 在看结果前冻结；
- 每个 `(task, runtime, load)` 至少做多个独立 repeat/block；
- task/stream 是 statistical unit；
- paired/cluster bootstrap；
- 保留 raw runs；
- infrastructure failure 按 block 整体重跑，而不是只重跑表现差的方法。

具体 repeat 数量应根据成本与预运行噪声分析确定，而不是看到结果后决定。

---

# 24. Run order 与环境公平性

当前 `blocked randomization` 应保留：

```text
block = (question_id, repeat)
```

block 内：

```text
M0/M1/M2
```

使用固定 seed 平衡排列。

理由：

- 让 paired methods 尽可能靠近 wall-clock 时间；
- 降低 server thermal drift、LAN jitter、background noise。

同时保持：

```text
hot engine
cold cross-run application state
fresh logical graph
cold run-level embedding cache
same HTTP client config
same endpoint
same global construction cap
```

这是合理的 systems fairness control。

---

# 25. Network 与远程 vLLM

继续使用真实 E2E latency。

禁止：

```text
E2E - RTT = “corrected latency”
```

原因：

> 网络、server queue、request serialization、batching 是真实部署路径的一部分。

但必须控制第三方噪声：

```text
NO_PROXY / direct LAN
exclusive GPU
no external requests
pre/post health check
server restart / OOM / throttle detection
```

M2 因并发产生：

```text
better batching
higher queue
better GPU utilization
```

都属于 treatment effect，不能数学扣掉。

---

# 26. 当前 Go/No-Go 阈值怎么处理

当前内部门槛：

```text
makespan speedup >= 1.5x
P95 arrival_to_publish reduction >= 30%
...
```

可以继续作为：

> **预注册的工程研究推进 Gate**

但最终论文不要写成：

> “1.5x 是统计上正确的科学阈值。”

论文应报告：

```text
effect size
95% CI
per-workload distribution
failure cases
load dependence
```

GO threshold 只决定：

> “是否值得继续投入第二 architecture 和完整 paper evaluation。”

它不是 publication claim 的显著性定义。

---

# 27. 新的执行状态机

建议将 v1.2：

```text
V1 -> V2 -> V3 -> V4 ...
```

修改为：

```text
H0  Host Stack Qualification
        ↓ PASS + FREEZE

V2-R Correctness Oracle Requalification
        ↓ PASS

V3-R Full M0 -> M2 Correctness Smoke
        ↓ PASS

V4 Native Characterization + Arrival Calibration
        ↓ PASS

V5 Strong Baseline Tuning
        ↓ PASS

V6 Formal Pilot:
    correctness lane
    performance lane
        ↓

V7 Mechanism + GO/NO-GO Verdict
        ↓ STOP
```

历史 V1 nondeterminism closure：

> 保留，不需要因 H0 重新追求 embedding bitwise determinism。

旧 V2/V3 model outputs：

> 只作历史 artifact，不进入新 oracle namespace。

---

# 28. H0 必须先新增的 TDD contract

至少新增：

```text
test_host_qualification_candidate_order_is_frozen.py
test_first_passing_candidate_is_selected.py
test_evaluation_split_not_used_for_host_tuning.py
test_exposed_evaluation_canary_is_quarantined_by_id_only.py
test_live_h0_requires_green_contracts_and_explicit_state_gate.py
test_candidate_manifest_allows_only_declared_diff.py
test_candidate_trial_seed_http_attempt_and_retry_are_distinct.py
test_candidate_induced_retry_fails_qualification.py
test_infra_failure_reruns_whole_h0_stage.py
test_qualified_manifest_is_immutable.py
test_old_oracle_namespace_rejected_after_protocol_change.py
test_h0_never_populates_formal_oracle.py
test_m0_m1_m2_share_qualified_host_config.py
test_no_method_specific_sampling_override.py
test_no_method_specific_schema_override.py
test_no_method_specific_retry_override.py
test_json_object_injects_effective_shim_schema.py
test_q2_top_k_min_p_are_observed_in_payload.py
test_requested_and_effective_context_budget_are_recorded.py
test_valid_but_degenerate_output_fails_semantic_utility.py
test_schema_shim_hash_is_manifested.py
test_finish_reason_length_fails_qualification.py
test_parse_or_pydantic_failure_fails_qualification.py
test_all_candidates_failed_blocks_without_q4.py
test_work_volume_bounds_are_two_sided_and_predeclared.py
test_concurrency_selector_is_quality_feasible_and_uses_fixed_tiebreak.py
test_paper_stages_remain_forbidden_before_v7_go.py
```

如果修改 server structured backend：

```text
test_server_launch_config_fingerprint_matches_qualified_manifest.py
```

---

# 29. 代码层建议：减少 `QwenVLLMClient` 对 upstream Graphiti 的重写范围

当前 `QwenVLLMClient` 重写整个 `_generate_response()`：

- request construction；
- retry budget；
- parsing；
- failure tracking。

这增加 baseline validity 风险。

建议 refactor 原则：

> **Instrumentation around upstream behavior，而不是 reimplement upstream behavior。**

优先方式：

1. 保留 Graphiti upstream `OpenAIGenericClient.generate_response()`；
2. 只通过薄 wrapper / transport interception 注入 vLLM/Qwen 必需 kwargs；
3. tracing/counters 包在 API boundary 外；
4. structured response format、schema injection、Graphiti retry 逻辑尽量复用 upstream；
5. 如果确实必须 override，输出：
   ```text
   upstream_vs_effective_client_diff.md
   ```
   按行为逐条说明。

Formal report 必须能回答：

> “我们改了 Graphiti 的什么？为什么这些修改与 M0/M1/M2 对称？是否改变 memory semantics？”

---

# 30. 不建议做的“修复”

当前禁止以下 opportunistic fix：

### A. 无限增大 max_tokens

```text
8192 fail -> 16384 -> 32768 -> ...
```

不能成为无界 debugging loop。

H0 只按预注册候选测试。

### B. 给 `extracted_entities` 随便加 `maxItems=32/64`

这会潜在改变 Graphiti 的 extraction capacity。

除非有明确算法依据和 quality guardrail，否则不能进入正式 protocol。

### C. 只修改 M0 让 oracle 跑通，再给 M2 复用

Host config 必须所有方法共享。

### D. 因方法结果不好删除 LongMemEval case

禁止按质量或性能结果删除难例。v1.3 对 `c6853660` 的处理只基于其已被大量开发
暴露、不能再称 held-out 的事实，使用原 hash rule 补入下一个未观察 ID，并同时保留
旧 split 与该 case 的全部历史负证据；这与结果导向删例不同。

### E. 打开 Batch Invariance 来“修” correctness

Correctness 已通过 replay 隔离；performance 应保留正常 batching。

### F. 升/降 vLLM 版本作为第一反应

先用当前 v0.26.0 完成官方支持 path 的 H0。

只有所有预注册官方-compatible candidates 都失败，才把：

```text
vLLM version
```

作为新的 protocol-level compatibility dimension。

---

# 31. Immediate Action Plan

从历史 `blocked_waiting_for_explicit_protocol_deviation` 状态开始：

## Step 1

冻结并归档当前：

```text
v3_actual_schema_compatibility_probe_20260809_005_fresh_restart.json
```

以及所有 V3 failure/TDD artifacts。

状态：

```text
historical_negative_host_qualification_evidence
```

---

## Step 2

创建：

```text
MemBind_CURRENT_VALIDATION_PLAN_v1.3.md
```

明确：

```text
old V3 formal lane = stopped
new H0 offline contracts/harness = authorized
live H0 candidate = forbidden
```

---

## Step 3

先写 H0 TDD contracts。

禁止先改 client 再补测试。

---

## Step 4

完成最小 offline harness、focused GREEN、full regression、manifest/hash 审阅，并把
machine state 通过**单独显式 gate**改成 `h0_q1_a_live_only`。在此之前停止。

---

## Step 5

执行 Q1。

如果 PASS：

```text
不要执行 Q2/Q3
```

进入 H0-B/C。

如果 FAIL：

保留 artifact，执行 Q2。

---

## Step 6

若 Q2 FAIL，再执行 Q3。

Q3 是 Graphiti upstream-supported fallback，不属于 M2-specific adaptation。

---

## Step 7

第一组通过 H0-A/B/C 的配置立即冻结。

禁止比较候选的 performance 后挑最快的。

---

## Step 8

创建新 model-oracle namespace，重新执行 V2-R。

---

## Step 9

重新执行 V3-R：

```text
1 × full M0 capture
1 × full M2 read-only replay
```

只有 V3-R PASS 才重新开放 V4+。

---

# 32. Formal Fairness Checklist

正式 measured run 前，每项必须为 true：

```text
[ ] Host config passed H0 on calibration only
[ ] Formal evaluation split never used for tuning
[ ] M0/M1/M2 exact same model revision
[ ] M0/M1/M2 exact same structured-output mode
[ ] M0/M1/M2 exact same decoding config
[ ] M0/M1/M2 exact same retry policy
[ ] M0/M1/M2 exact same schema / compatibility shim
[ ] M0/M1/M2 exact same embedding endpoint
[ ] M0/M1/M2 exact same Neo4j config
[ ] M0/M1/M2 exact same client/HTTP resource limits
[ ] Performance lane response cache OFF
[ ] Correctness lane model replay ON, no live fallback
[ ] Upstream-vs-deterministic Graphiti guardrail completed
[ ] M1 baseline concurrency tuned only on calibration
[ ] Arrival/load points frozen before formal results
[ ] Run order blocked-randomized
[ ] Infra failures retained and block-level rerun
[ ] No result-dependent dataset/config changes
```

---

# 33. 对当前 plan 各部分的裁决

| 当前设计 | 裁决 | v1.3 建议 |
|---|---|---|
| Graphiti v0.29.3 pinned commit | 保留 | 正确 |
| Qwen3-32B-FP8 | 保留 | 有 Agent Memory Characterization 先例 |
| Qwen3-Embedding-0.6B | 保留 | 同上 |
| vLLM 0.26.0 | 暂时保留 | 先 qualification，不立即换版本 |
| thinking=false | 保留 | Agent Memory Characterization 同样关闭 thinking |
| temperature=0/top_p=1 永久冻结 | **修改** | 移入 H0 qualification；优先测试 Qwen recommended config |
| 2048 -> 8192 | **修改** | 不再作为正式固定 contract；先测试 Graphiti upstream 16K |
| forced json_schema | **修改** | H0 允许 Graphiti official json_object fallback |
| backend=auto | 条件保留 | json_schema 正式使用时优先显式冻结；否则记录 limitation |
| `episode_indices=[0]` shim | 条件保留 | 明确 compatibility shim + hash；不得继续拍脑袋加 outer maxItems |
| LLM+embedding capture/replay | **强保留** | correctness 最合理的控制方式之一 |
| correctness/performance split | **强保留** | 不混用 |
| Batch Invariance OFF | 保留 | correctness 用 replay，performance 测正常 runtime |
| M0/M1/M2 same endpoint | **强保留** | 核心公平性 |
| M1 C8 only | **增强** | 增加 calibration best-tuned M1 |
| deterministic open-loop | 保留 Pilot | Paper 再加 MemoryArena 5s + load sweep |
| 8 instance × 2 repeat | 保留 Pilot | 不作为完整 paper-level tail evidence |
| blocked randomization | **强保留** | 降低时变环境偏差 |
| network included in E2E | **强保留** | 不做 RTT subtraction |
| current frozen V3 must never change | **修改** | 历史失败保留，但批准新 v1.3 protocol deviation |

---

# 34. 为什么这个修改不会造成“为了跑通而不公平调参”

Reviewer 最容易质疑的是：

> “你遇到 Ours 不工作，于是不断调参数直到它工作。”

v1.3 用下面四道防线阻止：

### 1. Host qualification 在方法比较之前

H0 根本不运行 M2 performance。

### 2. 只用 calibration / compatibility data

Formal evaluation split 不参与配置选择。

### 3. First-passing candidate

不是所有候选跑完后挑最快。

### 4. 全方法共享

Qualified config 对：

```text
M0
M1
M2
```

完全相同。

因此 H0 调的是：

> **实验宿主是否可运行**

而不是：

> **Ours 的超参数。**

这与顶会论文对 baseline/system 做 compatibility adaptation 和 calibration tuning 的实践是一致的。

---

# 35. Pilot GO 后才允许扩展的 Paper Protocol

以下内容**不授权当前立即实现**。

只有 Graphiti Pilot 证明：

```text
Compile fraction significant
M2 hides meaningful work
M2 correctness passes
M2 has real live benefit
```

之后才进入：

## P1 Architecture Abstraction Audit

审计：

```text
Graphiti
Mem0
A-Mem
```

是否都能映射：

```text
state-independent prepare
state-dependent resolve
publication boundary
ordering/conflict dependency
```

只有成立才声称：

> General Agent Memory Runtime。

---

## P2 Second Host

至少第二个 memory architecture：

```text
Native-Best
Async-Serial
Strong Generic
Ours
```

scheduler/runtime core 不修改，只允许薄 adapter。

---

## P3 Online MemoryArena

复现 prior work：

```text
physics split
20 multi-session tasks
5s inter-session gap
```

然后做 load sweep。

---

## P4 Paper-level Baselines

最终 runtime axis 至少考虑：

```text
Native-Best
Async-Serial
Best-Tuned WholeUpdate Parallel
Strong-General（若经调研存在合理方案）
Ours
```

不要把不同 memory algorithm 当同层 runtime baseline。

---

# 36. 可直接用于论文 Methodology 的核心公平性表述

建议以后论文写法遵循以下逻辑：

> We separate host compatibility qualification from method evaluation. Before any measured experiment, we qualify a single Graphiti–Qwen3–vLLM interface configuration on a disjoint calibration set using a pre-registered fallback order derived from upstream Graphiti and model-provider recommendations. The first configuration satisfying the qualification criteria is frozen and used unchanged by all execution policies. No evaluation instance or MemBind performance result is used for configuration selection.

> For semantic correctness, we capture model-derived outputs from the serial reference execution and replay them read-only for alternative execution policies, while keeping graph-state reads, resolution, invalidation, and database commits live. This isolates execution-order semantics from neural-model sampling variance. Performance experiments instead use the same live model and embedding services for all methods, with response caching disabled.

> We use identical hardware, endpoints, client resource limits, database configuration, model revisions, prompts, schemas, and arrival traces across methods. Baseline concurrency is tuned only on calibration workloads and frozen before formal evaluation.

---

# 37. 最终结论

当前最合理的修改不是：

```text
继续 V3 forensic
```

也不是：

```text
直接换模型 / 换 vLLM / 给 schema 加上限
```

而是：

> **承认 v1.2 缺少 pre-freeze host qualification，并用一个可预注册、可审计、对所有方法对称的 H0 阶段修正实验顺序。**

这不会削弱当前已经积累的 artifact。

相反：

```text
旧 V3 failure
```

会成为：

> “为什么正式 protocol 需要 Host Qualification Gate”

的明确工程证据。

当前 MemBind 核心研究假设仍然没有被 V3 failure 检验到：

```text
Parallel Semantic Compile
    +
Latest-State Bind
    +
Ordered Commit
```

是否能够：

```text
preserve semantic state
+
reduce freshness/materialization latency
```

仍需要在 qualified host stack 上重新验证。

---

# References / Protocol Precedents

1. **ContextPilot: Fast Long-Context Inference via Context Reuse**, MLSys 2026.  
   https://proceedings.mlsys.org/paper_files/paper/2026/file/b0131b6ee02a00b03fc3320176fec8f5-Paper-Conference.pdf  
   主要借鉴：baseline tuning、online cold-start、performance + quality guardrail。

2. **Agentix: An Efficient Serving Engine for LLM Agents as General Programs**, NSDI 2026.  
   https://www.usenix.org/system/files/nsdi26-luo.pdf  
   主要借鉴：`vLLM -> vLLM-opt -> MLFQ -> Agentix` 强 baseline ladder；program-level arrival；Poisson workload；tail latency。

3. **Pie: A Programmable Serving System for Emerging LLM Applications**, SOSP 2025.  
   https://doi.org/10.1145/3731569.3764814  
   主要借鉴：相同 high-level workflow；统一底层 FlashInfer backend；baseline best-effort optimization；runtime overhead analysis。

4. **Agent Memory: Characterization and System Implications of Stateful Long-Horizon Workloads**, arXiv preprint, 2026.  
   https://arxiv.org/abs/2606.06448  
   主要借鉴：Agent Memory construction 作为 systems workload；local Qwen3-32B FP8 + Qwen3-Embedding-0.6B；最小 compatibility adaptation；MemoryArena 5-second timing-trace replay freshness experiment。公开 metadata 当前不支持 IISWC venue/acceptance claim。

5. **DistServe: Disaggregating Prefill and Decoding for Goodput-optimized Large Language Model Serving**, OSDI 2024.  
   https://www.usenix.org/system/files/osdi24-zhong-yinmin.pdf  
   主要借鉴：无真实 timestamp 时使用 Poisson arrivals；多 request-rate sweep；SLO/goodput；queueing 与 execution 分解。

6. **Parrot: Efficient Serving of LLM-based Applications with Semantic Variable**, OSDI 2024.  
   https://www.usenix.org/conference/osdi24/presentation/lin-chaofan  
   主要借鉴：application-level abstraction 与 end-to-end system path；不把真实系统交互开销随意从 E2E 指标中删除。

7. **Graphiti v0.29.3 pinned source / OpenAIGenericClient**.  
   https://github.com/getzep/graphiti/blob/021d3a57d511f21b10adaf7fa923bd5c1fce5e9d/graphiti_core/llm_client/openai_generic_client.py  
   关键事实：local-model default max tokens 16K；支持 `json_schema` 与 `json_object` fallback。

8. **Graphiti official README — local/OpenAI-compatible provider structured output**.  
   https://github.com/getzep/graphiti/blob/021d3a57d511f21b10adaf7fa923bd5c1fce5e9d/README.md  
   关键事实：local provider structured-output reliability varies；`json_object` 是官方支持 fallback。

9. **Qwen3-32B-FP8 official model card**.  
   https://huggingface.co/Qwen/Qwen3-32B-FP8/blob/aa55da1ecc13d006e8b8e4f54579b1ea8c3db2df/README.md  
   关键事实：non-thinking mode 推荐 `temperature=0.7, top_p=0.8, top_k=20, min_p=0`。

10. **vLLM v0.26.0 structured-output source**.  
    https://github.com/vllm-project/vllm/blob/v0.26.0/vllm/config/structured_outputs.py  
    https://github.com/vllm-project/vllm/blob/v0.26.0/vllm/sampling_params.py  
    关键事实：`backend=auto` 的 backend selection 是 opinionated choice，并可能随 release 变化。

---

# Appendix A — 对执行 Agent 的一句话最高优先级指令

> **Do not continue V3 under the known-incompatible frozen provider configuration. Preserve all existing V3 evidence, introduce a pre-freeze Host Stack Qualification stage using a pre-registered first-passing fallback sequence derived from upstream Graphiti and Qwen guidance, freeze the first qualified configuration symmetrically for M0/M1/M2, rebuild the correctness oracle namespace, and only then resume the M0→M2 correctness smoke. No evaluation data or MemBind performance result may influence host-configuration selection.**
