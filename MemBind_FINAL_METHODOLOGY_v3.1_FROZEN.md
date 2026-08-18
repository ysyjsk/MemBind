# MemBind Final Methodology v3.1 — FROZEN METHODOLOGY

## 0. 文档定位

本文定义MemBind进入正式实现与paper evaluation之前的最终方法学版本。

该版本替换此前以：

- whole-update concurrency；
- Node-only Prepare；
- `C/W/K`参数调优；
- hard global Bind barrier；
- APC/prefix cache作为独立核心贡献；

为中心的原型描述。

MemBind的最终研究对象是：

> **有状态Agent Memory的construction runtime。**

MemBind不改变Memory architecture决定“算什么”，而是重新组织：

- 什么工作必须等待persistent state；
- 什么工作可以在目标state version产生之前提前执行；
- 哪些future update可以进入speculation；
- 哪个stateful work必须优先推进；
- 哪些合法ready requests可以为了backend locality重排；
- 最终如何保证persistent-state effects仍与serial reference一致。

最终方法概括为：

[
\boxed{
\text{Arrival Eligibility}
\rightarrow
\text{State-Cut Compilation}
\rightarrow
\text{Prepared Speculation}
\rightarrow
\text{Version-Bound Frontier Execution}
\rightarrow
\text{Ordered Publication}
}
]

在此基础上，可选地利用：

[
\boxed{
\text{Semantic Slack}
\rightarrow
\text{Cache-Affine Admission}
\rightarrow
\text{Existing Prefix Cache}
}
]

------

# 1. 核心研究问题

给定一个固定的、有状态的Agent Memory architecture：

- 不修改memory algorithm；
- 不修改prompt内容及schema语义；
- 不修改retrieval和QA逻辑；
- 不削弱原有state consistency contract；
- 不改变source evidence；
- 使用相同LLM、Embedding、DB和serving backend；

我们研究：

> **如何在未来memory version尚未产生时，安全地提前执行future update中只依赖已到达source evidence的计算，同时把所有mutable-state-dependent操作精确绑定到正确的前驱memory version，并优先推进最早能够改善memory visibility的stateful work。**

因此MemBind研究的不是：

```text
How to run more add_episode() calls concurrently?
```

而是：

```text
How to safely expose hidden execution freedom
inside a sequential stateful memory update?
```

------

# 2. 系统核心Insight

## 2.1 Insight 1：Update Boundary不是最小串行边界

Native memory construction通常表现为：

```text
Update_0
  Extract
  Resolve
  Update
  Commit

Update_1
  Extract
  Resolve
  Update
  Commit

Update_2
  ...
```

即：

[
U_0 \prec U_1 \prec U_2
]

但一次update内部并不是所有operator都依赖当前persistent memory。

部分操作只依赖：

```text
current source evidence
previously arrived source evidence
fixed prompt/schema/config
upstream pure results
```

而另外一些操作才真正依赖：

```text
latest committed entities
existing edges
current summaries
temporal validity
persistent identity
```

因此真正serialization boundary由：

[
\boxed{\text{Persistent-State Dependency}}
]

而不是：

[
\boxed{\text{Update Boundary}}
]

决定。

------

## 2.2 Insight 2：Stateful Update可以被Partial-Evaluate

定义第(i)个memory update：

[
U_i(E_i,S_i,M_{i-1})
\rightarrow M_i
]

其中：

- (E_i)：当前update的合法source evidence；
- (S_i)：该source stage按Evidence Fence冻结的immutable Evidence Snapshot；
- (M_{i-1})：第(i)次update必须观察到的前驱committed memory；
- (M_i)：完成第(i)次update后的memory state。

MemBind将其重写为：

[
A_i=C_i(E_i,S_i)
]

以及：

[
B_i=B(A_i,M_{i-1})
]

最后：

[
M_i=Commit(B_i,M_{i-1})
]

关键性质是：

[
C_i(E_i,S_i)
]

不依赖：

[
M_{i-1}
]

因此即使：

[
M_{i-1}
]

尚未产生，只要(E_i)已经合法到达且对应(S_i)已由Evidence Fence冻结，就可以执行(C_i)。

而：

[
B_i
]

必须等待：

[
M_{i-1}
]

产生以后才能执行。

因此MemBind的核心不是普通DAG scheduling，而是：

> **对stateful memory update进行state-cut transformation，将evidence binding与state binding分离。**

------

# 3. 系统定位

MemBind位于Memory architecture和LLM serving backend之间。

```text
┌──────────────────────────────────┐
│ Agent / Application              │
└──────────────────────────────────┘
                 │
                 ▼
┌──────────────────────────────────┐
│ Memory Architecture              │
│ Graphiti / future adapters       │
│                                  │
│ Defines WHAT to compute          │
└──────────────────────────────────┘
                 │
                 ▼
┌──────────────────────────────────┐
│ Memory Backend Adapter           │
│                                  │
│ Certified operator contracts     │
│ Construction Plan                │
└──────────────────────────────────┘
                 │
                 ▼
┌──────────────────────────────────┐
│ MemBind Runtime                  │
│                                  │
│ 1. Arrival Gate                  │
│ 2. State-Cut Compiler            │
│ 3. Prepared Reorder Buffer       │
│ 4. Publication Frontier          │
│ 5. Frontier-First Admission      │
│ 6. Optional Cache-Affine Policy  │
└──────────────────────────────────┘
                 │
                 ▼
┌──────────────────────────────────┐
│ LLM Serving Backend              │
│                                  │
│ vLLM                             │
│ APC / batching / chunked prefill │
└──────────────────────────────────┘
```

职责边界：

```text
Memory Architecture
    决定算什么

Memory Adapter
    声明哪些operator依赖什么

MemBind
    决定何时算、哪些可提前、何时绑定state

LLM Backend
    决定已经admit的request如何执行
```

MemBind：

- 不重新实现Graphiti；
- 不重新实现vLLM；
- 不实现新的KV cache；
- 不要求修改Neo4j事务模型。

------

# 4. Memory Construction Plan

## 4.1 Construction Graph

对于每个update (i)，Memory Adapter提供一个逻辑construction graph：

[
G_i=(V_i,E_i)
]

其中：

- (V_i)：construction operators；
- (E_i)：真实数据依赖、evidence依赖和state依赖。

该graph是**方法抽象**。

第一版实现不要求构建通用DAG engine，也不要求分析任意Python源码。

Graphiti Adapter可以静态声明并qualification其operator contracts。

------

# 5. Operator Contract

每个operator至少包含以下metadata。

## 5.1 Dependency Class

```text
EVIDENCE_BOUND
STATE_BOUND
```

### EVIDENCE_BOUND

只能读取：

- 当前已经arrive的source evidence；
- Evidence Snapshot；
- 固定schema/config；
- 上游EVIDENCE_BOUND/PURE operator输出。

不得读取persistent mutable memory。

### STATE_BOUND

执行结果取决于：

- persistent entities；
- resolved identity；
- mutable edges；
- latest summary；
- temporal validity；
- 当前committed memory的其他内容。

------

## 5.2 Effect Class

```text
PURE
STATE_READ
STATE_WRITE
PUBLISH
```

其中：

### PURE

既不读也不写mutable persistent state。

### STATE_READ

读取当前合法committed state。

### STATE_WRITE

产生persistent-state mutation。

### PUBLISH

使新的memory state按照serial-reference contract对后续stateful work可见。

------

## 5.3 Source Sequence

每个update具有：

[
seq_i
]

用于确定：

- predecessor state；
- publication frontier；
- source-order state effects。

------

## 5.4 Stream Identity

若workload包含多个相互独立的memory histories/namespaces，则每个update关联：

[
h_i
]

即memory stream identity。

所有state ordering guarantee都在同一stream内部定义。

不同stream之间不存在隐含state dependency。

第一版主实验可按history分别运行，避免把cross-history trivial parallelism与MemBind核心收益混在一起。

------

# 6. Arrival Eligibility

State speculation不能观察尚未发生的future input。

因此每个episode/update (i)具有arrival time：

[
t_i
]

只有满足：

[
t\ge t_i
]

时，update (i)才可以进入MemBind ready set。

定义：

# [ Eligible_i(t)

(t\ge t_i)
]

因此：

[
ReadyCompile(t)=
{
C_i
\mid
Eligible_i(t)
\land
DependenciesSatisfied(C_i)
}
]

关键区分：

```text
Future in state version
    可以speculate

Future in wall-clock arrival
    不可以提前观察
```

即：

> MemBind可以提前执行“已到达但尚未轮到state binding”的update，不能提前执行“尚未到达”的future episode。

------

# 7. Evidence Snapshot / Evidence Fence

对每个已经arrive的update (i)，定义immutable Evidence Snapshot：

[
S_i
]

其中(S_i)不是额外的future输入，而是由该source stage在arrival contract下已经合法可观察的source context确定并冻结：

[
S_i=
Freeze(
SourceContext_{\le i}^{arrived}
)
]

包含Native在该source stage合法可观察的source-local信息，例如：

```text
current episode
already-arrived previous source episodes
timestamps
fixed prompt/schema/config
upstream pure results
```

不得包含：

```text
future unarrived episodes
current mutable graph candidates
future resolved entity identities
future persistent edge state
future commit results
```

所有evidence-bound computation必须满足：

[
A_i=C_i(E_i,S_i)
]

不得读取：

[
M_{current}
]

Evidence Fence的作用是：

> **允许future-state speculation，但禁止future-evidence leakage。**

------

# 8. State-Cut Compilation

## 8.1 定义

MemBind不自动推断任意program的state dependence。

Memory Adapter负责certify每个operator的dependency/effect contract。

定义Compile Region：

[
C_i=
\operatorname{MaximalCertifiedEvidenceBoundSubgraph}(G_i)
]

要求其中所有operator满足：

[
R_M(v)=\varnothing
]

且：

[
W_M(v)=\varnothing
]

即：

- 不读取persistent mutable state；
- 不产生persistent mutable-state effect。

------

## 8.2 Prepared Artifact

执行：

[
C_i
]

得到：

[
A_i=PreparedArtifact_i
]

PreparedArtifact只能保存：

```text
immutable evidence-derived results
raw extraction results
pure intermediate structures
schema/config identity
source provenance
```

不得包含：

```text
state-resolved entity identity
future graph candidate identity
mutable edge version
future invalidation result
partially committed state
```

因此PreparedArtifact是：

> **state-unbound intermediate representation。**

------

## 8.3 Adapter Certification Protocol

`certified`不是人工注释，也不是对Graphiti源码的一次性主观判断。

对于每个候选Compile operator，Memory Adapter必须生成可重现的`CertificationRecord`，至少记录：

```text
memory backend / adapter version
operator identity / code revision
allowed evidence inputs
allowed upstream outputs
forbidden persistent-state APIs
declared persistent read set
declared persistent write/effect set
external side effects
rendered LLM input template/version
qualification trace digest
```

候选EVIDENCE_BOUND operator必须满足fail-closed资格规则：

```text
persistent mutable-state read count  = 0
persistent mutable-state write count = 0
undeclared external side effect       = 0
future-evidence access                = 0
```

qualification阶段应对Graph Driver、memory-facing search/read API、persistent write API和其他adapter声明的mutable-state入口进行instrumentation。

任何一次观察到：

```text
unexpected persistent read
unexpected persistent write
undeclared state-facing call
forbidden future-evidence access
```

均产生：

```text
STATE_CUT_CERTIFICATION_FAILURE
```

该operator不得进入Compile Region，而必须fail closed到STATE_BOUND Bind Region。

正式运行期间仍保留轻量runtime guard / event instrumentation。

如果已经certified的Compile operator在formal run中触发persistent-state access，则：

```text
current run invalid
direct hard violation recorded
operator certification revoked for the next protocol version
```

不得静默fallback后继续把该run计入正式结果。

Certification与以下identity绑定：

```text
memory backend version
adapter version
operator/code revision
prompt/schema version
```

上述identity发生变化时必须重新qualification。

因此：

[
C_i=
\operatorname{MaximalCertifiedEvidenceBoundSubgraph}(G_i)
]

中的“Maximal”仅表示：

> **在当前adapter声明的construction graph与已经通过上述fail-closed contract的operators之内，取满足evidence-bound闭包的最大子图。**

MemBind不claim自动证明任意Python程序的state independence。


# 9. Version-Bound Bind

对于第(i)个update，State-Bound Region定义为：

[
B_i=B(A_i,M_{i-1})
]

关键不变量：

[
\boxed{
Bind_i
\text{ must bind to }
M_{i-1}
}
]

而不是：

```text
whatever state happens to be latest
```

因此：

```text
Bind_i ≠ bind(artifact_i, arbitrary latest state)
```

而是：

```text
Bind_i = bind(artifact_i, exact predecessor state)
```

第一版实现不要求Neo4j支持显式MVCC。

只要同一stream内Bind严格按source order执行：

[
Bind_0\prec Bind_1\prec Bind_2
]

并满足下述backend/adapter contract，则当Bind(_i)开始时，底层合法state对应：

[
M_{i-1}
]

------

## 9.1 Exact Predecessor-State Assumptions

上述“当前state即精确前驱state”不是无条件成立，而依赖每个memory stream满足：

```text
single writer per stream
namespace / stream isolation
no external writer mutates the same stream
no hidden asynchronous mutation after Publish_i
Publish_i waits for every declared state effect of Bind_i
all spawned state-mutating futures/tasks are joined before Publish_i
```

其中`Publish_i`是一个**completion boundary**：

[
Publish_i
\Rightarrow
\text{all declared persistent effects of }Bind_i\text{ completed}
]

如果backend存在无法由adapter追踪或join的background state mutation，则该backend/configuration不满足当前Version-Bound contract，必须：

```text
fail qualification
or
narrow the formal claim
```

不得仅依据“函数已经return”推断：

[
DB=M_i
]

这些assumptions必须由Backend Contract与runtime event trace显式记录，而不是作为隐含前提。

------

# 10. Prepared Reorder Buffer

MemBind为每个memory stream维护Prepared Reorder Buffer：

```text
sequence    status

F           PREPARED / BINDING
F+1         PREPARED
F+2         COMPILING
F+3         ARRIVED
F+4         NOT_ARRIVED
```

每个slot至少记录：

```text
stream_id
source_sequence
arrival_state
compile_state
prepared_artifact
bind_state
publish_state
```

它允许：

```text
Compile_{F+3}
```

先于：

```text
Compile_{F+1}
```

完成。

但stateful effects仍受publication frontier控制。

核心原则：

[
\boxed{
\text{Execute evidence work out of order;}
\quad
\text{bind state in order.}
}
]

------

# 11. Publication Frontier

对于memory stream (h)，定义publication frontier：

[
F_h=
\min{
i
\mid
Publish_i
\text{ not completed}
}
]

只有frontier update允许进入state-bound execution。

如果：

[
Prepared[F_h]=true
]

且：

[
Publish_{F_h-1}=completed
]

则：

[
Bind_{F_h}
]

runnable。

对于：

[
j>F_h
]

即使：

[
Prepared[j]=true
]

也不能执行state-bound region。

------

# 12. Bounded Speculation

定义：

[
W
]

为per-stream speculation window。

当前frontier为(F)时，仅允许：

[
F\le i\le F+W
]

且：

[
Arrival_i\le t
]

的update进入Compile。

因此合法speculation set：

[
\mathcal S_F(t)=
{
i
\mid
F\le i\le F+W
\land
Arrival_i\le t
}
]

作用：

```text
限制PreparedArtifact占用
限制future speculation
限制LLM request burst
限制KV working set
避免无界ahead execution
```

(W)是runtime parameter，不claim novelty。

------

# 13. Frontier-First Work-Conserving Admission

这是最终版本相对于旧hard Bind barrier最重要的修改。

旧策略：

```text
Bind runnable
→ stop all Compile
```

过于粗粒度。

Bind中可能存在：

```text
LLM
DB
CPU
Embedding
LLM
DB
```

如果整个Bind期间冻结Compile，会让LLM capacity在DB/CPU阶段空闲。

因此MemBind采用：

> **Frontier-first、request-level、work-conserving admission。**

------

## 13.1 LLM Capacity

定义：

[
K_{LLM}
]

为整个construction runtime最多允许的真实LLM inflight requests。

所有真正发送给construction LLM backend的调用都必须经过统一admission controller，包括：

```text
NodeExtract
EdgeExtract
NodeResolve
EdgeResolve
Attribute/Summary
other construction LLM calls
```

不得只限制episode worker数，而让nested Graphiti calls绕过runtime。

------

## 13.2 Semantic Priority

如果frontier state-bound operator存在ready LLM request：

[
Ready(B_F)=true
]

则：

[
Priority(B_F)

> 

Priority(C_j)
]

对于所有speculative Compile (C_j)。

含义：

> frontier stateful request获得下一个可用LLM permit的最高优先级。

------

## 13.3 Work Conservation

但MemBind不要求：

```text
Bind ready
→ all remaining capacity idle
```

如果：

[
K_{LLM}=4
]

而frontier Bind当前只需要一个LLM request，则允许：

```text
slot 1 → Bind_F
slot 2 → Compile_{F+1}
slot 3 → Compile_{F+2}
slot 4 → Compile_{F+3}
```

因此：

[
Ready(B_F)
]

意味着：

> **speculative work不得在admission层插队frontier state advancement。**

而不是：

> **整个runtime停止speculation。**

------

## 13.4 非抢占性质

第一版不要求抢占已经发送到vLLM的Compile request。

因此准确语义是：

> **non-preemptive frontier-first admission。**

已经admitted的Compile可以完成。

但在新的LLM capacity释放时：

```text
frontier Bind
```

优先于：

```text
new speculative Compile
```

------

# 14. Resource Model

MemBind第一版只统一控制：

[
K_{LLM}
]

因为LLM request是主要共享construction resource。

Neo4j、Embedding和CPU不是同一resource pool。

因此：

```text
K_LLM
```

不能错误解释为：

```text
all construction operations concurrency
```

如果未来embedding成为独立瓶颈，可以增加：

[
K_{emb}
]

但这属于implementation policy，不是当前核心方法。

------

# 15. Cache-Affine Scheduling

Cache-affine scheduling不是独立核心贡献。

它只作用于：

> **已经通过state semantics判定为合法、且不延迟frontier state advancement的ready set。**

即先得到：

[
\mathcal S_{\text{legal}}(t)
]

再在其中选择执行顺序：

[
\pi
\in
\operatorname{Perm}(\mathcal S_{\text{legal}})
]

并优化：

[
CacheLocality(\pi)
]

完整关系：

[
\boxed{
State\ Semantics
\rightarrow
Legal\ Ready\ Set
\rightarrow
Frontier\ Priority
\rightarrow
Backend\ Locality\ Optimization
}
]

而不是：

```text
prefix locality
→ arbitrary request reordering
```

也不是：

```text
wait intentionally for a better cache cohort
→ delay the publication frontier
```

第一版MemBind保持**work-conserving**：cache affinity只决定“当前已有合法候选中先发谁”，不为了等待未来同prefix请求而主动空置可用LLM capacity。

------

# 16. Prefix Signature

## 16.1 Exact Rendered Request

对于真正发送给LLM backend的request (r)，先定义其完整rendered/tokenized输入：

[
T(r)=Tokenize(Render(r))
]

其生成必须固定并记录：

```text
model identity
model/tokenizer revision
chat template version
prompt/schema version
sampling/config fields that affect cache identity
rendered request
token ids
backend prefix-match granularity G
physical KV block size(s), if distinct
backend cache identity/hash configuration
```

不得仅根据：

```text
operator == NodeExtract
```

或：

```text
source mapping hash
```

推断两个request具有可复用prefix。

operator type只能作为候选分组hint，不能作为exact-prefix证据。

------

## 16.2 Backend Prefix-Match Granularity

MemBind不得把physical KV block size硬编码为通用prefix-match边界。

定义：

[
G=
\text{frozen backend/version提供的effective prefix-match granularity}
]

即backend允许prefix-cache hit落下的最细合法token boundary。

同时记录：

[
B=
\text{physical KV block size}
]

在某些backend/version中：

[
G=B
]

但方法定义不要求二者恒等。

如果backend存在更细的prefix matching unit，或不同KV cache group具有不同physical block layout，则以**冻结backend公开/可验证的effective match semantics**确定(G)，并记录对应configuration。

如果backend无法暴露独立match granularity，则：

```text
G = documented cacheable-prefix boundary of the frozen backend/version
```

不得根据运行结果反向选择(G)。

------

## 16.3 Granularity-Aligned Prefix Path与Shared Prefix

设：

[
T(r)=
(t_0,t_1,\ldots,t_{n-1})
]

对于request (r)，定义以(G)为边界的exact prefix fingerprints：

[
P_G(r)=
(p_G^{(1)}(r),p_G^{(2)}(r),\ldots)
]

其中：

[
p_G^{(k)}(r)
=
H(
t_0{:}t_{kG-1},
c_r
)
]

(c_r)包含影响backend cache identity的固定上下文，例如：

```text
model/tokenizer identity
chat template
prompt/schema identity
cache salt / tenant identity if applicable
other backend-declared cache-identity fields
```

fingerprint只用于request-side exact-prefix equivalence与调度，不修改backend内部hash、KV allocator、storage或eviction。

两个request之间定义：

[
LCP_G(r_i,r_j)
=
G\cdot
\max\left\{
k\ge0
\mid
t_0{:}t_{kG-1}(r_i)
=
t_0{:}t_{kG-1}(r_j)
\right\}
]

即二者在backend合法prefix-match granularity下的最长exact shared prefix，单位为tokens。

prefix affinity因此不是：

```text
same family / different family
```

而是长度量：

```text
r1 ↔ r2 : 4096 reusable-prefix tokens
r1 ↔ r3 : 2048 reusable-prefix tokens
r1 ↔ r4 : 0 reusable-prefix tokens
```

离散prefix family可以作为工程索引，但正式方法与指标以：

[
LCP_G
]

为准。

需要注意：

> **LCP_G描述request-side matching opportunity，不代表对应KV state此刻一定resident，也不代表backend一定已经materialize了可命中的cache state。**

实际命中仍必须由backend measurement确认。

------

## 16.4 Cache Residency与Prefix Equivalence分离

必须区分：

```text
exact reusable prefix exists
```

与：

```text
that prefix is currently resident in backend KV cache
```

前者可以由rendered/tokenized request确定；后者取决于backend执行历史、KV容量、eviction和并发状态。

因此MemBind不得仅凭prefix fingerprint宣称：

```text
cache hit guaranteed
```

准确表述是：

> **Prefix affinity identifies legal reuse opportunities; actual cache hits are measured from the backend.**

如果backend不提供可用于在线调度的安全cache-residency接口，第一版MemBind不得读取或修改vLLM内部cache状态作为必要机制。

backend暴露的：

```text
cached_tokens
APC hit statistics
prefill timing
```

优先用于measurement，而不是作为正确性所依赖的调度oracle。

------

# 17. Cache-Affine Compile Policy

## 17.1 Admission Precedence

每次出现可用LLM permit时，调度优先级严格为：

```text
1. ready publication-frontier STATE_BOUND LLM request
2. legal EVIDENCE_BOUND Compile request
3. no legal work → idle
```

即：

[
SemanticPriority
>
CacheAffinity
]

Cache policy永远不能使speculative Compile越过ready frontier Bind。

------

## 17.2 Prefix-Affinity Score

当frontier request已经获得所需permit，且仍有剩余capacity时，对合法Compile ready set：

[
\mathcal C_{ready}\subseteq\mathcal S_{legal}
]

进行cache-affine排序。

第一版定义一个**request-side、backend-independent**的affinity score，不读取vLLM私有KV residency状态。

设：

[
\mathcal Q_{done}(t)
]

为当前run中在时刻(t)之前已经完成prefill、因此已经实际产生过对应KV prefix的LLM requests。

对候选request (r)，定义：

[
Affinity(r,t)
=
\max_{q\in\mathcal Q_{done}(t)}
LCP_G(r,q)
]

若多个provider提供相同最长prefix，则优先选择prefill completion time最近的provider，作为更强的residency surrogate；但这仍然不等价于backend确认resident。

同时定义当前ready pool中的future cohort support：

[
CohortGain(r)
=
\sum_{q\in\mathcal C_{ready},q\ne r}
LCP_G(r,q)
]

默认采用lexicographic ordering：

[
\boxed{
Affinity(r,t)
\downarrow,
\quad
ProviderRecency(r,t)
\downarrow,
\quad
CohortGain(r)
\downarrow,
\quad
source\ sequence
\uparrow
}
]

含义是：

```text
优先复用已经实际计算完成的最长prefix
→ 同长度时偏向更近期的provider
→ 再偏向能够形成更大future prefix cohort的request
→ 最后用source sequence/FIFO稳定打破平局
```

如果当前不存在任何completed provider，则：

```text
Affinity = 0
```

此时只用CohortGain组织未来的temporal locality，但**不假设同批首次出现的共享prefix能够立即命中APC**。

不要求第一版实现复杂的learned cost model，也不claim该heuristic对任意backend/workload全局最优。

------

## 17.3 Prefix-Homogeneous Admission

例如ready set包含：

```text
NodeExtract(E1)
NodeExtract(E2)
EdgeExtract(E3)
NodeExtract(E4)
```

若真实tokenization得到：

```text
LCP_G(NodeExtract(E1), NodeExtract(E2)) = 4096
LCP_G(NodeExtract(E1), NodeExtract(E4)) = 4096
LCP_G(NodeExtract(E1), EdgeExtract(E3)) = 0
```

并且已有一个recent completed NodeExtract request提供相同4096-token prefix，则在不影响frontier request的前提下，优先连续admit该prefix-compatible cohort。

如果这些request共享的prefix此前从未完成计算，则MemBind只能把它们组织得更相邻，以改善后续temporal locality；第一版**不假设同时admit的首次请求之间可以直接共享尚未materialize的KV**。

因此：

```text
prefix-homogeneous ordering
≠
warm-then-fanout barrier
```

前者保持work-conserving；后者如果要求leader先materialize prefix再释放followers，会引入额外等待，仍属于§18的optional backend policy。

MemBind也不会因为operator name相同就默认共享prefix，更不会为了凑齐cohort主动等待尚未ready的request。

------

## 17.4 为什么该Policy可能提高APC Hit

MemBind不改变prompt本身，因此不改变由workload和prompt结构决定的intrinsic prefix-sharing opportunity。

MemBind能够改变的是：

```text
legal request ordering
reuse distance
prefix-homogeneous temporal locality
KV working-set pressure
```

目标链路是：

```text
State-Cut exposes legal ready requests
        ↓
cache-affine ordering shortens reuse distance
        ↓
reusable KV blocks are less likely to be displaced before reuse
        ↓
realized cached prefix tokens may increase
        ↓
recomputed prefill work may decrease
```

因此正式claim必须写成：

> **MemBind may improve realized prefix reuse within legal semantic slack.**

不得写成：

> **MemBind creates new reusable prefixes.**

------

## 17.5 与W和K_LLM的关系

(W)和(K_{LLM})共同影响scheduler可观察到的legal ready-set size与KV working set。

```text
W too small
→ insufficient reorder freedom

W too large
→ excessive speculative working set / request burst

K_LLM too small
→ underutilized backend

K_LLM too large
→ larger heterogeneous working set / possible cache pressure
```

因此二者不是“越大越好”。

但(W)和(K_{LLM})仍然只是runtime knobs，不属于cache机制本身的novelty。

正式评测中必须：

```text
freeze W and K_LLM before main comparison
use the same values for MemBind-FIFO and MemBind
不得根据最终APC结果post-hoc选择最优配置
```

------

## 17.6 Scope

MemBind：

- 不修改prompt；
- 不重排prompt字段；
- 不修改KV blocks；
- 不重新实现APC；
- 不修改backend eviction policy；
- 不保证prefix一定resident；
- 不保证APC hit rate一定上升；
- 只在legal speculative requests之间改变admission/order。

------

# 18. Warm-Then-Fanout

Warm-Then-Fanout不是最终方法必需组件。

其形式为：

```text
one leader request
    ↓
prefix materialization
    ↓
follower requests
```

由于它存在：

[
SavedPrefill
\leftrightarrow
FollowerWaiting
]

tradeoff，因此第一版定义为：

```text
OPTIONAL_BACKEND_POLICY
```

不得作为MemBind核心novelty。

如果formal method freeze时不启用，则主实验不得根据结果临时加入。

------

# 19. Graphiti v0.29.x Adapter

Graphiti作为当前primary backend。

目标mapping如下。

## 19.1 Candidate Compile Region

```text
Node Extraction
       ↓
Edge Extraction
```

即：

[
C_i=
NodeExtract_i
+
EdgeExtract_i
]

前提：

### NodeExtract

必须仅依赖：

```text
current episode
Evidence Snapshot
fixed entity schema/config
```

### EdgeExtract

必须仅依赖：

```text
current episode
raw extracted nodes
Evidence Snapshot
fixed edge schema/config
```

并经过一次deterministic qualification确认：

```text
zero mutable persistent-state read
```

如果某Graphiti版本中EdgeExtract发生mutable-state read：

```text
EdgeExtract
```

必须移动到Bind，不允许为了扩大parallel region修改Graphiti算法语义。

------

## 19.2 Bind Region

目标：

```text
NodeResolve
    ↓
ResolveEdgePointers
    ↓
EdgeResolve
    ↓
Attribute / Summary
    ↓
Temporal Invalidation
    ↓
Persistence
```

这些操作均绑定：

[
M_{i-1}
]

并且同一stream内严格source ordered。

------

# 20. Correctness Contract

MemBind的核心correctness目标定义为：

> **Source-Order Serializability with Evidence Equivalence。**

------

## 20.1 Arrival Safety

必须满足：

[
Compile_i
\text{ starts only if }
t\ge Arrival_i
]

不得观察unarrived future evidence。

------

## 20.2 Evidence Equivalence

Compile(_i)只能读取serial reference在相同source stage合法获得的：

[
(E_i,S_i)
]

不得读取future mutable state。

------

## 20.3 State-Version Binding

Bind(_i)必须观察：

[
M_{i-1}
]

不能观察：

```text
M_{i-2}
stale state
future state
partially committed successor state
```

------

## 20.4 Source-Ordered State Effects

同一memory stream内：

[
Write_i\prec Write_{i+1}
]

------

## 20.5 Ordered Publication与Query Visibility Scope

同一memory stream内：

[
Publish_i\prec Publish_{i+1}
]

且：

[
Bind_{i+1}
\text{ starts only after }
Publish_i
]

因此对**后续state-bound construction**而言，MemBind只允许完整发布后的(M_i)成为下一update的合法前驱state。

但必须区分：

```text
construction-to-construction version ordering
```

与：

```text
concurrent retrieval cannot observe partial Bind_i writes
```

后者需要额外的`PublishedReadContract`。

只有当target workload / serial reference要求per-update query-visible atomicity时，adapter才必须提供以下任一机制：

```text
backend transaction / snapshot isolation
version-filtered reads
stream-level read gate
equivalent published-version read mechanism
```

此时：

```text
Bind_i in progress
→ retrieval for the same stream cannot observe unpublished partial M_i

Publish_i
→ M_i becomes query-visible
```

如果底层Graphiti/Neo4j原生不提供这种atomic visibility，且target workload也没有在Bind内部并发查询，则MemBind**不得为了强化claim而静默改变原生architecture semantics**。

在没有`PublishedReadContract`时，正式claim收窄为：

> **construction-to-construction source-order serializability；retrieval/QA只在published checkpoints上评测。**

MemBind不额外claim底层backend原本不存在的transaction-level atomicity。

------

## 20.6 Exactly-Once Publication

每个source sequence必须满足：

```text
publish_count(i) = 1
```

不得：

```text
duplicate publish
lost publish
out-of-order publish
```

------

## 20.7 State-Cut Serializability Theorem

在deterministic / captured-oracle execution中，先定义冻结的canonical state projection：

[
Canonical(M)
]

`Canonical(M)`必须满足：

```text
include:
all persistent state that can affect subsequent construction,
retrieval, temporal validity, identity resolution, or publication semantics

exclude:
only fields pre-declared by the adapter as VOLATILE_NON_SEMANTIC
and proven not to affect any subsequent semantic work
```

例如runtime-generated identifiers、driver metadata或serialization artifacts只有在**formal run之前**被adapter明确声明且证明为non-semantic时才允许排除；不得在看到parity diff之后临时增加排除字段。

canonicalization规则必须绑定：

```text
backend / adapter version
schema version
projection version
```

并在formal evaluation开始前freeze。

定义canonical published-state equivalence：

[
M_i^{MemBind}\equiv M_i^{Serial}
\iff
Canonical(M_i^{MemBind})
=
Canonical(M_i^{Serial})
]

若对任意update (i)满足：

1. `Compile_i`只读取serial reference在相同source stage可合法观察的(E_i,S_i)；
2. `Compile_i`不读写persistent mutable state，且不存在undeclared external state effect；
3. 相同rendered/oracle inputs产生相同Prepared Artifact：
   [
   A_i^{MemBind}=A_i^{Serial}
   ]
4. `Bind_i`仅在`Publish_{i-1}`完成后运行，并观察精确(M_{i-1})；
5. `Bind_i/Commit_i`在给定相同(A_i,M_{i-1})时具有相同canonical state transition；
6. 同一stream满足single-writer、publish-completeness与source-ordered state effects；

则：

[
\boxed{
\forall i,\quad
M_i^{MemBind}
\equiv
M_i^{Serial}
}
]

### Proof Sketch

对(i)归纳。

Base case：

[
M_{-1}^{MemBind}
\equiv
M_{-1}^{Serial}
]

由相同初始state成立。

Induction hypothesis：

[
M_{i-1}^{MemBind}
\equiv
M_{i-1}^{Serial}
]

由于`Compile_i`只依赖相同合法evidence/oracle inputs且无persistent-state effect：

[
A_i^{MemBind}
=
A_i^{Serial}
]

`Bind_i`又绑定相同前驱state：

[
M_{i-1}^{MemBind}
\equiv
M_{i-1}^{Serial}
]

因此相同canonical state transition得到：

[
M_i^{MemBind}
\equiv
M_i^{Serial}
]

归纳成立。

该theorem证明的是：

> **在captured/deterministic semantic work固定时，State-Cut transformation与out-of-order evidence computation不会改变published state sequence。**

它不把live LLM sampling/numerical nondeterminism错误解释成runtime correctness violation。

------

## 20.8 Deterministic Qualification

在deterministic/replay控制环境下，正式qualification要求：

```text
same rendered inputs
same captured/oracle outputs
same semantic work contract
canonical state parity at every published checkpoint
zero hidden fallback
zero State-Cut certification failure
```

用于验证State-Cut transformation与上述theorem premises实际成立。

------

## 20.9 Live-LLM环境

真实LLM执行存在sampling、floating-point和serving nondeterminism时，不要求：

```text
bitwise graph equality
```

正式correctness claim是：

```text
same evidence contract
same predecessor-state contract
same state-effect order
same publication order
```

同时通过retrieval/QA quality指标确认没有实际semantic degradation。

------

# 21. Direct Violation定义

Direct hard violation必须来自runtime event witness，而不是通过final graph差异推断。

至少包括：

```text
Compile-before-arrival
STATE_CUT_CERTIFICATION_FAILURE during formal run
mutable-state read/write inside certified Compile
Bind_i before Publish_{i-1}
Bind_i against wrong predecessor version
Publish_i before Publish_{i-1}
state-mutating async work continues after Publish_i
duplicate publish
lost publish
interleaved ordered state effects
query observes unpublished partial state, when PublishedReadContract is enabled
```

最终graph difference：

```text
≠
direct state-order violation
```

除非存在上述直接trace证据。

------

# 22. Execution Algorithm

高层逻辑：

```python
for each stream h:
    frontier[h] = 0
    ROB[h] = {}

while unfinished_updates_exist():

    # --------------------------------------------------
    # 1. Admit newly arrived updates
    # --------------------------------------------------
    for update in newly_arrived_updates():
        if within_speculation_window(
            update,
            frontier[update.stream],
            W,
        ):
            mark_compile_ready(update)

    # --------------------------------------------------
    # 2. Determine frontier state-bound work
    # --------------------------------------------------
    bind_ready = []

    for h in streams:
        f = frontier[h]

        if (
            ROB[h][f].prepared
            and predecessor_published(h, f)
        ):
            bind_ready += ready_state_bound_llm_ops(h, f)

    # --------------------------------------------------
    # 3. Highest semantic priority:
    #    frontier state-bound LLM requests
    # --------------------------------------------------
    while available_llm_permits(K_LLM) and bind_ready:
        req = choose_frontier_request(bind_ready)
        dispatch(req)

    # --------------------------------------------------
    # 4. Remaining capacity is work-conserving:
    #    speculative evidence-bound Compile
    # --------------------------------------------------
    if available_llm_permits(K_LLM):

        candidates = ready_compile_ops(
            arrived_only=True,
            within_window=True,
        )

        candidates = legal_semantic_ready_set(candidates)

        # Optional secondary policy
        candidates = order_cache_affine(candidates)

        dispatch_up_to_available_capacity(candidates)

    # --------------------------------------------------
    # 5. Completed Compile enters Prepared ROB
    # --------------------------------------------------
    for artifact in completed_compile_artifacts():
        ROB[artifact.stream][artifact.seq].prepared = True
        ROB[artifact.stream][artifact.seq].artifact = artifact

    # --------------------------------------------------
    # 6. Complete frontier Bind and publish
    # --------------------------------------------------
    for h in streams:
        f = frontier[h]

        if bind_completed(h, f):
            publish(f)

            assert publish_count(f) == 1

            frontier[h] += 1

    wait_for_next_event_if_needed()
```

实际实现应采用event-driven coordinator，不要求busy loop。

------

# 23. Baseline / Execution Policies

所有方法：

```text
same memory architecture
same source workload
same prompt/schema
same model
same embedding
same Neo4j
same vLLM
same APC
same LLM-serving-backend scheduler configuration
same arrival trace
```

只改变execution policy。

| Method              | Execution                                                                 |
| ------------------- | ------------------------------------------------------------------------- |
| U0 Native Serial    | serial reference construction                                             |
| A0 Async-Serial     | background FIFO，但state construction仍串行                               |
| P* Naive Parallel   | concurrent whole-update execution                                         |
| MemBind-Barrier     | State-Cut + Prepared ROB + hard Bind barrier + FIFO Compile                |
| MemBind-FIFO        | State-Cut + version-bound frontier + work-conserving admission + FIFO Compile |
| MemBind             | MemBind-FIFO + cache-affine Compile ordering                              |

------

# 24. Baseline角色

## U0

回答：

> serial-reference architecture的真实construction成本是多少？

------

## A0

回答：

> 仅仅把construction搬到background是否已经足够？

A0不会恢复update内部隐藏parallelism。

------

## P*

回答：

> 如果直接parallelize整个stateful update，能够暴露多少粗粒度parallel capacity，以及会产生什么state-order风险？

P*不是correct implementation candidate，而是：

```text
semantics-unconstrained parallel reference
```

P*不要求每一次运行都产生violation。

正式报告：

```text
observed direct violation frequency/count
```

------

## MemBind-Barrier

回答：

> 仅有State-Cut与Prepared Speculation，但在frontier Bind期间使用旧式hard barrier时，能够获得多少收益？

其语义与MemBind-FIFO相同，但admission policy为：

```text
frontier Bind runnable / binding
→ stop admitting new speculative Compile LLM requests
→ wait until frontier Publish completes
```

已经admitted的Compile request不要求抢占。

MemBind-Barrier只作为**代表性mechanism ablation**，用于隔离：

```text
State-Cut / early evidence computation
```

与：

```text
frontier-first work-conserving admission
```

的增量贡献。

它不需要扩展成完整baseline family，也不要求重复全部retrieval/QA evaluation；只要deterministic semantic parity已经通过，正式性能ablation在代表性workload上报告即可。

------

## MemBind-FIFO

回答：

> 在相同State-Cut与version-binding semantics下，frontier-first work-conserving admission相对hard Bind barrier贡献多少？

它使用FIFO Compile，不进行prefix-aware reordering。

------

## MemBind

回答：

> 在相同state semantics下，利用合法semantic slack进行backend-aware ordering还能增加多少收益？

------

# 25. Workload Contract

## 25.1 Stateful Stream

主workload必须保留：

```text
episode/update sequence
timestamps
session provenance
source sequence
```

以构成真实persistent-memory evolution。

------

## 25.2 Arrival Trace

所有execution policy必须使用完全相同的：

[
ArrivalTrace
]

不得出现：

```text
U0:
arrival = service start

A0/P/MemBind:
arrival = external submission
```

这种不公平语义。

定义：

[
Freshness_i=
Publish_i-Arrival_i
]

如果serial U0产生backlog，则该backlog本身就是U0的科学结果。

------

## 25.3 Burst Workload

可额外设置：

```text
arrival_interval = 0
```

模拟saturated construction workload，用于研究：

```text
maximum safe construction throughput
```

此时所有episode在逻辑上同时arrive，因此跨episode speculation仍然合法。

------

## 25.4 Arrival-Rate Stress Regimes

除primary fixed arrival trace外，freshness evaluation至少包含三个预先冻结的offered-load regime：

```text
light load
near saturation
overload / burst
```

可先用U0 qualification run估计serial-reference service capacity：

[
\mu_{U0}
]

再在formal protocol freeze前固定若干：

[
\lambda/\mu_{U0}
]

例如：

```text
light          ≈ 0.5
near-saturation≈ 0.9–1.0
overload       > 1.0
```

或使用等价的确定性arrival intervals。

该sweep只改变arrival timing，不改变：

```text
episode order
source evidence
prompt/schema
semantic work
backend configuration
```

主要报告：

```text
goodput
P50/P95/P99 freshness
queue delay
maximum backlog
drain time
```

目的不是寻找最有利负载，而是验证：

> **MemBind的收益是否随着offered load逼近stateful construction capacity而系统性出现，而不是只在单一burst trace上成立。**

具体load points必须在主实验前freeze，不得根据结果post-hoc调整。

------

# 26. Primary Metrics

## 26.1 Performance

```text
construction makespan
successful construction goodput
episode completion throughput
LLM request throughput
```

------

## 26.2 Freshness

```text
median freshness
P95 freshness
P99 freshness
maximum backlog
drain time
```

------

## 26.3 Runtime Utilization

```text
LLM inflight
LLM busy time
DB time
embedding time
frontier waiting time
speculative ready time
prepared ROB occupancy
```

------

## 26.4 Exposed Safe Work Fraction

为了量化State-Cut实际暴露了多少可提前执行的semantic work，必须报告：

[
\rho_C^{req}
=
\frac{N_{LLM}(C)}
{N_{LLM}(C)+N_{LLM}(B)}
]

[
\rho_C^{prompt}
=
\frac{PromptTokens(C)}
{PromptTokens(C)+PromptTokens(B)}
]

以及在能够稳定获得per-request service instrumentation时：

[
\rho_C^{service}
=
\frac{\sum ServiceTime(C)}
{\sum ServiceTime(C)+\sum ServiceTime(B)}
]

这里`ServiceTime`使用同一U0/captured replay下的per-operator service time求和，而不是并行run中的overlapped wall time。

可额外报告：

```text
Compile-region LLM call count
Compile-region prompt/prefill tokens
Bind-region LLM call count
Bind-region prompt/prefill tokens
```

这些指标回答：

> **Graphiti一次stateful update中，究竟有多少真实LLM work位于persistent-state dependency boundary之前？**

它们是method opportunity / mechanism metrics，不是Amdahl-style speedup upper bound，也不保证相同比例必然转化为end-to-end speedup。

不同memory architecture可以具有不同：

[
\rho_C
]

这也是MemBind generality claim需要报告的关键解释变量。

------

# 27. Correctness Metrics

必须报告：

```text
direct hard violation count
direct hard violation rate
out-of-order publish
wrong-version bind
duplicate publish
lost publish
compile-before-arrival
evidence-fence violation
```

并记录：

```text
zero hidden fallback
exact source coverage
```

------

# 28. Retrieval / Quality Metrics

保留：

```text
Recall@10
```

同时增加：

```text
Recall@1
Recall@3
Recall@5
Recall@10
MRR
nDCG@10
```

直接复用已有ranked retrieval结果，不新增retrieval request。

如果已有temporal evidence能够无歧义计算，可进一步报告：

```text
stale evidence rate
conflicting evidence rate
latest-valid evidence coverage
```

如果无法可靠定义：

```text
NOT_AVAILABLE
```

不得临时构造规则。

最终QA报告：

```text
Reader/Judge accuracy
```

用于验证quality non-degradation。

------

# 29. Cache / Prefill Metrics

Cache机制不能只看backend aggregate APC hit rate。

正式报告必须区分三个层次：

```text
1. Structural Prefix Potential
   request本身是否存在exact granularity-aligned shared prefix

2. Schedule-Eligible Prefix Reuse
   在给定合法dispatch order下，之前执行过的request是否已经产生可复用prefix

3. Realized Backend Cache Reuse
   backend在实际执行时有多少prefix tokens真正命中resident KV cache
```

这样可以区分：

```text
prompt/workload本身缺少prefix sharing
vs
scheduler没有把共享prefix组织到一起
vs
backend因capacity/eviction没有保住可复用KV
```

------

## 29.1 Structural Prefix Potential

对完全相同的rendered/tokenized request multiset，离线计算granularity-aligned shared-prefix structure。

建议至少报告：

```text
requests with non-zero shared prefix
mean / P50 / P95 granularity-aligned shared prefix tokens
prefix cohort size distribution
structurally reusable prefix tokens
```

该指标用于证明：

> MemBind没有通过修改prompt创造新的prefix sharing opportunity。

对于correctness-preserving policies，如果deterministic/captured qualification确认其具有same semantic work和same rendered/tokenized request multiset，则U0、A0、MemBind-Barrier、MemBind-FIFO、MemBind的Structural Prefix Potential应一致；若不一致，必须解释request生成是否发生semantic work-volume变化。

`P*`不进入该cache因果等价集合。P*是semantics-unconstrained parallel reference；一旦wrong-version state改变state-dependent prompt或semantic work，其rendered request multiset本来就可能不同。P*只用于unsafe parallel performance reference与direct-violation characterization，不用于证明cache-affine scheduling的因果收益。

------

## 29.2 Schedule-Eligible Reusable Prefix Tokens

给定实际dispatch/prefill-completion trace，对每个request (r_i)定义其dispatch时刻：

[
d_i=DispatchTime(r_i)
]

只有在(d_i)之前已经完成prefill的request，才可以作为“不考虑后续eviction时已经materialize”的prefix provider。

定义：

[
\mathcal Q_i
=
\{q\mid PrefillCompleteTime(q)<d_i\}
]

则：

[
EligiblePrefixTokens(r_i)
=
\max_{q\in\mathcal Q_i}LCP_G(r_i,q)
]

如果(\mathcal Q_i)为空或没有共享合法prefix-match unit，则该值为0。

总量：

[
ScheduleEligibleReusablePrefixTokens
=
\sum_i EligiblePrefixTokens(r_i)
]

该定义明确排除了：

```text
与当前request同时开始、但尚未materialize KV的peer request
```

因此它反映在给定execution trace下，**理论上已经产生过、只因residency/eviction等backend因素可能无法命中的prefix work**。

------

## 29.3 Realized Cached Prefix Tokens

从backend实际measurement获得：

[
CachedPrefixTokens
]

并计算：

[
PrefixReuseEfficiency
=
\frac{
CachedPrefixTokens
}{
ScheduleEligibleReusablePrefixTokens
}
]

仅在分母大于0时报告。

这里：

```text
ScheduleEligibleReusablePrefixTokens
= dispatch前已经由completed prefill materialize、理论可复用的backend-match-granularity prefix

CachedPrefixTokens
= 实际执行时backend确认命中的prefix tokens
```

在满足以下条件时：

```text
same cache accounting scope
no external traffic
controlled initial cache state
only current-run requests contribute cache state
```

应有：

[
0\le PrefixReuseEfficiency\le1
]

如果backend的cached-token统计包含该measurement scope之外的cache来源，或统计粒度与上述backend-granularity定义不一致，则必须记录为：

```text
NOT_COMPARABLE
```

而不是强行解释该比值。

------

## 29.4 Prefill Work

应优先报告：

```text
prompt tokens
cached prefix tokens
uncached / recomputed prefill tokens
avoided prefill tokens
prefill latency
TTFT
LLM request throughput
```

如果backend能够可靠给出cached prompt tokens，则：

[
RecomputedPrefillTokens
=
PromptTokens-CachedPrefixTokens
]

以及：

[
APCHitRate_{token}
=
\frac{CachedPrefixTokens}{PromptTokens}
]

APC aggregate hit rate保留，但只作为**realized backend metric**，不能单独证明cache-affine scheduling有效。

------

## 29.5 Cache-Affinity Mechanism Test

正式cache mechanism comparison固定为：

```text
MemBind-FIFO
vs
MemBind
```

两者必须保持：

```text
same State-Cut
same arrival trace
same W
same K_LLM
same backend/APC configuration
same semantic work volume
same prompt/schema/model
```

只有cache-affine Compile ordering不同。

支持secondary mechanism的理想证据链是：

```text
Structural Prefix Potential      ≈ unchanged
Schedule-Eligible Reuse          ↑ or better clustered
Realized Cached Prefix Tokens    ↑
PrefixReuseEfficiency            ↑
Recomputed Prefill Tokens        ↓
Prefill latency / TTFT            ↓
```

最终makespan是否继续下降取决于prefill在总critical path中的占比，因此：

```text
APC hit ↑
```

不要求必然推出：

```text
end-to-end speedup同幅度↑
```

若只观察到aggregate APC hit变化，而没有cached-token / recomputed-prefill / latency证据，则不得claim完整因果链。

------

# 30. Observed Parallelism Recovery

为了描述MemBind从unsafe whole-update parallel reference中实际恢复了多少已观察到的性能空间，可报告：

[
ObservedRecovery=
\frac{
T_{U0}-T_{MemBind}
}{
T_{U0}-T_{P^*}
}
]

其中：

- (T_{U0})：serial reference makespan；
- (T_{P^*})：whole-update parallel makespan；
- (T_{MemBind})：MemBind makespan。

该指标只在：

```text
same semantic work volume
same backend
same arrival trace
T_P* < T_U0
```

成立时使用。

若(P*)没有形成正的observed performance headroom，则：

```text
ObservedRecovery = NOT_APPLICABLE
```

不得把负分母或接近0的分母解释成“恢复率”。

它不是correctness证明，也不是理论parallelism upper bound。

P*自身仍受：

```text
GPU saturation
batching
APC/cache state
queueing
backend scheduling
```

影响，因此该指标只作为summary statistic。

它只用于解释：

> MemBind在保持state contract的情况下回收了多少P*在该固定backend/workload上**实际观察到**的性能空间。

------

# 31. Contribution 1

## State-Cut Compilation for Stateful Memory Construction

MemBind把一个monolithic stateful memory update：

[
U_i(E_i,S_i,M_{i-1})
]

转换为：

[
A_i=C_i(E_i,S_i)
]

以及：

[
B_i(A_i,M_{i-1})
]

其中：

[
C_i
]

是最大adapter-certified evidence-bound subgraph，

而：

[
B_i
]

是精确绑定前驱memory version的stateful region。

核心贡献：

> **把serialization boundary从whole-update boundary下推到真实persistent-state access boundary。**

核心公式：

[
\boxed{
\text{Early Evidence Computation}
+
\text{Late Version Binding}
}
]

------

# 32. Contribution 2

## Frontier-Aware Version-Bound Speculative Runtime

MemBind允许多个已到达future update的evidence-bound work：

```text
compile out of order
```

并进入Prepared Reorder Buffer。

但只有publication frontier允许执行state-bound region。

frontier stateful request获得最高admission priority，而剩余resource继续work-conserving地执行speculative Compile。

核心：

[
\boxed{
\text{Bounded Speculation}
+
\text{Prepared ROB}
+
\text{Frontier Priority}
+
\text{Version-Bound Bind}
+
\text{Ordered Publication}
}
]

其目标同时优化：

[
Throughput
]

与：

[
Freshness
]

同时保持：

[
SourceOrderSerializability
]

其中：

```text
Frontier-first priority
```

是把version/publication semantic constraint落实为work-conserving resource admission的**runtime mechanism**，不单独claim为新的通用LLM scheduling primitive。

概念贡献是：

[
\boxed{
\text{Version-Bound Speculative Execution}
}
]

即：

> **future evidence work可以提前执行，但mutable-state work必须由publication frontier绑定精确前驱version。**

------

# 33. Secondary Mechanism

## Semantic-Constrained Cache Affinity

Cache-aware execution不是独立核心贡献。

MemBind先通过State-Cut与frontier semantics得到：

[
\mathcal S_{\text{legal}}
]

然后仅在不会延迟frontier state advancement的合法Compile候选内部，根据真实rendered/tokenized request的backend prefix-match-granularity-aligned affinity调整admission order：

[
\mathcal S_{\text{legal}}
\xrightarrow{backend\text{-}granularity\ prefix\ affinity}
\pi
]

目标不是创造新的prefix，而是：

[
\boxed{
Intrinsic\ Prefix\ Sharing
\text{ fixed}
\quad;
\quad
Realized\ Prefix\ Reuse
\text{ improved if possible}
}
]

实际KV复用仍由现成backend负责：

```text
vLLM APC
batching
chunked prefill
backend KV allocation / eviction
```

因此：

```text
MemBind does not invent prefix caching.
MemBind exposes legal execution slack.
MemBind uses that slack to improve temporal prefix locality when possible.
```

正式secondary claim必须由：

```text
MemBind-FIFO
vs
MemBind
```

在相同backend配置下验证，并优先依赖：

```text
cached prefix tokens
schedule-eligible reusable prefix tokens
PrefixReuseEfficiency
recomputed prefill tokens
prefill latency / TTFT
```

而不是只依赖aggregate APC hit rate。

如果正式实验未观察到明显cache benefit：

```text
Contribution 1 / Contribution 2仍然成立；
cache-affinity作为secondary/negative result报告。
```

不得把“开启APC”本身claim为MemBind贡献，也不得把backend已有cache收益计入MemBind独有贡献。

------

# 34. Backend Contract

U0、A0、P*、MemBind-Barrier、MemBind-FIFO和MemBind必须共享：

```text
same GPU
same model
same model weights
same vLLM version
same max_model_len
same LLM-serving-backend scheduler configuration
same APC configuration
same chunked-prefill configuration
same GPU memory budget
same embedding model
same Neo4j configuration
same backend prefix-match configuration
```

每个memory stream还必须满足：

```text
single-writer ownership
namespace isolation
no external state mutation
all declared Bind state effects joined before Publish
no hidden post-Publish state mutation
```

若target workload要求query-visible atomicity，则还必须冻结并共享同一：

```text
PublishedReadContract
```

U0与MemBind不得使用不同的read visibility semantics。

正式cache mechanism实验必须使用dedicated construction vLLM instance，并为MemBind-FIFO与MemBind采用完全相同的cache initial-state protocol：

```text
preferred: cold / empty controlled cache state before each measured run
or: identical documented warm-up sequence excluded from measurement
```

measurement期间不得混入不可控外部traffic。若无法控制初始cache state，则APC结果只能作为observational metric，不能用于强因果claim。

------

# 35. 明确不属于核心贡献的内容

以下均为implementation/configuration knobs：

```text
W
K_LLM
compile worker count
APC
chunked prefill
continuous batching
Neo4j tuning
embedding tuning
HTTP connection pool
GPU memory utilization
```

它们用于：

```text
stability
fairness
resource control
```

不单独claim novelty。

------

# 36. Graphiti不是方法定义

正式methodology不得写成：

```text
NodeExtract parallel
EdgeExtract parallel
Resolve serial
```

正确抽象是：

```text
Certified Evidence-Bound Region
+
Version-Bound Stateful Region
```

Graphiti中的：

```text
NodeExtract / EdgeExtract
```

只是Graphiti Adapter的一组具体operator mapping。

因此代码结构应保持：

```text
Graphiti Adapter
        ↓
Construction Contract
        ↓
MemBind Runtime
```

而不是：

```text
if function == "extract_nodes":
    parallelize()
```

------

# 37. Generality Claim

第一版论文可以claim：

> MemBind提供一个architecture-preserving runtime abstraction，并在Graphiti这一真实stateful temporal graph memory system上实现和验证。

不得claim：

> 所有Agent Memory architecture都天然具有相同parallelizable fraction。

不同architecture可能具有不同：

[
|C_i|/|U_i|
]

MemBind只要求adapter能够：

```text
certify合法state cut
enforce exact predecessor-state assumptions
define its published-read scope
```

不同backend不要求具有相同query-visible atomicity；formal claim必须服从各自adapter能够实际保证的contract。

未来backend可通过新adapter接入。

第二memory backend属于external-validity增强，不阻塞第一版核心实现。

------

# 38. Formal Claims

## Claim A — Hidden Safe Parallelism

相对于U0：

```text
State-Cut暴露显著的evidence-bound construction work
```

并能够与其他arrived updates重叠执行。

正式报告：

```text
ρ_C^req
ρ_C^prompt
ρ_C^service, when available
```

用于量化被恢复的safe work opportunity，而不是只凭最终speedup反推parallelism。

------

## Claim B — Performance / Freshness

相对于U0/A0：

```text
successful construction goodput ↑
makespan ↓
P95/P99 freshness ↓
backlog/drain ↓
```

------

## Claim C — Correctness

```text
zero direct hard state-order violations
zero State-Cut certification failure
zero wrong-version bind
zero out-of-order publication
zero hidden state mutation after Publish
zero future-evidence leakage
exactly-once publication
canonical published-state parity in deterministic/captured replay
retrieval/QA non-degraded in live evaluation
```

------

## Claim D — Observed Parallelism Recovery

相对于P*：

```text
MemBind保留serial-reference state contract
```

同时恢复P*所展示并行收益中的显著部分。

理想结果形态：

```text
                 Performance      Direct violation

U0               baseline             0
A0               ~baseline            0
P*               fastest              >0 on some traces
MemBind           close to P*          0
```

P*不要求每次都产生violation。

------

## Claim E — Backend Locality

仅作为secondary claim。

在相同APC/backend配置下比较：

```text
MemBind-FIFO
vs
MemBind
```

若同时观察到：

```text
Structural Prefix Potential ≈ unchanged
realized cached prefix tokens ↑
PrefixReuseEfficiency ↑
recomputed prefill tokens ↓
```

并在适用时伴随：

```text
prefill latency / TTFT ↓
```

则说明：

> **State-Cut暴露出的legal semantic slack能够被cache-affine admission进一步转换为realized backend prefix locality。**

不要求end-to-end speedup与APC hit提升成比例，因为decode、DB、embedding、queueing和stateful critical path仍可能占主导。

若cache收益不明显：

```text
不影响Contribution 1 / Contribution 2与核心correctness claim。
```

------

# 39. 最小Ablation

不再扩展大量M1/M2/M3变体。

只增加一个必要的mechanism ablation：

```text
MemBind-Barrier
```

形成：

```text
U0
↓
MemBind-Barrier
↓
MemBind-FIFO
↓
MemBind
```

比较：

[
U0
\rightarrow
MemBind\text{-}Barrier
]

回答：

> **State-Cut + early evidence computation / Prepared Speculation本身能暴露多少安全并行空间？**

比较：

[
MemBind\text{-}Barrier
\rightarrow
MemBind\text{-}FIFO
]

回答：

> **在相同state semantics下，frontier-first work-conserving admission相对hard Bind barrier贡献多少？**

比较：

[
MemBind\text{-}FIFO
\rightarrow
MemBind
]

回答：

> **Semantic-Constrained Cache Affinity还能提供多少secondary incremental benefit？**

其中Barrier与FIFO必须固定：

```text
same State-Cut
same W
same K_LLM
same arrival trace
same prompt/schema/model
same semantic work volume
```

区别仅为：

```text
Barrier:
frontier Bind期间不admit新的Compile

FIFO:
frontier request优先，但剩余capacity继续work-conserving Compile
```

FIFO与MemBind比较还必须固定：

```text
same APC/backend configuration
same cache initial-state protocol
same rendered requests / semantic work volume
```

并同时报告：

```text
APC token hit rate
cached prefix tokens
ScheduleEligibleReusablePrefixTokens
PrefixReuseEfficiency
recomputed prefill tokens
prefill latency / TTFT
end-to-end makespan
```

为了控制实验规模：

> **MemBind-Barrier只要求完成代表性performance/freshness mechanism ablation；不要求复制完整主实验与所有quality evaluation。**

如果deterministic/captured replay已经证明Barrier/FIFO/MemBind共享相同published-state semantics，则quality evaluation重点保留U0与最终MemBind，必要时对FIFO做spot check即可。

如果MemBind-FIFO与MemBind的cache指标无显著差异，则如实报告negative result，不继续增加新的cache heuristic以追逐主实验结果。

------

# 40. 实现优先级

正式实现按以下顺序推进。

## P0 — Semantic Core

```text
Arrival Gate
Evidence Fence
Graphiti State-Cut certification protocol
runtime persistent-state access guard
Prepared Artifact
Prepared ROB
Publication Frontier
Version-Bound Bind
Publish-completeness tracking
Ordered Publish
deterministic/captured serializability qualification
Direct violation instrumentation
```

------

## P1 — Runtime Scheduling

```text
Global LLM Admission
K_LLM
MemBind-Barrier ablation mode
Frontier-first priority
Work-conserving speculative admission
Bounded W
```

------

## P2 — Backend-Aware Optimization

```text
exact rendered-request capture
exact tokenization capture
backend prefix-match granularity G capture
physical KV block-size capture, if distinct
backend-granularity Prefix Path / LCP_G
recent-prefix / cohort index
cache-affine Compile ordering
cached-token / prefill instrumentation
```

P2实现约束：

```text
不修改vLLM APC实现
不依赖私有KV-cache内部状态才能正确运行
不为了形成cache cohort主动阻塞frontier
不根据formal main-result post-hoc修改score
```

默认cache-affine score在method freeze前固定为：

```text
longer backend-granularity affinity first
→ more recent completed prefix provider first
→ larger ready cohort first
→ source-sequence/FIFO tie-break
```

Warm-Then-Fanout不是P0/P1 requirement，也不是P2默认必需机制。

------

# 41. Method Freeze规则

本文件作为正式MemBind v3.1 frozen methodology。

进入formal evaluation后：

```text
不得根据主实验效果增加新的核心mechanism
不得改变State-Cut资格规则
不得根据性能结果移动operator boundary
不得修改arrival semantics
不得修改correctness contract
不得修改State-Cut Certification Protocol
不得在U0与MemBind之间改变PublishedReadContract
不得根据结果改变arrival-rate stress points
不得为MemBind单独改变backend配置
不得根据结果改变backend prefix-match granularity定义
不得把backend已有APC收益归入MemBind独有贡献
```

若发生实质method change：

```text
protocol version bump
previous formal results invalidated
new method freeze
new formal evaluation
```

------

# 42. 最终一句话定义

> **MemBind is an architecture-preserving runtime for stateful agent-memory construction. It cuts each memory update at the persistent-state boundary, speculatively executes the maximal adapter-certified evidence-bound region after the corresponding input has arrived, buffers the resulting state-unbound artifacts, and binds only the publication-frontier update to its exact predecessor memory version. Frontier stateful work receives admission priority while residual capacity remains available for bounded speculative execution; within that legal semantic slack, MemBind may further reorder requests using exact backend-granularity token-prefix affinity to improve realized reuse in the existing backend cache without weakening source-order state semantics.**

中文：

> **MemBind不改变Agent Memory“算什么”，而是把一次完整的stateful memory update沿persistent-state dependency切开：对已经到达输入中不依赖mutable memory的最大认证子图提前执行，将结果保存为尚未绑定state的Prepared Artifact；真正依赖persistent memory的操作只允许publication frontier绑定到精确的前驱committed version。Runtime优先推进最早能够改变memory visibility的frontier stateful work，同时利用剩余capacity继续执行有界speculation；只有在这些语义允许的执行自由度内，才基于真实tokenization的backend prefix-match-granularity-aligned affinity调整request order，以提高现有LLM backend中可复用prefix的实际命中，而不改变prompt或backend cache机制。**

最终方法：

[
\boxed{
\text{Arrival-Gated State Cut}
\rightarrow
\text{Prepared Speculation}
\rightarrow
\text{Frontier-First Version Binding}
\rightarrow
\text{Ordered Publication}
}
]

其中：

[
\boxed{
\text{Cache Affinity}
\subset
\text{Legal Semantic Slack}
}
]

而不是MemBind正确性的前提。