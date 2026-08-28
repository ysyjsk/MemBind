# MemBind V6.1 完整离线优化设计

状态：`SUPERSEDED_FOR_LOCAL_PROFILE_BY_LIVE_AUTORESEARCH`  
版本：`6.1`  
父版本：冻结的 V6 exact native-demand extraction replay  
2026-08-27 更新：本文件原有的 live 禁止条款已被
`MemBind_V6_1_Local_Qwen_Autoresearch_Workplan.md` 针对
`local-qwen3-14b-awq-v1` profile 明确覆盖。当前允许在新 namespace 和新 artifact root
中做 provider 调用与 Neo4j 写入；仍禁止修改 V6/V5 共享实现、扩大 replay 白名单或混入
冻结的 Qwen3-32B-FP8 产物。本文后续 gate 作为设计参考和诊断清单，不再作为会停止持续
autoresearch 的硬 gate。

## 1. 结论与版本定位

V6.1 不推翻 V6。最新 MAB context 2 已经证明 V6 最核心的机制成立：116 个
source 的 232 个 node/edge extraction 请求全部 exact capture/consume，native
extraction 从 B0 的 5350.95 秒下降到 0.389 秒。V6.1 要解决的是另一个问题：
如何让这 5350.56 秒的 critical-path opportunity 真正转化为端到端收益，而不是被
state-dependent suffix、共享 FCFS provider 干扰和 work amplification 吃掉。

V6.1 是 V6 的性能工程子版本，不是 V7：

- V6.1 保留 native-demand identity check、exact replay、single consume、fallback 和
  source-order durable publication。
- V6.1-Core 只优化调度、admission、lookahead、backpressure、证据和成本控制。
- V6.1-Suffix 单独研究 timestamp、dedupe、embedding 等后缀优化；没有 exact
  refinement proof 的候选不能进入 V6.1-Core，也不能与调度优化一起做第一次 live。
- V7 的 semantic certificate、incremental view 和 native-continuation 研究保持独立，
  不借 V6.1 的名字绕过其 Gate A-E。

因此，V6.1 的一句话方法定义是：

> 在不改变 Graphiti 原生语义和 exact replay contract 的前提下，用 foreground-protected、
> cost-aware、bounded-JIT speculation 替换 V6 的全量 eager speculation，并用完整的
> queue/service/work 证据选择策略。

## 2. 最新结果给出的精确问题

唯一完整的 MAB 三方 context 2 结果为：

| 指标 | B0 | V6 | V6 相对 B0 |
|---|---:|---:|---:|
| `T_build` | 8288.714 s | 7147.375 s | `1.1597x`，降低 13.77% |
| node extraction | 2116.515 s | 0.188 s | 基本消除 |
| edge extraction | 3234.434 s | 0.201 s | 基本消除 |
| node resolution | 1658.131 s | 3111.095 s | 1.88x 变慢 |
| edge resolution | 134.400 s | 1751.754 s | 13.03x 变慢 |
| attributes/summary | 1142.785 s | 1970.097 s | 1.72x 变慢 |

收益账本为：

```text
native extraction opportunity                  +5350.56 s
state-dependent suffix 增量                    -3897.85 s
frontier 首次准备/等待等未隐藏成本              -311.45 s
----------------------------------------------------------
最终净收益                                      +1141.34 s
```

V6 的问题不是 replay 没命中，而是 opportunity conversion rate 只有约 21.3%。

### 2.1 Provider interference

| transport 指标 | B0 | V6 |
|---|---:|---:|
| attempts | 1273 | 1482 |
| duration sum | 9163.397 s | 36993.570 s |
| mean | 7.198 s | 24.962 s |
| P50 | 1.530 s | 1.115 s |
| P95 | 23.774 s | 121.949 s |

P50 没有恶化而 P95 增长约 5.1 倍，说明主要问题是长尾干扰，不是所有调用的固定
overhead。edge-resolution 更明显：中位数只从 0.965 秒变为 1.099 秒，但 P95 从
4.164 秒变为 106.763 秒；source 5 单项达到 448.546 秒。

### 2.2 Work amplification

| 工作量 | B0 | V6 | 增量 |
|---|---:|---:|---:|
| entities | 602 | 712 | +18.3% |
| edges | 464 | 559 | +20.5% |
| LLM logical requests | 1271 | 1482 | +16.6% |
| embedding items | 3162 | 3795 | +20.0% |

V6 的 1482 个 transport 由 232 个提前 extraction transport 和 1250 个 native-only
suffix transport 组成；B0 后缀只有 1039 个调用。独立 live stochastic response 使两次
运行走出了不同的图和 adaptive work path。这个变化必须单独归因，不能全部算成调度
损失，也不能通过随意限制 extraction 输出把它“优化掉”。

### 2.3 已确认的 evidence/runner 缺陷

当前 MAB V6 runner 存在以下缺陷，但 V6.1 不回改旧 runner：

1. provider arbiter 没有传给 `run_frontier_history_async`，executor 和 provider 实际持有
   不同 arbiter，frontier advancement 没有直接唤醒真实 provider arbiter。
2. provider proof 调用 `validate_provider_events([])`，导致 1482 次 transport 的运行仍
   产生 `admission_count=0` 的 vacuous PASS。
3. `prepared_response_hash` 实际哈希 request private payload，不是 response。
4. `db_writes=0` 来自错误地从 metadata 读取 operation class；真实 trace 已有 writes。
5. MAB 制品没有封存 prepare/native interval、admission queue 和 policy state，无法直接
   计算 timely-ready、frontier starvation 和 speculative queue delay。

这些问题不会被解释为 V6 replay correctness 失败，但它们阻止精确的调度因果分析。
V6.1 必须在新 schema 中一次性修复。

## 3. 为什么参考顶会，但不能直接照搬参数

V6.1 直接吸收相关系统工作的机制，而不是重复发明调度概念：

| 工作 | 可迁移机制 | Graphiti/V6.1 边界 |
|---|---|---|
| Parrot, OSDI 2024 | application/program dataflow、semantic variable、frontier-aware scheduling | Graphiti native demand 是 adaptive 的；只能由真实 native callsite 确认 replay，不能从 source order 猜请求 |
| Sarathi-Serve, OSDI 2024 | 将 prefill/decode interference 作为一等问题，限制长 prefill 对 foreground 的 stall | 当前不能修改共享 vLLM 的 chunked-prefill/scheduling；先在 client 侧限制 future work 和建立 guard |
| DistServe, OSDI 2024 | 通过资源隔离保护不同阶段的 SLO | 8000 endpoint 冻结，V6.1-Core 不新增服务或 GPU；只把它保留为需要额外授权的 service-side lane |
| Llumnix, OSDI 2024 | 动态调度、迁移和负载变化下的 SLO 保护 | 当前无请求迁移权限；借用动态反馈思想，不声称具备 Llumnix 的服务端控制能力 |
| vLLM, SOSP 2023 / Orca, OSDI 2022 | continuous/iteration-level batching 使并发影响非线性 | 不能用 client semaphore 数量代替 GPU isolation；必须实测 native latency inflation |
| Agentix, NSDI 2026 | agent program DAG 与 critical-path-aware execution | V6.1 使用 Graphiti phase/DAG，但不把通用 agent scheduling 当 novelty |
| Speculate with Memory | speculation 必须有 readiness、validation 和 fallback contract | V6.1 沿用 exact identity + native demand + fallback，并把 interference cost 纳入选择 |

顶会给出了正确的设计原则：保护 foreground、控制大请求干扰、按 critical path 调度、
动态反馈。但它们不能替我们决定 `future_cap=1` 还是 `2`、`lookahead=2` 还是 `4`，因为：

- 当前共享 vLLM 的 service discipline、prefix cache 和 batch composition 不可控；
- Graphiti native suffix 会突发地产生大量 timestamp/dedupe 请求；
- extraction request 的 prompt/output 长度高度不均匀；
- 已 admission 的 FCFS 请求不可抢占；
- V6 的收益取决于准备是否及时完成，而不是单纯把并发降到最小。

所以 V6.1 可以先按理论直接实现机制，但参数和是否启用必须由离线 trace-driven search
筛选，再进入最小 live qualification。

## 4. 目标、约束与非目标

### 4.1 主目标

在 fixed workload、fixed Graphiti、fixed model/client/backend identity 下，提高：

```text
NetBenefit = T_build(V6 frozen) - T_build(V6.1)
```

同时降低：

```text
native provider latency inflation
frontier starvation time
untimely speculative work
future work admitted while native work is queued
```

### 4.2 不可违反的 correctness constraints

1. native Graphiti 仍生成每个 certified request。
2. 只有完整 request identity exact match 才 replay。
3. certified transcript capture/consume 都必须严格为 1。
4. 所有 miss 和非 certified callsite 都调用真实 provider。
5. shadow preparation 不写 authoritative graph。
6. durable publication 严格 source order。
7. 不以取消已提交 provider request 作为 correctness 或性能前提。
8. crash/partial artifact 不得被 success path 消费。

### 4.3 V6.1-Core 非目标

- 不修改 extraction prompt、temperature、schema、模型或 max tokens。
- 不减少模型抽出的 entity/edge 数量来伪造吞吐收益。
- 不 batch timestamp、不缓存 stochastic dedupe response、不跳过 native demand。
- 不修改 vLLM launch args，不新增 8002/8003，不重启共享 provider。
- 不根据 B1 relaxed-order reference 宣称等价语义上界。
- 不在 QA 仍无效时声明质量或语义等价。

## 5. 独立版本和代码边界

V6.1 后续实现只能新增以下路径：

```text
saturated_fixed_work_baseline_v1_3/
  src/saturated_fixed_work_baseline_v1_3/membind_v6_1/
    __init__.py
    contracts.py
    trace_ingest.py
    dag.py
    service_model.py
    scheduler.py
    simulator.py
    policy_search.py
    proof.py
    reducer.py
    artifact.py
    runtime_adapter.py          # offline gate 通过前不得实现 live transport
  scripts/
    run_membind_v6_1_offline.py
    run_membind_v6_1_live.py    # offline gate 通过前不得创建
  tests/
    test_membind_v6_1_*.py
  v6_1/
    artifacts/
```

以下路径冻结且不得因 V6.1 修改：

```text
.../membind_v6/
.../membind_v5/runtime/
.../mab_live_runner.py
所有 sealed V6/MAB artifact root
```

V6.1 的 method identity、namespace、run id 和 schema 必须使用：

```text
MEMBIND_V6_1
membind-v6-1-<run-id>-<context>-<attempt>
membind.v6_1.*.v1
```

旧 V6 的 hashes 由 `VERSION_BOUNDARY.json` 冻结。未来 CI 必须在每次 V6.1 test 中验证
这些 hashes 没有变化。V6.1 可以读取旧 artifact，也可以通过公开稳定接口调用
Graphiti，但不能让旧 V6 import V6.1。

## 6. V6.1 总体架构

```text
Frozen workload / sealed traces
              |
              v
      Trace Ingest + Validator
              |
              v
     Graphiti Critical-Path DAG
              |
       +------+-------+
       |              |
       v              v
 Empirical service   Work/graph-normalized
 time models         counterfactual models
       |              |
       +------+-------+
              v
 Foreground-Protected Policy Simulator
              |
              v
 Grid / robust policy search + ablations
              |
              v
 OFFLINE_SELECTION.json
              |
      offline gates all pass?
         no /       \ yes
   remain offline    authorize implementation review
```

运行时概念架构为：

```text
                         +----------------------+
source i ------------->  | bounded prepare      |
                         | window W              |
                         +----------+-----------+
                                    |
                                    v
                         +----------------------+
                         | cost/readiness gate  |
                         +----------+-----------+
                                    |
                         P1/P2      v
                  +--------------------------------+
native P0 ------> | one shared provider scheduler  |
                  | strict foreground priority     |
                  | future cap F, guard Q, C total |
                  +----------------+---------------+
                                   |
                                   v
                            frozen provider

native demand -> exact identity -> replay or real provider -> ordered publish
```

## 7. V6.1-Core 优化机制

### 7.1 L0：Evidence integrity

这是 mandatory fix，不作为性能贡献：

- executor、capture client、replay client 共享同一个 policy scheduler/arbiter；
- publication 必须向该 arbiter 发 `FRONTIER_ADVANCE`；
- provider proof 必须读取真实 admission events，非空 transport 不能产生零 admission PASS；
- response hash 对 transcript 中 canonical response 计算，而不是 request payload；
- DB writes 从 span 的 `operation_class` 统计；
- raw lifecycle、frontier、admission、policy、prepare/native interval 分文件封存；
- public artifact 只保留 digest/计数，私有 payload 权限固定为 `0600`；
- sealed legacy artifacts 不回填、不改写，只在新的 derived audit 中标记旧证据缺口。

### 7.2 L1：三类优先队列

所有真实 provider work 统一分为：

```text
P0 NATIVE_FRONTIER   当前 authoritative source 的 state-dependent call
P1 FRONTIER_PREPARE  下一 source d+1 的 certified extraction
P2 FUTURE_PREPARE    d+2 ... d+W 的 certified extraction
```

优先级固定为 `P0 > P1 > P2`。当 P0 waiter 存在时，禁止新 admission P1/P2。P0 可使用
全部 `C` 个 client permits，因为 edge-resolution 可能一次产生几十个 timestamp/dedupe
调用；V6 仅保留一个 slot 对这种 native burst 不足。

这只能控制尚未提交的请求，不能抢占 provider 内已经运行的 P2。因此还需要 L2/L3。

### 7.3 L2：Bounded JIT lookahead

V6 eager 创建全部 source preparation；V6.1 只允许窗口：

```text
eligible sources = [d + 1, d + W]
```

窗口外 source 不创建 provider work，也不在 arbiter 中排队。每次 durable frontier 推进
才开放一个新 source。这样同时限制：

- 长 future extraction 提前过多进入 FCFS provider；
- 116 个 task 对 scheduler evidence 的噪声；
- 已经准备完成但距离消费很远的 transcript residence time；
- failure 时需要取消/丢弃的 speculative work。

`W` 不是硬编码答案，离线候选为 `{1, 2, 4, 8}`；`W=all` 只作为 frozen V6 对照。

### 7.4 L3：Future cap 与 native guard

定义：

```text
C = frozen provider client capacity, current value 8
F = max admitted P2 requests
Q = max P2 requests allowed to remain active when native begins
```

候选空间：

```text
F in {0, 1, 2, 4, 7}
Q in {0, 1, 2}
Q <= F
```

当 d+1 preparation 已 ready、准备进入 native publication 时：

1. scheduler 进入 `GUARD`；
2. 不再 admission 新 P2；
3. 只取消仍在 client queue、尚未 transport 的过远 P2；
4. 等待 active P2 降至 `Q`，然后开始 native；
5. native 期间 P0 独占新释放的 permits；
6. source durable 后重新进入 `FILL`。

V6.1 不强制 `Q=0`。如果等待一个 200 秒的已提交 extraction 比共享执行更差，`Q=1/2`
可能更优；这个权衡必须由 trace-driven robust search 决定。

### 7.5 L4：Cost-aware admission

每个 P2 candidate j 计算：

```text
benefit_j = P(ready before native demand) * predicted_native_extraction_saved_j
cost_j    = predicted_provider_occupancy_j * foreground_interference_weight
score_j   = benefit_j - lambda * cost_j
```

只在以下条件全部满足时 admission：

```text
source_sequence <= d + W
active_future < F
no P0 waiter
score_j > 0
P(ready before demand) >= p_ready_min
interference_budget_remaining >= predicted_provider_occupancy_j
```

预测特征仅使用 demand 前可得字段：source body/token length、callsite、response schema、
previous-window size、历史 token bucket 和最近完成请求的 duration。不能偷看本次 response、
最终图大小或未来 live outcome。

第一版 cost model 使用可解释的分桶统计，不使用黑盒学习器：

```text
(callsite, input-token bucket, previous-window bucket)
    -> service-time p50/p90/p95, output-token p50/p95
```

### 7.6 L5：Adaptive interference controller

静态最优参数可能随 provider load 变化。V6.1 的自适应策略只在 source durable 边界更新，
避免单请求抖动：

```text
native_inflation = EWMA(
    observed_native_duration /
    isolated_reference_duration(callsite, token_bucket)
)
```

建议的 AIMD 规则：

```text
if native_inflation > high_watermark or native_p95_budget violated:
    F_next = floor(F / 2)
    W_next = max(1, W - 1)
elif timely_ready_rate < target and native_inflation <= low_watermark:
    F_next = min(F + 1, F_max)
else:
    keep current policy
```

约束：

- 初始 `F/W` 来自离线静态 winner；
- 只能在预注册范围内变化；
- 每次变化必须写 `POLICY_UPDATE`，包含观测、旧值、新值和 reason code；
- native waiter 存在时，controller 无权放宽 P2 admission；
- provider telemetry 缺失时 fail closed 到较小 F，不凭 endpoint aggregate counter 乐观扩容。

### 7.7 L6：Queued-work cancellation，不取消 transport

只允许取消尚未获得 admission 的 P2 task。已经进入 HTTP transport/provider 的请求不依赖
取消语义，因为共享 vLLM 是否真正停止计算未经证明。取消事件必须区分：

```text
CANCELLED_BEFORE_ADMISSION       safe, zero provider work
CANCEL_REQUESTED_AFTER_SUBMIT    accounting only, not assumed to save work
COMPLETED_BUT_NOT_CONSUMED       must be reported, cannot silently discard
```

## 8. V6.1-Suffix：全部后缀问题的独立优化 lane

“把所有可能有问题的任务都优化”不等于把所有改动一次性塞进 live。以下候选确实针对
node/edge resolution 和 attributes-summary，但会触碰 native stochastic semantics：

| 候选 | 潜在收益 | 主要风险 | V6.1 处理 |
|---|---|---|---|
| timestamp calls batching | 减少 539 个 timestamp transports | prompt/schema/call trace 改变，错误相关性改变 | 仅 V6.1-Suffix 离线 differential lane |
| edge dedupe batching/cache | 减少 537 个 resolve transports | 同 request 重调在 stochastic oracle 下不等于可复用 | 默认禁止；只有 provider oracle contract + native trace refinement 才开放 |
| node summary batching扩大 | 减少 67 个 summary calls | batch composition 会改变输出和 graph quality | 单独质量/语义实验 |
| embedding exact cache | 减少 20% embedding amplification | embedder/model/config epoch、float/ordering、写入引用 | 需要 exact input + epoch + consumer equivalence proof |
| candidate-query memoization | 减少 Neo4j reads | graph snapshot/version 和 top-k phantom 风险 | 使用 V7 certificate 研究，不并入 V6.1-Core |
| deterministic parse/object reuse | 降低本地 parse overhead | 需要证明对象 identity/UUID 不泄漏到 native seam | 可先做 provider-free differential tests，收益预计较小 |
| 限制 entity/edge 数 | 直接降低 suffix work | 明确改变 extraction semantics 和质量 | 不属于性能等价优化，禁止 |

V6.1-Suffix 的晋级条件：

1. complete call-trace refinement，而不是最终 set 看起来相同；
2. canonical graph、UUID-sensitive continuation 和 publication effect 等价；
3. adversarial tests zero false accept；
4. live stochastic oracle assumption 明确成立；
5. 独立消融证明收益，不与 scheduler 同时首次启用；
6. QA 有效后才能做质量 non-regression 声明。

若这些条件不成立，V6.1 的正式方法只保留 Core。这样仍然覆盖了所有问题：安全问题被
优化，高风险问题被明确证伪或隔离，而不是被遗漏。

## 9. Scheduler 状态机和伪代码

状态：

```text
FILL   -> 准备 d+1，并在预算内准备 future
GUARD  -> d+1 ready；停止新 future，等待 active future <= Q
NATIVE -> authoritative source 执行；P0 优先使用全部 permits
ADVANCE-> durable commit、更新 d、丢弃无效 queued work、打开新窗口
FAILED -> 停止 admission，保存 partial evidence，不推进 frontier
```

伪代码：

```python
while d + 1 < source_count:
    open_window(d + 1, min(source_count - 1, d + W))
    ensure_frontier_prepare_started(d + 1)          # P1

    while not prepared(d + 1):
        if no_native_waiter() and active_future < F:
            candidate = best_positive_score_future()
            if candidate is not None:
                admit(candidate, priority=P2)
        await next_completion_or_failure()

    state = GUARD
    stop_new_future_admission()
    cancel_queued_future_outside_new_need()
    await active_future_at_most(Q)

    state = NATIVE
    await native_add_episode(
        source=d + 1,
        priority=P0,
        exact_native_demand_replay=True,
    )

    assert durable_publication(d + 1)
    d += 1
    update_controller_at_source_boundary()
    state = ADVANCE
```

关键 invariant：只要 P0 waiter 存在，P2 admission 数不再增加；只要 native source 未
durable，frontier 不推进；任何 scheduler 决策都不能直接提供 replay response。

## 10. 完整离线实验系统

### 10.1 输入 corpus

离线设计读取但不修改：

1. MAB context 2：完整 B0/B1/V6，116 sources，最新主要诊断样本。
2. MAB context 0：完整 B0/V6，111 sources，用于跨 context robustness。
3. MAB context 1 V6：107 sources，只作 candidate trace，不作 B0 paired estimate。
4. 旧 V6 `6071bd76`：46 sources、两组 counterbalanced control/V6，拥有完整
   `admission.jsonl` 和 prepare/native intervals，用于 scheduler evidence calibration。
5. context 0 prefix 1/2/8：只作小规模边界行为和模型 sanity check。

每个输入记录 artifact seal/hash、method、source count、endpoint/model identity、证据缺失
字段和可用于何种 claim。缺少 admission trace 的 MAB V6 不能被伪装成完整 scheduler
ground truth。

### 10.2 DAG reconstruction

每个 source 至少重建：

```text
prepare.node_extract -> prepare.edge_extract -> PREPARED

previous_context
  -> native.node_extract(replay)
  -> node_resolution
  -> native.edge_extract(replay)
  -> edge_resolution
  -> attributes_summary
  -> publication
  -> durable_frontier
```

LLM/embedding/database 子 span 作为 phase 内部节点；只有 parent/interval 和 async gather
证明串并行关系时才连边。并行 child duration 不得直接求和当 critical path。

### 10.3 四种离线 service model

| 模型 | 方法 | 能回答什么 | 不能回答什么 |
|---|---|---|---|
| M0 observed replay | 保持每个 observed duration，重放原调度 | event parser、状态机和 timer reconstruction 是否正确 | 改调度后的真实 provider latency |
| M1 token-conditioned bootstrap | 按 callsite/token bucket 从 B0/低干扰样本 bootstrap service time | 参数相对排序和置信区间 | 精确复现 vLLM batching |
| M2 interference envelope | 使用 isolated p50 到 observed V6 p95/p99 的上下界 | policy 是否在悲观条件下仍不退化 | 单点精确加速比 |
| M3 fixed-work normalized | 固定同一 extraction transcript、graph size 和 suffix call DAG | 纯 scheduler 贡献 | live stochastic work amplification |

离线报告必须同时给 M1/M2/M3；只在一个模型上胜出不能晋级。

### 10.4 Simulator correctness

simulator 必须是 deterministic discrete-event engine：

- 相同 seed/config/input 得到 byte-identical output；
- 不 import OpenAI/vLLM client，不打开 socket，不连接 Neo4j；
- capacity、priority、window、guard、cancellation 都由事件状态机执行；
- 每个 policy 输出 critical path、queue delay、service time、ready slack、wasted work、
  native inflation 和 utilization；
- M0 对可完整重建的旧 V6 trace，timer reconstruction error 必须小于 1%；
- 对 evidence 缺失的 MAB trace明确输出 `PARTIAL_OBSERVABILITY`，不能插值为真实事件。

## 11. 离线 policy search

第一轮网格：

```text
W                 = 1, 2, 4, 8, all
F                 = 0, 1, 2, 4, 7
Q                 = 0, 1, 2  where Q <= F
p_ready_min       = 0.50, 0.75, 0.90
interference cap  = low, medium, high
controller        = static, AIMD
```

搜索不是按平均 `T_build` 单目标选择，而是按词典序：

1. correctness/proof 全通过；
2. native P95 inflation 最低；
3. M1 bootstrap 的 paired net benefit 90% lower bound 最大；
4. M2 pessimistic envelope 的最大回退最小；
5. timely-ready rate 高且 wasted speculative work 低；
6. 参数更少、策略更简单者优先。

建议先验不是最终答案：`W=2, F=1, Q=0`。它表达 foreground-first 的保守起点，
不能在看到模拟结果前宣布为 winner。

## 12. 必须完成的消融矩阵

| Arm | Replay | timed prepare | W | F | guard/cost | 目的 |
|---|---|---|---|---|---|---|
| A Frozen V6 | exact | yes | all | 7 | no | 当前实现基线 |
| B Evidence-only | exact | yes | all | 7 | no | 证明 evidence 修复本身不改变性能机制 |
| C Cap-only | exact | yes | all | search | no | future cap 单独贡献 |
| D Window-only | exact | yes | search | 7 | no | bounded JIT 单独贡献 |
| E Guard-only | exact | yes | all | 7 | Q search | native quiescence 单独贡献 |
| F Static combined | exact | yes | search | search | guard | 静态完整策略 |
| G Cost-aware | exact | selective | search | search | score | 避免长且不及时的 speculation |
| H Adaptive | exact | selective | dynamic | dynamic | AIMD | 负载变化鲁棒性 |
| I Traffic-only | no consume | yes | winner | winner | winner | 隔离 speculative traffic harm |
| J Magic replay | exact | no timed traffic | n/a | n/a | n/a | replay opportunity 上界 |
| K No future | exact | frontier only | 1 | 0 | Q=0 | speculation 下界/foreground reference |

离线阶段全部运行。未来 live 不需要机械地运行全部 arm，而是只运行能区分剩余因果问题的
最小集合；但不能只跑最终 winner 对历史 B0。

## 13. Metrics 与归因

### 13.1 Primary

```text
T_build: FORMAL_START -> final PUBLICATION_DURABLE
paired net benefit: same trace/model/seed 下 Frozen V6 - V6.1
```

### 13.2 Scheduler mechanism

```text
prepare ready slack
timely-ready rate
native admission queue delay
native request duration p50/p95/p99
native inflation by callsite/token bucket
future outstanding max/time-weighted mean
P2 admitted while P0 waiting (must be zero)
GUARD drain time
queued cancellation count
completed-but-unused speculation count
```

### 13.3 Work normalization

```text
logical requests / source
transport attempts / source
embedding items / source
entities and edges / source
suffix calls / entity and / edge
input/output tokens by PREPARE vs NATIVE
```

### 13.4 Evidence completeness

```text
admission_count == real provider calls requiring admission
capture == consume == exact binding count
response hash coverage == capture count
DB write spans > 0 when graph publication succeeds
all policy transitions reason-coded
all intervals monotonic and source-attributed
```

## 14. Offline proof obligations与测试

### 14.1 Property tests

1. `outstanding <= C` 始终成立。
2. `future_outstanding <= F` 始终成立。
3. P0 waiter 存在时不发生新的 P2 admit。
4. eligible prepare source 不超过 `d+W`。
5. `F=0, W=1` 仍能完成，不死锁。
6. 任意 completion order 下 publication 恒为 `0..N-1`。
7. preparation/native failure 不推进 durable frontier。
8. cancel 只移除未 admission work。
9. exact mismatch 永不 replay；miss 调用 fallback。
10. transcript 不 duplicate consume，run 结束无 silent orphan。

### 14.2 Differential tests

- V6.1 scheduler 关闭优化时，provider-free event semantics 与 frozen V6 一致；
- 同一 recorded response bank 下，Frozen V6 和 V6.1 产生相同 native request identity、
  call trace 和 canonical graph；
- evidence-only arm 不改变 transport schedule 以外的 observable behavior；
- response hash 修改 response 任意字段时必须变化，修改 request-only payload不能冒充
  response change；
- DB work inventory 与 native trace 的 operation class 逐项一致。

### 14.3 Adversarial scheduler tests

- 一个 1000 秒 P2 和突发 32 个 P0；
- P2 在 guard 前后完成的竞态；
- frontier advance 与 class reclassification 同时发生；
- capacity 1、2、8；
- source 0 preparation 极慢；
- future prepare failure、native timeout、structured-output retry；
- provider aggregate telemetry 缺失或回退；
- task cancel 返回但 provider work 继续的保守模型。

## 15. Offline 晋级门槛

所有 mandatory gates 同时满足才允许讨论 live：

### Gate A：Isolation

- `VERSION_BOUNDARY.json` 中冻结 hashes 不变；
- V6.1 只新增独立 namespace/path；
- offline CLI 不含 `--execute-live`，网络和 graph write 为零。

### Gate B：Correctness

- property/differential/adversarial tests 全通过；
- zero false replay、zero publication inversion、zero duplicate consume；
- scheduler disable 后与 frozen V6 provider-free semantics 一致。

### Gate C：Evidence

- admission proof 非 vacuous；
- response/work accounting 正确；
- policy/interval/queue trace 完整；
- simulator 对完整旧 V6 trace 的 M0 reconstruction error < 1%。

### Gate D：Robust performance prediction

- M1 bootstrap paired net benefit 的 90% lower bound > 0；
- M2 pessimistic model 下最大 `T_build` regression <= 5%；
- fixed-work M3 下 native P95 inflation 相对 Frozen V6 至少降低 25%；
- completed-but-unused speculation <= 5%；
- P2 admitted while P0 waiting = 0。

### Gate E：Interpretability

- cap、window、guard、cost-aware、adaptive 各自有消融；
- winner 不依赖单个 context/source 5 outlier；
- work amplification 与 scheduler effect 分开报告；
- 没有把 B1、不同 endpoint 或无效 QA 当等价性能证据。

任一 gate 失败，状态为 `V6_1_OFFLINE_NOT_READY`，不启动 live。

## 16. 后续实现顺序（本阶段不执行）

### R0：Offline contracts

- 建新 package、version contract、artifact schema、no-network guard；
- ingest sealed corpus，生成 `INPUT_CORPUS.json`；
- 不实现 runtime adapter。

### R1：Evidence audit

- 实现 trace validator、DAG reconstruction、legacy evidence gap report；
- 修复逻辑只写入 V6.1 derived artifacts，不修改旧 seal。

### R2：Simulator and static policies

- 实现 deterministic engine、F/W/Q、M0-M3；
- 完成 property/adversarial tests；
- 跑静态网格和消融。

### R3：Cost-aware and adaptive policies

- 只使用 demand-time features；
- 完成 bootstrap/robust selection；
- 输出 `OFFLINE_SELECTION.json` 或明确 NULL。

### R4：Provider-free runtime composition

- 用 fake provider delays 和 recorded responses 运行完整 Graphiti-compatible control flow；
- 证明 exact replay、fallback、failure 和 artifact seal。

### R5：Live authorization review

- 只有 Gate A-E 全通过才新建独立 `run_membind_v6_1_live.py`；
- 先跑预注册的小 prefix interference slice，再跑 counterbalanced full context；
- 不在同一个 live attempt 期间改代码/参数。

## 17. 未来 live 的最小合理设计（当前不执行）

离线 winner 产生后，live 至少需要：

```text
Frozen V6 vs V6.1 winner，same executable family / same provider identity
counterbalanced ABBA 或 BAAB
fresh namespace per arm
prefix interference slice: context 2 sources 0..5
full run: 至少一个完整 context，随后 held-out context
```

同时保留两个诊断 arm：

```text
traffic-only  -> 测 speculative traffic harm
magic replay  -> 测无 timed speculation 时的 replay opportunity ceiling
```

prefix 只能决定是否值得完整运行，不能代替 full-history claim。完整 live 结果必须同时报告
raw `T_build`、fixed-work-normalized estimate、work amplification、native p95 和 proof。

## 18. Stop conditions

以下任一情况停止增加复杂度：

- `F=0/1` 仍无法降低 native long tail，说明主要瓶颈不是 client-side speculation；
- offline winner 对 service model 极敏感，M2 下显著退化；
- cost model 必须偷看 response/未来 graph 才能有效；
- adaptive controller oscillation 导致频繁 policy change；
- suffix 候选无法证明 call-trace/native-continuation refinement；
- 优化收益只来自减少 entity/edge 或其他语义变化；
- evidence 仍不能区分 queue delay、service duration 和 work amplification。

此时应将结论写成 client-only V6.1 的性能边界，并把 server-side priority/disaggregation
作为需要新权限和新实验的后续工作，而不是继续堆 client heuristic。

## 19. V6.1 预期研究价值

V6 已经证明“哪些 Graphiti 工作可以 exact reuse”。V6.1 要回答更重要的系统问题：

> 当 speculation 与 authoritative state-dependent memory construction 共享同一个 FCFS
> LLM provider 时，如何把 reuse opportunity 转化为稳定的端到端收益，同时不给 native
> frontier 制造长尾？

价值不在于发明 semaphore 或 lookahead，而在于把以下内容闭合到一个可验证系统中：

- native-demand exact replay 的语义安全边界；
- Graphiti adaptive DAG 和 bursty suffix 的 foreground protection；
- speculative readiness、waste 和 interference 的统一成本模型；
- fixed-work normalization 与 live stochastic graph amplification 的分离；
- client-only control 能做到什么、不能做到什么的实证边界。

这比“把 future cap 从 7 改成 1 后快了多少”更有方法学价值，也能解释为什么 V6 在
46-source development history 上有 43%-53% 收益，却在最新 116-source MAB context 2
只有 13.77%：复用机制相同，但 speculation-to-suffix interference 和 adaptive work path
不同。V6.1 的目标正是让这种差异变得可控、可测和可复现。
