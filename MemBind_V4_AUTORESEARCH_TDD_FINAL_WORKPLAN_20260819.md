# MemBind v4 Autoresearch + TDD Final Workplan

> 日期：2026-08-19  
> 项目：`https://github.com/ysyjsk/MemBind`  
> 审计提交：`479c50b`（`3.1 finish`）  
> 状态：`IMPLEMENTATION_AND_FINAL_RUN_PLAN`  
> 本文替换：`MemBind_V4_MAIN_EXPERIMENT_FIRST_WORKPLAN_20260818.md`中仍以“v3.1尚未成功”为前提的执行顺序  
> 当前权威前提：v3.1已经成功；baseline已经在目标GPU重新运行；当前唯一主任务是设计、实现、短跑优化并正式运行v4

---

# 0. Executive Decision

下一阶段不再修v3.1、不再扩baseline、不再增加新benchmark，也不再围绕artifact contract进行多轮资格验证。

唯一任务是：

```text
冻结v3.1成功结果与新baseline结果
        ↓
以TDD实现v4最小闭环
        ↓
在development prefix上短跑
        ↓
分析语义命中、GPU/backend转化与关键路径趋势
        ↓
最多进行两次单因素调整
        ↓
趋势符合预期后冻结v4
        ↓
立即运行完整4 histories / 188 episodes实验
        ↓
生成最终主表、机制表和结论
        ↓
STOP
```

v4的核心方法冻结为：

> **Version-Bound Resource-Gated Speculation**：对未来episode的NodeResolve在旧的合法已发布memory version上提前物化和执行；当其精确前驱state产生后，重新物化exact Native request，只有完整semantic-call fingerprint一致时才复用结果，否则执行exact fallback。投机请求不是见缝就发，而是只有在frontier优先、全局`K=2`仍有第二个slot、且请求组合预计不会伤害frontier时才进入vLLM。

这使v4同时包含两个不可分割的机制：

1. **Validated State Speculation**负责创造原本不存在的合法候选工作；
2. **Resource-Gated Admission**负责判断这些候选工作是否值得送入单GPU vLLM。

如果只实现第1项而没有第2项，v4仍可能只是增加应用层并发；如果只实现第2项而没有第1项，现有W=4诊断已经表明ready pool太小，scheduler没有足够选择空间。因此v4必须把两者结合起来。

---

# 1. 当前项目状态与本轮边界

## 1.1 已完成、不得重复的工作

以下工作视为当前已完成事实：

- v3.1已经成功运行；
- baseline已经在目标GPU或目标GPU对应的统一服务封套下重新运行；
- Graphiti版本固定为`0.29.3`；
- 模型、prompt、schema、structured-output backend、embedding、Neo4j、arrival trace和评测逻辑已经固定；
- v3.1的State-Cut、Prepared ROB、frontier-first、version-bound Bind和ordered publication已经有真实运行路径；
- 现有代码已经包含NodeResolve边界分析、semantic-call fingerprint数据结构、validated runtime原型和相关单元测试；
- W=4诊断已经完成，不再增加W或lookahead；
- U0/A0/P(C=2)/v3.1不因v4再次修改。

执行v4前只做一次**结果登记**，不重跑：

```text
v3.1 completed RESULT.json / final reducer result
new baseline RESULT.json / main-table reducer result
provider execution envelope
physical GPU identity
vLLM启动参数
arrival trace identity
model / tokenizer / prompt / schema identity
```

若这些完成artifact尚未推送到仓库，只需登记其本地绝对路径和SHA-256，不得以“仓库里暂时没有”为理由重跑。

## 1.2 现有证据对v4的直接约束

现有W=4诊断已经得到：

```text
max legal-ready Compile count = 1
max Prepared ROB occupancy = 1
window-limited duration = 0
admission under-capacity with waiter = 0
under-capacity without waiter ≈ 431.5 s
```

因此：

- 继续增加`W`不会自动创造更多可执行工作；
- 继续增加compile workers不会解决state-dependent suffix；
- 只优化cache-affinity排序缺少足够ready pool；
- v4必须改变“NodeResolve只有到frontier才能开始”的限制；
- 新创造的工作必须受到resource gate约束，否则可能只增加GPU干扰。

## 1.3 当前LLM workload结构

现有U0的1900个logical calls显示明显异构性：

| 组别 | 调用数 | 输入token占比 | service span占比 |
| --- | ---: | ---: | ---: |
| 长请求，input≥4096 | 669 | 95.52% | 86.27% |
| 短请求，input<4096 | 1231 | 4.48% | 13.73% |

典型role的历史中位数为：

| Prompt role | input tokens | output tokens | 初始解释 |
| --- | ---: | ---: | --- |
| `extract_nodes.extract_message` | 约26690 | 约76 | 长prefill、短输出 |
| `extract_edges.edge` | 约26291 | 约228 | 长prefill、较长decode |
| `dedupe_nodes.nodes` | 约27655 | 约112 | 长prefill、state-bound |
| `extract_nodes.extract_summaries_batch` | 约25193 | 约167 | 长prefill、state-bound |
| `dedupe_edges.resolve_edge` | 约963 | 约19 | 短请求 |
| `extract_edges.extract_timestamps` | 约314 | 约17 | 短请求 |

这些数值只用于初始化分类规则。正式v4必须从刚重跑的目标GPU baseline trace重新生成role profile，不能把旧中位数硬编码为论文结果。

## 1.4 本轮明确禁止

本轮不实现：

- `K>2`；
- `W>2`或继续调lookahead；
- whole-update `P>2`；
- Attributes/Summary speculation；
- EdgeResolve speculation；
- MVCC、OCC、read-set repair、selective graph rollback；
- prompt重排、prompt压缩、schema修改；
- speculative decoding；
- 更换SGLang、NanoFlow、DistServe或第二backend；
- 修改模型、量化、max model length、completion cap；
- 新benchmark或新history；
- 为每个candidate创建新的authorization/contract/qualification体系；
- 在正式全量结果出现后继续调参挽救结果。

---

# 2. v4 Research Question与系统Insight

## 2.1 Research Question

给定已经正确运行的MemBind v3.1：

> 能否在不改变serial-reference memory semantics的前提下，把未来NodeResolve提前到前驱Bind期间执行，并只在单GPU vLLM能够把该工作转化为有效重叠时才进行投机，从而降低construction makespan与freshness？

## 2.2 v3.1到v4的因果链

v3.1解决：

```text
哪些工作在语义上可以提前？
```

v4进一步解决：

```text
哪些原本state-bound的工作可以安全投机？
这些投机工作什么时候值得送进vLLM？
```

完整因果链为：

```text
State-Cut
  产生future prepared evidence
        ↓
Stale-state NodeResolve materialization
  产生候选semantic call
        ↓
Resource Gate
  只允许有利的候选占用第二个LLM slot
        ↓
Exact predecessor materialization
  验证请求是否完全相同
        ↓
HIT：复用结果 / MISS：exact fallback
        ↓
Native effect application
        ↓
Ordered publication
```

## 2.3 为什么不是普通并发

v4不以`inflight request count`为目标，而以以下量为目标：

```text
Useful Critical-Path Progress per GPU Second
```

并发只有在同时满足以下条件时才有价值：

1. 投机结果最终HIT并替代未来exact NodeResolve；
2. 投机执行与前驱关键路径存在真实时间重叠；
3. 投机没有显著拉长frontier request service time；
4. MISS产生的额外token和服务时间没有抵消HIT收益；
5. vLLM useful token throughput或batch occupancy出现正向转化。

因此v4的预期收益不是“减少HIT路径总LLM token”。HIT路径通常只是把原本未来要执行的NodeResolve移到更早时间；其收益来自**隐藏关键路径时间和形成更好的backend batch**。MISS才会增加额外工作。

可近似表示为：

```text
NetLatencyGain
  = HiddenHitServiceTime
  - FrontierInterference
  - MissWasteCriticalCost
  - ValidationOverhead
  - NonOverlappedSpeculation
```

## 2.4 与相关顶会工作的关系

- Sarathi-Serve指出prefill计算密集、decode访存密集，chunked prefill可形成更平滑的混合batch；MemBind借鉴其phase-complementary思想，但调度候选首先受memory version约束。
- NanoFlow说明“请求足够多”不等于GPU利用充分；MemBind不声称实现operator-level GPU流水，而是在应用语义层为现有vLLM提供更有价值的候选组合。
- BlendServe联合resource overlap与prefix sharing，但假设offline请求可以自由重排；MemBind的ready set由persistent-state dependency决定，不能全局自由排序。
- Agentix按程序关键路径优先LLM calls；MemBind把publication frontier作为memory construction的程序关键路径，并增加exact-state validation。
- SGLang/ContextPilot证明prefix/context reuse可以降低prefill，但当前Graphiti prompt的动态`previous_episodes`靠前，因此prefix reuse只作为secondary signal。
- FlashAgents使用流式增量prefill隐藏agent间依赖；MemBind不改变prompt传输方式，而是在完整semantic request完全一致时复用投机结果。

v4的论文定位应写成：

> **A cross-layer state-aware runtime that converts semantically validated slack into GPU-efficient work while preserving serial memory semantics.**

---

# 3. v4精确执行模型

## 3.1 状态定义

对source sequence `i`：

```text
E_i       已到达并冻结的source evidence
A_i       v3.1 Compile产生的PreparedArtifact
M_(i-2)   投机时最后一个合法已发布memory state
M_(i-1)   source i必须观察的精确前驱state
Q_i^s     在stale state上物化的NodeResolve semantic call
Q_i^e     在exact predecessor上物化的NodeResolve semantic call
Y_i^s     speculative response
```

v4允许：

```text
Q_i^s = MaterializeNodeResolve(A_i, M_(i-2))
Y_i^s = Execute(Q_i^s)
```

当`M_(i-1)`产生后：

```text
Q_i^e = MaterializeNodeResolve(A_i, M_(i-1))
```

只有：

```text
Fingerprint(Q_i^s) == Fingerprint(Q_i^e)
```

才允许：

```text
Interpret(Y_i^s, Q_i^e)
ApplyEffect(..., M_(i-1))
```

否则：

```text
Y_i^e = Execute(Q_i^e)
Interpret(Y_i^e, Q_i^e)
ApplyEffect(..., M_(i-1))
```

## 3.2 Semantic-call fingerprint

fingerprint至少包含：

```text
operator identity
Graphiti version / adapter identity
model identity
decoding configuration
response schema identity
rendered chat token sequence identity
extracted-node order and canonical projection
candidate order
candidate UUID and canonical projection
previous-episodes projection and order
episode context projection
entity-type projection
NO_LLM / LLM execution mode
```

以下任一变化都必须判定MISS：

- candidate集合相同但顺序变化；
- previous episodes内容、顺序、timestamp或limit变化；
- extracted nodes映射变化；
- prompt token sequence变化；
- schema、model、temperature、max tokens或structured backend变化；
- stale路径为`NO_LLM`而exact路径需要LLM；
- stale路径需要LLM而exact路径为`NO_LLM`。

不允许使用：

- semantic similarity；
- fuzzy prompt match；
- candidate set无序比较；
- response相似度；
- “看起来结果一样”的人工判断。

## 3.3 持久化effect边界

speculative region只允许：

```text
DB read
embedding/search
deterministic candidate materialization
LLM request
response parse into private speculative artifact
```

speculative region禁止：

```text
Neo4j write
UUID publication
edge creation
summary mutation
episode publication
temporal invalidation
shared cache visible mutation outsidebackend-native KV cache
```

所有persistent effect仍在exact predecessor state下，通过Native Bind路径执行。

## 3.4 Speculation distance

当前只允许：

```text
one-version-ahead speculation
```

即source `i`只能在source `i-1`尚未发布、但`A_i`已经准备完成时投机。不得跨越多个未发布version，也不得为同一source同时生成多个stale版本。

## 3.5 NO_LLM路径

Graphiti NodeResolve可能通过deterministic resolution直接完成，不调用LLM。

v4必须把它记录为：

```text
execution_mode = NO_LLM
```

而不是伪造空prompt hash。若stale与exact都为相同`NO_LLM`结果，可以直接走Native deterministic result；若执行模式不同则MISS。

---

# 4. Resource-Gated Admission

## 4.1 总原则

投机不是在prepared artifact出现后立即启动。投机请求必须同时满足：

```text
semantic_ready
and speculation_distance == 1
and no active speculation for the same source
and configured_global_K == 2
and active_count < 2
and waiting_frontier_count == 0
and publication frontier is never delayed by admission
```

第一版最保守策略进一步要求：

```text
frontier_bind_region_count == 1
and active_frontier_count == 1
and active_count == 1
```

也就是投机只填充已经由frontier留下的第二个slot，绝不抢先于frontier启动。

## 4.2 Request profile

每个实际LLM call在提交前生成content-safe profile：

```text
request_kind             FRONTIER / COMPILE / SPECULATIVE
prompt_name
prompt_tokens_estimate
expected_output_tokens
resource_class           LONG_PREFILL / MIXED / SHORT
criticality              FRONTIER / BACKGROUND
source_sequence
state_version
exact_prefix_tokens
```

其中：

- `prompt_name`来自Graphiti已有`generate_response(..., prompt_name=...)`；
- input token数直接复用当前exact-token prefix encoder；
- expected output tokens使用目标GPU baseline中相同prompt role的中位数或EWMA；
- 不训练额外模型；
- 不读取prompt文本进行调度；
- 不把role profile写进模型prompt。

## 4.3 Candidate c01：Idle-Slot Validated Speculation

最小候选策略：

```text
frontier first
K = 2
one frontier transport active
no frontier waiter
one residual slot
→ admit at most one speculative NodeResolve
```

目的不是直接得到最终策略，而是用最小实现同时测出：

- exact fingerprint HIT/MISS；
- speculative call是否真实与frontier重叠；
- vLLM是否把第二个call转化为更高useful throughput；
- frontier interference是否可接受。

## 4.4 Candidate c02：Phase-Complementary Gate

只有c01满足“reuse机会真实存在，但frontier interference明显”时才实现c02。

c02在c01基础上增加一项单因素变化：

```text
speculative NodeResolve属于LONG_PREFILL
→ 仅在active frontier role为SHORT或decode-biased时进入第二slot
→ active frontier同样为LONG_PREFILL时等待
```

初始分类由baseline role profile生成；不得手工根据单个episode改分类。

建议的deterministic规则：

```text
prompt_tokens >= 4096 and expected_output_tokens < long_decode_cutoff
    => LONG_PREFILL

prompt_tokens < 4096
    => SHORT

otherwise
    => MIXED
```

`long_decode_cutoff`由baseline output-token分布的预注册分位数给出，并在candidate运行前写入`candidate.json`。看完candidate结果后不得改同一candidate的cutoff。

## 4.5 Candidate c03：Cost-Aware Admission

只有以下情况才允许c03：

```text
c02语义HIT和backend吞吐均为正向
但由于少量高成本MISS，wall-clock收益不稳定
```

c03只增加一个根据历史role统计得到的投机价值分数：

```text
PredictedValue
  = predicted_hit_probability × predicted_hidden_service
  - predicted_miss_cost
  - predicted_frontier_interference
```

只有`PredictedValue>0`才投机。

约束：

- 只使用之前candidate和baseline的development prefix统计；
- 不使用正式剩余histories；
- 不训练复杂模型；
- 只允许简单分桶或EWMA；
- c03是最后一个candidate，运行后必须FREEZE或STOP。

## 4.6 Prefix affinity的地位

APC/prefix reuse仅作为同分candidate的tie-break和解释指标：

```text
Resource safety / criticality
    > semantic value
    > exact prefix reuse
```

不得为了提高APC hit rate牺牲frontier或改变prompt。

---

# 5. 代码结构与复用边界

## 5.1 v3.1保持冻结

已成功的v3.1路径不得继续修改行为。v4建立独立package，并组合已有组件：

```text
paper-eval-v3/
├── src/paper_eval/membind_v4/
│   ├── __init__.py
│   ├── semantic_call.py
│   ├── node_resolve_adapter.py
│   ├── resource_profile.py
│   ├── admission.py
│   ├── runtime.py
│   ├── coordinator.py
│   ├── telemetry.py
│   ├── autoresearch.py
│   └── reducer.py
├── scripts/
│   ├── run_membind_v4_autoresearch.py
│   ├── run_membind_v4_full.py
│   └── reduce_membind_v4.py
└── tests/
    ├── test_membind_v4_semantic_call.py
    ├── test_membind_v4_node_resolve_adapter.py
    ├── test_membind_v4_admission.py
    ├── test_membind_v4_runtime.py
    ├── test_membind_v4_coordinator.py
    └── test_membind_v4_reducer.py
```

## 5.2 直接复用的v3.1模块

| v3.1模块 | v4用途 |
| --- | --- |
| `prepared_artifact.py` | Compile结果与source identity |
| `request_runtime.py` | 真实transport admission、prefix tokenization、request telemetry |
| `prefix_affinity.py` | exact token sequence与prefix metadata |
| `validated_node_resolve_runtime.py` | speculate/validate/commit状态机原型 |
| `node_resolve_speculation.py` | semantic-call identity与已有source audit |
| `coordinator.py` | arrival、compile、frontier和ordered publication骨架 |
| `live_block.py` | Graphiti、Neo4j、LLM/embedding client构建 |
| `provider_envelope.py` | 服务身份检查，直接复用，不再扩contract |

如果必须修改shared代码，只允许：

1. 抽取无行为变化的helper；
2. 增加默认关闭的v4 hook；
3. 增加content-safe telemetry字段。

每项shared修改必须有v3.1 regression test证明默认路径不变。

## 5.3 NodeResolve adapter接口

建议接口：

```python
class NodeResolveV4Adapter(Protocol):
    async def materialize(
        self,
        compile_input: object,
        prepared: PreparedArtifact,
        *,
        state_version: int,
    ) -> PreparedSemanticCall:
        ...

    async def execute(self, call: PreparedSemanticCall) -> object:
        ...

    async def interpret(
        self,
        response: object,
        exact_call: PreparedSemanticCall,
    ) -> ExactNodeResolveResult:
        ...

    async def continue_native_bind(
        self,
        compile_input: object,
        prepared: PreparedArtifact,
        node_result: ExactNodeResolveResult,
        *,
        logical_time_ns: int,
    ) -> object:
        ...
```

`materialize()`可以执行DB read和deterministic preprocessing，但不得写persistent state。

## 5.4 Coordinator插入点

在v3.1 coordinator中，source状态为：

```text
PREPARED
```

且其精确前驱尚未发布时，v4可创建一个background speculation task。

在`bind_one(sequence)`开始时：

1. 对exact predecessor重新`materialize()`；
2. 调用validated runtime判断HIT/MISS；
3. HIT则解释speculative response；
4. MISS则执行exact call；
5. 从Native NodeResolve之后继续Bind；
6. publication仍由原ROB按sequence完成。

background task必须在stream failure、source cancellation或正式结束时被取消并await，不能泄漏async task。

---

# 6. 测试驱动开发计划

## 6.1 TDD原则

本轮强调TDD，但TDD服务于快速实现和可信结果，不再演化为大型contract工程。

循环固定为：

```text
写一个能暴露错误的最小RED test
        ↓
实现最小GREEN代码
        ↓
重构并保持focused tests通过
        ↓
进入下一条行为
```

禁止：

- 先设计几十种artifact schema再写runtime；
- 为理论上不可能出现的状态建立完整外部协议；
- 每改一行都运行2251+全套测试；
- 在没有任何live趋势前继续扩展功能；
- 用mock通过代替真实Graphiti插入点。

## 6.2 第一组：Semantic-call与validation

必须先写RED tests：

1. 完全相同semantic call必须HIT；
2. candidate顺序变化必须MISS；
3. previous episodes变化必须MISS；
4. token sequence变化必须MISS；
5. schema/model/decoding identity变化必须MISS；
6. `NO_LLM↔LLM`变化必须MISS；
7. speculative response不能在未验证时进入interpret；
8. speculative response不能直接commit；
9. MISS必须exact fallback；
10. provider failure不能进入interpret或commit。

## 6.3 第二组：Resource gate

必须先写RED tests：

1. waiting frontier存在时speculation不得admit；
2. active frontier占一个slot且`K=2`时最多一个speculation；
3. active speculation不能阻止下一frontier进入剩余slot；
4. 同一source不得重复投机；
5. speculation distance大于1必须拒绝；
6. c02不得把LONG_PREFILL spec与LONG_PREFILL frontier主动配对；
7. c02可以把LONG_PREFILL spec与SHORT frontier配对；
8. cancellation后permit必须释放；
9. observed max inflight不得超过2；
10.默认v3.1 policy行为不变。

## 6.4 第三组：Coordinator与持久化语义

使用3至4个synthetic sources的fake adapter验证：

1. publication严格为`0,1,2,...`；
2. HIT路径只执行一次LLM；
3. MISS路径执行speculative＋exact两次LLM，但只提交exact result；
4. source `i`只能提交到`M_(i-1)`；
5. speculative region write probe为0；
6. bind failure使后续publication停止；
7. background task全部回收；
8. exactly-once publication。

## 6.5 第四组：真实Graphiti adapter parity

只做一个离线fixture，不启动vLLM：

- 使用固定PreparedArtifact；
- serial factorized NodeResolve与原Native NodeResolve产生相同request identity；
- deterministic preprocessing结果相同；
- candidate order相同；
- continuation调用参数相同。

这是实现边界测试，不再单独跑一轮live qualification。

## 6.6 测试运行节奏

开发时：

```bash
pytest -q \
  paper-eval-v3/tests/test_membind_v4_semantic_call.py \
  paper-eval-v3/tests/test_membind_v4_node_resolve_adapter.py \
  paper-eval-v3/tests/test_membind_v4_admission.py \
  paper-eval-v3/tests/test_membind_v4_runtime.py \
  paper-eval-v3/tests/test_membind_v4_coordinator.py
```

每个candidate前：

```bash
pytest -q paper-eval-v3/tests/test_membind_v4_*.py
```

正式全量前只运行一次：

```bash
pytest -q paper-eval-v3/tests
```

全套测试通过后，不得继续因重构美观而修改runtime。

---

# 7. 最小Telemetry与指标

## 7.1 Artifact最小化

每个autoresearch candidate只生成：

```text
candidate.json
events.jsonl
llm.jsonl
summary.json
failure.json        # 仅失败时
```

不再为candidate生成：

```text
authorization chain
multi-level contract chain
separate qualification report
manual approval artifact
multiple redundant checkpoint hashes
```

仍然要求：

- fresh run id；
- fresh namespace；
-失败不与其他candidate合并；
- raw prompt/response不写入public artifact；
- summary能回溯到events/llm trace。

完整正式run才生成最终manifest、result和reducer output。

## 7.2 Correctness指标

必须为0：

```text
direct state-order violations
wrong-version bind
out-of-order publication
speculative persistent writes
unvalidated reuse
future-evidence leakage
duplicate publication
lost publication
final graph parity violations
```

## 7.3 Speculation指标

```text
qualified NodeResolve count
speculation launched count
resource-gate rejected count
NO_LLM count
semantic HIT count
semantic MISS count
HIT rate
weighted HIT service time
exact fallback count
speculation lead time
speculation/frontier overlap time
hidden critical NodeResolve time
validation latency
MISS prompt tokens
MISS completion tokens
MISS service span
cancelled speculation count
```

## 7.4 Backend转化指标

从应用trace和vLLM `/metrics`或同等服务日志记录：

```text
prompt tokens/s
generation tokens/s
useful tokens/s
running request count over time
waiting request count over time
active_count=0/1/2 time fractions
TTFT distribution
TPOT / inter-token latency
request queue time
KV-cache usage
prefix-cache matched/cached tokens
preemption count
long-prefill + short/decode overlap time
long-prefill + long-prefill overlap time
```

若vLLM具体metric name与上述名称不同，启动时只做一次`/metrics`字段映射并写进`metric_map.json`，不得因此修改server或发明代理指标。

`GPU utilization %`只作为辅助，不单独用于证明v4有效。论文headline应使用useful throughput、makespan和freshness。

## 7.5 程序级指标

```text
construction makespan
goodput episodes/s
P50/P95/P99 freshness
mean/P50/P95 frontier Bind time
published episode count
work volume by operator
final nodes/edges/episodes
quality/retrieval overlay
```

## 7.6 Interference定义

```text
FrontierInterference
  = frontier service time under v4
  - aligned frontier service time under v3.1/reference
```

同时报告：

```text
InterferenceRatio
  = FrontierInterference / aligned frontier service time
```

不得把所有service-time波动都解释为interference；必须按相同prompt role、相近prompt-token bucket进行对齐。

---

# 8. Autoresearch协议

## 8.1 目的

Autoresearch不是自动生成大量方案，也不是无限调参。它只用于回答：

```text
v4最小机制的趋势是否符合预期？
若不完全符合，最可能是哪一个单因素导致？
是否值得进行一次针对性调整？
```

## 8.2 固定预算

```text
development history: 07741c45
initial prefix: sources 0..5
decision prefix: sources 0..11
max candidates: 3
max policy adjustments after c01: 2
K: 2 fixed
W/lookahead: v3.1正式值 fixed
compile workers: v3.1正式值 fixed
prompt/schema/model/backend: fixed
```

正式剩余histories在FREEZE前不得访问结果。

## 8.3 Baseline prefix reference

优先直接从目标GPU上刚完成的baseline/v3.1 trace裁出：

```text
sources 0..5
sources 0..11
```

不重新运行baseline prefix。

只有当v4不在baseline重跑的同一物理GPU/同一服务封套上运行时，才允许运行一次6-source v3.1 local anchor，用于估计GPU间偏差；不得重跑完整baseline。

若两个GPU的hardware/clock/power limit不同，或local anchor相对新baseline prefix偏差超过5%，跨GPU结果不得作为最终主表。必须把v4移到baseline GPU，或在v4 GPU补一条必要的aligned comparator。

## 8.4 单个candidate循环

每个candidate执行：

```text
1. 写candidate.json，冻结唯一变化
2. focused TDD tests
3. fresh namespace preflight
4. 运行sources 0..5
5. safety fail → 立即停止
6. 趋势明显负向 → 立即停止或按决策树调整
7. 趋势非负且机制被触发 → 扩到sources 0..11
8. reduce summary.json
9. 自动生成recommendation: FREEZE / TUNE_ONCE / STOP
```

不要为0..5和0..11建立两套方法身份；它们属于同一candidate的逐步延长。若0..5失败，0..11不得启动。

## 8.5 Candidate顺序

```text
c01 = IDLE_SLOT_VALIDATED_SPEC
c02 = PHASE_COMPLEMENTARY_VALIDATED_SPEC   # 条件执行
c03 = COST_AWARE_VALIDATED_SPEC            # 条件执行，最后一个
```

禁止平行尝试多个随机heuristic。

## 8.6 预注册趋势判定

### Safety gate

任一非0 correctness violation：

```text
STOP_AND_FIX_CORRECTNESS
```

只允许修复能由确定性test复现的bug。修复后重用同一candidate id并增加attempt id，不得把bug fix算新候选。

### Mechanism gate

必须观察到：

```text
qualified NodeResolve > 0
speculation launched > 0
exact validation completed > 0
```

否则表示机制未触发，先修集成，不讨论性能。

### Semantic opportunity gate

若12-source完整证据显示：

```text
HIT == 0
or hidden critical NodeResolve time == 0
```

则：

```text
STOP_V4_NODE_RESOLVE
```

不得扩展Attributes/EdgeResolve救结果。

### Backend conversion gate

与aligned prefix reference相比：

```text
useful token throughput不应下降超过5%
frontier P95 service不应上升超过5%
long+long harmful overlap不应显著增加
```

至少满足以下一项：

```text
makespan改善≥5%
or P95 freshness改善≥5%
```

并且必须同时看到至少一项mechanistic evidence：

```text
active_count=2 useful fraction增加
or hidden critical NodeResolve time > 0
or useful token throughput增加
```

### Decision

```text
安全 + HIT/overlap存在 + wall-clock改善
    => FREEZE

安全 + HIT/overlap存在 + backend吞吐改善
但frontier interference抵消wall-clock
    => c01→c02，或c02→c03

安全 + HIT/overlap存在
但backend和wall-clock都无改善
    => STOP，不继续调通用并发

MISS waste使总趋势为负
    => 若c02尚未执行且role gate可直接解释，执行c02
       否则STOP

任何正式阈值之外的“感觉可能再调一下”
    => STOP
```

## 8.7 防止结果导向调参

Autoresearch必须遵守：

- candidate变化在运行前写入`candidate.json`；
- 每次只允许一个可解释的策略变化；
- 不换source prefix；
- 不挑选表现最好的单episode；
- 不删除失败candidate；
- 不根据正式剩余history调policy；
- 不使用正式全量结果返回开发循环；
- 最多3个candidate；
- 第一个满足FREEZE条件的candidate立即冻结，不继续寻找更快版本。

---

# 9. 分阶段执行计划

## P0：登记成功结果与公平封套

目标：确认可比较性，不重跑。

操作：

1. 定位v3.1成功artifact；
2. 定位新baseline artifact；
3. 记录目标物理GPU UUID/name；
4. 记录vLLM版本与完整启动参数；
5. 记录model/tokenizer/prompt/schema/arrival trace identity；
6. 确认v4将使用同一目标GPU与服务配置；
7. 从baseline trace生成role profile和0..5/0..11 reference。

输出：

```text
paper-eval-v3/artifacts/paper_eval/membind_v4/BASELINE_BINDING.json
paper-eval-v3/artifacts/paper_eval/membind_v4/ROLE_PROFILE.json
paper-eval-v3/artifacts/paper_eval/membind_v4/PREFIX_REFERENCE.json
```

P0只记录事实，不增加新的live run。

## P1：TDD实现semantic call与adapter

顺序：

1. semantic-call fingerprint tests；
2. `NO_LLM` tests；
3. no-write boundary tests；
4. Graphiti NodeResolve materialization adapter；
5. serial factorized parity fixture；
6. focused suite通过。

P1完成条件：

```text
factorized serial request identity == Native request identity
speculative region persistent writes == 0
all focused semantic tests PASS
```

P1不启动网络请求。

## P2：TDD实现resource gate与coordinator

顺序：

1. resource profile；
2. c01 gate；
3. background speculation task；
4. exact validation与fallback；
5. frontier continuation；
6. cancellation/task cleanup；
7. synthetic 4-source integration test；
8. v3.1 regression tests。

P2完成条件：

```text
K<=2
frontier always admitted before waiting speculation
ordered publication
MISS exact fallback
no stale result commit
no leaked async task
```

## P3：c01短跑

推荐命令形态：

```bash
python paper-eval-v3/scripts/run_membind_v4_autoresearch.py \
  --candidate c01 \
  --history-id 07741c45 \
  --source-count 6 \
  --policy IDLE_SLOT_VALIDATED_SPEC \
  --fresh-namespace
```

6-source通过后扩展：

```bash
python paper-eval-v3/scripts/run_membind_v4_autoresearch.py \
  --candidate c01 \
  --history-id 07741c45 \
  --source-count 12 \
  --policy IDLE_SLOT_VALIDATED_SPEC \
  --fresh-namespace
```

随后：

```bash
python paper-eval-v3/scripts/reduce_membind_v4.py \
  --candidate-root <c01-root> \
  --reference paper-eval-v3/artifacts/paper_eval/membind_v4/PREFIX_REFERENCE.json
```

P3结束必须得到：

```text
FREEZE
TUNE_TO_C02
STOP
```

不得输出“继续收集更多证据再决定”。

## P4：条件调整

### P4-A：c02

只在c01已有HIT/overlap、但出现可解释的phase interference时执行。

实现工作仅包括：

- role-based phase class；
- long+long禁止规则；
-对应focused tests；
- 6-source→12-source同一流程。

### P4-B：c03

只在c02已有backend正向趋势、但少量高成本MISS抵消结果时执行。

实现工作仅包括：

- 简单role/bucket value estimator；
- `PredictedValue>0` gate；
-对应focused tests；
- 6-source→12-source同一流程。

c03结束后无论结果如何都必须FREEZE或STOP。

## P5：Freeze

当某candidate满足FREEZE条件时，立即生成：

```text
V4_FROZEN_METHOD.json
V4_FROZEN_METHOD.md
```

内容包括：

```text
candidate id
code commit
policy
all thresholds
role profile identity
K/W/workers
model/backend identity
prompt/schema identity
development prefix identity
focused/full test result
autoresearch candidate ledger
```

Freeze之后禁止：

- 修改策略；
- 调threshold；
- 修改prompt；
- 增加candidate；
-查看一个正式history后回滚调参。

## P6：正式全量v4

运行固定4 histories、188 episodes，与新baseline完全相同：

```text
07741c45
6071bd76
a2f3aa27
b6019101
```

每个history：

- fresh namespace；
- fresh run id；
-相同arrival trace；
-相同model/backend；
-相同embedding/Neo4j；
-相同K/W/workers；
-相同structured-output retry政策；
- frozen v4 policy。

推荐执行：

```bash
pytest -q paper-eval-v3/tests

python paper-eval-v3/scripts/run_membind_v4_full.py \
  --frozen-method paper-eval-v3/artifacts/paper_eval/membind_v4/V4_FROZEN_METHOD.json \
  --histories 07741c45,6071bd76,a2f3aa27,b6019101 \
  --fresh-namespaces
```

运行过程中：

- 可以checkpoint和resume现有完成单位；
- provider失败按已冻结retry政策处理；
- structured-output错误不得临时修改max tokens或prompt；
- 单history失败则标记失败，不把partial结果并入主表；
- 不因某个中间指标不好而在线修改policy。

## P7：Reducer与最终结果

正式run结束后一次性生成：

```text
V4_FULL_RESULT.json
V4_MAIN_TABLE.json
V4_MECHANISM_TABLE.json
V4_CORRECTNESS_TABLE.json
V4_QUALITY_OVERLAY.json
V4_FINAL_REPORT.md
```

Reducer必须同时读取：

- 新baseline结果；
- v3.1成功结果；
- frozen v4结果；
-已有quality/retrieval evaluator。

不重跑construction来补漏指标。缺失但不影响主结论的指标标为`NOT_AVAILABLE`。

---

# 10. 最终比较矩阵

## 10.1 主表

至少包含：

| Method | Makespan | Speedup vs U0 | Speedup vs v3.1 | Goodput | P50 freshness | P95 freshness | P99 freshness | Direct violations |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| U0 | | | | | | | | |
| A0 | | | | | | | | |
| P(C=2) | | | | | | | | |
| MemBind v3.1 | | | | | | | | |
| MemBind v4 | | | | | | | | |

v4的primary comparator是v3.1；U0/A0/P(C=2)用于展示相对Native与naive parallelism的位置。

## 10.2 机制表

| Metric | v3.1 | v4 | 解释 |
| --- | ---: | ---: | --- |
| NodeResolve qualified | | | |
| Spec launched | 0 | | |
| HIT / MISS | 0 | | |
| Hidden critical time | 0 | | |
| MISS waste tokens | 0 | | |
| Validation overhead | 0 | | |
| Frontier interference | | | |
| Useful token throughput | | | |
| active=2 useful fraction | | | |
| APC cached tokens | | | secondary |

## 10.3 Work-volume公平性

必须报告：

```text
total LLM logical calls
actual transport attempts
prompt tokens
completion tokens
embedding calls
DB operations
nodes/edges/episodes
speculative wasted calls/tokens
```

v4允许因MISS增加工作，但必须单独披露，不能把额外工作隐藏在aggregate throughput中。

## 10.4 Quality

继续复用现有：

```text
Recall@1/3/5/10
MRR
nDCG@10
Reader/Judge QA
graph quality overlay
latest-valid/conflict指标（若现有evaluator可得）
```

v4必须满足quality non-degraded。开发阶段使用已有Qwen evaluator；论文最终如计划所述再用GPT-4o重判，不阻塞construction结果。

---

# 11. 公平性与可重复性

## 11.1 必须相同

```text
dataset/source manifests
history order
arrival traces
Graphiti version
model and weights
vLLM version
max model length
GPU memory utilization
APC/chunked prefill
scheduling policy
structured-output backend
completion caps
embedding model
Neo4j version/config
K/W/workers
prompt/schema
retry policy
quality evaluator
```

## 11.2 唯一方法差异

v3.1与v4之间只允许：

```text
NodeResolve validated speculation
resource-gated admission
required content-safe telemetry
```

## 11.3 GPU要求

最优方案是v4与刚重跑baseline使用同一物理GPU和同一vLLM服务封套。

如果使用两张相同型号GPU：

- 记录GPU UUID、power limit、clock、driver、温度范围；
- 使用一次6-source v3.1 local anchor验证设备差异；
- 偏差≤5%才允许将跨GPU结果放入同一开发主表；
- 偏差>5%时必须在同一GPU补aligned comparator。

这一步是轻量设备校准，不是重新跑完整baseline。

---

# 12. STOP Rules

## STOP-1：语义无机会

```text
12-source qualified NodeResolve存在
但HIT=0或hidden critical time=0
```

动作：停止v4 NodeResolve，不扩其他operator。

## STOP-2：投机伤害backend

```text
useful throughput下降>5%
and frontier P95上升>5%
and wall-clock无改善
```

动作：若尚未执行c02且问题明确来自long+long pairing，执行c02；否则停止。

## STOP-3：MISS waste抵消收益

```text
MISS waste + interference >= hidden HIT benefit
```

动作：最多进入一次cost-aware c03；c03仍不改善则停止。

## STOP-4：正确性失败

任一：

```text
unvalidated reuse
wrong-version commit
speculative persistent write
out-of-order publication
final graph parity violation
future evidence leakage
```

动作：立即停止live run。只有存在确定性RED test的实现bug可以修复；若来自方法本身则v4失败。

## STOP-5：Candidate预算耗尽

```text
c01,c02,c03已运行
```

动作：FREEZE最佳首个合格candidate或STOP，不创建c04。

## STOP-6：正式全量已启动

正式188-episode v4启动后：

```text
no method change
no threshold change
no prompt change
no extra candidate
```

无论最终结果正负，reducer完成后本轮STOP。

---

# 13. 预计时间安排

## Day 1：实现最小闭环

```text
P0结果登记与role profile
P1 semantic call / adapter TDD
P2 resource gate / coordinator TDD
focused tests
```

目标：当天结束前具备可运行c01的代码，不写长篇资格报告。

## Day 2：Autoresearch短跑与冻结

```text
c01 6-source
c01 12-source
自动reducer
必要时c02或c03之一
FREEZE或STOP
```

若c01直接满足FREEZE，不运行c02/c03。

## Day 3及之后：正式全量

```text
full test suite一次
4 histories / 188 episodes v4
reducer
quality overlay
final report
STOP
```

运行时间由真实vLLM服务决定，不因预计耗时增加额外小实验。

---

# 14. Agent执行规则

交给Codex/AutoResearch agent时，必须在任务开头附上：

```text
1. v3.1已成功，不得重跑或重构v3.1。
2. baseline已在目标GPU重跑，不得重新设计baseline。
3. 当前目标是尽快得到v4正式结果，不是继续qualification。
4. 所有实现采用TDD：先最小RED，再GREEN，再focused regression。
5. Autoresearch最多c01/c02/c03三个candidate，每次只改一个因素。
6. 先6-source，再12-source；第一个满足FREEZE的candidate立即冻结。
7. 正式188-episode启动后禁止修改method。
8. 不增加benchmark、operator、K、W、backend或prompt变化。
9. 不创建新的重型contract/authorization体系。
10. 每次完成后报告：改了什么、测试结果、运行结果、下一决策。
```

每轮agent输出必须采用固定格式：

```text
STATUS: IMPLEMENTED / RUNNING / FREEZE / TUNE_ONCE / STOP

CHANGE:
- 唯一代码或policy变化

TEST:
- RED test
- GREEN result
- focused regression

RUN:
- source coverage
- safety
- HIT/MISS
- backend conversion
- makespan/freshness trend

DECISION:
- FREEZE / c02 / c03 / STOP

NEXT:
- 只有一个明确动作
```

禁止输出几十项新的未来工作。

---

# 15. 可能的最终结果与论文表达

## Outcome A：v4显著有效

条件：

- exact correctness通过；
- NodeResolve HIT和overlap存在；
- useful backend throughput提升或资源组合改善；
- makespan/freshness相对v3.1改善；
- quality不下降。

论文结论：

> v3.1通过State-Cut暴露evidence-level parallelism；v4进一步通过validated state speculation暴露state-bound slack，并使用resource-gated admission将该slack转化为单GPU serving收益。

## Outcome B：语义HIT高但GPU不转化

条件：

- exact HIT存在；
- speculation能够覆盖未来NodeResolve；
- 但vLLM throughput/makespan没有明显改善。

论文结论：

> semantic parallelism不是性能充分条件；当前单GPU backend已经接近饱和。v4作为negative mechanism result保留，正式headline仍为v3.1。

不得声称v4提升GPU利用率。

## Outcome C：NodeResolve不稳定

条件：

- exact fingerprint HIT低或为0；
- previous episodes/candidates频繁变化；
- MISS waste抵消收益。

论文结论：

> Graph memory state dependence在NodeResolve处真实存在，validated speculation无法获得足够复用。该结果支持State-Cut边界，而不继续扩展投机。

## Outcome D：v4伤害性能

如实报告：

- speculative waste；
- frontier interference；
- backend saturation；
- 停止原因。

不通过扩大并发、换backend或改prompt挽救。

---

# 16. Definition of Done

本轮只有满足以下全部条件才完成：

```text
[ ] v3.1成功结果与新baseline结果已登记
[ ] v4 semantic-call/adapter/resource-gate/coordinator已实现
[ ] focused TDD tests通过
[ ] v3.1 default-path regression通过
[ ] c01至少完成6-source和必要的12-source判断
[ ] candidate总数<=3
[ ] 得到FREEZE或明确STOP
[ ] 若FREEZE，完整4 histories / 188 episodes已运行
[ ] correctness/mechanism/performance/work-volume表已生成
[ ] quality overlay已复用
[ ] 最终报告明确v4是否优于v3.1及原因
[ ] 正式结果后没有继续调method
[ ] 项目进入STOP而不是新一轮qualification
```

最重要的完成标准不是“实现了很多v4组件”，而是：

> **在有限开发循环后得到一份完整、可解释、可比较的v4正式结果，并能够明确回答它是否真正把语义并行转化为vLLM/backend收益。**

---

# 17. 主要参考文献与实现

1. Sarathi-Serve, OSDI 2024.  
   https://www.usenix.org/conference/osdi24/presentation/agrawal

2. NanoFlow, OSDI 2025.  
   https://www.usenix.org/conference/osdi25/presentation/zhu-kan

3. BlendServe, ASPLOS 2026.  
   https://dl.acm.org/doi/10.1145/3779212.3790133

4. Agentix, NSDI 2026.  
   https://www.usenix.org/conference/nsdi26/presentation/luo

5. PLA-Serve, MLSys 2026.  
   https://proceedings.mlsys.org/paper_files/paper/2026/hash/bbb7506579431a85861a05fff048d3e1-Abstract-Conference.html

6. FlashAgents, MLSys 2026.  
   https://proceedings.mlsys.org/paper_files/paper/2026/hash/9a6f6e0d6781d1cb8689192408946d73-Abstract-Conference.html

7. ContextPilot, MLSys 2026.  
   https://proceedings.mlsys.org/paper_files/paper/2026/hash/b0131b6ee02a00b03fc3320176fec8f5-Abstract-Conference.html

8. SGLang, NeurIPS 2024.  
   https://proceedings.neurips.cc/paper_files/paper/2024/hash/724be4472168f31ba1c9ac630f15dec8-Abstract-Conference.html

9. vLLM scheduling与custom scheduler文档。  
   https://docs.vllm.ai/en/v0.26.0/cli/run-batch/

10. MemBind repository.  
    https://github.com/ysyjsk/MemBind

---

# 18. 当前唯一下一步

现在不要再写新的v4资格计划，也不要继续分析W/lookahead。

唯一下一步是：

```text
完成P0结果登记
        ↓
为semantic-call exact validation写第一组RED tests
        ↓
实现NodeResolve adapter最小GREEN路径
        ↓
当天推进到c01 6-source短跑
```

