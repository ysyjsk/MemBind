# MemBind V6.1 8B 双副本公平实验设计

> **基线语义纠偏（2026-08-28，取代本文早期草案）**：此前将
> `NATIVE_PARALLEL/B1` 写作 Native headline 是错误的。正式主比较现在固定为
> `B0/NATIVE_SERIAL`（`native-serial-dual`）：在同样双 GPU、模型、Embedding、
> workload、cache protocol 下，严格按 episode/source 顺序完成完整 stateful update
> 与 durable publication。V6.1 只能提前执行已证明无依赖的 PREPARE/replay 子操作，
> 且必须保持 B0 的 state evolution 与 publication order。`B1/NATIVE_PARALLEL`
> 允许完整 episode 并发、可能改变状态演化，只保留为
> `RELAXED_ORDER_B1_UPPER_BOUND` 性能上界；它不再参与 headline speedup，也不能据此
> 断言 dependency-aware concurrency 无效。历史 JSON/artifact 不改写，以下结果按此
> taxonomy 重新归类。

> 日期：2026-08-27  
> 新平台身份：`local-qwen3-8b-awq-dualreplica-v1`  
> 状态（2026-08-28）：8B 双副本平台已启动并通过 live preflight；双 endpoint
> runner 与 prefix-2 四臂诊断已完成。现有结果因工作量漂移、图语义错误、缓存顺序
> 和单次小样本仅用于工程诊断，正式实验执行以
> [`MemBind_V6_1_8B_Autoresearch_Workplan.md`](./MemBind_V6_1_8B_Autoresearch_Workplan.md)
> 为准。

## 1. 从最新结果出发

当前最有意义的结论来自同一 `local-qwen3-14b-awq-v1`、同一 history-0
prefix、同一 workload contract，而不是来自理论估计。

| 方法 | 范围 | makespan | 相对结果 | 证据状态 |
| --- | ---: | ---: | ---: | --- |
| Native `76130682ee53` | 30 episodes | 3202.687s | reference | sealed |
| V6.0 `57cbe4c487da` | 30 episodes | 6103.876s | 1.906x slower than Native | sealed；legacy provider proof 有已知缺口 |
| Native 同一运行的 prefix-16 | 16 episodes | 1456.351s | reference | 从 durable publication trace 截取 |
| V6.1 staged `9246ef932a04` | 16 episodes | 1287.939s | 1.131x speedup；wall time -11.56% | request/replay/provider/shared-arbiter 全 PASS |

V6.1 staged 的 1287.939s 可进一步分解为：

```text
PREPARE stage                  794.650s
global barrier
authoritative NATIVE stage    493.289s
total                        1287.939s
```

这次实验完成了 294 个 external logical LLM call、88 个 dialogue-partition
transport expansion、382 个真实 transport、0 retry、1207 个 embedding item、
80 次 DB write；32/32 certified extraction request exact match，32/32 replay
成功。也就是说，11.56% 不是少做工作或写入语义变化产生的。

但是它暴露了两个系统问题：

1. PREPARE 和 NATIVE 在单 GPU 上受全局 barrier 串行，正确性收益没有充分转化为端到端收益。
2. 首条 durable publication 从 Native 的约 29.8s 退化到 795.055s。只报告最终 makespan 会掩盖在线系统体验的明显回归。

因此当前 V6.1 还不能直接扩展 full5。下一步需要验证的核心命题是：在保持
exact transcript handoff、唯一权威写路径和工作量守恒的前提下，双副本能否把
`prepare(i+1)` 与 `native(i)` 流水重叠，同时恢复 time-to-first-publication。

## 2. 8B 与双卡分别解决什么

Qwen3-8B-AWQ checkpoint 已完整下载，catalog SHA-256 为
`9426c790db40e413df2ce871c01d29f773dfffe82cb581c652ecb78f1e975d3a`。

换 8B 主要降低权重和 GEMM 计算，不会让 KV capacity 按参数量同比增长：

```text
Qwen3-14B KV bytes/token = 163,840
Qwen3-8B  KV bytes/token = 147,456
reduction                 = 10%
```

因此 8B 不是对并发问题的单独答案。它的真正价值是让一张 24GB 卡在保留
65,536 context 的同时容纳一个副本，并让第二张卡有机会与 Embedding
共置。双卡用于两个 replica，而不是 TP=2：

- 8B 单卡可放下，TP=2 没有容量必要性；
- 两张 3090 Ti 之间是 `PHB`，没有 NVLink，TP 会在每层引入 PCIe/host bridge collective；
- TP=2 仍把 PREPARE 与 NATIVE 放在同一个 engine，不提供 phase isolation；
- 两副本允许传递 exact extraction transcript，不需要传输 model KV。

模型变小可能带来质量变化，所以 8B 必须拥有独立主表。任何 8B speedup 都只能
除以 fresh Native8B；14B Native/V6.0/V6.1 结果只作为诊断背景，不进入 8B
方法增益计算。

## 3. 公平主比较

### 3.1 Headline B0 dual-replica comparison

两个方法都获得完全相同的资源集合：

```text
endpoint A: qwen3-8b-awq, GPU 0, 127.0.0.1:18200
endpoint B: qwen3-8b-awq, GPU 1, 127.0.0.1:18201
embedding:  qwen3-embedding-0.6b, GPU 1, 127.0.0.1:18202
```

`B0/NATIVE_SERIAL` 使用 `capacity_weighted_least_outstanding`：不读取 phase label，
根据 platform manifest 中两个副本实测 KV capacity 做容量感知；但 episode 的
stateful update 和 durable publication 始终按 source 顺序完成，路由不能改变状态演化。

V6.1-Phase-Affinity 使用同样的 endpoint set，但允许使用 MemBind DAG 中本来就
存在的 phase 信息：PREPARE 固定到 GPU 1，NATIVE 固定到 GPU 0，handoff 只传
exact extraction transcript。这个路由差异就是方法变量。

如果 Native 只给一张卡、V6.1 给两张卡，审稿人可以合理地把收益全部归因于资源
翻倍；因此这种配置禁止作为 headline。反过来，要求 Native 也使用 V6.1 的 phase
label 会把待评估的方法机制泄漏给 baseline，也不是合理对照。

`B1/NATIVE_PARALLEL` 仅作为 relaxed-order performance ceiling：它可并发完整 episode，
其 publication 顺序和中间状态可能不同，因此不能替代 B0 headline，也不能用来否定
dependency-aware concurrency。

### 3.2 必须保留的消融

| Arm | 资源 | 目的 |
| --- | --- | --- |
| Native8B-single | GPU 0 单 endpoint | 单卡算法基线 |
| V6.1-single | 同一 GPU 0 endpoint | 隔离 replay/staging/scheduler 的算法收益与开销 |
| B0 Native8B-dual-serial | 两个 8B endpoint；严格 source-order stateful/publication | resource-matched headline baseline |
| B1 Native8B-dual-parallel | 两个 8B endpoint；完整 episode 并发，可改变 state evolution | relaxed-order performance ceiling（仅辅助） |
| V6.1-dual-affinity | 同两个 endpoint | headline candidate |
| Native8B-dual-static-role | 可选，两 endpoint 静态角色但无 replay | 区分简单分流与 MemBind semantic handoff |
| TP2 | 可选，且 Native/V6.1 都需重跑 | 硬件策略消融，不进入主配置 |

`Native8B-dual-static-role` 是很重要的强基线：如果它只凭长/短请求静态分流就达到
V6.1 的效果，那么论文贡献应收缩为 phase placement；如果 V6.1 仍明显更好，才说明
exact certified reuse、authoritative replay 和应用 DAG 共同产生了额外价值。

## 4. 固定变量与唯一可变变量

### 4.0 MemBind-Core boundary

The primary method is `MemBind-Core`. It preserves Native's computation semantics and
required work: only certified dependency-free PREPARE/execution may move earlier; exact
replay substitutes an already certified transcript; authoritative state updates and durable
publication remain in B0 source order. Summary bypass, predicate pushdown, grounded or
deterministic materialization, and any other work reduction/replacement are
`WORK_REDUCTION_EXTENSION` variants. They require separate contracts, ablations, and
attribution and must never be combined with the Core headline speedup.

Headline pair 必须相同：

- 两个 Qwen3-8B checkpoint、revision、config/tokenizer hash；
- endpoint set、GPU 型号、GPU UUID、功耗/频率策略；
- vLLM/CUDA/driver/PyTorch 版本；
- 64K YaRN、8 sequence、8K batch、FCFS、xgrammar、prefix cache、chunked prefill；
- thinking off、temperature 0、top-p 1、seed 20260806、32K completion budget；
- prompt、Graphiti 版本、Embedding 模型/维度、Neo4j 版本；
- workload manifest、history/prefix、DB 初始状态、index schema；
- client timeout、SDK retry、dialogue partition policy；
- 同一时间窗口内的其他 GPU/CPU/background workload policy。

允许不同：

- Native 与 V6.1 的方法代码；
- phase label 是否对 router 可见；
- capacity-aware phase-blind routing 与 semantic phase-affinity routing；
- fresh namespace、run id、运行顺序；
- 由方法自然产生的 queueing、overlap 和 durable publication timing。

## 5. 新平台部署合同

独立脚本位于 `scripts/local_runtime_8b_dual/`，新 profile 的 logs、run state、
profiles 和 experiments 全部使用 `local-qwen3-8b-awq-dualreplica-v1` 路径。
它不覆盖 `scripts/local_runtime/` 和 `local-qwen3-14b-awq-v1`。

初始 GPU reservation：

| Process | GPU | `gpu_memory_utilization` | Live gate |
| --- | ---: | ---: | --- |
| native 8B | 0 | 0.90 | observed KV >= 65,536 |
| prepare 8B | 1 | 0.70 | observed KV >= 65,536 |
| embedding | 1 | 0.25 | observed KV >= 32,768 |

GPU 1 合计 0.95，保留 5% 给 driver/runtime margin。0.70/0.25 目前只是由 14B 与
Embedding 日志推导出的启动候选，不是 autoresearch 参数，也不允许对某个方法单独
修改。首次 live characterization 只回答“能否满足冻结 capacity contract”。如果
失败，重新设计整个 profile 并让所有方法一起重跑。

平台只有在以下条件全部通过后写 `READY`：

1. 两个 `/v1/models` 精确返回 `qwen3-8b-awq`；
2. checkpoint catalog、config、tokenizer hash 与冻结值一致；
3. native PID 的 `CUDA_VISIBLE_DEVICES=0`，prepare/embedding 为 1；
4. 两个 LLM 都通过 thinking-off structured JSON probe；
5. Embedding batch 返回 1024 维；
6. 最新启动日志中的三项 KV capacity 达到合同；
7. Neo4j read-only canary 通过；
8. GPU1 reservation 不超过 0.95；
9. Native/V6.1 routing JSON 的 endpoint set 完全相同；
10. 生成不含 API key/password 的 immutable platform manifest 及 SHA-256。

当前 startup preflight 会因为 14B 的 `18100/18101` 和两个 vLLM GPU 进程而失败。
这是预期保护：8B 脚本不会自动停止现有服务，也不会在显存不足时抢占它们。

## 6. Runner 与数据状态的硬 gate

平台 endpoint 可用不等于实验已经公平。当前 14B `membind_v6_1/runtime.py` 仍只有
一个 `CONSTRUCTION_LLM_BASE_URL`，并把 profile identity 固定为 14B；所以在双
endpoint client/router 真正实现并通过测试前，8B 只能做 platform characterization，
不能产生正式结果。

每个计时 attempt 必须先生成 run contract，锁定：

- platform manifest path/hash；
- workload manifest path/hash；
- router/runtime implementation path/hash；
- arm、method、comparison class、endpoint set；
- decoding contract；
- fresh namespace 和 experiment output root。

Native/V6.1 pair 运行前使用 `fairness_check.sh`，不满足同 platform、同 workload、
同 endpoint set、同 Embedding、同 decoding 时 fail closed。

更换 LLM 即使 Embedding 模型未变，也必须为该 campaign 创建 fresh namespace，清空
目标 namespace 的 nodes/relationships，重新生成 embedding，并验证 vector index 不
引用 14B campaign 的数据。禁止复制旧 Neo4j namespace 后只补增量。

## 7. 运行顺序、重复与统计

单个 history 上一次结果不足以支撑 systems claim。建议按以下层级推进：

1. provider-free unit/TDD：router selection、capacity accounting、cancel/error release、header/phase isolation。
2. live platform characterization：只做 health、KV、短 structured probe，不计入性能。
3. 2/4 episode smoke：验证双路 transport evidence、exact replay、DB freshness。
4. 8/16 episode：确认 overlap、TTFP、native P95 与 work conservation。
5. history-0 prefix-30：至少 3 次 Native/V6.1 paired repetition。
6. 只有 prefix-30 的正确性与性能 gate 通过后，才运行 5 histories 主表。

五个 history 使用平衡顺序，例如：

```text
h0: Native -> StaticRole -> V6.1
h1: V6.1 -> Native -> StaticRole
h2: StaticRole -> V6.1 -> Native
h3: Native -> V6.1 -> StaticRole
h4: StaticRole -> Native -> V6.1
```

每个条件至少三个独立 repetition；报告 median、mean、标准差、95% bootstrap CI 和
paired speedup。不能从多次 repetition 中只选最快值。

Prefix cache 必须选择一种固定策略：每次 measured run 前重启两个 LLM 做 cold cache，
或者用同一个不计时 workload 对两个方法等量预热。不能让后运行的方法继承前一方法
的 prompt cache。GPU thermal/power state 也需要固定 cooldown 或记录时序数据。

## 8. 指标与晋级条件

主指标不应只有 makespan：

| 类别 | 指标 |
| --- | --- |
| Job | construction makespan、durable goodput、paired speedup |
| Online latency | time-to-first-publication、per-source p50/p95/p99、frontier wait |
| LLM | provider logical calls、transport attempts/retries、prompt/output tokens、TTFT/TPOT |
| Resource | 每卡 utilization、memory/KV occupancy、queue depth、active sequences |
| Work | extraction/replay count、embedding items、DB reads/writes、work amplification |
| Correctness | request exact match、replay proof、semantic graph diff、QA quality |
| Reliability | timeout/reset/OOM/cancel、permit drain、seal status |

V6.1 晋级 full5 至少需要：

- 所有 correctness/evidence proof PASS；
- transport、embedding、DB work 差异全部可解释；
- paired makespan 显著优于 Native8B-dual 和 static-role 强基线；
- TTFP 不再出现 staged barrier 的数量级回归；
- 两个 endpoint 无 OOM、reset storm 或容量降级；
- 8/16/30 prefix 的收益方向一致，而不是只在一个样本点成立；
- QA/semantic quality 不劣于 fresh Native8B 的预设容忍界限。

## 9. 审稿风险与对应证据

| 可能质疑 | 当前设计的回答 |
| --- | --- |
| V6.1 多用一张卡 | Native headline 同样获得两个 endpoint |
| 8B 本来就更快 | 8B 只和 fresh Native8B 比；14B 跨 profile 不算 speedup |
| V6.1 少做工作 | request/replay/transport/embedding/DB work inventory 守恒 |
| staged 牺牲在线延迟 | TTFP 与 source latency 升为主报告指标，dual streaming 必须修复 |
| 参数为结果调优 | 平台 capacity 一次 characterization 后冻结；不同方法共用同一 manifest |
| cache/warmup 偏置 | 对称 cold restart 或等量 deterministic warmup，顺序平衡 |
| 只挑最好一次 | paired repetitions + CI，所有失败/timeout 保留 |
| 双副本 baseline 太弱 | 加 capacity-aware work-conserving Native 和 static-role 强基线 |
| Router 声称与实际不一致 | run contract 记录实现文件 hash；trace 记录 endpoint/phase/request identity |
| Embedding 共置影响 V6.1 | 两方法同 endpoint/同共置，逐卡监控并报告 interference |

## 10. 当前结论

现在已有的有意义工作是：V6.1 已证明 certified extraction reuse 可以在工作量守恒和
唯一权威写路径下，将 prefix-16 makespan 降低 11.56%；同时 trace 明确指出剩余瓶颈
是全局 phase barrier 和单 GPU phase interference，不是 replay miss。

8B 双副本是针对这个已观测瓶颈的系统设计，不是简单缩小模型或调并发参数。它的
潜在价值是用 semantic transcript handoff 实现比 DistServe KV handoff 更轻量的
application-level phase disaggregation。但在完成双 endpoint runner、fresh Native8B、
static-role 强基线和 paired repetition 之前，不能宣称 8B 或双卡已经提升 V6.1。

相关系统设计依据包括 DistServe（OSDI 2024）、Sarathi-Serve（OSDI 2024）、Parrot
（OSDI 2024）、Llumnix（OSDI 2024）、Splitwise（ISCA 2024）、AlpaServe（OSDI
2023）和 vLLM/PagedAttention（SOSP 2023）。MemBind 需要证明的差异化不是一般的
多副本负载均衡，而是“应用 DAG + certified semantic reuse + authoritative replay”
如何在不传 KV 的情况下安全地解耦阶段。
