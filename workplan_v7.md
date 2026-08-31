MemBind V7 Incremental Memory Construction Workplan
Frozen-Substrate / Theory-Preserving Revision — 2026-08-29
文件地位：本文件是 MemBind_V7_Methodology_Workplan.md 的继承式修订版。  
它不删除、不篡改原 V7 的理论、审计与负结果，而是在原有形式化基础上纠正已经被 development evidence 暴露出的 incrementalization boundary 问题。  
核心原则：保留原 V7 正确的 theory-first / fail-closed / from-scratch-consistency 纪律；只改变已经缺乏机会证据的“直接增量化原 Graphiti execution trace”这一目标。

执行纪律：Theory → source/refinement audit → observer-only characterization → opportunity gate → minimum implementation → online economics → publication campaign。  
Gate 前禁止为了追求性能直接加入未经授权的 replay、reuse、repair、speculative apply、summary bypass、predicate shortcut 或 treatment flag。

Baseline 纠偏：

B0/NATIVE_SERIAL：严格按 episode/source 顺序完成完整 stateful update 与 durable publication，唯一 Native headline baseline。

B1/RELAXED_ORDER_UPPER_BOUND：允许 whole-update async，可能改变 state evolution，只是 relaxed-order performance ceiling。

V6/MemBind-Core：保持 B0 state evolution，仅利用 dependency-free work overlap 的前代方法。

V7-FRESH：新版 memory construction algorithm 的 from-scratch control。

V7-INCREMENTAL：在 V7-FRESH 上进行 delta-local maintenance 的最终 treatment。

禁止再次把 B1 作为 Native headline comparator。

Current Frozen Project State
本节只记录已经完成并允许被后续 V7 依赖的事实；它不是新的研究假设。

V6 frozen predecessor
V6 论文核心已经冻结为：

v6-membind-core-v1

唯一方法边界：

MEMBIND_CORE

固定合同：

execution_strategy = phase_isolated_dual_streaming_v1
route_policy = semantic_phase_elastic_affinity
state_contract = B0_SERIAL_STATEFUL_ORDERED_PUBLICATION
lookahead = 2
future_cap = 1
native_future_quota = 0
V6-Core 只允许：

phase-isolated dual streaming；

bounded frontier；

source lease 与 physical-transport permit 分离；

work-conserving partition-derived edge admission；

exact certified capture/replay；

source-order authoritative publication。

以下全部永久排除在 V6-Core headline 外：

summary bypass；

predicate pushdown；

endpoint-schema grounding；

grounded/deterministic summary materialization；

adaptive edge admission；

bootstrap future borrowing；

critical-path finish-time scheduler；

任何减少、替换或改变 Native provider work 的逻辑。

它们只能作为 WORK_REDUCTION_EXTENSION 或负消融，不得回写 V6-Core attribution。

V6 状态纪律
V6 从本文件生效起进入：

METHOD_FROZEN / EVALUATION_ONLY

允许：

fresh Core matched measurement；

fairness / work-preservation proof；

quality / publication / replay proof；

bug fix（仅限修复违反已冻结 contract 的实现错误，且必须重新 seal identity）。

禁止：

再次 autoresearch scheduler；

修改 lookahead/future cap/quota；

把 extension 偷带回 Core；

根据 V7 结果反向修改 V6 核心方法；

为追求 headline speedup 改变 B0 或 Core contract。

B0 frozen Native anchor
当前正式 B0：

artifact = d6e9e240c3ce
T_B0 = 2636.463018176 s
episodes = 30/30
durable publications = 30/30
source-order proof = PASS
route proof = PASS
B0 的角色固定为：

B0_NATIVE_SERIAL / ORDERED_NATIVE_HEADLINE

以后只要以下公共平台合同均未变化：

hardware；

model/checkpoint/revision；

embedding；

Neo4j/backend；

workload manifest；

Native arm/algorithm；

decoding/runtime contract；

cache-reset / warmup protocol；

则允许复用 sealed B0 artifact。

修改 V6/V7 私有文件本身不得触发 B0 重跑。

如果上述公共平台合同任一变化，则旧 B0 不能跨平台作为 headline anchor，必须重新建立 matched B0。

V6 still-open evaluation obligations
V6 方法虽然已经冻结，但论文结果尚未完全闭环。

在 V7 进入正式 online treatment 前，至少需要完成：

O1 — Fresh Core timing
完整运行 fresh v6-membind-core-v1 prefix-30：

[
Speedup{Core}=
\frac{2636.463018176}
{T{Core}}
]

旧 partial attempt 不得进入正式统计。

O2 — Dynamic work-preservation proof
必须动态证明 Core 只是改变执行时机，而没有静默减少 B0 应完成的逻辑工作。

至少比较：

logical callsite multiset；

canonical logical request identity；

prompt/input semantic identity（允许因调度而改变 transport timing，不允许 method-side semantic shortcut）；

expected extraction objects；

pagination logical work；

DB authoritative writes；

embedding logical work；

source-order publication。

如某一项因 Graphiti 原生 nondeterminism 无法 byte-identical，则必须明确区分：

same logical work contract
与
same physical transport schedule

不能用 metadata 中的 preserves_native_work=true 代替动态证明。

O3 — Core quality / proof seal
至少要求：

replay proof PASS；

route proof PASS；

shadow/non-authoritative write proof PASS；

ordered publication PASS；

quality guard PASS。

完成 O1–O3 后：

V6_CORE_EVALUATION_CLOSED

V7 的 provider-free 研发可与 O1–O3 并行进行，但 V7 最终论文主表不得在 V6-Core 结果未闭环时冻结。

Current V7 primitive
当前已经存在的 V7 provider-free primitive 只允许承担：

d=1 state delta
affected dependency closure
content-addressed artifact key
source/schema/model/config hash validation
frontier version validation
affected object full recomputation
closure-external reuse planning
其身份是：

V7_AFFECTED_CLOSURE_PRIMITIVE

它不是最终 V7 方法，也不是 Gate 后 treatment。

当前 primitive 禁止直接：

调 LLM；

调 Embedding；

调 Graphiti live update；

写 Neo4j；

执行 authoritative publication；

依据 heuristic semantic similarity 宣布 reuse；

把 closure 外 artifact reuse 等同于最终 from-scratch correctness。

它只用于支撑后续：

Delta Model → Dependency Closure → Repair Planner

最终是否进入 M1 必须由本 workplan 后续的 Stable IR、V7-FRESH、observer characterization 与 Opportunity Gate 决定。

0. Executive Decision
0.1 原 V7 不推翻：封存为 V7-A
原 V7 的核心理论仍然成立并继续继承：

[
StateDelta
\rightarrow SemanticReadDelta
\rightarrow DemandDelta
\rightarrow AffectedTransition
\rightarrow CriticalPathImpact
]

以及：

semantic partial-order trace；

six-kind dependency closure；

stable names / unique alignment；

typed witness；

STABLE / INVALID / UNKNOWN fail-closed certificate；

dirty worklist；

fresh repair；

exact reconvergence；

from-scratch consistency；

native continuation congruence；

operator/region-scoped delta completeness；

counterfactual critical-path analysis；

amplification accounting；

NULL 作为合法结果。

原 V7-A 的问题不是上述理论错误，而是研究对象过于严格：

试图在不改变 Graphiti 现有 computation structure 的情况下直接维护其旧 execution trace。

当前 development campaign 已观察到：

zero false STABLE / false unaffected；

early memory-specific validity 未出现；

CSP 为 0；

affected work 显著放大；

reconvergence 极低；

gross saved critical-path lower bound 为 0。

这些结果保留为 development negative evidence。它们说明“原 Graphiti trace 是差的 incrementalization target”，但由于现有 development NULL 文档明确不是正式 frozen R1–R3 publication result，因此不得把它冒充最终统计结论，也不得删除。

V7-A 状态
V7A_STRICT_GRAPHITI_REFINEMENT = DEVELOPMENT_NULL_RETAINED

除非未来需要复核某个 theorem/refinement bug，否则禁止继续为 V7-A 发明更复杂的 certificate 来“救”原 Graphiti。

0.2 新 V7：V7-B Incrementalizable Memory Construction
新的唯一研究问题是：

能否重新划定 LLM memory construction 的 semantic boundary，使昂贵且可稳定的 source-local semantic extraction 与 mutable-memory-dependent reconciliation 分离；当前序 source 提交产生 (\Delta S) 后，仅 invalid/repair 真正受影响的 semantic views，并在 exact reconvergence 后停止传播，从而在严格保持 ordered durable publication 的条件下减少 state-dependent recomputation？

核心结构：

[
e_i
\xrightarrow{\text{SourceLocalExtract}}
X_i
]

[
(X_i,S_i)
\xrightarrow{\text{Reconcile}}
Z_i
\xrightarrow{\text{Native/Ordered Publish}}
S_{i+1}
]

其中：

(X_i)：Stable Semantic IR，目标是只依赖 source-local immutable evidence 与 frozen environment；

(Z_i)：stateful reconciliation result；

(S_i)：当前 authoritative memory state；

(\Delta_i=S_{i+1}\ominus S_i)：前序提交产生的 state delta。

增量路径：

[
Z_i^{old},\Delta
\rightarrow DirtyRoots
\rightarrow RepairWorklist
\rightarrow Reconvergence
\rightarrow Z_i^{new}
]

最终必须满足：

[
V7Inc(S\oplus\Delta,e)
\equiv_\alpha
V7Fresh(S\oplus\Delta,e)
]

V7-B 的核心问题从 V6 的：

什么时候可以提前算？

升级为：

state 变化以后，已经算过的工作中到底哪些必须重算？

0.3 V6 → V7 的科研递进
B0 Native
完整 stateful update 按 source 顺序执行。

V6 MemBind-Core
识别完全不依赖最新 state 的工作：

[
StateIndependentWork
\rightarrow EarlyExecution
]

解决的是：

[
\textbf{when to compute}
]

V7-A
尝试对原 Graphiti state-dependent trace 做增量维护；development evidence 表明 state dependency 进入过早，opaque LLM call 导致 semantic change amplification。

V7-B
重新设计 incrementalization boundary：

[
StableSourceLocalIR
+
ExplicitStatefulViews
+
DeltaLocalRepair
+
ExactReconvergence
+
AdaptiveFallback
+
OrderedCommit
]

解决的是：

[
\textbf{what must be recomputed}
]

1. Scientific Claim Boundary
1.1 V7-B 不再声称“完全不改变 Graphiti algorithm”
这是与原 V7-A 最重要的边界。

V7-B 允许改变 memory construction 的内部计算分层，但不允许降低最终质量契约或放松 publication order。

因此必须拆成两层 correctness。

Layer 1：Incremental implementation correctness
严格要求：

[
V7Incremental
\equiv_\alpha
V7Fresh
]

这是 formal / differential correctness，不能用 QA 相同替代。

Layer 2：Algorithmic fidelity / quality
比较：

[
V7Fresh
\quad vs\quad
B0Graphiti
]

因为 V7-FRESH 已是新 algorithm，不要求 byte-identical Graphiti trace；但必须通过预注册质量守卫：

downstream QA non-inferiority；

current-state correctness；

entity/relation semantic coverage；

temporal consistency；

provenance/grounding；

no unexplained catastrophic graph loss；

source evidence traceability。

因此禁止声称：

V7-FRESH == Graphiti byte-identical

除非某个局部 operator 明确有严格 refinement proof。

1.2 Novelty boundary
V7 不把以下已有思想本身包装成创新：

dependency graph；

dirty propagation；

memoization；

stable naming；

from-scratch consistency；

incremental view maintenance；

speculative execution；

ordered commit；

top-k invalidation；

adaptive fallback；

KV reuse。

V7 的潜在 memory-specific contribution 限定为：

semantic change amplification characterization：解释 mutable memory 如何通过 opaque/adaptive LLM demand 放大微小 state mutation；

stable semantic boundary：把 source-local immutable semantic extraction 与 stateful reconciliation 显式分离；

memory semantic view abstraction：Entity/Fact/Relation/Temporal/Resolution 等可维护 view；

operator-scoped delta completeness 与 sound invalidation；

dynamic LLM demand + semantic dependency 的局部 repair/reconvergence；

strict ordered memory publication 下的 from-scratch-consistent incremental execution；

sparsity-aware adaptive incremental/fresh fallback；

面向 memory construction 的 work/critical-path/economic characterization。

2. Baselines, Arms and Estimands
2.1 Baseline 角色冻结
B0 — B0_NATIVE_SERIAL
定义：

source (i) 的完整 stateful update 完成；

durable publication (i) 完成；

之后 source (i+1) 才进入 authoritative native update；

使用与 V6/V7 相同的双 GPU、模型、Embedding、Neo4j、cache/warmup、workload 与 decoding contract；

两张 GPU 可被单个 native episode 内原生可并行请求使用，但不得人工引入跨 episode whole-update concurrency；

B0 不能为了“提高 GPU 利用率”而放松其 state evolution。

当前 matched 8B anchor：

artifact = d6e9e240c3ce
T_B0 = 2636.463018176 s
episodes = 30/30
durable publications = 30/30
复用规则：

公共 platform/workload/Native contract 不变时复用 sealed B0；V6/V7 私有代码变化不触发 B0 重跑。

角色：

唯一 Native headline baseline。

B1 — B1_RELAXED_ORDER_UPPER_BOUND
允许完整 episode 并发，可能改变：

read snapshot；

entity resolution；

edge resolution；

temporal evolution；

publication order；

graph state evolution。

角色：

relaxed-order empirical performance ceiling。

B1 只能回答“如果放松 ordered semantics 可以有多快”，不能用于判断 V6/V7 是否相对 Native 有效。

V6 — V6_MEMBIND_CORE
冻结身份：

version = v6-membind-core-v1
boundary = MEMBIND_CORE
execution_strategy = phase_isolated_dual_streaming_v1
route_policy = semantic_phase_elastic_affinity
state_contract = B0_SERIAL_STATEFUL_ORDERED_PUBLICATION
lookahead = 2
future_cap = 1
native_future_quota = 0
只允许：

dependency-aware PREPARE / dual streaming；

bounded frontier / controlled physical admission；

exact certified capture/replay；

ordered authoritative publication；

不改变 B0 原本应执行的逻辑工作。

所有 work-reduction extension 必须分离。

V7 启动后 V6-Core 进入 evaluation-only，不再进行方法搜索。

角色：

前代 concurrency-only 方法，用于回答 V7 是否进一步解决了“少算”问题。

V7-FRESH — V7_FRESH_FROM_SCRATCH
使用 V7-B 新 memory construction algorithm，但每个 source 都从当前 authoritative state 完整 fresh 执行，不做跨 state delta reuse。

角色：

新 algorithm 的质量 control；

V7-INCREMENTAL 的 from-scratch oracle；

拆分“算法结构变化收益”和“增量维护收益”。

V7-INCREMENTAL — V7_INCREMENTAL
与 V7-FRESH 使用完全相同的 semantic operators、prompt/schema/model 与 publication semantics，只允许：

reuse stable semantic IR/view；

delta-local invalidation；

dirty repair；

exact reconvergence；

adaptive fallback。

角色：

最终 treatment。

2.2 Primary estimands
V6 concurrency contribution
[
Speedup_{V6}=
\frac{T(B0)}{T(V6)}
]

V7 algorithm restructuring effect
[
Speedup_{Alg}=
\frac{T(B0)}{T(V7Fresh)}
]

该量允许包含 algorithmic work reduction，因此必须同时报告质量与 work accounting。

V6-Core attribution validity
在使用 (Speedup_{V6}) 作为论文 Core 结果前必须成立：

[
LogicalWork{Core}
\equiv
LogicalWork{B0}
]

这里的 LogicalWork 指 Native algorithm 应执行的 semantic/provider work，而不是 physical transport timing。

至少要求：

logical callsite coverage一致；

logical pagination obligation一致；

authoritative DB effect obligation一致；

embedding obligation一致；

没有 summary/predicate/grounding shortcut；

没有 adaptive work suppression。

否则该 run 自动降级为：

WORK_REDUCTION_EXTENSION

不得记入 V6-Core headline。

Pure incrementalization contribution
[
Speedup_{Inc}=
\frac{T(V7Fresh)}{T(V7Inc)}
]

这是 V7 增量机制最关键的因果量。

End-to-end final contribution
[
Speedup_{Total}=
\frac{T(B0)}{T(V7Inc)}
]

Relaxed-order gap recovery
仅作辅助：

[
GapRecovery=
\frac{T(B0)-T(V7Inc)}
{T(B0)-T(B1)}
]

当分母为正时解释“在不放松顺序的情况下拿回多少 relaxed-order 性能空间”。

3. Stable Semantic Boundary
3.0 Existing provider-free primitive — keep, but do not overclaim
现有 incremental_update.py / d=1 planner 作为底层 primitive 保留。

它目前实现：

[
\Delta S
\rightarrow
AffectedClosure
\rightarrow
ReuseOutsideClosure
]

这个 primitive 的正确角色是：

验证 state delta 表示；

验证 dependency closure；

验证 content-addressed artifact identity；

验证 source/schema/model/config/frontier mismatch 会 fail closed；

为后续 M1 提供 repair planner substrate。

它不能单独证明：

[
V7Incremental \equiv V7Fresh
]

因为它尚未定义完整的：

Stable Semantic IR；

stateful view semantics；

dynamic LLM demand；

operator-specific certificate；

repair/reconvergence；

publication seam。

因此开发纪律是：

保留这个模块，继续 provider-free 扩展它；但在 Gate F 之前不得把它直接接到 live provider 或 authoritative state 上。

3.1 Stage A — Source-Local Semantic Extraction
目标：

[
X_i=Extract(e_i,\Gamma_X)
]

其中 (X_i) 不读取 mutable memory state。

建议 IR 最小字段：

Mention
source id；

source span；

normalized surface；

type hint；

provenance。

Atomic Fact
subject mention；

predicate；

object/value；

source span；

factual confidence/parse status；

provenance。

Atomic Relation
source mention；

target mention；

relation label；

fact text；

source span；

temporal expression。

Temporal Evidence
explicit time；

relative time expression；

source-local normalized timestamp；

evidence span。

Source Metadata
source sequence；

source timestamp；

schema/model/prompt epoch；

immutable content hash。

Stable semantic identity
优先：

[
StableID=
Hash(
sourceHash,
operatorClass,
sourceSpan,
canonicalLocalArguments,
schemaEpoch
)
]

禁止使用：

runtime completion order；

random UUID；

provider arrival position；

current graph entity UUID；

mutable candidate rank。

3.2 Stage A 的允许与禁止
允许：

当前 source 文本；

immutable source metadata；

frozen schema/prompt/model；

deterministic parser；

source-local LLM extraction；

source-local grounding。

禁止直接读取：

previous episode retrieval；

current graph candidate；

current canonical entity；

current edge set；

mutable summary；

current adjacency；

mutable top-k；

mutable temporal conflict state。

如果某 extraction operator 无法去除这些 mutable inputs，它必须被标记为 STATEFUL_EXTRACTION_EXCEPTION，进入 Stage B 或 fallback，而不能假装属于 Stable IR。

3.3 Stage B — Stateful Semantic Reconciliation
定义：

[
Z_i=Reconcile(X_i,S_i,\Gamma_R,\Omega)
]

拆成显式 semantic views，而不是一个黑盒“完整 Graphiti update”。

建议最小 view 集：

EntityCandidateView

EntityResolutionView

RelationCandidateView

RelationResolutionView

TemporalConflictView

CanonicalFactView

SummaryDependencyView（若保留）

EmbeddingDependencyView

PublicationPlanView

每个 view 必须有：

stable key；

input dependency；

output canonicalization；

state observable set (Obs_\rho)；

delta domain；

repair function；

exact equality/reconvergence rule；

fallback rule。

4. Formal Semantic Model
4.1 Domains
(S)：authoritative memory state；

(e)：source/episode；

(X)：stable semantic IR；

(V)：stateful semantic view set；

(Z)：pre-publication semantic result；

(\Gamma)：code/model/prompt/schema/embedder/backend/config epochs；

(\Omega)：LLM/oracle choices；

(\Delta)：state delta；

(G)：semantic dependency graph；

(P)：ordered publication plan。

4.2 Fresh semantics
[
X=SourceLocalExtract_{\Gamma_X,\Omega_X}(e)
]

[
Z=FreshReconcile_{\Gamma_R,\Omega_R}(X,S)
]

[
S'=OrderedPublish_\Gamma(S,Z)
]

V7-FRESH 每次都执行上述完整路径。

4.3 Incremental semantics
给定旧 snapshot (S)、旧 reconciliation trace (\tau)、新 state：

[
S'=S\oplus\Delta
]

执行：

[
Z'=
Maintain(
\tau,
X,
\Delta,
S'
)
]

必须满足：

[
Can(Z')=
Can(FreshReconcile(X,S'))
]

随后：

[
OrderedPublish(S',Z')
]

仍遵守 B0 source order。

4.4 Semantic Dependency Graph
[
G=(N,E)
]

节点类型：

InputSource

StableIR

StateRead

CandidateView

ResolutionView

PureTransform

Control

Demand

LLMResponse

TemporalView

EmbeddingView

PublicationPlan

依赖边继续继承原 V7 六类：

data；

control；

existence；

ordered collection；

environment/oracle；

effect/publication。

异步 completion order 只有进入可观察语义时才是 semantic edge。

5. Delta and Witness Contract
5.1 Operator-scoped delta completeness
继续保留原 V7 的重要原则：

delta completeness 是 operator/region scoped，不要求全系统一次性完全建模。

对 operator (\rho)：

[
Complete_\rho(\Delta;S,S')
]

表示 (\Delta) 覆盖所有能够改变该 operator 可观察结果的 relevant mutation。

如果：

writer 未覆盖；

backend hidden mutation；

index epoch 不明；

tie/order 影响不明；

prompt-visible state 未建模；

则：

UNKNOWN → fresh

禁止根据“多次实验都没变”推出 completeness。

5.2 Witness
每个 stateful read/view 保存：

operator kind；

canonical query/key；

snapshot/version；

result；

affected domain；

ranking/cutoff；

tie set；

index/embedder/config epoch；

semantic predecessor digest；

proof data。

证书：

[
Cert(W,\Delta)\in{STABLE,INVALID,UNKNOWN}
]

其中：

[
STABLE\Rightarrow
Fresh_\rho(S\oplus\Delta)=W.result
]

6. Operator-Specific Delta Rules
6.1 Exact key/projection
STABLE 条件：

key/schema epoch不变；

delta 无同 key insert/delete；

delta 未修改任何 consumer-visible field。

missing-key witness 还必须证明 delta 无同 key insertion。

6.2 Adjacency / canonical endpoint
以 canonical endpoint/key domain 建立 invalidation。

只有 delta 与 read domain 相交才 affected。

禁止“图任何地方变了 → 所有 adjacency dirty”。

6.3 Exact cosine top-k
对旧 top-k witness 保存：

query embedding identity；

candidate domain；

cutoff (\theta)；

ties；

returned IDs/order；

index/embedder epoch。

若：

原 top-k成员未被修改/删除；

changed/new candidate 的 exact score 不可能跨越 cutoff；

tie/order不会改变；

query embedding 与 filtering domain 不变；

则 STABLE。

否则 fresh rescore relevant domain。

6.4 BM25 / Hybrid / RRF
没有可证明的 scoped influence contract 时：

UNKNOWN → fresh

不要为了提升 hit rate 做 heuristic reuse。

如果后续能够通过 backend source contract建立精确 affected domain，可以单独 preregister 新 certificate class。

6.5 Temporal conflict
建议 dependency key：

[
(subject,\ relation,\ object/domain,\ interval)
]

仅与 temporal conflict domain 相交的 delta 触发 dirty。

必须覆盖：

valid_at；

invalid_at；

overlapping interval；

canonical endpoint change；

relation alias；

source timestamp change。

6.6 LLM operator
默认规则：

semantic input完全相同
允许 reuse semantic artifact；live response reuse是否允许仍由 ReplayAdmissibility contract 决定。

semantic input变化
fresh LLM call。

V7 不把核心正确性建立在“prompt变了但旧response仍然大概率能用”的假设上。

fresh response 返回以后进行 semantic canonical comparison：

[
Can(Output{new})=Can(Output{old})
]

相同则在该 semantic node exact reconverge，不继续传播。

7. Stable Names and Reconvergence
7.1 Stable name
继续继承原 V7：

[
Name=
(source,
operatorClass,
canonicalSubject,
parentLineage,
occurrence)
]

对于 Stage A，优先使用 source span/content-derived identity。

对于 Stage B，禁止让 mutable rank/order成为唯一 identity。

7.2 Alignment
[
Align\in
{UNIQUE,OLD_ONLY,NEW_ONLY,AMBIGUOUS}
]

UNIQUE：允许 equality/reconvergence判断；

OLD_ONLY / NEW_ONLY：结构变化；

AMBIGUOUS：affected；

missing coverage不能自动解释为 drift。

7.3 Dirty worklist
初始化：

[
Dirty_0=
ChangedInputs
\cup InvalidReads
\cup UnknownReads
\cup ChangedEnv
\cup AlignmentFailures
]

对每个 dirty node：

在新 authoritative state fresh repair；

与旧 canonical output 比较；

仅当 output/control/structure改变才向 successor传播；

若 canonical output重新一致，则在此 exact reconverge。

传播条件：

[
Propagate(v)
\iff
Can(v{new})\neq Can(v{old})
\lor StructureChanged(v)
]

7.4 Termination
必须有：

finite trace；

bounded dynamic expansion；

well-founded dependency order；

repair budget；

cycle detection。

无法证明 termination：

fallback fresh

不是无限 repair。

8. Correctness Theorem Stack
以下结构尽量继承原 V7，不做不必要重写。

T1 — Snapshot Soundness
selected reconciliation region中的 state reads必须属于同一逻辑 snapshot/version。

失败：fresh。

T2 — Scoped StateDelta Completeness
对被增量维护的 operator/region，writer coverage + primitive delta extractor足以重建其 post-state observable projection。

失败：该 region UNKNOWN/fresh。

T3 — Semantic Certificate Soundness
[
Cert\rho(W,\Delta)=STABLE
\Rightarrow
Fresh\rho(S\oplus\Delta)=W.result
]

每个 certificate class独立证明。

T4 — Demand/Control Validity
如果上游 semantic outputs、control/existence、ordered predecessor context与environment均稳定，则对应 demand/build result可保持稳定。

LLM response reuse另外受 ReplayAdmissibility约束。

T5 — Dynamic Affected-Set Completeness
所有在 fresh execution中会改变的 semantic node，要么：

本身是 dirty root；

要么存在 typed changed predecessor使其进入 worklist。

禁止 false unaffected。

T6 — Reconciliation From-Scratch Consistency
[
Maintain(
Trace(S,X),
\Delta
)
\equiv_\alpha
FreshReconcile(S\oplus\Delta,X)
]

这是 V7-INCREMENTAL 最核心 theorem。

T6b — Ordered Publication Congruence
若 incremental 与 fresh 得到 seam-specific equivalent reconciliation result，且 authoritative frontier/version一致，则 ordered publication得到 α-equivalent final state。

T7 — Adaptive Fallback Safety
无论运行时选择：

incremental repair；

fresh fallback；

最终 semantic result必须与 V7-FRESH 等价。

decision policy只能影响性能，不能影响输出。

T8 — Persistent Apply / Recovery（仅当 V7 自己 staging writes）
如果 V7-B 引入独立 staged publication adapter，则需要：

closed plan；

frontier validation；

idempotency；

atomicity；

crash recovery；

exactly-once logical effect。

若 V7-B 仍把 final (Z) 交回已审计的 ordered native publication seam，则 T8 可以不进入 Core。

9. Assumption Registry
ID	Assumption	Fail-closed consequence
A1	Reconciliation selected region 使用一致 logical snapshot	fresh
A2	Stable IR 阶段无 mutable memory read	operator降级到 stateful stage
A3	selected operator writer/delta coverage complete	region UNKNOWN/fresh
A4	six-kind dependency lineage complete	enclosing region affected
A5	stable names 可唯一对齐	affected/fresh
A6	pure/control/request builder在相同env下确定	changed env affected
A7	selected certificate class sound	disable class
A8	rank/filter/tie/index/embedder epoch captured	read UNKNOWN
A9	live response replay有声明性 provider/deployment contract	fresh provider call
A10	canonical request/model/schema/tool/config identity完整	affected/fresh
A11	semantic IR grounding到source evidence	V7-FRESH quality fail
A12	V7-FRESH quality相对B0达到预注册non-inferiority	algorithm blocked
A13	repair fixed point终止	fresh fallback
A14	adaptive decision不改变semantic output	method blocked
A15	publication seam观察字段完整	move seam/fresh
A16	staged apply若存在则closed/idempotent/recoverable	staged apply blocked
禁止用“实验重复一致”替代 A3/A7/A9 等 contract/proof obligation。

10. P7 — Graphiti and V7-B Refinement Audit
在任何 runtime treatment 前冻结。

P7.1 原 Graphiti dependency audit
继续保留：

previous episode retrieval如何进入 extraction/resolution；

node/edge search；

exact cosine；

BM25/hybrid/RRF；

summary/attributes；

embedding；

bulk；

saga；

optional community work；

persistence effects。

目的：

解释 V7-A change amplification；

找到新 semantic boundary必须切断的 mutable dependencies；

不遗漏 publication-observable field。

P7.2 Stable IR purity audit
对 Stage A 每个 operator生成：

STABLE_IR_CONTRACT.json

字段至少：

operator；

source inputs；

environment epochs；

forbidden mutable state reads；

output schema；

stable-id rule；

grounding rule；

deterministic/canonicalization rule。

必须有静态源码审计 + runtime observer证明没有 hidden mutable memory read。

发现 hidden state read：

将该 operator移入Stage B；

不允许“先忽略”。

P7.3 Stateful view audit
每个 view生成：

VIEW_CONTRACT.json

包含：

key；

inputs；

Obs_rho；

delta domain；

readers/writers；

witness；

certificate；

repair；

canonical equality；

fallback；

estimated cost。

P7.4 Publication seam audit
冻结：

frontier；

version/bookmark；

logical ID map；

ordered collection；

timestamp semantics；

embedding/persistence；

saga；

idempotency；

optional downstream work。

目标：

V7-Incremental 与 V7-Fresh 在 publication seam 上可做严格 canonical differential。

11. R0 — Freeze
在任何 V7 treatment characterization 前：

record immutable V6 predecessor identity：v6-membind-core-v1；

record sealed B0 artifact / public platform contract；

seal repository HEAD；

seal Graphiti pin；

seal model/prompt/schema；

seal embedding；

seal Neo4j/backend；

seal benchmark/workload；

seal B0/B1/V6定义；

freeze V7-FRESH algorithm；

freeze semantic IR schema；

freeze view contracts；

freeze metric formulas；

freeze thresholds；

freeze stop rules；

CI阻止 treatment flag意外开启。

特别规定：

V7 不得修改 V6-Core private implementation；

V7 若需要共享 instrumentation，只能增加 observer-only hook；

observer hook 必须证明 observer-off 行为等价；

B0 复用由公共平台合同决定，不由整个 repository hash 决定；

V7-FRESH algorithm 一旦进入 R2，不允许为了改善 incremental hit rate 后验修改。

输出：

CORE_THEORY_FROZEN.json

P7_REFINEMENT_STATUS.json

V7_FRESH_ALGORITHM_FROZEN.json

BASELINE_CONTRACT.json

REQUIRED_OBSERVATION_FIELDS.json

12. R1 — Provider-Free Assumption Tests
必须先 RED → GREEN。

至少包含：

Existing affected-closure primitive
d=1 delta exact affected closure；

closure monotonicity；

missing dependency edge → RED；

content-addressed key mismatch → no reuse；

schema/model/config hash mismatch → no reuse；

frontier version mismatch → no reuse；

closure外 artifact reuse only；

affected object full recomputation；

deterministic serialization / replay of planner output。

Stable IR
mutable-state-read-in-stage-a → RED；

source span grounding；

stable ID跨run一致；

environment epoch改变→affected；

ambiguous semantic unit→fail closed。

Delta
missing delta field；

hidden writer；

delete/update/insert；

embedding epoch；

endpoint/group change；

temporal change；

index/config change。

Dependency
missing control edge；

missing existence edge；

missing ordered edge；

missing environment edge；

previous-episode dependency；

branch add/delete；

one-to-many fanout。

Operators
exact key；

adjacency；

exact cosine top-k；

cutoff crossing；

tie；

BM25/hybrid UNKNOWN；

temporal overlap。

Repair
dirty root；

changed output propagation；

exact reconvergence stop；

structural divergence；

nonterminating repair rejected；

incremental/fresh fallback equivalence。

Publication
source-order；

stale frontier；

ID mapping；

partial failure；

if staged adapter: crash/idempotency。

任何 correctness RED 未解决，不进入 live。

13. R2 — Two-Source Causal Characterization
13.1 目的
不是立即证明speedup，而是回答：

新 semantic boundary 是否真正把 mutable state 的影响限制在局部 reconciliation views？

构造：

source (e_0) 更新 (S_0\rightarrow S_1)；

对 source (e_1)：

在 (S_0) 上保存 old V7-FRESH trace；

在 (S_1) 上运行 fresh V7-FRESH ground truth；

用 (\Delta_0) 离线执行 Maintain；

比较 incremental trace与fresh trace。

13.2 必报指标
Boundary effectiveness
[
StableIRFraction=
\frac{StableIRWork}
{TotalSemanticWork}
]

[
StateDependentWorkFraction=
\frac{FreshReconciliationWork}
{TotalV7FreshWork}
]

Mutation locality
changed objects；

changed keys；

changed semantic domains；

delta bytes；

delta-to-state ratio。

Affected locality
[
AffectedWorkFraction=
\frac{RepairWork}
{FreshReconciliationWork}
]

Semantic change amplification
[
SCA_{work}
=
\frac{AffectedWork}
{DirectDeltaWork}
]

同时按：

provider service；

prompt tokens；

GPU time；

reads；

embedding；

CPU；

DB；

分别报告，禁止只给一个混合数字。

Reconvergence
first divergence；

first exact reconvergence；

reconvergence rate；

propagation depth；

fanout；

repaired-but-unchanged ratio。

LLM
dirty LLM fraction；

fresh rerun fraction；

post-rerun exact semantic reconvergence；

prompt token saving。

14. R3 — Multi-Source Observer-Only Characterization
仍然保持原 V7 “先characterize再选method”的纪律。

建议 development blocks：

2-source：assumption/causal；

6-source A；

6-source B；

不同 context / frozen seeds；

treatment calls = 0。

任何当前 temporary-provider 结果只能作为先验/负证据，不直接替代本轮 frozen campaign。

14.1 输出
MUTATION_LOCALITY

STABLE_IR_REPORT

VIEW_INVALIDATION_MATRIX

PROPAGATION_MATRIX

CERTIFICATE_CONFUSION

AFFECTED_SET_ORACLE

RECONVERGENCE_REPORT

SCA_WORK

WORK_SAVING_BOUND

COUNTERFACTUAL_CP

FALLBACK_SIMULATION

R3_DECISION_INPUT

immutable manifest

15. Opportunity Gate — Revised
原 V7 Gate 中最需要修改的是：

“必须在完整 native request 前证明有效”不再是整个 V7-B 成立的必要条件。

它可以作为一种更强 early-reuse opportunity，但不能阻止“变化后 repair + reconvergence”式增量维护。

Gate A — Correctness / Refinement
必须全部成立：

selected region T1–T7 obligations可闭合；

zero false STABLE；

zero false unaffected；

V7-Incremental frozen differential 与 V7-FRESH一致；

Stable IR purity通过；

publication order/proof通过。

任何 correctness failure：

BLOCK

Gate B — Boundary / Incrementalizability
回答：

新 architecture 是否真的创造了可稳定复用的 semantic layer，并缩小 state-dependent region？

预注册阈值必须在 R0 写死。

关注：

StableIRFraction；

state-dependent work fraction；

dirty LLM fraction；

affected semantic fraction。

如果 Stage A 仍大规模被 mutable state污染：

ARCHITECTURE_NULL

此时禁止继续堆 certificate。

Gate C — Repair Locality
要求至少存在可观测的：

localized affected set；

nontrivial exact reconvergence；

repair work小于fresh reconciliation；

SCA显著优于 V7-A development reference；

dominant cost不被 UNKNOWN operator完全覆盖。

原 CSP before full request 降级为 supporting metric，不再是必要 gate。

Gate D — Work / Critical-Path Opportunity
定义：

[
GrossWorkSaving=
FreshWork-IncrementalRepairWork
]

必须扣除或在costed graph中包含：

dependency tracking；

certificate；

trace persistence；

repair；

fallback decision；

duplicated speculative work；

extra storage。

Counterfactual DAG必须允许 path switch。

要求：

[
NetOfflineOpportunity>RequiredHeadroom
]

否则：

NULL_NO_ECONOMIC_OPPORTUNITY

Gate E — Adaptive Policy Feasibility
必须存在便宜的决策信号估计：

[
\hat C{repair}
\quad vs\quad
\hat C{fresh}
]

策略：

[
Choose=
\begin{cases}
Incremental,& \hat C{repair}+H < \hat C{fresh}\
Fresh,& otherwise
\end{cases}
]

其中 (H) 是预注册安全headroom。

要求：

decision overhead低；

false incremental不会导致质量错误，只导致性能回退；

large-delta能够及时fallback；

不允许为了提高incremental hit率强行repair。

Gate F — Minimum Sufficient Method
只实现能够删除 dominant work 的最小方法。

优先级：

M1 Semantic View Maintenance：局部view invalidation + repair + reconvergence已足够；

M2 Persistent Transition Maintenance：只有 dominant suffix跨publication seam且M1无法取得足够收益时考虑；

M0 Exact Replay：仅作为独立cache/speculation baseline；

NULL：没有有效locality/economics时合法终止。

禁止 Gate 后根据结果后验换 operator、换阈值、扩大 method。

16. Candidate Methods
M0 — Exact Request Replay Baseline
仅当：

canonical request exact；

artifact complete；

ReplayAdmissibility声明性contract成立。

它回答：

exact request cache能省多少？

不得包装成增量 memory method。

M1 — Delta-Localized Semantic View Maintenance
Minimum implementation
只选择 R3 中：

affected work占主导；

certificate/refinement已闭合；

repair明显便宜；

reconvergence明确；

的一个或少量 semantic views。

流程：

reuse Stable IR；

extract (\Delta)；

certificate invalidation；

deterministic dirty queue；

fresh repair dirty semantic units；

canonical compare；

exact reconvergence；

unchanged suffix reuse；

ordered native publication。

不允许
fuzzy semantic equality；

approximate candidate reuse；

prompt变化后直接复用旧LLM response；

unsupported BM25/hybrid强判stable；

silent work skipping。

M2 — Dynamic Affected Persistent Transition
只有在 M1 通过且仍存在 dominant publication-side suffix 时考虑。

可新增：

staged plan；

versioned view；

frontier validation；

persistent delta apply；

partial rollback/recovery。

必须额外完成 T8 与 crash/fault campaign。

M2 不是默认目标。

17. Adaptive Fallback
这是新版 V7 必须新增的核心机制。

17.1 原则
增量计算不是永远优于fresh。

当：

delta大；

dirty roots多；

fanout高；

unsupported operator多；

estimated repair接近fresh；

LLM dirty fraction高；

直接：

FALLBACK_FRESH

17.2 决策输入
允许使用：

delta object count；

delta domain count；

affected-key count；

certificate UNKNOWN fraction；

historical repair/fresh service；

predicted dirty LLM count；

view fanout；

propagation budget。

禁止使用：

treatment最终完成时间；

future ground truth；

QA结果；

后验知道“这次repair会很慢”的oracle。

17.3 Fail-safe
错误地选择incremental：

最坏只能更慢；

不能产生semantic错误；

correctness仍由T6保证。

如果repair超过预注册budget：

abort incremental trace；

fresh V7-FRESH；

记录 fallback cause。

18. R4 — Minimum Offline Implementation
Gate F 只允许一个最小 treatment。

先 provider-free：

typed trace；

stable IR cache；

delta extractor；

selected view certificate；

dirty queue；

repair；

reconvergence；

fallback；

ordered publication adapter。

禁止一次实现整个 Graphiti 全栈增量化。

19. R5 — Frozen Differential Correctness
在 frozen oracle / deterministic fixture 下：

对每个 mutation case：

old V7-FRESH；

apply delta；

new V7-FRESH；

V7-INCREMENTAL；

compare canonical trace；

compare seam result；

compare final graph/state。

必须：

[
FalseStable=0
]

[
FalseUnaffected=0
]

[
CanonicalMismatch=0
]

否则 method不进入online。

20. R6 — Two-Source Online Economics
Arms：

V7_FRESH observer-off

V7_FRESH observer-on matched control

V7_INCREMENTAL

如必要：M0 exact-replay baseline

使用固定：

workload；

model；

provider；

backend；

resource；

cache/warmup。

使用 paired ABBA/BAAB 或预注册平衡顺序。

20.1 Online headline
Pure incremental benefit
[
ActualIncBenefit=
T(V7Fresh)-T(V7Inc)
]

要求 paired CI/LCB 支持正收益后才扩展。

Work attribution
同时报告：

LLM calls；

prompt/completion tokens；

provider service；

GPU seconds；

reads；

embedding；

DB writes；

CPU；

trace/certificate storage；

fallback；

wasted speculative work。

21. R7 — Scale-Up
只有 R6 positive 才进行：

6-source；

12-source；

prefix-30；

full history。

观察：

benefit随history长度是否增加；

Stable IR reuse是否保持；

affected fraction是否膨胀；

adaptive fallback rate；

tail latency；

storage growth；

dependency graph growth；

provider interference。

如果规模扩大后：

[
RepairWork\rightarrow FreshWork
]

则必须展示 adaptive fallback是否把回退成本限制住，而不能只展示小样本收益。

22. R8 — Publication Campaign
22.1 主表 arms
正式主表建议：

Arm	Role
B0_NATIVE_SERIAL	Native headline
V6_MEMBIND_CORE	concurrency-only predecessor
V7_FRESH	new algorithm from-scratch control
V7_INCREMENTAL	final method
B1 放 supplementary：

Arm	Role
B1_RELAXED_ORDER_UPPER_BOUND	relaxed-order ceiling
若需要评价 V7 work-reduction extension，必须额外独立 arm，不能并入 V6-Core。

22.2 Fairness
所有主性能 arm共享：

同硬件数量与型号；

同LLM checkpoint/revision；

同embedding；

同Neo4j；

同benchmark/workload；

同cache reset/warmup；

同decoding contract；

fresh namespace；

frozen seeds/protocol。

但要明确：

B0 vs V7-FRESH
是：

same logical workload / resource-matched / quality-guarded algorithm comparison

不是“same number of LLM calls”。

V7-FRESH vs V7-INCREMENTAL
必须是：

same algorithm / same semantic workload / implementation-only incremental comparison

这是 pure incrementalization estimand。

23. Quality Protocol
不能只用QA一个指标。

23.1 Graph semantic surface
entity coverage；

relation/fact coverage；

canonical entity consistency；

duplicate rate；

current-state relation correctness；

contradiction/invalid edge；

temporal intervals；

provenance grounding。

23.2 Retrieval / downstream
official QA；

Recall@k；

evidence rank；

current-state slice；

temporal slice；

multi-session dependency slice；

conflict/update slice。

23.3 V7-FRESH vs B0
预注册 non-inferiority tolerance。

任何新的算法结构带来的明显质量退化：

ALGORITHM_INVALID

不能用更快抵消。

23.4 V7-INCREMENTAL vs V7-FRESH
要求更严格：

canonical semantic output应一致。

这里不是non-inferiority，而是 implementation correctness。

24. Metrics
24.1 Headline performance
construction makespan；

per-source durable latency；

goodput；

p50/p95/p99；

freshness。

24.2 Incremental metrics
StableIRFraction；

DirtyRootFraction；

AffectedWorkFraction；

DirtyLLMFraction；

RepairWorkFraction；

ReconvergenceRate；

ReconvergenceDepth；

PropagationFanout；

SCA_work；

IncrementalizableWorkFraction；

fallback rate。

24.3 Work accounting
logical LLM calls；

physical transports；

prompt tokens；

completion tokens；

pagination；

embedding items；

DB reads/writes；

CPU time；

GPU service；

queue；

storage；

duplicate/wasted work。

24.4 Critical path
分别构造：

B0 DAG；

V6 DAG；

V7-FRESH DAG；

V7-INCREMENTAL DAG。

最长路径重算必须允许 path switch。

禁止把 queue reduction直接等同于 CP saving。

25. Stop Rules
Immediate correctness stop
任一：

false STABLE；

false unaffected；

V7-Inc vs V7-Fresh canonical mismatch；

ordered publication violation；

hidden mutable read进入Stable IR；

unresolved delta completeness；

nontermination。

立即停止 treatment 扩展。

Architecture stop
如果经过 frozen characterization：

Stable IR比例太低；

mutable state仍过早污染主LLM stage；

affected work持续接近full recomputation；

reconvergence几乎不存在；

则：

V7B_ARCHITECTURE_NULL

不要继续堆证书。

Economics stop
如果：

[
NetRepairSaving\le0
]

或者 dominant cost仍由fresh LLM/page scan决定且incremental无法删除：

V7B_NULL_NO_ECONOMIC_BENEFIT

Success condition
必须同时有：

correctness；

quality；

localized repair；

positive pure incremental benefit；

positive total B0-relative benefit；

benefit在更长history下不消失；

overhead/fallback可解释。

26. Artifact Contract
每个run至少输出：

manifest.json

baseline_contract.json

semantic_ir.jsonl

semantic_ir_proof.json

state_delta.jsonl

view_witnesses.jsonl

dependency_edges.jsonl

certificate_events.jsonl

repair_events.jsonl

reconvergence_events.jsonl

fallback_events.jsonl

provider_events.jsonl

route_events.jsonl

publication_events.jsonl

work_accounting.json

critical_path.json

graph_digest.json

quality_results.json

construction_seal.json

qa_seal.json

隐私/成本敏感环境中可以只保存 hash/digest，不保存完整 prompt/response，但 artifact 必须足以审计 identity、dependency 与 correctness。

27. Autoresearch Rules
Agent必须遵循：

一次只提出一个可证伪机制假设；

先provider-free TDD；

再最小live；

不进行W/F/Q无目的网格搜索；

不根据单次最快值选方法；

failed attempt append-only；

不能修改已冻结 B0 定义；

不能把 B1 升格为 Native；

不能把 work-reduction 收益冒充 concurrency 收益；

不能把 QA 相同冒充 exact incremental correctness；

不能把 UNKNOWN 静默转为 STABLE；

不能从重复实验推出 provider replay contract；

不能为了让V7成立而删除原V7-A NULL；

新operator/certificate必须新 preregistration；

发现更深的 architecture 问题时优先修正 boundary，而不是继续调scheduler。

28. Recommended Execution Order
下面的顺序是执行约束，不是建议清单。Agent不得跨阶段抢跑。

Phase 0 — Seal history and frozen predecessor
立即完成并只读保留：

original V7 methodology；

V7-A development NULL；

B0 sealed artifact；

v6-membind-core-v1 identity；

current V7 affected-closure primitive。

输出：

HISTORY_SEAL.json

V6_PREDECESSOR_IDENTITY.json

B0_REFERENCE_POINTER.json

Phase 1 — Close V6 evaluation obligations（可与 V7 provider-free 并行）
只做三件事：

fresh Core prefix-30 matched run；

dynamic logical-work-preservation proof；

Core quality/proof seal。

禁止重新优化 V6。

完成后：

V6_CORE_EVALUATION_CLOSED

V7 provider-free开发无需等待该状态，但正式 publication campaign 必须等待。

Phase 2 — Harden existing V7 d=1 primitive
在 V7_AFFECTED_CLOSURE_PRIMITIVE 上补齐：

exact state-delta schema；

dependency-edge completeness fixtures；

content-addressed identity；

frontier/version validation；

affected full-recompute；

closure-external reuse；

fail-closed mismatch；

deterministic planner artifacts。

仍然：

treatment_calls = 0

Phase 3 — Design and freeze V7-FRESH
这是新版 V7 最关键的 architecture 阶段：

Stable Source-local Semantic IR；

stateful semantic view decomposition；

operator contracts；

ordered publication seam；

quality contract；

fresh reference implementation。

先做 from-scratch algorithm，再做 incremental system。

如果无法得到一个质量合格、边界明确的 V7-FRESH：

V7B_ALGORITHM_NULL

不得绕过它直接把 d=1 planner 接到 Native。

Phase 4 — Provider-free proof
完成：

Stable IR purity；

delta completeness；

view certificates；

six-kind dependency closure；

stable-name alignment；

dirty propagation；

repair/reconvergence；

adaptive fallback correctness；

from-scratch consistency fixtures。

全程 provider-free。

Phase 5 — V7-FRESH live qualification
只运行 V7-FRESH，不启用 incremental treatment。

目的：

[
Quality(V7Fresh)
\approx
Quality(B0)
]

以及确认实际 work decomposition 和 offline model一致。

V7-FRESH质量未过，不允许做V7-INCREMENTAL live性能实验。

Phase 6 — Observer-only incremental characterization
执行 frozen：

2-source causal；

6-source A；

6-source B。

要求：

treatment_calls = 0

只收集：

delta；

witness；

affected closure；

hypothetical repair；

reconvergence；

work saving；

CP bound；

fallback simulation。

Phase 7 — Opportunity Gate
按 Gate A–F 一次性决策：

M1；

必要时 M2；

或 NULL。

禁止 Gate 后因为结果不好：

改 Stable IR；

换 view；

改 threshold；

引入 unsupported replay；

增加 work-reduction shortcut。

需要改变 architecture 时：

结束当前 frozen round，作为新 revision重新从 R0 开始。

Phase 8 — Minimum incremental implementation
默认只实现：

M1 Delta-Localized Semantic View Maintenance

复用现有 d=1 affected-closure primitive 作为 planner substrate。

只有证据表明 publication-side persistent transition 是 dominant residual cost 才考虑 M2。

Phase 9 — Frozen differential correctness
严格验证：

[
V7Incremental
\equiv_\alpha
V7Fresh
]

任何 mismatch 都是 correctness failure，不允许以 QA相同豁免。

Phase 10 — Two-source online economics
首先只回答：

[
\frac{T(V7Fresh)}
{T(V7Incremental)}
]

是否 > 1 且有足够 headroom。

不要先拿 B0 headline 掩盖 pure incremental mechanism是否有效。

Phase 11 — Scale
按：

2
→ 6
→ 12
→ prefix-30
→ full history
逐步扩大。

每一级都重新检查：

affected fraction；

dirty LLM fraction；

reconvergence；

repair/fresh ratio；

fallback；

storage；

CP saving。

任一级 economics失效都允许停止。

Phase 12 — Publication campaign
只有以下状态同时成立才进入：

B0_REFERENCE_VALID
V6_CORE_EVALUATION_CLOSED
V7_FRESH_QUALITY_PASS
V7_INCREMENTAL_CORRECTNESS_PASS
V7_INCREMENTAL_ECONOMICS_PASS
主表：

B0_NATIVE_SERIAL
V6_MEMBIND_CORE
V7_FRESH
V7_INCREMENTAL
B1：

supplementary relaxed-order ceiling only

29. Decision Tree
原 Graphiti strict refinement 已有 development NULL
                  |
                  v
        设计 Stable Semantic Boundary
                  |
                  v
       V7-FRESH quality vs B0 过吗？
            /               \
          否                 是
          |                  |
   ALGORITHM_NULL            v
                    observer characterization
                              |
                     boundary local 吗？
                       /          \
                     否            是
                     |             |
            ARCHITECTURE_NULL      v
                         repair < fresh 吗？
                           /          \
                         否            是
                         |             |
                 ECONOMIC_NULL        v
                           minimum M1 implementation
                                      |
                           Inc == Fresh 吗？
                              /          \
                            否            是
                            |             |
                     CORRECTNESS_NULL     v
                                online benefit > 0?
                                   /        \
                                 否          是
                                 |           |
                         PERFORMANCE_NULL   SCALE
30. Paper Story if Positive
如果 V7-B 成功，论文故事建议严格表述为：

Stateful agent memory 的主要困难不只是请求串行，而是 mutable memory 很早进入 adaptive/opaque semantic computation。

V6 证明 dependency-free work 可以提前，但只改变执行时机，不减少核心 state-dependent work。

V7-A 对原 Graphiti trace 的严格增量化 development study 暴露了 semantic change amplification：小 delta 可污染大范围后续计算。

V7-B 因此重新划定 stable semantic boundary，把 source-local extraction 与 stateful reconciliation 分离。

系统维护显式 semantic views，利用 operator-scoped delta certificate、dependency-driven repair 和 exact reconvergence，仅重算受影响区域。

large delta 时 adaptive fallback fresh，避免增量维护自身成为负担。

所有publication仍保持B0顺序；V7-Incremental与V7-FRESH from-scratch consistent。

最终用 B0 证明端到端价值，用 V7-FRESH 对照证明incrementalization本身的收益，用 B1只展示relaxed-order ceiling。

31. Paper Story if NULL
NULL同样必须可发表式解释，而不是“没调出来”。

可能的合法结论：

NULL_ALGORITHM_QUALITY
source-local/stateful分离本身导致不可接受质量损失。

NULL_NO_LOCALITY
即使重新划定semantic boundary，small delta仍大范围污染stateful views。

NULL_NO_RECONVERGENCE
repair后很少重新与old computation汇合。

NULL_NO_ECONOMIC_BENEFIT
理论上可维护，但tracking/repair成本吃掉收益。

NULL_PROVIDER_DOMINATED
dominant work来自必须fresh执行的opaque LLM demand，无法通过安全增量维护删除。

这些结论都比后验发明新优化更可信。

32. Literature Mapping
本项目明确继承而不重复声称创新：

Adapton, PLDI 2014：demand-driven incremental computation、dependency tracking。

Nominal Adapton, OOPSLA 2015：stable names、cross-run alignment、from-scratch consistency。

GraphBolt, EuroSys 2019：dependency-driven mutation propagation并保持同步语义。

DZiG, EuroSys 2021：sparsity-aware incremental processing与adaptive strategy。

DBSP, PVLDB 2023 Best Research Paper：把复杂计算系统化地转为delta/incremental computation。

F-IVM, SIGMOD line / VLDB Journal：通过层次化辅助view降低增量维护成本。

Continuous Top-k, SIGMOD 2006：ranking influence region / cutoff-based maintenance。

Spectrum, PVLDB 2024：out-of-order speculative execution + predetermined serial-order final semantics + repair。

CacheBlend, EuroSys 2025 Best Paper：上下文变化时选择性重算而非完整重算，作为“partial recomputation”系统启发。

V7 的目标不是复现这些通用机制，而是证明它们如何被重新约束和组合到 adaptive LLM memory construction 中。

33. Reference URLs
Original MemBind V7 methodology:  
https://github.com/ysyjsk/MemBind/blob/main/MemBind_V7_Methodology_Workplan.md

V7 development NULL result:  
https://github.com/ysyjsk/MemBind/blob/main/MemBind_V7_DEVELOPMENT_NULL_RESULT_20260826.md

MemBind v1.3 evaluation baseline contract:  
https://github.com/ysyjsk/MemBind/blob/main/MemBind_v1_3_Evaluation_Autoresearch_Workplan.md

Current V6.1 8B autoresearch plan:  
https://github.com/ysyjsk/MemBind/blob/main/MemBind_V6_1_8B_Autoresearch_Workplan.md

Adapton:  
https://www.cs.umd.edu/~mwh/papers/hammer13adapton.html

Nominal Adapton:  
https://research.cs.queensu.ca/home/jana/papers/noma/

GraphBolt:  
https://www.sigops.org/s/conferences/eurosys/2019/toc.html

DZiG:  
https://www.cs.sfu.ca/~keval/contents/papers/dzig-eurosys21.pdf

DBSP:  
https://www.vldb.org/pvldb/vol16/p1601-budiu.pdf

F-IVM:  
https://arxiv.org/abs/1703.07484

Spectrum:  
https://www.vldb.org/pvldb/vol17/p2541-zhang.pdf

CacheBlend:  
https://www.microsoft.com/en-us/research/publication/you-only-prefill-once-combining-cached-knowledge-for-large-language-model-serving-with-cacheblend/

34. Authority and Conflict Resolution
当仓库中的旧 workplan、历史实验脚本、runner 默认值与本文件冲突时，执行优先级固定为：

本文件中冻结的 research question / baseline / method boundary；

frozen Core identity 与 B0 public-platform contract；

V7-FRESH frozen algorithm contract；

Gate preregistration；

runner / script defaults；

历史 autoresearch 参数。

因此：

旧脚本把 B1 命名为 Native 时，以本文件为准；

旧脚本默认启用 summary/predicate 等 extension 时，Core run必须关闭；

旧 manifest 因 V6/V7 私有文件变化要求重跑 B0 时，以 public-platform contract为准；

当前 V7 d=1 planner与最终 V7-B methodology冲突时，修改 planner，不修改 methodology；

任何为了匹配已有结果而改变冻结定义的行为视为 protocol violation。

35. Final Frozen Principle
最终 V7 必须始终满足以下四句话：

V6利用的是 dependency slack；V7利用的是 recomputation locality。

V7-A的NULL不被删除，它证明原Graphiti execution structure不是良好的增量化对象。

V7-B不是靠更激进地猜哪些旧LLM结果还能用，而是主动构造稳定semantic boundary，并只repair真正受state delta影响的semantic views。

所有性能结论最终回到严格B0 Native；B1永远只是一条relaxed-order upper bound。

36. Immediate Next Actions
按当前仓库状态，Agent 下一步只应执行：

A. V6:
   fresh v6-membind-core-v1 prefix-30
   + dynamic work-preservation proof
   + quality/proof seal
   -> 不再研发 V6

B. V7 provider-free:
   保留 incremental_update.py
   -> 补齐 delta / closure / identity / frontier correctness
   -> 不接 live provider

C. V7 architecture:
   明确定义 Stable Semantic IR
   -> 明确定义 Stateful Views
   -> 实现并冻结 V7-FRESH
   -> 先做 quality qualification

D. 只有之后：
   observer-only 2+6+6
   -> Gate
   -> minimum M1
   -> differential correctness
   -> live economics
当前禁止：

- 重启 V6 scheduler autoresearch
- 用 B1 替代 B0
- 把 r63 历史 158–164s 当纯 Core headline
- 为 V7 提前启用 live reuse
- 直接把 closure 外 artifact reuse 当作正确性证明
- 跳过 V7-FRESH
- 为追求 speedup 混入 summary/predicate/grounding shortcut
这四步完成顺序不得打乱。

38. Execution Log — V7-FRESH accounting and semantic qualification — 2026-08-29

本轮继续沿用冻结的 `local-qwen3-8b-awq-dualreplica-v1` 双 GPU 平台，没有重跑或修改 B0。

已完成：

- 修复 `run_v7_fresh_8b.py` 的观测缺陷：每个 source 的 build 与 ordered publication 现在都包在 `TraceRecorder.episode_scope` 中；此前 `provider_calls=0` 已确认是 accounting bug，不再作为实验事实。
- 新增 `provider_events.jsonl`、`semantic_ir.jsonl`、`publication_events.jsonl`、`work_accounting.json`、`graph_digest.json`、`construction_seal.json` 与 `quality_results.json` 输出；全部采用新 namespace 和 exclusive artifact 写入。
- 新增 `run_v7_fresh_quality_8b.py`，只读检查 namespace/provenance/source-order/nonempty graph/edge provenance 六项 graph semantic contract。
- 针对性测试 14/14 通过；新的 2-source qualification `r3-accounting-2source-20260829` PASS；graph semantic quality 6/6 PASS。

真实 2-source accounting（artifact：`/data/predator/ly/Mem/experiments/local-qwen3-8b-awq-dualreplica-v1/v7_fresh_qualification/r3-accounting-2source-20260829`）：

- `T(V7-FRESH)=81.345345010 s`，2/2 durable publication，source order `[0,1]`。
- 20 logical LLM calls，99 physical transport attempts，417,724 prompt tokens，26,350 completion tokens。
- 84 embedding calls（318 items），275 DB reads，10 DB writes；trace spans 492。
- canonical graph：81 entities、38 edges、2 episodes，graph semantic checks 6/6 PASS。

路由观察（必须如实解释）：

- PREPARE 区域：prepare-replica 39、native-replica 44；NATIVE 区域：native-replica 10、prepare-replica 6。
- 当前 `capacity_weighted_least_outstanding` contract 的 `phase_labels_visible=false`，因此 PREPARE/NATIVE 是逻辑 region 标记，不是硬 endpoint affinity。该 run 不能声称“Stage A 全部在 GPU1”；这属于 routing/fairness observation，不改变 V7 算法边界。

当前 gate 状态：

- V7-FRESH construction：PASS。
- Graph semantic surface：PASS。
- Downstream QA / V7-FRESH vs B0 non-inferiority：尚未执行，仍为 PENDING。
- V7-INCREMENTAL live treatment：仍未授权。

下一步固定为：在不改变 V7-FRESH 代码和 routing contract 的前提下，执行 observer-only 2-source causal characterization（treatment calls=0），输出 mutation locality、view invalidation、affected-set、reconvergence 与 counterfactual work accounting；只有该结果与 downstream quality contract 均满足后，才考虑 M1 treatment。

39. Execution Log — six-source fresh scale check — 2026-08-29

在完成 2-source accounting/semantic seal 后，按 `2 -> 6` 顺序执行了新的 6-source `V7_FRESH` qualification。该 run 使用独立 namespace：

`/data/predator/ly/Mem/experiments/local-qwen3-8b-awq-dualreplica-v1/v7_fresh_qualification/r5-fresh-6source-20260829`

结果：

- construction status `PASS`；6/6 durable publication；publication source sequences `[0,1,2,3,4,5]`；`T(V7_FRESH)=290.133609556 s`。
- 真实 work accounting：140 logical LLM calls、361 physical transport attempts、1,539,334 prompt tokens、85,088 completion tokens；297 embedding calls（1,111 items）；966 DB reads、30 DB writes；1,806 trace spans。
- graph digest：257 entities、135 edges、6 episodes；只读 graph semantic quality `7/7 PASS`；downstream QA 仍未执行。
- per-source construction duration（s）：source 0 `35.249`、source 1 `41.538`、source 2 `53.549`、source 3 `64.951`、source 4 `32.313`、source 5 `62.283`。后续应按 source workload size 分层，不能用简单平均值替代 matched comparison。

路由继续验证当前 resource contract 的真实含义：

- PREPARE region 233 次：native-replica 114、prepare-replica 119。
- NATIVE region 128 次：native-replica 72、prepare-replica 56。

因此两个 GPU 是同一 work-conserving capacity pool；PREPARE/NATIVE 仅是逻辑 region scope，不能解释为物理阶段隔离。该观察已纳入 fairness 风险记录；当前 run 不宣称“GPU1 专门抽取”。

本轮没有运行 V7-INCREMENTAL，也没有进行 B0-relative speedup 估计：B0 sealed artifact 是 30-source prefix，6-source run 只用于验证 V7-FRESH 的 work decomposition 和 ordered publication 在小规模扩展下稳定。下一合法动作仍是 observer-only causal characterization 和 downstream quality/non-inferiority，而不是直接扩展 treatment。

37. Execution Log — 2026-08-29

本轮执行严格从 Phase 0–4 的 provider-free 部分开始，未改动冻结的 B0、B1 或 V6-Core 合同。

已完成：

- `V7B_FRESH_ALGORITHM_FROZEN.json`、`V7B_BASELINE_CONTRACT.json`、`HISTORY_SEAL.json` 已建立；B0 继续指向 sealed `d6e9e240c3ce`，B1 仍为 supplementary upper bound。
- 新增 `membind_v7/v7b.py` 作为独立 provider-free V7-B reference：Stable IR、显式 semantic views、d=1 dirty propagation、exact reconvergence、adaptive fresh fallback、ordered publication 与审计 artifact。
- 新增 `tests/test_membind_v7b_offline.py`，新增测试 5/5 通过；V7 既有定向测试 41/41 通过。
- 执行 R2 two-source 与 R3-A/R3-B 两个 six-source synthetic observer block，共 13 个 pair；所有 `V7-INCREMENTAL == V7-FRESH` canonical differential 通过，provider/treatment calls 均为 0。
- 修复本地 vLLM 启动的 `statistics.py` 标准库遮蔽问题；14B 单实例服务重新启动后通过 models、8 路 structured JSON、Embedding 128 批量与 1024 维检查。

当前离线证据：

- `StableIRFraction_mean = 0.1613`；`AffectedWorkFraction_mean = 0.6154`；离线 work saving = `38.46%`；fallback = 0；temporal-year view 出现 exact reconvergence。
- 结果目录：`/data/predator/ly/Mem/experiments/local-qwen3-14b-awq-v1/v7b-offline-campaign-20260829-r2`。
- 首次 fixture 污染失败目录保留为 append-only 审计；修正后 13/13 differential 全部一致。

尚未满足、因此禁止越过的 gate：

- 当前运行 profile 是 `local-qwen3-14b-awq-v1`，而 sealed B0 是 `local-qwen3-8b-awq-dualreplica-v1`；8B dual startup preflight 检测到 14B 进程与 legacy ports 正在占用，未强行停止，故没有 resource-matched V7-FRESH live qualification。
- Provider-free synthetic parser/view 结果不能替代 V7-FRESH 对 B0 的 quality/non-inferiority，也不能证明 online wall-clock economics；V7-INCREMENTAL live treatment 仍未授权。

下一合法动作仍是：在明确切换到 8B dual 平台并重新通过 platform/workload/namespace preflight 后，单独执行 V7-FRESH live qualification；只有质量、差分正确性和 online economics 全部封存后，才可实现/运行 V7-INCREMENTAL treatment。若切换平台未获授权，本轮在此 stop-rule 边界保持 blocked，而不是把 14B 结果冒充 8B headline。

40. Execution Log — failed-attempt accounting and corrected six-source audit — 2026-08-29

本轮对 `r7-fresh-6source-accounting-fixed-20260829` 做了失败语义审计。该 attempt 在接近完成时收到真实 provider 的 malformed structured output，并抛出：

`JSONDecodeError: Expecting ',' delimiter: line 1 column 3150 (char 3149)`。

该 namespace 仍是旧的 `RUNNING` manifest，不能恢复、重放、纳入性能统计或升级为结果。runner 已补齐 fail-closed 处理：后续失败 attempt 会在清理前写入 `RUN_FAILURE.json`、`RUN_MANIFEST_FAILURE.json`，并尽可能保留 `ROUTE_EVENTS_PARTIAL.json` 与 `provider_events_partial.jsonl`；任何 replacement 都必须使用新的 run-id 和新的 namespace。

对成功的 `r5-fresh-6source-20260829`，原始 `RESULT.json` 的构建与图 artifact 不变，仅新增 append-only `RESULT_ACCOUNTING_CORRECTED.json`。修正原则是：logical LLM span 只用于请求计数，token/work denominator 只从 physical transport span 汇总，避免同一 provider usage 被计算两次。修正后得到 140 logical calls、361 transport attempts、769,667 prompt tokens、42,544 completion tokens、297 embedding calls/1,111 items、966 DB reads、30 DB writes；route region 仍严格解释为 advisory labels over one capacity-weighted work-conserving pool，而非物理 GPU affinity。

已完成的 6-source graph semantic sidecar 为 `7/7 PASS`；read-only retrieval diagnostic 仅有 1 个 prefix-complete QA，evidence recall@10=1.0，未运行 reader/judge，因此不能作为 downstream QA 或 B0 non-inferiority 结论。下一步按 scale policy 运行全新 12-source V7-FRESH，使冻结的 top-k=10 reader contract 具备足够 corpus，再执行只读 downstream QA overlay；V7-INCREMENTAL treatment 仍未授权。

41. Execution Log — 12-source provider-boundary failures and engineering correction — 2026-08-29

按 `2 -> 6 -> 12` scale policy 执行了两个新的 12-source V7-FRESH attempt，均使用独立 namespace，且均未触碰 B0 或 V7-INCREMENTAL：

- `r8-fresh-12source-20260829` 在 source 5 的 `extract_nodes.extract_summaries_batch` 收到 `finish_reason=length`（32,768 completion tokens），产生 malformed JSON。fail-closed runner 已写入 `RUN_FAILURE.json`、`RUN_MANIFEST_FAILURE.json`、`ROUTE_EVENTS_PARTIAL.json` 与 `provider_events_partial.jsonl`；完成 source 5/12，不能恢复或统计。
- 针对该 failure，V7-FRESH 增加了显式 bounded summary entity partition（默认 V6 关闭，V7 capacity=24），并通过 TDD 验证分页后摘要结果完整合并、没有实体丢失或 work reduction。
- `r9-fresh-12source-summary-pages-20260829` 证明 summary partition 已绕过 length truncation，但 source 8 的一个摘要页触发 64K context admission error：prompt 至少 32,769 tokens 加请求的 32,768 output tokens。根因是 8B dual runtime 未在 router completion seam 安装已有的 local context-budget adapter。

已修正 runtime：8B dual 在 V7-FRESH/V6.1 共用的本地 completion seam 安装 context-budget adapter，并在 close 时恢复；它只根据本地 tokenizer 和 32-token safety margin 收紧 wire `max_tokens`，不改变 prompt、模型、请求顺序、语义 operator 或 endpoint set。该 adapter 的目标是把 provider context rejection 变成可观测的 bounded request，而不是掩盖截断；失败 attempt 仍保留 append-only 审计。summary partition 与 context budgeting 均在下一次全新 namespace 重新验证，旧 r8/r9 不升级为成功结果。

42. Execution Log — V7-FRESH downstream QA overlay — 2026-08-29

`r10-fresh-12source-summary-pages-budgeted-20260829` 已完成 construction、graph semantic 与只读下游 QA qualification；没有重跑 B0，也没有启用 V7-INCREMENTAL treatment。

Construction seal（保持不变）：

- `status=PASS`，12/12 durable publication，source order `[0..11]`；`T(V7_FRESH)=688.477473295 s`。
- graph digest：420 entities、295 edges、12 episodes；canonical graph hash `c00e5f3dd781976cdc70220f65cfb2b1affefabd7ab97cbc5fc0cb6c67353e04`。
- observed work：333 logical LLM calls、781 physical transport attempts、1,895,790 prompt tokens、83,846 completion tokens；658 embedding calls/2,203 items；2,008 DB reads、60 DB writes。

Graph semantic sidecar：`7/7 PASS`。首次 retrieval-only diagnostic 为 3 个 prefix-complete QA，mean evidence recall@5=`0.8667`、@10=`0.9333`；该 diagnostic 不包含 Reader/Judge，不能作为 downstream QA 结论。

只读 downstream overlay 使用独立输出目录（`r2` 因 artifact privacy 修正保留为审计失败/非权威尝试，以下 `r3` 为权威结果）：

`/data/predator/ly/Mem/experiments/local-qwen3-8b-awq-dualreplica-v1/v7_fresh_qualification_qa/r10-fresh-12source-summary-pages-budgeted-20260829-r3/RESULT.json`

- 固定 Quality-v1 retrieval、Reader 与官方 LongMemEval Judge；Judge 显式绑定实际 served model `qwen3-8b-awq`，使用同一 8B dual endpoint identity、temperature=0、no-thinking、max attempts=1。
- 3 个 complete-gold、prefix-12 可寻址问题全部完成：Reader/Judge `3/3` 有效，官方 Judge accuracy `2/3=0.6667`；session recall@10 `3/3=1.0`。
- construction latency 排除；QA Reader/Judge latency 不回填 construction makespan；namespace state 前后相同（432 nodes、776 relationships、12 episodes），artifact snapshot unchanged；database mutation attempts/mutations=`0/0`。
- 首次 overlay attempt 因复用了历史固定 `qwen3-32b-fp8` Judge wrapper 而失败，未改变 namespace；`r2` 虽改为显式 8B generic backend 并得到相同数值，但其 artifact 暂存了不应持久化的参考答案诊断；最终独立 `r3` 同样绑定 8B generic backend 且仅保存 terminal judge projection，作为权威 QA 结果。首次失败与 `r2` 目录均保留为 append-only 工程审计，不纳入 QA 统计。

判定：V7-FRESH downstream QA overlay `PASS`，但 scope 明确为 `V7_FRESH_PREFIX_DOWNSTREAM_QA_ENGINEERING_QUALIFICATION`。当前只有 12-source prefix、3 个 QA，不能计算 B0-relative headline speedup 或完整 5-history non-inferiority；`headline_noninferiority_authorized=false` 仍保持。该结果满足继续做 observer-only characterization 的质量前置条件，但 V7-INCREMENTAL live treatment 仍需 frozen differential correctness、opportunity gate、adaptive fallback contract 与 online economics gate，未获授权前不得启动。

43. Final Methodology Closure Revision — 2026-08-29

本节根据 `v7优化prompt.md` 对当前研究做最后收口；旧章节、NULL、failed attempts、sealed artifacts 与历史 ledger 均保留。后续执行顺序冻结为：

`Theory / Contract Freeze -> Source Audit -> Observer-only Characterization -> Offline Counterfactual -> Opportunity Gate -> Minimum Method -> Provider-free Correctness -> Minimal Live -> Scale-up -> Publication Campaign`。

43.1 研究问题与归因边界

- V6 只回答 dependency slack 是否能在不改变 B0 logical work 与 ordered state evolution 的前提下改变执行时机；V6 已进入 evaluation-only。
- V7-FRESH (`V7_FRESH_CONTROL_V1`) 先回答新的 stable source-local semantic boundary 是否具有质量与工程可行性。
- C0 是全 closure 的保守理论参照；C1 是 guarded dynamic repair：dirty view fresh recompute 后与旧 canonical output 比较，只有 semantic/structure change 才向 successor 传播。
- 最终 pure incremental estimand 固定为 `T(V7_FRESH_CONTROL_V1) / T(V7_INCREMENTAL)`；论文 headline 仍回到 `T(B0_NATIVE_SERIAL) / T(V7_INCREMENTAL)`；B1 永远只是 relaxed-order ceiling。

43.2 本轮仅有的 methodology corrections

1. LLM partition 不再声称与不可执行或会截断的 hypothetical unpartitioned call exact-equivalent；改为 exact input/work coverage + deterministic merge + frozen algorithm identity + B0 quality Gate。
2. V6 dynamic work-preservation 提升为 per-call canonical logical identity 对齐，而不是笼统的总 work 相似。
3. D0 必须用保留 ordered publication、resource/semantic dependency、UNKNOWN fresh 的 counterfactual DAG longest path，禁止用 saved-work 求和替代 makespan。
4. Architecture Rescue 最多一轮、一次一个 evidence-selected hypothesis；改变 FRESH 语义即生成新 identity 并重新 qualification。
5. 正式 publication 使用 paired same-history design、明确失败排除规则与 paired uncertainty；qualification 不自动成为 headline statistics。

43.3 已冻结新增合同

- `v7/V7_FRESH_ALGORITHM_IDENTITY.json`：冻结 V7-FRESH V1 的 Stage A/B、adapter、runtime、publication 与比较身份，锚定 r12 prefix-30。
- `v7/V7_FRESH_ADAPTER_COVERAGE_SEAL.json`：严格记录 summary/dedupe/context partition 的完整输入覆盖；不作不可证明的 partitioned-LLM output theorem。
- `v7/V7B_OBSERVER_TARGET_CONTRACT_8B.json`：规定 observer 必须指向 V7-FRESH Stage B/stateful semantic computation，不能把 V7-A opaque trace 当成 V7-B 证据。
- `v7/V7B_GATE_FREEZE_V2.json`：冻结 A/B/C/D0/D1 的阈值、DAG 估计式、fallback safety、一次 rescue 限制与 treatment=false。
- `v7/R1_R3_PROTOCOL_FREEZE_8B_DUAL_V1.json`：冻结当前 8B 双 GPU observer provider/routing/workload/harness 身份，独立于旧 SiliconFlow 协议。

43.4 TDD 与 autoresearch 纪律

- 所有方法变化先添加 provider-free failing test，再实现，再跑 targeted test，最后跑完整 suite；本轮最新完整结果为 `579 passed`。
- scheduler/lane/future cap 等 deployment 参数不属于本轮方法搜索；不以最快一次 live run 选择 method。
- 任何失败 attempt 必须使用新 run-id、新 namespace、append-only failure artifact；禁止重放、覆盖或把失败混入统计。
- 当前 C1 reference 已通过 13 对 provider-free canonical differential；新增 `run_v7b_counterfactual_campaign.py` 输出 FRESH/C0/C1、reconvergence 与 conservative CP lower bound。该结果仅为离线 TDD 证据，不授权 live。

43.5 当前执行状态与下一步

- r12 是 `V7_FRESH_CONTROL_V1` candidate anchor：30/30 publication、7/7 graph semantic PASS；11 题 QA 仅为 engineering qualification，headline non-inferiority 仍 false。
- r13 是 scope configuration failure；r14 在 R1-R2 尚未完成时被执行会话中断，已按 `INVALID_FOR_R1_R3_GATES` 封存，均不得进入统计。
- 下一合法动作是以新 run-id 重跑 8B observer-only 2+6+6；其后完成 target audit、paired semantic-node ground truth、B0 read-only matched QA、algorithm-tax audit、FRESH/C0/C1 offline 与 D0/D1 决策。
- 在 A/B/C/D0/D1 全部通过前，V7-INCREMENTAL live、M2、d>1、new scheduler、summary/predicate/work-reduction shortcut 均保持 blocked。

任何后续结论必须同时给出：代码/identity hash、platform/workload contract、baseline role、quality scope、work/critical-path accounting、fallback 与失败样本；若条件不满足，`NULL` 是合法且优先于事后堆叠优化的研究结论。

44. Execution Log — r16 observer characterization and architecture-rescue plan — 2026-08-29

本轮先按 `v7优化prompt.md` 收口后的计划恢复并验证了完全统一的 `local-qwen3-8b-awq-dualreplica-v1` 平台：native `18200`、prepare `18201`、embedding `18202`、Neo4j Bolt `7687` 均 UP；模型、embedding、dataset hash、Graphiti `0.29.3`、routing contract 与 `R1_R3_PROTOCOL_FREEZE_8B_DUAL_V1.json` 一致。没有重跑 B0、没有修改 V6-Core、没有启动 B1 或 V7-INCREMENTAL treatment。

新 attempt `r16-observer-2plus6plus6-20260829` 完成了冻结的 R1-R2、R3-A、R3-B 三个 observer-only block，状态 `SEALED`，`treatment_calls=0`、`response_replay_calls=0`，route event 1,826。所有 artifact 使用新 namespace；dangling-edge 日志仅作为 Graphiti 工程观测，最终以 semantic/provenance artifact 判定，不作为成功或失败的单行日志证据。

44.1 结果驱动的判断

- R1 assumption audit：`real_graphiti_evidence=true`、dependency edge kinds complete、`false_stable=0`、`false_unaffected=0`，但 A7/A9 为 `UNKNOWN`，因此 `core_assumptions_supported=false`，必须 fail-closed。
- R2 causal trace：65 个 semantic reads、31 个 requests；当前 `node_cosine` seam 中 read query/filter/config identity 随 mutable previous state 改变，观测为 `demand_prediction=UNKNOWN`、`demand_truth=CHANGED`。这不是“证书阈值太保守”的证据，而是当前边界没有可证明的 early stable reuse。
- R3 decision：两个独立 six-source block 均完成，但 `CSP=null`、`reconvergence_rate=0`、`stable_prediction_count=0`、`early_memory_specific=false`、`sca_work=7.895260057`，`CRITICAL_OPPORTUNITY.status=UNKNOWN_INCOMPLETE_SEMANTIC_DAG`。因此 Gate A-E 全部不通过，`METHOD_SELECTION.selected_method=NULL`，`treatment_authorized=false`。
- 该结果不能支持任何线上 speedup 结论；也不能把 provider-free C1 的 work reduction 当作真实 Graphiti economics。当前最诚实的结论是：V7-B 的现有 `node_cosine` 目标尚未显示可归因的 incremental opportunity。

44.2 唯一 architecture-rescue hypothesis（最多一轮）

`H1-source-local-boundary`：如果把可复用边界严格限制为不读取 mutable memory 的 source-local semantic IR，并将 node resolution、edge resolution、attribute materialization 和 ordered publication 明确视为每次 state delta 后的 stateful views，那么可能获得可证明的局部维护；任何依赖 mutable query/filter/domain 的 `node_cosine` 结果仍保持 `UNKNOWN`，不得被放宽为 `STABLE`。

该假设是对边界的验证，不是对证书、lane、future cap 或 lookahead 的参数搜索。若 H1 仍不能在 provider-free differential 中产生非零安全 reuse 或在 conservative DAG 中产生正的 critical-path margin，则正式记录 `V7B_ARCHITECTURE_NULL`，不再堆叠第二个 rescue 或启动 live treatment。

44.3 H1 的 TDD/autoresearch 执行协议

1. 先增加 provider-free failing tests：source-local IR 在任意 state/frontier 变化下 canonical identity 不变；不同 source hash 不得误命中同一 IR artifact；任何 unknown environment/delta 必须 fallback 到 FRESH；ordered publication 与 FRESH/C1 canonical differential 必须保持不变。
2. 通过 targeted tests 后运行现有 13 对 C0/C1 counterfactual campaign，分别报告 safe reuse、exact reconvergence、affected work 与 conservative longest-path；禁止用 saved-work 求和替代 makespan。
3. 只有 H1 的 correctness、quality、locality、D0、D1 全部满足冻结阈值，才生成新的 algorithm identity 并授权最小 M1 live；否则保留 `NULL`，不修改冻结 V7-FRESH V1，不进行部署参数 autoresearch。

本节之后的停止条件因此明确为：完成 H1 的 provider-free TDD 与反事实审计；若无正的、可证明的 critical-path opportunity，V7-INCREMENTAL 以 architecture/economics NULL 结束，而不是为了得到正结果继续改变方法边界。

45. H1 Execution Result and Stop Decision — 2026-08-29

按 44.3 执行了 H1 的 TDD 与 provider-free 反事实审计。第一轮 targeted test 暴露 `stable_mention_id` 未包含 `source_id` 的跨 source identity collision；已将 `source_id` 加入 canonical digest，并新增同源复用、跨源隔离、跨 state version 稳定性测试。修复后 targeted `13 passed`，完整 `saturated_fixed_work_baseline_v1_3/tests`（正确 PYTHONPATH）`581 passed in 8.48s`。

新的 `v7_counterfactual/v3-h1-identity-20260829` 保持 `13/13` FRESH/C0/C1 canonical differential；C0 affected-work fraction=`0.7019230769`，C1=`0.4769230769`，C1 相对 C0 provider-free work reduction=`0.3205479452`。这些数字只证明 reference engine 的 guarded repair accounting，不能转化为 live speedup。

H1 修复了一个真实工程正确性问题，但没有改变 r16 的真实 Graphiti 证据：`node_cosine` 仍没有 certifiable STABLE observation，reconvergence=`0`，CSP=`null`，critical opportunity=`UNKNOWN_INCOMPLETE_SEMANTIC_DAG`，因此不能授权 M1 treatment。依据冻结的最多一轮 rescue policy，本研究在当前 V7-FRESH identity 下记录 `V7B_ARCHITECTURE_NULL` / `NULL_NO_ECONOMIC_OPPORTUNITY`，停止继续堆叠 V7-B 机制。

本轮报告：`MemBind_V7_H1_ARCHITECTURE_RESCUE_REPORT_20260829.md`。未来若要继续，只能提出新的、明确改变 semantic boundary 的算法版本并新建 identity，重新执行全部 source audit、quality、observer、D0/D1 gate；不得在当前 identity 下重跑 treatment 或搜索 scheduler/lane/future-cap。

## 46. Sealed B0/FRESH matched audit and closure — 2026-08-29

在恢复同一 `local-qwen3-8b-awq-dualreplica-v1` 平台后，完成了 B0
`NATIVE_SERIAL` 的只读 matched downstream QA overlay；没有重跑 B0，也没有
修改任何 sealed artifact。B0 namespace 前后状态一致，数据库 mutation
attempts/mutations=`0/0`，11/11 Judge 结果有效。

同一 frozen Quality-v1 contract 下，B0 与 `V7_FRESH_CONTROL_V1` 的 prefix
engineering qualification 完全一致：accuracy=`0.5454545455`，mean
Recall@10=`0.9136363636`。该结果仍不是 full-five-history non-inferiority，
`headline_noninferiority_authorized=false` 保持不变。

完成了只读 sealed evidence audit：

- 公共硬件、GPU UUID、Qwen3-8B revision、Embedding、Neo4j、软件和 workload
  canonical digest 全部匹配；两次 platform manifest 的差异仅来自捕获时间
  和 method-specific routing entries；
- `T_B0=2636.463018176s`，`T_FRESH=3958.332938057s`；因此
  `T_B0/T_FRESH=0.6660538816`，FRESH 相对 B0 为 `1.5013800348x`
  (`+50.138%`) wall-clock tax。FRESH 是 control，不把该比值写成 V7
  headline speedup；
- FRESH 的 observed work 相对 B0 为：logical LLM `1.0058x`、transport
  `1.1836x`、Embedding `1.4632x`、Neo4j reads `1.5220x`、writes `1.0x`。
  logical operator span 显示 `dedupe_nodes.nodes` 从 `205.167s/19 calls`
  增至 `2591.111s/29 calls`，说明当前 algorithm boundary 的 stateful
  resolution tax 是主要问题，而非硬件不公平；
- machine-readable audit：
  `/data/predator/ly/Mem/experiments/local-qwen3-8b-awq-dualreplica-v1/v7_audit/sealed-evidence-audit-20260829/RESULT.json`；
  human-readable report：`MemBind_V7_SEALED_EVIDENCE_AUDIT_20260829.md`。

因此当前身份的结论继续冻结为 `V7B_ARCHITECTURE_NULL` /
`NULL_NO_ECONOMIC_OPPORTUNITY`：D0=`UNKNOWN`（没有 live incremental DAG
和安全 critical-path margin），D1=`UNKNOWN`（没有 online incremental
economics），treatment 不授权。任何改变 semantic boundary 的后续研究
必须新建 algorithm identity，并重新完成 source audit、quality、observer
和 D0/D1 gates；当前身份不再启动 V7-INCREMENTAL、M2、d>1 或 scheduler
autoresearch。

## 47. DMSV Phase 2B methodology freeze — 2026-08-31

本节是根据 `workplan_v7优化prompt.md` 新增的 append-only 冻结执行契约，
不删除前述 V7-B/DVSR 记录，也不把已有 provider/live diagnostic 升级为
本轮证据。本轮唯一目标是审计并冻结 DMSV provider-free Phase 2B，执行
顺序固定为：

`Stage A: identity/evidence audit -> workplan freeze -> Stage B: B0 preregistration -> B1 closure -> (only if MAIN_TRACK_CANDIDATE) B2/B3 -> B4 seal and stop`。

### 47.1 审计身份与边界

- `audited_commit=f91a0500beb87d5013644442e135e6d3afb4507c`；branch=`main`；
  `origin/HEAD` 与 `origin/main` 均指向同一 commit；remote identity 已核验。
- `workplan_v7.md` 输入 SHA-256=`049d27250ce83343037cf25bbb8d5556742c554ca4ea5ab2d6f33e3f192891e0`；
  本 prompt 输入 SHA-256=`38cd4e5208ef3717f5a4640cc7b2949c00a5f14e1efe552c12ccd92729ebefb3`。
- 实际 Graphiti pin=`0.29.3`；平台身份仍为
  `local-qwen3-8b-awq-dualreplica-v1`，B0 anchor 继续指向既有 sealed
  `NATIVE_SERIAL/d6e9e240c3ce`，B1 只作 relaxed-order upper bound。
- 当前 worktree 有此前实验/修复改动；这些 modified/untracked 文件均保留，
  本节不执行恢复、清理、commit 或 push。
- 本轮禁止真实 provider、live Graphiti treatment、authoritative write、
  Phase 3A/3B、held-out 访问、scheduler/lane/future-cap 参数搜索；即使
  B1 得到候选，本轮也必须在 B4 停止。

### 47.2 研究中心与 method boundary

论文中心暂定为 `DMSV — Delta-Maintained Semantic Views for Stateful Agent
Memory Construction`。方法必须分层记录：

`V_rank -> V_prompt -> V_request -> ResponseArtifact -> Continuation`。

只有 source-local semantic IR 的提前物化可以不读 mutable memory；
`ResponseArtifact/Continuation` 的复用、fresh recompute 与 descendant
reconvergence 分开记账。DMSV 不得把“Top-K kernel 能增量更新”直接包装成
主方法，除非它覆盖当前 dominant critical path 且保持 Graphiti 原生 call
boundary 和 B0 state/publication semantics。

### 47.3 BaseViewAvailability 最高优先级 Gate

逐路径记录 `BV-NATIVE`、`BV-VERSIONED`、`BV-PERSISTENT` 的
`BaseMaterializationCost`、`DeltaMaintenanceCost`、snapshot/epoch、
lifecycle/GC、unused/failed work、seam tax 和 `t_base_ready <=
t_authoritative_need`。缺证明统一为 `UNKNOWN`，不得推断为 pass。

现有 sealed timing recovery 只证明 `V6 PreparedArtifact` 与 stateful
BaseView 不同：29 个 source pair 中只有 1 个在 predecessor publication
开始前具备 cross-snapshot launch 条件；它不能证明可持续的 versioned 或
persistent BaseView。没有已实现的 DMSV BaseView lifecycle/maintenance
artifact 时，B1 只能输出 `BLOCKED` 或 `DMSV_BASE_VIEW_UNAVAILABLE`，不能
进入 B2。

### 47.4 Dominant request closure

使用 Graphiti 0.29.3 的真实 `prompt_library.dedupe_nodes.nodes` 与完整
canonical serialization，必须把 extracted nodes、candidate ID/order 与
payload、current episode、`previous_episodes`、entity schema、unresolved
membership、batch shape、template/serialization 以及 model/config/schema/
index epoch 纳入 `V_request`。Top-K ID 相同不等于 request exact；若只拆分
原生 batch call 才能局部化，必须标记为新的 algorithm identity。

### 47.5 Phase 2B preregistration and decision table

`B0` 冻结 exact state/query/filter/config identities、full-scan/fresh-request
oracle、delta types、unknown-to-fresh fallback、no-write proof 与 work/
latency accounting。B1 先做 BaseViewAvailability 与 dominant-request
closure；按以下优先级 fail closed：

1. 无任何已证明合法 BaseView 路径 -> `DMSV_BASE_VIEW_UNAVAILABLE`；
2. BaseView 可用但 dominant request 在相邻 state 必然变化且无原生合法局部化
   -> `DMSV_DOMINANT_CALL_UNAVOIDABLE`；
3. 只覆盖 retrieval kernel -> `KERNEL_ONLY`；仅 scale crossover 有收益 ->
   `SCALABILITY_TRACK`；
4. 只有完整 exactness、合法性和 critical-path value 均成立，才是
   `MAIN_TRACK_CANDIDATE`。

只有 `MAIN_TRACK_CANDIDATE` 才能执行 B2 Exact Top-K Delta Maintainer TDD
和 B3 layered affectedness/economics TDD；否则直接写 B4 report/ledger 并
停止。B2/B3 的任何 patch/refill、buffer、materialization/maintenance、
affectedness、reconvergence 和 longest-path accounting 都必须与 full-scan
oracle exact 对齐，不能用 saved-work 求和替代 makespan。

### 47.6 冻结标记

本节在 Stage A identity/evidence/terminology/forbidden-action/self-audit、
targeted TDD、`git diff --check` 和 SHA-256 记录完成后标记：

`WORKPLAN_FROZEN_FOR_PHASE2B`。

Stage B 交付物为 DMSV evidence closure、B0 preregistration、B1 closure
matrix、Phase 2B report/ledger 和测试结果。无论最终是正、负或 BLOCKED，
都必须在 B4 停止；不得在本轮自动启动 Phase 3A/3B、live treatment 或
full evaluation。

## 48. DMSV Phase 2B execution seal — 2026-08-31

`WORKPLAN_FROZEN_FOR_PHASE2B` 已写入并执行。B0 preregistration 与 B1
provider-free closure 已封存于 `v7/dmsv_phase2b/`：

- `DMSV_PHASE2B_PREREGISTRATION.json`：冻结 B0、oracle、delta、unknown/
  fallback、BaseView、accounting 和 forbidden-action contract。
- `DMSV_B1_CLOSURE.json`：`BV-NATIVE=FAIL`（29 个已恢复 pair 中只有 1 个
  满足及时 launch 条件），`BV-VERSIONED=UNKNOWN`，`BV-PERSISTENT=UNKNOWN`；
  aggregate `base_view_verdict=BLOCKED`。
- `DMSV_DOMINANT_REQUEST_DELTA_MATRIX.json`：在 Graphiti 0.29.3 实际
  `prompt_library.dedupe_nodes.nodes` 上，candidate payload/order/membership、
  `previous_episodes`、batch shape、episode content 与 model/config/schema/
  index epoch 均改变完整 canonical request；因此当前 native batch boundary
  的 verdict 为 `DMSV_DOMINANT_CALL_UNAVOIDABLE`。
- `DMSV_PHASE2B_REPORT_20260831.md`：B0=`PASS`，B1=`BLOCKED`，B2/B3=`NOT_EXECUTED`
  （缺少 `MAIN_TRACK_CANDIDATE` 授权），B4=`COMPLETE`。

Provider-free DMSV TDD 为 `3 passed`，纳入 DMSV 与全部 corrective contracts
的相关 regression slice 为 `160 passed`。完整 repository suite 为 `718 passed, 7 failed`；失败均为
历史 source-hash freeze binding 漂移，已在 B4 report/ledger 明确保留，未改写
历史协议 hash。`py_compile` 与 `git diff --check` 通过。

最终状态：`BLOCKED`。本轮已在 B4 停止，未执行 provider/live treatment、
Phase 3A/3B、Top-K maintainer、scheduler search、held-out access 或 full
evaluation。任何未来重开必须新建 algorithm identity，并先证明一个合法的
timely BaseView 路径及完整 dominant request 的 exact preservation/localization。

## 49. DMSV B1 closure repair — 2026-08-31 (append-only correction)

本节纠正 `58a925f` 之前报告中的 provenance、diff-check 与因果结论边界；不
修改或重写任何历史 sealed artifact。输入 revision 固定为
`58a925f372db1a095c9e90b969ad74d101c4e96a`，parent 为
`f91a0500beb87d5013644442e135e6d3afb4507c`，Graphiti 为 `0.29.3`。

### 49.1 Provenance and integrity correction

`f91a050..58a925f` 实际包含 32 个文件、3075 insertions 与 62 deletions；
逐文件分类必须写入 `DMSV_COMMIT_SCOPE_AUDIT.json`，不得沿用旧报告的“9 个
主要文件”摘要。对输入 commit 执行 `git show --check` 得到 FAIL，原因是旧
`DMSV_PHASE2B_REPORT_20260831.md` 第 3、4 行 trailing whitespace。因此旧
结论只能记为 `prior_commit_diff_check=FAIL`，不能 retroactively 改写旧报告。

workplan hash 必须拆分为：
`stage_a_input_workplan_sha256`、`stage_a_frozen_workplan_sha256`、
`post_b4_append_workplan_sha256` 与 `correction_workplan_sha256`；旧报告中
无法由当前 checkout 重现的 `7fc39e6d...` 记为
`OLD_FROZEN_WORKPLAN_HASH_UNREPRODUCIBLE`。

### 49.2 Dominant-request causal vocabulary

provider-free synthetic mutation 只能证明：

`Sensitivity = field mutation changes canonical request`。

真实相邻 authoritative states 才能证明：

`Inevitability = the field changes in an actual adjacent state pair`。

只有再证明原生 `dedupe_nodes.nodes` batch boundary 没有合法 localization，
才能声称：

`Unavoidability = inevitable field change + no native localization seam`。

已有 delta matrix 保留为 sensitivity evidence。真实 observer pair 若缺少
`reference_time`、`group_id/source/last_n`、完整 `request_binding_digest`、
config/schema/index epoch 或 decoding contract，必须输出
`REAL_PAIR_WITNESS_MISSING_FIELD`，并将最终状态保持为
`BLOCKED_DOMINANT_REQUEST_INEVITABILITY_UNPROVEN`；不得升级为
`DMSV_NATIVE_NODE_NULL_DOMINANT_CALL_ALWAYS_DIRTY`。

### 49.3 Repair execution order and stop state

先写入并 hash `DMSV_B1_CLOSURE_REPAIR_PREREGISTRATION.json`，再执行 R3
provider-free tests。R3 只允许使用 Graphiti 0.29.3 的真实 retrieval/prompt
assembly、冻结 development observer 的 digest-only membership/order/request
证据和可从固定 workload 合法恢复的字段；禁止用手工覆盖
`previous_episodes` 冒充 causal witness。R5 必须在 parent/current clean
checkout 中复现完整测试历史并逐项归类
`PREEXISTING_FAILURE`、`COMMIT_INDUCED_SOURCE_HASH_DRIFT`、
`ENVIRONMENT_DEPENDENT` 或 `UNRESOLVED`。

本轮新增文件均为 append-only correction artifacts：
`DMSV_B1_CLOSURE_REPAIR_PREREGISTRATION.json`、
`DMSV_COMMIT_SCOPE_AUDIT.json`、`DMSV_DOMINANT_REQUEST_CAUSAL_WITNESSES.jsonl`、
`DMSV_ADJACENT_STATE_REQUEST_CAUSAL_PROOF.md`、`DMSV_PHASE2B_CORRECTION.json`、
`DMSV_PHASE2B_CORRECTION_20260831.md`、`DMSV_B1_CLOSURE_REPAIR_LEDGER.jsonl`
和 `tests/test_dmsv_b1_closure_repair.py`。不得修改
`DMSV_PHASE2B_REPORT_20260831.md`、`DMSV_B1_CLOSURE.json`、
`DMSV_DOMINANT_REQUEST_DELTA_MATRIX.json` 或旧 ledger。

固定终态约束：`MAIN_TRACK_CANDIDATE=false`、`B2_AUTHORIZED=false`、
`B3_AUTHORIZED=false`、`PHASE3A_AUTHORIZED=false`、`LIVE_AUTHORIZED=false`。
只有 complete real-pair witness、Graphiti actual chain、native boundary
localization 证据与 identity/environment closure 全部通过，才可在未来新
identity 下重新评估；本轮在 correction B4 后停止，不启动 provider/live。

## 50. DMSV B1R2 Structural Closure — 2026-08-31 (current authoritative entry)

This append-only section is the authoritative plan for the `DMSV B1R2 Structural
Closure` run. It does not modify Sections 48 or 49, the Frozen V6 substrate, or
any sealed Phase 2B evidence. The run answers only whether a completely bound
real adjacent authoritative transition makes the native Graphiti
`dedupe_nodes.nodes` request structurally dirty, and whether the unchanged
native batch boundary offers a legal localization seam.

### 50.1 Fixed identity and authorization

- `input_commit=37871aae8193d994a1642605e3a705712dd786e1`,
  `parent=58a925f372db1a095c9e90b969ad74d101c4e96a`;
  `branch=dmsv-b1r2-structural-closure`; Graphiti=`0.29.3`;
  profile=`local-qwen3-8b-awq-dualreplica-v1`.
- The prior BaseView status remains `BV-NATIVE=FAIL`,
  `BV-VERSIONED=UNKNOWN`, `BV-PERSISTENT=UNKNOWN`,
  `base_view_verdict=BLOCKED`.
- Fixed authorization flags are all false:
  `MAIN_TRACK_CANDIDATE`, `B2_AUTHORIZED`, `B3_AUTHORIZED`,
  `PHASE3A_AUTHORIZED`, `PHASE3B_AUTHORIZED`, `LIVE_AUTHORIZED`,
  `HELD_OUT_AUTHORIZED`, `TOPK_MAINTAINER_AUTHORIZED`,
  `SCHEDULER_SEARCH_AUTHORIZED`.

### 50.2 Frozen claim lattice and state vector

The five claim levels are immutable for this run:

`L1 SENSITIVITY` -> controlled mutation changes canonical request;
`L2 DIRTY_WITNESS_EXISTS` -> at least one fully bound eligible adjacent pair
changes request;
`L3 DIRTY_RATE_ESTIMATED` -> dirty fraction over preregistered development
population;
`L4 STRUCTURALLY_ALWAYS_DIRTY` -> theorem/exhaustive proof covers every eligible
transition;
`L5 NATIVE_CALL_UNAVOIDABLE` -> L4 plus no legal localization at the frozen
native batch boundary.

One pair cannot imply L4; request change cannot imply L5; Top-K or UUID equality
cannot imply canonical request equality. The state vector is reported
independently as:

`PHASE2B_STATE=BLOCKED`;
`BASE_VIEW_STATUS=BLOCKED_NO_PROVEN_PATH`;
`NODE_REQUEST_STATUS=DIRTY_WITNESS_INCOMPLETE`;
`NATIVE_LOCALIZATION_STATUS=UNPROVEN`;
`ARTIFACT_PORTABILITY_STATUS=NON_SELF_CONTAINED`;
`SEMANTIC_ROOT_STATUS=REQUIRES_SCOPE_AUDIT`.

### 50.3 Eligible transition domain

For predecessor `p=i` and future source `f=i+1`, a pair enters the structural
domain only if E1--E13 all pass: same frozen history/order; identical prepared
future extraction; `p` durably published in `S_(i+1)` but absent in `S_i`; `p`
visible to retrieval; reference-time predicate passes; group/source selector
passes; known `last_n>0`; no later-authoritative visibility; `dedupe_nodes.nodes`
invoked in both executions; all model/config/schema/index/template/
serialization/decoding epochs match; previous projection reconstructable;
request binding complete; no unresolved valid_at tie or nondeterministic order.
Each condition is `PASS`, `FAIL`, or `UNKNOWN`; any key `UNKNOWN` excludes the
pair from L3/L4/L5.

### 50.4 Semantic-root and structural audit

The audit compares B0 Native, Frozen V6 no-reuse, and observed paths. It must
determine whether `strip_certified_previous_context` changes only preparation
inputs or also the `resolve_extracted_nodes` / `dedupe_nodes.nodes`
`previous_episodes` closure. The request layers remain
`V_rank -> V_prompt -> V_request -> ResponseArtifact -> Continuation`; UUID
changes alone are not prompt changes. The theorem covers both non-full and full
windows, selector/reference-time misses, ties, identical projections, omitted
calls, epoch changes, and any V6 context removal. Unresolved cases stay
`STATIC_THEOREM_UNKNOWN`.

### 50.5 Self-contained evidence and stop rule

All decision inputs must be content-addressed and repository-local, with no raw
episode content, raw UUID, prompt, model output, API key, database credential,
absolute machine path, or implicit external fixture. Convenience-mode missing
artifacts may skip, but evidence-required mode must fail. The only permitted
helpers are parsing, eligibility evaluation, canonical request comparison,
truth-table evaluation, and privacy/provenance validation. No Top-K maintainer,
batch split, prompt/schema rewrite, scheduler, provider call, DB write, or live
reuse is allowed.

The preregistration commit must precede every result artifact. A second local
result commit seals the final vector state and report. The run stops at B1R2/B5
regardless of outcome; no B2/B3 or live authorization can be inferred from a
dirty witness.
