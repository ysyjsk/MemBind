# MemBind V7 Workplan：DVSR方法论审计与优化执行计划

> 版本：v3-final-methodology
> 日期：2026-08-30
> 状态：METHOD SELECTION前；不授权DVSR live treatment
> 取代：上一版以DVSR-NODE-V1为先验第一实现的计划
> 研究主线：Frozen V6 substrate＋cross-snapshot stateful speculation＋exact validation/repair＋ordered publication

---

## 0.审计结论与文件地位

当前DVSR方向值得继续，但上一版计划不能直接进入实现。问题不在DVSR主线，而在方法选择和证据顺序：

1. Node因为证书容易形式化而被过早冻结；
2. 已有sealed V6 trace没有先完成Evidence Closure；
3. certificate尚未经过反例TDD，就被安排用于正式cross-snapshot判断；
4. Gate使用固定request hit-rate，而不是critical-path-weighted net benefit；
5. Attributes、Summary和更深stateful prefix被混成同一operator；
6. V6的previous-context变化没有被准确纳入semantic root；
7. 新seam仍有重新形成V7-FRESH式旁路的风险；
8. offline operator selection错误计入了只能通过live测得的foreground interference；
9. nested cut没有分离CUT-N base benefit与deeper-prefix marginal benefit；
10. reconvergence被标量公式错误地视作当前operator自身的saved work；
11. development selection与held-out formal inference尚未冻结到具体history集合。

本版保留DVSR，不退回旧Stable IR，也不重新设计Memory算法。最终研究问题冻结为：

> 对同一个由V6产生的prepared future source，在相邻authoritative state上，哪些stateful Graphiti计算能够通过semantic read validation得到exact single-call reuse或exact reconvergence；这些机会在development histories上是否具有正offline value，并在held-out live中计入foreground interference后真实缩短V6剩余关键路径？

本文件当前只授权：

- existing-evidence离线归约；
- scientific identity审计；
- V6-based prepared/no-reuse seam；
- provider-free certificate反例TDD；
- 不发布speculative结果的cross-snapshot observer；
- operator selection。

在Operator Selection Gate通过前，禁止：

- 实现DVSR live reuse；
- 改写V6 Core；
- 使用旧V7-FRESH作为fresh oracle；
- 将observer结果写入authoritative graph；
- 为了补流程而重跑已有sealed实验；
- 根据结果事后调整economic Gate或operator定义。

---

# A.KEEP：确认正确且不得删除的设计

## A1.研究边界

以下主线继续冻结：

$$
\text{speculate}
\rightarrow
\text{track reads}
\rightarrow
\text{validate delta}
\rightarrow
\text{reuse/repair}
\rightarrow
\text{exact reconvergence}
\rightarrow
\text{ordered publication}
$$

V7只攻击V6已经暴露出的stateful critical path，不重新研究extract_nodes或extract_edges的提前执行。

## A2.前代与证据纪律

- B0仍是Native headline anchor；
- V6 Core仍是冻结的直接前代与DVSR执行基座；
- B1只保留为relaxed-order upper bound，不是正确性oracle；
- 旧V7-A/V7-B/V7-FRESH失败证据append-only；
- 旧NULL结论只作用于其冻结architecture和boundary；
- 新方法不得覆盖、删除或重解释旧失败；
- 所有Gate输出hash-sealed、machine-readable、append-only。

## A3.正确性合同

以下合同继续作为hard invariant：

- speculative phase不得有pre-publication DB write；
- authoritative source只能按source sequence发布；
- publication不得等待future speculation；
- mixed snapshot一律UNKNOWN；
- 不完整read-set一律UNKNOWN；
- tie、模型/config epoch不一致、payload缺失一律UNKNOWN；
- validation不能证明时必须fresh；
- forced miss必须退化为V6 substrate＋已测的小seam/validation tax；
- reused LLM output必须来自同一canonical logical request的single-call oracle；
- 不假设两个独立随机LLM调用response相同；
- repair后必须与同一provider-response分支的no-reuse continuation exact reconverge。

## A4.必要control

保留DVSR_NOREUSE_CONTROL，但重新限定其身份：

> 它不是新的from-scratch Graphiti实现，而是Frozen V6 executor、admission、transcript、context policy和ordered publication上的最小prepared/stateful seam；唯一关闭项是speculative reuse。

若该control改变stateful prompt、previous context、DB read集合、LLM request结构、continuation或canonical graph projection，必须先修control，禁止进入treatment。

---

# B.CHANGE：上一版必须修改的内容

## B1.取消DVSR-NODE-V1的先验冻结

在cross-snapshot evidence出现前，统一使用中性身份：

DVSR_OPERATOR_NEUTRAL_OBSERVER_V1

只有Offline Operator Selection Gate通过后，才生成唯一第一方法身份：

- DVSR_NODE_V1；或
- DVSR_DEEP_PREFIX_V1；或
- DVSR_NO_OPERATOR_OPPORTUNITY_NULL。

Node当前是证据支持更强的先验候选，但不是已冻结结论。

## B2.把Attributes/Summary拆开

Graphiti 0.29.3的真实DAG为：

$$
\text{prepared extraction}
\rightarrow
\text{node resolution}
\rightarrow
\text{edge resolution}
\rightarrow
\text{typed attributes + summary hydration}
\rightarrow
\text{publication}
$$

现有三条sealed V6 trace中：

- extract_nodes.extract_attributes调用数均为0；
- extract_nodes.extract_summaries_batch分别为64、63、67。

因此当前workload的Candidate B不是泛化的Attributes/Summary，而是：

> Summary/Hydration cut：在Node和Edge speculative prefix之后，提前执行summary batch及其后续hydration。

Typed Attributes当前不是实测热点，不得为了机制完整作为第一论文operator。只有未来workload真实触发typed attribute calls时再单独Gate。

## B3.把operator改成nested cut，而不是孤立函数

候选必须按可执行的nested cut比较：

- CUT-N：prepared extraction后执行Node Resolution；
- CUT-D：执行Node→Edge→Summary/Hydration至publication seam；
- CUT-E：把Edge作为直接复用目标；当前只保留后续扩展资格。

CUT-D包含CUT-N。它不能把整个prefix收益命名为“Summary收益”，也不能只比较CUT-N与CUT-D的总收益。必须同时报告：

$$
\Delta B_{D\mid N}=B(CUT\text{-}D)-B(CUT\text{-}N)
$$

该边际量才表示加入Edge＋Summary/Hydration deeper prefix后的净贡献。CUT-D的稳定性、miss成本和work amplification必须按整个prefix计费。

## B4.删除固定20% hit-rate生死线

request hit rate、candidate stability和validation ratio继续报告，但只作解释指标。

Economic Gate拆成两个层次。

G4只回答“哪个operator值得实现并进入live”，不使用尚未实测的foreground interference：

$$
\text{OfflineBenefit}
=\text{ReuseHiddenCP}
+\text{ReconvergenceSavedDescendantCP}
-\text{ValidationCost}
-\text{VisibleRepairCP}
-\lambda\cdot\text{FailedSpeculationWork}
-\text{SeamTax}
$$

G6才回答“真实在线是否加速”：

$$
\text{OnlineBenefit}
=\text{OfflineBenefit}
-\text{MeasuredForegroundInterference}
-\text{OnlineControlOverhead}
$$

其中λ在看结果前冻结，用于把浪费的resource-seconds转换为经济代价。OfflineBenefit、OnlineBenefit、latency与total work必须分别报告，禁止用估计interference让G4自洽，也禁止在G6漏计真实interference。

## B5.把certificate TDD移到正式observer之前

正式顺序改成：

Scientific identity
→ Existing Evidence Closure
→ V6-based no-reuse seam
→ observability＋provider-free adversarial TDD
→ cross-snapshot observer
→ operator selection
→ selected-operator repair/admission TDD
→ minimal live
→ full evaluation。

未经反例TDD的certificate只能记录字段，不能输出VALID，也不能参与机会率统计。

## B6.重新定义V6 semantic root

当前代码和测试明确对certified extraction安装strip_certified_previous_context。现有V6 request identity中，同一sealed context的222个node extraction记录均使用空previous_context digest。

因此本版不再写：

> V6已证明是相对B0的Native timing-only优化。

在新的paired B0动态request audit完成前，只能写：

> DVSR保持冻结V6 Core的逻辑输入和状态演化语义。

若论文必须声称相对B0 algorithm-preserving，则需独立完成B0↔V6 dynamic request identity audit。该问题不是DVSR的实现前置条件，但属于论文claim前置条件。

## B7.收缩模块数量

任何模块必须映射到实测V6关键路径。

Operator selection前只允许公共基础：

- existing evidence reducer；
- V6 prepared-object adapter；
- no-reuse stateful seam；
- read/delta/request observer；
- certificate schema与provider-free oracle。

Node-specific repair、Summary-specific repair、online admission和live epoch gate只能在对应Gate后实现。

---

# C.EXISTING_EVIDENCE：DVSR_EXISTING_EVIDENCE_AUDIT

## C1.状态词

每个RQ、Gate和metric必须使用以下唯一状态：

| 状态                  | 含义                                            | 允许动作                           |
| --------------------- | ----------------------------------------------- | ---------------------------------- |
| ALREADY_PROVEN        | 已有sealed或冻结artifact已足够回答              | 禁止重跑                           |
| PARTIALLY_SUPPORTED   | 已有证据支持一部分，但scope或字段不足           | 只补缺口                           |
| MISSING_FIELD         | 实验已发生，但checked-in artifact未保存所需字段 | 优先离线恢复；不能恢复才加observer |
| REQUIRES_NEW_OBSERVER | 需要新读-only/capture-only配对观测              | 禁止live reuse                     |
| REQUIRES_NEW_LIVE     | 只有真实资源竞争或在线发布能回答                | 必须在前置Gate后运行               |

Evidence Closure必须生成：

- DVSR_EXISTING_EVIDENCE_AUDIT.json；
- DVSR_EXISTING_EVIDENCE_AUDIT.md；
- artifact path、construction seal、commit、config和字段级provenance；
- 不可恢复字段清单；
- 禁止重跑清单。

## C2.当前8B sealed V6剩余关键路径

以下数字由三条现有sealed V6 native_trace离线归约得到，不需要新增实验。

| Context         |  V6 build |  Node Resolution |      Summary阶段 |  Edge Resolution | Publication |
| --------------- | --------: | ---------------: | ---------------: | ---------------: | ----------: |
| C0/e78d9a9be2e5 | 5774.875s | 2617.162s，45.3% | 1852.796s，32.1% | 1016.687s，17.6% |      1.882s |
| C1/6db1005726a9 | 5520.666s | 2659.782s，48.2% | 1890.137s，34.2% |  662.168s，12.0% |      1.915s |
| C2/8c4a5e4e66b5 | 7147.375s | 3111.095s，43.5% | 1970.097s，27.6% | 1751.754s，24.5% |      2.279s |

结论：

- Node是当前三条sealed V6中最大的单一stateful phase；
- Summary稳定为第二；
- Edge并非始终很低，在C2达到24.5%；
- publication本身不是V7目标；
- 当前实测热点支持CUT-N与CUT-D并列进入selection，不支持typed Attributes进入第一轮。

Artifact：

- saturated_fixed_work_baseline_v1_3/artifacts/mab-v1-3-live-firstpass-c0-recovery-methods-20260825-011/context-0/V6/e78d9a9be2e5
- saturated_fixed_work_baseline_v1_3/artifacts/mab-v1-3-live-firstpass-context1-20260825-014/context-1/V6/6db1005726a9
- saturated_fixed_work_baseline_v1_3/artifacts/mab-v1-3-live-firstpass-context2-20260825-015/context-2/V6/8c4a5e4e66b5

## C3.旧32B/V5阶段数字的正确作用域

历史单history结果：

- attributes/summary约697.13s；
- node resolution约519.50s；
- edge resolution约98.11s；
- future-ready slack最小103.881s、中位752.190s；
- future preparation基本隐藏。

这些数字来自较早32B/V5开发配置，作用是证明：

- V6之后stateful native确实是剩余问题；
- workload存在future slack；
- 单纯扩大preparation window不是主方向。

它们不能用于：

- 选择当前8B第一operator；
- 推断current CUT-N/CUT-D cross-snapshot稳定率；
- 推断current GPU interference；
- 替代三条sealed V6的phase decomposition。

Artifact：

- MemBind_V6_Graphiti_Autoresearch_Workplan.md
- saturated_fixed_work_baseline_v1_3/artifacts/sfwb-v1-3-v6-autoresearch-20260822-191241

## C4.已有负证据及其作用域

| 已有事实                                     | 状态           | 可证明什么                                                   | 不能证明什么                                      |
| -------------------------------------------- | -------------- | ------------------------------------------------------------ | ------------------------------------------------- |
| V4 future NodeResolve legal window=0         | ALREADY_PROVEN | 旧协议禁止跨authoritative frontier读旧state，因此旧legal window为0 | 不能否定DVSR显式读取稳定旧snapshot                |
| V6 304/370 comparison misses全部missing_side | ALREADY_PROVEN | shadow coverage不完整，没有aligned field mismatch            | 不能解释为stateful request instability            |
| 旧V7-B/r16 CSP=null、treatment=0             | ALREADY_PROVEN | 旧Stable IR/V7-FRESH boundary没有授权机会                    | 不能回答同一V6 prepared source跨S_i/S_i+1的稳定性 |
| V7-FRESH比B0慢50.138%                        | ALREADY_PROVEN | 旧fresh路径有巨大algorithm/mechanism tax，不能作DVSR oracle  | 不能否定V6-based stateful seam                    |
| workload存在backlog/slack                    | ALREADY_PROVEN | 有投机时间资源的必要条件曾出现                               | 不能给出candidate-specific hidden CP              |

对应artifact：

- paper-eval-v3/artifacts/paper_eval/membind_v4/V4_FINAL_DECISION.md
- MemBind_V7_Methodology_Workplan.md
- MemBind_V7_H1_ARCHITECTURE_RESCUE_REPORT_20260829.md
- MemBind_V7_SEALED_EVIDENCE_AUDIT_20260829.md
- MemBind_CURRENT_EXPERIMENT_REPORT.md

## C5.RQ与Gate字段审计

| 问题/字段                              | 状态                  | 已有答案                                                     | 下一合法动作                                               |
| -------------------------------------- | --------------------- | ------------------------------------------------------------ | ---------------------------------------------------------- |
| V6后是否仍有stateful CP                | ALREADY_PROVEN        | 三条8B sealed V6约94%–96%的build时间落在Node/Summary/Edge及其余native工作 | 禁止重跑characterization                                   |
| Node、Summary、Edge当前CP权重          | ALREADY_PROVEN        | 见C2                                                         | 离线封装reducer和provenance                                |
| typed Attributes是否为当前热点         | ALREADY_PROVEN        | 三条trace调用数均为0                                         | 从第一轮候选删除                                           |
| future preparation是否已基本隐藏       | ALREADY_PROVEN        | 旧V5分解与V6设计已回答                                       | 不再研究extraction                                         |
| 当前candidate-specific可提前窗口       | MISSING_FIELD         | checked-in sealed目录未保存PREPARE_READY/frontier明细        | 先检查可恢复live journal；不可恢复则在新observer记录       |
| 同source跨S_i/S_i+1的semantic reads    | REQUIRES_NEW_OBSERVER | 无paired same-source数据                                     | 最小read-only observer                                     |
| prompt-visible projection稳定性        | REQUIRES_NEW_OBSERVER | 无字段                                                       | 同一observer记录projection digest                          |
| canonical LLM request exact match      | REQUIRES_NEW_OBSERVER | 旧304/370不能回答                                            | 同一observer记录canonical request                          |
| request变化后的exact reconvergence     | REQUIRES_NEW_OBSERVER | 旧r16不是同一prepared source                                 | paired single-call branch oracle                           |
| Node top-K delta certificate soundness | REQUIRES_NEW_OBSERVER | 代码路径可形式化，但未有反例覆盖后的真实trace                | 先provider-free TDD，再observer                            |
| Summary batch membership/request稳定性 | REQUIRES_NEW_OBSERVER | 已有耗时，无跨state稳定字段                                  | CUT-D observer                                             |
| validation成本                         | REQUIRES_NEW_OBSERVER | 无operator-specific测量                                      | observer内计时，不调用额外LLM                              |
| repair work/cost                       | REQUIRES_NEW_OBSERVER | 无DVSR repair                                                | 先branch oracle，后selected-operator TDD                   |
| GPU foreground interference            | REQUIRES_NEW_LIVE     | 旧V6 admission只证明干扰风险存在                             | selected operator后forced-miss/minimal live                |
| reuse=0是否退化V6                      | REQUIRES_NEW_OBSERVER | 旧V7-FRESH明确失败                                           | V6-based no-reuse seam differential                        |
| DVSR端到端收益                         | REQUIRES_NEW_LIVE     | 无treatment                                                  | 所有前置Gate通过后                                         |
| V6是否相对B0 timing-only               | PARTIALLY_SUPPORTED   | 代码、测试和V6 digest证明context被清空；paired B0 request field不足 | 论文若保留Native-equivalence claim，新增capture-only audit |

## C6.禁止重复实验

以下问题已有数据，不得为了阶段完整重新跑：

- Node/Summary/Edge哪个阶段耗时大；
- V6 future extraction是否已基本隐藏；
- V4旧协议是否有legal cross-frontier NodeResolve；
- 304/370 missing_side是否意味着request drift；
- 旧V7-FRESH是否可作为低税oracle；
- 旧V7-B NULL是否成立；
- 当前workload typed attribute LLM是否被触发。

## C7.Development与held-out数据角色冻结

数据角色直接复用现有：

membind-validation/artifacts/dataset/frozen_split_v1_3.json

Operator selection与所有调参只允许使用4个DEVELOPMENT_EXPOSED calibration histories：

- 07741c45；
- b6019101；
- 6071bd76；
- a2f3aa27。

c6853660已因历史检查进入compatibility-development quarantine，只允许做预声明兼容性诊断，不得影响operator选择、阈值、λ或admission policy。

Formal evaluation只允许使用已冻结且尚未查看method outcome的8个evaluation histories：

- b01defab；
- 0f05491a；
- 6aeb4375；
- 06db6396；
- 89941a94；
- c4ea545c；
- ce6d2d27；
- 08e075c7。

硬合同：

- Phase 3/G4必须覆盖全部4个development histories；最小prefix只用于schema/TDD shakeout，不能进入selection ledger；
- Phase 5/Phase 6的repair、admission、λ和online budget只在development histories上冻结；
- 8个held-out histories在method、certificate、repair、admission、统计脚本和停止规则全部冻结前不得运行或查看method-specific outcome；
- held-out一旦开始，禁止回到development修改方法后继续合并原held-out结果；
- 若held-out触发方法修改，该批结果永久转为exposed，必须重新冻结未暴露test set；
- G4使用development worst-history criterion，不声称总体95%CI；
  -正式95%CI只在held-out Phase 7/G7计算。

---

# D.TRUE_UNKNOWNS：真正需要新实验回答的问题

新实验只收缩到相邻state的same-source stability。

对source i+1，冻结同一个V6 PreparedExtractionArtifact A_i+1：

$$
\hat R_i^c=R_c(A_{i+1},S_i)
$$

authoritative publish source i后：

$$
\Delta_i=S_{i+1}\ominus S_i
$$

再得到：

$$
R_{i+1}^c=R_c(A_{i+1},S_{i+1})
$$

唯一核心未知为：

1. Semantic read stability

   read key、candidate set、ordered result和prompt-visible payload是否变化？

2. Canonical request stability

   model、schema、flags、batch order和完整messages是否exact match？

3. Exact reconvergence

   request变化后，delta-local repair能否在共享同一次authoritative provider response的branch oracle中回到fresh continuation？

4. Critical-path weight

   可复用或重收敛部分在V6关键路径上能隐藏多少，而不是累计多少work？

5. Validation economics

   requery、delta proof、digest、repair和snapshot control分别花多少？

6. Runtime interference

   speculation是否延迟authoritative LLM、DB或publication？

其中1–5由development cross-snapshot observer回答，6只由selected operator的matched live回答。Phase 4不得把6的估计值伪装成实测量。

以下不再属于V7未知：

- extract_nodes和extract_edges能否提前；
- preparation是否有backlog；
- 旧V7 Stable IR是否成功；
- publication能否乱序。

---

# E.METHOD_SELECTION：Node、Summary与Edge

## E1.Graphiti 0.29.3真实依赖

固定代码版本：

- Graphiti tag：v0.29.3
- observed commit：021d3a57d511f21b10adaf7fa923bd5c1fce5e9d

真实stateful路径：

1. resolve_extracted_nodes；
2. resolve_extracted_edges；
3. extract_attributes_from_nodes；
4. _process_episode_data。

关键事实：

- Node candidate search为全量Entity exact cosine、score threshold、ORDER BY score DESC、LIMIT；
- 无UUID二级排序；
- node_similarity_search当前不返回score，证书需sidecar绑定；
- unresolved nodes合并为一次dedupe_nodes.nodes batch LLM；
- Summary按MAX_NODES=30分batch，依赖resolved nodes、new edges、previous episodes和existing summaries；
- Edge含endpoint-local read、hybrid BM25/cosine/RRF和global invalidation；
- _process_episode_data前存在无写publication seam。

## E2.Candidate A：CUT-N

范围：

- node embedding；
- exact cosine candidate search；
- deterministic exact/fuzzy resolution；
- unresolved-node batch dedupe_nodes.nodes；
- resolved node payload与continuation。

优势：

- 当前三条sealed V6中CP占比最大：43.5%–48.2%；
- 上游只依赖V6 prepared extraction；
- exact cosine允许构造较强的C1 delta certificate；
- invalidation surface相对局部；
- miss时repair边界较清楚。

风险：

- LLM是batch call，不能无证明拆为per-node reuse；
- candidate ID不变但summary/labels/attributes变化仍会改变request；
- cutoff tie无二级排序；
- state更新可能改变deterministic resolution分支和batch membership。

第一版允许：

- per-node search/deterministic阶段validation；
- whole-call dedupe_nodes.nodes exact reuse；
- batch request变化时whole-call fresh；
- repair后只在whole continuation exact reconverge时继续复用下游。

## E3.Candidate B：CUT-D（Deep Stateful Prefix）

范围不是孤立summary function，而是：

Node→Edge→Summary/Hydration→publication seam。

潜在价值：

- Summary phase额外占V6 build的27.6%–34.2%；
- summary LLM本身调用少而重；
- 如果Node/Edge输出常重收敛，深prefix可能隐藏更大CP。

风险：

- 必须先承担Node和Edge的speculative work；
- Edge global invalidation的semantic read surface大；
- batch membership、node order、existing summary、previous episodes、new edges任一变化都会改变summary request；
- CUT-D miss可能浪费整个prefix；
- 更容易造成GPU foreground interference。

第一轮不要求为Edge发明C1 hybrid-search证明。CUT-D observer可先使用：

- Node C0/C1；
- Edge C0 requery＋ordered result/request digest；
- Summary canonical request identity；
- 任何不一致则fresh或UNKNOWN。

## E4.Candidate C：CUT-E

Edge不是当前第一候选，但不能以“历史权重低”排除：

- 当前CP占比为12.0%–24.5%；
- C2已经是显著热点；
- 它也是CUT-D的上游稳定性条件。

直接Edge method暂缓的原因是certificate complexity：

- endpoint-local existing edge；
- duplicate hybrid search；
- global invalidation；
- BM25 global statistics；
- cosine；
- RRF；
- temporal validity。

只有以下同时成立才升级为独立第一方法候选：

- CUT-N经济Gate不通过；
- Edge在新observer中有更高critical-path-weighted stable opportunity；
- C0 requery成本显著小于avoided Edge LLM；
- certificate/adversarial TDD无false VALID。

## E5.当前推荐，但不冻结

当前证据排序不是主观选择：

1. Node在三条当前sealed V6中均为最大单一phase；
2. Node是最浅stateful cut；
3. 证书可以利用exact cosine；
4. Summary虽重，但必须携带Node和Edge prefix；
5. typed Attributes当前没有真实调用。

因此CUT-N是领先候选，CUT-D必须保留为并列observer候选。最终选择只能由G4的development worst-history OfflineBenefit决定。

## E6.基于operator DAG的收益记账

不能使用“结果相同×原operator耗时”的标量记账。对每个source建立真实依赖DAG：

$$
N_{search}
\rightarrow
N_{LLM}
\rightarrow
E_{read/search}
\rightarrow
E_{LLM}
\rightarrow
S_{batch}
\rightarrow
P
$$

每个DAG node只能处于以下一种经济状态：

| 状态                | 含义                                                         | 允许记入的收益                                     |
| ------------------- | ------------------------------------------------------------ | -------------------------------------------------- |
| EXACT_REUSE         | canonical request或确定性operator input exact，authoritative path不再执行该node | 该node实际被避免且落在critical path上的work        |
| REPAIRED_CHANGED    | input/request变化并fresh执行                                 | 自身收益为0，成本计入VisibleRepairCP               |
| RECONVERGED         | repair后output digest与speculative output相同                | 自身收益仍为0；只能尝试保住已投机完成的descendants |
| INVALIDATED/UNKNOWN | 无法证明或结果不同                                           | 无收益；按fresh/failed work计费                    |

因此：

$$
\text{OfflineBenefit}
=\text{ReuseHiddenCP}
+\text{ReconvergenceSavedDescendantCP}
-\text{VisibleRepairCP}
-\text{ValidationCost}
-\lambda\text{FailedSpeculationWork}
-\text{SeamTax}
$$

其中：

- ReuseHiddenCP只包含因EXACT_REUSE而真正从authoritative critical path删除的DAG node；
- ReconvergenceSavedDescendantCP只包含因父node重收敛而避免失效的、已经speculated完成的后继node；
- descendant仍必须单独通过其state read和canonical request certificate；
- VisibleRepairCP包含changed node及被其失效的descendants在authoritative path上的重算；
- 所有时间以critical-path DAG重算后的非重叠区间为准，禁止直接求service-time总和。

特别地：

- CUT-N中若Node LLM fresh重跑后才reconverge，Node自身LLM收益为0；
- CUT-N没有已投机的Edge/Summary descendants，因此该reconvergence通常没有额外性能收益；
- CUT-D中Node reconvergence只有在Edge/Summary已经投机完成、且各自证书仍VALID时，才能记为ReconvergenceSavedDescendantCP；
- Edge fresh重跑后reconverge同理，只可能保住Summary descendants，不能回收Edge自身成本。

Observer必须输出per-source：

- operator DAG；
- exact-reused nodes；
- repaired nodes；
- reconverged parent→saved descendant attribution；
- counterfactual no-reuse critical path；
- treatment critical path；
- 去重后的ReuseHiddenCP、ReconvergenceSavedDescendantCP和VisibleRepairCP。

## E7.预注册offline selection规则

对每个cut c、source i定义：

$$
W_i(c)=\max(0,t_{need,i}(c)-t_{ready,i}(c))
$$

$$
H_i(c)=\min(W_i(c),CP_i(c))
$$

$$
B^{off}_i(c)=
U_i(c)
+Q_i(c)
-V_i(c)
-R_i^{vis}(c)
-\lambda F_i(c)
-T_i^{seam}
$$

其中：

- U为ReuseHiddenCP；
- Q为ReconvergenceSavedDescendantCP；
- V为validation visible cost；
- R为VisibleRepairCP；
- F为failed speculative resource work；
- λ在Gate前冻结；
- G4不包含foreground dilation。

由于当前只有4个DEVELOPMENT_EXPOSED calibration histories，G4不使用不稳定的跨history 95%CI。对每个history h计算：

$$
B^{off}_h(c)=\sum_{i\in h}B^{off}_i(c)
$$

并定义nested-cut边际贡献：

$$
\Delta B^{off}_{D\mid N,h}
=B^{off}_h(CUT\text{-}D)-B^{off}_h(CUT\text{-}N)
$$

选择规则：

1. hard correctness先通过；
2. CUT-N通过当且仅当所有development histories上B_off_h(CUT-N)>0；
3. CUT-D通过当且仅当所有development histories上B_off_h(CUT-D)>0，且所有history上Delta B_off_D|N,h>0；
4. CUT-D通过时选择DVSR_DEEP_PREFIX_V1，因为其边际扩展在每条development history上均为正；
5. 否则若CUT-N通过，选择DVSR_NODE_V1；
6. 两者都不通过则冻结DVSR_NO_OPERATOR_OPPORTUNITY_NULL；
7. 任何history缺关键字段则为DVSR_OBSERVER_INCONCLUSIVE，不能用均值掩盖；
8. source-level bootstrap只作measurement sensitivity，不产生跨history总体claim。

request hit rate无最低硬阈值。

---

# F.REVISED_PHASE_ORDER：优化后的执行顺序

## Phase 0：Scientific Identity Audit

目标：

- 冻结B0、V6、observer和候选cut身份；
- 明确DVSR semantic root是Frozen V6；
- 冻结Graphiti/model/embedder/config/workload；
- 明确旧V7 NULL作用域；
- 冻结canonical graph projection与Continuation K。

必须输出：

- DVSR_SCIENTIFIC_IDENTITY.json；
- V6_SEMANTIC_ROOT_AUDIT.md；
- previous-context claim decision；
- 不授权live treatment声明。

停止条件：

- 无法定义Frozen V6逻辑输入；
- 无法区分B0 claim与V6-preserving claim；
- 任何计划步骤需要修改V6 sealed identity。

## Phase 0A：Existing Evidence Closure

只做离线工作：

- 遍历V4/V5/V6/V7 artifact与report；
- 归约三条sealed V6 CP；
- 标注每个RQ/Gate/metric状态；
- 检查frontier字段是否可从existing journal恢复；
- 读取并seal frozen_split_v1_3的数据角色与8个held-out ID；
- 生成禁止重跑清单；
- hash-seal audit。

禁止：

- provider call；
- DB mutation；
- live experiment；
- 用旧32B数字替代当前8B operator selection。
- 查看8个held-out histories的method-specific outcome。

## Phase 1：Frozen-V6 Prepared/No-Reuse Seam

目标不是新建V7-FRESH，而是在现有V6 prepare/publish链上暴露stateful seam。

实现合同：

- 继续使用V6 executor、lookahead、admission、transcript和context transform；
- V6 prepare产生的node/edge object只materialize一次并可clone；
- stateful path调用Graphiti 0.29.3原生resolution functions；
- publication仍调用原生_process_episode_data；
- 不读取旧V7-FRESH的empty-history semantics；
- no-reuse control始终在当前authoritative state fresh resolve；
- extraction physical calls与Frozen V6一致；
- stateful logical request identity、DB reads、Continuation K和graph projection一致。

对比：

Frozen V6
vs
V6_PREPARED_NOREUSE_CONTROL。

必须报告：

- extraction/provider request identity；
- stateful request identity；
- DB read/write inventory；
- object/UUID/time identity；
- continuation digest；
- graph canonical projection；
- seam tax p50/p95；
- forced-miss predicted tax。

若差异显著或无法解释，停止。不得用“测试都过了”替代动态differential。

## Phase 2：Certificate Observability＋Provider-Free Adversarial TDD

先让observer看见真值，再允许它判定。

公共schema：

- PreparedArtifact identity；
- snapshot/read epoch；
- operator ID/version；
- logical read keys；
- ordered candidate IDs；
- candidate scores或score sidecar；
- prompt-visible payload digest；
- canonical request digest；
- result/continuation digest；
- actual touched-write delta；
- validation/repair timing；
- fail-closed reason。

Node反例TDD至少包括：

- 新节点越过cutoff；
- 旧candidate payload变化；
- labels/summary/attributes变化；
- threshold边界；
- cutoff tie；
- group/filter/K/min_score变化；
- embedder/model/config epoch变化；
- deterministic分支改变batch membership；
- batch order改变；
- candidate删除；
- mixed snapshot；
- incomplete delta；
  -浮点精度与serialization差异。

Summary反例TDD至少包括：

- resolved node UUID变化；
- node order变化；
- batch partition变化；
- existing summary变化；
- new edge集合/顺序变化；
- previous episode projection变化；
- typed attribute schema变化；
- upstream repair未reconverge；
- mixed snapshot。

Hard target：

- adversarial false VALID=0；
- UNKNOWN覆盖所有无法证明的情况；
- C0 fresh requery是correctness floor；
- C1只能优化C0，不能比C0更宽松。

## Phase 3：Operator-Neutral Cross-Snapshot Observer

这是第一个新系统观测，但仍无live reuse、无speculative publish。

对同一个source i+1：

1. 从Frozen V6得到同一个PreparedArtifact；
2. 在S_i运行CUT-N和CUT-D speculative branch；
3. 记录完整read-set、request和continuation；
4. authoritative publish source i；
5. 在S_i+1对同一PreparedArtifact fresh resolve；
6. 使用actual touched-write delta验证；
7. 比较semantic read、projection、canonical request、result、continuation；
8. 对request变化路径使用paired single-call branch oracle；
9. 记录candidate-specific ready/need窗口和CP；
10. 丢弃所有observer artifact，不写authoritative graph。

执行范围：

- schema与instrumentation shakeout可先使用一个development history的2/6/12-source prefix；
- 任何prefix结果只用于修observer，不进入operator selection；
- 正式selection ledger必须覆盖07741c45、b6019101、6071bd76、a2f3aa27全部4个development histories的完整source序列；
- compatibility-development的c6853660不参与selection；
- 8个held-out evaluation histories不得运行。

最小化原则：

- 只补C5中REQUIRES_NEW_OBSERVER和MISSING_FIELD；
- 不重新执行V6已回答的extraction研究；
- observer不改变资源调度结论时，优先serial/capture-only；
- 若调用真实provider，只调用无法由现有transcript或single-call branch oracle回答的请求。

输出：

- DVSR_CROSS_SNAPSHOT_OBSERVER.jsonl；
- per-cut read/request transition matrix；
- exact request hit与reconvergence matrix；
- per-source operator DAG；
- ReuseHiddenCP；
- ReconvergenceSavedDescendantCP；
- VisibleRepairCP；
- CUT-N/CUT-D total OfflineBenefit；
- Delta OfflineBenefit D|N；
- validation/repair timing；
- unknown reason breakdown；
- total speculative work；
- operator selection input。

## Phase 4：Operator Selection Gate

G4是offline implementation-worthiness Gate。在看任何live treatment结果前冻结：

-唯一第一operator；
-certificate level；
-repair unit；
-admission features；
-λ；
-统计方法；
-NULL定义。

允许结果：

- DVSR_NODE_V1_SELECTED；
- DVSR_DEEP_PREFIX_V1_SELECTED；
- DVSR_NO_OPERATOR_OPPORTUNITY_NULL；
- DVSR_OBSERVER_INCONCLUSIVE。

判定使用4个development histories的worst-history criterion，不使用跨history 95%CI，不包含ForegroundInterference。INCONCLUSIVE只能由缺字段、operator DAG无法闭合或测量不确定性产生；不得自动扩展到更多复杂operator。

## Phase 5：Selected-Operator Repair/Reconvergence/Admission TDD

只实现被选operator需要的机制。

若选择Node：

- C0 requery；
  -通过TDD后可启用C1 top-K delta proof；
- whole-batch LLM exact reuse；
- batch变化whole-call fresh；
- downstream dependency repair；
- continuation reconvergence。

若选择Deep Prefix：

- upstream Node/Edge C0 validation；
- summary batch membership/request identity；
- changed batch fresh；
- unchanged batch single-call reuse；
- hydration/embedding continuation reconvergence。

Admission硬合同：

- authoritative call优先；
- future stateful speculation最多1个；
- d=1；
- publication不等待speculation；
  -跨commit未完成speculation取消或UNKNOWN；
- low-confidence不admit；
- forced miss路径存在；
  -无hit时不扩大GPU foreground queue。

## Phase 6：Development Live Treatment与Online Economic Gate

先在development histories上做2/6/12-source或等价递增prefix；correctness、forced-miss和interference均通过后，再完成4个development histories的full-history matched live。不得使用held-out。

实验臂：

- Frozen V6；
- V6_PREPARED_NOREUSE_CONTROL；
- selected DVSR；
- 若selected=DVSR_DEEP_PREFIX_V1，必须额外运行同配置DVSR_NODE_V1作为nested-base arm；
- selected DVSR forced-miss；
- admission-off或speculation-off消融。

必须逐级通过：

- no prewrite；
- ordered publish；
- graph/state equivalence；
- continuation equality；
- false VALID=0；
- forced-miss tax符合G1预算；
- foreground queue dilation符合G6预算；
- 每条development history的OnlineBenefit均为正；
- CUT-D若被选择，其Delta OnlineBenefit D|N也必须为正。

任何一级失败立即停止，append-only记录，不扩量。

## Phase 7：Held-Out Formal Evaluation

只有G6 development live通过且代码、certificate、repair、admission、λ、统计脚本与停止规则全部冻结后，才打开8个held-out histories：

- b01defab；
- 0f05491a；
- 6aeb4375；
- 06db6396；
- 89941a94；
- c4ea545c；
- ce6d2d27；
- 08e075c7。

正式执行：

- 8个完整held-out histories；
- Frozen V6、V6_PREPARED_NOREUSE_CONTROL、selected DVSR的matched/counterbalanced repetitions；
- 若selected=DVSR_DEEP_PREFIX_V1，增加DVSR_NODE_V1 arm以估计Delta OnlineBenefit D|N；
- 每个primary arm至少2次counterbalanced repeat，具体顺序在首个held-out运行前seal；
- churn分层；
- resource-matched配置；
  -至少一个model/config sensitivity；
- Node/Summary/Edge read stability characterization；
- C0 vs C1；
- reuse-only vs repair；
- admission-on/off；
- forced miss；
- backlog/slack sensitivity；
- state/QA evaluation；
- total work与energy/resource proxy。

只有Phase 7计算history-clustered formal 95%CI。任何held-out结果不得反馈修改方法后继续并入同一formal table。

## Phase 8：Artifact与论文封装

必须提供：

- evidence audit；
- observer schema；
- certificate checker；
- adversarial tests；
- no-reuse differential；
- operator selection record；
- sealed live artifacts；
- NULL路径；
- exact reproduction command；
- claim-to-artifact mapping。

---

# G.REVISED_GATES：正确性、经济条件与NULL

## G0：Semantic Root Gate

Hard correctness：

- Frozen V6 identity、config、context policy和Graphiti commit明确；
- DVSR不声称未证明的B0 timing-only equivalence；
- old V7 failure scope明确；
- canonical request、state projection和Continuation K冻结。

PASS：

- DVSR-preserves-V6 claim可验证。

NULL：

- DVSR_INPUT_IDENTITY_NULL：无法定义或动态验证semantic root。

## G0A：Evidence Closure Gate

Hard correctness：

- 每个RQ/metric有唯一状态；
  -每个已有数字有artifact provenance；
  -旧32B与当前8B证据分层；
  -禁止重跑清单sealed；
  -缺字段与新observer严格区分。

PASS不要求方法有正收益，只要求证据账本闭环。

NULL：

- DVSR_EVIDENCE_PROVENANCE_NULL：关键主张无法追溯。

## G1：V6-Based Seam Gate

Hard correctness：

- extraction physical work与Frozen V6一致；
- stateful request sequence、DB reads、previous context、object identity、Continuation K一致；
- no pre-publication write；
- graph canonical projection一致；
- publication order一致。

Economic：

- seam tax在每条development history上的p50/p95和worst-history upper bound被测出并冻结；
  -该tax必须小到不吞噬C2中现有phase opportunity的保守下界；
- forced miss预算来自实测seam/validation，不用任意百分比。

NULL：

- DVSR_SEAM_SEMANTICS_NULL：control换了算法；
- DVSR_SEAM_TAX_NULL：control税已吞噬机会。

## G2：Certificate Soundness Gate

Hard correctness：

- provider-free adversarial false VALID=0；
- tie、mixed snapshot、missing field和epoch mismatch均UNKNOWN；
- C1 VALID集合是C0 fresh oracle的子集；
- payload projection覆盖真实prompt读取；
- canonical request包括model/schema/flags/order/messages；
- single-call oracle合同通过。

Economic只记录validation成本，不在此使用hit-rate门槛。

NULL：

- DVSR_CERTIFICATE_UNSOUND_NULL。

## G3：Cross-Snapshot Evidence Gate

Hard correctness：

-同一个PreparedArtifact在S_i和S_i+1成对；

- state版本与actual touched-write delta完整；
- observer无写；
- request变化的branch oracle不受双重随机LLM调用混淆；
  -每个UNKNOWN有明确原因。

Completeness：

- candidate-specific ready/need窗口可观测；
- Node和CUT-D均有read/request/CP/validation字段；
- Edge至少作为CUT-D上游可观测；
- operator DAG支持区分ReuseHiddenCP、ReconvergenceSavedDescendantCP和VisibleRepairCP；
  -全部4个development histories完成full-source ledger。

NULL：

- DVSR_OBSERVER_INCONCLUSIVE，而不是方法失败。

## G4：Offline Operator Selection Gate

Hard correctness：

- candidate已通过G2/G3；
  -任何reuse都满足exact request或exact reconvergence；
- speculation work、miss和repair全部计费。
- reconvergence不记当前operator自身收益；
- CUT-D与CUT-N使用同一source、同一DAG ledger计算，避免双重计数。

Economic：

$$
\min_{h\in D_{dev}}B^{off}_h(c)>0
$$

对于CUT-D还必须满足：

$$
\min_{h\in D_{dev}}\Delta B^{off}_{D\mid N,h}>0
$$

G4不减ForegroundInterference，因为它在此阶段尚未实测。统计单位是完整development history；source-level resampling只作敏感性分析。

解释指标：

- read stability；
- exact request hit rate；
- reconvergence rate；
- reconvergence实际保住的descendant CP；
- validation/avoided-work ratio；
- critical-path-weighted coverage；
- work amplification；
- CUT-D相对CUT-N的边际收益；
- UNKNOWN rate。

NULL：

- DVSR_NO_OPERATOR_OPPORTUNITY_NULL：没有candidate通过全部development histories的worst-history条件；
  -不得因单独hit rate低而NULL；
  -不得因单独hit rate高而PASS。

## G5：Selected Repair与Admission Gate

Hard correctness：

- selected repair反例TDD false GREEN=0；
- repair continuation与paired no-reuse branch exact；
- authoritative-first、d=1、future cap=1；
- publication不等speculation；
  -跨epochartifact失效；
- forced miss退化路径完整。

Economic：

- admission只在预测OfflineBenefit正时启用；
- admission feature不得使用future truth；
  -训练/校准只使用4个development histories；
  -8个held-out histories不得用于feature、threshold、λ或budget调整。

NULL：

- DVSR_REPAIR_NULL；
- DVSR_ADMISSION_LEAKAGE_NULL。

## G6：Online Economic Gate

Hard correctness：

- canonical graph/state与V6/no-reuse control一致；
- ordered durable frontier一致；
- no prewrite；
- request/result/continuation evidence闭环；
- quality gate不退化。

Economic：

- 对每个development history计算：

$$
B^{on}_h(c)
=B^{mech,live}_h(c)
-D^{fg,measured}_h(c)
-O^{online}_h(c)
$$

- B_mech,live使用与G4完全相同的DAG公式，但从该history真实live的reuse、repair、failed work和seam ledger重新计算，不直接复制Phase 3估计；
- 4个development histories上B_on_h(c)必须全部大于0；
  -若选择CUT-D，定义：

$$
\Delta B^{on}_{D\mid N,h}
=B^{on}_h(CUT\text{-}D)-B^{on}_h(CUT\text{-}N)
$$

并要求4个development histories上的Delta B_on_D|N,h全部大于0；

- forced-miss slowdown不超过G1＋G2预注册预算；
- foreground queue-delay/service-time inflation按每条history实测并低于预注册budget；
- total speculative work和GPU occupancy完整报告；
  -结果不依赖单个outlier source；
- G6仍是development qualification，不产生formal 95%CI claim。

NULL：

- DVSR_LIVE_ECONOMIC_NULL；
- DVSR_FOREGROUND_INTERFERENCE_NULL；
- DVSR_STATE_OR_QUALITY_NULL。

## G7：Held-Out Formal与Top-Conference Readiness Gate

必须同时满足：

- frozen_split_v1_3中的8个evaluation histories全部按预注册协议完成；
  -主要结论在held-out history-clustered分析上给出95%CI和effect size；
- primary OnlineBenefit或paired end-to-end improvement的95%LCB大于0；
  -多history/churn/resource sensitivity；
  -forced miss、C0/C1、repair、admission消融；
- state与QA不退化；
  -所有失败append-only；
  -artifact可复现；
- novelty不退化为prompt cache。

若held-out 95%CI跨0，只能报告INCONCLUSIVE或characterization result，不能回到development改方法后继续使用同一批held-out结果。

未满足时只能写engineering/characterization paper，不宣称通用top-conference method。

---

# H.TOP-CONFERENCE_RISK：最可能的审稿攻击

## H1.“这只是prompt cache或OCC”

风险：

-如果实现只比较prompt hash并复用response，novelty不足；
-speculation、OCC、memoization和cost admission本身均不是新贡献。

预先解决：

-展示operator-specific semantic read certificate；
-证明top-K/payload/request之间的映射；
-展示request变化后的delta-local repair和exact reconvergence；
-证明ordered memory continuation；
-报告C0 requery、C1 delta proof和cache-only baseline差异。

论文底线：

> 若最终只有identical prompt hit，没有semantic validation、repair或ordered continuation theorem，V7应判定NOVELTY_NULL。

## H2.“V6已经改变了Graphiti算法”

风险：

-strip_certified_previous_context使Native timing-only claim不可直接成立；
-审稿人会质疑所有B0/V6/V7比较的semantic root。

预先解决：

-主方法明确DVSR preserves Frozen V6；
-单列B0↔V6 dynamic request audit；
-所有quality/state结果分别报告B0、V6、DVSR；
-不以metadata声明替代request digest；
-若B0 equivalence失败，缩窄论文claim而不是隐藏。

## H3.“Node是事后挑出的容易case”

风险：

-上一版先冻结Node会被视为certificate-driven cherry-pick；
-Summary其实耗时也大。

预先解决：

-预注册CUT-N和CUT-D；
-先Existing Evidence Closure；
-同一paired observer记录两者；
-使用4个development histories的worst-history OfflineBenefit，而非hit-rate或不稳定的小样本CI；
-单独报告Delta Benefit D|N；
-冻结4个development IDs和8个held-out IDs；
-公开selection tie-break和NULL；
-typed Attributes因0调用而透明排除。

## H4.“LLM随机性使exactness无法成立”

风险：

-两个独立LLM结果相同/不同都不能作为确定性correctness proof。

预先解决：

-exact reuse以canonical logical request identity为依据；
-同一logical call只执行一次并复用其response；
-request变化后的repair使用paired single-call branch oracle；
-独立重复调用只用于variance characterization，不作exact oracle。

## H5.“速度来自抢占资源或隐藏额外work”

风险：

-future speculation可能拖慢authoritative GPU；
-只报latency不报work会掩盖成本；
-深CUT-D可能用大量失败work换少量hit。

预先解决：

-resource-matched；
-authoritative-first；
-future cap=1、d=1；
-forced miss；
-foreground queue/service dilation；
-total logical calls、physical transports、tokens、embeddings、DB reads/writes和GPU occupancy；
-G4 OfflineBenefit与G6 OnlineBenefit分开报告；
-OnlineBenefit显式减去实测ForegroundInterference；
-total work单独报告。

## H6.“Graphiti-specific，缺乏系统贡献”

风险：

-只为Neo4j cosine写一个patch，不足以支撑系统论文。

预先解决：

-抽象OperatorCertificate接口；
-区分C0 requery、C1 delta proof、C2 repair；
-把Graphiti Node和Summary作为两类不同read surface；
-至少做一组config/churn sensitivity；
-明确哪些结论只适用于exact cosine，哪些可推广到semantic retrieval。

---

# I.优化后的最终执行计划

## I1.研究问题

RQ1：在Frozen V6之后，Node、Summary和Edge分别贡献多少authoritative critical path？

状态：ALREADY_PROVEN。

RQ2：对同一PreparedArtifact，CUT-N和CUT-D跨相邻state的semantic read和canonical request有多稳定？

状态：REQUIRES_NEW_OBSERVER。

RQ3：operator-specific certificate能否在无false VALID下比C0 fresh requery更便宜？

状态：REQUIRES_NEW_OBSERVER，先TDD后真实trace。

RQ4：request变化后，delta-local repair能否exact reconverge；这种reconvergence能保住多少已投机descendant critical path，而不是错误地回收当前operator自身重算成本？

状态：REQUIRES_NEW_OBSERVER。

RQ5a：在development observer上计入seam、validation、miss、repair和work amplification后，是否有正OfflineBenefit？

状态：REQUIRES_NEW_OBSERVER。

RQ5b：selected operator在development live和held-out formal evaluation中计入实测foreground interference后，是否有正OnlineBenefit？

状态：REQUIRES_NEW_LIVE。

RQ6：方法贡献是否超出Graphiti prompt cache？

状态：取决于certificate、repair、ordered continuation和跨config证据。

## I2.方法身份

选择前：

DVSR_OPERATOR_NEUTRAL_OBSERVER_V1

选择后唯一合法身份：

- DVSR_NODE_V1；或
- DVSR_DEEP_PREFIX_V1。

共同参数：

- semantic root：Frozen V6；
- Graphiti：v0.29.3；
- horizon：d=1；
- future stateful cap：1；
- publication：ordered authoritative；
- speculation write：0；
- validation：fail-closed；
- LLM reuse：canonical request single-call；
- fallback：fresh resolve on current state。

## I3.最小artifact schema

PreparedExtractionArtifact：

- source sequence；
- workload/source hash；
- V6 method/config identity；
- extracted node/edge payload；
- previous-context policy identity；
- provider transcript binding；
- UUID/time/randomness binding；
- clone/immutability proof。

SpeculativeResolutionArtifact：

- prepared artifact hash；
- operator cut；
- read epoch；
- read-set；
- canonical requests/responses；
- operator outputs；
- continuation；
- validation state；
- timing/work inventory；
- no-write proof。

StateReadSet：

- query/filter/K/threshold；
- ordered result IDs；
- score sidecar；
- prompt-visible payload digest；
- exact read version。

TouchedWriteSetDelta：

- actual committed entity/edge create/update/delete；
- before/after prompt-visible projection；
- publication sequence；
- commit version；
- completeness proof。

Continuation K：

- resolved nodes；
- resolved edges；
- summaries/attributes；
- embeddings；
- invalidation/temporal outputs；
- publication input order；
- canonical digest。

## I4.Certificate levels

C0：Fresh requery and compare

-在S_i+1重新执行read；
-比较ordered result和prompt-visible payload；
-request exact才复用；
-作为correctness floor。

C1：Delta proof

-使用actual delta证明read result和request不变；
-仅在exact search和完整delta下启用；
-任何tie/unknown回落C0。

C2：Selective repair and reconvergence

-只重算受影响read/operator；
-用paired single-call oracle得到authoritative changed request response；
-当前operator fresh重算后即使output相同，自身saved work仍为0；
-只有已投机descendant同时保持read/request certificate VALID，才把其critical-path work记为ReconvergenceSavedDescendantCP；
-下游continuation exact后重新GREEN；
-否则fresh到seam。

## I5.Node C1证书

Speculative时记录：

- query embedding digest及原始向量精度合同；
- group/filter/K/min_score；
- ordered candidate IDs；
  -每个candidate score；
- cutoff；
- K边界tie；
- candidate name、labels、summary projection、attributes digest；
- deterministic resolution分支；
- batch membership/order；
  -完整canonical LLM request；
- model/embedder/index/config epoch。

VALID必要条件：

- query/config/epoch相同；
  -旧candidate未删除；
  -旧candidate prompt projection未变；
  -所有新增/更新candidate无法进入top-K；
- cutoff无tie或有Graphiti原生稳定二级序；
- deterministic branch和batch membership/order不变；
- canonical request exact。

否则C0、repair或UNKNOWN。

## I6.Deep-prefix Summary stage C0/C2合同

V1不承诺Summary C1 delta theorem。

必须记录：

- resolved node IDs/order；
- existing summaries；
- new edges及顺序；
- previous episode projection；
- entity type/schema；
- batch membership和partition；
- canonical summary request；
- hydrated node/embedding continuation。

复用条件：

- upstream continuation exact；
- batch membership/order exact；
  -完整canonical request exact。

变化时：

- changed batch fresh；
- unchanged batch可复用；
  -全部hydrated continuation exact后才reconverge；
  -Node或Edge重收敛不产生自身收益，只能保住仍VALID的后继batch。

## I7.Read epoch

Graphiti多个DB查询不是天然同一snapshot。Observer和live都必须绑定logical read epoch。

规则：

-每次stateful read记录version；
-同一spec artifact所有reads必须同version；
-publication开始不等待speculation；
-正在跨commit的artifact取消或UNKNOWN；
-不得阻塞authoritative publish来保护speculation；
-online epoch gate只在operator selection后实现；
-selection前只做version observability。

## I8.Economic Admission

Admission不是方法前提，而是selected operator后的online policy。

输入只能来自过去：

- per-call/cut service history；
- validation history；
- observed stability/reconvergence；
- current backlog；
- resource occupancy；
- time-to-need；
- UNKNOWN risk。

禁止使用：

- future delta truth；
  -正式test history结果调参；
  -未sealed的人工标签。

Policy：

- hard safety过滤；
  -离线阶段估计conservative OfflineBenefit；
  -只在正时admit；
  -authoritative-first；
  -future cap=1；
  -G6使用实测ForegroundInterference校验OnlineBenefit；
  -支持always-off、always-on和oracle-upper-bound消融。

## I9.评测矩阵

Baselines：

- B0 Native；
- Frozen V6；
- V6_PREPARED_NOREUSE_CONTROL；
- selected DVSR；
- conditional DVSR_NODE_V1 nested-base arm（仅selected=DVSR_DEEP_PREFIX_V1时）；
- forced-miss DVSR；
- cache-only canonical request reuse；
- C0-only；
- C1-enabled；
- no-repair；
- no-admission。

Primary metrics：

- end-to-end build time；
- durable goodput；
- per-source latency；
- ReuseHiddenCP；
- ReconvergenceSavedDescendantCP；
- VisibleRepairCP；
- CUT-N/CUT-D OfflineBenefit；
- Delta Benefit D|N；
- OnlineBenefit；
- foreground queue/service dilation；
- seam/validation/repair cost；
- failed speculative work；
- logical/physical LLM calls；
- tokens；
- embeddings；
- DB reads/writes；
- GPU occupancy或可复现proxy；
- work amplification。

Correctness/quality：

- canonical graph projection；
- state hash；
- source/publication order；
- no-prewrite；
- continuation digest；
- temporal validity；
- QA/Recall/accuracy；
- entity/edge counts；
- false VALID/FALSE GREEN；
- UNKNOWN coverage。

统计：

- development/operator selection固定为07741c45、b6019101、6071bd76、a2f3aa27；
- development Gate使用所有history均为正的worst-history criterion；
- formal evaluation固定为frozen_split_v1_3中的8个evaluation IDs；
- source嵌套在history；
  -正式结果以held-out history/source cluster bootstrap报告effect size和95%CI；
- development结果不生成总体95%CI claim；
  -不能把每个LLM call当独立样本；
  -不能只展示best history。

## I10.相关工作与新颖性边界

以下思想是继承，不作为V7单独novelty：

- Noria：partially-stateful data-flow与state重建；
- Nectar：computation identity与复用；
- Adapton/Incoop：依赖追踪、dirty propagation和增量重算；
- OCC：validate-before-commit；
- Forerunner：constraint-based speculative execution；
- Helix：cost-aware materialization；
- Parrot：LLM application dataflow与semantic variables；
- DistServe：LLM阶段隔离和resource interference；
- CacheBlend：KV cache融合与selective recomputation；
- HedraRAG：RAG graph/pipeline optimization；
- Graphiti/Zep、Mem0、A-MEM、LightMem：memory representation、organization和online/offline构建。

Primary references：

- Noria：https://www.usenix.org/conference/osdi18/presentation/gjengset
- Nectar：https://www.usenix.org/conference/osdi10/nectar-automatic-management-data-and-computation-datacenters
- Adapton：https://www.cs.umd.edu/~mwh/papers/hammer13adapton.html
- Incoop：https://dl.acm.org/doi/10.1145/2038916.2038923
- OCC：https://dl.acm.org/doi/10.1145/319566.319567
- Forerunner：https://dl.acm.org/doi/10.1145/3477132.3483564
- Helix：https://dl.acm.org/doi/10.14778/3297753.3297763
- Parrot：https://www.usenix.org/conference/osdi24/presentation/lin-chaofan
- DistServe：https://www.usenix.org/conference/osdi24/presentation/zhong-yinmin
- CacheBlend：https://dl.acm.org/doi/10.1145/3689031.3696098
- HedraRAG：https://dl.acm.org/doi/10.1145/3731569.3764806
- Zep/Graphiti：https://arxiv.org/abs/2501.13956
- Mem0：https://arxiv.org/abs/2504.19413
- A-MEM：https://proceedings.neurips.cc/paper_files/paper/2025/hash/19909c36f51abc4856b4560aff3d36d6-Abstract-Conference.html
- LightMem：https://aclanthology.org/2026.acl-long.588/

V7可能成立的新颖性只能集中在：

1. mutable Agent Memory construction中的semantic read validation；
2. operator-specific top-K/read certificate；
3. cross-state canonical LLM request identity；
4. exact single-call response reuse；
5. delta-local repair与exact reconvergence；
6. ordered memory continuation correctness；
7. critical-path-aware、interference-aware speculation economics。

若最终实现等价于“相同prompt cache”，立即冻结：

DVSR_NOVELTY_NULL。

## I11.最近一轮唯一合法动作

按顺序执行：

1. 生成并seal DVSR_EXISTING_EVIDENCE_AUDIT；
2. 完成V6 semantic root与previous-context claim decision；
3. 设计Frozen-V6 prepared/no-reuse seam differential；
4. 写certificate observability schema；
5. 先完成Node和Summary provider-free adversarial TDD；
6. 用development prefix完成observer schema shakeout；
7. 在全部4个development histories上运行operator-neutral cross-snapshot observer；
8. 基于operator DAG计算CUT-N/CUT-D OfflineBenefit和Delta Benefit D|N；
9. 用worst-history rule冻结DVSR_NODE_V1、DVSR_DEEP_PREFIX_V1或NULL；
10. 只有G4 PASS才设计development live treatment；
11. G6通过并冻结全部方法后，才打开8个held-out histories。

当前禁止：

- 直接实现DVSR_NODE_V1；
- 直接实现DVSR_DEEP_PREFIX_V1；
- 直接实现EconomicAdmission online policy；
- 启动full-history live；
- 修改V6 Core；
- 用V7-FRESH重建fresh oracle。

---

## 最终冻结摘要

| 项目              | 本版决定                                                     |
| ----------------- | ------------------------------------------------------------ |
| 主线              | 保留DVSR，不推翻                                             |
| semantic root     | Frozen V6，而非未证明的B0 timing-only                        |
| 第一operator      | 未冻结；CUT-N与CUT-D并列observer                             |
| 当前领先候选      | CUT-N，基于三条sealed V6 CP与较小依赖面                      |
| typed Attributes  | 当前3条trace为0调用，从第一轮删除                            |
| Edge              | CUT-D上游必须观测；独立method后置                            |
| 核心未知          | same-source adjacent-state read/request/reconvergence stability |
| G4 Offline Gate   | 4个development histories的worst-history OfflineBenefit>0     |
| Nested-cut Gate   | CUT-D还要求每条development history的Delta Benefit D          |
| G6 Online Gate    | OnlineBenefit=OfflineBenefit−MeasuredForegroundInterference−OnlineOverhead，且每条development history>0 |
| Formal inference  | 冻结的8个held-out histories，正式95%CI                       |
| 固定hit-rate Gate | 删除                                                         |
| certificate顺序   | 先provider-free反例TDD，再正式observer                       |
| control           | V6_PREPARED_NOREUSE_CONTROL                                  |
| fallback          | fresh resolve on current state                               |
| correctness       | fail-closed、no prewrite、single-call exactness、ordered continuation |
| live授权          | 当前无                                                       |
| NULL              | 按identity/seam/certificate/opportunity/live/novelty分层     |

## 执行修订 2026-08-31：经济计费与response证据冻结

本节为append-only执行修订，不改变已冻结的研究问题、数据角色或Gate顺序；它记录在 Phase 3 TDD 中发现并修复的计费歧义，后续所有 observer 与 selection artifact 必须使用此口径。

### 1. `VisibleRepairCP` 的相对基线定义

`fresh_dag.baseline_cp_ns` 是 `V6_PREPARED_NOREUSE_CONTROL` 在 `S_{i+1}` 上本来就必须执行的 counterfactual work。即使 cross-snapshot comparison 为 `UNKNOWN`，这段 fresh work 也不能再次作为 treatment-only cost 扣除。

因此：

- `ReuseHiddenCP` 只记录从 authoritative critical path 删除的 `EXACT_REUSE` DAG segment；
- `FailedSpeculationWork` 记录已完成但被失配/UNKNOWN 丢弃的 old speculative DAG work；
- `ValidationCost` 与 `SeamTax` 记录 observer/控制路径的新增成本；
- `VisibleRepairCP` 仅记录相对 no-reuse control 的额外 repair work（例如 selected operator 引入的第二次 repair call），默认值为 0；
- fresh branch 的 baseline work 单独输出为 `baseline_fresh_cp_ns`，不得进入 OfflineBenefit 的负项。

该定义由 provider-free TDD `test_offline_mismatch_does_not_charge_baseline_fresh_work_as_visible_repair` 冻结，并由 `derive_offline_benefit_components()` 统一实现。任何需要把 baseline fresh work 计为额外成本的改动，必须先新增反例测试、更新本节并重新运行全部 targeted TDD，不能在看结果后临时调账。

### 2. Single-call response evidence

每个成功的 observed provider request 必须保留 `response_digest`（canonical digest，64 个十六进制字符）；失败请求必须写入 `response_digest=null`。原始 response、messages、query vector、prompt body 和 credentials 仍禁止落盘。该字段只用于 same-canonical-request 的 exact single-call binding 与 request-change 后 branch oracle 归因，不能单独把两个独立 provider 调用判为 exact。

`test_request_observer_records_complete_digest_identity_without_raw_prompt` 与 `test_request_observer_failed_request_has_no_response_digest` 是该合同的最小回归集。

### 3. 当前推进状态

- Phase 0/0A/1/2：完成并保留原 artifact；
- Phase 3：`DVSR_OPERATOR_NEUTRAL_OBSERVER_V1`，同一 `PreparedArtifact` 的 2-source pair 已有可运行实现；
- 下一合法动作：在本修订通过 targeted TDD 后重跑 fresh 2-source，核验 digest、DAG 与新计费，再递增至 6/12-source；
- 任何 prefix 结果仍不得进入 G3/G4 selection ledger；
- G4 前不授权 selected operator、repair/admission 或 live reuse。

### 4. 执行修订：CUT-N continuation boundary与semantic digest（2026-08-31）

6-source shakeout发现 `29/29` Node semantic reads完全稳定时，CUT-N仍被记录为 `continuation_changed`。原因是旧 sanitizer 对原始 Graphiti object payload 计算 digest；这些隐藏运行时字段不属于 Node-resolution continuation boundary，会制造假 miss。

修订合同：

- `BuildStageResult.resolved_nodes` 保存 Node-resolution 原始输出；`BuildStageResult.nodes` 继续保存 hydration 后节点，CUT-D 不变；
- CUT-N continuation 只能由 `resolved_nodes` 的顺序/ID语义投影构成；
- sanitized continuation 的 `payload_digest` 只对已公开的 redacted semantic projection（ID、顺序、计数、epoch、frontier 等）做 canonical digest，不对隐藏 object payload 做 digest；
- 真实 ID、顺序、计数、read/request identity或 epoch 改变仍必须导致 `UNKNOWN`；
- 该修订不放宽 certificate，也不授权 reuse，只修复 observer 的 false UNKNOWN 风险。

TDD合同：`test_build_stage_preserves_raw_node_resolution_for_cut_n`、`test_continuation_digest_binds_redacted_semantics_not_hidden_object_payload`，并由 targeted suite 重新验证。

### 5. 执行修订：single-call branch oracle与C0经济口径（2026-08-31）

Phase 3旧实现对 old/fresh 相同 canonical request 各调用一次随机 provider，虽然能够比较 request identity，却会让 downstream continuation 被第二次随机 response 混淆。修订后的 observer-only branch oracle 使用以下合同：

- old branch 的成功 response 仅在进程内按 `(source_sequence, prompt_name, prompt_ordinal, request_identity)` 保存；
- fresh branch 对 exact request 仍执行 provider transport，以测量 no-reuse baseline 的真实 request timing，但 downstream 只消费 old logical call 的 response；
- fresh transport response 只保存 digest，用于诊断随机性，不参与 continuation；
- changed request 不 replay，直接消费 fresh provider response；
- 原始 response 永不落盘，observer 结束后内存 response store 随进程释放；
- 这是 Phase 3 paired oracle，不是 DVSR live reuse，不产生 speculative publication。

C0经济口径同步冻结：

- `c0_validation_cost_ns` 是 fresh native requery interval 的 wall-time union；
- exact-domain loader、reference ranking和observer wrapper成本记为 `observer_only_overhead_ns`，不伪装成 live validation；
- whole-cut `VALID` 时，`ReuseHiddenCP` 是完整 fresh cut CP，C0 requery仍作为 `ValidationCost`扣除；
- whole-cut非VALID时，exact canonical request segment可独立计入 `ReuseHiddenCP`；
- stable C0 read只作证据，不作为避免的work；只有未来真实C1 delta certificate通过后，其read segment才允许成为removable node；
- failed speculative work只包含未被exact reuse保住的old DAG work。

上述口径由 single-call replay、changed-request fallback、partial request reuse、whole-cut C0 validation和interval-union TDD共同冻结。下一合法动作是用该最终 Phase 3 observer重新执行2/6/12-source shakeout。

最终论文主张应写成：

> MemBind DVSR在冻结V6 prepared extraction之上，对mutable Agent Memory construction的stateful semantic reads进行跨state验证；系统仅在canonical LLM request保持相同或delta-local repair exact reconverge时复用投机结果，并以ordered publication保持authoritative memory evolution。是否执行投机由critical-path和foreground interference共同决定。

在G4前，这仍是一个待证方法假设，不是已成立的系统结论。

## 执行修订 6：v7-run-831 scientific-contract corrective audit（2026-08-31）

本节覆盖前文所有“继续2/6/12-source”或“authorize full development histories”的执行授权，但不删除旧记录，也不改变DVSR研究主线、Frozen V6 semantic root、development/held-out split或G4/G6边界。

### 当前强制顺序

```text
Scientific-contract repair
-> targeted TDD
-> 2-source
-> 6-source
-> 12-source
-> G3 completeness audit
-> 4 development histories
-> G4
```

在以下 corrective contract 全部 GREEN 前，禁止新2/6/12-source及full-history workload：

1. Continuation K semantic projection；
2. Frozen V6与DVSR Prepared/no-reuse真实动态differential；
3. `t_ready/t_need`和window-bounded critical-path accounting；
4. Node C1 delta certificate及C0 fallback；
5. `VALID/INVALID_CHANGED/UNKNOWN_INCOMPLETE_EVIDENCE` tri-state；
6. descendant-only reconvergence attribution；
7. API/publication/state-projection三层no-write proof；
8. primary lambda及accounting identity seal；
9. targeted与full repository regression；
10. Frozen V6 file hash复核。

### Gate与artifact状态纠正

- G1当前为`BLOCKED_PENDING_DIFFERENTIAL`；旧provider-free seam的`5_PASS`只证明抽象接口，不证明Frozen V6真实Prepared semantic root；
- G2/G3 readiness在上述合同闭合前均为`BLOCKED_BY_CORRECTIVE_AUDIT`；
- 既有Phase-3 2/6-source prefix全部为`DIAGNOSTIC_ONLY`，不得进入G3/G4；
- 两次12-source malformed structured JSON attempt均为`INFRASTRUCTURE_PROVIDER_STRUCTURED_OUTPUT_INVALID`和`INVALID_FOR_G3_G4`；重复的相同error digest否定“单次瞬态故障”假设，但不构成方法NULL；
- 当前未授权4-history development observer、live reuse、online admission或held-out访问。

### P0.1与P0.3当前执行状态

- P0.1 Continuation K已完成provider-free RED/GREEN：同UUID下summary/labels/temporal变化均不可exact；runtime-only字段不制造false mismatch；ordered semantic projection相同才exact；
- P0.3已建立digest-only machine-readable reducer，状态仅允许`EXACT`、`EXPLAINED_NON_SEMANTIC_DIFFERENCE`、`SEMANTIC_MISMATCH`；缺失必需字段直接block G1；
- P0.3审计发现旧observer把Prepared extraction和stateful suffix放在同一PREPARE调用段，未复现Frozen V6的certified capture/replay边界；修复必须在observer-side拆开这两个阶段，不得修改Frozen V6 Core。

只有生成并seal `DVSR_PHASE3_READINESS_AUDIT.json/.md`，且所有必需项为PASS时，状态才可变为`AUTHORIZED_FOR_DEVELOPMENT_G3`。
