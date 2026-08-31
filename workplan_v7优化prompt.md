# MemBind V7 DMSV：最终 Agent 执行 Prompt

> 日期：2026-08-31
>
> 目标：先优化并冻结 `workplan_v7.md`，再严格执行 provider-free Phase 2B；本轮在 B4 停止。
>
> 科学目标：形成能够支撑系统/ML 系统顶会的方法边界，而不是为了延续项目堆实现。

---

## 0. 最高优先级停止约束

> **若 B1 已经证明 BaseView 没有合法可行路径，或 `previous_episodes` 等结构性依赖使 dominant `dedupe_nodes.nodes` request 在相邻 state 下必然变化，则立即停止 DMSV 主线，不得为了继续项目而先实现 Top-K maintainer。只有明确存在 `MAIN_TRACK_CANDIDATE` 路径时，Top-K maintainer 才能作为主方法继续；否则只能如实封存为 `KERNEL_ONLY`、`SCALABILITY_TRACK` 或 `NULL`。**

此约束高于后文所有阶段任务。不能用“代码已经规划”“kernel 理论漂亮”“可能在更大规模有效”绕过它。

---

## 1. 你的角色与本轮唯一目标

你是 MemBind V7 的方法论与系统研究 Agent。你必须用仓库真实代码、真实版本、sealed evidence 和 primary-source related work 作判断；不能把当前 plan 的详细程度当作正确性证据。

本轮严格串行执行两个 Stage：

1. **Stage A — Methodology / workplan 审计、优化、冻结**；
2. **Stage B — 仅执行冻结后的 provider-free Phase 2B（B0–B4）**。

在 Stage A 完成并写入 `WORKPLAN_FROZEN_FOR_PHASE2B` 之前，不得执行 Stage B。B4 完成后必须停止并向用户汇报，不得自动开始 Phase 3A observer、Phase 3B、provider/live treatment 或 full evaluation。

本轮最终必须回答三个问题：

1. Base view 是否存在合法且及时的生成/维护路径？
2. 占 Node 阶段绝对主导成本的 `dedupe_nodes.nodes` LLM call，能否通过 delta-maintained semantic view 被 exact 避免或局部化？
3. 若不能，DMSV 应降级为 `KERNEL_ONLY`、`SCALABILITY_TRACK` 还是 `NULL`？

“实现了多少代码”不是成功标准。

---

## 2. 冻结的科学身份

### 2.1 不得改变的 predecessor

Frozen V6 是唯一执行基座。保持其已冻结合同，包括：

- `lookahead=2`；
- `future_cap=1`；
- exact extraction capture/replay；
- authoritative-first admission；
- ordered authoritative publication；
- no speculative pre-publication write；
- fail-closed；
- 无合法 reuse 时退化为 V6 加可测的小额 seam/validation tax。

不得修改 V6 Core、B0 定义、B1 身份或 sealed evidence。B0 仍是 Native serial headline baseline；B1 仅是 relaxed-order upper bound。

### 2.2 V7 的新中心

论文中心改为：

> **DMSV — Delta-Maintained Semantic Views for Stateful Agent Memory Construction**

核心关系是：

```math
V_{t+1}^{inc}=Maintain(V_t,\Delta_t)
=V_{t+1}^{native}
```

其中 speculation 只负责提前物化合法的 base view；DVSR 是 runtime validation/repair protocol，不是论文的首要科学假设。

Semantic view 必须分层定义，禁止把整条 LLM execution 塞进一个含混的 `V`：

```text
V_rank
  -> V_prompt
  -> V_request
  -> ResponseArtifact
  -> Continuation
```

主要被增量维护的是 `V_rank / V_prompt / V_request`。`ResponseArtifact / Continuation` 是派生结果，其 reuse、recompute 和 reconvergence 必须单独记账。

### 2.3 顶会 novelty 最低线

以下均是已有思想，不能单独声称为贡献：speculation、cache、OCC、dirty propagation、memoization、materialized view、cost-aware admission。

可争取的新颖性必须落在真实 Agent Memory construction 的组合问题上：

- mutable semantic state 上的 operator-specific exact delta maintenance；
- versioned base-view availability 与 ordered memory continuation；
- rank、prompt-visible projection、canonical request 的跨 state affectedness；
- exact single-call response reuse；
- delta-local recomputation与 descendant reconvergence；
- critical-path-aware offline/online economics。

若最终只等价于“相同 prompt cache”或“一个未覆盖当前关键路径的 Top-K micro-kernel”，不得包装成主论文方法。

---

## 3. 已知证据：必须复用，禁止为流程完整重跑

Stage A 必须把下列事实定位到仓库中的具体 sealed artifact/trace/report；找不到原始出处时标记 `MISSING_PROVENANCE`，不得把本 prompt 当成证据：

- V5/V6 后，stateful Native work 是剩余 critical path；V6 已基本隐藏 future extraction preparation；
- 三条 sealed 8B trace 中 Node 是最大单 phase，约占 `43.5%–48.2%`；Summary 第二，Edge 仍不可忽略；
- 当前 Node 阶段约 `99.6%` 的时间来自 `dedupe_nodes.nodes` LLM，而 exact cosine retrieval 仅约 `8–11s`；
- 当前 complete pairs 中只有 `1/29` 满足及时的 native base-view availability；`V6 PreparedArtifact ready` 不等于 `BaseView ready`；
- V4 的 `Future NodeResolve legal speculation window = 0` 属于旧 frontier/read 协议，不能自动否定新的 versioned/persistent base-view 路径；
- V6 的 `304/370` comparison miss 全部为 `missing_side`，不能当成 stateful request instability；
- 旧 V7-B/r16 NULL 只否定其旧 architecture/boundary，不能自动否定 DMSV；
- workload 存在 backlog/slack，但这不证明 base view 合法或在线经济收益为正。

对现有 plan 的每个 RQ、Gate 和指标标注：

- `ALREADY_PROVEN`
- `PARTIALLY_SUPPORTED`
- `MISSING_FIELD`
- `REQUIRES_NEW_PROVIDER_FREE`
- `REQUIRES_NEW_OBSERVER`
- `REQUIRES_NEW_LIVE`

已有 sealed evidence 能回答的问题禁止重跑。

---

## 4. 全局执行禁令

本轮禁止：

- provider 调用、live Graphiti treatment、authoritative graph 写入；
- Phase 3A observer、Phase 3B、held-out formal evaluation；
- scheduler、lane、quota、`lookahead`、`future_cap` 参数搜索；
- 修改 Frozen V6/B0/B1 或删除旧 V7 NULL/失败证据；
- 使用旧 V7-FRESH 作为 Native oracle；
- 把 diagnostic/withdrawn/unauthorized 结果升级为正式证据；
- 用人为延迟 authoritative publication 创造 BaseView window；
- 把 `UNKNOWN` 静默改成 `STABLE`；
- 把理论 work reduction、provider-free speedup 或 scale crossover写成 online end-to-end speedup；
- 在 development histories 选择方法后，再用相同 histories 声称 held-out 95% CI；
- 未经用户明确授权进行 commit、push、删除、恢复或清理已有工作树改动。

所有新增模块或实验都必须分别回答：

> 它解决哪一段已经实测存在的 V6 critical path？

> 为什么已有 sealed evidence 不能回答这个问题？

无法回答则不新增。

---

# Stage A — 先优化并冻结 `workplan_v7.md`

## A0. 只读确定真实项目状态

在修改文件前：

1. 检查仓库根目录、当前 branch、local HEAD、remote default branch/HEAD 与 2026-08-31 的目标 revision；
2. 不得默认当前 checkout、旧 8 月 30 日 workspace 或文件名即为用户所说的 `v7-run-831`；
3. 记录：
   - `audited_commit`
   - `audited_branch`
   - `audited_remote_head`
   - `audited_worktree_status`
   - `workplan_input_sha256`
   - Graphiti 实际 pin/version
4. 识别并保护已有用户改动；不得恢复、覆盖或顺手清理与本任务无关的 deleted/modified/untracked 文件；
5. 若 remote revision 无法核验，明确写 `REMOTE_IDENTITY_UNVERIFIED`，继续只读本地审计，但不得声称已核对真实 8 月 31 日版本。

只读遍历至少包括：

- 当前 `workplan_v7.md` 与所有 V4/V5/V6/V7 evidence audit/ledger/report；
- sealed V6/8B traces、critical-path 和 request/callsite artifacts；
- V6 prepare/publish/replay/provider seam；
- Graphiti 实际版本的 `add_episode`、Node resolve、candidate search、`dedupe_nodes.nodes` request assembly、Edge、attributes/summary 与 `_process_episode_data`；
- 当前已有 DMSV/DVSR observer 或 diagnostic 代码及其授权状态。

## A1. 建立 Existing Evidence Closure

创建或更新 append-only evidence audit。每个结论必须给出：

- artifact path；
- run/manifest/commit identity；
- measured field；
- evidence status；
- 能回答什么；
- 不能回答什么；
- 是否需要新实验及原因。

不得只引用二手总结里的数字。

## A2. 修正研究问题与算法身份

把 workplan 的论文叙事改为：

```text
Frozen V6 Prepared Future Source
        +
Legal Versioned Base Semantic View
        + actual DeltaState
        -> exact incremental view maintenance
        -> affected LLM-call detection
        -> unaffected exact reuse / affected native recompute
        -> descendant reconvergence
        -> ordered publication
```

DVSR 保留为执行协议；DMSV 是方法中心。不得删除 fail-closed、read tracking、actual touched-write delta、exact request identity、repair、reconvergence、no-reuse control 和 ordered publication。

## A3. 把 BaseViewAvailability 提升为最高风险 Gate

必须分别审计：

| 路径            | 含义                           | 必须证明                                                     |
| --------------- | ------------------------------ | ------------------------------------------------------------ |
| `BV-NATIVE`     | 直接在现有 V6 时间线上产生     | `t_base_ready <= t_authoritative_need`，且不延迟 authoritative publish |
| `BV-VERSIONED`  | 在合法一致 snapshot 上异步物化 | snapshot/epoch 正确性、生命周期、失败与 no-reuse tax         |
| `BV-PERSISTENT` | 跨 source 持续维护 query/view  | query reuse/coverage、每 commit 维护成本、storage/GC、staleness |

每条路径都要计入：

- initial `BaseMaterializationCost`；
- per-commit `DeltaMaintenanceCost`；
- snapshot/epoch 与 storage/GC；
- failed/unused materialization；
- forced-miss/no-reuse seam tax；
- 对 Frozen V6 prompt/work/DB read/ordered publication 的影响；
- online foreground interference 仍为 `REQUIRES_NEW_LIVE`，本轮不得伪造。

首次 query 的 base build 必须写成：

```math
T_{first}=T_{materialize}(N)+T_{maintain}(|\Delta|)
```

不能假装首次 query 只有 `O(|Delta|)`。

## A4. 加入 Dominant Node Request Closure

在 workplan 中把下面的问题设为 B1 与 G2B 的共同硬条件：

> DMSV 能否保持或局部化占 Node 阶段约 99.6% 成本的 `dedupe_nodes.nodes` canonical request，而不只是加速 8–11 秒 cosine scan？

必须审计 canonical request 的完整 prompt-visible closure：

- extracted nodes；
- ordered candidate IDs/scores；
- candidate name、labels、summary、attributes 等 payload projection；
- current episode content；
- `previous_episodes`；
- entity types/schema；
- deterministic/exact/fuzzy resolution branches；
- unresolved membership、batch shape 和 order；
- serialization/template；
- model/config/schema/index epoch。

特别验证：当 `V_rank` 不变时，`previous_episodes` 或其他结构字段是否仍使 `V_request` 必然变化。不能用 Top-K ID 相同替代 canonical request 相同。

## A5. 冻结严谨的 Top-K 复杂度合同

只允许声称：

```math
T_{patch}=O(|\Delta|d+K\log K)
```

当 certificate/frontier buffer 足以 exact patch 时成立。发生删除/修改旧 frontier、tie、buffer 耗尽、filter/config epoch 变化或证据不完整时：

```math
T_{refill}=O(Nd)
```

workplan 必须要求报告：

- patch rate；
- refill rate；
- buffer size/memory overhead；
- initial materialization cost；
- per-delta maintenance cost；
- amortized cost；
- exact full-scan oracle equality；
- tie/unknown/fallback rate。

禁止把条件性 bound 写成无条件 `O(N) -> O(|Delta|)`。

## A6. 修正 economics、nested cut 与 reconvergence 记账

### Offline/online 分离

Phase 2B/3A 之前只能计算：

```math
OfflineValue=
StableHiddenCP
-ValidationCost
-VisibleRepairCP
-FailedSpeculationCost
-SeamTax
```

`ForegroundInterference` 只能在未来获批的 minimal live 中实测：

```math
OnlineNetBenefit=OfflineValue-ForegroundInterference
```

G2B/Phase 3A authorization 不能声称 online speedup。

### Nested cut 的边际价值

若比较：

- `CUT-N = Node`
- `CUT-DEEP = Node -> Edge -> Attributes/Summary`

必须报告：

```math
DeltaBenefit_{DEEP|N}=Benefit(CUT\text{-}DEEP)-Benefit(CUT\text{-}N)
```

不得把整个 deep prefix 的总收益归因给 Summary。避免使用误导性的 `DVSR_SUMMARY_V1` 名称。

### Reconvergence 的 DAG 价值

使用：

```math
Value=
ReuseHiddenCP
+ReconvergenceSavedDescendantCP
-VisibleRepairCP
-ValidationCost
-FailedSpeculationCost
-SeamTax
```

若当前 Node LLM 已 fresh 重跑，Node 结果随后 reconverge 不代表 Node LLM 成本被省掉；只有因此保住已物化 descendant work 时，才能记入 `ReconvergenceSavedDescendantCP`。

## A7. 冻结 development / held-out 隔离

在 workplan 里显式列出或由不可变规则生成：

- `development/operator-selection histories`；
- `held-out formal-evaluation histories`。

本轮不得读取 held-out outcome 作设计决策。若 history 数量不足以支持稳定 cluster CI，development selection 使用预注册的 worst-history 条件或“每个 development history 的 conservative value 均为正”，不要伪装 95% CI。正式 95% history/source-clustered CI 留给未来获批的 held-out evaluation。

## A8. 自审、冻结与 Stage B 授权

修改后的 `workplan_v7.md` 必须完成：

- terminology/phase/gate/reference 一致性检查；
- 不重复已有 sealed 实验检查；
- Frozen V6/no-reuse degeneration 检查；
- highest-priority kill-switch 在 Phase 2B 与 G2B 的镜像检查；
- `git diff --check`；
- 记录 output SHA-256。

只有全部通过，才在 append-only ledger/report 写入：

```text
WORKPLAN_FROZEN_FOR_PHASE2B
```

若未通过，停止并报告 `WORKPLAN_NOT_FROZEN`；不得进入 Stage B。

---

# Stage B — 仅执行 provider-free Phase 2B

## B0. Preregistration 与 harness 边界

在写实现前冻结：

- exact state/query/filter/config identities；
- `V_rank / V_prompt / V_request` schema；
- full-scan/fresh-request oracle；
- delta types与 `UNKNOWN -> fallback` 规则；
- base-view availability判定；
- dominant-request preservation/localization判定；
- work/latency accounting字段；
- verdict decision table；
- provider-free/no-write proof。

Phase 2B 可以使用：

- 已 sealed trace 和现有本地 artifacts；
- deterministic fixtures；
- fake/stub embedder 或已保存 embedding；
- canonical request builder/serializer；
- 只读 DB snapshot/export（若现有证据需要且不写库）；
- provider-free unit/property/metamorphic tests。

不得连接真实 LLM provider，不得产生 authoritative graph write。

## B1. 先做两个最高风险 Closure

### B1.1 BaseViewAvailability Closure

用现有时间线和最小 provider-free probe 分别判定 `BV-NATIVE / BV-VERSIONED / BV-PERSISTENT`：

- base view 在哪个 state/version 上构建；
- query 何时已知；
- `t_base_ready` 和 `t_authoritative_need`；
- 一致 snapshot 是否存在；
- 是否会阻塞/延迟 authoritative publication；
- view 是否值得跨 source 持续维护；
- 首次构建、每 commit 更新、未使用 view、GC 与 no-reuse tax。

不能仅证明“理论上可以存一个 view”；必须证明它与 Frozen V6 的合法执行时间线兼容。

### B1.2 Dominant Request Closure

用 Graphiti 实际版本的 request assembly 与 canonical serialization，构造最小相邻-state反例矩阵。至少分别改变：

- Top-K membership/order；
- candidate payload；
- `previous_episodes`；
- unresolved node membership/order；
- batch shape；
- episode/schema/model/config epoch。

回答：

- 哪些 delta 只改变 `V_rank`；
- 哪些继续传播到 `V_prompt/V_request`；
- 一个 changed node 是否污染整个 `dedupe_nodes.nodes` batch；
- 是否存在保持原生语义的 call-level reuse；
- 当前 Graphiti 是否已经存在不改变 Memory 算法与原生 call boundary 的合法 localization seam；
- 任何需要拆分原生 batch call 才能成立的 localization，必须标为新的 algorithm identity，本轮不得用它绕过 dominant-request Gate。

### B1 强制判定

按以下顺序 fail-closed：

1. 所有 `BV-*` 均无合法路径：seal `DMSV_BASE_VIEW_UNAVAILABLE`，停止；
2. 有 base view，但 `previous_episodes` 等结构性依赖使当前原生 dominant request 在相邻 state 下必然变化：seal `DMSV_DOMINANT_CALL_UNAVOIDABLE`，停止主线；不得临时拆分 batch call 绕过此结论；
3. 只有 retrieval kernel 的结构收益，不覆盖当前 workload 的显著 critical path：最多 `KERNEL_ONLY`；
4. 只有经 scale crossover 才有价值：最多 `SCALABILITY_TRACK`；
5. 只有未触发 1/2，存在合法 base view，并且 dominant LLM request 可被 exact 保持、由当前原生 call boundary 合法局部化，或 retrieval 已由 sealed evidence 证明覆盖当前 workload 的显著 critical path，才能输出 `MAIN_TRACK_CANDIDATE` 并进入 B2。

`hit rate` 不是独立生死 Gate；判定必须由 exactness、合法性和 critical-path-weighted value 共同决定。

若 B1 未输出 `MAIN_TRACK_CANDIDATE`，不得执行 B2/B3；直接进入 B4 封存结论。

## B2. 条件执行：Exact Top-K Delta Maintainer TDD

仅当 B1=`MAIN_TRACK_CANDIDATE` 时执行。实现必须独立于 live treatment，并以 Graphiti 当前 full exact cosine query 为 oracle。

至少覆盖：

- insert below/above cutoff；
- update non-member/member embedding；
- candidate payload-only update；
- delete non-member/member；
- sufficient `Top-(K+B)` buffer；
- buffer exhaustion -> full refill；
- cutoff tie / no deterministic secondary order -> `UNKNOWN`/fallback；
- group/filter/K/min-score变化；
- config/model/embedder/index epoch变化；
- multi-object mixed delta；
- empty delta；
- randomized/property test：incremental result与 full-scan ordered result exact equality。

记录每个 case 的 read-set、delta、certificate decision、patch/refill、oracle result 和复杂度/accounting字段。任何无法证明的情况必须 fallback full query。

## B3. 条件执行：Layered View Affectedness 与经济记账 TDD

仅当 B2 exactness 全部通过时执行。验证：

```text
DeltaState
  -> affected V_rank
  -> affected V_prompt
  -> affected V_request
  -> response reuse/native recompute
  -> descendant reconvergence
```

要求：

- canonical request identity基于完整逻辑输入，不基于 transport/request ID；
- unaffected request只允许 exact single-call response reuse；
- affected request走原生 fresh recompute；provider-free阶段只生成 plan，不调用 provider；
- changed batch导致的 whole-call invalidation必须如实记账；
- reconvergence只记 `SavedDescendantCP`，不能追溯虚构已重跑 operator 的收益；
- forced miss/no-reuse path的 work结构与 Frozen V6 一致，除预注册 seam/validation tax；
- 不得在此实现 runtime admission、GPU scheduling 或 online publish。

## B4. Seal、分类并停止

无论结果正负，生成 append-only Phase 2B report/ledger，至少包括：

- audited commit/worktree/workplan hashes；
- existing-evidence closure；
- BaseViewAvailability verdict及证据；
- dominant-request closure verdict及反例矩阵；
- 若执行 B2/B3：exact oracle结果、patch/refill rate、buffer/memory、materialization/maintenance/amortized cost、affectedness与critical-path accounting；
- 未执行项及原因；
- 所有 invariant/forbidden-action proof；
- novelty classification；
- 下一阶段是否获授权。

最终状态只能是以下之一：

| 状态                             | 含义                                                         | 本轮之后                                       |
| -------------------------------- | ------------------------------------------------------------ | ---------------------------------------------- |
| `PHASE3A_AUTHORIZED`             | 仅表示 provider-free 证据支持一个 `MAIN_TRACK_CANDIDATE`     | 停止；等待用户授权最小 cross-snapshot observer |
| `KERNEL_ONLY`                    | exact incremental kernel成立，但未覆盖当前 dominant critical path | 停止；不得包装为主方法                         |
| `SCALABILITY_TRACK`              | 只在预注册 scale crossover 下可能有价值                      | 停止；不得声称当前 end-to-end 收益             |
| `DMSV_BASE_VIEW_UNAVAILABLE`     | 无合法及时 base view                                         | 停止 DMSV 主线                                 |
| `DMSV_DOMINANT_CALL_UNAVOIDABLE` | dominant Node LLM 无法保持或原生局部化                       | 停止 DMSV 主线                                 |
| `NULL`                           | exactness、合法性或经济必要条件失败                          | 停止并保留负结果                               |
| `BLOCKED`                        | 版本、证据、依赖或测试基础设施不足以判定                     | 停止并列出最小解除条件                         |

即使状态为 `PHASE3A_AUTHORIZED`，也不得在本轮执行 Phase 3A。

---

## 5. Stage A/B 必需交付物

使用仓库现有命名/ledger 规范；若无对应文件，创建最小且清晰的 append-only 文件。至少交付：

1. 优化并冻结后的 `workplan_v7.md`；
2. `DVSR_EXISTING_EVIDENCE_AUDIT` 的 DMSV 修订/附录，保留旧审计与 NULL；
3. Phase 2B preregistration；
4. provider-free tests/fixtures（仅在对应 Gate 授权时）；
5. Phase 2B report/ledger；
6. 对全部新增/修改文件执行格式、测试和 `git diff --check`；
7. 最终汇报精确列出 changed files、未触碰的 Frozen files、测试命令/结果、artifact hashes 和最终状态。

不得为了交付物齐全创建没有回答真实未知问题的空模块。

---

## 6. 最终回复格式

最终回复必须以结论开头，并按以下顺序简洁报告：

1. `FINAL_STATE`；
2. Stage A 是否成功冻结，`workplan_v7.md` 的 hash 与关键方法修正；
3. BaseViewAvailability 结论；
4. dominant `dedupe_nodes.nodes` request 结论；
5. B2/B3 是否执行，为什么；
6. provider-free exactness/accounting 结果；
7. changed files 与验证；
8. 明确声明已在 B4 停止，未执行 provider/live、Phase 3A/3B 或 full evaluation；
9. 若为负结果，给出最小、可证伪的解除条件，不提出未经证据支持的新机制。

不要用“方向看起来不错”“可以继续探索”替代 Gate verdict。