# MemBind 基础验证实验协议优化：Characterization、Fairness 与环境噪声控制

> **文档状态**：Protocol Addendum / Freeze Candidate v1.1  
> **适用范围**：在现有 `MemBind_basic_validation_experiment.md`（Pilot Protocol v1.0）之上增加系统级 characterization、公平性控制与环境噪声控制。  
> **优先级**：本文件用于补充而不是推翻原协议。若两份文件冲突，除本文件明确标记为“替换原条款”的内容外，仍以原 Pilot Protocol v1.0 的 correctness、数据划分、模型版本、Graphiti commit、Go/No-Go 和 Evidence Fence 约束为准。  
> **核心目标**：不仅回答“MemBind 是否更快、是否保持语义”，还要严谨回答“Graphiti 原生瓶颈在哪里、MemBind 隐藏了哪部分工作、收益在什么负载下出现、网络/模型服务/缓存/系统噪声是否可能造成伪加速”。

---

## 0. 给执行 Agent 的最高优先级指令

### 0.1 当前阶段约束

1. **不得中断、覆盖或修改正在运行的 smoke attempt。** 当前 smoke attempt 必须自然结束并保留 artifact。
2. **只有当前 correctness smoke 达到协议要求后，才允许加入本文件的 instrumentation 并进入 characterization。**
3. 新 instrumentation 必须先通过 TDD，不允许边跑正式实验边修改埋点。
4. Characterization 的结果可以用于解释系统、发现瓶颈和决定后续研究方向，**不得用于事后修改已经冻结的 primary outcome、evaluation split 或原始 Go/No-Go 阈值**。
5. 如果 characterization 发现当前机制的 theoretical headroom 很低，可以提前记录 `mechanism_warning`，但正式 Pilot 是否 GO/NO-GO 仍按冻结协议执行，除非用户显式批准新 protocol version。

### 0.2 本文件明确禁止的行为

执行 Agent **MUST NOT**：

- 为了得到更好 speedup，只给 M2 更快的 endpoint、更大的 HTTP pool、更大的 DB pool 或更多 GPU；
- 因为某个方法碰到网络抖动而只重跑该方法并覆盖原结果；
- 将网络 RTT 从主端到端 latency 中“数学减掉”后作为主结果；
- 在看到 M0/M1/M2 性能以后再挑选 arrival rate、并发度或 workload；
- 以 episode 为独立样本做显著性检验；
- 因为 P99 好看就把 P99 升格为主指标；
- 用 response cache 测 performance lane；
- 让 vLLM prefix cache、embedding cache 或 Neo4j graph state 在不同方法间存在不对称的跨 run carry-over；
- 静默丢弃 transport error、HTTP 5xx、OOM、timeout、DB error 或 structured-output retry；
- 为了 deterministic replay 修改 M2 专属 prompt；所有会影响 prompt 的 deterministic normalization 必须统一作用于 M0/M1/M2，并单独验证其相对 upstream Graphiti 的语义影响。

---

# 1. 为什么需要升级现有协议

现有 Pilot Protocol 已经较完整地冻结了：

- M0 Native-Serial；
- M1 WholeUpdate-Parallel；
- M2 MemBind：Parallel Semantic Compile + Latest-State Bind + Source-Ordered Commit；
- correctness replay；
- live performance lane；
- open-loop arrival；
- P95 arrival-to-publish、makespan、canonical graph parity、retrieval guardrail；
- instance-level paired bootstrap。

但当前仍存在三个明显缺口。

## 1.1 缺少原生 Graphiti 的瓶颈剖析

现协议对 M0 最低只要求：

```text
add_episode_start
add_episode_end
publish_time
```

这只能回答：

> 一个原生 `add_episode()` 总共花了多久？

无法回答：

> 时间究竟花在 extraction、embedding、candidate search、entity resolution、edge resolution、invalidation、summary/attributes 还是 DB publication？

也无法证明：

> MemBind 想并行的 Compile 部分是否真的占原生关键路径的大头。

因此需要将 M0 从“black-box baseline”升级为“phase-traced native baseline”。

## 1.2 远程模型服务使网络与服务端排队成为潜在混杂因素

当前拓扑是：

```text
本地：Graphiti + replay driver + Neo4j
                |
                | LAN / HTTP
                v
远端：Qwen3-32B-FP8 vLLM construction server
      Qwen3-Embedding-0.6B server
```

因此客户端观测到的一次 LLM 调用时间至少包含：

```text
client serialization
+ socket / network
+ vLLM admission / queueing
+ GPU inference
+ response transmission
+ client parse
```

M2 又会主动改变请求并发和时间分布，所以不能简单假设“API latency = GPU compute latency”。必须同时保留端到端指标和机制分解指标。

## 1.3 当前 workload 只有一个冻结 arrival interval，足够做主比较，但不足以解释系统行为

主 evaluation 使用：

```text
DELTA_MS = median(native_episode_service_ms)
```

作为单一 deterministic open-loop workload 是合理的，因为它可复现、可配对并接近 backlog 形成区间。

但如果只跑这一点，就无法判断：

- MemBind 只是极端 overload 才有效，还是正常负载也有效；
- 瓶颈究竟是 Compile、Bind、remote vLLM capacity，还是 source-order frontier stall；
- C8 是否已经饱和，C4 是否就足够；
- service-time long tail 是否造成 queue amplification。

因此正式 64-run 前应增加一个小而严格的 **Phase 4.5 Characterization Gate**。

---

# 2. 系统顶会实验方法带来的直接原则

本协议优化参考以下系统工作，不照搬其 workload，而抽取与 MemBind 最相关的实验原则。

## 2.1 Pie，SOSP 2025

**Pie: A Programmable Serving System for Emerging LLM Applications**。

与本实验最相关的做法：

1. Pie 在同一 GCP server 上放置被比较的 serving systems，并从**远程 Python client**测量端到端 latency；说明远程网络并不必然要从 E2E latency 中排除，只要它属于部署路径且比较条件一致。
2. 为了避免把 kernel/backend 差异误认为系统架构收益，Pie 让 Pie、vLLM、SGLang 使用相同 FlashInfer GPU backend。
3. baseline 使用相同 high-level application logic，仅改变 serving architecture。

**对 MemBind 的启示**：

> 主性能指标应保留“本地 runtime → 远程 vLLM → 返回”的真实端到端路径；同时必须保证 M0/M1/M2 使用相同 endpoint、HTTP stack、模型、backend 与资源上限。不要把网络从 E2E 主结果中人为扣掉，而应增加独立网络与服务端 telemetry 来判断收益是否由网络噪声造成。

## 2.2 Parrot，OSDI 2024

**Parrot: Efficient Serving of LLM-based Applications with Semantic Variable**。

与本实验最相关的做法：

1. Parrot 明确把 client ↔ LLM service 的网络延迟和重新排队视为 application E2E latency 的组成部分。
2. 其 workload 根据实际测量向请求加入约 200–300 ms 随机网络延迟，显式研究网络交互对 agentic / chained LLM workflow 的影响。
3. 论文同时给出 latency breakdown，而不是只给最终 speedup。

**对 MemBind 的启示**：

> 网络不是“必须删除的噪声”；如果部署天然跨机器，它是系统路径的一部分。但必须把“网络本身的随机波动”和“由于方法改变请求交互方式产生的网络开销差异”区分开。前者要控制，后者属于真实系统效果。

## 2.3 DistServe，OSDI 2024

**DistServe: Disaggregating Prefill and Decoding for Goodput-optimized Large Language Model Serving**。

与本实验最相关的做法：

1. 明确报告 testbed 与 cross-node bandwidth（其主集群为 25 Gbps 跨节点网络）。
2. 数据集没有真实 arrival timestamp 时，用 Poisson process 合成到达，并扫多个 request rates。
3. 将 queueing delay 与 execution time 区分，并进行 latency/communication breakdown。
4. 使用 SLO attainment、TTFT/TPOT 等面向服务体验的端到端指标，而不是只看 kernel 时间。

**对 MemBind 的启示**：

> 当前 deterministic open-loop trace 可以作为 primary workload，但至少要增加小型 arrival-rate sensitivity；同时明确区分 `queue_wait` 与实际 service，并记录远程通信条件。

## 2.4 Llumnix，OSDI 2024

**Llumnix: Dynamic Scheduling for Large Language Model Serving**。

与本实验最相关的做法：

1. 所有 scheduler baseline 使用同一 vLLM underlying inference engine，把比较焦点限制在 scheduling/runtime。
2. 同时使用 Poisson 与 Gamma arrival；Gamma 通过 CV 改变 burstiness。
3. 使用 10,000-request traces，并观察 P99 tail latency、queueing 与负载变化。
4. 同时使用真实长度分布和生成的 long-tail 分布。

**对 MemBind 的启示**：

> M0/M1/M2 必须共享同一模型 server；系统故事需要 tail freshness 与 queueing，而不只是平均 service time。Pilot 不必复制 10k requests，但应至少做 deterministic 主 trace + 小规模 stochastic/bursty sensitivity。

## 2.5 ContextPilot，MLSys 2026

**ContextPilot: Fast Long-Context Inference via Context Reuse**。

与本实验最相关的做法：

1. 对所有 baseline 调参以获得合理的最佳性能/精度，并尽量与各自论文的最佳设置对齐。
2. 对 Mem0 等在线场景明确采用 **online cold-start**，而不是把离线预构建收益混入在线结果。
3. 同时报告性能和 accuracy/quality guardrail。

**对 MemBind 的启示**：

> M1 不能故意固定在一个明显不佳的并发度来凸显 M2；C8 可以保留为 iso-resource 主比较，但必须通过 calibration C-sweep 检查 M1 的 best-tuned concurrency，并作为 strong-baseline secondary result。所有 measured run 必须明确 cache cold/warm state。

## 2.6 vLLM，SOSP 2023

**Efficient Memory Management for Large Language Model Serving with PagedAttention**。

相关原则：

> LLM serving 系统应在相近 latency/SLO 下比较可持续 request rate/throughput，而不是只报告孤立请求的最快 latency。

**对 MemBind 的启示**：

> `makespan` 与 `arrival_to_publish` 都要保留；后续若扩展正式论文，应增加可持续 arrival rate / freshness SLO goodput，但当前 Pilot 不需要立即扩展成大规模 serving benchmark。

---

# 3. 升级后的研究问题

正式实验必须区分下面五类问题。

## RQ0：原生 Graphiti 为什么慢？

回答：

- `add_episode()` 的关键路径由哪些阶段组成？
- 哪些阶段的 wall time / LLM time 占主要比例？
- 瓶颈是否随 graph size、episode index、candidate count 增长而迁移？

## RQ1：有多少原生工作具有 MemBind 可利用的提前执行空间？

回答：

- Compile-eligible critical-path fraction；
- State-dependent Bind fraction；
- DB/commit fraction；
- 这些比例是否稳定。

## RQ2：MemBind 是否真的把这部分工作隐藏掉？

回答：

- Compile hiding ratio；
- queue amplification reduction；
- Bind utilization；
- ready-artifact queue；
- source-frontier stall。

## RQ3：端到端收益是否不是实验环境伪影？

回答：

- 相同资源、相同模型、相同 endpoint、相同 client stack；
- 网络 baseline 稳定；
- server queue / GPU load 可解释；
- prefix/embedding cache 不存在跨方法 carry-over；
- run order 不与方法绑定；
- instrument overhead 足够低。

## RQ4：收益在什么 workload regime 下成立？

回答：

- underload / near-saturation / overload；
- deterministic / Poisson；
- C1/C2/C4/C8；
- 不要求在 Pilot 中建立完整 queueing theory，只做小型 mechanism characterization。

---

# 4. 指标重新分层

必须区分 **Primary outcome、Guardrail、Mechanism、Characterization**。禁止在报告时把后两类事后升级为主指标。

## 4.1 Primary outcomes：保持原协议

### 性能

1. `P95 arrival_to_publish_ms`
2. `instance_makespan_ms`

### 正确性

3. `canonical_graph_parity`
4. `Evidence Recall@10` guardrail

正式 Go/No-Go 继续按原协议。

## 4.2 Cost / fairness guardrails

每个 run 记录：

```text
llm_input_tokens
llm_output_tokens
llm_call_count
embedding_call_count
http_request_count
db_query_count
db_write_count
structured_retry_count
transport_retry_count
bytes_sent
bytes_received
```

其中：

- `transport_retry_count` 原则上 performance lane 必须为 0；
- frozen structured-output bounded retry 继续按原协议计入；
- 若某方法因为自身并发导致 server 429/5xx，不能简单当作“网络噪声”删除。

## 4.3 Mechanism metrics：新增，必须实现

### M1. Native Compile-eligible critical-path fraction

对 M0 中被 classification 为 Compile-eligible 的 span 做**时间区间并集**，不能简单把嵌套 span duration 相加：

```text
T_compile_eligible_union
F_compile = T_compile_eligible_union / T_add_episode
```

### M2. State-dependent fraction

```text
T_bind_union
F_bind = T_bind_union / T_add_episode
```

### M3. Commit / publication fraction

```text
T_commit_union
F_commit = T_commit_union / T_add_episode
```

检查：

```text
F_compile + F_bind + F_commit + F_other ≈ 1
```

允许少量 instrumentation/unclassified gap，但必须报告。

### M4. Compile hiding ratio

定义：

```text
compile_total_work_ms
compile_exposed_on_publish_path_ms

compile_hiding_ratio =
    1 - compile_exposed_on_publish_path_ms / compile_total_work_ms
```

`compile_exposed_on_publish_path_ms` 指 episode 已到达且其 publish frontier 最终仍必须等待当前 episode Compile 的时间；不是简单 `compile_ms`。

### M5. Queue amplification

每 episode：

```text
service_ms = publish_time - first_work_start
queue_wait_ms = first_work_start - arrival_time
freshness_ms = publish_time - arrival_time

queue_amplification = freshness_ms / service_ms
```

同时直接报告 `queue_wait_ms`，不要只报告 ratio。

### M6. Source-frontier stall

仅 M2：

```text
frontier_stall_ms =
    Bind worker 空闲，且存在 source_sequence > frontier 的已完成 artifact，
    但 frontier 所需下一个 artifact 尚未 ready 的累计时间

frontier_stall_ratio = frontier_stall_ms / makespan_ms
```

这是检测 Compile straggler 引入新 HOL blocking 的关键指标。

### M7. Pipeline utilization

```text
compile_worker_utilization =
    sum(compile_worker_busy_ms) / (C * makespan_ms)

bind_utilization =
    bind_busy_ms / makespan_ms
```

### M8. Ready queue

时间序列：

```text
compiled_ready_count(t)
compile_inflight_count(t)
bind_busy(t)
source_frontier(t)
arrival_queue_depth(t)
```

最终至少报告：

```text
mean_ready_queue_depth
p95_ready_queue_depth
max_ready_queue_depth
```

## 4.4 Characterization metrics：新增，解释性使用

```text
phase latency vs episode index
phase latency vs graph_node_count
phase latency vs graph_edge_count
resolve latency vs candidate_count
LLM latency vs prompt type
LLM input tokens vs prompt type
service-time CV / SCV
speedup vs offered load
speedup vs compile concurrency
```

这些指标**不得用于选择 evaluation instance**。

---

# 5. M0 Native Graphiti phase-level tracing

## 5.1 原则

必须根据 pinned Graphiti `v0.29.3 / commit 021d3a5` 的真实源码边界埋点，不能为了匹配 MemBind 的名词人为伪造阶段。

建议至少记录以下 logical span。若真实源码名称不同，执行 Agent应在 `phase_map.json` 中保存“logical phase → exact Python function / source location”的映射。

```text
add_episode
├── source_context_prepare
├── node_extract
├── edge_extract
├── node_embedding
├── edge_embedding
├── node_candidate_search
├── node_resolve_llm
├── edge_candidate_search
├── edge_resolve_llm
├── invalidation
├── attribute_or_summary_update
└── db_publication
```

如果某些阶段实际合并在一个上游函数中：

- 允许记录 coarse span；
- 禁止通过复制/重写 Graphiti 逻辑来人为拆开；
- 可以对子函数 wrapper/monkey-patch 做 timing，但必须保证输入输出完全透明。

## 5.2 Span schema

每个 span 至少写：

```json
{
  "run_id": "...",
  "question_id": "...",
  "method": "M0",
  "episode_sequence": 17,
  "span_id": "...",
  "parent_span_id": "...",
  "phase": "edge_resolve_llm",
  "semantic_class": "bind",
  "start_ns": 0,
  "end_ns": 0,
  "duration_ns": 0,
  "status": "ok",
  "exception_class": null
}
```

`semantic_class` 只能取：

```text
compile_eligible
bind_state_dependent
commit
other
```

该分类必须在看到 formal performance result 前冻结到：

```text
artifacts/characterization/phase_map.json
```

## 5.3 禁止 duration double-count

如果：

```text
node_resolution
└── llm_call
```

则：

```text
node_resolution.duration + llm_call.duration
```

不能直接相加作为总 work。

分析必须至少输出两套值：

1. **inclusive duration**：用于理解函数 latency；
2. **exclusive / interval-union duration**：用于计算 phase fraction。

---

# 6. LLM request-level tracing

每个 construction LLM request 必须记录：

```text
run_id
question_id
episode_sequence
prompt_name
prompt_hash
request_id
client_send_ns
client_first_byte_ns        # 若 HTTP client 可可靠获得
client_done_ns
client_observed_ms
input_tokens
output_tokens
finish_reason
structured_retry_index
transport_retry_index
inflight_llm_at_submit
inflight_llm_at_finish
response_hash               # 允许，禁止泄漏 API key
```

如果能从 vLLM server 获取 request-level queue/service 信息，再附加：

```text
server_queue_ms
server_prefill_ms / model_service_ms（若实际可获得）
server_running_at_arrival
server_waiting_at_arrival
```

如果 pinned vLLM 无法可靠提供 request-level server timestamps：

> **不得虚构 server compute time。**

此时用：

- `client_observed_ms`；
- run-level vLLM metrics；
- GPU telemetry；
- 网络 pre/post probes；

完成间接分解，并在报告中说明限制。

## 6.1 Prompt-type breakdown

至少输出：

| prompt type | calls | input tokens | output tokens | P50 client ms | P95 client ms | share of total client LLM time |
|---|---:|---:|---:|---:|---:|---:|
| node extraction | | | | | | |
| edge extraction | | | | | | |
| node resolution | | | | | | |
| edge resolution | | | | | | |
| attribute/summary | | | | | | |

这样可以回答：

> 原生 Graphiti 的 latency 到底来自“少数超长 extraction”还是“大量 resolution calls”。

---

# 7. DB / Search tracing

不要求把 Neo4j 每条 Cypher 都做复杂 tracing，但至少聚合四类：

```text
previous_episode_lookup
entity_candidate_search
edge_candidate_search
write_publication
```

每个 episode 记录：

```text
db_query_count_by_type
db_latency_ms_by_type
entity_candidate_count
edge_candidate_count
graph_node_count_before
graph_edge_count_before
graph_node_count_after
graph_edge_count_after
```

目的不是证明 Neo4j 本身快慢，而是分析：

```text
T_bind ~ candidate_count
T_bind ~ graph_size
```

如果后期 Bind 比例持续增长，才能有证据支持未来研究 conflict-domain / selective Bind，而不是提前发明机制。

---

# 8. 网络与远程 API：主协议

这是本次优化的关键新增部分。

## 8.1 主结论：网络必须考虑，但不能简单去掉

当前部署本身就是远程 vLLM，因此：

```text
arrival_to_publish_ms
makespan_ms
```

**继续包含真实网络路径**。

原因：

1. 它是当前系统实际部署的一部分；
2. M0/M1/M2 都走相同路径；
3. agentic / LLM systems 顶会工作也常从远程 client 统计 E2E latency，或显式将网络建模为 workflow latency 的组成部分。

但必须增加网络控制，避免随机网络抖动形成假 speedup。

## 8.2 网络基线 artifact

在整个实验 campaign 开始前，对 construction endpoint 与 embedding endpoint 分别做网络基线：

```text
100 × lightweight HTTP health/model-list request
```

要求：

- 使用与实验相同 NIC、路由和代理设置；
- 不包含 LLM inference；
- 记录 monotonic client latency；
- 不在正式 measured run 期间持续发探针。

保存：

```text
artifacts/environment/network_baseline.json
```

字段：

```json
{
  "construction_endpoint": {
    "n": 100,
    "success": 100,
    "median_ms": 0,
    "p95_ms": 0,
    "p99_ms": 0,
    "mad_ms": 0
  },
  "embedding_endpoint": {},
  "route": "...",
  "proxy_env_present": false
}
```

### 重要

如果正式实验使用内网 endpoint，则性能 driver **不得通过 HTTP/HTTPS proxy** 访问该 endpoint。

启动 gate 必须读取进程环境并确认对应内网地址进入 `NO_PROXY/no_proxy` 或根本不使用代理。

## 8.3 每个 run 的 pre/post network gate

每个 measured run：

```text
pre-run: 20 health probes
post-run: 20 health probes
```

这些 probe 必须发生在正式计时区间之外。

保存：

```text
network_pre_median_ms
network_pre_p95_ms
network_post_median_ms
network_post_p95_ms
network_error_count
```

### 网络异常判定

不要用一个凭空固定的 5 ms 或 10 ms 阈值。

使用 campaign baseline：

```text
baseline_med = campaign median
baseline_mad = campaign MAD
```

若 pre/post 任意一个满足：

```text
HTTP probe success < 100%
OR
probe median > baseline_med + 5 * max(baseline_mad, 0.1 ms)
```

则标记：

```text
network_unstable = true
```

同时报告 P95 比例，不自动删除结果。

如果 baseline 本身高度不稳定，先停止实验，修复网络，不进入正式 run。

## 8.4 网络异常如何重跑

正式 performance 使用 **method block**：

```text
block = (question_id, repeat)
block contains M0, M1, M2
```

如果某个 method run 因明确 infrastructure/network failure 被判无效：

1. 保留该 run artifact，标记 `infra_failed=true`；
2. 不覆盖；
3. 使用新 block ID；
4. **整个 block 的 M0/M1/M2 都重新跑**，不能只补一个方法；
5. 旧 block 不进入 primary paired statistics，但必须在 failure appendix 中列出。

这样避免“只给表现差的方法挑一个好网络时段重跑”。

## 8.5 不允许做 RTT subtraction

禁止：

```text
corrected_llm_latency = observed_llm_latency - ping_rtt
```

原因：

- HTTP request 不是一个固定 RTT；
- server queue / serialization / payload transfer 与 RTT 纠缠；
- M2 的并发本身可能改变连接复用与发送时序；
- 简单减法会产生不可解释指标。

网络 probe 只用于：

> 判断环境是否稳定，以及估计网络相对于 LLM latency 的量级。

## 8.6 主报告必须同时给一个“网络量级判断”

计算：

```text
network_fraction_proxy =
    network_baseline_p95_ms / median_llm_client_observed_ms
```

仅作为 rough diagnostic。

如果：

```text
network_fraction_proxy < 1%
```

可合理说明当前 LAN jitter 相对 LLM request latency 极小。

如果明显更大，则必须在报告中保留网络作为重要限制，并优先获取 server-side telemetry。

---

# 9. vLLM server 状态与共享资源控制

## 9.1 远程 GPU 必须是实验独占资源

正式 run 前必须确认：

```text
vLLM running requests = 0
vLLM waiting requests = 0
无其他模型 workload
GPU 无异常 thermal/power throttling
```

若远端 GPU 与其他用户共享，则当前结果不能作为稳定系统性能结论，应标记 `shared_server=true`，正式 performance gate 暂停。

## 9.2 记录 server telemetry

建议 1 s 粒度保存：

```text
timestamp
GPU utilization
GPU memory used
power draw
SM clock
memory clock
temperature
vLLM running requests
vLLM waiting requests
vLLM KV cache usage（若 metrics 暴露）
```

保存：

```text
artifacts/telemetry/<run_id>.parquet
```

采样工具必须先做 overhead test；如果 telemetry 明显影响服务，则降为 2–5 s。

## 9.3 为什么 server queue 不能从主 latency 排除

M2 的目标就是通过并行 Compile 更好利用 construction server。

因此：

> M2 增加 vLLM queueing、batching 或 GPU utilization，是 treatment 的一部分，不是需要“校正掉”的外部噪声。

真正需要排除的是：

> 与方法无关的第三方请求、网络异常、server restart、thermal throttling。

---

# 10. Prefix cache / embedding cache：必须修复跨-run carry-over

原协议允许 vLLM 自身正常 prefix/KV cache，这本身合理；但**跨方法跨 run 残留 cache 会产生严重顺序偏差**。

例如：

```text
先跑 M0
→ vLLM 缓存大量 Graphiti common prefix / prompt prefix
→ 后跑 M2
→ M2 无意获得 warm prefix advantage
```

即使 run order 随机化，也只是把偏差随机化，不如直接控制。

## 10.1 Performance lane 的 cache 状态定义

采用：

> **hot engine + cold cross-run application/prefix state + natural within-run reuse**

即：

- 模型已加载；
- CUDA/runtime 已 warm；
- 每个 measured run 开始前清空 vLLM prefix cache；
- run 内允许正常 prefix reuse；
- run 之间禁止继承 prefix state。

vLLM 提供 `reset_prefix_cache` 用于 benchmarking；执行 Agent 必须先在 pinned vLLM `0.26.0` 上通过 endpoint contract 验证可用性和返回值，再写入正式 protocol。若当前部署未暴露该 endpoint，则使用等价且经过验证的 cache isolation 方式，不得假设 reset 成功。

## 10.2 Warm-up 顺序

不要再使用“每 run 一个真实 benchmark warm-up episode 然后直接开始计时”的模糊规则。

推荐顺序：

```text
A. 模型服务启动后执行 synthetic warm-up，完成 CUDA/kernel/runtime 预热
B. 等待 running/waiting request = 0
C. reset prefix cache
D. 验证 reset success
E. 清空 / 重建该 run 的逻辑数据库
F. reset per-run embedding cache
G. pre-run network gate
H. 创建正式 HTTP client connection pool，并做一次不计时 health preconnect
I. 开始 measured trace
```

**正式数据 prompt 不得用于 C 之后的 warm-up。**

## 10.3 Embedding cache

performance lane 使用：

> 每个 run 从空 embedding cache 开始；run 内只允许 exact-text cache。

这样：

- 不让后跑方法继承前一方法的 embedding；
- 保留一个真实系统在单次 workload 内的自然复用。

Correctness lane 的 deterministic embedding cache 继续按原协议。

## 10.4 Neo4j

定义：

> hot DB engine + cold logical graph。

不要求每 run 重启 Neo4j，也不要求清 Linux page cache，因为那会引入更大冷启动噪声并需要高权限操作。

必须：

- 删除前一 run 的逻辑数据；
- 验证 node/edge count = 0；
- 重建统一索引/约束；
- 使用相同 Neo4j connection pool 配置；
- DB pool 必须足够大，不得只把 M1/M2 卡在 client pool 上。

---

# 11. HTTP client 与连接公平性

M0/M1/M2 必须使用**同一 client implementation 和同一参数**：

```text
connect timeout
read timeout
write timeout
pool timeout
max connections
max keepalive connections
keepalive expiry
TCP_NODELAY（若可控）
HTTP version
```

建议：

- 一个 run 内复用一个 async HTTP client；
- 不为每个 LLM request 新建 TCP connection；
- M0 不人为关闭 keep-alive；
- M2 不获得专属更大的连接池；
- global HTTP max connections 至少覆盖冻结的 construction concurrency cap 8，但所有方法共享同一值。

`request timeout` 不能设置得接近正常 P95 latency，否则会形成方法相关 censoring。应根据 calibration 的真实长请求设置足够高的一致上限。

---

# 12. Retry / Error 分类

必须把失败分成三类。

## 12.1 Protocol-approved semantic retry

例如冻结的 structured-output bounded retry。

规则：

- 所有方法一致；
- 全部计 call/token/time；
- 不算 infra failure；
- 单独报告。

## 12.2 Infrastructure failure

典型：

```text
connection reset
DNS / route failure
client cannot reach endpoint
server process restart
unrelated host failure
```

规则：

- 不静默 retry measured request；
- 标记 run failed；
- 按 method block 重跑整组；
- 保留 artifact。

## 12.3 Treatment-induced system failure

典型：

```text
M1/M2 并发导致 vLLM overload / 429
OOM
DB transaction conflict
method-specific deadlock
queue explosion
```

这不是网络噪声。

规则：

- 记入该方法结果；
- 不按 infra failure 删除；
- 若超过原协议 failure threshold，判 INCONCLUSIVE/NO-GO。

---

# 13. Resource fairness：不要错误地“让每个方法瞬时用一样多资源”

公平性应定义为：

> **same resource envelope, not same instantaneous utilization**。

三种方法共享：

```text
same construction GPU
same model
same vLLM config
same max_num_seqs
same global LLM concurrency cap = 8
same embedding GPU
same Neo4j
same HTTP/DB pool
same network path
same decoding config
same prompt/schema
```

M0 因语义依赖只能暴露较少并行度，是 baseline 的真实行为，**不能为了“公平”强行给 M0 制造无意义的并行请求**。

同样，M2 能把 GPU 利用率提高，是其设计收益，不应归一化掉。

但必须记录：

```text
max observed in-flight LLM requests
mean / p95 vLLM queue depth
GPU utilization
```

证明没有突破统一资源 envelope。

---

# 14. Baseline tuning：M1 不能被故意设成弱 baseline

主实验可以继续保留：

```text
M1 WholeUpdate-Parallel-C8
M2 MemBind-GO-C8
```

用于 iso-resource 比较。

但在 calibration/characterization 中必须增加：

```text
C ∈ {1, 2, 4, 8}
```

至少对一个固定 calibration instance 运行 M1 和 M2。

输出：

```text
best_m1_concurrency_on_calibration
best_m2_concurrency_on_calibration
```

### 报告规则

如果 M1-C4 明显快于 M1-C8：

- formal primary 仍可保留冻结的 C8 iso-resource comparison；
- 但论文/报告必须额外报告 **Best-Tuned M1**；
- 不能只拿更差的 M1-C8 来证明 Late Binding 必要性。

该原则来自系统论文常见的 strong-baseline practice，也与 ContextPilot 明确对 baseline 做最佳性能/精度调参的做法一致。

---

# 15. Open-loop workload 优化

## 15.1 Primary workload 不改

仍使用：

```text
DELTA_MS = round100ms(median M0 native service time on calibration)
```

且 formal evaluation 使用 deterministic arrivals：

```text
t_i = i * DELTA_MS
```

优点：

- 完全配对；
- 低 workload randomness；
- 容易复现；
- 能直接观察 backlog/freshness amplification。

## 15.2 Calibration 同时保存 service-time distribution

除了 median，增加：

```text
mean
p25
p50
p75
p90
p95
std
CV = std / mean
SCV = variance / mean^2
```

原因：Graphiti episode service time 已观察到明显长尾；同样的平均 load，在高 SCV 下 tail queueing 会大幅变化。

## 15.3 Phase 4.5 小型 load sensitivity

只选 **1 个固定 calibration instance**，冻结后不得更换。

定义：

```text
rho_proxy = median_M0_service / DELTA
```

测试：

```text
rho ≈ 0.5   → DELTA = 2.0 * median_service
rho ≈ 1.0   → DELTA = 1.0 * median_service
rho ≈ 1.5   → DELTA = 0.67 * median_service
```

只要求：

```text
M0 vs M2-C8
```

M1 可选，不作为 gate 必需。

## 15.4 Stochastic arrival sensitivity

因为 LongMemEval 没有真实 arrival timestamp，参考 DistServe/Llumnix，再加一个极小验证：

```text
Poisson arrival, mean inter-arrival = DELTA_MS
fixed RNG seed
same exact arrival trace replayed to M0 and M2
```

可选增加：

```text
Gamma arrival with CV=2
```

但只有在主 Pilot 结果值得继续时再做；不要让基础验证发散成大规模 queueing study。

---

# 16. Run order：用 blocked randomization 替换单纯 global shuffle

原协议：

```python
random.shuffle(all_runs)
```

虽然能够防止固定顺序，但长达数十分钟甚至数小时的 run 会受到：

- server thermal drift；
- 网络时段变化；
- 远程主机 background jitter；
- 长周期资源漂移。

因此建议**替换原 performance lane global shuffle 条款**为：

```text
block = (question_id, repeat)
```

每个 block 内包含：

```text
M0, M1, M2
```

用固定 seed 随机排列三者顺序，并在所有 blocks 上尽量平衡：

```text
M0-M1-M2
M1-M2-M0
M2-M0-M1
M0-M2-M1
M2-M1-M0
M1-M0-M2
```

循环使用并由 seed 决定起点。

### 优点

同一个 instance 的三个方法尽量处于相近 wall-clock 时间段，减小环境漂移造成的 paired comparison 偏差。

### 仍然必须

每个 run 之间 reset prefix cache、embedding cache、logical graph，因此 block order 不形成 cache carry-over。

---

# 17. Repetition 与噪声处理

当前 formal performance 每 `(instance, method)` 重复 2 次，可以保留以控制成本，但增加预注册稳定性 gate。

## 17.1 不允许按效果方向选择性增加 repeat

在 2 repeats 完成后，计算每个 `(instance, method)`：

```text
relative_repeat_gap = |x1 - x2| / mean(x1, x2)
```

对 primary P95 和 makespan 都检查。

若满足：

```text
超过 25% 的 (instance, method)
其 relative_repeat_gap > 10%
```

则：

> 当前性能环境噪声过大，primary performance 标记 `stability_inconclusive=true`。

这时若预算允许，增加第三 repeat 时必须：

- 对全部 8 instances；
- 对全部 M0/M1/M2；
- 使用新冻结的完整 run blocks；
- 不能只补“看起来异常”的方法。

此规则必须在看正式结果前写入代码/配置。

## 17.2 统计单元继续是 instance

禁止把 46 个 episode 当作 46 个独立样本做 CI/p-value，因为它们共享同一 graph state 和 queue。

继续：

```text
resampling unit = question_id
```

---

# 18. Tail metric 的严谨性

每个 LongMemEval instance 只有约几十个 episodes，因此：

```text
P99 within one instance
```

实际上非常接近 max，方差很大。

因此：

- P95 保持 primary；
- P50 保持 secondary；
- P99 只做 descriptive trace metric；
- **禁止把 P99 的单 instance 波动解释成稳定 tail claim。**

如果后续扩展到 MemoryArena/更长 online stream，再把 P99 升格为正式 tail metric。

---

# 19. Instrumentation 自身必须做 overhead gate

埋点不能显著改变被测系统。

## 19.1 实现原则

- `time.monotonic_ns()`；
- span 先写内存 buffer；
- 不在每个 span `fsync()`；
- run 结束后批量 flush；
- telemetry 单独低频采样；
- 不在 critical path 做复杂 JSON pretty-print。

## 19.2 Overhead test

使用 deterministic response replay / fake model，使远程模型噪声不主导：

```text
M0 instrumentation OFF × 5
M0 instrumentation ON  × 5
```

同一小 trace，交替运行。

计算：

```text
instrumentation_overhead =
    median(on) / median(off) - 1
```

Gate：

```text
<= 2%：PASS
2%–5%：WARNING，说明并尽量优化
> 5%：FAIL，不进入 formal performance
```

这是 instrumentation gate，不是 MemBind Go/No-Go。

---

# 20. Deterministic candidate normalization 的 baseline guardrail

当前为保证 exact replay，M0/M1/M2 统一使用：

```text
logical_content_ascending_after_top_k
```

它不改变 top-K candidate membership，但改变 candidate presentation order，因此严格说它不是完全 untouched upstream Graphiti。

正式实验前增加一次：

```text
Upstream-Native-Serial
vs
Deterministic-Native-Serial
```

在 4 个 calibration instances 上比较：

```text
canonical graph parity
entity/edge F1
Evidence Recall@10
LLM call count
token count
makespan overhead
```

### 判定

如果 canonical exact parity 无法 4/4：

- 不得继续把 M0 称为“完全原生 Graphiti”；
- 报告名称改为 `Deterministic-Native-Serial`；
- upstream M0 作为 semantic guardrail baseline 保留。

如果 retrieval 基本一致但 canonical graph 有小差异，也必须如实说明 candidate order 对 LLM resolution 的影响。

不得因为该差异不利于 MemBind 而删除此 guardrail。

---

# 21. Performance lane 的 live semantic drift 诊断

Correctness lane 已通过 response replay 隔离模型随机性。

Performance lane 使用 live model 时，即使 `temperature=0`，并行 batching / floating-point 非确定性仍可能造成少量 response divergence。

因此每个相同 prompt hash 跨方法记录：

```text
live_response_hash
finish_reason
input_tokens
output_tokens
```

派生：

```text
live_response_divergence_rate
```

### 规则

- 不把它作为主 correctness 结论；correctness 仍以 replay lane 为准；
- 如果 live divergence 很高并导致不同方法 LLM call/token work volume 明显不同，则 performance 结果必须标记 confounded；
- 不允许用这种随机 divergence 解释为 MemBind 的语义错误。

---

# 22. Characterization 执行计划：Phase 4.5

仅在当前 smoke correctness gate 通过后执行。

## 22.1 Phase 4.5-A：Instrumentation TDD

新增测试：

```text
test_span_nesting.py
test_phase_interval_union.py
test_llm_request_trace_schema.py
test_network_gate.py
test_cache_reset_contract.py
test_run_block_randomization.py
test_instrumentation_overhead.py
test_frontier_stall_accounting.py
test_pipeline_sampling.py
```

必须先红后绿。

## 22.2 Phase 4.5-B：Native characterization + arrival calibration

直接复用原本就要执行的 4 个 M0 calibration instances。

每个 instance：

1. 运行完整 M0；
2. 同时采集 phase span、LLM call、DB、network、GPU telemetry；
3. 生成 service-time distribution；
4. 计算统一 `DELTA_MS`；
5. 冻结 `phase_map.json`；
6. 不根据任何 M2 结果修改 phase classification。

输出：

```text
artifacts/characterization/native_phase_spans.parquet
artifacts/characterization/native_llm_requests.parquet
artifacts/characterization/native_db_operations.parquet
artifacts/characterization/native_phase_summary.json
artifacts/calibration/arrival_interval.json
```

## 22.3 Phase 4.5-C：Concurrency sensitivity

固定 1 个 calibration instance：

```text
M1: C1 C2 C4 C8
M2: C1 C2 C4 C8
```

不进入 formal primary statistics。

输出：

```text
concurrency_sensitivity.parquet
```

至少分析：

```text
makespan
P95 freshness
GPU utilization
vLLM queue depth
frontier stall
canonical/retrieval guardrail（M1/M2 能算则保存）
```

## 22.4 Phase 4.5-D：Load sensitivity

同一个固定 calibration instance：

```text
M0 vs M2-C8
rho ≈ 0.5 / 1.0 / 1.5
```

再加：

```text
Poisson(mean = DELTA_MS), fixed seed
```

如果时间/预算不足，Poisson 优先于 Gamma；Gamma burstiness 留到 GO 后。

## 22.5 Phase 4.5-E：Freeze

Characterization 完成后生成：

```text
artifacts/characterization/CHARACTERIZATION_REPORT.md
artifacts/characterization/freeze.json
```

`freeze.json` 必须记录：

```text
phase map hash
instrumentation code hash
network baseline hash
cache policy
HTTP pool policy
DB pool policy
formal DELTA_MS
formal method configs
formal run blocks
```

之后不再改 instrumentation 语义。

---

# 23. 正式 Performance Lane 的 run lifecycle

每一个 measured run 严格按下列状态机执行：

```text
PRECHECK
  ↓
SERVER_IDLE_GATE
  ↓
CACHE_RESET
  ↓
DB_RESET
  ↓
NETWORK_PRE_GATE
  ↓
TELEMETRY_START
  ↓
MEASURED_OPEN_LOOP_RUN
  ↓
DRAIN_TO_ZERO
  ↓
TELEMETRY_STOP
  ↓
NETWORK_POST_GATE
  ↓
DB_CANONICAL_EXPORT
  ↓
ARTIFACT_FLUSH
  ↓
RUN_FINALIZE
```

## PRECHECK

确认：

```text
Graphiti commit
vLLM version/model/context
embedding model/dim
Neo4j version
config hash
method hash
no proxy for LAN endpoint
```

## SERVER_IDLE_GATE

确认：

```text
no running model requests
no waiting model requests
no prior experiment process
```

## CACHE_RESET

```text
vLLM prefix cache reset success
per-run embedding cache empty
application response cache disabled
```

## DB_RESET

```text
node_count == 0
edge_count == 0
constraints/indexes ready
```

## DRAIN_TO_ZERO

final publish 后仍等待：

```text
all client requests completed
vLLM running=0
vLLM waiting=0
```

再结束 telemetry，防止尾部请求漏计。

---

# 24. 新 artifact 目录

在现有 artifact 上增加：

```text
artifacts/
├── environment/
│   ├── network_baseline.json
│   ├── server_capability.json
│   └── cache_reset_contract.json
│
├── characterization/
│   ├── phase_map.json
│   ├── native_phase_spans.parquet
│   ├── native_llm_requests.parquet
│   ├── native_db_operations.parquet
│   ├── native_phase_summary.json
│   ├── concurrency_sensitivity.parquet
│   ├── load_sensitivity.parquet
│   ├── instrumentation_overhead.json
│   ├── upstream_normalization_guardrail.json
│   ├── CHARACTERIZATION_REPORT.md
│   └── freeze.json
│
├── telemetry/
│   └── <run_id>.parquet
│
├── network/
│   └── <run_id>.json
│
└── final/
    ├── run_manifest.parquet
    ├── episode_metrics.parquet
    ├── instance_metrics.parquet
    ├── mechanism_metrics.parquet
    └── ...
```

---

# 25. 推荐最终图表

正式 Pilot 至少生成以下图。

## Figure 1：Native Graphiti critical-path breakdown

按 4 calibration instances 汇总：

```text
source/context prep
node extraction
edge extraction
node resolution
edge resolution/invalidation
DB publication
other
```

同时标颜色/标签：

```text
Compile-eligible
State-dependent Bind
Commit
```

目的：直接验证研究 motivation。

## Figure 2：Representative M0 vs M2 timeline

用同一 instance 的前若干 episodes：

```text
M0: serialized full add_episode
M2: overlapping Compile + ordered Bind
```

标出 arrival、Compile ready、Bind、publish。

目的：一眼解释 runtime mechanism。

## Figure 3：Freshness distribution

```text
M0 / M1 / M2
P50/P95 或 CDF
```

主结果。

## Figure 4：Speedup vs offered load

仅 characterization：

```text
rho ≈ 0.5 / 1.0 / 1.5
```

目的：说明适用区间。

## Figure 5：Pipeline bottleneck

推荐二选一：

```text
Bind fraction vs episode index / graph size
```

或：

```text
ready queue depth + bind utilization over time
```

目的：说明瓶颈是否从 Compile 转移到 Bind。

---

# 26. 分析时必须回答的机制问题

`CHARACTERIZATION_REPORT.md` 必须按以下顺序回答。

## Q1. Graphiti 原生瓶颈是什么？

必须给：

```text
F_compile
F_bind
F_commit
LLM prompt-type time/token breakdown
```

禁止只写“LLM 很慢”。

## Q2. MemBind 实际隐藏了多少 Compile？

必须给：

```text
compile_hiding_ratio
compile exposed time
```

## Q3. MemBind 的新瓶颈是什么？

必须给：

```text
bind_utilization
ready queue depth
frontier_stall_ratio
vLLM queue depth
```

## Q4. 收益来自单请求变快还是 queueing amplification 被抑制？

必须比较：

```text
service_ms
queue_wait_ms
arrival_to_publish_ms
```

## Q5. 收益是否只存在于 overload？

必须使用 load sensitivity 回答。

## Q6. 网络是否可能解释结果？

必须报告：

```text
network baseline
pre/post run stability
network_fraction_proxy
transport errors
```

若网络量级远小于 LLM latency，明确说“network jitter is unlikely to explain the observed effect”；若不是，则不能弱化该限制。

---

# 27. Formal statistics：保持主协议，但增加这些规则

## 27.1 Primary comparison

```text
M2 vs M0
```

instance-level paired。

报告：

```text
geometric mean speedup
median speedup
95% cluster bootstrap CI
raw per-instance ratios
```

## 27.2 Secondary

```text
M1-C8 vs M0
M2-C8 vs M1-C8
M2-C8 vs Best-Tuned-M1 calibration result（仅辅助解释）
```

注意：Best-Tuned-M1 如果没有在全部 formal instances 重跑，就不能伪装成 formal paired primary baseline；它只能用于说明 C8 是否是合理强设置。若后续准备投稿，应再决定是否正式加入 tuned M1 evaluation。

## 27.3 Mechanism statistics

characterization 以 effect size / distribution 为主，不做大量 hypothesis tests。

例如：

```text
median F_compile
IQR F_compile
Spearman(T_bind, candidate_count)
Spearman(T_bind, graph_size)
```

相关性只做解释性分析，不据此宣称因果。

---

# 28. 公平性审查表（每次冻结前逐项 PASS）

| 类别 | 必须满足 |
|---|---|
| Model | M0/M1/M2 同一 checkpoint/revision |
| Serving | 同一 vLLM 版本、参数、GPU |
| Decode | 相同 temperature/top_p/max_tokens/seed/schema |
| Prompt | 相同 Graphiti prompt；deterministic normalization 三方法一致 |
| LLM concurrency | 同一全局 cap；不为 M2 特供更多资源 |
| Embedding | 同模型、同 endpoint、同维度、同 per-run cache policy |
| DB | 同 Neo4j、同 indexes、每 run 空逻辑图 |
| HTTP | 同 client、pool、timeout、keep-alive |
| Network | 同路由；LAN 不经过 proxy；pre/post gate |
| Prefix cache | 每 run reset，run 内自然使用 |
| Warm state | hot engine；cold cross-run cache state |
| Arrival | 相同 frozen timestamps |
| Run order | blocked randomized / balanced |
| Retry | 同一 frozen policy；不静默 retry |
| Failure | infra vs treatment-induced 明确区分 |
| Telemetry | 同一采样率；先通过 overhead gate |
| Statistics | instance-level paired；不把 episodes 当独立样本 |
| Dataset | frozen split，不根据结果挑样本 |

任何一项 FAIL：

> 不得将该 run 纳入 primary performance statistics。

---

# 29. 修改现有 Pilot Protocol 的最小 patch 清单

执行 Agent 不要重写整个工程，只做以下最小修改。

## 必须修改

### A. `tracing.py`

增加：

- hierarchical span；
- phase classification；
- interval union；
- M2 pipeline state samples。

### B. Graphiti instrumentation adapter

只 wrapper pinned upstream functions，不改变返回值和控制流。

### C. LLM client wrapper

增加 request-level timing / request id / prompt type；保持原 Graphiti请求内容不变。

### D. `replay_driver.py`

增加：

- run lifecycle；
- pre/post network gate；
- cache reset；
- block schedule；
- telemetry start/stop。

### E. `statistics.py`

增加：

- phase fraction；
- compile hiding；
- frontier stall；
- utilization；
- repeat stability gate。

### F. configs

新增明确字段：

```yaml
measurement:
  network_gate: true
  network_probe_count: 20
  telemetry_interval_s: 1.0
  block_randomization_seed: 20260806
  reset_prefix_cache_between_runs: true
  reset_embedding_cache_between_runs: true
  instrumentation_overhead_limit: 0.02

characterization:
  enabled: true
  concurrency_levels: [1, 2, 4, 8]
  load_rho: [0.5, 1.0, 1.5]
  poisson_sensitivity: true
```

## 不得修改

- LongMemEval frozen split；
- Graphiti commit；
- model revision；
- Evidence Fence；
- correctness cache semantics；
- source-ordered Bind/Commit；
- primary Go/No-Go threshold；
- M0/M1/M2 core semantics。

---

# 30. TDD 执行顺序

严格执行：

```text
1. 当前 smoke attempt 自然结束
2. smoke correctness gate PASS
3. 新增 instrumentation contract tests（先红）
4. 实现 span / network / cache lifecycle（转绿）
5. 全量 unit regression
6. instrumentation-overhead gate
7. cache-reset live contract
8. network baseline gate
9. Upstream vs deterministic-normalized M0 guardrail
10. 4 calibration M0 + Native Characterization
11. freeze DELTA_MS + phase_map
12. small C sensitivity
13. small load/Poisson sensitivity
14. freeze characterization
15. 生成 blocked formal run plan
16. correctness lane
17. performance lane
18. paired statistics
19. GO / INCONCLUSIVE / NO-GO
```

只要 3–14 任意 gate 失败：

> 停止，不进入 formal 64-run。

---

# 31. 最终 GO / NO-GO 之外增加 Mechanism Verdict

原正式 GO / INCONCLUSIVE / NO-GO 保持不变。

额外输出一个不影响原门槛的：

```text
mechanism_verdict
```

允许：

### `MECHANISM_SUPPORTED`

满足现象：

```text
F_compile 明显占原生关键路径
M2 能隐藏大部分 Compile
queue/freshness 明显改善
Bind/Commit 成为合理 serial suffix
```

### `MECHANISM_PARTIAL`

例如：

```text
M2 有加速
但 Compile 比例有限
或主要收益只在高 load
或 frontier stall 明显
```

### `MECHANISM_NOT_SUPPORTED`

例如：

```text
原生 bottleneck 主要在 Bind/DB
Compile 可并行比例很低
M2 实际没有隐藏有效 critical-path work
```

这样可以防止“跑出了一个 speedup 数字，就自动解释成原始 hypothesis 被证明”。

---

# 32. 对网络问题的最终明确裁决

执行 Agent 必须遵循以下一句话：

> **当前 MemBind 性能主指标必须包含远程 API 的真实网络延迟，因为这属于冻结部署路径；但网络必须通过同路径、无代理、cache isolation、blocked randomization、pre/post stability gate 和 server/client telemetry 被控制与量化。不能忽略网络，也不能简单从 E2E latency 中减去 RTT。**

特别注意：

- 如果 M2 因并发让 server batching 更好，这是方法收益；
- 如果 M2 因并发让 server queue 更长，这是方法代价；
- 如果某天 LAN 突然抖动，这是环境噪声；
- 如果 background user 抢 GPU，这是实验污染；
- 如果 M1 自身造成 DB/LLM overload，这是 baseline 的真实行为，而非网络故障。

---

# 33. 本 Pilot 做到什么程度就够了

不要一次性把研究扩成完整生产 benchmark。

**基础验证必须做到：**

1. smoke correctness 通过；
2. M0 phase-level bottleneck 画像完成；
3. 网络/cache/server fairness gate 完成；
4. 4 calibration instances 完成；
5. 1 个 instance 的 C sensitivity；
6. 1 个 instance 的 load + Poisson sensitivity；
7. formal 8-instance correctness/performance 完成；
8. 能明确解释 speedup 来自哪里。

**基础验证暂时不要求：**

- 第二 memory backend；
- conflict-aware Bind；
- fault-tolerant recovery；
- 大规模多租户；
- 完整 Poisson/Gamma × concurrency 二维 sweep；
- 跨 WAN/公网实验；
- 几百/几千 episode 的 P99 serving study；
- answer judge 主指标。

只有当前 Pilot `GO + MECHANISM_SUPPORTED/PARTIAL` 后再扩展。

---

# 34. 最终报告模板

最终 `VALIDATION_REPORT.md` 建议升级为六个问题：

## 1. Is the split semantically valid?

- M0 ↔ M2 replay parity；
- unexpected prompt；
- exactly-once；
- retrieval guardrail。

## 2. Where does native Graphiti spend its time?

- native phase breakdown；
- prompt-type LLM breakdown；
- graph/candidate growth。

## 3. Does MemBind hide that bottleneck?

- compile hiding；
- Bind utilization；
- frontier stall；
- queue amplification。

## 4. Does MemBind improve end-to-end freshness?

- P95 arrival-to-publish；
- makespan；
- drain；
- paired CI。

## 5. Is the improvement robust and fair?

- same resource envelope；
- network stability；
- prefix-cache isolation；
- repeat stability；
- concurrency/load sensitivity；
- strong M1 setting。

## 6. Is the idea worth continuing?

同时给：

```text
formal_verdict = GO / INCONCLUSIVE / NO-GO
mechanism_verdict = SUPPORTED / PARTIAL / NOT_SUPPORTED
```

不得使用主观描述替代这两组证据。

---

# 35. 参考系统论文

本文件的方法学原则主要参考以下系统工作：

1. **In Gim, Zhiyao Ma, Seung-seob Lee, Lin Zhong. _Pie: A Programmable Serving System for Emerging LLM Applications_. SOSP 2025. DOI: 10.1145/3731569.3764814.**
   - 远程 Python client 测 E2E latency；
   - 同 GPU backend 控制系统比较；
   - 相同 high-level application logic。

2. **Chaofan Lin et al. _Parrot: Efficient Serving of LLM-based Applications with Semantic Variable_. OSDI 2024.**
   - 把 Internet/network 与 queueing 纳入应用 E2E；
   - 使用基于实际测量的随机网络 delay；
   - 给出 E2E latency breakdown。

3. **Yinmin Zhong et al. _DistServe: Disaggregating Prefill and Decoding for Goodput-optimized Large Language Model Serving_. OSDI 2024.**
   - 明确 testbed 与网络带宽；
   - 数据无 timestamps 时使用 Poisson arrivals；
   - 扫 request rates；
   - 分析 communication / latency breakdown。

4. **Biao Sun et al. _Llumnix: Dynamic Scheduling for Large Language Model Serving_. OSDI 2024.**
   - baseline 共用 vLLM engine；
   - Poisson + Gamma/CV burstiness；
   - request-rate sensitivity；
   - 重视 P99 tail 与 queueing。

5. **Yinsicheng Jiang et al. _ContextPilot: Fast Long-Context Inference via Context Reuse_. MLSys 2026.**
   - baseline 调优以匹配各自最佳表现；
   - online memory workload 使用 cold-start incremental state；
   - 性能与 quality 同时评估。

6. **Woosuk Kwon et al. _Efficient Memory Management for Large Language Model Serving with PagedAttention_. SOSP 2023. DOI: 10.1145/3600006.3613165.**
   - 以相近 latency 条件下的可持续 throughput/request rate 衡量 serving system，而不是只看孤立 micro-latency。

---

# 36. 一句话执行摘要

> **不要继续堆新的 MemBind mechanism。先在 smoke correctness 通过后，把 M0 变成可解释的 phase-traced baseline；把远程网络、vLLM queue、prefix-cache carry-over、run-order drift 和 instrumentation overhead 变成受控变量；用 calibration 做极小的 concurrency/load sensitivity；然后冻结一切进入正式 paired evaluation。最终既要证明“更快”，也要证明“为什么快，而且不是网络、cache 或 baseline 设置造成的假象”。**
