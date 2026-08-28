# MemBind V6.1 8B 双副本 Autoresearch 执行计划

> **立即纠偏（2026-08-28）**：本文早期版本曾把 `NATIVE_PARALLEL/B1` 当作 Native
> headline，这一解释已废止。唯一正式 headline comparator 是严格按
> episode/source 顺序完成 stateful update 与 durable publication 的
> `B0/NATIVE_SERIAL`（`native-serial-dual`），并与 V6.1 共享同一双 GPU、模型、
> Embedding、workload、cache 和 decoding contract。V6.1 的加速估计固定为
> `T(B0_NATIVE_SERIAL) / T(V6_1_B0_PRESERVING)`；V6.1 只可提前执行 certified
> dependency-free PREPARE/replay 子操作，不能改变 B0 state evolution 或 publication
> order。`NATIVE_PARALLEL/B1` 允许完整 episode 并发且可能改变状态演化，统一重命名为
> `RELAXED_ORDER_B1_UPPER_BOUND`，只做辅助性能上界分析，绝不作为 headline 或主表
> Native。历史 attempt/JSON 不删除、不改写，仅在新 contract 与 correction report 中
> 重新归类。

> 权威版本：2026-08-28  
> 工作目录：`/data/predator/ly/MemBind`  
> 实验身份：`local-qwen3-8b-awq-dualreplica-v1`  
> 最终目标：在公平、可复现、质量不退化的协议下，使 V6.1 相比 B0/NATIVE_SERIAL
> 资源匹配 Native baseline 获得稳定收益；随后启动五个完整 history 的正式实验队列，并在
> 观察到持续进展、证据落盘和服务稳定后停止本轮执行。

## 0.1 用户修订版推进规则（2026-08-28）

本节优先级高于本文后续较早版本中的“必须完成三次 prefix-30 才能解锁”条款。用户
明确要求以 **Native 相对加速** 作为主要决策依据，避免在已有明显方法收益后继续重复
昂贵的中间实验。

1. Headline 指标固定为同一 `context/prefix/workload/platform` 下的
   `speedup = T(B0_NATIVE_SERIAL) / T(V6_1_B0_PRESERVING)`。`1.30x` 是“明显收益”的工作阈值，等价于
   `T(V6_1) <= 0.769 * T(B0_NATIVE_SERIAL)`；最终论文仍报告五 history 的完整分布，
   不用单次最快值替代统计结果。
2. Native、V6.1 必须共享模型、两个 LLM endpoint、Embedding、Neo4j、cache reset/warmup、
   workload 和公共 extraction policy。只改 V6.1 私有方法代码时，已冻结的 Native
   comparator 直接复用，不重复运行；平台/公共栈变化才同时重跑 Native。
3. 只保留三项轻量保护：同一逻辑工作合同、无明显质量崩溃（QA 通过且核心实体/边覆盖
   不低于 Native 的 95%）、construction/route/replay/shadow-write proof 通过。详细
   queue、token、temporal 和 grounding 继续记录用于论文与异常定位，但不再构成无限期
   晋级 gate。
4. 当前已有 fresh prefix-30 B0 comparator（`d6e9e240c3ce`, `2636.463018176s`）；
   首次 Core attempt `a2631b77f1e2` 被用户中止，仅完成 early prefix，不能计算正式 speedup。
   下一步在同一平台/workload 上重新运行一次 `MemBind-Core` prefix-30，并按
   `T_B0/T_MemBind-Core` 计算 speedup。若达到约 `1.30x`，或已显示清晰且可解释的同方向
   收益，即冻结当前候选并启动 full5，不再等待三次 prefix-30。若收益不足，则只做一个
   可证伪的方法代码改动后在同一尺度复验，禁止回到 W/F/Q 参数搜索或重复跑 Native。
5. full5 主表包含 `B0_NATIVE_SERIAL`、`STATIC_ROLE`、`V6_1`；
   `B1_RELAXED_ORDER_UPPER_BOUND` 仅列在 supplementary，上述每个 history 做三次
   repetition；它负责给出最终统计显著性，不再被用来阻止一个已经达到 1.3x 的候选进入
   正式实验。所有失败 attempt 继续 append-only 保存，并由 supervisor 用新 namespace
   恢复。

## 0. 执行原则

### 0.1 Research question and method boundary (frozen)

`MemBind-Core` answers one narrowly testable question:

> 在不改变 Native 原本计算语义、应执行工作量、state evolution 或 durable publication
> order 的前提下，仅把 certified dependency-free PREPARE 子操作提前，与当前
> authoritative NATIVE 工作重叠，并通过 exact replay 和 ordered publication 改变执行时机，
> 能否相对 B0/NATIVE_SERIAL 加速？

Core 允许的机制只有 dependency-aware prepare/execution overlap、exact certified replay
以及 ordered authoritative publication。`summary bypass`、`predicate pushdown`、
grounded/deterministic materialization，以及任何减少、替换或改变 Native 原生 provider
工作量的逻辑，全部标记为 `WORK_REDUCTION_EXTENSION`，必须独立运行、独立归因、独立
报告，不能进入 MemBind-Core headline 或与 Core 共用 speedup 结论。任何 Core run contract
缺少 method-boundary 字段，或宣称 work reduction 仍属于 Core，fairness checker 必须 fail closed。

### 0.2 Immediate experiment hold

在完成本次 baseline/contract 纠偏并补跑 fresh B0 之前，暂停所有针对 B1 的 live
autoresearch，包括 scheduler、lane、future-cap、borrow、spillover 或其他并发参数探索。
不得为了追求 `1.3x vs NATIVE_PARALLEL` 继续修改方法。B1 仅保留已有 artifact 作为
performance ceiling；后续 live 工作只能服务于 B0 anchor 与 MemBind-Core 的同语义比较。

本计划替代旧 14B workplan 中的 W/F/Q 参数搜索路线。Autoresearch 优化对象是
V6.1 的执行图、复用协议、调度器和工程实现，不通过给 V6.1 私有地缩短输出、减少
抽取、降低质量或增加硬件来制造速度收益。

执行遵循以下闭环：

```text
观察 artifact/trace/图质量
  -> 提出一个可证伪的系统假设
  -> provider-free TDD
  -> prefix-2/4 smoke
  -> 读取工作量、关键路径和质量证据
  -> 接受、撤回或修改该设计
  -> prefix-8/16/30 晋级
```

不会因为单个候选变慢、一次 localhost 网络失败或服务重启而停止。候选失败会保留
证据并进入诊断/恢复；只有正确性已满足的候选才允许进行性能比较。不会为追求正数
跳过最强 baseline、复用不匹配的 Native 或选择多次运行中的最快一次。

## 1. 冻结的实验平台

每条实验、测试、恢复和监控命令先执行：

```bash
cd /data/predator/ly/MemBind
source /data/predator/ly/MemBind/scripts/local_runtime_8b_dual/activate.sh
```

平台合同：

| 组件 | 固定配置 |
| --- | --- |
| Native replica | GPU 0，Qwen3-8B-AWQ，`18200/v1`，manifest-observed KV 106608 tokens |
| Prepare replica | GPU 1，Qwen3-8B-AWQ，`18201/v1`，KV 72208 tokens |
| Embedding | GPU 1，Qwen3-Embedding-0.6B BF16，`18202/v1`，1024d |
| LLM runtime | 64K、YaRN 1.6、8 sequences、8K batch、xgrammar、prefix cache、chunked prefill、FCFS |
| Decoding | thinking off、temperature 0、top-p 1、seed 20260806、SDK retry 0 |
| Graph | 同一 Neo4j 实例；每个 attempt 使用 fresh 8B-only namespace |
| Client | HTTP timeout 3600s，`GRAPHITI_MAX_COROUTINES=8` |

正式 attempt 必须绑定当前 immutable platform manifest 及 payload hash。模型、
Embedding、vLLM 配置、显存 reservation 或公共 Graphiti 语义发生变化时，旧结果自动
失去复用资格。GPU 1 与 Embedding 共置以及两个 replica 的 KV 不对称必须在论文中
报告，不能隐藏。

## 2. 方法与公平主比较

| Arm | 资源 | 执行语义 | Router 可见信息 | 作用 |
| --- | --- | --- | --- | --- |
| `B0_NATIVE_SERIAL` | 两个 LLM endpoint | 严格 B0 episode/source 串行 stateful update 与 durable publication | phase-blind capacity-aware | 唯一正式 headline Native baseline |
| `B1_RELAXED_ORDER_UPPER_BOUND` | 同两个 endpoint | B1 完整 episode 并行，可能改变 state evolution | phase-blind capacity-aware | relaxed-order 性能上界，仅辅助 |
| `STATIC_ROLE` | 同两个 endpoint | B0 串行、无 replay | Graphiti request class | 隔离简单静态角色分流 |
| `V6_1` | 同两个 endpoint | dual streaming + authoritative replay | semantic phase | headline candidate |
| single-GPU pair | 同一 GPU 0 endpoint | Native/V6.1 各一组 | 对应单端点策略 | 拆分算法收益与双副本收益 |

Headline 结论必须以 `B0_NATIVE_SERIAL` 对 `V6_1` 为主，并同时给出 `STATIC_ROLE`。
`B1_RELAXED_ORDER_UPPER_BOUND` 只回答激进放松顺序后的性能上界。V6.1 可以因 exact certified
replay 少做重复的外部 provider 工作，这是方法贡献；但必须证明逻辑抽取语义相同、
唯一权威写路径、无 replay transport、无 shadow DB write 和质量不退化。

## 3. 当前诊断起点（prefix-2，非正式结果）

| Arm | Makespan | LLM logical / transport | Prompt tokens | Graph |
| --- | ---: | ---: | ---: | --- |
| B0 Native Serial | 146.153s | 36 / 135 | 728,972 | 85 entities / 51 edges |
| B1 Native Parallel (upper bound) | 64.642s | 6 / 99 | 472,043 | 86 entities / 45 edges |
| StaticRole | 143.121s | 36 / 135 | 729,017 | 85 entities / 51 edges |
| V6.1 | 102.249s | 36 / 135 | 728,941 | 85 entities / 51 edges |

这是 `phasea-p2-shared-20260828-r1` 的单次四臂 shared-stack smoke。每个 measured arm
前已对两个 endpoint 对称 reset prefix cache，并执行相同、不计时的 structured warmup；
平台 manifest、attempt preparation、route seal 和 construction seal 均已落盘。结果只
用于定位问题，不能进入论文主表，因为只有一次 repetition，公共 extraction policy
尚未冻结，而且 B1 Native Parallel 的工作量/语义与另外三臂不同。

当前已经确认：

1. V6.1 相对 B0 Native Serial 快 30.0%，相对 StaticRole 快 28.6%，证明 dual-streaming
   overlap 与 exact replay 在同语义工作下有效；B1 仅显示 relaxed-order ceiling，不能作为
   headline 负结果。
2. Native Serial、StaticRole、V6.1 的 51 条 canonical edge 与 85 个实体 name/label
   集合完全一致；graph hash 差异来自 summary 等字段，不是核心边集合漂移。
3. provenance guard 已真实拒绝跨实体 candidate，最终图中 `Evernote -> Notion` 与
   `Spirit Airlines -> baggage restrictions` 均正确；仍需处理 summary batch 返回
   unknown entity 的告警，并把该行为纳入机器可读证据。
4. V6.1 的 4/4 certified replay、0 replay transport、0 shadow DB attempt 均通过；
   PREPARE/NATIVE 在两个 GPU 上发生了实际重叠。
5. 135 次 transport 中，边抽取占 88 次。当前 single-edge pagination 把每个新增边
   变成一个请求，并把全部历史边反复写入 prompt，是当前公共栈最大的系统瓶颈；
   Native 的 `transport_retry_attempts=99` 实为 expansion continuation，不能作为真实
   网络重试报告。

## 4. Phase A：公共语义与测量闭环

这一阶段允许修改所有方法共享的 8B compatibility layer 和 instrumentation。修改后
现有 Native 结果全部失效；在公共栈冻结前不反复跑正式 Native。

1. 为错误实体绑定建立最小回归测试，定位 extraction、node dedupe/resolution 或导出
   映射中的责任边界；修复 invalid ID、unknown entity 和跨 source 错绑，禁止通过
   删除合法边或收紧任意全局 `maxItems` 掩盖问题。
2. 使 serial、parallel、static-role、V6.1 都记录同一套工作量：logical wrapper calls、
   physical transport、failed transport、true retry、compatibility expansion、pagination
   page/continuation、embedding items、DB read/write、dedupe/summary calls及 token 数。
   `attempt_index > 0` 不得再被直接解释为网络 retry。
3. run contract 必须 hash runner、`runtime.py`、`runtime_8b.py`、`routing.py`、scheduler/
   replay 模块、Graphiti package/source 和公共 extraction policy；依赖变化时禁止复用。
4. 加入 measured-attempt cache 协议：正式 repetition 前重启两个 LLM replica，并执行
   同一个不计时 structured warmup；记录 restart、READY、warmup 和温度/功耗状态。
   Embedding 是否重启保持两方法对称。
5. `bounded-delta-cap8-v3` 与 `bounded-delta-cap2-v4` 已证明 8B 模型在多边页上会产生
   关系改写、端点漂移及 downstream work amplification。当前候选恢复 single-edge
   cardinality，保留 unique-delta、empty/zero-delta fixed point、endpoint provenance
   和有界页数；不再把增大 `maxItems` 作为优化方向。
6. `single-edge-partition-pipeline-v5` 只并行互相独立的 turn-local partition；每个
   partition 内仍按 page 0 -> page 1 -> fixed point 串行。所有 logical edge call 共享
   一个容量为 4 的 physical-page semaphore，使用固定大小 worker pool，按 partition ID
   确定性归并，并记录 queue/service time、峰值 active pages、per-partition page count 和
   merge order。名称兼容守卫同时覆盖确定性 fuzzy promotion 与 LLM promotion。
7. 用 provider-free 测试覆盖上述行为，运行完整 V6.1 测试集；真实 prefix-2 先只跑
   V6.1 candidate，验证无错绑、核心实体/边不退化、无下游工作放大且 makespan 明显低于
   102.249s；通过后因公共 timed path 已变化，必须重跑 fresh 四臂 shared-stack smoke。

Phase A 完成条件很直接：没有已知跨 source 错绑或未解释的 summary 丢失；四组工作量
字段均可信；任何代码变化都能使不匹配 baseline 复用失败；冷启动/warmup 有机器可读
证据；multi-edge 与 single-edge 诊断在核心实体/边或预先声明的质量 tolerance 内等价，
且显著减少 transport/page/token 放大。若仍有质量错误，继续修复，不能以性能为由晋级。

## 5. Phase B：冻结公共栈与参考 baseline

公共栈通过 Phase A 后写 immutable common-stack manifest。之后：

1. 在 history 0 的固定 prefix 上生成严格 B0/NATIVE_SERIAL、B1 upper-bound 和 StaticRole
   reference；同一公共栈、fresh namespace、相同 cold/warm protocol。
2. 每个 reference 至少做 3 次 independent repetition；采用预先固定的平衡顺序，
   不选最快值。Autoresearch 使用 median reference 和 paired observation。
3. reference 只在公共栈或平台合同变化时失效。V6.1 私有 executor/scheduler 修改不会
   触发 Native 重跑。
4. 输出 graph/QA reference、工作量分解和每 source durable publication timing。

复用资格由 common-stack hash 决定，而不是由方法名称决定。公共 extraction、Graphiti
compatibility、instrumentation（若影响 timed path）、模型服务或 cache protocol 任一
改变，所有 arm reference 同时失效；只改 V6.1 私有 scheduler/executor/proof materializer
且 dependency manifest 证明 Native timed path 未变时，Native/StaticRole reference 才可
复用。复用必须在新 candidate 的 manifest 中列出原 attempt、hash 和 eligibility proof。

先在 prefix-2/4 校准流程，再生成 prefix-8/16/30 reference。避免在尚未验证的长尺度
上浪费 Native 运行。

## 6. Phase C：V6.1 系统 Autoresearch

递进尺度为 `2 -> 4 -> 8 -> 16 -> 30`。每轮读取最近 artifact 后才决定下一步，不能
因为代码能跑就机械扩展。

每个候选必须写：

```text
hypothesis
code/dependency hash
scale/history/repetition
critical-path phase timing
logical/transport/token/embedding/DB work
route/replay/shadow-write proof
graph diff and QA/semantic score
accept/reject reason
next hypothesis
```

研究优先级：

1. 先在 Core 边界内消除相对 B0 的非必要串行关键路径和 Prepare/NATIVE 空洞；任何
   summary/predicate/grounding/materialization work reduction 仅作为独立 extension 分支，
   不得混入 Core 结果。
2. 在不改变逻辑工作和 publication order 的前提下，使 PREPARE(i+1) 与 NATIVE(i)
   真正跨副本重叠；优化 phase handoff、backpressure、streaming frontier 和请求编排。
3. 根据逐 GPU queue、TTFT/TPOT、KV 和 embedding interference 选择调度设计；只有 trace
   证明 resource limit 是瓶颈时才改变公共平台，而且一旦改变所有方法一起重跑。
4. 保留 exact replay、单权威写路径和 source order。任何 speculative work amplification
   必须被收益覆盖并完整报告。
5. 负结果撤回对应候选代码或隔离为 ablation，artifact 永久保留。

轻量晋级规则：

- prefix-2/4：只判断正确性、证据完整与是否存在明显死锁/工作放大；性能只作诊断。
- prefix-8/16：至少两次方向一致，graph/QA 不劣于 frozen Native tolerance；分析关键路径
  后决定扩展或改设计。
- prefix-30：至少 3 次 paired repetition；V6.1 median makespan 必须优于
   StaticRole；相对 B0/NATIVE_SERIAL 的 headline 目标是获得稳定正收益。B1 只报告
   relaxed-order ceiling ratio，禁止把 B1 与 B0 混成同一 estimand，或把少做工作解释为调度优势。
   95% paired bootstrap CI 不支持明显回退，TTFP/P95
  不出现数量级退化，proof 全 PASS。

如果某一尺度没有达标，继续“诊断 -> 单一主要改动 -> TDD -> 同尺度复验”，而不是
直接扩大。若候选连续显示最强 baseline 的优势来自工作量/语义漂移，先回到 Phase A
修公共栈，不在 V6.1 中私自适配。

## 7. Phase D：五个 history 正式设计

只有 prefix-30 通过后才冻结 selected V6.1 code/policy 和正式 protocol。主表至少包含：

```text
B0_NATIVE_SERIAL
STATIC_ROLE
V6_1
```

`B1_RELAXED_ORDER_UPPER_BOUND` 可作为 supplementary ceiling。每个方法、每个 history 至少
3 次 repetition；正式队列按 repetition 分层使用 Latin-square/平衡顺序，示例：

```text
r0 h0: NativeParallel -> StaticRole -> V6.1
r0 h1: V6.1 -> NativeParallel -> StaticRole
r0 h2: StaticRole -> V6.1 -> NativeParallel
r0 h3: NativeParallel -> V6.1 -> StaticRole
r0 h4: StaticRole -> NativeParallel -> V6.1
```

后续 repetition 循环旋转起始方法。每个 timed block 前执行相同 restart/warmup；同一
时刻只运行一个 campaign block，避免 15 个实验互相争抢两个 LLM endpoint。五个
history 全部预先写入 append-only queue 和 manifest，supervisor 自动恢复可恢复的
localhost/service/GPU 故障；失败 attempt 保留并创建 fresh retry，不能覆盖。

主表报告：

- construction makespan、durable goodput、paired speedup、mean/median/std、95% CI；
- TTFP、per-source p50/p95/p99、frontier wait；
- logical/transport/retry/token、embedding/DB work、work amplification；
- 每卡 utilization、memory/KV、queue/active sequences、Embedding 干扰；
- route/replay/shadow-write/seal proof，graph diff 和下游 QA；
- OOM、timeout、reset、cancel 与所有失败 attempt。

## 8. 故障恢复

遇到资源或网络问题按以下顺序自动处理：

1. 读取 READY、tmux、PID、端口、`/v1/models`、最新日志、`nvidia-smi` 和磁盘/inode。
2. 若只是控制端代理，使用 activation 后的 localhost direct probe；不访问外部 API。
3. 若服务不存在或不健康，停止本 profile 的残留进程，运行
   `scripts/local_runtime_8b_dual/start_all.sh`，等待新 READY，并验证 manifest 身份。
4. 若 OOM/磁盘满/主机重启，标记当前 timing invalid，保留 artifact；修复基础设施后
   用 fresh attempt/namespace 重跑，不从未认证的 timed midpoint 拼接结果。
5. 单个服务恢复最多执行有日志的有限重试；研究循环同时继续 provider-free 分析，
   不因一次 live failure 停止。

## 9. 本轮停止条件

本轮不能在“代码实现完成”或“prefix-2 看起来更快”时停止。只有以下条件全部满足才
结束主动执行：

1. 公共正确性、工作量计数、依赖身份和 cache 协议完成并通过测试；
2. prefix-30 严格 B0/NATIVE_SERIAL comparator 已完成；MemBind-Core 需在该语义下完成有效计时并达到性能和质量晋级条件；
3. selected V6.1 与 5-history protocol/queue/manifest 已冻结；
4. 五个 history 均已进入持久正式队列，并且每个 history 都至少有一个正式 attempt
   实际进入 `RUNNING` 或 `COMPLETE`，不能以只写 queue/manifest 代替启动；
5. 五个 history 全部实际启动后继续观察至少 10 分钟和 3 个 supervisor heartbeat，确认
   frontier/provider counters 推进、服务 READY、GPU 无 OOM/reset storm、artifact 持续
   落盘、失败 attempt 可由 supervisor 以 fresh namespace 接续；
6. 交付当前运行位置、已完成/待完成 blocks、监控命令、所有关键 hash 与已知风险。

如果在充分 autoresearch 后 `MemBind-Core` 仍无法公平超过 B0 Native Serial，不允许伪造“良好
效果”或换弱 baseline。应保留负结果，并将 work-reduction extensions 单独作为 ablation
报告；只有用户明确改变研究目标，
或出现无法由本机恢复且需要外部输入的硬阻塞，才提前停止。

### 9.1 当前起点与不可重复工作

本轮从 `fair-p30-three-arm-20260828-r45a` 接续。旧 `NATIVE_PARALLEL/20597f72b70f`
（`696.445710877s`）已降级为 B1 relaxed-order upper bound，不能作为 Native headline。
fresh B0 comparator 已由 `d6e9e240c3ce` 完成（`2636.463018176s`，30/30 publication，
order proof PASS）。首次 Core attempt `a2631b77f1e2` 因用户中止仅作 partial engineering
evidence；下一步需 fresh Core prefix-30 有效计时。B0 comparator 仅在 Native timed-path
dependency 或平台合同变化时失效。

当前 V6.1 comparator 为 r44a/b/c 三次独立 prefix-30：`826.224/829.137/824.525s`，
中位数 `826.224s`。它们是质量 substrate，不再作为性能合格候选。r45a 已完整收尾：
StaticRole `3417.042s`，fresh V6.1 `850.263s`；该轮只能说明 V6.1 相对 StaticRole
的差异，不能给出相对 B0 的 headline speedup。三臂均 seal PASS；旧候选失败后不启动 r45b/r45c。

### 9.2 单候选执行事务

每个 autoresearch 候选严格按以下事务推进，并把每一步写入 append-only ledger：

```text
DESIGN
  -> RED/GREEN provider-free tests
  -> full V6.1 suite
  -> prefix-8 smoke
  -> prefix-16 confirmation（仅当 prefix-8 正确且方向有利）
  -> prefix-30 x3（仅当 prefix-16 两次方向一致）
  -> ACCEPT / REJECT / REDESIGN
```

`DESIGN` 必须写唯一主要机制、预期改变的 trace counter、必须保持不变的语义，以及可
证伪条件。测试失败优先修实现；live 结果无进展则读 critical path、route outstanding、
page queue、tokens、GPU 与 graph diff 后重设假设，不能机械扩量。localhost/服务故障使
当前 timing invalid，但不等于候选失败；恢复平台后用 fresh namespace 重试。质量、seal、
workload identity 或 replay proof 失败则候选不能晋级，其他缺失的诊断字段记录为工程债并
尽快补齐，不设计会让研究循环永久卡住的复杂 gate。

### 9.3 v40 的因果隔离与路由修正

平台 manifest 的事实是两个 LLM 都为 `max_num_seqs=8`，GPU0/GPU1 observed KV 分别为
`106608/72208` tokens；当前 policy 却使用单一硬编码 `65968`，并同时存在固定
`NATIVE_DECODE_LANES=2` 与 `EDGE_PHYSICAL_PAGE_LANES_8B=2`。不能同时放开两个 gate，
否则 live 收益无法归因。

第一阶段 `v40a` 只删除 Native-side 固定二 lane 条件。Native request 仍必须同时满足
authenticated `CapacityAuthority=8` 与保守的 weighted token budget；第九个小请求必须
等待，token budget 先耗尽时必须更早等待，release/cancel/error 后 sequence permit 与 token
全部归零。provider proof 改为证明 `max_native_outstanding <= authenticated capacity`，不再
把人为 decode lane 当第三个资源维度。edge physical page gate、future cap、lookahead、
source priority、summary、route、publication order 和 extraction 全部保持不变。

`v40a` 的 prefix-8 live 已完成，结果为 `215.684522576s`，未优于冻结 r42a/r42b 的
`212.520080127/214.493002757s`。它确实把 provider queue 从约 `269--271s` 降到
`208.6s`，但 GPU0 最大 dispatch 从 3 增至 9、GPU1 仅为 2，provider service 增至
`444.0s`，dedupe-edge service 从约 `27.5--28.9s` 增至 `74.7s`。工作量仍为
104 logical / 386 transports / 250 pages / 230 deltas，图、QA、grounding、proof 和
0 retry/reset 均未退化。因此 `v40a` 判定为
`REJECTED_SERVICE_DILATION_RETAIN_SUBSTRATE`：统一 capacity/token admission substrate
保留，但不能直接放开 edge physical page gate。

`v40b` 只改变 V6.1 的物理 routing，不改变 admission、edge physical page gate、future/
lookahead、source priority、summary、extraction、exact replay 或 publication order。新策略为
`semantic_phase_capacity_balanced_affinity`：每个 phase 的首个请求仍选择 preferred endpoint；
preferred 已占用后，按 `(outstanding + 1) / manifest_capacity_weight` 选择 projected load
较低的 endpoint，相等时由当前 phase 的 preferred endpoint 确定性胜出。route event 必须封存
完整 capacity weights、selection outstanding、preferred endpoint 与选择原因，proof 能逐次
重演并拒绝 endpoint/reason/weight 篡改，取消/错误后两 endpoint counter 必须归零。

这不是 W/F/Q 或经验阈值搜索：capacity weight 来自冻结 platform manifest 的 observed KV
capacity，唯一可证伪假设是“消除 v40a 的 GPU0 burst pile-up 能保留 queue 降幅并恢复 service
time”。prefix-8 若未明确优于 r42a/r42b，或 104/386/250/230 work、图/QA/proof 发生退化，
则不扩 prefix-16，而是读取 per-endpoint service/queue 与 phase critical path 后重新设计。
冻结 Native comparator 使用 `c58a480...`；当前 candidate runtime 使用更新后的
`2e910b00d070...` manifest。两者必须通过机器可读 resource-identity diff 证明硬件、模型、
端口、服务参数和 Embedding 身份不变；candidate route 作为 V6.1 方法合同写入
campaign/run contract 并单独 hash，不因方法私有变化重跑已冻结 Native。

### 9.4 v41a 之后的结果驱动执行闭环

每个新候选严格沿 `离线 RED/GREEN -> 全量 provider-free 回归 -> fresh prefix-8 discovery ->
证据审计 -> fresh prefix-8 confirmation -> prefix-16 两次 -> prefix-30 三次` 推进，但不把阶段
数量本身设计成死锁 gate。每次 live 完成后先形成一页机器可复算的 decision record，至少回答：

1. makespan 改变来自 queue、service、critical source 还是 work count，是否与该候选的唯一机制一致；
2. logical/transport/token/page/delta、dedupe bypass 和两端 route 是否存在隐性工作放大或资源倾斜；
3. graph stable core、QA/gold rank、node/unit grounding、temporal true-update 和所有工程 proof 是否保持；
4. 相比最近两次可复现的 selected prefix，收益是否大于运行抖动；若不大于，则保留为消融并回到
   trace 设计下一项方法优化，不用增加并发、放宽质量或挑最快样本来掩盖失败。

`v41a` 的唯一变量是 acceptance-aware predicate pushdown，物理 routing 恢复冻结的
`semantic_phase_elastic_affinity`。首轮 prefix-8 只有在 dedupe logical calls/transport/service
按离线审计显著下降、总 makespan 明确优于 r42a/r42b、298-entity/178-edge stable core 与所有
质量/proof 合同通过时才重复确认；否则立即停止该候选扩量并分析剩余 critical path。第二次
prefix-8 与第一次机制和结果一致后才进入两次 prefix-16；prefix-16 若任一次回退到 r43 区间、
出现 work amplification 或质量退化，则仍回到 autoresearch，不自动进入 prefix-30。

`v41d/r52a` 已完成这个闭环中的首轮 discovery，结论固定为
`CORRECTNESS_PASS_PERFORMANCE_INSUFFICIENT`。attempt `abc0f18318ee` 的 makespan 为
`207.733707935s`，相对 r42a/r42b 只快 `2.25%/3.15%`，没有越过 prefix-8 性能晋级线。
它把 dedupe edge logical calls 从 79 降到 16、dedupe queue 从 104.15s 降到 3.99s，
但 `extract_edges` 仍有 250 次 transport，service 从 r42a 的 217.24s 增至 227.84s；八个
source 的 preparation interval 也没有系统性缩短。因此 v41 predicate pushdown 与 temporal
transaction 作为正确性/工作消除 substrate 保留，不为 r52a 增加 confirmation，也不扩
prefix-16。

r52a 的 correctness 不是近似结论：它记录 5 个 reused-resolved snapshots 和 1 次真实 rollback，
10,000-mile balance 恢复为 `invalid_at=null`；最终为 298 entities / 178 edges / 5 invalid
temporal rows / 0 null-valid。Quality-v1 episode/gold ranks、298/298 entity 与 621/621 unit
grounding、construction/order/request/replay/shared-arbiter/provider/route/shadow-DB proofs 均
PASS。图不声称 byte-exact：`FLYING_FROM` 变为等价的 `FLY_FROM`，一个冗余 Boston 事实缺失，
并新增一条有原文依据的 Delta SkyMiles 事实。

下一候选只能针对 extraction critical path。设计前必须先按真实 `route_events.jsonl` schema
重建 endpoint/phase/partition/page 的 queue、service 与 outstanding 时间线，再区分三类原因：
页内 fixed-point 数据依赖、跨 partition/source admission 空洞、provider service dilation。
候选只能改变其中一个机制；prompt/schema、page/delta 工作、publication order、global physical
cap、route replay 和最终语义均冻结。若 prefix-8 不能同时保持 r52a 的 correctness substrate 且
明显快于 r42a/r42b，则保留负证据并继续重设假设，不以去掉 dedupe 工作作为端到端成功。

本轮 trace 审计已经识别出第一个单机制候选
`deterministic-node-partition-pipeline-v42a`。r52a 的 48 个
`extract_nodes.extract_message` transports 在每个 source 内严格串行，单 source active 始终为
1；但全局 node transport 已自然达到 2。八个 source 的 node logical service 分别为
`12.79/12.90/17.61/25.31/7.18/16.09/6.68/15.41s`，并在 source 2/5/7 edge 开始前造成
`2.49/9.06/7.50s` 的无 edge transport 空洞。v42a 不提高已观察到的 global node physical
cap=2，而是让同一 source 的独立 turn partitions 在另一 node lane 空闲时并行；responses、entity
provenance hints 和 merge 必须按 partition ID 确定性提交。edge fixed-point、edge gate=2、
future/lookahead、routing、prompt/schema、transport/token/page/delta 和 publication order全部不变。

v42a 的 RED 必须覆盖乱序完成仍按 partition ID 归并、同名 entity provenance 排序稳定、global
node active 不超过 2、异常时取消 sibling 且 permit 全释放。GREEN 后完整 provider-free suite 必须
通过。首个 live prefix-8 的可证伪目标是 48 个 node transports 与 response identity 不变，node
logical wall time和上述 19.1s 空洞可解释下降，298 entities/178-edge semantic surface、r52a
temporal rollback substrate、Quality-v1/grounding/proofs 不退化；若 service dilation 抵消收益，
立即拒绝而不搜索 concurrency 数值。

### 9.5 B0 Native headline gate（纠偏）

当前没有 prefix-30 B0 comparator。此前冻结的 `NATIVE_PARALLEL/20597f72b70f`
（`696.445710877s`）现重新归类为 `B1_RELAXED_ORDER_UPPER_BOUND`，只作辅助上界，
不能用于 headline speedup。必须先在当前 8B 双 GPU profile 上完成一次严格
`B0/NATIVE_SERIAL` (`native-serial-dual`) prefix-30 freeze；之后 V6.1 必须在同一
workload/platform/cache protocol 下 fresh 运行，并按 B0-preserving contract 比较。

1. 优先在 prefix-16 做 B0 Native/V6.1 两次 fresh 配对；若已有同尺度 B0 结果，直接计算
   `speedup = T(B0_NATIVE_SERIAL) / T(V6_1_B0_PRESERVING)`。两次中位 speedup 达到约 `1.30x`（允许记录实际值和区间）
   即视为明显方法收益。
2. 若 prefix-16 尚未达到 1.30x，但 prefix-30 单次结果已明显优于 Native 且关键路径变化
   可解释，也允许冻结候选并启动 full5；不再要求机械完成三次 prefix-30。
3. 解锁只保留轻量保护：相同 workload/platform/cache protocol；QA 不出现明显崩溃、核心
   entity/edge coverage 不低于 Native 的 95%；construction、route、replay、shadow-write
   proof 通过。token、temporal、grounding、per-source timing 仍完整落盘，作为论文分析和
   异常定位依据，而不是阻塞研究循环的额外 gate。
4. 若 speedup 不足，继续一次“单一方法代码改动 -> provider-free tests -> 同尺度复验”；
   不重复生成已冻结 Native，不搜索 W/F/Q，不通过减少逻辑工作制造速度。

full5 解锁后仍使用 `B0_NATIVE_SERIAL`、`STATIC_ROLE`、`V6_1` 三臂、五 history、三
repetition，最终以 paired distribution 和置信区间给出正式结论。full5 是最终统计验证，
不是阻止明显候选进入实验的前置门槛。

### 9.6 full5 启动与稳定观察

解锁后先生成不可变 selected-method manifest，再一次性写入 `5 histories x 3 repetitions x
3 arms = 45 blocks` 的 append-only queue。每个 repetition 内采用预先固化的平衡顺序；同一
时刻只能有一个 measured block。supervisor 状态机固定为
`QUEUED -> PREPARING -> RUNNING -> COMPLETE/FAILED_RETRYABLE/FAILED_FINAL`，retry 必须新建
attempt/namespace，绝不覆盖旧 artifact。

为同时满足资源互斥与“五个 history 都实际启动”，队列在第一轮先对 h0-h4 各执行一个
最小正式 block，再继续剩余 blocks；这里的“启动”以 attempt preparation 已落盘且 measured
construction 实际进入 `RUNNING` 为准。五个 history 达到该状态后继续观察至少 10 分钟和
至少 3 个持久 heartbeat。每个 heartbeat 写当前 block、history 覆盖、durable frontier、
artifact mtime/size、三服务 health、GPU utilization/memory、provider/route counters、失败与
fresh retry。三次 heartbeat 均显示进展或可解释的长调用，且无 OOM/reset storm 后，本轮才
允许停止主动监控，supervisor 必须留在可恢复的 tmux 会话中继续执行完整 45-block 队列。

## 10. 状态账本

| 阶段 | 状态 | 当前证据/下一步 |
| --- | --- | --- |
| 平台 | `READY` | 8B 双副本 + Embedding 服务健康；冻结 Native manifest 为 `platform_manifest.20260827T210605Z.c58a48071201.json`（payload `c58a4807120189913c3450bccd54cdf75c865b0fb71c7e419656d7c686fafc1f`）；当前 candidate manifest 为 `platform_manifest.20260828T050058Z.2e910b00d070.json`（payload `2e910b00d070a56b90a8f302a786d1b55dccf3cd6cd475ae2cb4e05c1a6f8f90`）；不重跑 Native |
| prefix-2 诊断 | `COMPLETE_NONFORMAL` | 新四臂结果已封存；V6.1 同语义收益 28.6%-30.0% |
| Phase A | `PREFIX4_CORRECTNESS_PASS` | source-grounded nodes 和 current-evidence certified extraction 已通过 live：r26 最终 188 个实体均来自 Native 的 grounded entity 集；92 条边全部有合法 `valid_at`，proof/replay/shadow DB 全 PASS |
| Phase B | `PREFIX4_REFERENCE_COMPLETE_B0_B1_STATIC` | r27 的 Native Parallel 118.809s 现归类为 B1 upper-bound；StaticRole 456.186s，双臂 route/construction seal PASS。正式 headline 仍需严格 B0/NATIVE_SERIAL prefix-30 freeze。 |
| Phase C | `B0_COMPARATOR_REQUIRED_BEFORE_HEADLINE` | 旧 B1 `20597f72b70f` prefix-30 `696.445710877s` 仅作 relaxed-order ceiling；V6.1 `705.136007872s` 不能计算 B0 headline speedup。下一步先完成当前 profile 的 fresh B0 prefix-30，再运行同尺度 V6.1。 |
| Phase D/full5 | `UNLOCK_ON_B0_SPEEDUP` | B0 comparator 与 V6.1 同尺度、同 contract；达到约 `1.30x`（或清晰可解释收益）且轻量质量/proof 通过后，主表使用 B0_NATIVE_SERIAL/STATIC_ROLE/V6_1，B1 仅 supplementary。 |

### 10.1 本次续执行事务（2026-08-28，Native 冻结修订）

1. 保留被中断的 `phasec-p16-v42a-node-partition-pipeline-20260828-r54b`，不复用其
   namespace 或中间 timing。
2. `NATIVE_PARALLEL/20597f72b70f`（`696.445710877s`）保留为 B1 relaxed-order
   upper-bound；此前 r55a prefix-16 Native 仅作 append-only 辅助记录，排除出主表。
3. 先以 `native-serial-dual` 在 fresh namespace 完成严格 B0 prefix-30 freeze；再运行
   V6.1 fresh prefix-30，并按 `T(B0_NATIVE_SERIAL)/T(V6_1_B0_PRESERVING)` 计算 headline。
   未达到目标时只提出一个方法代码假设，继续同尺度验证，不重跑已冻结 B0。

## 11. Autoresearch 候选账本

| 候选 | 状态 | Prefix-2 结果 | 决策 |
| --- | --- | --- | --- |
| `single-edge-v2` | diagnostic reference | V6.1 102.249s，135 transports，51 edges | 保留为语义参考，不作为最终公共栈 |
| `bounded-delta-cap8-v3` | `REJECTED` | 227.210s，197 transports，122 logical calls，180 edges | page 虽降至 64，但产生 246 delta edges，dedupe 从 29 放大到 113；丢失 reference 19 edges，新增 148，拒绝扩展 |
| `bounded-delta-cap2-v4` | `REJECTED` | 123.337s，128 transports，70 entities / 61 edges | 比 cap8 收敛，但相对 single-edge 丢失 36 条 reference edges 并产生跨实体错绑，拒绝 |
| `single-edge-partition-pipeline-v5-c4` | `REJECTED` | 90.804s，120 transports，86 entities / 40 edges | 峰值 4 个 page 并发带来 11.2% 速度收益，但漏掉 SkyMiles/Spirit/酒店等显式事实，拒绝按规模扩展 |
| `single-edge-partition-pipeline-v5-c1` | `REJECTED_DIAGNOSTIC` | 114.093s，120 transports，86 entities / 40 edges | 与 c4 的 49 delta/40-edge core 完全一致但慢 25.6%；排除 partition 并发是质量漂移根因 |
| `single-edge-exact-prompt-pipeline-v6-c4` | `REJECTED` | 82.586s，106 transports，86 entities / 30 edges | wire prompt 指纹已恢复，但同一 logical call 的 4-way partition fan-out 使 fixed-point 提前收敛 |
| `single-edge-exact-prompt-v7-w1g2` | `REJECTED_DIAGNOSTIC` | 97.174s，106 transports，86 entities / 30 edges | 与 v6 edge core 完全一致；旧拓扑仍未恢复旧 completion，排除 worker fan-out 根因 |
| `single-edge-wire-schema-v8` | `REJECTED_DIAGNOSTIC` | 97.089s，106 transports，86 entities / 30 edges | 恢复 schema identity 后 core 仍不变，排除 schema title 是主因 |
| `single-edge-direct-loop-v9` | `REJECTED_DIAGNOSTIC` | 97.160s，106 transports，86 entities / 30 edges | 与 v6-v8 相同 30-edge core；排除额外 task yield 是质量漂移主因 |
| `single-edge-raw-progress-endpoint-guard-v10` | `REJECTED_DIAGNOSTIC` | 98.454s，110 transports，66 pages，86 entities / 30 edges | 首次无效端点确实多推进 4 pages，但未恢复任何最终边；证明它只是次要截断因素 |
| `single-edge-bounded-duplicate-recovery-v11` | `REJECTED_ENGINEERING` | 运行 566.771s 后 JSONDecodeError；summary 生成约 152KB 未闭合 JSON | recovery 恢复更多 edge 并把 dedupe calls 提升至 33，但 Graphiti 无界 summary schema 造成下游 runaway，结果未封存成功 |
| `single-edge-recovery-bounded-summaries-v12` | `REJECTED_QUALITY_AND_PERFORMANCE` | 170.603s，199 transports，148 pages，71 raw unique / 65 accepted deltas，86 entities / 51 edges，proof PASS | summary bound 修复了 v11 的 152KB runaway，但 recovery 把 prompt work 放大到 1,154,816 tokens；与旧 51-edge reference 仅重合 22 条，并产生 Citi card→Spirit baggage/travel 等跨主题错绑，不能以 edge count 晋级 |
| `evidence-aligned-turn-local-single-edge-v13` | `REJECTED_RECALL_RETAIN_SUBSTRATE` | 102.022s，116 transports，72 pages，44 raw unique / 40 accepted deltas，86 entities / 30 edges，proof PASS | 跨 topic 错绑已消失，prompt tokens 从 v12 1,154,816 降至 441,709；但核心 user facts、Airbnb/VRBO 与酒店事实仍缺失，和旧 reference 仅 exact-overlap 3，不能晋级；保留 evidence-local substrate |
| `evidence-aligned-one-recovery-per-window-v14` | `REJECTED_COST_PER_FACT` | 130.229s，161 transports，114 pages，60 raw unique / 50 accepted deltas，86 entities / 38 edges；26 recovery 中仅 7 成功 | 比 r13 多 45 transports / 28.2s 只换来 8 条 final edges，且仍缺 SkyMiles balance、Spirit 行程、Airbnb/VRBO 与酒店事实；bounded 但成本过高 |
| `evidence-local-minimum-multi-edge-delta-v15` | `PROMISING_NOT_QUALITY_PASS` | 116.223s，122 transports，68 pages，74 raw unique / 59 accepted deltas，86 entities / 51 edges，proof PASS | 相比 v14 更快且恢复 USER→Miami/Citi/Boston、Spirit baggage/FLL 与酒店实体，但仍缺 10,000 balance、USER→Spirit 行程、Airbnb/VRBO；大量通用 ASSISTANT discourse edge 和同义 USES 抢占有限 delta，保留 cap=2 substrate |
| `memory-utility-ordered-evidence-cap2-v16` | `REJECTED_PROMPT_ONLY_CONTROL` | 124.804s，123 transports，72 pages，84 raw unique / 59 accepted deltas，86 entities / 49 edges；invalid endpoints 36 | 找回 10,000 balance，但出现 `USER has plan to redeem Citi card` 等错误 attribution，且 generic discourse 仍占多数；说明文字优先级不能可靠控制 8B structured decode |
| `actor-domain-evidence-cover-cap2-v17` | `QUALITY_DIRECTION_PASS_COST_FAIL` | 142.743s，168 transports，108 pages，115 raw unique / 99 accepted deltas，86 entities / 71 edges | 首次同时恢复 10,000 balance、USER→Spirit/FLL、Boston/Miami 与多条酒店→Miami；但 provenance-only domain entities 造成 `book journal→Airbnb/VRBO` 跨 episode 错边，且 dedupe/DB work 过高，保留结构方向 |
| `lexically-grounded-actor-domain-cap2-v18` | `QUALITY_PASS_COST_FAIL` | 144.173s，163 transports，105 pages，112 raw unique / 95 accepted deltas，86 entities / 70 edges | 删除了 book-journal→Airbnb/VRBO 错边并保留全部核心 USER/酒店事实；但 work 未降。分解显示 adjacent-domain 33 pages 对应 31 delta、23 duplicate、11 invalid，而 adjacent-user 26 pages 对应 26 delta、5 duplicate、0 invalid |
| `marginal-work-pruned-actor-domain-cap2-v19` | `PERFORMANCE_PASS_DOMAIN_RECALL_FAIL` | 119.093s，114 transports，72 pages，74 raw unique / 64 accepted deltas，86 entities / 47 edges | 相比 v18 少 25.1s/49 transports/33 pages，核心 USER facts 全保留且无跨 episode 错绑；但酒店→Miami 边随 adjacent-domain 丢失，说明仍需边界 join 而非完整删除 |
| `semi-naive-grounded-boundary-join-cap2-v20` | `REJECTED_JOIN_AMPLIFICATION` | 142.736s，161 transports，104 pages，108 raw unique / 84 accepted deltas，86 entities / 64 edges；23 non-boundary outputs 被拒 | 大窗口仍反复输出分区内边，后置 cross filter 未恢复酒店关系却恢复了 v18 的工作放大，拒绝 |
| `grounded-base-domain-adjacent-user-cap2-v19-selected` | `SELECTED_FOR_SCALE_VALIDATION` | 复用 r19 已封存证据，不重复 V6.1 prefix-2 | 选择依据是所有正确候选中的最低 work：核心 USER facts、Spirit/FLL、lexical grounding、proof/replay/shadow DB 均通过；domain recall 风险在 prefix-4+ graph/QA gate 继续验证 |
| `bounded-node-extraction-schema-v21` | `ENGINEERING_FIX_QUALITY_FAIL` | r23 成功完成：355.958s，340 transports，1,748,134 prompt tokens，183 entities / 126 edges，proof PASS | schema bound 消除了 length/JSON 工程失败，但 source 3 候选达到 125，future source 竞争与 downstream edge/dedupe/summary work 严重放大，不能晋级 |
| `source-grounded-bounded-nodes-v22` | `CORRECTNESS_PASS_PERFORMANCE_INSUFFICIENT` | r24 339.480s，316 transports，1,564,237 prompt tokens，150 entities / 120 edges；29 audits 共拒绝 317 ungrounded + 57 duplicate，最终 150/150 实体均可在输入全文精确落证，proof PASS | 相比 r23 减少 16.478s、24 transports、183,897 prompt tokens、33 final entities，说明根因成立；但 source 2 work 未降且总成本仍不可扩展，保留 grounding 并继续系统优化 |
| `work-conserving-dual-route-ablation-v23` | `REJECTED_PHASE_BLIND_CONTENTION` | r25 331.361s，312 transports，1,546,261 prompt tokens，141 entities / 118 edges，proof PASS；仅比 r24 快 2.39% | source 0 wait 80.184→56.791s，但 source 2/3 wait 增至 59.057/113.057s，phase-blind 竞争吃回收益且 graph 随 dynamic batching 漂移；保留负结果，不作为 selected route |
| `current-evidence-certified-extraction-v24` | `PERFORMANCE_PASS_QUALITY_AUDIT` | r26 228.178s，254 transports，89 logical requests，528,936 prompt / 31,136 completion tokens，188 entities / 92 edges，0 true retry；相对 r24 makespan -32.8%、prompt work -66.2%、transport -19.6% | 8 capture + 8 replay context-selection events 共删去 187,518 chars，8/8 replay request identity exact match；edge page/delta 为 144/127，与旧 Native 一致。旧 Native 的 203 个 unique entity 中 r26 覆盖 188 个且无 r26-only entity；92 条边中 91 条 exact match。差异审计确认两条看似缺失的 USER/NBA 与 scavenger-hunt 边在 r26 以更完整 paraphrase 存在，另两项是跨 episode 合并和 book journal 单复数归一化；全部核心事实保留，不需要 relevance-history fallback |
| `provenance-predicate-pushdown-v25` | `SELECTED_PREFIX4_HEADLINE_STILL_FAIL` | r28 202.746s，252 transports，87 logical requests，486,410 prompt tokens，187 unique entities / 92 edges；相对 r26 makespan -11.15%，node-dedupe service 63.125s -> 5.051s | 将现有 name-compatibility 最终验收谓词下推到 LLM prompt 前；2,235 个 retrieval candidates 中 2,231 个按同一谓词必拒，146 个实体直接保留为新实体，仅 4 个 candidate 进入 LLM。92/92 edges 与 r26 exact，91/93 与 r27 Native exact；8/8 replay、order、route、shadow DB 全 PASS。唯一移除的是无最终边引用的泛化 entity `reading`。保留该方法，但相对 Native 仍慢 70.65%，继续 phase-aware idle stealing，不进入 prefix-8 |
| `semantic-phase-elastic-affinity-v26` | `SELECTED_ROUTE_PROOF_PASS_HEADLINE_STILL_FAIL` | r29 181.166s，254 transports，89 logical requests，488,290 prompt tokens，187 unique entities / 94 edges；相对 r28 makespan -10.64%，相对 r26 -20.60% | 72 次 spill 全部满足现有 idle-alternate 选择谓词，GPU0/GPU1 分别承载 123/131 transports，route/replay/order/shadow DB proof 全 PASS；r29 完整包含 r28 的 92 条边并恢复 Native 已有的 NBA 与 scavenger-hunt 两条短 paraphrase，94/94 `valid_at` 合法。但 route trace 显示 GPU0 `outstanding_at_dispatch` 峰值为 2：当 preferred 与 alternate 同时繁忙时仍向 preferred 排队；同时 admission 以 `phase_isolated=true` 分池记账，与 elastic 跨 phase endpoint 使用并非同一个物理资源模型。保留 r29 为诊断 substrate，不进入 prefix-8 |
| `durable-frontier-active-permit-promotion-v27` | `SELECTED_PREFIX4_HEADLINE_STILL_FAIL` | r30 166.967s，252 transports，87 logical requests，486,408 prompt tokens，187 unique entities / 92 edges；相对 r29 makespan -7.84%，source3 frontier wait 44.654s -> 26.399s | source1/source2 发布时分别将 active ticket 6/22 从 future 原子晋升为 frontier，已发请求 0 cancel / 0 resubmit，provider proof v3 逐 ticket PASS。r30 的 92 条边全部与 r29 exact；r29-only 两条是已有长事实覆盖的 NBA/scavenger-hunt 短重复 paraphrase，187 个实体名完全一致，92/92 `valid_at` 合法。保留 promotion；相对旧 r27 Native 仍慢 40.53%，且正式 fresh baseline 尚待最新冻结栈重跑，不进入 prefix-8 |
| `bounded-bootstrap-future-borrow-v28` | `REJECTED_SERVICE_DILATION` | r31 169.118s，252 transports，87 logical requests，486,416 prompt tokens，187 unique entities / 92 edges；相对 r30 makespan +1.29% | bootstrap 借用把 source2 首个 permit queue wait 从 42.55s 降至约 50us，并使 source1/source2 在 source0 publication 前完成 PREPARE_READY；但 source0 PREPARE service 从 55.746s 膨胀到 73.819s，连续 batching 的三路竞争吃掉全部收益。92/92 edges 与 r30 exact，route/replay/order/shadow DB/seal/proof 全 PASS。保留可选实现与测试作为负消融，正式 V6.1 明确关闭 borrowing |
| `incremental-current-evidence-summary-v29` | `SELECTED_SYSTEM_DIRECTION_HEADLINE_STILL_FAIL` | r32 161.687s，254 transports，89 logical requests，452,638 prompt / 27,366 completion tokens，187 entities / 94 edges；相对 r30 makespan -3.16%、prompt work -6.94% | 6 个 Native summary flight 共移除 156,386 chars previous-history，保留 60,839 chars current evidence 和 1,438 chars existing durable summary；source3 prompt 从 15,232/15,757 tokens 降到 4,088/4,903，但两个 flight service 仍合计 15.607s。94-edge core 是 r30 的 92 条加 r29 已审计过的两条短 paraphrase；3 个 prefix-4 可判定 QA 的 episode rank 与 top facts 保持，8 个 blank-summary node 全部 edge-degree=0，route/replay/order/shadow DB/seal/proof PASS。保留方法方向，但相对旧 Native 仍慢 36.09%，不能进入 prefix-8 |
| `canonical-endpoint-candidate-filter-v30` | `REJECTED_TEMPORAL_QUALITY` | r33 157.588s，211 transports，46 logical requests，417,903 prompt / 26,363 completion tokens，187 entities / 92 edges；相对 r32 makespan -2.54% | 667 个 invalidation candidates 中过滤 454 个，并新增 41 次 LLM bypass，但删改 remaining prompt context 后，8B 将 source-1 的 Evernote/book-journal/Facebook 六条并存事实错误标为 source-3 时失效；6 条均无原文否定，r30/r32 均保持有效。性能收益不能覆盖时态语义错误，拒绝该 partial-filter 规则 |
| `all-or-nothing-disjoint-call-bypass-v31` | `REJECTED_TEMPORAL_QUALITY_RETAIN_SUBSTRATE` | r34 157.372s，211 transports，46 logical requests，418,703 prompt / 26,334 completion tokens，187 entities / 92 edges；41 次全不相交调用 bypass，0 个 non-bypass prompt 被删改 | 仍产生 8 条错误 invalidation，包括 `USER flies out of Boston`、Evernote/Notion 和 book-journal facts。说明即使 remaining prompt 原样不变，改变 continuous-batching 轨迹也足以改变 8B contradiction 输出；保留安全 bypass substrate，但没有独立时态验收前不得使用 |
| `deterministic-temporal-acceptance-v32` | `IN_TDD` | 目标保持 r34 的 41 次 bypass/约 157s 路径，同时把 8 条错误失效恢复为 0 | LLM contradiction 只作为 proposal；同 canonical endpoint pair 可接受，跨 target 仅在 relation 一致且新事实含显式 transition cue 时接受，其余恢复 candidate 原始 `invalid_at/expired_at`。evidence 记录 proposed/accepted/rejected 数量与理由，不保存事实正文；prefix-4 后还必须在 knowledge-update 样例验证 true update recall，不能只做“永不失效”的伪修复 |
| `deterministic-temporal-acceptance-v32` | `SELECTED_QUALITY_SUBSTRATE_HEADLINE_STILL_FAIL` | r35 159.369s，213 transports，48 logical requests，420,568 prompt / 26,610 completion tokens，187 entities / 94 edges | 8 个 proposals 中 2 个 endpoint 全不相交、6 个缺乏跨 pair transition 证据，全部回滚；最终 r32/r35 的 94 条完整 edge rows 与 187 entity names exact，0 null `valid_at`、0 invalid/expired。proof 全 PASS；相对 r32 -1.43%，但相对旧 r27 Native 仍 +34.14%，保留 guard 并继续关键路径优化 |
| `global-cap-preserving-cross-partition-pipeline-v33` | `IN_TDD` | r35 四个 edge logical calls 各有 14-17 个独立 evidence partitions，service 29.84-44.25s；当前单 logical call 最多只占一个 physical page lane | partition 内 fixed-point 串行、global physical cap=2 和 page capacity=2 全不变；只允许一个 source 在另一路空闲时并行推进第二个独立 partition，按 partition ID 确定性归并。晋级要求 peak physical pages 不超过 2、page/delta 工作无放大、94-edge/187-entity/时态/QA/proof 不劣，且 source0 或 tail edge service 有可解释下降 |
| `global-cap-preserving-cross-partition-pipeline-v33` | `SELECTED_PERFORMANCE_DIRECTION` | r36 137.033s，211 transports，46 logical requests，418,713 prompt / 26,334 completion tokens；相对 r35 makespan -14.0% | page=144、delta=127、duplicate=45、invalid endpoint=31 与 substrate 同量级且无放大；四个 calls 均 workers=2/global cap=2。source2/source3 edge service 从 44.25/39.41s 降至 29.01/21.66s。187 entity names 不变，92-edge core 只少两条已有完整长事实覆盖的短 paraphrase，0 时态错误，proof PASS。保留 pipeline，但 headline 仍 +15.3% |
| `durable-frontier-source-priority-page-gate-v34` | `IN_TDD` | r36 source0 PREPARE 仍为 52.44s，source1 反而先 ready；69.03s page queue wait 表明 global FIFO 在 frontier 与 future partitions 间分配 lane | capacity/page work/partition workers 全不变；page admission 按最小 active source sequence、同 source ticket FIFO，deferred drain 允许下一 fixed-point page 重新入队后再选择。必须证明 capacity 守恒、取消安全、无 future starvation、source0 ready 提前且总 makespan下降；若 source1/2 延迟吃回收益则拒绝 |
| `durable-frontier-source-priority-page-gate-v34` | `SELECTED_PERFORMANCE_DIRECTION_HEADLINE_STILL_FAIL` | r37 130.486s，211 transports，46 logical requests，418,700 prompt / 26,334 completion tokens；相对 r36 -4.78%、相对 r35 -18.12% | source0 edge service 23.81s、PREPARE ready 36.46s，证明 durable-frontier priority 有效；但 source1/2/3 frontier bubbles 为 7.31/21.68/11.16s，source1 page queue wait 累计 43.83s，source2 ticket 2 等待 46.259s。r36/r37 完整 edge rows、entity names 和 work inventory exact，request identity/replay/route/order/shadow DB/seal 全 PASS。保留 priority substrate，但严格最小 source 策略存在 starvation，不能扩展 prefix |
| `bounded-aging-page-admission-v35` | `REJECTED_SERVICE_DILATION_RETAIN_ABLATION` | r38 135.477s，211 transports，144 pages/127 deltas，187 entities / 92 edges；相对 r37 makespan +3.83% | 18 次 bounded-aging grants 将 source1 frontier bubble 从 7.31s 降至近零，但 source0 PREPARE 从 36.46s 膨胀到 46.27s、source2 bubble 从 21.68s 增至 25.24s；source0 page queue wait 新增 19.09s。r37/r38 entity/edge rows exact，0 temporal 错误，proof/seal PASS。保留可选 gate 与 TDD 作为负消融，active 8B policy 恢复 strict source priority，不再搜索 burst 参数 |
| `provenance-grounded-summary-materialization-v37` | `TDD_PASS_PREFIX4_LIVE_PENDING` | r37 的 6 次 `extract_nodes.extract_summaries_batch` 累计约 44.863s service；其中 118/187 entities 为 degree-0，但仍有 115 个生成式 summary | 不删除 summary，也不把 benchmark 不消费 node summary 当作等价证明。V6.1-only patch 以 current edge facts、current episode 中 entity-name exact mention span、进程内已认证 prior units 确定性物化；逐 unit 只封存 source/span hash、字符数和选择/丢弃原因。`skip_fact_appending=True` 保守回到 upstream，filter=false 不改节点，依赖 seam 漂移 fail-closed，close 恢复。完整 V6.1 suite `109 passed`；prefix-4 要求 6→0 summary LLM、低于 r37 且目标低于 118.809s，同时 entity/edge/temporal/node-surface/grounding/proof 全不劣 |
| `provenance-grounded-summary-materialization-v37` | `PERFORMANCE_PASS_COMPACTNESS_FAIL` | r39 111.769s，205 transports，40 logical requests，390,844 prompt / 21,855 completion tokens；相对 r37 -14.34%，相对旧诊断 Native -5.93% | 6→0 summary LLM、144 pages/127 deltas、92 edge rows exact、0 temporal/null-valid 错误、Quality-v1 与全部 proof PASS。188 entities 比 r37 多 source-grounded degree-0 `reading`；主要拒绝扩量原因是 summary 54,973 chars、249/505 selected unit uses 重复同一 span。保留正性能 artifact `878c456331f3`，进入 v2 最小句物化，不以 r39 直接扩 prefix |
| `minimal-sentence-grounded-summary-v38` | `IN_TDD` | r39 的 current episode units 平均 172 chars，102/279 超过 200 chars；短整行策略会把多个无关句复制到同一实体 summary | 只改变 span selection：无论行长都优先取 mention 所在最小句，每实体每 current episode 至多一个 span；edge facts、prior-certified units、1000-char cap、调度/route/work 全冻结。新增 canonical-name hash 供 final graph 逐节点对证；prefix-4 要求保持 r39 的 6→0 与 headline 性能，同时显著降低 total chars/重复，edge/temporal/Quality-v1/proof 不劣 |
| `minimal-sentence-grounded-summary-v38` | `SELECTED_WITH_ENGINEERING_FOLLOWUP` | r40 111.396s，205 transports，40 logical requests，390,833 prompt tokens；summary 40,495 chars，430 units，203 duplicate source/span uses | 相对 r39 summary chars -26.3%、units -14.9%、duplicate uses -18.5%，性能同速；r37/r40 Quality-v1 三题全 exact，node name embedding 六查询全 top-3。仅缺 r37 的 degree-0 alias `tbr list`，但 `tbr lists`/`tbr` rank 1/2 且 92 edges exact，判定为 dedupe 而非 recall loss。先修长窗口 word boundary，再冻结 prefix-4 |
| `word-boundary-grounded-span-v39` | `IN_TDD` | node probe 对 430/430 units 来源验证 PASS，但人工审计发现长句 bounded window 可能从单词中间开始，例如 `tbr lists` 摘要开头为 `nd ratings` | 只在超过 320 chars 时将 window start/end 向内对齐空白边界；仍为原文 exact substring，不改变 entity-specific sentence、单位上限或优先级。必须保持 r40 work、性能和全部质量/proof，并消除 partial-word 开头 |
| `word-boundary-grounded-span-v39` | `SELECTED_PREFIX4_FROZEN` | r41 111.593s，205 transports，40 logical，390,829 prompt tokens；186 entities / 92 edges，summary 40,370 chars | 与 r40 entity/edge/work exact，summary 少 125 chars；Quality-v1、node embedding top-3、430/430 unit grounding、117/117 degree-0 span、temporal/request/replay/route/order/shadow DB/seal 全 PASS。冻结该版本进入 prefix-8，不再做 summary 微调 |
| `word-boundary-grounded-span-v39` | `PREFIX8_SCALE_PASS` | r42a/r42b 212.520/214.493s，均为 298 entities / 178 edges、386 transports、104 logical、73,622 summary chars | 两次 fresh repetition 差 0.93%，图实体与 summary exact；6 条 temporal update 仅 `expired_at` 随 attempt wall-clock 变化，fact/relation/endpoints/valid/invalid semantics exact。Quality-v1 的 episode/gold ranks exact，node-surface 298/298 nodes、621/621 units、178/178 degree-0 nodes grounded，所有工程 proof PASS。授权扩至 prefix-16，不授权 full5 |
| `word-boundary-grounded-span-v39` | `PREFIX16_SCALE_PASS` | r43a/r43b 493.011/487.832s；259/254 logical、882/877 transports、621/622 entities、430/425 edges | 性能差 1.05%，pages=555、invalid endpoints=116、最终 7 条 temporal semantics exact；稳定边交集 421，少量受原文支持的生成式 extraction/paraphrase 差异如实保留，未伪报 exact。Quality-v1 7/7 episode/gold ranks、全部 node/unit grounding与工程 proof PASS。授权进行 prefix-30 三次重复，不授权 full5 或 headline speedup |
| `word-boundary-grounded-span-v39` | `PREFIX30_CANDIDATE_FROZEN` | r44a/b/c 826.224/829.137/824.525s；448/450/452 logical、1512/1514/1519 transports、987/982/987 entities、724/726/727 edges | 速度范围仅 0.56%，三方稳定 core 972 entities/706 edges，7 条 temporal rows exact；14/14 QA episode/gold ranks、所有 final node/unit grounding与工程 proof PASS。冻结为公平三臂候选；是否具有 MemBind-Core headline 价值必须由 fresh B0 Native Serial 对照决定，当前结果本身不作为 speedup 声明 |
| `manifest-capacity-unified-physical-admission-v40a` | `REJECTED_SERVICE_DILATION_RETAIN_SUBSTRATE` | r46a prefix-8 `215.684522576s`；work=104 logical/386 transports/250 pages/230 deltas，298 entities/178 edges，6 temporal rows/0 null-valid，QA/grounding/proof PASS、0 retry/reset | provider queue 从约 269--271s 降至 208.6s，但 provider service 增至 444.0s、dedupe-edge service 增至 74.7s；GPU0/GPU1 max dispatch=9/2。capacity/token substrate 保留，性能候选拒绝，不扩 prefix-16 |
| `semantic-phase-capacity-balanced-routing-v40b` | `REJECTED_NO_CRITICAL_PATH_GAIN` | r47a `214.526059635s`；route 193/193、max dispatch 6/4，queue 197.31s、service 418.15s，source7 durable 218.74s；104 logical/386 transports/250 pages/230 deltas、0 retry/reset。Quality-v1 episode/gold ranks exact，298 entity surface exact、grounding PASS；177 edges，少一条 Delta SkyMiles fact | 相对 r46a 只快 0.54%，未优于 r42a/r42b；按 physical request 数量均衡忽略 logical expansion locality 和 token/service cost，不能扩 prefix-16。保留 route proof substrate，进入 logical-call-affine token-debt routing |
| `logical-call-affine-token-debt-routing-v40c` | `REJECTED_SERVICE_LOCALITY_MISMATCH` | r48a `239.612384223s`，104 logical groups/208 group events/386 transports proof PASS 且 counters 全归零；queue/service 216.36/460.19s，edge service 276.86s，route 185/201、max dispatch 6/4。Quality-v1 ranks、298 entity surface、grounding PASS，178 edges/6 temporal invalid/0 null-valid | 比 r42a 慢 12.75%；把整个长 edge logical call 固定到 GPU1 低 KV、Embedding 共置副本造成 service dilation。拒绝扩量，保留为 locality ablation |
| `acceptance-aware-edge-invalidation-pushdown-v41a` | `REJECTED_TEMPORAL_SELF_INVALIDATION_RETAIN_SUBSTRATE` | r49a `208.180326708s`；41 logical / 323 transports / 250 pages / 230 deltas，140 predicate bypass，queue/service 163.18/372.56s；Quality-v1、298 entities、621 grounding units 和全部 proofs PASS，但 177 edges 且一条 10,000-mile balance 被错误失效 | 比 r42a 快 2.04%，工作消除明显但 headline gain 尚小；错误来自 Graphiti 将同一 edge 同时报为 duplicate/contradiction，same-pair guard 允许 resolved edge 自我失效。保留 pushdown substrate，不重复挑样本 |
| `self-invalidation-state-invariant-v41b` | `PREFIX8_LIVE_DISCOVERY` | RED/GREEN 与完整 116-test suite PASS；object/UUID self-invalidation 被拒并恢复原 invalid/expired，distinct same-pair true update 和 explicit transition 单测保持 | 图状态机不允许边使自身失效；GPU 冷却与 live preflight 完成，r50a fresh prefix-8 仍只运行 V6.1 |
| `self-invalidation-state-invariant-v41b` | `RETAINED_INVARIANT_LIVE_SHAPE_MISS` | r50a `208.927352831s`，41 logical/323 transports/250 pages/230 deltas、178 edges、0 retry，性能/工作与 r49a 复现；但 7 proposals 全按 same pair 接受且 balance 仍失效 | Graphiti 两条 retrieval path 返回不同 object/UUID，identity guard 未命中；不变量本身正确并保留，但不能单独解决 live bug，不扩量 |
| `idempotent-duplicate-invalidation-guard-v41c` | `PREFIX8_LIVE_DISCOVERY` | RED/GREEN 与完整 117-test suite PASS；不同 hydration copy 的 exact normalized fact 不得互相失效，snapshot 回滚；same-pair 非相同 fact 与 explicit transition 保持 | 跨 retrieval hydration 的语义 identity invariant，不依赖运行 ID 或数据文本；r51a fresh prefix-8 只运行 V6.1，验证 live balance 与 reason audit |
| `idempotent-duplicate-invalidation-guard-v41c` | `RETAINED_INVARIANT_LIVE_PATH_MISS` | r51a `205.512886378s`，41 logical/323 transports/250 pages/230 deltas、178 edges、0 retry；balance 仍失效且 idempotent rejection 为 0 | resolved edge 的 temporal side effect 发生在 Graphiti 返回 invalidation list 之前且该分支返回空 list，fact guard 没有观察对象；规则保留，但不能单独解决 live bug |
| `reused-resolved-edge-temporal-transaction-v41d` | `CORRECTNESS_PASS_PERFORMANCE_INSUFFICIENT` | r52a `207.733707935s`；41 logical/323 transports、707,939 prompt/39,704 completion tokens、250 pages/230 deltas、140 predicate bypass；5 snapshots/1 rollback，298 entities/178 edges/5 invalid temporal rows/0 null-valid | balance、Quality-v1、298/298 entity 和 621/621 unit grounding、全部 proof PASS；相对 r42a/r42b 仅快 2.25%/3.15%。dedupe 79->16 logical 但 extraction 250 transports 仍主导，保留 correctness substrate，拒绝扩量并转向 extraction critical path |
| `deterministic-node-partition-pipeline-v42a` | `PREFIX8_CONFIRMATION_PASS` | r53a/r53b `188.947/190.406s`，相差 0.77%；41 logical、321/323 transports、48 node transports、248/250 pages、node shared max=2、0 retry，node logical service 84.10/84.60s。相对 r42a/r42b 快 10.41%--11.91%，收益由 node 串行链缩短与 edge 零活跃空洞下降解释 | r53a/r53b 为 297/174、298/179，稳定 core 297/174；两次 5 invalid/0 null-valid、1 temporal rollback，Quality-v1 episode/gold ranks 与全部 node/unit grounding PASS，全部工程 proof/seal PASS。`team`/重复 holding、错绑 LOCATED_IN、等价 relation label 和有替代表达的 upgrade 差异如实保留。授权同代码 prefix-16 x2，不授权 prefix-30 |
| `native-prefix16-calibration-r55a` | `AUXILIARY_CALIBRATION_EXCLUDED_FROM_HEADLINE` | Native prefix-16 仅用于尺度校准，非正式 comparator | 不覆盖、不替代冻结的 Native prefix-30；不再重复运行 |
| `phasec-p30-v42a-native-frozen-r56a` | `FAILED_INFRASTRUCTURE_RETRYABLE` | preparation/基础设施中断，未形成可用 timing | 保留原始 namespace 与失败证据；不纳入性能比较，恢复服务后使用 fresh namespace |
| `deterministic-node-partition-pipeline-v42a-r56b` | `REJECTED_NATIVE_RELATIVE_HEADLINE` | V6.1 prefix-30 `724.819525646s`，PREPARE service `1125.33s`、queue `668.33s`，external PREPARE calls `1129`，max prepare outstanding `2`；冻结 Native `696.445710877s`，相对 `0.9609x` | construction/route/replay/request/shared-arbiter/shadow-DB/seal 全 PASS，质量工作量完整；瓶颈是 PREPARE 服务膨胀和阶段交接，不是 Native comparator 不稳定。禁止 full5，继续方法级优化 |
| `adaptive-future-window-v43b-r57a` | `REJECTED_SERVICE_DILATION` | prefix-8 `196.024717027s`；服务膨胀，graph edge drift | frontier/route/proof 证据保留；扩大 future admission 没有改善关键路径，拒绝重复该方向 |
| `edge-call-affinity-v44-r58a` | `REJECTED_ENGINEERING_ROUTE_PROOF` | construction 完成但 route sealing 失败，非性能结果 | 非法 route reason 已修复于 r58b；r58a timing 不纳入比较 |
| `edge-call-affinity-v44-r58b` | `REJECTED_SERVICE_LOCALITY_DILATION` | prefix-8 `215.758122336s`，相对 v42a prefix-8 慢约 13%--15%；queue `172.06s`、service `390.01s`，proof/seal 全 PASS | 固定 edge continuation endpoint 在 GPU1/embedding 共置环境下放大服务时间；不重复运行该候选，代码作为 rejected ablation 保留 |
| `per-transport-token-debt-routing-v45` | `REJECTED_NO_HEADLINE_GAIN_RETAIN_ROUTE_PROOF` | r59a prefix-8 `193.413633819s`，332 transports、738,803 prompt / 41,157 completion tokens、259 pages / 246 deltas，297 entities / 199 edges；route/provider/replay/request/shadow-DB/construction proof 与 297/297 node grounding 全 PASS | 该实现按每次物理 transport 的 active admitted token debt 路由，continuation 可独立 spill，不是已拒绝的 logical-call 固定路由。相对 v42a r53a/r53b 慢 2.36%/1.58%；PREPARE node queue `152.831s`、edge service `241.535s`，且比 r53a 多 11 transports、34,817 prompt tokens、1,955 completion tokens。Quality-v1 三题 episode/gold ranks exact，但只与 r53a exact-overlap 166/174 edges，新增 33、丢失 8，新增多为 Chase/篮球细粒度重复关系。拒绝扩至 prefix-16/30，保留 route proof 作为负消融 |
| `unified-physical-request-admission-v36` | `BACKUP_RESEARCH_DIRECTION` | 当前 logical future_cap 与跨 node/edge/dedupe/summary 的真实 physical transport 不同构，source2 node ticket 无法利用短暂空闲，但简单 borrowing 已在 r31 失败 | 仅在 bounded aging 未达到 headline 时进入；先建立所有 phase 的单一 physical-resource trace/replay 和 endpoint outstanding 守恒，再研究 bootstrap admission。首次 live 不与 aging 同时启用，确保负结果可归因 |

### r59a per-transport token-debt 结论与下一前沿

1. r59a 不是服务或网络异常。332/332 physical transports 均成功且有 usage/finish reason，
   0 retry、0 length finish；route runtime 的 outstanding 与 active dispatch token debt 全部归零，
   construction seal、route seal、replay/request identity、publication order、shared arbiter 和
   shadow DB proof 均通过。
2. token-debt 确实改变了物理分配：PREPARE 有 156 次流向 `native-replica`、151 次留在
   `prepare-replica`；NATIVE 有 8 次 spill 到 `prepare-replica`、17 次留在
   `native-replica`。但这没有降低关键路径。node extraction service 从 r53a 的
   `84.103s` 降至 `77.333s`，其 queue wait 却从 `145.984s` 增至 `152.831s`；edge
   extraction service 从 `230.019s` 增至 `241.535s`，最终 makespan 反而增加 `4.466s`。
3. 该路由还改变了 8B continuous-batching structured decoding 轨迹。r59a 比 r53a 多
   18 accepted deltas、11 transports 和 25 final edges；exact edge overlap 为 166/174。
   read-only Quality-v1 保持三题 episode/gold ranks，但 top facts 改变；node surface 仅在
   `team`、`ultimate rewards portal`、`points`、`travel insurance` 等生成实体上漂移，
   297/297 node grounding 仍 PASS。该候选因此既无性能收益，也不能宣称图完全等价。
4. 决策记录封存在
   `/data/predator/ly/Mem/experiments/local-qwen3-8b-awq-dualreplica-v1/v6_1_mab/analysis/autoresearch-decisions.jsonl/20260828-r59a-per-transport-token-debt.json`。
   后续禁止重复 edge-call affinity、logical-call-affine token debt、per-transport token debt、
   adaptive future window、bootstrap borrowing 或 bounded-aging burst 搜索。
5. 下一候选只允许处理一个系统缺口：当前 arbiter 在 logical wrapper 层计费，而 edge/node
   expansion 和 endpoint route 发生在 physical transport 层，二者对真实占用看见的是不同
   请求集合。先建立 provider-free 的 unified physical request admission trace 与守恒证明，
   不改变 prompt、schema、page cardinality、W/F/Q、publication order 或 route policy；只有
   离线证明 physical acquire/release 无双重计费、无泄漏且不降低 Native frontier 优先级后，
   才允许 fresh prefix-8。

### 10.2 r60 logical-source-lease + endpoint-aware physical admission（离线 GREEN）

本轮只改变 admission 的资源边界，不改变 prompt、schema、page cardinality、W/F/Q、
publication order、extraction 或 route policy。r59a 的根因假设被拆成两个显式对象：

1. `SourceLease`：一个 PREPARE source 从 `extract_nodes` 开始到 `extract_edges` 完成持有，
   只消耗 source capacity/future source cap，不消耗 provider slot 或 KV token；frontier
   推进可以把该 lease 从 `FUTURE_PREPARE` 原子重分类为 `FRONTIER_PREPARE`。
2. `PhysicalPermit`：每个真实 transport 独立按 prompt + decode reserve 获取/释放，严格
   计入 slot/KV；同一 source lease 可以展开多个 transport，不会把 `future_cap` 错当成
   transport 数上限。对 routed client，permit 在 endpoint 选择之后获取，并带上
   `resource_id`，因此 route 与 admission 可逐一对应。

实现位置：`membind_v6_1/admission.py`、`provider.py`、`routing.py`、`mab.py`；旧
`acquire()/release()` 保留用于历史兼容，新的 live 路径使用显式
`acquire_source_lease()/release_source_lease()` 与 `acquire_physical()/release_physical()`。
provider proof 新增 source lease identity/promotion/terminal 守恒和 physical future-inflight
分解，避免将 source cap 与 physical transport 数混为一谈。

离线 TDD 结果：

- 首轮 RED：新 source/physical API 不存在，测试按预期在收集阶段失败。
- GREEN：source lease 独立于 slot/KV；单 source lease 下两个 future physical transports
  可并行；frontier promotion 不丢失 permit；source/physical 事件身份和释放严格配对；
  routed endpoint failure 会释放 physical permit。
- 完整 provider-free suite：`126 passed, 1 warning`（2026-08-28）；scheduler `74/74`，
  routing/provider/runtime/MAB/summary/campaign 全部通过。

当前仍不宣称性能收益，也不启动 full5。下一步只允许在 fresh namespace 运行一次 prefix-8
V6.1，读取 endpoint-aware admission、source/physical critical path、work inventory、
Quality-v1、node surface 和全部 proof；若无 headline gain，保留该实现作为系统消融并继续
下一个单一方法假设，Native comparator 继续冻结不重跑。

### r37 结果与下一执行前沿

1. r37 的收益来自方法级调度，而不是放宽资源：global physical page cap、partition workers、
   route policy、模型、Embedding、prompt 与最终 work inventory 均未改变。source0 的 edge service
   从 r36 的 39.76s 降到 23.81s，总 makespan 从 137.033s 降到 130.486s。
2. r37 不是最终晋级结果。严格 source-order priority 把瓶颈从 source0 转移到尚未发布的
   future sources；source2 ticket 2 在约 0.19s 入队，却要等 source0 publication 后才获准，
   queue wait 46.259s。下一候选必须解决 starvation，而不是继续提高 source0 优先级。
3. 在任何新 live 之前执行两项独立 gate：使用 read-only guard 比较 r35/r36/r37 的
   Quality-v1 episode ranks/top facts；用 provider-free exact-pair 与显式 cross-target
   transition 测试验证 temporal guard 保留 true-update recall。前者不得构建或写 Neo4j，
   后者不得以“全部拒绝 invalidation”伪造安全性。
4. 每个新候选坚持一次只改变一个主要机制：先 trace replay/TDD，再 prefix-4 live，再逐项
   对比 makespan、source service/wait、transport/token、graph/temporal/QA/proof。只有当前
   尺度 headline、质量与工程 gate 同时通过，才进入 prefix-8；不能用旧 r27 Native 作为
   最终 speedup 声明，代码和公共平台冻结后必须 fresh 三臂。
5. r35/r36/r37 的 read-only Quality-v1 sidecar 已完成：三个 prefix-addressable 问题的
   episode rankings 与 post-hoc gold ranks 完全一致，分别为 `[1]`、`[1,2]`、`[1]`；
   construction calls=0、graph writes=0、driver init task 不存在，36 次 Neo4j 请求全部受
   read-only guard 约束。top-fact 差异仅由 r35 多出的已审计短 paraphrase
   `USER attended NBA game` 挤占尾部名额，完整事实仍存在。temporal true-update recall 的
   exact-pair 与显式 cross-target transition 测试均通过，JUnit 与 sidecar 结果已落盘。
6. r38 已证伪 page-level bounded aging：它只是把 source1 的等待转移给 source0，并造成
   3.83% 端到端回退。因此停止 burst 参数搜索。r39 每次只改变 summary materialization：
   r37 的 page admission、future cap、route、edge/node extraction 和 publication order 全部冻结。
7. r39 live 后除现有 Quality-v1 外必须执行 node-surface read-only probe：比较 name query 的
   top entities、问题相关实体 summary 可检索性、degree-0 mention-span 覆盖、每个 summary unit
   到 edge fact/episode span 的哈希映射，以及 source3 node-resolution candidate/result。任一
   graph 或 node 质量失败即保留负结果并恢复 r37，再进入 unified physical admission，不能扩量。

### r29 elastic route 审计与 r30 假设

`phasec-p4-elastic-affinity-v61-20260828-r29` 的结果排除了“只要给空闲副本
spillover 就能达到 headline 性能”的假设：

1. makespan 为 181.166s，较 r28 的 202.746s 降低 10.64%，说明跨 phase 的 idle
   stealing 有真实价值；但相对旧 r27 Native 的 118.809s 仍慢 52.48%。r27 只用于诊断，
   因 r28 node resolution 与 r29 routing/platform manifest 已变化，正式三臂必须在最新
   冻结栈上 fresh 重跑。
2. 254 次 transport 中有 72 次合法 spill：PREPARE -> GPU0 为 57 次，NATIVE -> GPU1
   为 15 次；route proof 可逐 dispatch 重演，未发现违反当前规则的 spill。GPU0/GPU1
   累计 service time 分别为 123.953s / 172.635s。
3. frontier wait 为 source0 55.649s、source1 近 0、source2 20.012s、source3 44.654s。
   但 admission queue 不是 source0 的直接根因：source0 两个 PREPARE logical calls 的
   queue wait 合计仅 0.019s，service 却为 55.628s。优化必须针对 physical dispatch 与
   logical admission 的一致资源模型，不能只让等待队列重排后宣称成功。
4. 当前 admission 已对排队中的 `NATIVE_FRONTIER / FRONTIER_PREPARE /
   FUTURE_PREPARE` 使用优先级；后到 frontier 可以超过尚未获 permit 的 future，同类保持
   source/ticket 顺序。r30 不重复实现已有 priority queue。
5. 对照 strict r28 后，`outstanding_at_dispatch=2` 不是 r29 新增的根因；r29 的跨卡分摊
   反而把 makespan 降低了 21.580s，因此不把 endpoint concurrency 武断收紧为 1。r30 的
   单一主假设改为 `durable-frontier active-permit promotion`：一个已获 permit 的
   `FUTURE_PREPARE(i)` 在 durable frontier 变成 `i-1` 后，原请求不取消、不重发，但其
   资源分类原子晋升为 `FRONTIER_PREPARE(i)`，立即释放一个 future quota 给 `i+1`。
   r29 中 source2 edge 在 source1 发布后仍错误占用 future quota 约 20s，直接阻塞了
   source3 node；这是固定分类造成的 head-of-line bubble，不是 W/F/Q 搜参。
6. r30 provider-free gate 覆盖：只晋升恰好成为新 frontier 的 active future；错误 source
   不晋升；晋升后下一个 future 可 admission；同一 permit 不重复晋升；已获请求不抢占；
   原 acquire handle 仍能正确 release；drain 后 counters、active permits 与 waiters 全为 0；
   proof 逐 ticket 验证 admit -> promotion -> release 的时序、source 与计数守恒。live gate
   同时要求 route、replay、shadow DB、publication order 和至少 r29 的 94-edge core。

### r30 结果与 r31 bootstrap 候选

1. r30 的两次 active promotion 将 source3 future admission 从 r29 的约 101.3s 提前到
   约 81.5s；source3 frontier wait 降低 18.255s，总 makespan 降低 14.199s。source3
   Native tail 同时从 21.066s 波动到 26.462s，因此总收益小于 bubble 降幅，不能只看
   admission 事件估算端到端收益。
2. 现有最大可行动空洞是 source2 首个 permit 的 42.541s queue wait。首个 Native 在
   source0 PREPARE_READY 之前根本不存在，此时用 future cap=1 保护 foreground 没有对象；
   source0+source1 两个 logical extraction 占用双卡时，source2 完全不参与 continuous
   batching，导致后续 source2/source3 分别仍有 18.781s/26.399s frontier wait。
3. r31 单一假设为 `bounded bootstrap future borrowing`：只在 durable frontier=-1、
   dual-streaming 且 native guard 尚未出现时，允许恰好一个额外 future permit；首个
   `FRONTIER_ADVANCE` 后关闭 borrowing，active permit 按 r30 规则晋升并恢复 cap=1。
   该规则是 foreground 不存在时的 work-conserving admission，不改变 CLI 的 lookahead/
   future-cap/quota，不扫描参数，也不允许 prefix-4 初始窗口之外的任务物化。
4. evidence/proof 必须标记每个 borrowed ticket，验证 borrow 仅发生在 frontier=-1、最多
   一个额外 future、无 native guard；首个 frontier 后不得新 borrow，terminal counters
   全 0。live 主要判据是 source2 admission wait 和 source2/3 frontier wait 同时下降，
   且 source0 service dilation 没有吃回收益；质量仍按 92-edge semantic core、187 entity
   names、0 null `valid_at`、8/8 replay 和全部 seal/proof 判定。

### r31 负结果与 r32 方法假设

1. r31 的局部调度目标成立，但端到端假设被证伪：bootstrap borrow count=3、最大 future
   outstanding=2、最大 prepare outstanding=3；source2 首个 permit 几乎零等待，source1/2
   也都在首个 publication 前 ready。与此同时 source0 PREPARE service 增加 18.073s，最终
   makespan 比 r30 慢 2.151s。这说明双卡 8B 平台在该阶段受 decode service dilation
   约束，继续放宽 admission 不是剩余关键路径的解法。
2. r31 的 187 个 entity name 与 r30 完全一致，92/92 edge exact，0 null/invalid
   `valid_at`，8/8 replay identity、route、publication order、shadow DB、construction seal
   和 provider proof v4 全 PASS。拒绝原因纯粹是端到端性能，而不是工程失败或质量波动；
   该负结果和 artifact `phasec-p4-bootstrap-future-borrow-v61-20260828-r31` 永久保留。
3. 正式 V6.1 恢复 r30：active-permit promotion 开启，`bootstrap_future_borrow=false`。
   borrowing 仅能由显式 ablation 构造 arbiter 时开启，不能再由 dual-streaming execution
   strategy 自动打开。
4. r32 的可证伪方法假设转向 `incremental current-evidence summary`。Graphiti 当前每轮
   summary 同时重放全部 previous episodes、current episode、existing summary 和 new edge
   facts；r30 source3 的 summary batch 是 Native tail 最大单项（2 calls、约 24.9s service、
   约 30,989 prompt tokens）。持久化 existing summary 已代表 prior durable state，故候选
   递推为 `S_i = summarize(S_{i-1}, current episode, new facts)`，不再每轮重复注入完整历史。
5. r32 先做 V6.1 私有、scope-aware 的 provider-free TDD，不修改所有 arms 共用的 Graphiti
   公共栈。evidence 必须逐 flight 封存 prior-summary chars/hash、current-episode hash、new-fact
   count/hash、removed previous-history chars/hash，并保证 capture/replay request identity exact。
   live 除 makespan 外必须比较 187 个 node summaries、92-edge semantic core、下游 graph/QA、
   route/order/shadow DB/seal；若 summary 质量或请求可重演性不成立，立即拒绝而不扩展 prefix。

### r32 结果与 r33 edge predicate pushdown

1. r32 证明历史重放是实在的冗余工作，但不是剩余主瓶颈。总 prompt tokens 从 r30 的
   486,408 降到 452,638，source3 两个 summary prompt 缩短约 69.8%，makespan 仅下降
   5.280s；source2 summary 虽从 12,169 降到 5,021 tokens，service 反而由 5.869s 波动到
   7.427s。continuous batching、输出 decode 和 concurrent route 仍决定实际 service。
2. r32 的 187 entity names 与 r30 一致，94 edges 完整包含 r30 的 92 条，新增项是已在 r29
   审计过的 `USER attended NBA game` 与 `USER plans to have the scavenger hunt at Staples
   Center`，均有更完整长事实覆盖。0 null/invalid `valid_at`，8/8 request/replay、两次 active
   promotion、route/order/shadow DB/seal 均 PASS。prefix-4 中三个存在 gold-session 交集的
   Quality-v1 read-only probes，r30/r32 episode ranking 完全相同；唯一 gold 全落在 prefix-4
   的问题仍将两个证据排在 rank 1/2。
3. node-summary exact text 大范围改变是预期的生成差异，不能伪装为等价。总 summary chars
   为 21,906（r30 为 22,097），blank nodes 从 14 降为 8；r32 的 8 个 blank node 均为
   edge-degree=0。USER summary 从 1,957 chars 原始 edge-fact concatenation 压缩为 685 chars，
   但全部 94 条事实边和原始 episodic evidence 仍封存。当前 benchmark 的 frozen Quality-v1
   retrieval 只使用 edges+episodes，不消费 node summary；因此将 r32 保留为系统方向，且在
   prefix-8+ 继续单独报告 node-summary 差分，不把 QA 不变误称为 summary 完全等价。
4. r33 转向 `canonical endpoint predicate pushdown`。r32 有 69 个
   `dedupe_edges.resolve_edge` calls：source1/2/3 分别 12/32/25，累计 service 约 23.09s，
   大量两-lane queue wait 处于 Native critical path。Graphiti 先完成 node resolution，再做
   edge duplicate/contradiction retrieval；related same-endpoint candidates 不变，global
   invalidation candidate 只有与新边至少共享一个 canonical endpoint 时才可能更新同一实体
   关系。无 endpoint 交集者在 LLM 前过滤；endpoint 缺失者保守保留。
5. r33 evidence 对每条 extracted edge 记录 related/candidate/retained/rejected/malformed 数量、
   endpoint-set hash 和 newly-enabled LLM bypass。provider-free TDD 必须覆盖 same-source、
   same-target、cross-endpoint、disjoint、malformed 和 restore；live gate 要求 dedupe logical
   calls 实际下降，r32 94-edge core/时态字段、QA ranks、request identity、route/order/shadow
   DB/seal 全部不劣。若候选几乎都有 endpoint overlap 或 graph diff 不可解释，则拒绝该谓词。

### r26 prefix-4 审计签字

`phasec-p4-current-evidence-v61-20260828-r26` 的 live 结果满足当前尺度的正确性和
性能 gate：

1. `context_selection.jsonl` 有 16 条 sealed evidence；capture 与 replay 对四个 source
   的 node/edge extraction 使用相同 transform，外部 replay transport 为 0，request
   identity 全部 exact match。
2. r26 对旧 Native 的 raw exact edge overlap 为 91/92。Native-only raw rows 中，
   `USER attended NBA game` 与 `USER plans to have the scavenger hunt at Staples Center`
   分别对应 r26 的更完整事实 `...an NBA game at Staples Center` 和
   `...a sports-themed scavenger hunt at Staples Center`，不是召回缺失。
3. `USER uses book journal` 被 V6.1 合并为 source `[1,3]`；`book journals` 到
   `book journal` 是实体单复数归一化。两项是 V6.1 顺序权威去重的预期效果，不能按
   Native 的未合并 raw row 数判为质量下降。
4. live 控制台出现两次非致命 `Error parsing valid_at date, skipping`，但 r26 最终
   92/92 edges 的 `valid_at` 均合法，`invalid_at/expired_at` 无异常；旧 r24 反而有 5 条
   `valid_at=null`。告警未污染最终图，prefix-8 继续把 null/parse count 作为 gate。
5. 当前结论只授权公平 baseline 复核，不直接授权 prefix-8。由于 node grounding 位于
   公共 compatibility layer，下一步必须 fresh 重跑 `B0_NATIVE_SERIAL` 和 `STATIC_ROLE`；
   旧 B1 Native 仅用于 semantic diff/upper-bound，不能用于新的 headline speedup。

### r27 公平 baseline 复核

`phasec-p4-fair-baselines-20260828-r27` 已用 fresh namespace 和强制 reference rerun
完成，两个 attempt 都通过 route seal、construction seal、0 transport failure 和相同
cache reset/warmup：

| Arm | Makespan | Logical / transport | Prompt tokens | Graph |
| --- | ---: | ---: | ---: | --- |
| `B1_RELAXED_ORDER_UPPER_BOUND` | 118.809s | 100 / 265 | 446,540 | 198 entities（189 unique）/ 93 edges |
| `STATIC_ROLE` | 456.186s | 158 / 339 | 1,661,061 | 175 unique entities / 131 edges |
| `V6_1` r26 | 228.178s | 89 / 254 | 528,936 | 188 unique entities / 92 edges |

解释边界：

1. r26 相对 StaticRole 的 49.98% makespan 收益证明 dual streaming、exact replay 和
   current-evidence extraction 的联合方法价值；两者使用相同物理资源，StaticRole 的
   456s 不是小显卡或单 GPU 结果。
2. 不能把全部 49.98% 写成调度收益。StaticRole 仍把 previous episodes 放入当前
   extraction，产生 160 edge pages、169 deltas、105 edge-dedupe、36 timestamp calls 和
   5 条 `valid_at=null`；r26 的 current-evidence policy 同时降低工作量并改善图约束。
3. 当前 Native 与 r26 的 edge semantic overlap 为 91/92；Native 93 条 edge 中另两条
   只是 `book journals` 单复数形式。Native 有 9 个重复 entity rows，r26 无重复。这说明
   Native 118.809s 的一部分优势来自并发 episode 对跨 episode node dedupe 的规避，不能
   在不报告图合并质量的情况下当作等工作量 oracle。
4. headline 仍按 Native 计：r26 慢 92.05%，所以当前不进入 prefix-8。trace 显示 r26
   173 个 PREPARE transports 全固定在 GPU1，81 个 NATIVE transports 全固定在 GPU0；
   3 次 node-dedupe prompt 为 13,325 / 19,322 / 25,050 tokens，service 共 63.125s。
   下一候选先做 provenance predicate pushdown：现有 name guard 最终必拒的 candidate
   在 LLM 前过滤并落盘 audit，不改变可接受 resolution 集合；live 后再判断是否需要
   phase-aware elastic spillover。

prefix-4 r23 的工作分解排除了“只是 GPU 不够快”的解释。四个 source 的 physical prompt
tokens 分别为 90,107 / 271,875 / 499,104 / 887,048，transport 为 43 / 71 / 100 / 126；
后两个 source 占总 prompt work 的 79.3%。source 3 进入 edge cover 前已有 125 个 distinct
entity candidates，产生 52 edge pages、46 次 edge dedupe 和 18 次 timestamp extraction。
最终 183 个实体中，22 个在四个输入 episode 全文均无规范化词面证据，包括 `gamecube`、
`cracked windshield`、`lockheed martin` 和 `wikipedia`。因此先修 node provenance，再看
episode-level admission；在语义工作量未恢复前，不通过 lookahead/future-cap 扫描掩盖问题。

`bounded-delta-cap8-v3` 的失败是方法性负结果，不归因于 GPU 或网络：197/197 transport
均成功、无 `finish_reason=length`，两个服务持续满载推进。根因是 8B 模型在较大 structured
page 中过度枚举/改写关系，引发 downstream dedupe、embedding 和 DB read 放大。后续候选
必须同时优化 page 数和 delta precision，不能只看上游 transport。

`bounded-delta-cap2-v4` 证明把容量缩小仍不能恢复语义：虽然 page=77、delta=78、
dedupe=33 已接近 single-edge，但最终图出现 `Evernote -> Airbnb`、`Jordan -> New York`
等错误合并。下一候选不再改变每页 cardinality，而是利用 turn-local partitions 之间的
独立性，通过有界 pipeline/continuous batching 缩短关键路径；每个 partition 内的
single-edge fixed-point 顺序不变，因此可直接对照原 51-edge 语义参考。

`single-edge-partition-pipeline-v5-c4` 的 proof、120/120 transport、4/4 replay、0 shadow
DB 均通过，makespan 从 102.249s 降至 90.804s；但只有 40 条最终边。差分中部分新增边
（如 `Jordan uses a dog leash`）可由原文支持，且旧 reference 自身也存在错误边；然而
候选确实漏掉 SkyMiles balance、Spirit baggage restrictions、Airbnb/VRBO 及多条酒店
事实，不能以 paraphrase 解释。下一次只改 physical-page concurrency 为 1；若召回恢复，
说明额外 nested batching 会改变 8B structured decoding/fixed-point 轨迹，后续应改用
稳定 admission/pipeline 设计，而不是继续提高 fan-out。

实际 c1 与 c4 的 extraction work 和 graph edge core 完全一致，说明 nested batching 没有
造成这次质量差异；c1 只把 makespan 恶化到 114.093s。通过本机先前 Codex session 的
append-only tool log 恢复了旧 `single_edge_semi_naive_until_zero_delta_v2` 的精确实现：
它使用 `Return exactly one ...`，并把 pagination 指令插在 Graphiti `# TASK` 之前。
cap8/cap2 修改则改写了措辞并把指令追加到 `# TASK` 之后；即使重新设 `maxItems=1`，
wire prompt 仍每页多 18 tokens，并改变首批 structured completion。v6 将精确恢复旧 prompt
语义/位置，同时保留 page-level audit、名称守卫和共享并发 4 的 worker pipeline。

v6 的首个 edge page token 指纹已精确恢复到旧版（例如 8925），但 c4 只产生 36 个
unique delta 和 30 条最终边，说明 exact-one pre-task prompt 对单个 logical call 内的
partition fan-out 敏感。旧代码并非全局串行：每个 edge wrapper 内按 partition 串行，
两个 episode 的 wrapper 仍可并行。v7 因此把 per-call worker 与 shared physical cap
解耦为 `W=1/G=2`；该值来自旧执行拓扑，不作为吞吐调参。

v7 仍产生与 v6 完全相同的 36 delta/30-edge core。进一步比较发现 messages 的 token
指纹相同，但 wire-level structured schema 不同：旧 helper 生成的 model/title 是
`MemBindSingleEdgePage`，通用 bounded helper 在 capacity=1 时仍发送
`MemBindBoundedEdgePage1`。该字段位于 OpenAI `response_format.json_schema`，不会计入
chat prompt token，却会改变 xgrammar 解码请求。v8 对 capacity=1 恢复旧 schema identity。

v8 的 completion/core 仍与 v7 相同。旧 diagnostics 显示两个 logical edge call 的页请求
交错但不在 page-0 对齐；旧代码直接在调用协程中执行 `for partition: await page-chain`。
当前 W1 仍创建 worker task 后 `gather`，会在第一个物理请求前额外 yield，使两个 episode
更容易同时进入 page-0 continuous batch。v9 对 W1 恢复 direct loop，仅 W>1 使用 task pool。

v9 实测 97.160s、106 transports、33 logical calls、577,648 prompt tokens、36 个 accepted
delta，最终仍为 86 entities / 30 edges；21 个 duplicate page、4 个 invalid endpoint
response 和 25 个 zero-delta termination 构成新的因果证据。代码此前先过滤非法端点，再
用 accepted delta 是否为空判断 fixed point；因此一个首次出现但端点非法的 raw edge 会
立刻结束该 partition，后续合法事实永远没有机会返回。v10 将 `pagination_history`、
`seen_raw_identities` 和 `accepted_partition_edges` 分离：首次非法边进入 history 并继续，
但不进入 Graphiti；仅重复 raw identity 才 zero-delta。新增
`pagination_raw_unique_progress_edges` 后，离线回归覆盖“非法 -> 合法 -> 重复合法”和
“非法 -> 重复非法”两条轨迹，完整 provider-free suite 为 79 passed。live prefix-2 的首要
gate 是 page 数和合法图覆盖恢复接近 single-edge-v2 的 88 pages / 51 edges；在此之前不以
makespan 作为晋级理由。

v10 live 显示 raw-progress 修复按预期工作，但只从 v9 的 62 pages / 106 transports 增至
66 pages / 110 transports，raw unique=40、accepted delta=36，最终 graph hash 和 30-edge
core 未恢复。更关键的输入差分排除了上游语义漂移：reference 与 v10 的 15 次 node
extraction 在 partition、prompt tokens、completion tokens 上逐项完全相同；26 个 edge
partition 的 page-0 prompt tokens、observed tokens、实体数和 structured schema 也逐项
相同。差异从同输入 page-0 completion 开始，说明 8B structured decoding 的 continuous
batch 数值/调度轨迹会改变首边选择，而“一次重复即 fixed point”把该差异放大为召回
损失。v11 因此只在首次重复时加入一次 `DUPLICATE_RECOVERY` 请求，要求按 ENTITIES 顺序
寻找不在历史中的完整 tuple；正常页 wire prompt 不变。若 recovery 成功率低则拒绝并转向
确定性候选覆盖分解，禁止无限 retry。

v11 live 在第二个 source 的 recovery 后把 `dedupe_edges.resolve_edge` logical calls 从 v10
最终 24 提升到运行中 33，证明 duplicate recovery 能找回新事实；但随后的
`extract_nodes.extract_summaries_batch` 使用 Graphiti 原生无界 `summaries: list[...]`
schema，在 GPU 0 连续解码约 6 分钟、输出约 152KB 后以未闭合 JSON 和
`JSONDecodeError` 失败。服务、网络、GPU 与 Neo4j 全程健康，因此这是代码层 work
amplification，不是基础设施噪声。v12 把 structured schema 严格对齐 Graphiti 已写入
prompt/后处理的合同：返回行数不得超过该 flight 请求的实体数，每条 summary 不得超过
`MAX_SUMMARY_CHARS`；unknown/duplicate provenance audit 继续保留。这是公共栈修复，若
v12 通过则所有正式 baseline 都必须在该 shared stack 上重跑。

v12 live 证明“恢复边数”不是充分条件：尽管最终同为 51 edges，exact overlap 只有 22，
且新增边中出现 `Citi AAdvantage Executive card -> baggage policies/restrictions/Fort
Lauderdale/March` 等明显跨 topic 绑定。根因是旧 `turn_local_entity_cover_merge_v2` 只缩小
`ENTITIES`，却仍把完整 episode 放在每个 partition 的 `CURRENT MESSAGE`；因此每个 Citi
实体窗口仍能看到 Spirit、酒店等全文，同时每一页重复携带 4K-9K prompt。v13 将候选
定义改为 `local entities + exact node-partition source text`：单 source 与相邻双 source
窗口都以 source id、source SHA-256 和字符数审计，不保存原文；live provider scope 若
缺 node source provenance 则显式失败，不允许退回全文。v13 首轮关闭 v12 recovery，先
单独验证证据对齐能否同时消除错绑和 prompt amplification。其 prefix-2 晋级条件为：
accepted edges 均可在对应 source window 中落证、无跨 topic endpoint misbinding、proof
与 4/4 replay 通过、0 shadow write/true retry、无 summary runaway，并相对 v12 显著降低
edge-page prompt tokens 与 downstream work。边数不要求机械等于旧 51，但 SkyMiles、
Spirit、Airbnb/VRBO 和酒店等当前 prefix 中出现的核心显式事实必须逐项检查。

## 12. 2026-08-28 continuation: r60/r61 and next falsifiable mechanism

`r60a` (`3bf8f9a0beac`) 已完成 prefix-8，但其首次 live timing 暴露了一个工程 hook
错误：物理 admission 被写到 `QwenVLLMClient` 的 `_QwenOpenAITransport` wrapper，而非
实际 `RoutedOpenAIClient`，因此没有任何 `ADMISSION_*` physical event。该 timing 不作为
物理准入结果，artifact 和 decision record 保留。

修复 `_resolve_routed_client` 后，`r61a` (`2dd8d933570e`) 的 332 个 transport 均出现
`ADMISSION_ENQUEUE/ADMIT/RELEASE`，endpoint resource id 与 route 一致，计数全部归零，
construction/route/replay/shadow-write/Quality-v1/node-surface proof 全 PASS。但总时长
`198.2396s`，慢于保留的 r53a/r53b (`188.9475/190.4057s`)；edge pages 为 259、page
queue wait 约 `145.8s`，且工作量比 r53a/r53b 的 248/250 pages 更高。因此
`logical-source-lease-endpoint-aware-physical-admission` 判定为
`REJECTED_NO_HEADLINE_GAIN`，不扩展 prefix-16，也不重跑 Native。

下一候选 `entity-block-literal-endpoint-grounding-v1` 只在 V6.1 的 edge partition
structured-output schema 中把当前 `<ENTITIES>` 的合法 source/target names 编译成
`Literal` enum。它不改变 page capacity、partition 并发、prompt 语义、relation/fact
字段、fixed-point 顺序、publication order、资源或模型；目标是把当前约 48 个
invalid-endpoint raw candidates 在生成阶段排除，减少 predicate rejection 后的分页放大，
并记录 `endpoint_schema_grounding_enabled`。Native 的 schema 保持旧路径，故旧
`NATIVE_PARALLEL/20597f72b70f` 仅作为 B1 upper bound 保留且不重跑。RED/GREEN 与完整离线套件
通过后，先做一个 fresh prefix-8；只有 invalid endpoint、transport/page work 和总关键
路径同时改善且 graph/QA/proof 不退化，才做第二次确认。

## 13. 2026-08-28 continuation: r63 work-conserving edge admission

`r63a` (`62a684f33423`) 实测验证了下一项方法代码机制
`arbiter_work_conserving_partition_derived_v1`。V6.1 的 edge physical page lanes 从固定
gate 的 2 个变为由现有 `EDGE_PARTITION_WORKERS_8B=2` 拓扑推导的 4 个；page capacity 仍为
2，partition/page prompt、fixed-point 顺序、模型、资源、route policy 和 publication order
均未改变。运行 manifest 明确记录了该 policy，故这不是 W/F/Q 或 lane 数扫描。

结果为 8 episodes、313 transports、240 pages、253 raw unique delta edges、684,354 prompt
tokens、38,717 completion tokens，`makespan=158.771889902s`。相同 endpoint-grounded
schema 的 r62a/r62b 为 183.044661251/182.102466202s，故相对 r62 median 提升 13.037%；
相对保留 r53a/r53b 的 188.947472239/190.405727670s 提升 15.970%/16.614%。物理 arbiter
有 313/313 enqueue、admit、release，0 transport failure、0 retry、0 invalid endpoint。

construction、route、replay/request identity、shadow DB、Quality-v1 和 node-surface
grounding 全部通过。Quality-v1 的 3 个问题 episode ranking 与 prefix-gold rank 均 exact；
top-fact hash 跨候选不 exact，故当前只声明速度与合同 proof 的改善，不声明 exact graph
equivalence。r63 图为 292 entities/197 edges，namespace snapshot 在只读 probe 前后不变。

该方向暂记为 `RETAINED_DIRECTION_PENDING_REPLICATION`，不是 full5 解锁。下一步只重复
一次同一 r63 方法代码的 fresh prefix-8 (`r63b`) 以排除单次服务抖动；不重跑 Native、不扫描
更多 lane 数、不改变 page/prompt/semantic work。若 r63b 保持收益且 service dilation、graph、
quality、proof 均不退化，再进入 prefix-16 验证；prefix-16 通过后才评估是否启动 full5。

### r63b 复测结论与 prefix-16 晋级

`r63b` (`824a9121c5dc`) 在同一代码和资源下完成 8 episodes，`makespan=164.477281484s`；
其 320 transports、248 pages、711,817 prompt tokens、39,946 completion tokens 均无
transport failure/retry，`pagination_invalid_endpoint_edges=0`。sealed manifest 仍为 4 个
work-conserving edge physical lanes，edge page queue wait 的总和仅 0.247s（r63a 为 0.349s），
没有出现 queue-gate 被移除后 service dilation 抵消收益的证据。

r63a/r63b 中位数为 `161.624585693s`，相对 r62a/r62b 中位数 `182.5735637265s` 提升
11.474%，相对 r53a/r53b 提升 14.461%/15.116%。两次 Quality-v1 的 episode ranking 和
prefix-gold rank 均 exact，node-surface/grounding、route、replay、request identity、shadow
DB 与 physical admission proof 均 PASS；只读 probe graph writes=0 且 namespace 前后不变。
8B 的 graph surface 仍有自然变动（r63a 292/197，r63b 297/211），所以不宣称 exact graph
equivalence。

该方向现记为 `RETAINED_DIRECTION_PROMOTE_PREFIX16`。下一步仅运行一个 fresh prefix-16 的
V6.1 attempt，保持 endpoint-grounded schema、4 个 topology-derived edge lanes、page=2、
prompt/semantic work、模型和资源全部冻结；不重跑 Native、不搜索 lane 数。prefix-16 必须同时
有清晰 makespan 改善、proof/quality/grounding 通过，才进入 full5 评估。

### r64a prefix-16 结论与 headline gate

`r64a` (`c60399b7233b`) 完成 16/16 episodes，`makespan=413.013033178s`，相对旧 V6.1
r54a 的 `439.384925830s` 快 6.002%。同尺度、仅作辅助校准且不再重跑的 Native r55a 为
`481.952142590s`，故相对 B1 relaxed-order auxiliary calibration 为 `1.1669x`；这不是
B0/Core headline speedup，full5 仍不解锁。

r64a 为 702 transports、552 pages、1,710,476 prompt tokens、89,552 completion tokens，
0 transport failure/retry、0 invalid endpoint，graph 为 623 entities/492 edges。construction、
route、replay/request identity、shadow DB、702/702 physical admission、Quality-v1 7/7 episode
与 prefix-gold ranking、node-surface/grounding 均 PASS；只读 probe 无 graph write/namespace
变化。

关键路径解释不是简单的“并发越高越快”：r54a 两个 edge lane 的 page queue wait 总和为
259.532s，r64a 四个 topology-derived lane 将其降至 0.689s；但 edge page service 总和从
792.698s 增到 874.278s，service dilation 抵消了大部分 queue 收益。这支持保留方法方向，
但不支持直接宣称 headline 已实现。

按 9.5 headline gate，下一步先运行一次严格 B0 `native-serial-dual` prefix-30，随后
运行同一方法代码的 fresh prefix-30 V6.1；旧 B1 `696.445710877s` 只作 ceiling 辅助。
若相对 B0 的大尺度收益清晰且 critical path、proof、quality 可解释，才冻结 selected
method 并启动 full5；否则记录负结果并继续系统方法设计。

## 14. 理论诊断：收益为什么在小规模出现、在大规模消失

本节是后续 autoresearch 的设计约束，不是新的参数搜索计划。当前 V6.1 的贡献应准确
表述为：在 Graphiti 的有序 durable frontier 上，把“下一 episode 的可复用抽取”与
“当前 episode 的权威写入”做受控重叠，并用 capture/replay、单权威 publication、来源
证据和 fixed-point page 证明结果可复用。它不是简单地把 HTTP 请求发得更多，也不是把
`lane` 数调到某个经验值。

### 14.1 真实执行模型

对 episode `i`，把执行分成四类工作：

```text
P_i : PREPARE（node/edge extraction、page fixed-point、必要的候选解析）
N_i : NATIVE（当前 frontier 的 Graphiti merge/dedupe/summary/publication）
R_i : replay/证据与本地物化
D_i : embedding、Neo4j read/write、提交顺序和 frontier 依赖
```

V6.1 当前由 `run_jit_frontier_history_async` 建立最多两个 source 的准备窗口；
`ForegroundAdmissionArbiter` 只允许有界 future work，`enter_native_guard` 保证当前
episode 的 publication 不被未认证 future work 破坏，`V61ProviderClient` 把已 capture 的
调用在 NATIVE 阶段变成零 transport replay。于是端到端时间不是“请求数除以并发度”，而是

```text
T_v61 ~= pipeline fill/drain
       + max(P-path, N-path, D-path)
       + frontier stalls
       + GPU service dilation
```

其中 `P-path`、`N-path` 和 `D-path` 由 episode/page 的实际依赖图决定；任何增加并发的
动作，若使单个 GPU 上的 service time 增长，都会直接抬高 `P-path`。这就是为什么
queue wait 下降并不等于 makespan 下降。

### 14.2 r53/r63 与 r64/r65 的因果差异

| 证据 | prefix-8（r53/r63） | prefix-16/30（r64/r65） | 理论含义 |
| --- | ---: | ---: | --- |
| V6.1 makespan | 188.947/190.406 s（r53）；158.772/164.477 s（r63） | 413.013 s（r64）；705.136 s（r65） | 小规模仍处于 pipeline 可隐藏的阶段 |
| edge page 数 | 约 240-250 | 552 / 952 | history 增长后固定点工作接近线性累积 |
| prompt tokens | 约 0.68-0.71 M | 1.71 M / 3.02 M | 每页反复携带历史上下文，工作量不是常数 |
| edge page queue wait | r63a 0.349 s、r63b 0.247 s | r64a 0.689 s；r65a 0.945 s | 4-lane 解决的是 admission 等待，不是 GPU 服务 |
| edge page service | r63a 367.927 s | r64a 874.278 s；r65a 1477.286 s | 放大并发后 service dilation 成为瓶颈 |
| V6.1 对 Native | 仅有 prefix-16 辅助校准 1.1669x；prefix-30 正式为 0.9877x | 未达 1.30x | 不能把小样本收益外推到 full5 |

r63 的收益来自两个有明确因果的动作：节点 partition pipeline 消除了 source 内
的空洞，work-conserving admission 消除了大量 page gate 等待；它不是“4”这个数字本身。
到了 r64/r65，page gate 已经几乎不等待，继续放宽 gate 只能让同一 GPU 同时持有更多
长 structured-decoding 请求。vLLM 的内部 scheduler 会在 iteration-level batch 中合并这些
请求，但客户端的 semaphore 看不到真正的 prefill/decode token 形状，因此不能保证增加
外部并发会增加有效 GPU throughput。

另一个更根本的事实是，V6.1 和冻结 Native 的逻辑工作还不是同一个 estimand：prefix-30
Native 有 828 个 logical LLM calls、1,895 transports、3.058 M prompt tokens；r65a 有
147 个 external logical calls、1,219 transports、3.021 M prompt tokens，且 V6.1 通过
replay/predicate/grounded materialization 少做了大量 dedupe/summary provider 工作。V6.1
在少做工作的情况下仍只达到 `0.9877x`，说明当前首要问题是资源调度与阶段依赖，而不是
“再找一个 lane 数”。但论文必须同时报告 resource-matched 与 same-semantics 两种
estimand，不能把少做工作全部归因于调度。

### 14.3 与系统顶会方法的对应关系

下面这些是可迁移的系统思想，不是把论文名称当作参数来源：

| 系统思想 | 代表工作/会议 | 对 MemBind 的启示 | 当前缺口 |
| --- | --- | --- | --- |
| iteration-level continuous batching | Orca, OSDI 2022；vLLM/PagedAttention, SOSP 2023 | 以 token/iteration 而不是 request 数度量并发；区分长 prefill 与短 decode | 当前 gate 只看 request/粗粒度 token debt，看不到 vLLM 内部 batch 形状 |
| prefill/decode disaggregation | DistServe, OSDI 2024；Splitwise, ISCA 2024 | 将会造成长尾的准备工作与 latency-sensitive publication 隔离，或显式传递 KV/中间结果 | 两个完整副本不是严格的 prefill/decode disaggregation；spillover 仍可能把 PREPARE 放到 NATIVE GPU |
| token-aware serving | Sarathi-Serve, OSDI 2024；PagedAttention 系列 | admission 应以 prompt+completion token 的可用预算和服务时间预测为资源，而不是 lane | `ForegroundAdmissionArbiter` 有 token budget，但路由和 page gate 没有统一 token-cost model |
| critical-path/list scheduling | DAG/HEFT 类异构调度；数据库执行器中的 ready-task scheduling | 对 source frontier、page fixed-point、dedupe、publication 建 DAG，优先真正影响 durable frontier 的 ready task | 当前 source-priority/FIFO gate 不知道剩余路径长度和 endpoint finish time |
| morsel-driven / work stealing | HyPer/Morsel-Driven Parallelism, VLDB 2014；GraphIt, OSDI 2018 | 把独立 partition/page 作为可窃取 morsel，动态填补空洞，同时按 ID 确定性归并 | 当前 partition worker 先静态展开；跨 source 的 ready work 没有统一队列 |
| out-of-order execute, in-order commit | 流处理与数据库的 epoch/snapshot/ordered commit | 允许准备结果乱序完成，以 immutable artifact 和版本检查保证按 source 顺序提交 | 当前 future cap=1、native guard 偏保守，且没有把“可安全乱序”的任务边界显式化 |
| incremental view maintenance / delta processing | 数据库增量维护、流处理 backpressure | 对未变化的实体/关系使用 content-addressed extraction artifact，只处理 frontier 的 delta 与受影响邻域 | 当前 edge page 仍反复携带历史上下文；这是潜在的数量级工作减少点，但必须做语义闭包证明 |

### 14.4 下一项真正的方法代码候选

下一项不改变 lane、page capacity、prompt 截断、模型或资源。候选命名为
`frontier_critical_path_resource_scheduler_v1`，核心是一个**全局 ready-task DAG 调度器**：

1. 将 node partition、edge page、dedupe/summary、embedding 和 publication 表示为带有
   `source_sequence`、`partition_id`、前驱集合、预计 prompt/completion token 和历史服务
   时间的任务；page 内 fixed-point 依赖仍保持串行。
2. 让两个 endpoint 共享一个可重演的 ready queue。每次 dispatch 以“预计最早完成时间
   加上对 durable frontier 的 critical-path slack”为排序依据，同时检查每卡 token
   budget；这改变的是任务选择与资源绑定，不是把容量常数调大。
3. 允许非关键的 future page 乱序完成，但只把 immutable response 放入 content-addressed
   result store；`NATIVE` 仍按 source 顺序消费 capture/replay，版本或 provenance 不匹配
   时丢弃 speculative result，不写 Neo4j。
4. 将 `PREPARE` spillover、NATIVE 小请求和 embedding/Neo4j 阶段统一纳入 finish-time
   估计，避免现在“preferred endpoint + idle spillover”把长请求堆到同一 GPU。调度证据
   必须能重放每次选择，取消、失败和 permit 释放保持守恒。

唯一可证伪预测是：在**总 logical/transport/token/page 工作不增加**的前提下，减少
`frontier_wait`、GPU idle gap 和 endpoint service dilation，使 prefix-16/30 的
`T_critical` 下降；若只是 queue wait 下降而 service sum 上升，则候选立即拒绝。该候选
先做 provider-free DAG/取消/确定性归并测试，再做 fresh prefix-8；没有达到预期时不再
调度参数，而是转向下面的第二条路线。

### 14.5 第二条路线：真正的复用，而不是重复抽取

如果全局调度仍不能在 prefix-30 超过冻结 Native，最有潜力的理论方向不是继续增加并发，
而是把 v6.1 的“复用”从 episode 内 replay 推进到**增量视图维护**：

```text
source hash + prompt/schema/model hash -> immutable node/edge extraction artifact
new source -> changed entities/relations -> affected-neighborhood closure
closure only -> fixed-point validation -> ordered publication
```

这条路线的收益来自消除 Graphiti 当前对全部历史边的重复扫描，而不是减少合法语义。必须
先定义受影响邻域的闭包：同一 canonical entity、同一 relation type、temporal conflict
candidate、以及会改变 summary 的 source span 都必须进入闭包；闭包外的历史事实可直接
复用。每次 cache hit 要记录 artifact hash、依赖 frontier、命中/失效原因，并用 reference
Native 或离线全重算做 exact stable-core、temporal 和 QA 对账。没有闭包证明，不能把少做
的 provider calls 宣称为系统加速。

### 14.6 当前决策

`r66a` 的 adaptive controller 已被性能证据否定（8-episode `169.417s`，其中 page queue
48.835s；固定 4-lane r63a 为 `158.772s`），只保留为负面消融。8B runner 的默认路径已
恢复为固定 work-conserving substrate，adaptive 只能由明确命名的 ablation 显式启用。

在完成本节理论设计和 provider-free TDD 之前，不启动新的 live attempt，也不启动 full5。
下一次 live 必须能回答一个方法问题：全局 critical-path/token-aware 调度是否在相同工作
合同下减少 GPU service dilation；若不能，再进入增量视图维护，而不是继续微调适配参数。

### 14.7 r67 实验结论：测量边界错误导致候选被拒绝

`r67-critical-path-prefix8-20260828` 是在新 namespace 中对上述 scheduler 的首次真实
验证，8 episodes 完成且所有 correctness、route proof、replay、shadow DB、ordered
publication 和 quality 证据均 PASS。结果为：

```text
V6.1 critical-path candidate: 165.510699654 s
retained fixed V6.1 r63a:     158.771889902 s
relative to r63a:             -4.25%
transport attempts:           320
prompt tokens:                711,810
completion tokens:            39,942
embedding items:              1,554
db writes:                    40
```

因此该候选不进入 prefix-16/30，也不解锁 full5。失败不是功能或证据失败，而是系统
测量反馈错误：scheduler dispatch 在 endpoint 选择时预留的是 provider service work，
但完成样本原先从 route entry 开始计时，包含 physical-admission queue wait。排队越长，
EWMA 反而越大；后续选择因此把真实排队拥塞误认为端点服务能力差，并产生大量跨 phase
spillover。r67 的 route proof 显示 critical scheduler 虽然 balanced，但 320 个请求中
154 个被标记为 spillover，说明该反馈已经主导路由而不是只在少数 tie 上起作用。

本轮已完成的方法级修复：service sample 从 physical permit 获得后才开始，route event
同时记录端到端 `duration_ns` 与 provider-only `service_duration_ns`；admission 在 provider
启动前失败/取消时调用 scheduler `cancel`，不污染 service EWMA。critical-path route
proof 现在要求 start/end/duration 三者自洽，防止再次把 queue 纳入 service 模型。该修复
有专门的 admission-wait 单测，V6.1 scheduler/routing/runtime 定向回归为 `111 passed`。

全套 `saturated_fixed_work_baseline_v1_3/tests` 在本轮执行时进入已有 qualification 测试
的文件系统 journal wait，未获得可靠退出码；这属于测试环境 I/O 阻塞，不改变上述定向
回归结论，也没有停止任何模型服务。下一次 live 只应重新验证修复后的同一 critical-path
方法，继续使用 prefix-8 和新 namespace；只有在 service dilation、frontier wait 和
总工作量同时改善时，才进入更长 prefix。

### 14.8 r69 确认性结论：service-only 反馈仍无稳定收益

`r69-critical-path-release-excluded-prefix8-20260828` 在进一步排除 physical release/cleanup
时间后完成，全部 correctness、route proof、replay、quality 和 balanced state 仍为 PASS：

```text
r69 critical-path: 164.270327060 s
r63b retained elastic: 164.477281484 s
r63a retained elastic: 158.771889902 s
```

但 r69 使用了 312 transports / 681,668 prompt tokens，r63b 使用 313 / 684,354；严格
resource-matched 口径下，0.13% 的差异不足以归因于 scheduler。r69 的 frontier wait 为
124.95s、provider service sum 为 314.80s，且 PREPARE 有 146 次 spillover 到 native、
NATIVE 有 22 次 spillover 到 prepare。结合 r67/r68/r69，结论是：**仅靠 endpoint
finish-time 与服务 EWMA 调度，没有解决端点内部 batch dilation，也没有稳定超过既有
work-conserving elastic substrate。** critical-path 分支保留为已审计负面结果，不进入
prefix-16/30 或 full5。

下一步转向真正的系统机制：严格的 prefill/decode 角色隔离与跨 source 的增量视图维护。
前者避免 speculative PREPARE 请求侵入 authoritative NATIVE 队列；后者用 source/schema/
model 内容寻址 artifact 和受影响邻域闭包，减少历史边的重复 extraction。两者都必须先
在 provider-free contract 中证明不改变 durable graph 语义，再做 fresh prefix-8；在此之前
不启动 full5，也不再搜索并发或 lane 参数。
