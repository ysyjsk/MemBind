# MemBind V6：Graphiti-first Autoresearch + TDD 执行计划

> 版本：2026-08-23  
> 适用对象：接手 `/data/predator/ly/MemBind` 的 coding/research agent  
> 目标：在真实环境中用连续的小实验推导 V6，而不是预先认定某个机制；完成实现、证明、测试，并在完整 history `6071bd76` 上跑通同端点主实验。  
> 当前范围：Graphiti。只保留未来适配其他 memory system 的窄接口，不实现第二 adapter。

---

## 0. 给执行 Agent 的直接任务

你不是来照抄一个预设的 EDSR/事务/调度方案，也不是来写一份新的长计划。你的任务是在 **4–6 小时左右的连续 autoresearch** 中完成以下闭环：

1. 读取真实项目、历史实验和失败恢复记录，重建 V5 的 critical path；
2. 用最便宜的小实验区分“request 可复用性”“共享 vLLM 干扰”“native 输入本身需要缩减”三类可能；
3. 每轮先写测试或可证伪断言，再做最小修改；
4. 遇到反例时缩小复现、查对应顶会论文和公开实现、修订假设，再继续实验；
5. 从证据中选择一个最简单、可证明、真实减少 critical-path latency 的 V6；
6. 在固定的 `8000/8001`、Graphiti 0.29.3、真实 Neo4j 和完整 `6071bd76` 上，运行胜出 V6 及同环境 control；若收敛为 null，则运行 matched control 与最强负诊断，直到预注册 attempts 结束并封存；
7. 输出 V6 方法、证明、负结果、主实验结果和可复现命令。

**启动 tmux、进入 RUNNING、写出一版代码或一次 smoke 通过都不算完成。** Agent 要继续观察、分析和迭代，直到完整主实验产生合法终态；如果只能被当前权限、凭据或远端服务长期不可达阻塞，才提交可恢复的外部阻塞报告。

这个计划没有“触发阈值就停止研究”的 dead gate。只有语义安全与实验合法性是硬约束；某个候选不通过时，应转入诊断和下一假设，而不是终止整个 autoresearch。

最短执行路径只有五步：

```text
重算真实 V5 critical path
→ 2-source request-stability + 单 request interference
→ 由证据选择一个 phase / 一个机制
→ TDD 后按 2 → 6 → 12 source 扩大
→ 8000/8001 上完整 46-source matched main（正候选 control+V6；null 则 control+最强负诊断）
```

后文是这五步所需的环境、证明和恢复细节，不是要求同时建设十几个子系统。

---

## 1. 本轮要回答的唯一研究问题

> 在不改变 Graphiti 原生 source-order durable semantics 的前提下，哪一种最小的 write-path/runtime 变换——state-dependent speculation/validation、frontier-aware scheduling，或 native input/delta reduction——能缩短 authoritative native critical path，并在共享 vLLM 上保持可审计的 interference 边界？

V5 已经回答了“能否隐藏 source-derived extraction”。V6 不再把主要精力放在扩大 future window，而是研究 **native Graphiti 在当前 graph state 下才确定的请求**。

最终方法允许借用已有的 speculation、OCC、transaction、incremental view、LLM serving scheduling 等概念。论文贡献不要求发明全新术语；应来自：

- 对 Graphiti write path 的真实 bottleneck 与上界定位；
- 将证据选中的成熟机制迁移到实测 bottleneck seam，而不是预设 replay 为答案；
- 可检查的语义证明和 fail-closed fallback；
- 在真实 Graphiti 上可复现的端到端收益与适用边界。

### 防止方向继续复杂化

- 同一时间只允许 **一个 active hypothesis、一个目标 phase、一个主要代码变化**。
- 第一版不做通用 compiler、plugin registry、第二 backend、全局 conflict-graph engine 或新 memory architecture。
- 任何机制若不能解释“它消除了哪段实测 critical path”，就不进入代码。
- 任何论文若不能改变假设、实现、证明义务或下一实验，就只记入 Related Work。
- 不把多个微弱候选拼成“大而全 V6”；先让一个窄机制在一个 phase 上成立。
- `attributes/summary` 与 `node resolution` 是首轮候选，依据 microtrace 选择其一，不能同时重写两条路径。

---

## 2. 已知事实：这些是起点，不是待猜假设

真实 V5 history 为 `6071bd76`，共 46 个 source。历史证据给出：

| 观测 | 数值或结论 | 对 V6 的含义 |
|---|---:|---|
| B0 makespan | `1771.566 s` | 历史参考点 |
| V5 makespan | `1522.518 s` | 相对 B0 的单 trace 点估计为 `-14.06%` |
| V5 critical-path 分解 | `206.530 s` source-0 preparation + `1315.798 s` ordered native + `0.187 s` inter-native gaps + 约 `0.002 s` tail | 分解与实测只差约 `0.011 s`；future preparation 已基本全部隐藏 |
| future-ready slack | 最小 `103.881 s`，中位 `752.190 s` | 继续增大 window 的可见收益上限只有约 `0.187 s`，不是主要方向 |
| native share | `86.42%` | 只优化 V5 的 future preparation 无法越过 native ceiling |
| native phase | attributes/summary `697.13 s`；node resolution `519.50 s`；edge resolution `98.11 s` | 前两项占 native span 的约 `92.5%`，先在其中选择一个窄 phase |
| V5 replay | 92 个 capture 全部被 native 消费；仍有 337 个 native provider calls | extraction hit 已高，剩余瓶颈不是继续提高 extraction acceptance |
| logical calls | V5 429 attempts，B0 389 | 加速不能只看 latency；必须报告额外 work/tokens/GPU cost |
| source-0 anomaly | V5 extraction 约 `206.52 s`，历史 B0 同段约 `27.636 s`；一个 attributes 调用达 `344.922 s` 且与 7 个 future 请求重叠 | 共享 FCFS vLLM interference 是强假设，但当前端点不匹配，不能直接作因果结论 |
| correctness | graph hashes 不同，QA 为 `INVALID_RETAINED` | 当前 V5 不能提供正式语义质量 claim |
| endpoint | 历史 B0 用 `8000/8001`，该 V5 P9 用 `8002/8003` | 现有 14.06% 不是干净的同后端因果估计；V6 全部 live 实验必须回到 `8000/8001` |

额外注意：V5 preparation 外层 interval 累计约 `15,883.5 s`，除以 makespan 得到平均并发约 10.43，超过 future cap 7；因此这些 interval 含等待、嵌套和重叠，**绝不能被当作 provider work**。V6 必须使用 logical/transport attempts、tokens、server queue/running 指标以及必要时 GPU-seconds 近似来计量工作。

上述结果只来自一个 development-exposed history。它足以淘汰“继续堆 lookahead”作为主方向，但不足以证明 V6 的总体均值收益或论文级因果结论。

---

## 3. 真实执行环境合同

### 3.1 唯一合法执行位置

正式代码、Neo4j、vLLM 探测、GPU metrics 和 live artifacts 都在远端实验环境：

```text
REPO=/data/predator/ly/MemBind
VALIDATION=/data/predator/ly/MemBind/membind-validation
PYTHON=/data/predator/ly/MemBind/membind-validation/.venv/bin/python
```

Windows/Codex 控制 sandbox 只可用于发起、观察和取回结果，不能因为网络受限而把实验降级成本机 Graphiti、替代模型或模拟服务。Agent 首先执行 `pwd`、`hostname` 和 `test -d /data/predator/ly/MemBind` 判断自己是否已在实验主机；如果不在，只使用项目历史中已经确认的远端执行通道，不臆造新的 SSH、隧道或代理方案。

### 3.2 固定 provider 与 client

V6 所有 micro、prefix、control 和 main run 统一使用下列 **frozen expectations**。它们不是“此刻服务仍然如此”的证明；每个 live block 前必须重新采集 catalog、process argv/version 与 metrics identity。尤其 vLLM 版本、prefix/chunked flags 和 GPU/process mapping 不能只从旧 artifact 推断。

| 组件 | 固定值 |
|---|---|
| construction endpoint | `http://10.87.5.247:8000/v1/` |
| served model | `qwen3-32b-fp8` |
| vLLM | `0.26.0` |
| context | `65536` |
| serving policy | FCFS，prefix caching 开启，chunked prefill 开启，xgrammar，thinking 关闭 |
| construction GPU memory utilization | `0.75` |
| embedding endpoint | `http://10.87.5.247:8001/v1` |
| embedding model | `qwen3-embedding-0.6b` |
| embedding | pooling，BF16，dimension `1024`，context `32768`，max batched tokens `32768`，max seqs `128` |
| Neo4j | Community `5.26.0`，实验主机本机 `bolt://localhost:7687`、HTTP `http://localhost:7474` |
| Graphiti | pinned `0.29.3`，commit `021d3a57d511f21b10adaf7fa923bd5c1fce5e9d` |
| client | temperature `0`，top-p `1.0`，max tokens `16384`，seed `20260806`，JSON Schema，thinking false |
| Graphiti max coroutines | `8`；它是 runtime submission setting，不是 GPU capacity 证明 |

backend/client 的机器可读 contract 是：

- `/data/predator/ly/MemBind/saturated_fixed_work_baseline_v1_3/configs/frozen_backend_v1_3.json`
- `/data/predator/ly/MemBind/saturated_fixed_work_baseline_v1_3/configs/frozen_client_v1_3.json`
- `/data/predator/ly/MemBind/saturated_fixed_work_baseline_v1_3/configs/resource_policy.json`

其中 `resource_policy.json` 只定义策略，不是实际 GPU/process resource envelope；真实 resource identity 由本次 preflight evidence 提供。

V6 runner 必须显式记录并断言实际 runtime identity；出现 `8002/8003` 应立即判当前 attempt 配置错误并重新生成 fresh attempt，不能默默 override。现有 `p9_runner.py` 中为历史 GPU0 pair 写的 alternate override 不能直接继承到 V6。

`.env` 中的 API key 与 Neo4j credential 继续由项目已有 lazy env loader 读取。不得打印、复制、写入命令行、tmux pane、artifact 或报告；`.env.example` 只是字段模板，不能当作凭据或 client policy authority。

### 3.3 Python 路径

live runner 优先复用已真实运行过的解释器，并在启动前验证 import closure：

```bash
REPO=/data/predator/ly/MemBind
PY="$REPO/membind-validation/.venv/bin/python"
export PYTHONPATH="$REPO/saturated_fixed_work_baseline_v1_3/src:$REPO/saturated_fixed_work_baseline_v1_2/src:$REPO/membind-validation/src:$REPO/paper-eval-v3/src"
"$PY" -V
"$PY" -c 'import graphiti_core; import saturated_fixed_work_baseline_v1_3'
```

项目文档中的 targeted test 曾使用 `paper-eval-v3/.venv/bin/pytest`。Agent 应在 `/data` 上检查两个解释器及依赖，而不是凭 Windows sparse snapshot 判断环境损坏。若 live venv 已包含 pytest，统一用 `"$PY" -m pytest`；否则复用已存在的 `paper-eval-v3/.venv/bin/pytest` 并把解释器 identity 写入 test evidence。不要为了一个 import error 随意重建环境或升级 Graphiti/vLLM。

### 3.4 direct/no-proxy 网络规则

固定：

```text
NO_PROXY=127.0.0.1,localhost,10.87.5.247
no_proxy=127.0.0.1,localhost,10.87.5.247
```

正式 preflight 复用 `saturated_fixed_work_baseline_v1_2.services::probe_model_catalog`。它使用 `ProxyHandler({})` 禁用代理，并检查 model id、max model length、root 与 response hash；`curl 200` 只能作即时诊断，不能替代该 evidence。

如果控制 sandbox 在 TCP 建立前返回 `Operation not permitted`：

1. 记录为 `SANDBOX_NETWORK_VISIBILITY_LIMITATION`，不是 vLLM crash；
2. 阅读 `/data/predator/ly/MemBind/membind-validation/GLOBAL_MEMORY.md` 的网络边界记录；
3. 从真实 `/data` 实验主机执行相同 direct/no-proxy `/v1/models` 与 `/metrics` probe；
4. 若目标侧健康，继续在目标侧的 tmux 运行，不换端口、不走外网 proxy、不启动本机替代服务；
5. 若当前工具需要网络权限，申请最小网络权限后重复完全相同的 preflight；等待期间继续 offline TDD、trace diff、证明与 runner 测试。

正式 timed run 中不并发执行额外网络 probe，也不从 makespan 中减去 RTT。

### 3.5 服务与资源边界

- 默认只复用和只读探测 `8000/8001` 与 Neo4j；历史 authority 不允许擅自 restart/kill shared service。
- 不因看到 GPU busy 就 kill 进程。先识别 tmux、PID、完整 argv、GPU UUID、当前 client、output growth 和 seal condition。
- `/metrics` 至少检查 `vllm:num_requests_running` 与 `vllm:num_requests_waiting`；正式实验同时采样可用的 queue、running、KV/cache、prefill/decode 指标。
- Neo4j 先做 HTTP/Bolt/version/`RETURN 1`/active-transactions 只读检查；只操作本 attempt 的 fresh namespace，绝不全库清空。
- 历史 `8000/8001` 启动命令含 6 小时 watchdog。开始几小时 autoresearch 前必须读取真实 PID elapsed/argv，确认 main run 不会撞上自然到期。watchdog 中止记为 infrastructure failure，不能算方法失败，也不能擅自延长或改服务器参数。

---

## 4. 项目材料：首个 probe 必读与按症状路由

不能在准备阶段通读所有长文档。首个 2-source probe 前只读以下相关段落和实际调用链；冲突时以当前源码、机器可读配置和 sealed artifact 为准：

1. `membind-validation/GLOBAL_MEMORY.md` 中 Codex sandbox network boundary、long live tmux、failure recovery 三段；
2. `saturated_fixed_work_baseline_v1_3/{PROTOCOL.md,BLOCK_LIFECYCLE_CONTRACT.md}` 的 endpoint、fresh namespace 与 clock boundary，以及三个 frozen config；
3. 真实 V5/P9 的 `history_result.json`、`block_metrics.json`、`runtime_identity.json`、`native_trace.jsonl`、`transport_attempts.jsonl`、`admission.jsonl`：  
   `saturated_fixed_work_baseline_v1_3/artifacts/sfwb-v1-3-v5-queue-20260822-032328/p9-history-6071bd76-gpu0-20260822/`
4. 当前实际 live composition：`membind_v5/p9_runner.py`、`live_runner.py`、request transport wrapper、TraceRecorder 和 telemetry sampler。

其余材料按观察到的症状再读：

- 需要历史解释/失败恢复：`MemBind_CURRENT_EXPERIMENT_REPORT.md` 与 `WORKPLAN_V5_FINAL.md` 对应章节；
- 需要 matched reference：sealed B0 `sfwb-v1-3-formal-baseline-20260822-002/` 的 seal、result 与目标 history trace，不先扫全树；
- request drift 定位到某 phase：只读 pinned Graphiti 该 phase、其调用者、`_process_episode_data` 和相关 tests；
- method/novelty 决策：按第 10 节每轮最多读 1–3 篇 primary source。

Agent 需要自己检查当前 repo 的 `git status`。已有用户修改不得覆盖；V6 使用 append-only 新模块或最小、可审计的现有 seam 修改。所有 sealed baseline/V5 artifact 永远只读。

---

## 5. 最小方法空间：先区分，再选择

不要一开始实现全部方案。方法空间有两条正交轴：

1. **语义 treatment**：exact replay、certified partial reuse、native input/delta reduction，或 no semantic transform。
2. **共享 provider 控制**：只要存在 timed speculation，就始终使用最小的 frontier-aware admission 与 bounded outstanding；Probe B 用来校准 horizon/token budget，而不是决定是否可以完全取消控制器。

首轮 treatment branches 如下：

| 真实观测 | 单一主 treatment | 最小机制 |
|---|---|---|
| native 完整 request 在旧 snapshot 上稳定 | Exact native-demand replay | shadow 只计算 oracle artifact；native Graphiti 真实生成 request 后做完整 identity match；hit replay，miss 原生调用 |
| exact request 失配，但只来自少量可验证 graph reads/candidate versions | Certified partial reuse / selective repair | 记录完整 read footprint；native seam 重读并验证；只重算失效的最小 sub-DAG |
| request 失配广泛，或 validation 几乎等同重做 | Native input/delta reduction | 局部 candidate view、affected set 或 incremental summary；直接减少 native provider work，不继续扩大 speculation |
| semantic transform 无收益，但 queue/interference 本身可改善 | Scheduling-only control，或 null | 保持原生 work；只检验 JIT/frontier-aware admission。若也无净收益，形成可复现 null result |

每轮只允许一个主 treatment。只有单独效应已在 L3 被识别后才允许组合 replay/certificate 与更强的 scheduling control，并必须保留相应的单机制中间 arm。不能把多个变化一起打开后再把收益归给“V6”。

若这些候选均无保守净收益，允许形成明确的 null result。不能为了必须有复杂“新方法”而放宽 request equality、改变 Graphiti 语义或隐藏负结果；null 分支按第 15–16 节的独立完成条件收束。

### 第一轮只做两个判别 probe

#### Probe A：request stability

- 取真实 history 的 2 个 source；必要时扩到 4 个，不能直接跑 46 个。
- 在 side-effect-isolated shadow 中执行一个候选 native phase，只观察将发给 provider 的完整 request。
- pinned Graphiti 中第一处 persistent graph write 位于 `_process_episode_data`；首个 observation-only shadow 可在进入该函数前终止，但必须用测试证明此前没有 driver write、正式 namespace mutation、共享 mutable-object 泄漏或 durable publication。embedding/LLM 等允许的外部工作仍要被完整计量。
- native Graphiti 在正式 frontier 上自己生成真实 request；先不 replay，只比较：messages、顺序、schema/tools、model、sampling、chat-template kwargs、candidate ordering、state/version inputs、code/config identity。
- 现有公开 V5 artifact 只有 hash、token 和 timing，不能伪造还原 wire payload。需要时在现有 transport wrapper 增加受控的 **private full-request capture**；公开 artifact 只保存 hash/分类，private 文件限制权限且不得进入报告。
- 将 mismatch 分类为：timestamp/UUID、prompt formatting、candidate order、ANN candidate set、prior summary、graph version、真正 semantic state drift。

输出不是“hit rate 一个数”，而是：哪一个 phase 的哪些字段稳定、哪一个字段首先使 request 失效、它是否可完整验证。

#### Probe B：shared-vLLM interference

- 在同一 `8000` 上选择一个真实 foreground request，分别做 isolated 与“叠加一个真实 speculative request”的 AB/BA 小实验。
- 先只测试一个 prefill-heavy 或 V5 source-0 代表 pair；不要一上来扫 `k=1,2,4,8`。
- 保持相同 request、idle 条件、endpoint、model、client、cache policy。先 qualification 当前 vLLM/client 是否支持唯一 `cache_salt`；支持时每个 timed attempt 使用预注册且不跨 attempt 复用的 salt，失败则改用 counterbalanced order 并显式标记 cache-order confounding。
- 当前稳定 telemetry 主要是 process-global queue/running、KV、prefix hit/query、preemption 与 token counters。TTFT/TPOT、per-request cached tokens、server-side prefill/decode 只有 qualification 后才能使用；否则写 `NOT_OBSERVABLE`，不能估算或伪造。
- 记录 target latency、输入/输出 tokens、spec completion，以及所有已经 qualification 的 server metrics。
- 该 probe 只更新局部 interference 假设，不直接成为论文总体结论。

Probe A 决定“能否复用”，Probe B 决定“能否安全提前跑”。两者共同决定首个 V6 实现分支。

---

## 6. Autoresearch 主循环

每轮目标 25–40 分钟；时间盒用于促使快速学习，不是到点终止：

```text
读取上轮真实 evidence
→ 写一个可证伪 hypothesis 和 null
→ 指出它应减少的 critical-path span
→ 先写 failing test / experimental assertion
→ 实现一个最小 probe 或最小代码变化
→ scoped GREEN
→ 用 1–2 个最便宜的真实样本测量
→ 主动构造最强反例
→ 只针对已观察到的症状阅读 1–3 篇 primary paper/source
→ 支持：只扩大一个尺度
  证伪：缩窄或切换候选
  歧义：只补一次新的观测量，然后重测
→ 写 ledger，进入下一轮
```

禁止把“原样再跑一次看看”当作修订。同一歧义第二次出现时，必须新增可区分的观测或更换假设。外部服务偶发失败按 evidence-based classifier 归类：例如 TCP 前 sandbox `EPERM`、provider process/watchdog exit、control 与 candidate 都出现相同 endpoint failure、或 Neo4j canary failure。保留失败 attempt；环境恢复后允许一次有界的同配置重试，不要求先给出不可能的逻辑“证明”。

每轮追加一行 `V6_AUTORESEARCH_LEDGER.jsonl`：

```json
{
  "iteration": "R03",
  "observation": "...",
  "hypothesis": "...",
  "null_hypothesis": "...",
  "predicted_critical_path_change": "...",
  "test_written_before_code": "path::test_name",
  "single_change": "...",
  "endpoint_identity": "8000/8001 + hashes",
  "artifact_root": "...",
  "raw_artifact_hashes": {},
  "result_and_uncertainty": "...",
  "strongest_counterexample": "...",
  "paper_or_source_used": "...",
  "hypothesis_revision": "...",
  "next_cheapest_discriminating_test": "..."
}
```

同时维护 `RUN_STATE.json`，至少写：当前 iteration、active hypothesis、git commit/diff hash、own tmux sessions、running attempt、last sealed attempt、下一动作。这样 Agent context 被压缩、SSH 断开或任务恢复时，能从 artifact 继续，不重新猜状态。

---

## 7. TDD 纪律：安全约束是硬的，失败后的研究不是停止的

### 7.1 每个机制必须先攻击这些性质

1. 任意 request identity 字段变化必须 miss；不得为了 hit rate 使用不完整 whitelist。
2. replay hit 不得产生 provider transport；miss 必须恰好产生一次原生 logical provider call。
3. native Graphiti 真实代码决定 demand、call order 和 control flow；不能只用 source/phase ordinal 猜绑定。
4. speculative graph/index/publication effect 不得进入 authoritative namespace。
5. future episode 在其 source-order publication 前不可见。
6. durable publication 仍严格按 source order。
7. wrong source、phase、call ordinal、model/config/code hash 的 artifact 必须拒绝。
8. crash、timeout、duplicate delivery 不得造成重复 publication 或 partial result 被标成成功。
9. ANN phantom、candidate reorder、summary version 改变必须 miss 或进入经过证明的 repair，不能误 replay。
10. frozen oracle 下，逐 episode effect trace 与 canonical graph 必须和 native serial reference 一致。
11. live stochastic oracle 在第 11 节条件纯度假设成立时，最多将结果映射到某条允许的 nondeterministic serial trace；不声称保持原 response distribution，也不声称跨运行 bitwise graph equality。
12. timer 包含所有 semantic speculation、request/certificate validation、queueing 和 fallback；最终 canonical export、QA、artifact hashing 与 seal 位于 `T_build` 外。

若出现 false accept，立即缩小可复用 seam，并先新增 reproducing RED。**一次 false accept 比一百次 hit 更严重。**

### 7.2 测试节奏

- 改 V6 代码前先保存当前 scoped/full test baseline。只要求修复 V6 引入的新 regression；既有失败隔离、记录并判断是否影响本次路径，不能把数小时耗在无关历史问题。
- 每轮：只跑新 RED、受影响 V5 core tests 与一个邻接 regression。
- 候选进入真实 6-source 前：跑全部 V6 tests、核心 V5 binding/equivalence/p9 runner tests。
- 进入完整主实验前：跑 v1.3 全部 provider-free tests 和受影响的 telemetry tests，保存 XML/log/hash。
- 全套失败不是“STOP”；分类为 code regression、fixture drift、missing optional dependency 或真实环境问题，修复后从最早受影响的 RED 继续。

可复用的现有回归重点：

```text
test_membind_v5_core_red.py
test_membind_v5_binding.py
test_membind_v5_equivalence.py
test_membind_v5_graphiti_native.py
test_membind_v5_p9_runner.py
test_membind_v5_semantic_quality_gate.py
test_protocol_contracts.py
paper-eval-v3/tests/test_apc_vllm_telemetry.py
```

V6 新测试以 `test_membind_v6_*` 命名，但不要复制整套 V5 runner。优先复用 V5 的 `P9FullConfig` 思路、durable journals、partial failure trace、transport evidence、canonical exporter 和 lifecycle；不要使用旧 `simple_campaign.py` 或 provider-free scripted runner 冒充真实 live path。

---

## 8. 小实验阶梯：一次只增加一个尺度

这不是通过/失败后停止的 gate，而是 evidence ladder。某层失败时，在该层产生最小反例并改假设；不带着未知错误扩大规模。

| 层级 | 规模 | 目的 | 典型时长 |
|---|---:|---|---:|
| L0 | 纯离线历史 trace | 重算 V5 DAG、future slack、native span、phase 排名；reducer 必须还原 `1522.518 s` | 10–20 min |
| L1 | frozen/scripted oracle，2 episode | TDD：identity、replay/fallback、side-effect isolation、crash/order | 数秒–数分钟 |
| L2 | 1–2 个真实 source | 验证 8000/8001、Graphiti seam、private request capture、Neo4j fresh namespace、metrics | 2–8 min |
| L3 | 6-source prefix | 运行最小 2×2 factorial diagnostic；有至少两个随机化 block 时才估计初步 treatment effect | 15–35 min |
| L4 | 12-source prefix | 只把 L3 的赢家扩大一次；检查 hit 分布、work amplification、state divergence | 15–35 min |
| L5 | 完整 46-source `6071bd76` | 正候选按预注册 ABBA/BAAB 运行 matched control 与 V6；null 分支运行 matched control 与必要的最强负诊断 | 约 1–2 h，依真实服务为准 |

L3 的理想四臂为：

以下 “spec/replay” 只指本轮新增的 V6 treatment；现有 V5-compatible extraction、instrumentation、lifecycle 与 client config 在四臂中保持不变。

| Arm | timed speculation | replay | 作用 |
|---|---|---|---|
| `Y00` | 无 | 无 | matched control |
| `Y10` | 有，但 artifact discard | 无 | 单独测 speculative traffic/interference |
| `Y01` | 无；timer 前预装 frozen artifact | 有 | 只注入与 `Y11` 相同 eligibility policy/命中集合；否则仅是 oracle upper bound，不是 replay effect |
| `Y11` | 有 | 有 | 完整候选 |

每个 block 在运行前写机器可读 treatment manifest：semantic treatment、admission policy、phase、horizon/budget、cache salt/policy、arm order、commit 与 config hashes。若最终 V6 同时含 reuse 与 admission，L3 至少保留一个单机制中间 arm。单次四臂只称 factorial diagnostic；至少两个独立随机化 block 后才报告初步 paired effect 与不确定性。

若四臂对当前候选不成立，保留能区分 traffic、reuse 与 interaction 的最小子集，并解释缺失 arm。不要在完整 46-source 上反复扫参数；horizon、budget、phase 的选择应在 L2–L4 完成。

---

## 9. 几小时执行节奏

### 0:00–0:30：恢复状态与冻结事实

- 阅读第 4 节材料，记录 git/environment/tmux/service inventory。
- 只读确认当前 `8000/8001`、Neo4j、GPU、watchdog 剩余时间、是否有其他 client。
- 用现有 artifacts 重算 V5 critical path；为已知分解写 reducer regression。
- 建立新 append-only root：  
  `saturated_fixed_work_baseline_v1_3/artifacts/v6-autoresearch-<UTC>/`
- 写 `RUN_STATE.json`、ledger 首行和 V6 proof skeleton。

### 0:30–1:20：两个最小判别 probe

- 先做 2-source request-stability capture；不实现完整 replay。
- 再做一个真实 request 的 isolated vs one-spec interference AB/BA。
- 只在结果需要时添加一个观测字段，不做大参数 sweep。

### 1:20–1:45：症状驱动调研

- 根据已观察到的 mismatch 或 interference 选择最多 1–3 篇 primary source。
- 每篇写一张 decision card：  
  `症状 → 假设 → 借用机制 → Graphiti 不满足的假设 → 最小实验 → 结果如何改变路线`。
- 查公开源码/tests，优先于只读论文摘要。

### 1:45–2:45：实现一个 phase 的最小 V6

- exact stable：实现 native-demand identity check + replay/fallback。
- stable but harmful：增加最小 JIT/foreground-first controller；不要改 vLLM 或换端口。
- local drift：只实现该 phase 的完整 read certificate 与 selective invalidation。
- broad drift：停止堆 replay 条件，做一个 candidate-view/delta reduction probe。

### 2:45–3:30：correctness attack

- frozen oracle differential、state/version counterexample、crash/retry/order、no speculative writes。
- 修复时缩小 seam，不扩大 equality。
- 跑 scoped regression。

### 3:30–4:30：L2 → L3 → L4

- 1–2 source smoke 后做 6-source factorial diagnostic；资源允许时完成第二个随机化 block。
- 若证据方向仍成立，只扩大到 12 source；每次只改规模。
- 选择主实验候选并冻结 commit、policy、参数与预注册预测。

### 4:30 以后：完整主实验与收尾

- 正候选在 detached tmux 中按预注册 `ABBA` 或 `BAAB` 运行完整 matched control 与 V6；null 分支按第 15 节运行 matched control 与必要的最强负诊断。
- Agent 持续观察直到预注册 attempts 合法结束；不能在启动后交差。若正候选的服务预算只能支持一个 AB/BA pair，本轮结果降级为 qualification point，不用于宣称正收益。
- 生成 reducer、proof、method/report 和下一阶段四-history campaign 建议。

如果 vLLM latency 使时间超过 6 小时，继续完成已启动的完整实验；不能因计划时间盒而杀掉合法 run。反之，如果某个候选 20 分钟内被强反例证伪，应立即切换，不把剩余时间耗在重复验证。

---

## 10. 文献调研路由：观察到什么才读什么

| 观察症状 | 首读 primary work | 只迁移什么 |
|---|---|---|
| native frontier 高、依赖关系不清 | [Parrot, OSDI 2024](https://www.usenix.org/conference/osdi24/presentation/lin-chaofan)、[Agentix, NSDI 2026](https://www.usenix.org/conference/nsdi26/presentation/luo) | application/program-level dataflow 与 frontier-aware scheduling；V6 不能把通用 agent scheduling 当 novelty |
| speculation 命中但拖慢 foreground | [Sarathi-Serve, OSDI 2024](https://www.usenix.org/conference/osdi24/presentation/agrawal) | token/prefill interference、headroom、foreground protection；DistServe/Llumnix 只有允许服务端架构变化时才作实现参考，当前不新增 8002/8003 |
| lossless/idle-time agent speculation 与本方法过近 | [Speculate with Memory](https://arxiv.org/abs/2607.12236) | 明确差异必须落在 state-dependent Graphiti write oracle、request/read validation 与 shared-provider interference，而不是“有 speculation” |
| 想声称首次发现 memory construction bottleneck | [Agent Memory systems characterization](https://arxiv.org/abs/2606.06448) | 已有工作约束 novelty；本轮只能主张 Graphiti exact native frontier 的定位、上界和机制 |
| stale request / validate-before-commit | [Aria, VLDB 2020](https://www.vldb.org/pvldb/vol13/p2047-lu.pdf)、[Speculative Actions](https://openreview.net/pdf?id=P0GOk5wslg) | read validation、actor/speculator separation、fallback；不把事务边界本身包装成 novelty |
| request drift 只影响局部依赖 | [Differential Dataflow, CIDR 2013](https://www.cidrdb.org/cidr2013/Papers/CIDR13_Paper111.pdf)、[DBToaster, VLDB](https://www.vldb.org/pvldb/vol2/vldb09-1042.pdf) | affected set、delta recomputation；只有实测 state footprint 局部时才实现 |
| Graphiti consolidation/candidate work 太大 | [Graphiti](https://arxiv.org/abs/2501.13956)、[A-MEM](https://arxiv.org/abs/2502.12110)、[Mem0](https://arxiv.org/abs/2504.19413) | 局部候选、ADD/UPDATE/NOOP、memory evolution 的写路径设计；不能把质量结果自动当 latency 结果 |
| 加速后 quality/freshness 下降 | [LongMemEval, ICLR 2025](https://proceedings.iclr.cc/paper_files/paper/2025/file/d813d324dbf0598bbdc9c8e79740ed01-Paper-Conference.pdf) | temporal update、abstention、freshness evaluation；首轮不新建 benchmark |

[MemTX](https://arxiv.org/abs/2607.23929)、[MemTxn](https://arxiv.org/abs/2607.27834)、[Continuity Kernel](https://arxiv.org/abs/2608.11632) 可用于 transaction/commit novelty boundary 与 crash proof obligation，但其存在不要求回避这些概念；如果机制适合 Graphiti，可明确引用并迁移。顶会正式论文优先，preprint 用于边界，不把未经复现的结论当 ground truth。

每轮文献调研必须结束于一个可执行的小实验。连续两轮没有改变假设或实验设计，就关闭该文献分支。

---

## 11. V6 必须同时形成的理论证明

### 11.1 串行语义模型

Graphiti 的 provider calls 是 adaptive 的：后续 request 数量、内容与控制分支可能依赖前面 response，不能把 request 集合假定为预先固定。定义：

\[
(S_i,\tau_i)=\operatorname{Exec}_{\Omega}(S_{i-1},e_i)
\]

\[
q_{i,t}=g_t(S_{i-1},e_i,r_{i,<t},\ell_{i,<t}),\qquad
r_{i,t}\sim\Omega(q_{i,t})
\]

其中 `S_i` 是 source `i` durable 后的 authoritative state，`\tau_i` 是该 source 的 adaptive call/effect trace，`\ell` 是此前 local control/parse/read trace，`\Omega` 是 oracle contract。正确性先对同一 source 内的 call trace `t` 归纳，再对 source `i` 归纳。

V6 的 speculative artifact 只能来自 side-effect-isolated shadow，包含：

```text
complete request identity
model/config/code/provider-launch identity
response
source/phase/call identity
read/version certificate（仅 partial-reuse 分支）
ready timestamp
```

native execution到真实 callsite 时：

1. 仍由原生 Graphiti 生成 `q_native` 并决定是否 demand；
2. exact branch 只有 `identity(q_shadow) == identity(q_native)` 才 replay；
3. certificate branch 只有全部 read/version predicate 重新验证通过，且可证明生成相同 request/effect 时才复用或局部 repair；
4. 其他情况执行一次原生 provider call；
5. 只有原生 Graphiti 的 source-order path 可以 durable publish。

### 11.2 正确性 theorem 的最低形式

若满足：

- shadow side effects 完全隔离；
- request identity 覆盖所有影响 oracle 分布的字段；
- oracle 的响应语义只由完整 request 与 frozen provider identity 决定，且提前调用本身没有影响后续 Graphiti 执行的 durable semantic side effect；
- native demand 与 control flow 不被 speculation 决定；
- miss 完整 fallback；
- publication 按 source order；

则 failure-free theorem 可按“call trace、再 source”归纳：在 frozen deterministic oracle 下，V6 的逐 source effect/state 与 native serial 完全相同；在 live stochastic oracle 下，最多证明结果可映射到该 oracle contract 允许的某条 nondeterministic serial trace，不保证保持原 response distribution，也不保证与另一独立运行 bitwise 相同。若 provider hidden state、batch order、session state或调用本身的语义副作用无法纳入 identity/assumption，就不能声称 live replay 的 serial semantic equivalence，只能报告 publication/order 等较弱 invariant。

crash theorem 必须单列，并额外假设/验证：atomic durable-frontier advancement、idempotent completion、side-effect fencing、partial artifact 不可被 success path消费。不能用 failure-free theorem 自动推出 crash safety。

若最终方法不是 replay，而是 candidate-view/delta reduction，则必须重新写语义定理，证明缩减后的 request/effect 与原生 Graphiti 等价；不能沿用 replay theorem 冒充证明。

若最终方法是 scheduling-only，则证明目标改为 logical-work conservation、native demand/order不变、无 dropped/duplicate request、durable publication不变；不应强套 request-replay theorem。

### 11.3 性能模型

性能用集合级 counterfactual，不能把并行调用 latency 逐项相加。令 `Z` 为 `Y11` 中按照同一预注册 eligibility policy 实际 exact/certified 且 timely-ready 的集合：

- `T00`：matched no-spec/no-reuse；
- `Tmagic(Z)`：相同 `Z`、相同 replay/validation overhead，但 artifact 被视为 timer 开始时已就绪且没有 timed speculative traffic；
- `T11`：完整在线候选。

\[
B(Z)=T_{00}-T_{\mathrm{magic}}(Z)
\]

\[
C_{\mathrm{online}}(Z)=T_{11}-T_{\mathrm{magic}}(Z)
\]

\[
T_{00}-T_{11}=B(Z)-C_{\mathrm{online}}(Z)
\]

`B(Z)` 是该实际 eligibility set 的 replay/transform opportunity；`C_online` 含在线准备、调度与 interference 的总成本，`Y10` 再帮助定位其中的 speculative-traffic harm。若 `Y01` 不能严格复现 `Y11` 的 eligibility set，它只能给 oracle upper bound。

每调用的 `H_j` 只能作 DAG attribution；除非 DAG 证明互不重叠且同属一条链，否则不得求和。完美 V6 上界也只能由重算后的 joint critical-path span 得出。额外 token/GPU work单独报告，不塞入 latency 等式。

共享 FCFS vLLM 上，client-side admission 只能保证 bounded outstanding，不能据此给出有限 latency bound，因为已经 admitted 的请求不可抢占且 service time 未被证明有界。只有验证 provider-side priority/reservation，或验证 prompt/output/chunk 上限与取消语义后，才能陈述 deterministic interference bound；本轮默认称为 empirical interference risk。

主实验前，`V6_METHOD.md` 必须写清：假设、correctness theorem、performance opportunity bound、无法保证的部分，以及哪些 metrics 会证伪该方法。

---

## 12. 后台执行与恢复：统一使用 tmux

任何预计超过 5 分钟的 test、prefix 或 full live run 都放 detached tmux。每个 attempt 使用新的 session、run id、namespace 和 artifact root。autoresearch campaign root 可预先写 ledger/RUN_STATE，但绝不能直接传给 P8/P9-derived runner；runner 的 `--output-root` 必须是尚不存在的叶子目录。

推荐模板；`ENTRYPOINT` 与 CLI 参数必须先通过真实源码/`--help` 验证，不能假装尚未实现的 `run_v6.py` 已存在：

```bash
set -euo pipefail
REPO=/data/predator/ly/MemBind
PY="$REPO/membind-validation/.venv/bin/python"
CAMPAIGN_ROOT="$REPO/saturated_fixed_work_baseline_v1_3/artifacts/<existing-v6-autoresearch-UTC-root>"
ATTEMPT_CLASS="micro"  # 仅可改为 prefix、main/control、main/candidate 或 main/null_diagnostic
RUN_ID="v6-rNN-$(date -u +%Y%m%d-%H%M%S)"
SESSION="membind-$RUN_ID"
ATTEMPT_PARENT="$CAMPAIGN_ROOT/$ATTEMPT_CLASS"
RUN_ROOT="$ATTEMPT_PARENT/$RUN_ID"
LOG="$CAMPAIGN_ROOT/logs/$RUN_ID.log"
ENTRYPOINT="<verified V6 entrypoint under $REPO>"

test -d "$REPO"
test -x "$PY"
test -d "$CAMPAIGN_ROOT"
test ! -e "$RUN_ROOT"
mkdir -p "$ATTEMPT_PARENT" "$CAMPAIGN_ROOT/logs"
tmux has-session -t "$SESSION" 2>/dev/null && exit 3

# 用真实 --help/tests 替换 ...；不得删掉 fresh --output-root。
V6_ARGS=(--output-root "$RUN_ROOT" ...)
printf -v EXEC_CMD '%q ' env PYTHONUNBUFFERED=1 \
  NO_PROXY=127.0.0.1,localhost,10.87.5.247 \
  no_proxy=127.0.0.1,localhost,10.87.5.247 \
  PYTHONPATH="$REPO/saturated_fixed_work_baseline_v1_3/src:$REPO/saturated_fixed_work_baseline_v1_2/src:$REPO/membind-validation/src:$REPO/paper-eval-v3/src" \
  "$PY" -u "$ENTRYPOINT" "${V6_ARGS[@]}"
printf -v QUOTED_LOG '%q' "$LOG"
COMMAND="set -o pipefail; $EXEC_CMD 2>&1 | tee -a $QUOTED_LOG; exit \${PIPESTATUS[0]}"
tmux new-session -d -s "$SESSION" -c "$REPO" \
  "/bin/bash -lc $(printf '%q' "$COMMAND")"

tmux list-panes -t "$SESSION" -F '#S:#I.#P #{pane_pid} #{pane_current_command} #{pane_current_path}'
tmux capture-pane -pt "$SESSION" -S -100
```

runner 的 success `attempt_status.json` 必须在 success seal 前写入并被 seal inventory 绑定；失败路径写 failure status且不得产生 success seal。不能在 `finally` 中于 seal 后继续修改 runner root。tmux log 位于 runner root 的兄弟目录，因此 seal 后继续写 console 不会污染 sealed tree。session 消失但没有合法 seal/failure status 视为 crash，不视为成功。

观察：

```bash
tmux ls
tmux list-panes -a -F '#S:#I.#P #{pane_pid} #{pane_current_command} #{pane_current_path}'
tmux capture-pane -pt "$SESSION" -S -200
tail -n 200 "$LOG"
ps -eo pid,ppid,etimes,cmd
ss -ltnp
```

Agent 每 30–60 秒读取一次增量日志或 artifact growth；不要快速 busy-poll，也不要用一次超长 blocking sleep。启动后继续工作和汇报，直至合法终态。

恢复规则：

- session 仍在：capture/tail，不能重复启动同一 run。
- session 消失：先读 lifecycle、checkpoint、failure、seal 和日志尾，再查 PID。
- 只有 V6 runner 已实现且测试过 completed-block rollback/resume contract 时才 resume。
- 否则保留 partial attempt，换新 run id、fresh namespace 和 root；绝不向旧 partial root 续写成成功。
- 只允许停止明确属于本次 V6 的 invalid session/process；不得停止未知 tmux、baseline、8000/8001 或 Neo4j。
- live attempt 运行期间不修改它所使用的代码/配置；可阅读论文和分析只读 trace，下一修改等当前 attempt 结束或被合法判 invalid 后进行。

---

## 13. 故障不是逃离研究的理由

| 症状 | 先做什么 | 继续路线 |
|---|---|---|
| assertion / semantic mismatch | 保存最小 trace，定位第一 divergence，写 reproducing RED | 缩小 seam、修 identity/certificate 或切换下一候选 |
| exact hit 低 | diff 完整 request，按字段分类 drift | local drift 做 certificate；broad drift 转 native input reduction |
| foreground inflation 高 | 用一个 target/spec pair 重现，查 queue/prefill/cache | 缩短 speculative quantum、JIT、in-flight coalescing、foreground-first；仍不用 8002/8003 |
| 6/12 source 无 E2E gain | 重建 critical path，区分 off-path savings、validation 与 interference | 选择新 phase或接受 null，不扩大到参数 sweep |
| provider malformed JSON/retry | 区分 logical call 最终结果与 transport attempts | 用最小真实 request复现，查 Graphiti/vLLM源码和历史修复；不静默改 schema/model |
| sandbox network error | direct/no-proxy + `/data` 主机侧 probe | 控制端错误记 visibility limitation，目标侧继续；等待权限时继续 offline work |
| 8000/8001 目标侧失败 | 保存 evidence，查监听/PID/argv/tmux/log/watchdog | 无 service-admin 权限时继续 offline；有明确权限才按冻结方式恢复，绝不换端口 |
| Neo4j 失败 | HTTP/Bolt/version/transaction/canary 只读检查 | 仅处理本次 owned namespace；不清全库、不 kill shared daemon |
| Python/import 失败 | 核对真实 venv、PYTHONPATH、pyproject、历史 launcher | 最小 import/test 修环境；不随意升级依赖 |
| tmux/SSH 断开 | capture/tail/PID/artifact/seal | 仍运行则继续观察；partial 按恢复合同处理 |

同一外部故障要做“带新证据的恢复轮次”，不是死循环重复同一命令。可复用项目的 `recovery_probe.py` / `external_diagnosis.py` 模式：记录 hostname、GPU/process/environ、tmux、service、log hashes，并自动 redact secret。

仅以下情况允许最终报告外部阻塞：

- 缺少完成 live 所必需的用户 authority/credential；
- `/data` 目标环境长期不可达：至少三次有新 evidence 的目标侧/owner-status probe、跨不少于 15 分钟窗口，仍无法建立目标侧观察或执行；单纯控制 sandbox socket error不满足此定义；
- shared service 确认停止，但当前 authority 明确禁止恢复。

即使如此，也要先完成所有不依赖 live 的 TDD、trace analysis、proof 和 runner dry-run，写出精确的恢复命令、所需最小权限和当前 artifact state。不能用“环境有问题”代替根因调查。

---

## 14. 完整 `6071bd76` 主实验协议

本节的 counterbalanced candidate protocol 用于正结果分支；若小实验收敛为 null，按第 15 节只运行足以封存负结论的 matched control/最强负诊断，不强造四个“V6” attempts。

### 14.1 runner 必须补齐的最小能力

现有 V5 CLI 只能完整四 histories 或截断 smoke；不能用 `--smoke-history` 冒充单个完整 history。V6 应在复用 P9 lifecycle 的基础上新增清晰、测试过的语义：

```text
--history-id 6071bd76 --full-history
--policy matched-control | v6
--output-root <fresh root>
--run-id <unique id>
```

实际脚本名由 Agent 根据仓库决定。两个 policy 必须从 **同一 executable、同一 commit、同一 frozen config** 切换；不要在 control 与 V6 之间改代码。`matched-control` 是 V6 新机制关闭、但其余 instrumentation/lifecycle 相同的 V5-compatible policy。这样才能估计 V6 对 V5 的增量，而不是拿历史 `8002/8003` V5 直接比较。

只能复用 P9 的 journals、lifecycle、trace composition、canonical exporter 等实现，不得直接调用旧 P9 CLI 后改名字：P9 verification、method、namespace、manifest、seal 与 live action 都硬编码 V5；`run_v5_minimal_live.py` 也固定默认 `07741c45 × 2`，没有通用 history/source CLI。

V6 必须增加专属 live action/state、method identity、namespace prefix、success/failure/seal schema，并用 TDD 证明 V5/V6 不能混标。用户对本计划中的 V6 live 实验授权不等于 service-admin 授权；仍不得管理 shared provider。

历史 `minimal-9` P8 preflight 实际探测的是 `8000/8001`，不是 `8002/8003`；问题在于该 seal/manifest没有绑定 endpoint、server argv、vLLM version 或 GPU identity，因此不能作为 V6 deployment identity proof。Agent 要在 L2 产生 V6-specific、同 commit 且完整绑定本次 `8000/8001` runtime identity 的 minimal evidence，然后自然继续；不能绕过 identity check。

现有 baseline QA 为 `INVALID_RETAINED`。若 performance-only runner 需要等价于 `allow_invalid_qa=True` 的读取路径，必须在 treatment manifest 中明确“只复用 workload/performance reference，不声称 quality”，而不是把 invalid QA 静默当 PASS。

### 14.2 运行前冻结

- exact server catalog、实际 argv/hash、vLLM version、model revision、PID、GPU UUID、process elapsed/watchdog；
- frozen backend/client hashes；
- Graphiti commit、V6 git commit 与 diff hash；
- endpoint 必须为 `8000/8001`；
- Neo4j version、fresh namespace、无其他 active transaction；
- 连续 idle samples 与 no-other-client evidence；
- warmup、cache policy、cached-token telemetry；不擅自清 server cache或重启 provider；
- 完整 workload manifest 和 source-order hash；
- control/V6 完整顺序在 `ABBA` 与 `BAAB` 中预先随机并记录；该顺序不能看过结果后修改。

prefix caching 是冻结期望，而 service-admin 默认不授权 flush/restart。先 qualification `cache_salt`；若不支持，开发主实验沿用 protocol warmup/idle并用 `ABBA/BAAB` 减少顺序混杂，同时明确其仍不是正式总体因果证据。

### 14.3 执行顺序

```text
preflight + idle evidence
→ 按预注册 ABBA 或 BAAB 执行四个 46-source attempts
→ 每个 attempt 使用 fresh namespace、尚不存在的 output leaf，drain、durable seal、canonical/QA
→ attempts 之间 recheck same backend identity + idle；不得改代码/config
→ counterbalanced paired reducer + proof/claim audit
```

任何一个 attempt 因 infrastructure failure 失效：保留 root，恢复同一环境后用新 root 重跑该 arm；不能把 partial 与成功 arm 拼起来。

如果当前 watchdog/resource budget 只能完成一个 AB 或 BA pair，允许把它作为 **main qualification point** 跑通并封存，但它不能用于正收益候选选择或论文 claim；报告必须预注册下一次反向顺序 replication。

### 14.4 主指标

Primary：

- end-to-end `T_build = timer_stop_ns - timer_start_ns`，其中 `timer_start_ns` 与 `FORMAL_START` 对齐，终点使用共享 runner 的 `timer_stop_ns/t_durable_complete_ns` authority；
- 最后一个 `PUBLICATION_DURABLE` 必须不晚于 stop，并单独报告 publication-to-stop delta及其中无 semantic work 的证据；不能用更早 publication event 替代 timer stop；
- authoritative native frontier span；
- phase-level critical-path decomposition。

Mechanism：

- exact/certified hit、timely hit、fallback、repair；
- 每个 hit 的 DAG attribution，以及重算后的 joint hidden span；除非证明同链且不重叠，不报告 `sum(Z_j H_j)`；
- validation/replay/scheduler overhead；
- foreground inflation 与 speculative waste。

Work/resource：

- logical calls、transport attempts、input/output tokens；
- vLLM process-global queue/running、KV、prefix hit/query、preemption、token counters；per-request cached tokens、TTFT/TPOT、prefill/decode 只有 qualification 后才报告，否则为 `NOT_OBSERVABLE`；
- embedding 与 Neo4j operations；
- work amplification，不以 interval sum代替。

Correctness 分为两个 evidence plane：

- deterministic frozen-oracle：逐 source effect trace 与 canonical graph equality，用于验证声明假设下的 semantic mechanism；
- live main：source coverage、durable order、no early visibility、no duplicate publication、request/fallback accounting；live canonical graph diff只作描述性结果，不能要求两个 stochastic run bitwise equality。

现有 QA/temporal update checks 单独报告。若 QA 仍 invalid，明确保留并禁止 quality/freshness equivalence claim；frozen-oracle differential 不能替代有效 live QA。

结果只能表述为 `6071bd76` development trace 的 matched point estimate，除非之后完成多 history/repetition。主实验跑通后，将其余三个 development histories列入下一 campaign，不要求在最初几小时内全部跑完。

历史 sealed B0 只作 context，不是本次同时间块 control。正式论文 campaign 还需在同一 counterbalanced design 中加入 fresh native B0、V5 与 V6；本轮优先回答 V6 相对其直接 V5-compatible predecessor 是否有增量。

---

## 15. 最终如何选择并命名 V6

正结果与 null 使用不同选择规则，不能用一个含糊条件让任意候选“通过”。

正结果候选按词典序选择：

1. **soundness**：在明确 identity/certificate/oracle 假设下证明成立；adversarial suite 的声明测试域内 zero observed false accept；
2. **因果可解释性**：同端点/同 commit，能用 treatment arms 分开 semantic transform、admission 与 interaction；
3. **实测机制**：收益来自 DAG-recomputed joint critical-path span，不是 interval 加总或 endpoint 差异；
4. **直接净效应**：独立 slice 或重复随机化 block 的 direct paired net effect 具有预注册的保守正下界；嵌套的 6/12-source prefix 只算 scale consistency，不算两份独立证据；
5. **简洁性**：满足前四项的候选中，选代码更少、work amplification 更低、假设更窄者。

null 分支在以下情况下成立：候选均有可复现反例或保守净效应不为正；最强安全候选/关键负对照已完成必要的 full qualification；native ceiling、反例和下一信息增益实验已经封存。null 不要求发明、实现或重命名一个“新方法”。

可能的最终名字由真实机制决定，例如：

- `V6 Exact Native-Demand Replay`；
- `V6 Frontier-Aware JIT Replay`；
- `V6 Certified Partial Revalidation`；
- `V6 Incremental Native View`；
- scheduling-only 若胜出可命名 `V6 Frontier-Aware Runtime`；null 保持 `V6_NULL_RESULT`，不包装成新机制。

名字不是预注册答案。最终报告必须列出被证伪候选及最强反例，说明为何没有选择更复杂方案。

---

## 16. 最终产物与完成定义

在新的 V6 artifact root 中至少产生：

```text
RUN_STATE.json
V6_AUTORESEARCH_LEDGER.jsonl
environment/preflight.json
environment/runtime_identity.json
papers/decision_cards.md
method/V6_METHOD.md
method/V6_PROOF.md
tests/test_evidence.json
micro/<attempts...>
prefix/<attempts...>
main/control/<sealed attempts...>
main/candidate/<sealed attempts...>        # 正结果
main/null_diagnostic/<sealed attempts...>  # null 分支
main/V6_MAIN_COMPARISON.json
report/V6_AUTORESEARCH_REPORT.md
report/NEXT_CAMPAIGN.md
```

`V6_AUTORESEARCH_REPORT.md` 必须先给结论，再给：

- V5 上界为什么存在，以及 V6 攻击了哪一段；
- 最终方法和 proof assumptions；
- 每轮小实验如何改变了方法；
- 负结果与没有采用的方案；
- 同 `8000/8001` 完整主实验结果；
- correctness、work amplification、interference、QA；
- 当前 claim 能到哪里、不能到哪里；
- 下一步四 history / held-out / formal repetition 计划。

共有两个合法 `COMPLETE` 终态：

**正结果终态**

- 一个由真实 evidence 选出的 V6 已实现，关键 TDD/regression 与证明完成；
- `6071bd76` 的 counterbalanced matched control/V6 在 `8000/8001` 完整结束并有合法 artifacts；
- reducer 和报告不把 development trace、无效 QA 或不同端点包装成总体结论。

**null 终态**

- 所有候选均有可复现反例或非正净效应，最强安全候选/关键负对照已完成必要 full qualification；
- matched control、native ceiling、负候选排序、证明不成立的位置与下一实验均已封存；
- 不要求为了完成任务强行实现或命名一个 V6。

两个终态都要求保留失败 attempts、环境异常、test evidence、ledger 与可恢复状态。

---

## 17. 待验证的顶级论文假设

本轮只产生 development hypothesis 与 mechanism evidence，不直接形成论文总体 claim。若证据支持正结果，候选叙事保持四句话：

1. 在当前真实 Graphiti development traces 中，V5 之后的瓶颈假设是 state-dependent native oracle frontier，而不是 future window。
2. 第二句由胜出机制生成：replay 胜出才写 exact native-demand validation；delta、scheduling 或 null 分支必须改写，不能套 replay 叙事。
3. 证明明确给出语义成立的假设，以及共享 FCFS provider 上只能经验评估、不能证明的 interference。
4. 在固定 `8000/8001` 的 development workload 中报告 exposed native time、额外 work、interference 与有效的 correctness/QA evidence；QA invalid 时禁止 quality/freshness equivalence。

进入投稿 claim 的 publication gate 是：预留且未参与方法选择的 held-out histories、counterbalanced paired repetitions、预注册统计方法与置信区间、有效 live QA/temporal quality evidence。四个现有 `DEVELOPMENT_EXPOSED` histories 仍只能用于调研、调参和形成假设。这个 gate 只限制论文表述，不让 autoresearch 停止。

未来 Graphiti、Mem0、A-MEM、Letta 等 adapter 的扩展只保留一个窄边界：`operator demand → request/read certificate → validated result → ordered commit`。本轮不实现这些 adapter，也不把“未来可扩展”写成已经验证的 portability claim。
