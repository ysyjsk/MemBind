# MemBind 最后一轮主实验前纠偏、受约束 AutoResearch 与正式三臂实验执行 Prompt

你现在位于 MemBind 仓库：

`/data/predator/ly/MemBind`

本轮不是继续写工作计划，也不是继续扩张 qualification/certificate 体系。你的任务是：先把当前 HEAD 的三臂实现与实验闭环修到真实可运行、可复核、符合论文公平性要求；随后用受约束的 AutoResearch 自主分析小规模诊断结果，定位问题、查阅相关顶会论文和官方实现、提出并验证最小候选；冻结方法后完成工程 canary、正式三臂 construction、对应 FULL QA、离线归约和最终 claim-support 报告。

除非遇到本文定义的硬 STOP，你必须持续推进，不要在“给出建议”“生成下一轮 prompt”“还需要进一步 qualification”处结束。你也不得为了得到正结果而修改 benchmark、evaluator、Native、正式统计规则或已冻结的方法。

执行前先遵守仓库中的 `AGENTS.md`。每次对用户输出前，先用一段简单的话说明本次准备做什么。

---

## 0. 当前已知事实：必须先核实，不能盲信旧结论

远端 `main` 最近已知 HEAD 为：

`58af2320c5a606195968dbfd5704eaf2a805c8fd`

当前 evidence 中的 `FINAL_DECISION.json` 虽然给出：

`CODE_READY_FOR_THREE_ARM_ENGINEERING_CANARY`

但它绑定的 `base_code_commit` 是：

`c62b548d18bbf0da161069be7be86750e977581c`

已知从 `c62b548` 到 `58af232` 至少有以下真实运行代码发生变化：

- `saturated_fixed_work_baseline_v1_3/src/saturated_fixed_work_baseline_v1_3/mab_live_runner.py`
- `saturated_fixed_work_baseline_v1_3/src/saturated_fixed_work_baseline_v1_3/membind_v6_1/mab.py`

因此，旧 `CODE_READY` 只能证明旧 source bundle，不能自动授权当前 HEAD canary。先检查实际 HEAD、工作树和上述差异；如果仓库已更新，以实际 HEAD 为准，并记录相对本 Prompt 已知状态的新差异。

当前还已知以下待收口风险，必须用源码和测试核实，不得直接照抄：

1. `run_mab_v61_8b.py::_reusable_reference()` 对 Native/B1 的实现身份约束可能不足，正式实验不得跨 campaign 复用旧 construction。
2. `finalize_recent_three_arm_campaign.py` 仍可能接受旧 dataset decision 名称，并可能把 `quality_status`、`prepare_native_overlap`、`frontier_wait` 写成 `MISSING` 后仍形成过强结论。
3. V6.1 当前已知 fixed policy 为 `lookahead=2, future_cap=1, native_future_quota=0`，且 adaptive path 被禁用；必须完成方法身份审计，不能因为它能运行就自动把这些数写成最终方法。
4. 当前 official dataset parity 已知为官方发布的 5 records、每个 60 QA、differences=0；第 5 record 的 question 38 异常必须按官方发布数据保留并披露，不能删除或改 gold。
5. fresh Graphiti write 必须继续省略外部 episode UUID；本地稳定 idempotency key 不能伪装成 Graphiti UUID。恢复语义只能声称 `AT_LEAST_ONCE_WITH_STABLE_IDEMPOTENCY_KEY`。

先输出一个简短的 `CURRENT_HEAD_GAP_AUDIT.md/json`，回答：旧证据覆盖什么、当前 HEAD 新增了什么、哪些结论仍可复用、哪些必须重算。不要重新证明整个 Graphiti/Python 世界。

---

## 1. 本轮唯一允许的三臂身份

### A — `GRAPHITI_UPSTREAM_SERIAL`

严格 upstream Graphiti serial baseline：

- history 内 episode 顺序不变；
- `episode i` 的完整 `add_episode()` durable 完成后才能开始 `episode i+1`；
- 不修改 upstream prompt、schema、messages、temperature、top_p、seed、max_tokens、解析/repair/retry、dedupe、timestamp、summary、DB mutation 语义；
- 不跳过任何 Native 应执行的 LLM call；
- 不注入 MemBind 调度、work reduction、future extraction 或 publication 逻辑。

允许三臂共享完全相同的物理模型 serving pool 和 arm-agnostic router。Native 的逻辑算法仍然串行；router 不得根据 arm identity 给出不同 priority、replica、capacity、cache 特权或队列插队。

### B — `RELAXED_ORDER_PARALLEL`

论文中的 relaxed-order performance ceiling，不是主方法：

- 使用与 A/C 相同的模型、endpoint pool、资源预算和数据；
- 允许放松 Native 的 episode/order 约束以估计并行上界；
- 必须明确标记 `RELAXED_ORDER_PERFORMANCE_CEILING`；
- 不能把 B 对 A 的收益写成 MemBind 主收益；核心 headline 只比较 A 与 C。

### C — `MEMBIND_V6_1`

最终冻结的 MemBind candidate：

- 保持已声明的方法不变量、logical input/work coverage、ordered durable publication 和 Graphiti fresh-write 语义；
- 允许在不污染 A 的前提下进行 dependency-aware、resource-aware 的调度；
- 最终只能是一个冻结身份：`V6_FIXED_POLICY` 或经过本文流程证明的最小 structural adaptive 版本；
- 不得在正式实验中途切换身份。

---

## 2. 四个且只有四个主 Gate

不要再增加新的 H0-Hn/certificate 层级。现有证据只作为输入。四个 Gate 是：

### G1 — Native identity and fairness

通过条件：正式 A runner 在 primary workload 上与 pinned upstream 的调用包络和行为一致；A/B/C 看到相同 serving pool、资源上限、模型配置、cache policy 和 arm-agnostic router；没有已知 prohibited Native patch。

行为比较是主证据，patch inventory 只是 sanity check。无需穷尽所有 import alias 或不可达 maintenance path。

### G2 — V6.1 correctness

通过条件：fresh write UUID 正确；logical source/work coverage 可核对；ordered durable publication 正确；失败不会被误记为完成；正式策略采用 `NO_RESUME_FORMAL_ATTEMPT`。

若 construction/publication crash：该 arm attempt 整体 INVALID，保留全部失败证据，使用 fresh namespace 和 new attempt id 重跑。不要为本轮实现分布式事务或完整 durable reconciliation。

### G3 — Primary runtime robustness and observability

只要求正式三臂配置下、primary MAB workload 实际可达的 structured-output 和运行路径通过。未触达的 maintenance/community/saga/disabled fallback 路径记录为 `NON_BLOCKING_UNEXERCISED_CALLSITE`，不得阻塞 canary。

trace 必须足以重建时间、调用、token、调度、publication、失败和资源机会，但采集本身不得显著改变 critical path。

### G4 — Dataset and evaluator identity

官方 5 records、每 record 60 QA、顺序、question id、gold、官方 evaluator/metric、judge 配置必须冻结。question 38 按官方数据保留并披露。任何旧的“4 contexts”结果不得进入主表。

对可执行 correctness bug 使用 RED → GREEN；对 identity/data/provenance 使用 source audit + deterministic verifier。不要为了格式制造无意义测试。

---

## 3. 实现身份：先解决旧 evidence 与当前 HEAD 脱节

创建一个可内容寻址的 `EVALUATED_IMPLEMENTATION_IDENTITY.json`，至少记录：

- `head_commit`
- `working_tree_clean`
- `tracked_diff_sha256`（clean 时为 null）
- `source_bundle_sha256`
- `runner_sha256`
- `native_boundary_sha256`
- `v61_source_sha256`
- `method_spec_sha256`
- `dataset_sha256`
- `evaluator_sha256`
- `config_sha256`
- `generated_at`

如果本轮需要编辑代码但用户未明确授权 commit/push，不要自行 commit/push。结果必须同时绑定 HEAD 与完整 diff/source bundle hash；不得把旧 commit 伪装成当前被测实现。不得使用 `git reset --hard`、覆盖用户改动或删除历史 evidence。

修复 finalizer，使其 fail-closed：只有 G1-G4 evidence 与当前 `EVALUATED_IMPLEMENTATION_IDENTITY` 完全一致，才可授权 canary/formal。仅 evidence 文件晚于 implementation commit 是允许的；运行源码发生变化而仍复用旧 base identity 是不允许的。

---

## 4. H5：V6.1 critical-path-aware bounded-admission audit

H5 的目标不是删除 `lookahead/future_cap/native_future_quota`，也不是提高 GPU utilization。真正问题是：

> 哪些 bound 只限制逻辑可见范围，哪些 bound 在保护不可抢占的物理 provider/GPU 队列；能否删除前者，同时把后者重述为 resource-derived、priority-aware、bounded physical admission？

不得默认：

`dependency-ready + credit available → dispatch all ready future work`

天然优于 fixed policy。合法 future work 可能占据不可抢占的 FCFS provider queue，导致之后才 ready 的 authoritative request head-of-line blocking、batch/service dilation、token/KV/DB pressure 和 work accumulation。work-conserving 不等于 critical-path optimal。

### 4.1 先重建真实调度问题

必须从当前源码、现有 tests、历史 development ledger 和 raw trace 回答，而不是凭抽象 scheduler 猜：

1. 当前有哪些 task/call class；每类真实 callsite、dependency、输入、输出和消费方是什么？
2. 哪些 task 是当前 durable frontier 的 authoritative/dependency-unblocking work？
3. 哪些由真实 direct-consumer dependency 变成下一次消费所需 work，哪些只是合法但非紧迫的 future/speculative work？
4. provider 的 admission、排队和执行边界在哪里；physical request 是否可抢占？
5. future request dispatch 后，critical request 能否越过；不能时，最大可归因 blocking 是什么？
6. 每类 work 的 service time、prompt/output tokens、embedding、DB、memory/KV、provider slot 占用是什么？
7. 当前 source lease、physical permit、provider request、endpoint/GPU capacity 是不是同一种 credit？
8. 当前 fixed guards 最初解决了什么已观察到的 failure/pathology？

当前已知但必须复核的源码事实包括：

- `_PRIORITY` 已区分 `NATIVE_FRONTIER / FRONTIER_PREPARE / FUTURE_PREPARE`；不要重复实现一个表面相同的 P0/P1/P2 queue。
- `SourceLease` 声称只控制 logical future-source admission；`PhysicalPermit` 才承载 provider slot/token weight。必须验证所有真实 callsite 是否遵守这层分离。
- provider 被描述为不可抢占 FCFS；native guard 通过停止新 future admission 并 drain 已 active future calls 到 `native_future_quota` 来建立可执行边界。
- `V61Policy.token_budget()`、KV cache/headroom/decode reserve 和 `CapacityAuthority` 必须验证是否来自当前 live runtime，而非过期的 8B 常数。
- 历史 `r66a` adaptive controller、`r67-r69` finish-time/service-EWMA critical scheduler 已形成负面证据：它们出现 queue/service feedback 混淆、跨 phase spillover、batch/service dilation，最终没有稳定超过 retained fixed/elastic substrate。除非新证据明确推翻原失败原因，否则不得换名重跑同一思路。

生成 `SCHEDULER_PROBLEM_AUDIT.json/md`，至少包含：

- `TASK_CLASSES`
- `TRUE_DEPENDENCIES`
- `CONSUMPTION_HORIZON`
- `ARTIFICIAL_SERIALIZATION`
- `NON_PREEMPTIBLE_BOUNDARIES`
- `INTERFERENCE_RISKS`
- `CURRENT_FIXED_GUARDS`
- `WHY_EACH_GUARD_EXISTS`
- `HISTORICAL_REJECTED_SCHEDULERS`
- `OPEN_METHOD_QUESTION`

### 4.2 严格区分三种“空闲”

- `PROVIDER_IDLE`：当前看不到 active physical request；这不证明 GPU 已完全空闲。
- `RESOURCE_CREDIT_AVAILABLE`：某个明确 physical admission boundary 仍允许接收 work。
- `BOUNDED_SPECULATION_OPPORTUNITY`：future task dependency-ready，当前无更高优先级 work ready/queued，真实 physical credit 可用，且 outstanding non-preemptible future exposure 仍在可证明边界内。

只有第三种才可能授权 future physical dispatch。这里刻意不用 `SAFE`：在无可靠 arrival predictor、provider 又不可抢占时，scheduler 无法保证 P2 dispatch 后不会突然出现 P0。方法不预测未来 P0 arrival，也不声称消灭全部 priority inversion；它只通过 bounded admission 控制最坏 exposure，并把随后出现的 P0 blocking 记录为 `unavoidable_future_blocking`。

不得实现：

`provider idle → future ready → immediately dispatch`

### 4.3 采用逻辑 ready 与物理 admission 两层模型

优先分析的结构是：

```text
dependency graph
  → wide dynamic LOGICAL_READY_SET
  → classify criticality/consumption horizon
  → P0 authoritative/dependency-unblocking
  → P1 direct-next-consumer dependency（仅在真实 DAG 能证明时）
  → P2 future/speculative
  → real per-resource admission credits
  → bounded PHYSICALLY_ADMITTED_SET
  → ordered authoritative publication
```

逻辑 ready set 可以很宽；进入不可抢占 provider queue 的 future work 必须小且受控。`lookahead` 限制“能看多远”，physical admission 限制“实际占用多少不可抢占资源”；两者不能混为一谈。

候选可尝试删除不必要的逻辑 horizon，但不得因此删除所有 physical outstanding bound。最终也不预设一定采用该候选：若真实资源/arrival 模型无法保护 critical work，应保留 fixed safeguard。

### 4.4 调度目标与优先级

最终目标固定为：

> 在不改变 C 已冻结并声明的 Graphiti logical-work/input/publication contract 的前提下，用 dependency-safe overlap 降低 end-to-end critical-path latency，同时限制 future work 对 critical work 的 queueing、batch、token/KV、memory、DB 和 provider interference。若 C 另含独立 work-reduction extension，必须单列其 contract 与 effect，不能把它算成 scheduler 收益。

至少逻辑区分：

- `P0`：authoritative / dependency-unblocking work；
- `P1`：其结果已被真实 consumption edge 证明为下一次 authoritative transition 的直接输入；
- `P2`：合法但非近期消费的 future/speculative work。

具体 task 映射必须由真实调用 DAG 产生，不能按名称、`source_distance <= K`、固定 horizon 或“离 frontier 很近”臆测。当前 `sequence == frontier + 1` 只能作为待验证实现事实，不能自动证明 P1 identity。若无法证明直接 consumption edge，就取消 P1，使用可审计的 P0/P2 两级分类。基本关系是 `P0 > P1 > P2`，但禁止新增未经证据支持的连续 score、arrival/service predictor 或硬件特调 threshold。

任何时刻出现 P0 ready/queued 后，必须停止 admit 新 P2；若保留 P1，P1 ready/queued 时也不得以 P2 越过它。已经进入不可抢占边界的 P2 只能被计入 `unavoidable_future_blocking`，不得假装已抢占。必须测量：

- `critical_queue_wait`
- `future_queue_occupancy`
- `p0_waiting_while_p2_active`
- `new_p2_admitted_while_p0_waiting`（正式安全期望为 0）
- `new_p2_admitted_while_p1_waiting`（仅保留 P1 时适用，正式安全期望为 0）
- `unavoidable_future_blocking_ns`
- `future_result_consumption_lag`
- `completed_but_not_consumable_future_work`
- `wasted/discarded_speculation`

定义：

`SPECULATION_DEBT = 已经消耗 provider/token/DB/materialization work、但尚未进入 authoritative consumption path 的 future work`

至少记录 debt 的 count、已知 input/output tokens、累计 work、artifact bytes/内存占用、age 和对应 consumer dependency。它不自动等于内存泄漏：若结果最终必被消费，它可能只是提前完成的 sunk work；只有当 debt 无界增长、占用真实 bounded resource、导致额外/失效工作或显著落后于 consumption frontier 时，才构成 backpressure pathology。

physical slot 空闲并不自动授权继续增加 debt。候选必须给出 debt backpressure 规则及其 authority：优先从 consumer dependency、artifact/memory/token budget 或已冻结 work contract 推导；不得根据 dev/formal speedup选择一个新的 debt threshold。若无法给出非任意边界，应保留现有 bounded safeguard，而不是无限扩大逻辑 horizon。

GPU utilization 上升但 `T_build`、critical path 不降，或 critical queue/service dilation 上升，不能判为成功。

### 4.5 Credits 必须映射真实资源

审计 endpoint concurrency、vLLM admission/queue、GPU replica、KV/token residency、embedding、Neo4j/DB concurrency 和 route。每种 credit 必须说明：

- 对应的 physical resource/boundary；
- authority 的实时来源；
- unit（request、token、KV、DB transaction 等）；
- acquire/release/cancel 守恒；
- 是否包含 provider queue 还是仅 execution；
- 三臂是否共享同一 deployment envelope。

若 credit 不能映射真实 bottleneck，就不能把它作为 adaptive 的核心机制。理想语义是：

`execution_credit = capacity to enter a named non-preemptible physical queue/execution boundary now`

而不是另一个换名的 `future_cap=2`。

必须检查同一个 request credit 所代表的 non-preemptible occupancy 是否近似同质。如果短 2 秒请求和长 70 秒请求都只算 1，就不能只用 count bound。优先组合调用前已经可知、可审计的资源量，例如：

- outstanding request/slot count；
- exact/verified prompt-token count；
- frozen max/decode-reserve token；
- input bytes/items 或已知 embedding/DB work units；
- provider/runtime 暴露的 KV/token envelope。

当前代码已尝试用 `request_tokens = prompt_tokens + decode_reserve` 配合 request authority；必须验证所有 primary routed/auxiliary callsite 都经过同一 physical admission，token counter/fallback 的误差和 headroom 有 provenance，且预算来自当前 runtime。不得用预测 service time、EWMA arrival prediction 或正式 benchmark 结果替代调用前可知 work。

资源容量约束是方法不变量；具体 endpoint 数、credit 数、显存和并发值属于 deployment identity。必要的 safety headroom 可以保留，但必须由 runtime capability/measurement 导出并记录 provenance，不能来自正式 5 histories 的效果调参。

### 4.6 Provider-free deterministic scheduler stress tests

在选择 candidate 前，至少构造以下 deterministic tests；它们验证语义和 failure mode，不用真实性能数字选方法：

1. **Only critical**：行为退化为 authoritative execution。
2. **Critical busy + future ready**：future 不得抢占或越过 critical。
3. **Critical blocked on an unmet dependency + no P0/P1 ready/queued + physical credit idle**：存在 bounded speculation opportunity 时 future 可以运行。
4. **Future dispatched, then critical ready**：记录不可避免 blocking；立即停止新 future admission。
5. **Many future ready**：logical ready 可大，physical future outstanding 不得无界增长。
6. **Single capacity**：自然接近 serial，不制造 provider backlog。
7. **Multiple capacities**：符合 bounded-admission 条件的 future work 能按真实 capacity 扩展，不靠扩大 lookahead。
8. **Highly variable service time**：长 future request 不得导致无界 critical head-of-line blocking。
9. **Invalid/fallback future result**：计入 wasted work，不污染 authoritative state。
10. **Cancellation/failure**：source lease、physical permit、token/KV credit 精确守恒，无 leak/double release。
11. **Priority inversion**：P0 queued 时 `new_p2_admitted_while_p0_waiting == 0`。
12. **Ordered publication**：乱序 prepare completion 不能改变 durable source order。
13. **P1 identity**：只有存在直接 consumer edge 的任务能进入 P1；source distance/horizon 不能改变分类。
14. **Speculation debt/backpressure**：producer 快于 consumer 时 debt 可观测且有界；到达冻结边界后停止新 P2，consumer 前进后恢复。

输出 event-by-event oracle，而不是只断言最终 pass。

### 4.7 Fixed 与 Adaptive 的决策

只有同时满足以下条件，才允许冻结新的 structural adaptive：

1. 目标 guard 主要是在限制 physical outstanding future work，而其逻辑 horizon 可以独立放宽；
2. 新机制由真实 resource credits、P0/P1/P2 priority 和 bounded physical admission 表达；
3. 通过全部 deterministic stress tests；
4. 不增加 prediction model、magic threshold 或正式 benchmark 调参；
5. C 已声明的 logical-work/input/publication contract 不变；若存在独立 work-reduction extension，其 effect 与 scheduler effect 可分离；
6. 代码改动局部、可审计，并与历史 r66/r67-r69 负面候选有实质区别；
7. 在合法非正式 dev workload 上，机制证据显示 critical stall 减少且 interference/work 不放大。

若 provider 不可抢占且 critical arrival 无法安全预测、credit 无法映射真实资源、future service 高度不稳定、接口不能区分 critical/future，或新方法需要新的复杂 cost model，则冻结：

`V6_FIXED_POLICY`

并明确：当前 window/quota 是 `BOUNDED_ADMISSION_SAFEGUARD`，不是理论最优参数。不得仅因 fixed 不影响 correctness 就声称参数合理；必须披露它保护的物理边界，并在正式实验后做预注册的非选择性 sensitivity。

输出：

- `V61_PARAMETER_IDENTITY.json/md`
- `SCHEDULER_PROBLEM_AUDIT.json/md`
- `HEURISTIC_NECESSITY_AUDIT.json/md`
- `SCHEDULER_RESOURCE_CREDIT_MAP.json/md`
- `SCHEDULER_STRESS_TEST_RESULT.json/md`
- `HISTORICAL_SCHEDULER_NEGATIVE_EVIDENCE.json/md`
- `METHOD_INVARIANTS.json`
- `DEPLOYMENT_PARAMETERS.json`
- `HEURISTIC_PARAMETERS.json`
- `ADAPTIVE_DECISION.json`

---

## 5. 受约束 AutoResearch：采用框架精神，不照搬无限调参

本项目采用 AutoResearch 的以下核心模式：固定不可变评测面、一次一个假设、小预算真实运行、自动读取结果、`keep/discard/crash`、append-only ledger、根据结果继续提出下一候选。

但不得照搬“单一分数更好就 keep、无限循环、随意修改所有代码”。本项目是多目标系统实验，存在 Native 公平性、语义正确性、benchmark 泄漏、provider 成本和正式冻结边界。

### 5.1 AutoResearch 的可变与不可变区域

冻结不可修改：

- A 的 upstream 算法和所有 Native 语义参数；
- B 的 ceiling 定义；
- official 5-record dataset、QA inventory、gold、evaluator、judge、Reader prompt/model；
- 三臂共享 serving pool 和资源公平性；
- 正式统计 estimand、arm order、replicate、cache/warmup 和失败替换规则；
- 已生成的 raw/invalid/rejected artifacts。

冻结前允许研究：

- C 的 scheduler/admission/ready-set/resource-credit 实现；
- primary-path correctness、structured-output recovery 和 observability bug；
- 不改变语义的低开销 trace；
- harness/finalizer 中明确的实现缺陷。

### 5.2 AutoResearch 使用的数据边界

性能候选的 keep/discard 不得使用正式 5-history headline 结果，也不得使用 official canary 的 A/C speedup 选择方法。

冻结前优先使用：

1. provider-free deterministic fixture / trace replay；
2. 历史上已明确标记为 development、非正式的数据；
3. 不属于 official 5 records 的冻结 development workload；
4. 若没有合法 dev workload，则只允许 correctness/structural reasoning，不得因 official canary 的速度选择候选。

在第一个 live performance candidate 之前，必须生成并封印 `FROZEN_DEV_WORKLOAD_MANIFEST.json/md`。它至少记录：

- 固定的 `DEV-D0/DEV-D1/DEV-D2`（若合法数据不足，可少于 3，但不得在看到候选结果后补换）；
- dataset/revision/source/history/session/content hashes；
- 与 official 5 records 的 ID、source、session 和内容级 non-overlap proof；
- 仅依据静态 workload structure 选取的覆盖理由，例如 source count、prompt/input size、dependency shape、task mix；
- 固定 source prefix/full scope、control/candidate 交错顺序、replicate 与资源配置；
- manifest/source-bundle hash 和冻结时间。

不能直接沿用名称中带 `development` 的旧集合。当前已知 `membind_v7/dvsr_workload.py` 的历史集合中存在与 official 5 records 重叠的 ID，必须逐项内容核对；重叠项不能用于本轮 performance keep/discard。允许从未进入 official 5 的原始 benchmark records 或独立合成/真实 dev 数据建立 dev set，但选择规则必须在候选结果之前冻结。

所有 candidate 必须使用同一 sealed dev set 和同一 paired protocol。禁止不断改变 prefix/history、挑选最有利 workload，或把某个 candidate 失败解释为“换一个小样本再试”。若没有能证明独立的 dev workload，就不授权 live performance-driven method selection。

如果 official canary 暴露结构性问题，只能把问题抽象成非正式 deterministic fixture 或独立 development workload 后验证候选；这会开启新 method epoch，并要求重新冻结、重新 canary。official canary 自身的性能数字不能成为 keep/discard 指标。

### 5.3 每个小结果后的自主诊断循环

每个小运行完成后，不得只说“更慢/失败，继续试”。必须生成一条 append-only `AUTORESEARCH_LEDGER.jsonl` 和一个 `DIAGNOSTIC_DECISION.json`，至少包含：

- candidate/epoch/source bundle identity
- 运行是否有效
- 预期机制和可证伪预测
- 实际 `T_build`、TTFDP、logical/physical calls、input/output tokens
- provider queue/service、dependency wait、publication wait、unknown time
- endpoint idle、GPU idle、scheduler idle opportunity、bounded speculation opportunity
- P0/P1/P2 queue/active state、unavoidable blocking、speculation debt/consumption lag
- work amplification、route balance、cache/warmup state
- correctness/graph/publication/quality signals
- 与 control 的 paired delta 及噪声带
- 第一处 divergence
- 至少两个 competing hypotheses
- 证据支持/反对什么
- 是否需要查文献/官方代码
- 下一次唯一改变的变量
- `KEEP / DISCARD / CRASH / INVALID / NEEDS_DIAGNOSIS`

诊断顺序固定为：

1. attempt/data/identity 是否有效；
2. provider、GPU、Neo4j、网络是否发生外部漂移；
3. instrumentation 是否污染 critical path；
4. logical/physical work 或 token 是否放大；
5. ready work 与 available credit 是否同时存在却未 dispatch；
6. queue/service 是否因错误 routing/admission 膨胀；
7. dependency/frontier/publication 是否成为关键路径；
8. 最后才判断方法结构本身不足。

禁止凭单次 wall-clock 结果改方法。

### 5.4 小结果问题分类与动作

| 分类 | 允许动作 | 禁止动作 |
|---|---|---|
| `CORRECTNESS_FAILURE` | 最小复现、RED→GREEN、同一小 workload 复验 | 绕过调用、改 Native、放宽验收 |
| `OBSERVABILITY_FAILURE` | 补低开销 raw event，离线归约 | 在 critical path 同步分析/fsync |
| `PROVIDER_OR_ENV_DRIFT` | 标记 INVALID、恢复环境、fresh attempt | 当成方法失败或偷偷换模型 |
| `NO_SPEEDUP/PERFORMANCE_REGRESSION` | 分解关键路径与 work/queue/service，验证单一机制候选 | 直接调 lookahead/grid search |
| `WORK_AMPLIFICATION` | 找第一处额外 logical/physical work；只做语义守恒的消除 | 通过跳 LLM call 获得速度 |
| `QUALITY_REGRESSION` | 先查 input/work/publication identity 和 evaluator 稳定性 | 改 gold、prompt、judge、QA subset |
| `UNIDENTIFIABLE` | 增加最小观测或构造可区分假设的 probe | 盲目连续尝试多个 patch |

### 5.5 文献与官方实现驱动的补救规则

出现以下任一情况时，agent 必须主动查阅 primary source，而不是靠记忆猜：

- 同一 bottleneck 连续两个候选未解决；
- 当前 scheduler/admission 设计假设不清；
- trace 显示 prefill/decode、batching、token budget 或应用 DAG 相关干扰；
- 需要决定一项机制能否迁移到 MemBind；
- 需要定义 metric、fairness 或 artifact protocol。

优先阅读论文原文、作者官方页面和官方代码；不得把博客摘要当唯一依据。至少考虑：

- Parrot（OSDI 2024）：应用级数据流/DAG 与端到端调度；
- Sarathi-Serve（OSDI 2024）：prefill/decode 干扰、chunked prefill、stall-free scheduling；
- DistServe（OSDI 2024）：只有定义明确 SLO 时才使用 goodput；
- NanoFlow（OSDI 2025）：并发操作的 interference-aware execution ordering/resource allocation；它不是“ready 就 dispatch”的依据，且其 operation-level/GPU-internal 假设不能直接套到跨请求 Graphiti workflow；
- Llumnix（OSDI 2024）：参考 heterogeneous/unpredictable requests、priority isolation、queueing analysis 和 runtime rescheduling；不得直接迁移其依赖 KV-state live migration 的机制；
- vLLM/PagedAttention（SOSP 2023）：serving、batching 和内存管理边界；
- MemoryAgentBench 官方论文/代码：数据与 accuracy 语义；
- USENIX/OSDI artifact 指南：可复现 artifact、命令、环境和结果绑定。

每次查阅后生成 `REFERENCE_DECISION_CARD`：

- source URL / paper / official code revision
- 该工作真正提出的机制
- 依赖的 workload/hardware/serving assumptions
- 与 MemBind 当前瓶颈的对应关系
- 不能迁移的部分
- 导出的一个最小候选
- 该候选的可证伪预测

禁止“因为顶会用了某机制所以照搬”。只有 trace 证明 assumptions 相符时才实现。

### 5.6 Candidate 纪律、预算和退出

每个 candidate 只改变一个 evidence-selected hypothesis。先写预测再改代码。候选至少经过：

1. provider-free correctness/regression；
2. 在 `FROZEN_DEV_WORKLOAD_MANIFEST` 中预先指定的同一短 workload 上进行真实 diagnostic screening；
3. 若依据性能 keep，则在冻结的非正式 dev workload 上进行交错 paired confirmation，并证明方向一致且效应超过同 epoch 的噪声带；
4. correctness、Native fairness、冻结的 logical-work contract、scheduler safety 和 publication invariant 全部不退化。

Candidate decision 不能最大化单一 `T_build speedup`。采用以下分层规则：

1. **Hard validity gates**：correctness、Native fairness、声明的 logical-work/input contract、scheduler safety、publication/quality pipeline；任一失败直接 reject/invalid，不允许性能补偿。
2. **Mechanism support**：候选是否按预测减少真正的 critical stall/priority exposure，而不是只提高 utilization 或移动 queue time。
3. **Result efficiency**：`T_build`、critical-path effect、physical/token/work amplification 和 quality signal 共同判断；额外 work 必须被完整披露，不能把“用更多资源换时间”写成纯调度效率。
4. **Complexity tie-break**：当有效效果落在同一噪声带内，优先状态更少、heuristic 更少、代码更小、runtime measurement 更少且 failure surface 更窄的候选；删除复杂度而维持效果可以是 KEEP。

这里要求保持的是 C 已冻结并声明的 logical-work contract，不是强迫 C 的 logical-call inventory 与 A 完全相同；若 C 包含独立声明的 work-reduction extension，必须在 method identity 与 estimand 中单列，不能与 scheduler 收益混算。

提出候选前必须检索 `HISTORICAL_REJECTED_SCHEDULERS` 和 append-only ledger。若机制与 r66 adaptive controller 或 r67-r69 finish-time/service-EWMA scheduler 同构，默认 `DO_NOT_REPEAT`；只有在明确指出旧实验的哪个前提/测量缺陷已改变、以及新候选为何不再产生 phase spillover/service dilation 时，才允许重新授权。

`HISTORICAL_SCHEDULER_NEGATIVE_EVIDENCE` 不能只列 PASS/FAIL；每行至少包含：`candidate_id`、核心机制、原可证伪预测、workload/identity、实际 pathology、第一处 divergence、根因、修复是否验证、为什么拒绝、允许重试所需的新条件、与当前候选的 mechanism hash/差异。旧结论保持 append-only。

同一 bottleneck family 最多连续 3 个 implementation candidates；3 个都失败后必须停止局部微调，汇总负面证据，查 primary literature，并只允许一次 architecture-level pivot。一次 research epoch 最多 2 个 bottleneck families。达到预算仍无 adaptive 支持时，记录 `VALID_NEGATIVE_ADAPTIVE_RESULT_FIXED_CONTINUES` 并冻结较简单且合法的 fixed candidate；不得为了正结果继续无界搜索，也不得因此跳过 canary/formal。

AutoResearch 方法搜索的终点不是“必须赢”，而是以下之一：

- `METHOD_CANDIDATE_SUPPORTED_FOR_CANARY`
- `FIXED_POLICY_SELECTED_WITH_DISCLOSED_HEURISTICS`
- `VALID_NEGATIVE_ADAPTIVE_RESULT_FIXED_CONTINUES`
- `EXTERNAL_BLOCKER_WITH_REPRODUCIBLE_EVIDENCE`

`VALID_NEGATIVE_ADAPTIVE_RESULT_FIXED_CONTINUES` 只结束 adaptive 候选搜索：若 `V6_FIXED_POLICY` 仍通过 G1-G4 和 scheduler safety，就冻结它并继续 canary、formal 和全部报告。不得仅因 adaptive 没赢而停止主实验。只有不存在任何不改变三臂核心定义且满足正确性/公平性的合法 C 身份时，才构成需要用户决定的核心方法 blocker。

保留 rejected candidate、失败 trace 和原因；不得改写 ledger。未经用户授权不得 commit/push；不得用 destructive git 命令回退用户工作。

---

## 6. 冻结前的必要 harness 收口

只修会污染实验或使报告不完整的缺陷：

1. canonical arm IDs 在 construction、resume/QA、finalizer、report 全链路一致；旧 B0/B1/V6 名称只能作为显式 legacy alias，不能产生混合身份。
2. canary/formal 默认禁止跨 campaign construction reuse。若保留 reuse 功能，只允许同一 sealed campaign 内恢复已完成 QA/归约，并要求完整 implementation/data/config/namespace/attempt identity 匹配。
3. 每个 attempt 有唯一 `campaign_id/history_id/replicate_id/arm/attempt_id/namespace`。
4. finalizer 接受当前官方 dataset parity schema，但必须精确校验 5×60 inventory、hash、differences=0 与 anomaly disclosure。
5. `quality_status`、overlap、frontier wait、trace completeness 不能硬编码 `MISSING` 后仍生成支持性 claim。
6. raw trace 使用 append-only、in-memory buffered 或异步批量写；运行中不做昂贵 JSON 分析或频繁 fsync。critical path、overlap 和 DAG 全部离线计算。
7. 能区分 `OBSERVED_CAUSAL_EDGE`、`INFERRED_CAUSAL_EDGE`、`UNKNOWN_INTERNAL_PROVIDER_TIME`，不要求虚构完整 provider 内部 DAG。

完成后重算 G1-G4，并把结果绑定到新的 `EVALUATED_IMPLEMENTATION_IDENTITY`。

---

## 7. `FINAL_METHOD_SPEC` 与冻结

AutoResearch 和 H5 结束后生成：

- `FINAL_METHOD_SPEC.json/md`
- `PRECANARY_METHOD_SEAL.json`
- `FROZEN_RESOURCE_AND_ROUTING_CONTRACT.json`
- `FROZEN_CACHE_WARMUP_PROTOCOL.json`
- `FROZEN_METRIC_DEFINITIONS.json`

至少冻结：

- C 是 fixed 还是 structural adaptive；
- method invariants 与所有剩余 heuristic；
- P0/P1/P2 的精确定义与映射；若 P1 存在，绑定其 direct-consumer dependency proof；否则明确为两级 P0/P2；
- logical source lease 与 physical request/token/work credit 的定义、容量、获取/释放/cancel 规则和守恒不变量；
- bounded speculation opportunity、future outstanding bound、speculation debt 指标与 backpressure 触发/恢复条件；
- provider 为不可抢占边界时的 `unavoidable_future_blocking` 定义；
- `NO_ARRIVAL_PREDICTOR`：不得把未来 P0 到达、service time/EWMA 或当前机器速度预测写进方法正确性；
- model、endpoint、replica、token/context limits；
- A/B/C route 与 arm-agnostic fairness；
- Native max_tokens 等实验协议参数；
- V6 自身预算与 deployment capacity 的边界；
- cache 是否允许、warmup 次数、cache counters/state；
- construction/QA 分界；
- trace schema 和 instrumentation mode；
- 采用与排除的 historical mechanism families、对应 evidence hash 与可重新考虑的条件；
- `FROZEN_DEV_WORKLOAD_MANIFEST` 的 hash，以及所有 live candidate 使用同一 dev workload 的证明。

这里先建立供 canary 使用的 content-addressed candidate seal。canary 若只产生性能弱/负面观察，seal 不变；若发现 correctness、observability 或 scheduler-safety bug 并发生源码/语义修复，旧 seal 立即失效，必须重新生成并重跑三臂 canary。只有三臂 canary 通过后，才把完全相同的 method/config/source hashes 晋升并写入 `FINAL_METHOD_FROZEN.json`。冻结后，性能差、收益小或 quality 不理想都不能直接改方法。

---

## 8. 两 source 三臂 engineering canary

选择官方 5 histories 中预先固定的一个合法 history，但只消费其前两个合法 sources。两 source 是为了真正触发 B 的并行和 C 的 scheduling/frontier，不是跑完整 history。

每个 arm 使用 fresh namespace 和 fresh attempt；A/B/C 共享相同物理 serving pool，按预定顺序执行。canary 只判断：

- 三臂能完成或给出结构化失败；
- A 仍为严格 Native serial；
- B 确实触发 relaxed-order overlap；
- C 确实触发冻结 scheduler/admission；
- UUID、work coverage、durable order、artifact completeness 正确；
- trace 可以重建主要时间分解且开销合格；
- primary structured-output path 不会立即失败；
- future physical outstanding 不超过冻结的 resource-derived/fixed safeguard；
- P0 ready/queued 后没有继续 admit 新 P2；
- P1 ready/queued 后没有被新 P2 越过（仅保留 P1 时适用）；
- 没有 credit leak、starvation、无界 future backlog 或 physical concurrency 越界；
- 已 dispatch P2 对随后 P0 的不可避免 blocking 可观测；
- logical ready、physical admitted、provider queued/active、completed-but-not-consumable 四种状态没有混账。
- P1（若保留）由真实 direct-consumer edge 触发，而非 source distance；
- speculation debt 可重建，达到冻结 backpressure 边界时停止新 P2，并在 consumer 前进后恢复。

canary 的 speedup/quality 只能记为 `ENGINEERING_OBSERVATION_NOT_FOR_SELECTION`。

若 canary 出现 correctness/observability/scheduler-safety pathology：进入最小 AutoResearch bug loop，修复后建立新 implementation identity，并重跑全部三臂 canary。scheduler-safety pathology 包括 priority inversion、credit leak、future outstanding 越界、starvation、无界 backlog、冻结机制未实际执行或 authoritative ordering 异常；它不是“速度没有达到预期”。

若只是性能不佳：不得在该官方 canary 上调参。可以把瓶颈抽象到非正式 dev fixture，开启新 method epoch；若这样做，之前 canary 失效，必须重新冻结并重跑。

---

## 9. 正式实验前封印与 preregistration

canary PASS 后，先 provider-free 生成并验证：

`FORMAL_CAMPAIGN_MANIFEST_SEAL.json`

预生成 45 个 construction cells：

`5 histories × 3 replicates × 3 arms`

每个 cell 必须冻结：

- history/record/source inventory hash
- replicate id
- arm 与 within-history arm order
- campaign/attempt/namespace
- implementation/method/data/evaluator/config hash
- model/endpoints/resource pool
- cache/warmup state
- expected construction 与 FULL QA artifacts

只检查 manifest 完整性、唯一性和 hash，不准扩展成新 qualification 工程。

正式 preregistration 必须提前写明：

### Primary performance estimand

`A vs C` 的同 history、同 replicate paired `T_build` ratio；B 单独作为 ceiling。

### Quality policy

Primary QA metric 使用官方定义。默认采用 `PAIRED_QUALITY_DELTA_ONLY`：报告 per-question paired delta、每 history/replicate 结果、disagreement 和 cluster-aware uncertainty，不自动声称 non-inferiority。

只有在看正式结果之前，能从 benchmark 分辨率、官方协议或外部先验独立论证 margin `δ` 时，才允许注册 `QUALITY_NONINFERIORITY`；必须记录 δ 来源。禁止根据正式数据选 δ；“不显著”不等于 preserved。

### Run order

严格 history-atomic：完成一个 history 的三个 replicates 后才能进入下一 history；每个 replicate 内 A/B/C 三臂连续完成。使用预注册 cyclic counterbalance，例如：

- r1: A → B → C
- r2: B → C → A
- r3: C → A → B

history 顺序也在 manifest 中冻结。

### Failure/replacement

`NO_RESUME_FORMAL_ATTEMPT`。失败 arm INVALID；保留 artifacts；fresh namespace/new attempt 重跑同一 cell。不得复用失败 graph。

### Epoch rule

正式开始后任何运行源码、方法、evaluator、资源公平性或 trace 语义变化都会关闭整个 experiment epoch。旧数据保留但不与新 epoch 混入同一主表。

---

## 10. 正式 campaign 的 AutoResearch 边界

正式 45 cells 期间启用 **blinded validity monitor**：agent 可以自动检查完成状态、artifact、hardware/service drift、provider error、DB failure、资源身份、fairness 和预注册 scheduler-safety invariants，但不得用累计 A/C speedup 或 quality delta 决定修改方法、改变剩余顺序或只补有利 cells。

### 10.1 首个完整 history 后的只读科学诊断

完成并 seal 第一个完整 history 的：

`3 replicates × A/B/C construction + corresponding FULL QA`

后，自动生成一次 `EARLY_SCIENTIFIC_DIAGNOSTIC.json/md`。它只读分析：

- A/C `T_build`（仅描述，不作为 keep/discard）；
- critical-path change；
- P0 critical queue/provider wait；
- P2 future queue occupancy 与 unavoidable blocking；
- speculation debt、consumption lag 与 backpressure 是否按冻结规则工作；
- bounded speculation opportunity 与 scheduler idle opportunity；
- logical ready / physical admitted / queued / active / completed-not-consumable；
- route/service dilation；
- work/token amplification；
- quality/failure；
- 冻结 scheduler 是否实际产生了预期事件和状态转换。

结论只能是：

1. `CONTINUE_FORMAL_CAMPAIGN`：协议与机制有效，继续。
2. `METHOD_EFFECT_WEAK_BUT_VALID`：收益弱或暂时变慢，但实现/实验有效；仍继续，不改方法。
3. `SCIENTIFIC_PATHOLOGY_DETECTED`：只有预注册的 correctness/fairness/scheduler-safety invariant 被破坏，才允许停止 epoch。

`SCIENTIFIC_PATHOLOGY_DETECTED` 必须由看结果前冻结的机器可判定条件触发，例如：

- A/B/C identity 或 resource fairness 不成立；
- P0 queued 时仍 admit 新 P2；
- physical future outstanding 超过冻结 bound；
- credit conservation、cancel/release 失配；
- starvation、deadlock 或无界 backlog；
- speculation debt 超过预注册的 resource/dependency-derived bound，或 backpressure 在冻结条件下未触发/未恢复；
- C 没有执行 `FINAL_METHOD_SPEC` 声称的机制；
- authoritative order/work/input/quality pipeline 被系统性破坏；
- instrumentation 使 timing 不再可解释。

单纯 `speedup < expected`、GPU utilization 低、一个 history 变慢、critical queue 数值较高但没有违反已冻结 safety condition，均只能归入 `METHOD_EFFECT_WEAK_BUT_VALID`，不能停止或调参。若确因 pathology 停止，整个 epoch 标记 `ABORTED_BY_PREREGISTERED_SAFETY_DIAGNOSTIC`，旧数据不进主表；修复后必须重新冻结、canary、manifest，并从 45 cells 起完整重跑。

### 10.2 正式运行问题分类

1. **外部/瞬态失败**：attempt INVALID，按预注册规则 fresh replacement；方法不变。
2. **harness/measurement/correctness/safety bug**：停止当前 epoch，保留全部旧数据；进入 AutoResearch 修复；重新冻结、canary、manifest，从新 epoch 完整重跑。
3. **有效但负面的科学结果**：继续完成 campaign，不修、不调参；最终如实报告不支持或不确定。

完整正式实验若得到 `EXPERIMENT_COMPLETE_BUT_METHOD_NOT_SUPPORTED`，必须保留整个 epoch，并根据统一 trace 区分 dependency opportunity 不足、resource slack 不足、future interference、不可抢占 provider queue、work amplification 或 stateful critical phase 主导。若据此提出新方法，必须建立新 method epoch、重新 preregister 和完整重跑；不得覆盖或混合原结果。

这一步是 AutoResearch 的关键约束：自主分析问题不等于利用正式结果反复优化到成功。

---

## 11. 正式 metric 定义

### `T_build`

从 `FORMAL_CONSTRUCTION_START` 到该 attempt 最后一个 expected source 的 `PUBLICATION_DURABLE`。不含进程启动、模型启动、warmup、preflight、FULL QA、cooldown 和离线分析。

### `TTFDP`

`Time-to-First-Durable-Publication`：从 construction start 到第一个 source durable publication。不要使用含义模糊的 TTFP。

### Construction throughput

`durable_sources / T_build`。除非预先定义明确 SLO 与质量门，不得称为 goodput。

### 时间与机会分解

至少报告：

- provider queue wait
- provider service time
- dependency/frontier wait
- publication/DB wait
- unknown internal provider time
- `ENDPOINT_IDLE`
- `GPU_IDLE`
- `SCHEDULER_IDLE_OPPORTUNITY = ready work exists AND execution credit available AND no dispatch`
- `BOUNDED_SPECULATION_OPPORTUNITY`
- `P0_QUEUE_WAIT`
- `P1_QUEUE_WAIT`（仅保留 P1 时适用）
- `P2_QUEUE_OCCUPANCY`
- `P0_WAITING_WHILE_P2_ACTIVE`
- `NEW_P2_ADMITTED_WHILE_P0_WAITING`
- `NEW_P2_ADMITTED_WHILE_P1_WAITING`（仅保留 P1 时适用）
- `UNAVOIDABLE_FUTURE_BLOCKING_NS`
- `FUTURE_RESULT_CONSUMPTION_LAG`
- `COMPLETED_BUT_NOT_CONSUMABLE_FUTURE_WORK`
- `SPECULATION_DEBT_COUNT/WORK/TOKENS/BYTES/AGE`
- `SPECULATION_BACKPRESSURE_ACTIVE`

论文机制证据优先使用 bounded speculation/scheduler idle opportunity 和 critical blocking，而不是把“无 active HTTP request”等同于 GPU idle。高 utilization 不能单独证明调度有效。

### Work/accounting

至少报告：

- logical calls
- physical transports/retries/failures
- input/output tokens
- per callsite/kind/source counts
- durable sources
- graph/entity/edge/summary counts
- work amplification ratio
- route/replica balance
- cache hits/misses/state

### Critical path

离线构建可复核的 counterfactual DAG；区分 observed、inferred 和 unknown edges。不要为了完美 DAG 推断 provider 内部不可见的 batching/kernel 细节。

---

## 12. FULL QA

每个有效正式 construction attempt 都对应一次 FULL QA：

`construction timing end → seal graph → FULL QA`

QA 时间绝不能进入 `T_build`。同一个冻结 QA output/judge 只执行预注册次数，不因结果不理想重复 judge。A/B/C 使用完全相同的 60-question inventory、Reader、Judge 和失败规则。

question 38 及官方 anomaly 保留、单独披露，不得删题、替换 gold、把 evaluator failure 当 wrong，或只报告有利 subset。

---

## 13. 统计与报告

replicates 嵌套在 history 内，不能把 15 个 ratio 当 15 个 IID workload samples。

至少输出：

1. 每个 history × replicate × arm 的 raw result；
2. 每个 history 内三个 replicate 的中心与离散程度；
3. 5 个 history-level A/C paired effects；
4. overall geometric mean speedup；
5. history-clustered/hierarchical bootstrap uncertainty；
6. 全部 5 个 per-history effects，不能只给 overall CI；
7. B ceiling 单独表；
8. quality paired delta/disagreement；
9. mechanism：critical path、idle opportunity、work amplification、tokens、route/cache；
10. invalid/replacement ledger 和 epoch history。

由于只有 5 个顶层 histories，95% CI 只能作为有限 cluster 下的 descriptive uncertainty，不得包装成强渐近推断。raw per-history effect 是第一等结果。

若最终方法是 fixed，可执行预注册的非选择性邻域 sensitivity；它只能解释 robustness，不能重新选择主方法或替换主表。

---

## 14. 最终必须一次性交付的 artifacts

至少生成：

- `CURRENT_HEAD_GAP_AUDIT.json/md`
- `EVALUATED_IMPLEMENTATION_IDENTITY.json`
- `FOUR_GATE_RESULT.json/md`
- `V61_PARAMETER_IDENTITY.json/md`
- `SCHEDULER_PROBLEM_AUDIT.json/md`
- `HEURISTIC_NECESSITY_AUDIT.json/md`
- `SCHEDULER_RESOURCE_CREDIT_MAP.json/md`
- `SCHEDULER_STRESS_TEST_RESULT.json/md`
- `HISTORICAL_SCHEDULER_NEGATIVE_EVIDENCE.json/md`
- `ADAPTIVE_DECISION.json`
- `FROZEN_DEV_WORKLOAD_MANIFEST.json/md`
- `AUTORESEARCH_STATE.json`
- `AUTORESEARCH_LEDGER.jsonl`
- 每个 candidate 的 `DIAGNOSTIC_DECISION.json`
- 使用文献时的 `REFERENCE_DECISION_CARD*.json/md`
- `FINAL_METHOD_SPEC.json/md`
- `PRECANARY_METHOD_SEAL.json`
- `FINAL_METHOD_FROZEN.json`
- `ENGINEERING_CANARY_RESULT.json/md`
- `FORMAL_PREREGISTRATION.json/md`
- `FORMAL_CAMPAIGN_MANIFEST_SEAL.json`
- `EARLY_SCIENTIFIC_DIAGNOSTIC.json/md`
- 45 个 expected construction cells 的 raw artifacts
- 对每个有效 construction 的 FULL QA artifacts
- `INVALID_ATTEMPT_LEDGER.jsonl`
- `FORMAL_CONSTRUCTION_TABLE.csv/json`
- `FORMAL_QUALITY_TABLE.csv/json`
- `PER_HISTORY_PAIRED_EFFECTS.csv/json`
- `MECHANISM_AND_CRITICAL_PATH_REPORT.json/md`
- `CACHE_RESOURCE_FAIRNESS_REPORT.json/md`
- `STATISTICAL_ANALYSIS.json/md`
- `CLAIM_SUPPORT_MATRIX.json/md`
- `FINAL_THREE_ARM_EXPERIMENT_REPORT.md`
- `REPRODUCTION_COMMANDS.md`

`CLAIM_SUPPORT_MATRIX` 至少逐项给出：

- same-resource `T_build` reduction
- quality delta / non-inferiority（若预注册）
- reduced scheduler idle opportunity
- reduced critical-path latency
- bounded future physical admission / zero new P2 while P0 waits
- future interference and unavoidable blocking
- frozen declared logical-work/input/publication contract preserved
- scheduler-induced physical/token/work amplification
- 独立声明的 work-reduction extension 对 logical work 的影响（若不存在则 `NOT_APPLICABLE`）
- speculation debt / consumption lag / backpressure behavior
- Native fairness
- cross-history consistency
- cross-hardware generality（未评测就写 `NOT_EVALUATED`）

矩阵必须按以下三层 claim hierarchy 组织，不能从结果层直接跳到方法结论：

### Layer 1 — Necessary validity conditions

- correctness、authoritative publication 和 fresh-write UUID 语义成立；
- A 保持 strict upstream Native identity，A/B/C 的资源、数据、模型和 evaluator 公平；
- 每臂各自冻结并声明的 logical-work/input/publication contract 被遵守；
- quality pipeline、trace 和 artifacts 足以解释结果。

任一必要条件失败，该 cell/epoch 只能是 INVALID 或相应 claim 的 `NOT_EVALUABLE`；性能数字不能补偿正确性和公平性失败。

### Layer 2 — Mechanism evidence

- C 是否实际减少了可避免的 serialization、scheduler idle opportunity 或 P0 critical wait；
- priority、resource credit、bounded speculation 与 backpressure 是否按冻结状态机执行；
- future interference、unavoidable blocking、speculation debt 和 work amplification 是否有界且可解释；
- scheduler effect 与独立 work-reduction extension 的 effect 能否分开归因。

### Layer 3 — Outcome evidence

- `T_build` paired effect、跨 history 一致性和 uncertainty；
- work/token/resource cost；
- FULL QA quality delta 或预注册 non-inferiority 结论。

解释必须遵守：

- idle opportunity 降低但 `T_build` 未降低：是非关键路径机会或有效负面机制结果，不能声称端到端收益；
- `T_build` 降低但 work/token amplification 明显：只能报告 latency–resource trade-off，不能声称纯调度效率提升；
- `T_build` 降低、critical wait 同步降低且 scheduler-induced work 基本不增：才是最强的 scheduler-mechanism 支持；
- quality 未达到预注册要求：不得用性能收益声称总体方法成立；
- 未进行跨硬件实验：不得把当前机器上的 resource-aware 结果外推为跨硬件 generality。

最终状态只能是客观状态，例如：

- `PREREGISTERED_CLAIM_SUPPORTED`
- `PERFORMANCE_SUPPORTED_QUALITY_INCONCLUSIVE`
- `EXPERIMENT_COMPLETE_BUT_METHOD_NOT_SUPPORTED`
- `EXPERIMENT_COMPLETE_WITH_MIXED_CLAIM_SUPPORT`
- `VALID_NEGATIVE_METHOD_RESULT`
- `EXTERNAL_BLOCKER_WITH_REPRODUCIBLE_EVIDENCE`

不要自动声称 `PAPER_READY`；novelty、positioning 和 reviewer bar 需要另行人工判断。

---

## 15. 硬 STOP 与非 STOP

只有以下情况允许停止并等待用户：

- 需要新的凭证、付费授权、硬件权限或用户才能做的外部操作；
- 数据/模型/服务不可获得，且已完成有边界的诊断与最多 3 次合理恢复；
- 发现必须改变论文核心问题或三臂定义的冲突；
- 用户工作树存在无法安全绕开的重叠改动；
- AutoResearch 预算已用尽、且不存在任何通过 G1-G4 与 scheduler safety 的合法 C 身份；若 fixed C 仍合法，预算耗尽只结束 adaptive 搜索，必须继续主实验。

以下不是停止理由：

- 一次测试失败；
- 一个小结果变慢；
- provider 短暂错误；
- 某个 candidate 被拒；
- 发现需要查论文或官方代码；
- 需要补最小 trace 或复现；
- 正式出现一个 invalid attempt。

但“不要停止”不代表必须强行得到正结果。科学上有效的负结果、混合结果和不确定结果都是合法终点。

---

## 16. 最终执行顺序

严格按下列顺序推进：

`HEAD/dirty-tree audit`

→ `current implementation identity`

→ `G1-G4 minimal closure`

→ `H5 scheduler problem/resource-credit/historical-negative audit`

→ `provider-free scheduler stress tests`

→ `freeze and seal non-overlapping DEV-D workload manifest`

→ `bounded pre-freeze AutoResearch on non-formal dev evidence`

→ `FINAL_METHOD_SPEC + PRECANARY_METHOD_SEAL`

→ `2-source three-arm engineering canary`

→ `correctness/observability fix loop if needed`

→ `FINAL_METHOD_FROZEN`

→ `formal preregistration + 45-cell manifest seal`

→ `history 0: three replicates, each replicate runs A/B/C`

→ `sealed EARLY_SCIENTIFIC_DIAGNOSTIC: continue unless preregistered safety pathology`

→ `history 1 ... history 4, one whole history at a time`

→ `each valid construction → sealed graph → FULL QA`

→ `offline trace reduction and clustered paired analysis`

→ `CLAIM_SUPPORT_MATRIX + final report + reproduction commands`

不要再次停在“建议下一步运行 canary”。如果四门、方法冻结和服务条件满足，就实际运行 canary；canary 通过后就实际封印并启动 formal；formal 完成后就实际完成 FULL QA 和最终报告。

## Primary references

- AutoResearch program: https://github.com/karpathy/autoresearch/blob/master/program.md
- Parrot, OSDI 2024: https://www.usenix.org/conference/osdi24/presentation/lin-chaofan
- Sarathi-Serve, OSDI 2024: https://www.usenix.org/conference/osdi24/presentation/agrawal
- DistServe, OSDI 2024: https://www.usenix.org/conference/osdi24/presentation/zhong-yinmin
- NanoFlow, OSDI 2025: https://www.usenix.org/system/files/osdi25-zhu-kan.pdf
- Llumnix, OSDI 2024: https://www.usenix.org/conference/osdi24/presentation/sun-biao
- vLLM/PagedAttention, SOSP 2023: https://doi.org/10.1145/3600006.3613165
- MemoryAgentBench official repository: https://github.com/HUST-AI-HYZ/MemoryAgentBench
- MemoryAgentBench paper: https://arxiv.org/abs/2507.05257
- OSDI artifact guidance: https://www.usenix.org/conference/osdi26/call-for-artifacts
