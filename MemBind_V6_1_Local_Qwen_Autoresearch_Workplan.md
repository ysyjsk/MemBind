# MemBind V6.1 本地 Qwen Autoresearch 执行计划

> **状态说明（2026-08-28）：本文件是 14B 历史执行记录，不再作为当前执行协议。**  
> 当前 Qwen3-8B 双副本平台、公共正确性修复、V6.1 autoresearch 与 5-history
> 正式实验均以
> [`MemBind_V6_1_8B_Autoresearch_Workplan.md`](./MemBind_V6_1_8B_Autoresearch_Workplan.md)
> 为唯一权威 workplan。下文保留用于追溯 14B 负结果和系统设计演进。

> 创建日期：2026-08-27  
> 工作目录：`/data/predator/ly/MemBind`  
> 实验身份：`local-qwen3-14b-awq-v1`  
> 目标：先在 MAB 第一个 history 的约 30 个 episode 上重测 Native 与冻结 V6.0，随后以 TDD + 小数据 autoresearch 开发独立 V6.1，最后启动五个 history 的 Native、并行 baseline、V6.1 完整主实验并观察至稳定运行。

## 0. 2026-08-27 系统研究修订（覆盖旧参数搜索路线）

本节是当前执行依据；下文早期 `W/F/Q` 候选搜索记录只保留为历史，不再驱动新实验。

- Native 30 episode 已冻结为 `76130682ee53`，makespan `3202.686653549s`；禁止重复运行。
- V6.0 30 episode 已冻结为 `57cbe4c487da`，makespan `6103.876204731s`；禁止修改或重复运行。
- V6.1 `c1c71dfc2c17` 证明 KV budget 内的 long prefill/native co-residency 仍会造成 10-30s native latency 和 7 次 transport reset。
- V6.1 `ca37da04d1de` 已排空 future prefill，但 8 路 native decode 仍为 `520.596834050s` 并产生 8 次 reset。
- 根因已经从“future 参数不佳”收敛为“单资源 admission 模型错误”：provider slots、KV tokens 和 native decode concurrency 必须独立建模。
- V6.1-Core 固定采用 61,440 KV-token budget、future-before-native drain、native interval future close、2 条 native decode lanes。2 lanes 来自固定部署 trace characterization，不作为 autoresearch 参数。
- 8-episode system-method validation `3127ab8ab7aa` 已完成：`303.289918823s`，相对同一冻结 Native 8-prefix `336.123286943s` 提升 `9.77%`；request/replay/provider proof 全部通过，且与旧稳定 V6.1 的 entities/edges 语义集合 exact match。
- 16-episode promotion attempt `4ee6a778b467` 因 `/data/predator` 在 timed interval 内耗尽可用空间而失效；source 10 的 258.253s provider interval 含 226.264s 基础设施等待，live journal 以半条 JSON 结束。该 attempt 仅作故障证据，`timing_invalidation.json` 已明确禁止进入性能比较。
- fresh attempt `f7af88e0a0f5` 推进至 source 8、135 次 provider call 全成功且 0 retry；随后主机在 `2026-08-27 17:03:51 CST` 重启，进程退出且没有 seal。已标记 `SYSTEM_REBOOT_DURING_TIMED_RUN`，同样不进入性能比较。
- detached tmux attempt `43f669f8c9e1` 完成 16 个 source 的 publication、328 次 provider wrapper call 且资源归零，但 seal 前发现 observed transport=384、provider external logical=296 的 88 次未归因 transport；当时的 reranker 旁路假设随后被 10-episode 精确 trace 否定，attempt 保持失败且不进入性能比较。
- V6.1 代码已增加 auxiliary shared-transport admission guard：reranker 的每个直连请求现在与 LLM wrapper 共用 provider/native/KV arbiter，并记录 `auxiliary=true` 的 provider evidence；普通 wrapper 调用通过 ContextVar 避免双重 admission。下一步从 2-episode smoke 验证 accounting 后再升到 4/8/16。
- 后续 10-episode trace 证明额外 transport 不是 reranker：一个 oversized extraction logical call 会按完整 dialogue turns 展开为多个真实 transport。V6.1 provider evidence 现逐 logical call 精确记录 `transport_attempt_count`、`transport_retry_count`，并以严格等式对齐 recorder span。
- nested Qwen transport 诊断 `32e217db8a31` 完成全部 source 后以 `observed=216, expected=432` 被正确拒绝；根因是 Qwen outer seam 与 raw AsyncOpenAI inner seam 双重计数，已通过 transport-wrapper depth 修复并标记 `ACCOUNTING_INSTRUMENTATION_DOUBLE_COUNT`，禁止纳入性能比较。
- fresh 10-episode attempt `fc53d7163382` 已封存：`591.219614405s`，冻结 Native 同一 10-prefix=`629.335901356s`，提升 `6.06%`（`1.064x`）；194 external logical + 22 dialogue-partition expansion + 0 retry = 216 transport，与 216 recorder spans 完全一致；20/20 request、20/20 replay、provider/shared-arbiter proof 全 PASS。
- fresh 16-episode pre-handoff attempt `50d657ad4b87` 已封存：`1430.288857268s`，相对冻结 Native 16-prefix=`1456.351381255s` 提升 `1.79%`；296 external logical + 88 expansion + 0 retry = 384 transport，32/32 replay 和全部 proof PASS。
- pre-handoff 16 trace 同时直接暴露 frontier completion race：最终 FRONTIER permit release 后、executor 进入 native guard 前，排队的 39K--42K token FUTURE 可立即抢占空出的 lane，导致 9 次 native guard drain。atomic frontier-to-native handoff 候选已完成 provider-free 验证以及 fresh 4/16 real-stack 实验；16-episode attempt `95e4c864521d` 为 `1455.613130429s`，虽把 drain 从 9 次/249.824s 降到 2 次/39.188s，但比 pre-handoff `50d657ad4b87` 慢 `25.324273161s`，仅比冻结 Native 快 `0.05069%`。结论为 correctness `PASS`、performance `REJECT`，相关代码已经撤回，artifact 保留为负结果。
- Native16 与 pre-handoff V6.1 实际执行相同的 296 logical LLM、384 transport 和 80 DB writes；当前收益无法继续扩大的主因不是 replay miss，而是 JIT guard 把 extraction 与 native suffix 反复串成 `PREPARE -> drain -> NATIVE`。下一候选固定为 bounded extraction stage separation：Stage A 仅并发执行最终都会被消费的 certified node/edge extraction，不做 shadow DB write；barrier 归零 provider 状态后，Stage B 按 source order 执行 authoritative native publication并 exact replay。它是执行图/critical-path 优化，不是 W/F/Q 搜索。
- 在同一 30-episode prefix 的 V6.1 同时优于冻结 Native/V6.0 前，full5 保持禁止启动。
- bounded extraction stage separation 已完成 fresh 4/8/16 验证；16-episode attempt `9246ef932a04` 为 `1287.939039904s`，相对冻结 Native 同 prefix `1456.351381255s` 达到 `1.13076x` speedup、wall time 降低 `11.564%`。382/382 transport 成功、0 retry，32/32 request/replay 与 provider/shared-arbiter proof 全 PASS。
- staged-16 的 PREPARE=`794.650441105s`、NATIVE=`493.288598799s`，全局 barrier 使首条 durable publication 延迟至 `795.054902720s`；下一系统 arm 改为独立 8B 双副本 phase affinity，在 fresh `local-qwen3-8b-awq-dualreplica-v1` 下用 resource-matched Native8B 对照验证，禁止将冻结 Native14B 当作 8B headline baseline。

完整论文、官方源码与 MemBind 代码边界映射见：

`saturated_fixed_work_baseline_v1_3/v6_1/V6_1_SYSTEMS_RESEARCH_20260827.md`

---

## 1. 本轮研究问题与交付物

本轮不再讨论“V6.0 extraction replay 是否命中”：已有结果已经证明 232/232 capture 被精确消费，并把 node/edge extraction 从数千秒降到亚秒。真正要解决的问题是：

> 如何在共享、FCFS 的本地 Qwen3-14B-AWQ provider 上，保留 V6 exact extraction replay 的语义，同时限制 future work 对 authoritative native work 的排队干扰和额外工作放大，使 replay 的局部收益转化为端到端收益？

必须交付：

1. 第一个 MAB history、约 30 episodes 的 Native 与冻结 V6.0 同环境速度基线；
2. 与 V6.0 隔离的 `membind_v6_1` 实现、单元测试、调度与 evidence 修复；
3. 小样本逐级扩大的 autoresearch 记录，以及最终选定的 V6.1 policy；
4. 同一 30-episode prefix 上的 Native、V6.0、V6.1 可比结果；
5. 五个完整 history 的 Native、并行 baseline、V6.1 后台主实验队列、运行状态和恢复说明；
6. 后续可直接汇总为主表的机器可读 artifact。

本轮 construction latency/makespan 是主指标。QA 不进入早期优化循环；完整主实验是否跑 QA 由现有 MAB protocol 决定，但不得让 QA 阻塞 construction 主表启动。

---

## 2. 不可改变的运行环境合同

每一条实验、测试、preflight、监控和恢复命令都先执行：

```bash
cd /data/predator/ly/MemBind
source /data/predator/ly/MemBind/scripts/local_runtime/activate.sh
```

固定配置：

| 资源 | 本轮固定值 |
|---|---|
| profile / artifact identity | `local-qwen3-14b-awq-v1` |
| Python | `/data/predator/ly/Mem/envs/membind-local/bin/python` |
| LLM | `http://127.0.0.1:18100/v1`, `qwen3-14b-awq`, API key 由 activation 注入 |
| LLM runtime | GPU 0；64K；YaRN 1.6；`max_num_seqs=8`；8K batch；xgrammar；prefix caching；chunked prefill；FCFS；thinking off |
| Embedding | `http://127.0.0.1:18101/v1`, `qwen3-embedding-0.6b`, 1024d |
| Embedding runtime | GPU 1；BF16；32K；`max_num_seqs=128`；32K batch |
| Graphiti | `GRAPHITI_MAX_COROUTINES=8` |
| Graphiti client completion/transport | logical `32768`；2048、8192 与 16384 均在 context 0 source 25 截断；本地 Qwen chat-template 精确计数后令 wire budget=`min(32768,65536-input-32)`，另有最多 6 次仅针对 context-overflow 的 server-error fallback；HTTP timeout=`3600s`；SDK retries=`0`；Graphiti retry policy=`single_attempt_no_tenacity`；oversized extraction=`dialogue_turn_partition_merge_v1` |
| Neo4j | activation 后的本机独立服务配置 |
| service tmux | `membind-local-llm`, `membind-local-embedding` |
| ready marker | `/data/predator/ly/Mem/run/membind-local/background-setup.status` 必须为 `READY` |

正式实验前必须同时验证：

```text
GET http://127.0.0.1:18100/v1/models -> qwen3-14b-awq
GET http://127.0.0.1:18101/v1/models -> qwen3-embedding-0.6b
```

如果服务不可达：先检查 status、tmux、PID、日志与端口；确认未运行后执行：

```bash
/data/predator/ly/MemBind/scripts/local_runtime/start_all.sh
```

一次网络失败不构成停止条件。若控制侧受沙箱/代理影响，应在上述本机 activation 环境使用 direct localhost probe 重试，并通过 tmux 日志和 PID 交叉验证。只有连续恢复失败且本机服务确实无法建立，才记录基础设施故障；保留 namespace 和 checkpoint 后继续可离线进行的 TDD 工作。

### 当前已验证起点（2026-08-27）

- ready marker：`READY`
- LLM catalog：`qwen3-14b-awq`, `max_model_len=65536`
- embedding catalog：`qwen3-embedding-0.6b`, `max_model_len=32768`
- activation 正确解析到 `/data/predator/ly/Mem/envs/membind-local/bin/python`
- repo HEAD：`111f7b4440bbcd94157b6fcdd89cf227e0853d55`
- exact-fit attempt `7f664c480cff`：source 25 运行期间服务端 `length` 计数未增加，证明 16K 截断问题已消失；随后 attempt 因 OpenAI SDK 默认 `600s` read timeout 失败，而非 context overflow/JSON truncation。
- transport 修复：local runtime 显式注入 `timeout=3600s,max_retries=0`；Graphiti construction client 与 reranker 共享该 transport；聚焦测试 `21 passed`，真实固定实验 Python 属性核验通过。
- retry 修复：local `QwenVLLMClient` 实例绕过 Graphiti 默认四次 tenacity JSON retry，避免确定性截断重复消耗 provider；Native/V6.0/V6.1 共用并记录该策略，且不修改冻结 32B 实现。
- extraction 修复：source 25 的 oversized `extract_nodes` prompt 按完整 dialogue turn 分块，逐块真实抽取并按实体名稳定去重合并；不截断、不静默丢弃，Native/V6.0/V6.1 共用该 local client policy。

每个 timed block 前仍重新验证，不能沿用本条记录代替当次证据。

---

## 3. 版本、数据与产物隔离

### 3.1 冻结边界

- 不覆盖、不重命名、不补写现有 Qwen3-32B-FP8 配置和 artifact。
- V6.0 是只读对照；允许新 runner 调用其冻结实现，不在 V6.0 模块内修 bug。
- V6.1 使用新模块：`saturated_fixed_work_baseline_v1_3.membind_v6_1`。
- 已有 `saturated_fixed_work_baseline_v1_3/v6_1/` 设计文件保留，并从 `OFFLINE_DESIGN_ONLY` 更新为本轮获准的 local live campaign；不得将其误标成旧 32B 正式实验。
- 任何模型、embedding、维度或 prompt/client contract 改变，都创建新的 profile、artifact root 和 graph/vector namespace，并重建向量索引。

### 3.2 新产物根目录

所有本轮运行产物放在 `/data/predator/ly/Mem`，建议固定为：

```text
/data/predator/ly/Mem/experiments/local-qwen3-14b-awq-v1/v6_1_mab/
  campaign_manifest.json
  preflight/
  prefix30/
    native/
    v6_0/
    autoresearch/
    v6_1_selected/
  full5/
    native/
    parallel/
    v6_1/
  logs/
  state/
  summary/
```

每个 attempt 使用 append-only、可唯一定位的目录：`method/history/prefix/policy/attempt_id`。失败 attempt 不覆盖，重跑创建新 attempt 并在 index 中标注 supersedes 关系。

Neo4j group/namespace 至少包含：

```text
local-qwen3-14b-awq-v1 + campaign + method + history + prefix + attempt_id
```

任何 timed attempt 前必须证明 namespace 尚未被使用；同一 failed attempt 的恢复只允许走经过测试的 resume contract，否则创建新 namespace。

### 3.3 最小可复现 manifest

每个 attempt 至少保存：

- profile、模型 catalog hash、endpoint（不含 secret）、embedding dimension；
- git HEAD、dirty-file list、runner/module file hashes；
- history id、episode limit、method、policy、seed；
- Graphiti/version/client/runtime concurrency；
- Neo4j namespace/group id；
- 启停 wall clock、monotonic makespan、终态；
- provider attempt、admission、replay、native interval、phase timing；
- nodes/edges/embedding items 或同等 work counters；
- stdout/stderr log、heartbeat、checkpoint 路径。

---

## 4. 已知 V6.0 问题与 V6.1 修改边界

V6.0 的 replay 核心有效，但历史全量结果只有 `1.1597x`，原因不是 extraction 没复用，而是 suffix 和 provider interference 抵消了收益：

| 证据 | V6.0 暴露的问题 |
|---|---|
| 232/232 replay exact match | extraction correctness/acceptance 不是当前首要瓶颈 |
| provider attempts 1273 -> 1482 | future work 放大 |
| mean transport 7.20s -> 24.96s | 共享 provider 排队恶化 |
| P95 23.77s -> 121.95s | native tail latency 失控 |
| entities/edges/embedding items 约 +18% 至 +20% | speculative suffix work 放大 |
| node resolution 1658s -> 3111s | replay 后 native suffix 被 future 请求压住 |
| edge resolution 134s -> 1752s | FCFS 队列中的 ahead-of-native work 形成极端尾部 |

V6.0 还有四个必须由 V6.1 修复的 evidence/composition bug：

1. MAB runner 创建 provider arbiter，却没有把同一个实例传给 frontier executor；
2. provider proof 对空事件调用 validator，产生 `admission_count=0 PASS` 的 vacuous proof；
3. `prepared_response_hash` 实际 hash 了 request/private payload，而非响应；
4. `db_writes=0` 从错误字段读取 operation class，不能证明 shadow 无写入。

V6.1-Core 只做与上述问题直接相关的改动：

- provider 与 executor 共享唯一 arbiter；
- bounded JIT preparation，不再 eager materialize 全 history；
- 分离 client semaphore 与“未来工作 outstanding”预算；
- native foreground priority、native guard/draining、可配置 future cap；
- admission/backpressure 基于真实 queue/native 状态，且 fail-closed；
- response hash、DB-write accounting、provider/native interval evidence 正确；
- exact extraction replay 的 request identity 与 source-order durable semantics 不改变。

本轮不把 timestamp batching、随机 dedupe cache、改变 candidate/result semantics 等 suffix 优化混入 V6.1-Core。若 Core 已稳定但收益仍受某个单一 suffix bottleneck 限制，可在独立 `V6.1-Suffix` arm 测试；主表必须保留 Core 单独结果。

---

## 5. 执行阶段

### Phase A：preflight 与复现闭环

1. 激活本地 runtime，验证 READY、两个 model catalog、Neo4j、Python import closure。
2. 解析 MAB 五个 history 的固定顺序、history 0 的 id、episode 数和 30-episode prefix 边界。
3. 验证 runtime builder 最终使用 `18100/18101`、本地模型名和 1024d，确认 `.env` 不会覆盖 activation。
4. 建立 campaign root、manifest、attempt index 和新 namespace 生成器。
5. 用 provider-free/fake-driver 测试证明 artifact path、namespace 和 resume 不会碰到旧 32B 产物。

完成定义：能打印一份不含 secret 的 resolved runtime/campaign manifest；preflight 与 namespace 测试通过。配置不匹配时修 runner 或创建 fresh attempt，不绕过检查。

### Phase B：history 0、约 30 episodes 的速度基线

在同一 history、同一前 30 episodes、同一 runtime、fresh namespace 上依次运行：

1. Native serial (`B0`)；
2. 冻结 V6.0 replay baseline；

运行顺序记录在 manifest。若明显存在服务 warm/cache order 影响，可做一次短 probe 判断，不能把额外重复运行变成无限 gate。核心输出：

```text
method, history_id, prefix_n, makespan_s,
provider_attempts, transport_mean_s, transport_p95_s,
nodes, edges, embedding_items,
replay_captured, replay_consumed, replay_exact,
native_phase_spans, terminal_status
```

V6.0 的已知 evidence bug要如实标为 `LEGACY_INVALID/NOT_OBSERVABLE`，不能用 vacuous PASS 支撑结论。该 run 的职责是提供同模型端到端速度对照，而不是把错误 proof 修回 V6.0。

### Phase C：V6.1 provider-free TDD

先写失败测试，再实现以下最小行为：

1. 单一 shared arbiter 在 provider wrapper 与 frontier executor 中对象 identity 相同；
2. `W/F/Q` 参数合法：`W>=1`、`0<=Q<=F<=7`；
3. future 工作只在 lookahead window 内创建，outstanding 不超过 `F`；
4. native begin 触发 future admission close；超过 `Q` 时等待/drain，native end 后恢复；
5. cancellation、provider error、DB error 均释放 permit，不死锁；
6. admission evidence 来自真实事件，空事件不能通过 timed-run proof；
7. response hash 对响应稳定、对响应变化敏感；
8. DB write accounting 覆盖 driver operation class，shadow write 导致 fail-closed；
9. source-order publication、exact replay matching 和 miss fallback 与 V6.0 一致；
10. heartbeat/checkpoint 能区分 progress、slow request、stalled/deadlocked。

测试不要求一次建成复杂状态机。先覆盖本轮实际 runner 需要的三个状态：`FUTURE_OPEN`、`NATIVE_GUARD`、`DRAINING`。

### Phase D：小数据 autoresearch

递进尺度：`2 -> 4 -> 8 -> 16 -> 30` episodes。只要某个尺度没有 correctness/deadlock 问题，就继续扩大；性能差的 policy 被淘汰，但研究循环继续探索下一 policy。

每轮只改一个主要变量并记录 hypothesis：

```text
W = JIT lookahead window
F = max future outstanding
Q = native begin 后允许仍活跃的 future 数，Q <= F
```

初始研究顺序：

1. correctness anchor：`W=1,F=0,Q=0`，退化为无 future interference 的 replay path；
2. conservative JIT：`W=1,F=1,Q=0`；
3. 小幅 overlap：`W=2,F=1,Q=0`；
4. 若 provider 空闲且 preparation 成为关键路径，再尝试 `W=2/4,F=2,Q=0/1`；
5. 只有前述证据显示低 interference，才尝试 `F=4`；本机 `max_num_seqs=8` 下默认不再复现 V6.0 的 `F=7,eager-all` 作为候选。

不做完整 `W x F x Q` 网格。autoresearch controller 每轮读取最近结果，保留最多 2 个候选，再选择一个邻域变化：

- native P95 高或 queue wait 增长：减 `F`、减 `Q`、更早 guard；
- preparation tail 暴露且 native queue 干净：增 `W` 或 `F` 一档；
- replay miss/extra work 上升：缩 `W`，检查 exact identity 和取消点；
- GPU/provider 利用率不足但 wall time无改善：检查非-provider suffix，暂不盲目加并发。

轻量 promotion 规则：

- **硬约束**：无死锁；30-episode 内 source-order 完成；replay exact/miss fallback 正确；无 shadow DB write；artifact 完整；
- **软选择**：优先最小 makespan，其次 native provider P95、provider attempts/work amplification，再次策略复杂度；
- 小样本慢不停止整个项目，只淘汰该 policy 或用于修正假设；
- 2/4 episode 噪声只用于排错，8/16 用于方向判断，30 用于最终 prefix 选择。

每个实验有 heartbeat 与合理的 request-timeout/stall 诊断，但不设置会因正常长尾请求频繁误杀的复杂 gate。一次失败先最小复现和修复；重复基础设施失败时保留工作，恢复服务后从 fresh attempt 继续。

### Phase D.1：Codex-style autoresearch controller（已实现）

控制器现在按持久化短循环运行：

```text
HYPOTHESIS_PROPOSED
  -> CANDIDATE_START
  -> CANDIDATE_HEARTBEAT (live_state.json)
  -> CANDIDATE_FINISH
  -> CANDIDATE_ANALYSIS
  -> POLICY_KEEP / POLICY_REJECT
  -> SCALE_PROMOTE 或 POLICY_MUTATE
```

具体约束：

- 每个候选使用独立 `run_id`、attempt 目录和 Graphiti namespace；失败或 timeout 不覆盖旧 attempt。
- 候选运行期间由 `Popen` 轮询子进程、attempt journal、durable frontier、日志增长和 no-progress 时间；正常长请求不会仅因 frontier 暂停就被判死锁。
- 分析同时检查 correctness、source-order frontier、provider/replay/shared-arbiter proof、transport/embedding/db work 和 extra-work ratio，并记录 failure class。
- `best_policy.json` 可以是 `PROVISIONAL`，但只有 `scale=30`、完整 evidence 且 `makespan < B0` 与 `makespan < V6_0` 的 candidate 才能生成 `selected_policy.json`。
- 没有满足改善 gate 时写入 `CAMPAIGN_NEEDS_MORE_SEARCH`；pipeline 使用新的 campaign namespace 持续 mutate，不进入 B1/full5。
- 搜索空间仍受 `W>=1`、`0<=Q<=F<=7` 限制，默认预算为 24 个 candidate 或 24 小时，避免无界重复实验；预算耗尽时保留全部日志和 checkpoint，等待下一轮 campaign。
- candidate budget 固定保留到最终尺度：`n=2/4/8/16` 各最多 4 个，`n=30` 使用剩余预算（默认最多 8 个）；best 只在同一 scale 内按 makespan 比较，避免小样本绝对耗时错误地主导大样本选择。
- 若冻结 V6.0 在三个 fresh namespace 上均以 `APITimeoutError` 未完成，则记录为 `RIGHT_CENSORED_TIMEOUT`，使用三次中最短 elapsed 作为保守 makespan 下界。它不是成功 V6.0 结果；V6.1 必须完整成功且同时快于该下界与成功 B0，才允许 promotion。
- 旧 pipeline 在代码升级前已启动，另设 `membind-v61-handoff` 只读监控；旧 supervisor 未退出前不启动 workload，若旧版 autoresearch 两轮未达标退出，则从新的 `autoresearch_retryN` namespace 由新版 supervisor 接续。

### 当前执行状态（持续更新）

- Native B0 30-episode prefix：已完成，`3202.686653549s`，attempt `76130682ee53`。
- 冻结 V6.0：第三次 fresh attempt `57cbe4c487da` 已成功封存，30 episodes makespan=`6103.876204731s`；前两次在 source 11 后分别于约 5024s、5011s timeout，最终成功 attempt 必须作为正式速度基线，失败 attempt 保留用于 tail-latency 诊断。
- V6.1 TDD：当前环境无 `pytest`，本轮不能沿用旧环境的 `21 passed` 作为已执行结论；`py_compile` 与五个定向 Python scenario 已通过。
- V6.1 autoresearch：transport accounting 修复后的 10-episode attempt `fc53d7163382` 为 `591.219614405s`，相对冻结 Native10 提升 `6.06%`；pre-handoff 16-episode attempt `50d657ad4b87` 为 `1430.288857268s`，相对冻结 Native16 提升 `1.79%`，全部 correctness/accounting proof PASS。atomic handoff 16-episode attempt `95e4c864521d` 为 `1455.613130429s`，performance REJECT 且代码已撤回。bounded extraction stage separation 已完成 fresh 4/8/16 验证，16-episode `9246ef932a04` 为 `1287.939039904s`、相对 Native16 `1.13076x`，但 TTFP 因全局 barrier 退化至 `795.054902720s`；下一步转入 resource-matched 8B 双副本 streaming 验证。
- full5：只有 autoresearch `CAMPAIGN_PASS` 且 pipeline 重新验证 promotion gate 后才会启动。

### Phase E：30-episode 选择与冻结

在与 Phase B 完全相同的 prefix 上运行胜出 V6.1。输出至少包括：

| 方法 | 30-episode makespan | 相对 Native | provider attempts | native P95 | work amplification | correctness/evidence |
|---|---:|---:|---:|---:|---:|---|
| Native | 实测 | 1.0x | 实测 | 实测 | 1.0x | native reference |
| V6.0 | 实测 | 实测 | 实测 | 实测/legacy gap | 实测 | replay + legacy evidence limits |
| V6.1 | 实测 | 实测 | 实测 | 实测 | 实测 | corrected proof |

冻结内容：policy、代码 hash、tests、runtime identity、history/prefix hash、命名空间规则和完整 launch command。V6.1 若仍未超过 Native，也必须选择“语义正确且 interference 最小”的最强诊断 policy启动主实验，不得为了追求正数而改变口径；同时在结果中明确它是 diagnostic/null candidate。

### Phase F：五 history 完整主实验启动

主表 methods：

1. Native serial；
2. 并行 baseline（现有 MAB `B1` 语义，使用本轮 local runner 与新 namespace）；
3. frozen selected V6.1。

五个 history 都必须进入调度计划。为避免三个方法互相污染同一 FCFS LLM 的 timed latency，construction timed runs 默认采用单个 campaign queue 串行执行；“五个 history 已启动”指五个 history 都已写入持久队列/manifest，首个 block 进入 RUNNING，后续 block 能由 supervisor 自动接续，而不是同时向一个 LLM 发起 15 个相互干扰的 run。

默认 block 顺序采用按 history 的方法轮转，并在 manifest 固定：

```text
h0: Native -> Parallel -> V6.1
h1: V6.1 -> Native -> Parallel
h2: Parallel -> V6.1 -> Native
h3: Native -> V6.1 -> Parallel
h4: Parallel -> Native -> V6.1
```

若现有 protocol 对顺序有冻结要求，以 protocol 为准并写入 manifest。每个 block 使用 fresh graph/vector namespace；block failure 不阻塞整条队列，supervisor记录失败、执行一次受限恢复或创建 fresh retry，然后继续可运行 block。

启动形式：

- 独立 tmux session，例如 `membind-v61-local-main`；
- supervisor PID、session、queue manifest、current block、heartbeat 写入 `state/`；
- 日志只写 `/data/predator/ly/Mem/experiments/local-qwen3-14b-awq-v1/v6_1_mab/logs/`；
- 不写现有 `saturated_fixed_work_baseline_v1_3/artifacts/*watch.log`；
- watchdog 监测 log/heartbeat/provider/Neo4j，但不在 timed block 中注入额外 LLM 请求。

本轮 agent 可以在满足以下条件后停止持续在线观察并交接后台任务：

1. 五个 history、三种方法的 15 个 block 全部进入持久 queue manifest；
2. tmux/supervisor 存活，服务 catalog 与 Neo4j 健康；
3. 当前 block 连续产生 episode/checkpoint/provider progress；
4. 至少跨越多个 heartbeat 周期，未出现 permit 泄漏、无进展、namespace 冲突或日志反复异常；
5. 自动续跑、失败记录和恢复命令已经实际验证或通过 provider-free 集成测试。

无需等待 15 个 block 全部完成才交接，但完成前不得将未跑出的单元格填成结果。

---

## 6. 监控、恢复与持续探索规则

### 6.1 心跳和稳定性

每个 running block 至少暴露：

```text
last_progress_at
history_id / method / episode_index
native_active
future_outstanding
provider_attempts_completed
last_checkpoint
process_pid
terminal_status
```

正常长 LLM 请求不能仅因日志暂时不增长就判死锁。stall 判断需要组合：进程存活、provider running/waiting、request interval、heartbeat state 和合理的最大 observed tail。

### 6.2 恢复优先级

1. read-only 检查 process/tmux/log/status/models；
2. 若服务未运行，执行 local runtime `start_all.sh` 并重新 preflight；
3. 若 runner 失败，保存 traceback 与 attempt state，修复后创建 fresh attempt；
4. 若单个 policy 死锁，先做 provider-free reproducer，再降低 `F/Q` 继续；
5. 若数据/namespace 不一致，禁止复用，创建 fresh namespace并重建向量索引；
6. 不清空共享数据库，不 kill 不属于本 campaign 的进程，不修改冻结 artifact。

### 6.3 Autoresearch 日志

每轮追加一条 JSONL：

```json
{
  "round": 1,
  "hypothesis": "F=1 and Q=0 hides extraction without placing future work ahead of native",
  "change": {"W": 1, "F": 1, "Q": 0},
  "scale": 4,
  "attempt_id": "...",
  "result": {"makespan_s": null, "native_p95_s": null},
  "correctness": "PASS|FAIL",
  "decision": "expand|keep|discard|debug",
  "next_reason": "..."
}
```

Controller 每轮只能依据已落盘 evidence 做下一决策；不允许根据未封存的终端印象覆盖结果。

---

## 7. 执行清单与状态

状态值：`TODO`、`RUNNING`、`DONE`、`FAILED_RETRYING`。

| ID | 工作项 | 状态 | Evidence |
|---|---|---|---|
| A0 | 创建本 workplan | DONE | 本文件 |
| A1 | local activation + READY + 两个 `/v1/models` | DONE | 2026-08-27 初始 probe；正式 block 前重采 |
| A2 | 解析 dataset/history 0/prefix 30 和现有 runner | DONE | context 0=`longmemeval_s*:3c7e614ecadaf1a6`，111 episodes；prefix=30 |
| A3 | 建立独立 campaign root/manifest/namespace | DONE | `/data/predator/ly/Mem/experiments/local-qwen3-14b-awq-v1/v6_1_mab/` |
| B0 | Native history 0 prefix 30 | DONE | fresh attempt `76130682ee53`（`prefix30-recovery-b0-1787783595`）30/30 durable，source 25 触发 5 个 turn partitions；`construction_seal.json`=`CONSTRUCTION_SEALED`；build makespan 3,202.69s；当前 attempt 无新增 `length`/`error` |
| B1 | 冻结 V6.0 history 0 prefix 30 | DONE | fresh attempt `57cbe4c487da` 已封存；30 episodes makespan=`6103.876204731s` |
| C0 | V6.1 scheduler/evidence failing tests | DONE | RED：缺失 admission/provider 模块；pytest collection 按预期失败 |
| C1 | V6.1 provider-free tests GREEN | DONE | scheduler/provider/MAB composition/runtime transport 聚焦测试 21 PASS；相关 legacy tests 通过 |
| C2 | local long-completion reliability/chunk merge | DONE | focused tests 21 PASS；source 25 node prompt 34,007 tokens -> 5 partitions, each 31.8K prompt + 8,192 output fits 64K |
| D0 | 2/4 episode smoke | DONE | 固定 multi-resource candidate 已完成逐级小样本验证 |
| D1 | 8/16 episode system-method exploration | DONE | bounded extraction stage separation 4/8/16 全 PASS；16 `9246ef932a04`=`1287.939039904s`，相对 Native16 `1.13076x`；382 transport、0 retry、32/32 exact replay，但 staged barrier 导致 TTFP=`795.054902720s` |
| D2 | 30 episode selected V6.1 | TODO | |
| E0 | prefix30 对比与 V6.1 freeze | TODO | |
| F0 | 五 history x 三 method queue manifest | TODO | |
| F1 | tmux supervisor RUNNING | TODO | |
| F2 | 多 heartbeat 稳定观察与交接 | TODO | |

此表在执行中实时更新。某个候选失败不把整份计划标记为失败；回到最近可用 policy继续探索。

---

## 8. 最终汇报口径

最终汇报先从最新本地结果讲价值，不从模块列表讲实现：

1. Native/V6.0 在新 14B 环境的实际端到端瓶颈；
2. V6.1 限制了多少 provider 排队、native P95 和额外 work；
3. exact replay 的局部收益有多少真正转化成 end-to-end makespan；
4. 五 history 主表当前完成/运行状态以及可信区间所需剩余 block；
5. 方法适用边界：共享 FCFS provider、`max_num_seqs=8`、Graphiti source-order semantics；
6. 对论文贡献的表述：不是“提取可复用”，而是“在状态依赖图写入中，以可审计 exact replay + foreground-aware admission 将 speculative work 约束在不会破坏 authoritative critical path 的范围内”。

所有性能 claim 同时报告 absolute seconds、speedup、work/provider attempts 与 correctness/evidence 状态，禁止只报最优单点百分比。
