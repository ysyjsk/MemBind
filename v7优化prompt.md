请基于当前真实代码、artifact、正在执行的r14 observer以及现有`workplan_v7.md`，完成一次**最后的methodology收口修订**，然后严格按照冻结后的plan继续执行。

本轮目标不是重新设计V6/V7，也不是为了让结果变好继续增加优化，而是保证整个研究：

1. scientific question明确；
2. baseline公平且角色固定；
3. V6/V7贡献能够独立归因；
4. correctness与quality先于performance；
5. observer证据真正对应当前V7算法；
6. Opportunity Gate能够在live treatment之前判断是否存在真实机会；
7. 所有正负结果均可复核；
8. 最终实验设计达到系统顶会论文所要求的因果隔离、可复现、fail-closed和统计可信度。

不要推翻原plan已经正确的部分，不删除任何NULL、failed attempt、历史ledger或sealed artifact。

---

# 0. 总体研究纪律继续冻结

后续顺序固定为：

`Theory / Contract Freeze`
→ `Source Audit`
→ `Observer-only Characterization`
→ `Offline Counterfactual`
→ `Opportunity Gate`
→ `Minimum Method`
→ `Provider-free Correctness`
→ `Minimal Live`
→ `Scale-up`
→ `Publication Campaign`

禁止：

* Gate前启动V7-INCREMENTAL live；
* 后验修改Gate阈值；
* 后验改变baseline角色；
* 根据最快一次run选择method；
* 为性能加入未经授权的summary/predicate/cache/scheduler；
* 删除失败attempt；
* 把engineering qualification冒充论文performance result；
* 用QA相同代替incremental exact correctness；
* 用总work saving直接冒充critical-path saving。

当前r14如果仍在运行，允许自然完成并append-only seal，但不得因为r14中途出现positive signal提前实现treatment。

---

# 1. V6继续冻结，不允许V7反向污染

`v6-membind-core-v1`保持冻结。

V6论文Core定义不变：

`dependency-safe PREPARE/NATIVE overlap`
+
`exact certified capture/replay`
+
`bounded speculative frontier`
+
`work-conserving capacity-safe admission`
+
`B0 ordered authoritative publication`

以下继续严格排除：

* summary bypass
* predicate pushdown
* endpoint/schema grounding
* grounded summary materialization
* deterministic materialization
* adaptive work reduction
* critical-path finish-time scheduler
* 任何减少、替换或改变B0 logical provider work的机制

这些只能属于独立：

`WORK_REDUCTION_EXTENSION`

或negative ablation。

---

# 2. V6 method parameter与deployment parameter保持分离

继续保留当前plan中的区分。

## Method-level invariant

* dependency-safe early execution
* bounded speculative frontier
* capacity-safe admission
* exact replay
* ordered authoritative publication

## Deployment configuration

包括但不限于：

* lookahead
* future_cap
* native_future_quota
* worker数量
* page lanes
* endpoint capacity
* 当前8B双GPU相关固定数字

这些数字不得描述为MemBind论文算法需要搜索得到的超参数。

当前`v6-membind-core-v1`中已经冻结的值不要修改，以保证当前artifact可复核。

未来如果研究device-portability，只能作为独立runtime extension：

# `available concurrency`

`min(ready dependency-independent work, available physical capacity)`

不得因此重新改动当前论文Core。

---

# 3. V6只剩evaluation，不再autoresearch

继续复用sealed：

`B0_NATIVE_SERIAL`
`T_B0 = 2636.463018176s`

不要重跑B0。

后续只允许：

1. fresh `v6-membind-core-v1 prefix-30`
2. dynamic work-preservation audit

但是进一步收紧V6 work-preservation定义。

不能只检查“整体logical work大致一致”。

对于能够alignment的每一个logical semantic call，必须比较：

* callsite identity
* operator identity
* canonical semantic input
* source identity
* model/schema/config identity
* candidate domain
* logical output schema

理想要求：

`CanonicalLogicalRequest_B0 == CanonicalLogicalRequest_Core`

允许改变的是：

* execution time
* physical endpoint
* queue/admission
* capture/replay transport existence
* physical interleaving

如果存在logical input差异，必须：

1. 逐callsite解释；
2. 给出refinement proof；
3. 证明不是work reduction；
4. 证明没有改变B0应执行semantic workload。

不能使用笼统的“合法时序差异”掩盖prompt/work变化。

最终V6必须能够支持：

> MemBind-Core改变的是`when/where to execute`，而不是`what logical semantic work must be performed`。

V6以后不再做scheduler autoresearch。

---

# 4. 冻结当前V7-FRESH身份

当前成功锚点：

`V7-FRESH r12`

结果：

`T_V7_FRESH = 3958.332938057s`

`30/30 durable publication`

`strict source order PASS`

`graph semantic sidecar = 7/7 PASS`

`QA = 6/11 = 0.5455`

`session Recall@10 = 0.9136`

将对应算法冻结为：

`V7_FRESH_CONTROL_V1`

必须生成不可变identity manifest，至少包含：

* `v7_fresh.py` source hash
* Stage A boundary
* Stage B boundary
* Stable Semantic IR schema
* stateful reconciliation operators
* prompt/schema
* model checkpoint/revision
* decoding configuration
* embedding configuration
* Graphiti/backend version
* summary partition adapter
* dedupe partition adapter
* context-budget adapter
* ordered publication seam
* source hashes
* runtime compatibility adapters

以后：

`V7-INCREMENTAL`

必须基于完全相同的：

`V7_FRESH_CONTROL_V1`

否则：

`V7-FRESH vs V7-INCREMENTAL`

不再是纯incrementalization comparison。

如果之后修改V7-FRESH algorithm：

必须生成：

`V7_FRESH_CONTROL_V2`

并重新qualification。

---

# 5. 修正V7-FRESH Adapter Equivalence定义

当前summary/dedupe/context partition是为了让8B production运行可完成。

这里不要提出无法证明的：

`partitioned LLM output == hypothetical unpartitioned LLM output`

因为LLM不是可组合确定性operator，尤其unpartitioned call本身可能因context/output limit根本不能完成。

因此把adapter correctness拆成两层。

## Layer A — Strict Work/Coverage Preservation

必须严格证明：

# `OriginalLogicalInputSet`

`Union(AllPartitionInputSets)`

检查：

* candidate全集没有silent truncation；
* entity全集没有遗漏；
* source evidence没有遗漏；
* partition boundary没有删除candidate；
* context budget不能删除semantic input；
* top-k不能未经证明缩小；
* pagination不能未经证明提前停止；
* duplicate partition必须可检测；
* global candidate/entity identity保持；
* page union覆盖完整logical domain。

这一层必须exact。

## Layer B — Algorithm Identity

对于LLM-based partition：

如果无法证明：

`partitioned output == impossible/full-call output`

不要伪造equivalence theorem。

应明确声明：

> partition adapter是`V7_FRESH_CONTROL_V1` algorithm identity的一部分。

其正确性通过：

1. complete input coverage；
2. deterministic partition/merge contract；
3. grounding/provenance；
4. V7-FRESH vs B0 quality Gate；

共同验证。

对于纯确定性operator，如果能够证明partition前后canonical output exact，则仍保留exact equivalence proof。

输出：

`V7_FRESH_ADAPTER_COVERAGE_SEAL.json`

和：

`V7_FRESH_ALGORITHM_IDENTITY.json`

不要把不可证明的LLM exact equivalence作为阻塞条件。

---

# 6. 立即执行V7-FRESH Algorithm Tax Audit

当前：

`T_B0 = 2636.463018176s`

`T_V7_FRESH = 3958.332938057s`

因此：

`V7-FRESH / B0 ≈ 1.50`

V7-FRESH比B0多：

约`1321.87s`

也就是说V7-INCREMENTAL至少要删除V7-FRESH约：

`33.4%`

的makespan，才只是追平B0。

并且这还没有计算：

* dependency tracking
* certificate
* trace persistence
* repair
* fallback
* incremental bookkeeping

因此新增并执行：

`V7_FRESH_ALGORITHM_TAX_AUDIT`

只能使用sealed B0+r12 artifact做只读分析。

至少按operator比较：

* logical LLM calls
* physical transports
* prompt tokens
* completion tokens
* provider service
* queue/service
* node extraction
* edge extraction
* dedupe_nodes calls
* dedupe_nodes candidate width
* dedupe_edges calls
* dedupe_edges candidate width
* summary entity count
* summary page count
* pagination
* candidate retrieval
* cosine/BM25/hybrid
* temporal resolution
* embeddings
* DB reads/writes
* generated entities
* generated edges
* invalidated edges
* Stable IR units
* Stage B reconciliation units
* reconciliation fanout

重点解释：

为什么Stable Semantic Boundary以后出现大型：

* summary
* dedupe
* candidate
* pagination

工作。

必须区分：

## Production Tax

例如：

* structured-output limit
* JSON/schema overhead
* request partition serialization
* context-budget adapter overhead

## Architecture Tax

例如：

* source-local extraction产生更多ambiguity
* canonicalization后移
* candidate explosion
* Stage B resolution domain扩大
* semantic unit fanout扩大

如果主要是Architecture Tax，后面不能简单用cache掩盖，必须进入Architecture Gate。

输出：

`V7_FRESH_ALGORITHM_TAX_REPORT.json`

---

# 7. 补齐B0 matched readonly quality

不要重跑B0 construction。

使用sealed B0 namespace执行与r12完全相同的：

* retrieval
* Reader
* Judge
* gold-blind QA overlay

固定：

* 同一11题
* 同一prefix-complete condition
* 同top-k
* 同Reader
* 同Judge
* 同8B模型
* 同prompt/schema
* 同quality contract

得到：

`Quality(B0)`
vs
`Quality(V7_FRESH_CONTROL_V1)`

同时比较：

* official QA
* Recall@10
* entity coverage
* edge/fact coverage
* duplicate rate
* current-state correctness
* temporal correctness
* contradiction/invalidation
* grounding
* provenance
* graph size
* source evidence traceability

11题不能作为唯一Gate。

Gate必须综合：

`paired QA`
+
`graph semantic surface`
+
`current-state`
+
`temporal`
+
`grounding/provenance`

如果存在更多已经sealed且无需重建B0即可运行的quality样本，可以作为secondary evidence。

在看到B0对应quality结果之前，先冻结non-inferiority rule。

禁止看到结果以后再改tolerance。

如果V7-FRESH无法通过quality Gate：

`BLOCK V7-INCREMENTAL`

先重新设计V7-FRESH Stage A/B architecture。

---

# 8. r14结束后首先做Observer Target Audit

r14完成后append-only seal。

不得直接根据r14结果授权treatment。

建立：

`V7B_OBSERVER_TARGET_CONTRACT`

证明observer真实characterize的是：

`V7_FRESH_CONTROL_V1`

中的：

`Stage B / stateful semantic computation`

至少核验：

* observer harness source hash
* v7_fresh source hash
* Stage A boundary
* Stage B boundary
* Stable IR schema
* stateful read callsites
* semantic nodes
* semantic dependency edges
* delta root
* publication seam
* model/schema/config
* treatment=false
* observation completeness

如果r14主要观察的是：

* 原Graphiti trace
* 旧V7-A
* stale Stage boundary

则r14只能标记为：

`V7A_GRAPHITI_REFERENCE_CHARACTERIZATION`

不能授权V7-B。

必要时针对：

`V7_FRESH_CONTROL_V1 Stage B`

补一轮真正的observer-only trace。

---

# 9. Observer必须支持paired semantic-node ground truth

仅有dependency graph不能计算C1。

C1必须知道：

`Can(v_old)`

以及：

`Can(v_fresh,new_state)`

因此observer必须能够建立：

* old state node output
* new state fresh node output
* stable-name alignment
* canonical output
* existence change
* control change
* ordered collection change
* structure change
* successor lineage
* execution cost

如果r14只有：

dependency/timing/callsite/delta

但没有new-state fresh semantic output，则它只能支持：

`C0`

不能支持：

`C1`

此时补：

`V7B_PAIRED_OBSERVER_TRACE`

但：

`treatment = false`

继续禁止V7-INCREMENTAL live。

---

# 10. 当前affected closure降级成C0 baseline

保留现有：

`incremental_update.py`

不要删除。

正式定义：

`C0_CONSERVATIVE_FULL_CLOSURE`

规则：

`changed objects`
→ `full dependency transitive closure`
→ `closure内全部fresh`

C0只回答：

> 最保守dependency-based incrementalization理论上能省多少？

C0不是最终V7。

禁止继续把：

`dependency reachable`

等价为：

`must recompute`

---

# 11. 真正分析C1 Guarded Dynamic Repair

恢复原V7正确理论：

`dirty root`
→ `fresh repair semantic unit`
→ 比较old/new canonical output
→ unchanged则exact reconvergence
→ changed才向successor传播

形式化：

`Propagate(v)`
iff

`Can(v_new) != Can(v_old)`
or
`StructureChanged(v)`

StructureChanged至少包括：

* existence
* control branch
* ordered membership/order
* demand existence
* semantic successor structure

因此：

dependency edge表示：

> 可能受到影响

而不是：

> 必须重算全部suffix。

---

# 12. 统一生成FRESH / C0 / C1三个离线Counterfactual

live treatment之前必须先完成：

## FRESH

`V7_FRESH_CONTROL_V1`

## C0

`full transitive closure`

## C1

`guarded dynamic repair + exact reconvergence`

至少报告：

* StableIRFraction
* DirectDeltaWork
* DirtyRootFraction
* AffectedSemanticFraction
* AffectedWorkFraction C0
* AffectedWorkFraction C1
* RepairWork/FreshStatefulWork
* DirtyLLMFraction
* LLM fresh rerun fraction
* prompt token saving
* transport saving
* provider-service saving
* pagination saving
* embedding saving
* DB-read saving
* propagation depth
* fanout
* reconvergence rate
* repaired-but-unchanged rate
* first divergence
* first reconvergence
* SCA_work
* SCA_provider
* SCA_prompt
* UNKNOWN fraction
* fallback fraction
* dependency tracking cost
* certificate cost
* trace storage
* repair cost

重点回答：

> C1是否真正解决旧V7-A的change amplification？

旧V7-A只作为historical reference：

`SCA≈75.86`

`reconvergence≈2.5%`

`gross saved CP≈0`

不得冒充本轮结果。

---

# 13. Gate A — Correctness

必须全部PASS：

* zero false STABLE
* zero false unaffected
* Stage A无mutable-memory leakage
* scoped delta completeness
* semantic dependency completeness for selected region
* stable-name/canonical alignment可审计
* C1 counterfactual与fresh ground truth一致
* ordered publication invariant
* UNKNOWN fail-closed
* termination/fallback safety

失败：

`BLOCK CURRENT METHOD`

不能为性能放宽。

---

# 14. Gate B — V7-FRESH Algorithm Validity

## B1 Quality

`V7-FRESH vs B0`

必须通过预注册non-inferiority。

## B2 Architecture

检查Stable Semantic Boundary是否导致严重：

* candidate amplification
* summary amplification
* dedupe amplification
* pagination amplification
* prompt amplification
* Stage B fanout
* ambiguous semantic unit amplification

如果存在严重Architecture Tax：

`ARCHITECTURE_REVIEW`

不能直接用incremental cache掩盖。

---

# 15. Gate C — Incremental Locality

C1至少必须表现出：

* affected region明显小于C0
* affected region明显小于full fresh
* nontrivial exact reconvergence
* repair work明显低于fresh stateful work
* SCA明显优于旧V7-A
* expensive operators中存在真实可避免work
* UNKNOWN/fallback没有覆盖绝大多数dominant cost

失败：

`CURRENT_M1_LOCALITY_FAIL`

注意：

这只否定当前M1/C1。

不能自动推出：

`V7 FINAL NULL`

---

# 16. Gate D0 — Architecture-level Optimistic Safe Headroom

这是这次必须进一步修正的地方。

D0不能用：

`T_FRESH - sum(all removable work)`

因为总work saving不等于makespan saving。

必须构建：

`V7_FRESH_EXECUTION_DAG`

节点至少记录：

* operator
* semantic dependency
* resource dependency
* provider service
* DB/embedding
* ordered publication
* actual start/end
* removable/stable status

然后构建optimistic safe counterfactual DAG：

1. 对所有被oracle证明可以安全reuse的work，将其execution cost设置为理论最低合法cost；
2. UNKNOWN仍然fresh；
3. ordered publication仍然保留；
4. semantic/resource dependency仍然保留；
5. 允许critical path发生切换；
6. 重新求整个counterfactual DAG最长路径。

定义：

`T_V7_IDEAL_CP = LongestPath(G_ideal)`

而不是：

`T_V7_FRESH - SumSavedWork`

同时报告：

* MaximumSafeRemovableWork
* MaximumSafeCriticalPathSaving
* T_V7_IDEAL_CP

D0目的：

> 当前V7-FRESH architecture即使有一个接近理想的、安全的incremental runtime，理论上是否可能超过B0？

如果：

`T_V7_IDEAL_CP >= T_B0`

则：

`ARCHITECTURE_NO_HEADROOM`

当前architecture不允许进入live incremental。

应进入Architecture Rescue/Redesign。

---

# 17. Gate D1 — Current M1 Conservative Economics

只有D0 PASS才计算D1。

使用当前C1真实counterfactual估计：

`repair execution`
+
`dependency tracking`
+
`certificate`
+
`trace persistence`
+
`fallback overhead`
+
`pre-registered safety headroom`

重新构造C1 counterfactual DAG并求：

`T_C1_ESTIMATED_CP`

必须有合理证据支持：

`T_C1_ESTIMATED_CP < T_B0`

才能授权live。

允许同时报告：

* optimistic
* expected
* conservative / LCB

但live授权阈值必须在看到V7-INCREMENTAL live结果前冻结。

如果：

`D0 PASS`
但
`D1 FAIL`

结论：

`CURRENT_M1_ECONOMIC_FAIL`

而不是：

`V7 FINAL NULL`

说明architecture有机会，但当前M1拿不到。

---

# 18. Architecture Rescue只能一轮，而且一次只允许一个Hypothesis

继续保留Architecture Rescue，但限制research degrees of freedom。

Architecture Rescue只有在：

* Gate A PASS
* Gate B quality PASS
* D0 PASS
* C1/D1 FAIL

时才允许进入。

并且：

**整个V7最多允许一轮Architecture Rescue。**

进入Rescue前，必须根据observer evidence选择：

`ONE dominant failure mechanism`

以及：

`ONE minimum architectural modification`

例如如果证据表明：

`EntityResolutionView过粗导致70% affected amplification`

那么Rescue只允许测试：

`split EntityResolutionView`

不能同时加入：

* 更细view
* boundary移动
* top-k delta
* temporal delta
* summary reuse
  -新的cache
  -新的scheduler

只能一次改一个机制。

允许候选类别：

A. finer semantic view granularity

B. stable semantic boundary adjustment

C. candidate/view incrementalization

D. operator-specific invalidation

E. incremental auxiliary semantic view

但必须：

1. 从当前证据选择一个；
2. 写明hypothesis；
3. 冻结new algorithm identity；
4. 冻结Gate；
5. 再characterize。

不得同时尝试A/B/C/D/E然后挑最快结果。

如果Rescue改变V7-FRESH semantics，必须生成：

`V7_FRESH_CONTROL_V2`

并重新通过fresh qualification与B0 quality Gate。

---

# 19. V7 FINAL NULL的定义继续严格区分

## M1_NULL

当前C1/M1方法不足。

## ARCHITECTURE_V1_NULL

当前V7-FRESH V1即使optimistic safe CP bound也无法超过B0。

## V7_FINAL_NULL

只有：

1. M1失败；
2. 一次Architecture Rescue已经完成；
3. Rescue后的architecture-level optimistic safe critical-path bound仍没有足够headroom；

才允许整个V7最终NULL。

禁止因为第一版保守方法没有跨过33.4%就直接关闭V7。

---

# 20. Gate E — Minimum Method Authorization

只有：

* A PASS
* B PASS
* C PASS
* D0 PASS
* D1 PASS

才授权：

`M1_V7_INCREMENTAL`

其唯一组成：

`delta-local semantic view maintenance`
+
`guarded dynamic repair`
+
`exact reconvergence`
+
`simple adaptive fresh fallback`
+
`B0-compatible ordered publication`

暂不允许：

* M2 persistent transition
* d>1
* new scheduler
* GPU finish-time routing
* summary optimization
* predicate work reduction
* heuristic stale-response reuse
* approximate reuse

---

# 21. Adaptive Fallback保持简单，不引入新超参数泥潭

如果：

* dirty units太多
* UNKNOWN过高
* affected work estimate接近fresh
* propagation budget耗尽

则：

`FALLBACK -> V7-FRESH`

目标：

`T_V7Inc ≈ min(repair, fresh) + small overhead`

初版决策只能使用：

* dirty unit count
* affected-work estimate
* UNKNOWN fraction
* propagation budget
* estimated repair work

不要重新引入：

* EWMA finish time
* GPU latency prediction
* complex endpoint weights
* device-specific lane search
* critical-path scheduler

任何fallback threshold：

必须在对应live结果之前根据observer distribution冻结。

不要看到live结果以后调。

---

# 22. Provider-free Correctness必须先于Live

实现M1后先运行：

`V7_INCREMENTAL_REFERENCE`

对每个mutation：

1. old V7-FRESH
2. apply delta
3. new-state V7-FRESH
4. V7-INCREMENTAL
5. canonical semantic-node comparison
6. seam comparison
7. final graph/state comparison

要求：

`FalseStable = 0`

`FalseUnaffected = 0`

`CanonicalMismatch = 0`

`OrderedPublicationViolation = 0`

否则：

不允许live。

---

# 23. Minimal Live固定顺序

只有provider-free correctness PASS后：

## Stage 1

2-source paired：

`V7-FRESH vs V7-INCREMENTAL`

验证：

* exact correctness
* observed work saving
* observer prediction direction
* pure incremental speedup

## Stage 2

通过后：

6-source

## Stage 3

通过后：

12-source

## Stage 4

通过后：

30-source

禁止：

2直接跳30。

---

# 24. Publication Campaign与统计设计

qualification run不能直接作为论文最终统计。

方法和所有threshold冻结后，正式publication campaign使用预注册paired design。

优先复用仓库已有multi-history/full5 infrastructure。

要求：

* 相同history成对运行；
* counterbalanced order，例如ABBA/BAAB；
* fresh namespace；
* same platform contract；
* same model/config；
* construction和QA分离；
* failed infrastructure run提前定义排除规则；
* 不删除合法慢run；
* 不按最快值汇报。

最终至少报告：

* paired speedup
* median/geometric mean
* per-history ratio
* bootstrap CI或等价paired uncertainty
* p50/p95 source latency
* work accounting
* quality
* fallback rate
* incremental hit/locality
* negative/wasted work

如果样本数量不足以支持统计显著性：

不得声称statistically significant。

可以诚实报告qualification/effect-size evidence。

---

# 25. 最终主表固定

正式主表：

1. `B0_NATIVE_SERIAL`
2. `V6_MEMBIND_CORE`
3. `V7_FRESH_CONTROL`
4. `V7_INCREMENTAL`

B1：

`B1_RELAXED_ORDER_UPPER_BOUND`

只进入supplementary relaxed-order ceiling。

Estimands固定：

## V6 concurrency contribution

`T_B0 / T_V6Core`

## V7 algorithm restructuring

`T_B0 / T_V7Fresh`

## Pure incremental benefit

`T_V7Fresh / T_V7Incremental`

## Final system benefit

`T_B0 / T_V7Incremental`

不要混淆：

* concurrency
* architecture
* incremental maintenance

三种收益。

---

# 26. Artifact与审计要求

每阶段append-only保存：

* manifest
* git/source hash
* algorithm identity
* baseline contract
* platform contract
* provider events
* logical call events
* token accounting
* semantic IR
* state delta
* dependency graph
* stable-name alignment
* certificates
* dirty/repair events
* reconvergence events
* fallback events
* publication events
* graph digest
* QA seal
* construction seal
* critical-path DAG
* counterfactual DAG
* failed-attempt reason
* threshold freeze

失败artifact不得覆盖。

任何后续结果都必须能回溯到：

`exact code + exact algorithm identity + exact platform + exact workload + exact Gate version`

---

# 27. 已有失败attempt继续保留

至少保留：

* r7 structured-output failure
* r8/r9 summary/context failure
* r11 dedupe length failure
* r13 observer-scope configuration failure

它们：

不能进入performance statistics，

但保留为：

`production-boundary evidence`

r12：

`V7_FRESH_CONTROL_V1 candidate anchor`

r14如果target正确且成功：

`V7B_8B_OBSERVER_EVIDENCE`

否则：

`V7A_GRAPHITI_REFERENCE_CHARACTERIZATION`

不得后验重新解释。

---

# 28. 立即执行顺序

完成本次plan修订后，严格按：

1. seal/changelog current methodology
2. r14自然完成并seal
3. freeze `V7_FRESH_CONTROL_V1`
4. generate adapter coverage seal
5. B0 matched readonly quality overlay
6. V7-FRESH algorithm-tax audit
7. V7B observer-target audit
8. verify paired semantic-node ground truth sufficiency
9. 必要时补paired observer-only trace
10. build FRESH/C0/C1 offline counterfactual
11. build actual/counterfactual critical-path DAG
12. evaluate Gate A
13. evaluate Gate B
14. evaluate Gate C
15. evaluate Gate D0
16. evaluate Gate D1
17. 输出Gate decision report
18. 只有A/B/C/D0/D1全部通过，才实现minimum provider-free M1
19. prove `V7-INCREMENTAL == V7-FRESH`
20. 2-source live
21. 6-source
22. 12-source
23. prefix-30
24. method完全冻结后才进入正式publication campaign

不得改变这个顺序。

---

# 29. 本次修订必须首先输出的文件

在继续实验前先生成：

`OLD_PLAN_TO_NEW_PLAN_CHANGELOG.md`

包含：

## PRESERVED

明确列出原plan继续保留：

* theory-first
* fail-closed
* V6 freeze
* B0 headline
* B1 ceiling
* Stable IR
* semantic dependency
* stable names
* dirty repair
* exact reconvergence
* FSC
* ordered publication
* V7-FRESH control
* algorithm tax
* matched quality
* observer target
* C0/C1
* D0/D1
* Architecture Rescue
* NULL合法终态
* provider-free before live

## CORRECTED

明确记录本轮只纠正：

1. LLM partition不再要求不可证明的unpartitioned exact-output equivalence；
2. adapter改成strict input/work coverage + frozen algorithm identity；
3. V6 work-preservation提升到per-call canonical logical identity；
4. D0从sum-of-work改成counterfactual critical-path DAG；
5. Architecture Rescue限制为最多一轮、一次一个evidence-selected hypothesis；
6. publication campaign补充paired/repeated statistical protocol。

## BLOCKED / DEFERRED

* V7-INCREMENTAL live
* M2
* d>1
* new scheduler
* new work-reduction optimization
* V6 algorithm modification
* B0 construction rerun
* 多hypothesis Architecture Rescue

---

# 30. 系统顶会论文Claim纪律

如果最终positive，允许的核心故事是：

> V6发现stateful memory construction中存在dependency slack，并在保持ordered state evolution的条件下利用安全overlap；但它不减少核心state-dependent work。V7进一步发现原memory pipeline存在semantic change amplification，因此建立stable source-local semantic boundary与explicit stateful views，并通过delta-local guarded repair、exact reconvergence和adaptive fallback，只重新计算真正受state delta影响的部分。整个incremental implementation保持与对应V7-FRESH from-scratch execution一致，并保持B0 ordered publication semantics。

不要claim：

* dependency graph是新思想；
* incremental computation是新思想；
* cache/replay是新思想；
* speculative execution是新思想。

真正可以论证的novelty应该落在：

* LLM memory construction中的semantic change amplification；
* stable semantic boundary；
* memory-specific semantic view；
* operator-scoped sound invalidation；
* dynamic LLM demand下的exact repair/reconvergence；
* ordered agent-memory incremental maintenance；
* locality/economics characterization。

如果最终NULL，也必须完整报告：

* 是current M1失败；
* architecture V1无headroom；
* 还是Architecture Rescue后仍无headroom。

不允许通过继续增加复杂优化强行制造positive result。

最终目标是：

> **得到一个即使结果为NULL，也能够在正确baseline、完整correctness、quality和economic证据下站得住的系统研究结论；如果结果positive，则每一部分收益均能够被独立归因和复现。**

请现在先完成methodology/workplan修订、生成changelog、冻结所有新增contract与Gate；随后从当前r14进度继续，不要重置已经合法完成的实验，也不要重跑B0。
