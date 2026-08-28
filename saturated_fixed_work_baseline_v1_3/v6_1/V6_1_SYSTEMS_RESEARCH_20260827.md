# MemBind V6.1 系统研究与代码映射

> 日期：2026-08-27  
> 范围：`local-qwen3-14b-awq-v1`，单 GPU 0 上的 vLLM 0.26.0，Graphiti construction  
> 目标：让 exact extraction replay 的局部收益转化为端到端 construction makespan 收益，而不是搜索经验参数。

## 1. 从最新结果出发

冻结对照和当前候选如下：

| 方法 / artifact | episodes | makespan | 关键事实 |
|---|---:|---:|---|
| Native `76130682ee53` | 30 | 3202.687s | 冻结基线，不再运行 |
| V6.0 `57cbe4c487da` | 30 | 6103.876s | 1.906x slower；长 prompt 同时进入 FCFS vLLM，KV 90%-100% 并形成长尾 |
| V6.1 `67993dc07e9c` | 4 | 103.081s | 相对 Native 1.278x；8/8 exact replay；语义图一致 |
| V6.1 `c2b06a55b408` | 8 | 341.858s | correctness PASS；最大 outstanding 约 2；略慢于 Native 336.123s |
| V6.1 `c1c71dfc2c17` | 8 | 511.276s | KV 未超限，但 long prefill 与 native 共驻；7 次 reset；native 小请求退化至 10-30s |
| V6.1 `ca37da04d1de` | 8 | 520.597s | future 已在 native 前排空；仍有 8 路 native decode；8 次 reset |

最新两次失败不是 methodology correctness 失败。两次都满足 exact replay、request proof 和语义图一致，但性能失败揭示了资源模型错误：

```text
一个 KV token budget 不能同时代表：
1. KV/prefill 容量；
2. decode 并发效率；
3. Graphiti coroutine 提交权限；
4. HTTP transport 的健康状态。
```

因此 V6.1 的下一步是固定的多资源 admission，而不是继续搜索 `W/F/Q`。

## 2. V6.1 的应用级执行图

每个 source 的权威路径可简化为：

```text
shadow prepare
  -> certified node/edge extraction capture
  -> source-order durable frontier
  -> native Graphiti publication
       -> exact extraction replay
       -> entity/edge resolution LLM calls
       -> embeddings
       -> Neo4j writes
```

V6.1 的价值不只是“缓存两次 LLM 返回”。它把 Graphiti 内部可证明相同的 extraction 子计算变成 durable、source-ordered 的可复用结果，同时保持 native publication 是唯一权威写路径。系统优化的任务是围绕这条 DAG 缩短 job completion time，并防止非权威 future work 阻塞 critical path。

## 3. 顶会论文与官方代码结论

### 3.1 Sarathi-Serve，OSDI 2024

- 论文：[USENIX OSDI 2024](https://www.usenix.org/conference/osdi24/presentation/agrawal)
- 官方代码：[microsoft/sarathi-serve](https://github.com/microsoft/sarathi-serve)，本次核对 commit `96f9911790ecc00af12ee9fae47cb8fa9ba0d199`
- 核心机制：chunked prefill、decode-maximal batching、stall-free scheduling。
- 源码事实：`SarathiScheduler._schedule()` 先把已运行且完成 prefill 的 sequence 加入 decode batch，再在剩余 token budget 中安排未完成 prefill 和 waiting request；每个 prefill chunk 受 chunk size 限制。
- 对 MemBind 的含义：native decode 是 critical path，应先保护；future long prefill 只能使用剩余资源。当前客户端无法控制 vLLM 的逐 iteration batch，因此可立即实现的是 native 前 temporal drain 和独立 decode lane；真正的 prefill chunk policy 属于 server-side future arm。

### 3.2 DistServe，OSDI 2024

- 论文：[USENIX OSDI 2024](https://www.usenix.org/conference/osdi24/presentation/zhong-yinmin)
- 官方代码：[LLMServe/DistServe](https://github.com/LLMServe/DistServe)，本次核对 commit `82831f1604cc6b10bebd360f6c437a07790dde9f`
- 核心机制：物理分离 prefill 和 decode，分别按 TTFT 与 TPOT 配置资源与并行度，消除 phase interference。
- 源码事实：context scheduler 同时检查 batch request 数、input token 数和 GPU block；decode scheduler维护独立的 batch、waiting、swapped queue 和 block accounting。
- 对 MemBind 的含义：本机只有一张 construction GPU，不能照搬物理 disaggregation；但必须采用同样的资源解耦。V6.1 用时间隔离模拟 phase isolation：native guard 前排空 future prefill，native interval 关闭 future lane，并用独立 decode lanes 控制小请求批量。

### 3.3 Parrot，OSDI 2024

- 论文：[USENIX OSDI 2024](https://www.usenix.org/conference/osdi24/presentation/lin-chaofan)
- 官方代码：[microsoft/ParrotServe](https://github.com/microsoft/ParrotServe)，本次核对 commit `2e1825ee2bc38cb783bab9d8ec3e5ae99a93ba46`
- 核心机制：向服务端暴露 application DAG、performance criteria、context relation，并基于 DAG 做 flow scheduling。
- 源码事实：`TaskCreator._lower_criteria()` 将 latency 目标同时降低为 task 数上限和 token 数上限；`GlobalScheduler` 根据 DAG depth 做 App-FIFO，并同时检查 engine 的 task capacity 与 token capacity。
- 对 MemBind 的含义：source publication，而不是单个 LLM request，是优化目标。frontier/native path 应高于 future prepare；evidence 必须报告 frontier wait、native service、provider queue，而不能只报告全局 throughput。

### 3.4 Orca，OSDI 2022

- 论文：[USENIX OSDI 2022](https://www.usenix.org/conference/osdi22/presentation/yu)
- 核心机制：iteration-level scheduling 与 selective batching。
- 论文算法同时使用 `max_bs` 请求数上限和 `n_slots` KV slot 上限；两者承担不同约束。
- 对 MemBind 的含义：`max_num_seqs=8` 是服务端最大请求槽，不代表对该 Graphiti workload 的最优 decode batch。V6.1 必须把 provider slots、KV tokens 与 native decode lanes 分开。

### 3.5 vLLM / PagedAttention，SOSP 2023 与部署代码 0.26.0

- 论文：[SOSP 2023](https://dl.acm.org/doi/10.1145/3600006.3613165)
- 官方代码：[vllm-project/vllm](https://github.com/vllm-project/vllm)
- 本次直接核对部署 tag `v0.26.0`：scheduler 的全局 token budget 是 `max_num_scheduled_tokens`；running requests 在 waiting requests 之前被安排；`long_prefill_token_threshold` 可限制单步 long prefill；waiting queue 支持 FCFS 或 priority。
- OpenAI chat protocol 支持 `priority` 字段，但非零 priority 在 server 未启用 priority scheduling 时会报错。本轮服务固定为 FCFS，因此不能假装客户端 priority 已生效。
- 对 MemBind 的含义：当前可执行边界是在请求进入 FCFS queue 之前做 admission。改成 server priority、custom scheduler 或更细 prefill chunk 是独立部署实验，会改变冻结环境，不能混入 V6.1-Core 主比较。

### 3.6 Llumnix，OSDI 2024

- 论文：[USENIX OSDI 2024](https://www.usenix.org/conference/osdi24/presentation/sun-biao)
- 官方代码：[AlibabaPAI/llumnix](https://github.com/AlibabaPAI/llumnix)
- 核心机制：跨实例 runtime rescheduling、live migration、load isolation 和 priority differentiation。
- 对 MemBind 的含义：它验证了异构长短请求需要运行时隔离，而不是只在到达时静态分流；但单实例、单 GPU 的本轮无法迁移已提交请求，所以 admission 必须在提交前 fail-closed，并通过 drain 建立不可抢占边界。

## 4. 直接落地的系统设计

V6.1-Core 固定采用三维资源模型：

| 资源 | 固定约束 | 解决的问题 |
|---|---:|---|
| provider request slots | 8 | 不超过部署 `max_num_seqs` / client authority |
| admitted KV tokens | 61,440 | 65,968 实测 KV tokens 减 4,528 headroom |
| native decode lanes | 2 | 避免 8 路 structured decode 的 10-30s 服务时间和 reset |

调度规则：

1. prepare-to-prepare 可在 KV 和 future cap 内重叠，以隐藏 shadow extraction 的 critical-path latency。
2. native guard 一旦进入，立即关闭 future admission，并排空已提交的 long future call。
3. native interval 内最多允许 2 个真实 native provider call 同时执行。
4. exact replay 不占 provider admission，因为它不产生外部 transport。
5. source publication 保持严格 source order；future result 不允许越过 durable frontier 写数据库。

`native decode lanes = 2` 不是 autoresearch knob。它来自同一固定部署的反事实 trace：旧约 2 路时小请求平均约 2s；8 路时平均 10s 以上、最大约 30s，并产生 7-8 次 reset。以后更换 GPU、模型、vLLM batch 或 Graphiti prompt 分布时必须重新 characterization，并创建新实验身份。

## 5. 暂不直接落地的机制

以下机制有价值，但会改变 server 或实验合同，不能混入当前 V6.1-Core：

- Sarathi 式逐 iteration decode-maximal chunk scheduler：需要 vLLM custom scheduler 或服务配置变更。
- DistServe/Splitwise 物理 prefill-decode disaggregation：需要额外 GPU 和 KV transfer。
- vLLM priority queue：需要将固定 FCFS server 改为 priority，并重新跑所有可比 baseline。
- Llumnix live migration：需要多个同模型实例。
- 修改 Graphiti candidate、dedupe 或 timestamp 语义：可能改变图结果，必须作为独立 methodology arm。

## 6. 工程正确性合同

每个 live attempt 除功能结果外必须检查：

1. permit identity 一一配对；取消、provider error、DB error 后所有计数归零。
2. `outstanding`、`future_outstanding`、`native_outstanding`、`tokens_outstanding` 永不为负。
3. seal 时无 active permit、waiter、native guard 或未关闭 future task。
4. `max_native_outstanding <= native_decode_lanes`，且 guard ready 时 active future 为 0。
5. `provider_calls.jsonl` 只含 wrapper logical call；retry event 不混入其 schema。
6. `transport_attempts = provider_external_logical_calls + transport_retry_attempts`。
7. runtime close 即使一个组件失败也继续关闭其余组件；部分失败后可重试；重复成功 close 幂等。
8. transport reset 只允许 native non-certified suffix 做一次 bounded recovery；certified capture/replay 继续保持 exact single-attempt contract。
9. entities/edges 比较忽略 `uuid`、`created_at`、`group_id`、`expired_at` 等运行元数据，再判断语义差异。
10. Native 只读取冻结 artifact，不再次消耗 GPU。
11. 高频 trace 使用 30 秒 bounded group commit；每行仍立即 flush 供 observer 读取，publication durable、失败、retry 与 close 强制 fsync，避免逐事件 fsync 的 observer effect。

## 7. 实验与晋级规则

下一轮先运行新的 8-episode namespace，固定 `lookahead=2, future_cap=1, native_future_quota=0`，只验证多资源方法代码。该组数值不是继续搜索对象。

必须同时满足：

- exact replay 和 request identity 全通过；
- 语义 entities/edges 与稳定 V6.1 reference 一致；
- `max_native_outstanding <= 2`；
- transport reset/retry 明显低于 8-way run；
- native 小请求 service time 回落；
- makespan 优于 341.858s 的旧 V6.1，并以冻结 Native 336.123s 为晋级目标；
- wrapper logical calls、transport attempts、embedding items、DB writes 的差异可解释。

如果 8-episode 仍慢，下一步按 trace 定位 non-provider suffix、frontier wait 或 transport，而不是扩大搜索空间。只有同一 30-episode prefix 的 V6.1 真正优于冻结 Native 与 V6.0，才允许启动 full5。
