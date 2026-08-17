# MemBind 主 Methodology 设计：Evidence-Bounded Semantic Late Binding

> Status: `DESIGN_COMPLETE`
> Renderer template SHA256: `dcd72ea7ce1e2c083e6195c1b70f8a0ab48581e524dbaa0d66354c4e6d78f9f0`
> Methodology decision run: `methodology-dev-20260817-001`
> Methodology decision payload SHA256: `50a76d29ff973b67465940af94d3bc9c3814db04bad2774b4ea834b78ed22f4d`  
> Revision date: 2026-08-17  
> 本文档已经移除“先设计完整方法、再寻找支持证据”的旧顺序。结构、源码边界、
> TDD gate 和证伪规则与 sealed development report/decision 确定性绑定；本文的
> development 裁决不自动授权 live 方法实现或论文结论。

本文档不是 live authority。它不启动模型、Neo4j 或 held-out 实验，也不更新任何
`CURRENT_*` pointer。历史 S5/M* 代码仅作为测试思想和 prototype evidence，不能直接
升格为正式方法。

## 研究问题

Stateful agent-memory construction 经常把两类性质不同的工作封装在一个粗粒度
update 中：

1. 昂贵的 semantic computation，可能只依赖当前 source 与先前 immutable evidence；
2. resolution、maintenance 和 publication，读取 mutable memory 的最新状态，或依赖
   latest-state resolution 的输出，或产生 graph mutation/publication。

本项目研究：

> 在不改变 backend memory algorithm、输入 evidence、模型调用语义和 publication
> invariant 的条件下，能否提前执行经过验证的 evidence-bound semantic work，并把
> state-bound continuation 保留到正确的 committed graph state 上，从而降低
> arrival-to-publication-ack？

当前 `publication-ack` 只表示 `add_episode()` 已返回并记录 durable acknowledgement；
它是潜在 query visibility 的观测上界，不是独立 reader 实测的 `time-to-queryable`。
只有 transaction-commit/独立读者 visibility witness 通过后，才使用后一个术语。

`algorithm-preserving` 是需要由 capture/replay、canonical projection、检索和质量
gate 验证的目标，不是当前已经成立的性质。

当前只研究：

```text
Graphiti v0.29.3
repository revision/commit 021d3a5
LongMemEval DEVELOPMENT_EXPOSED histories
Qwen3-32B-FP8 construction
Qwen3-Embedding-0.6B
Neo4j
```

明确不声明适用于所有 Agent Memory backend。纯 append-only memory、query-time
construction、由 agent policy 决定是否写入的 memory，以及改变 representation 或
consolidation algorithm 的系统，都不属于当前适用范围。

## 证据驱动的研究裁决

### Evidence cutoff

最终裁决只允许绑定以下 sealed artifacts：

```text
Native U0 run                         `nb-20260816-001`
Three-baseline suite                 `bs-dev-20260816-001`
Graph-native quality overlay         `gq-dev-20260817-001`
Development aggregate report         `report-dev-20260817-001`
Native characterization C2/C3        SEALED file=`a80ca5a8e763c19eea9d2cde1dbe001425200d04c857384cb862cc65ccf1887f` payload=`7adc924db06e33e319d973a9b6ceaf402866bda4ea38a8755d3781f2ca86449f`
Whole-update C5 counterexample        SEALED run=`c5-e3867c66ba92e7da` file=`00ebfe67c13758a02fbb2dcbc94a336de92f88dbe25e666b3e069d7737c3594d` payload=`73cfc5219c39e9e786e9353868f5c64d942fec1db1188858fa314763ad6f8dc7` events=`52a69edd8ff94c1eaca5ca00401ccb75e3d4f39dc326364cbdf3e322ead5e849`
Methodology decision                  SEALED run=`methodology-dev-20260817-001` payload=`50a76d29ff973b67465940af94d3bc9c3814db04bad2774b4ea834b78ed22f4d`
```

最终必须在这里写入并交叉验证：

```text
report_run_id                         `report-dev-20260817-001`
native_run_id                         `nb-20260816-001`
suite_run_id                          `bs-dev-20260816-001`
overlay_run_id                        `gq-dev-20260817-001`
development report file SHA256        `664d9b0250abbeba54abe2ba9b1486c1f5a76e1ea235945ddf65bf21aa8a49ca`
development report payload SHA256     `ba060bd48fb933319b522ef5196c003919b2a0c0d2a81c3eb9f00f4b264e9c62`
methodology_decision_run_id            `methodology-dev-20260817-001`
methodology decision payload SHA256   `50a76d29ff973b67465940af94d3bc9c3814db04bad2774b4ea834b78ed22f4d`
C5 run_id                              `c5-e3867c66ba92e7da`
C5 file SHA256                         `00ebfe67c13758a02fbb2dcbc94a336de92f88dbe25e666b3e069d7737c3594d`
C5 payload SHA256                      `73cfc5219c39e9e786e9353868f5c64d942fec1db1188858fa314763ad6f8dc7`
C5 events file SHA256                  `52a69edd8ff94c1eaca5ca00401ccb75e3d4f39dc326364cbdf3e322ead5e849`
characterization file SHA256           `a80ca5a8e763c19eea9d2cde1dbe001425200d04c857384cb862cc65ccf1887f`
characterization payload SHA256        `7adc924db06e33e319d973a9b6ceaf402866bda4ea38a8755d3781f2ca86449f`
```

本轮 baseline 与 overlay 只使用四个 `DEVELOPMENT_EXPOSED` histories，共
188 episodes。没有评估 `PILOT` 或 `FINAL_PAPER_TEST`。最终数值只能形成
descriptive development signal，不能形成显著性、失败率或 universal safety claim。

数据访问还要保留一个精确边界：live graph-quality runner 只打开四-record isolated
artifact；该 artifact 的一次性 materialization 曾扫描 combined source container。
因此不能声称项目生命周期从未扫描 combined container，只能声称没有评估已经分配为
PILOT/FINAL role 的 records。

### 已封存的 characterization

| Observation | Result | 合法解释 |
|---|---:|---|
| Native median service | 34.72 s | construction cost material |
| Native P95 service | 116.97 s | construction 有长尾 |
| LLM transport interval-union occupancy | 99.29% | LLM transport occupied 99.29% of the measured root interval union；不是 critical-path attribution，也不等于纯模型 compute |
| D1 state-independent fraction | 61.284% | 包含 arrival 时尚未 ready 的 edge extraction |
| conservative arrival-ready fraction | 22.920% | 当前直接支持 node-side opportunity 的保守比例 |
| C5 C=8 makespan change vs C=1 | -51.1% | one-history capacity signal |
| C5 C=8 throughput change vs C=1 | +104.3% | one-history capacity signal |
| C5 C=2/4/8 source order | direct counterexample | 固定 history/interleaving 的 existence evidence，不是失败率 |

`61.284%` 不能写成 arrival-ready opportunity。当前唯一保守的直接 arrival-ready
比例是 `22.920%`；其 C=2 structural speedup upper bound 约为 `1.129x`，还没有计入
remote serving capacity、batching、contention、ordering 和 runtime overhead。

C5 的 graph/retrieval differences 使用 live、未 replay-fixed 的 LLM outputs，因此是
confounded evidence。可直接使用的正确性证据只是 source-order counterexample；而
source-ordered visibility 是当前应用/protocol 的 invariant，不应伪装成 Graphiti 对
所有调用者的普遍语义定理。

### 三基础 baseline 结果

下面的表必须由 sealed `REPORT.json` 原样填写，禁止人工选择有利 run：

| Method | Episodes | Goodput (ep/s) | P95 freshness (s) | Makespan (s) | Session Evidence Recall@10 | Graph-native QA | Direct violations |
|---|---:|---:|---:|---:|---:|---:|---|
| U0 | 188 | 0.022115 | 99.918 | 8501.162 | 1.000 | 0.000 (4/4 valid) | 0 (MEASURED) |
| A0 | 188 | 0.022058 | 2258.750 | 8523.113 | 1.000 | 0.000 (4/4 valid) | N/A (NOT_EVALUATED_IN_LIGHTWEIGHT_BASELINE_SUITE) |
| P(C=2) | 188 | 0.025559 | 1867.920 | 7355.528 | 1.000 | 0.000 (4/4 valid) | N/A (NOT_EVALUATED_IN_LIGHTWEIGHT_BASELINE_SUITE) |

正式表还要报告 P99 freshness、max backlog、observed max active updates、whole-update
overlap、LLM/token/embedding/DB/candidate work volume 和 final graph size。
这里不把未测量的 direct violations 解释为 0。

| Diagnostic method | P99 freshness (s) | Max backlog | Max active | Overlap | Workers | LLM calls | Input/output tokens | Embedding calls/items | DB operations/transactions | Candidates | Nodes | Relationships |
|---|---:|---|---:|---|---|---:|---|---|---|---:|---:|---:|
| U0 diagnostics | 205.629 | N/A (NOT_APPLICABLE_SERIAL_BASELINE) | 1 | false | 1 | 1900 | 17861037/207768 | 1870/4718 | 5261/188 | 21171 | 1199 | 1705 |
| A0 diagnostics | 2367.464 | 49 (OBSERVED) | 1 | false | 1 | 1900 | 17861327/207485 | 1870/4718 | 5261/188 | 21171 | 1193 | 1705 |
| P(C=2) diagnostics | 2156.406 | 49 (OBSERVED) | 2 | true | 2 | 2019 | 17832623/203119 | 2012/5120 | 5666/188 | 22480 | 1253 | 1838 |

本轮 development suite 的 arrival timestamp 不是跨方法同义：U0 在每次 serial service
开始前记录 arrival，而 A0/P 先为整个 history 发出 intent。因此本表的 P95/P99 只能描述
各 API execution mode 下的 observed burst/closed-loop 行为，禁止计算跨方法 freshness
improvement。相同 episode inventory 下的 makespan/goodput 可作为 burst-drain capacity 的
directional development signal；它也不是重复实验或显著性证据。正式 U0/P/M freshness
比较必须共享预生成的 open-loop `arrival_trace_sha256`，忙碌方法也必须在同一 arrival
时刻记录并排队 source。

### Quality claim boundary

质量有两个独立 surface：

```text
Session Evidence Recall@10
Graph-native top-20 facts + top-20 entity summaries QA
```

Session Recall 证明 annotated source sessions 是否被召回；它不能替代 graph facts、
temporal validity 和 entity summaries 的可用性。Graph-native overlay 的 U0/A0/P
结果必须使用相同 retrieval、Reader、Judge、prompt 和 runtime identity。

当前本地 Qwen Judge 只能标记为：

```text
PROTOCOL_RUBRIC_COMPATIBLE_LOCAL_JUDGE_DIAGNOSTIC
```

整体 stack 只能标记为：

```text
PROTOCOL_ALIGNED_NOT_NUMERICALLY_MATCHED
```

这些结果不是 official LongMemEval accuracy，也不能与 Zep、Mnemis、UnifiedMem 或
LongMemEval paper 的绝对数字做 exact comparison。

### Decision matrix

| Comparable P(C=2) observation | Graph-native overlay | 研究裁决 |
|---|---|---|
| capacity gain + direct invariant counterexample | U0 usable | 进入 dependency-aware candidate |
| capacity gain + no observed insufficiency | all methods interpretable | 重新评估 MemBind 必要性，不把 P 称为 proven safe |
| no capacity signal + counterexample | U0 usable | runtime performance motivation 不足，STOP/转 serving problem |
| no capacity signal + no observed insufficiency | all methods interpretable | `STOP_NO_SYSTEM_SIGNAL` |
| U0 overlay invalid/degenerate | any | `BLOCKED_QUALITY_PROTOCOL`，不评价 scheduler |
| result dominated by model nondeterminism | any | 进入 frozen-provider capture/replay，不作 semantics claim |

最终 cell 由 `paper_eval.methodology_decision.build_methodology_decision()` 确定性生成，
不由执行者看完数值后手工选择。当前冻结谓词为：

```text
quality_protocol_usable =
  U0/A0/P(C=2) 每个方法 valid_judge_count == 4
  AND invalid_judge_count == 0
  AND graph_quality_qa_accuracy 为合法数值
  AND U0 graph_quality_qa_accuracy > 0

development_directional_capacity_signal =
  P(C=2) whole-update overlap observed
  AND observed_max_active_calls >= 2
  AND same-188-episode aggregate makespan < U0 aggregate makespan

direct_invariant_counterexample =
  sealed C5 overall_interpretation == DIRECT_INVARIANT_VIOLATION_OBSERVED
  AND C=2 block 含 "source-order invariant violation"
```

这里不设置事后 accuracy 提升阈值；`U0 == 0/4` 只表示当前 graph-native
retrieval/Reader 路径没有提供任何正例证据，因此属于精确的 degenerate block。高于 0
也只表示该路径可解释，不表示质量充分或达到论文可接受水平。

同 episode 数时 goodput 与 makespan 方向应互相一致；per-history win count 只报告为诊断，
不设置事后阈值。当前 arrival 语义不同的 P95/P99 不进入 matrix。C5 live graph/retrieval
差异仍标记为 model-nondeterminism-confounded，但不能覆盖直接 source-order trace evidence。
该规则及 REPORT、C3、C5、C5 events 的 file/payload SHA256 必须一起写入 exclusive-write
`METHODOLOGY_DECISION.json`。

一次 development screening 不能证明 P 普遍安全或普遍不安全。最终裁决状态当前为：

```text
actual decision-matrix cell = `BLOCKED_QUALITY_PROTOCOL`
problem_verdict       = `BLOCKED_QUALITY_PROTOCOL`
mechanism_status      = `NO_METHOD_SELECTED`
paper_claim_status    = `NOT_AUTHORIZED_DEVELOPMENT_ONLY`
live_method_status    = `NOT_AUTHORIZED`

freshness_comparison
  = `NOT_CROSS_METHOD_COMPARABLE_CURRENT_ARRIVAL_SEMANTICS`

makespan_goodput_comparison
  = `DESCRIPTIVE_BURST_DRAIN_DEVELOPMENT_CAPACITY`

resource_comparability
  = `NOT_ESTABLISHED_UNIFIED_REQUEST_ADMISSION_ABSENT`

semantic_parity
  = `NOT_AUTHORIZED_LIVE_MODEL_OUTPUTS_NOT_CAPTURE_REPLAY_FIXED`

statistical_claim
  = `NOT_AUTHORIZED_NO_REPEATS_DEVELOPMENT_ONLY`
```

## Graphiti 实际依赖边界

Pinned `Graphiti.add_episode()` 的真实顺序是：

```text
retrieve previous episodes
  -> construct EpisodicNode
  -> extract_nodes
  -> resolve_extracted_nodes
  -> extract_edges
  -> resolve_edge_pointers
  -> resolve_extracted_edges / temporal invalidation
  -> attributes and summaries
  -> transactional bulk persistence
  -> return / publication acknowledgement
```

该顺序只对本轮 frozen runner 的 `update_communities=false, saga=None` 成立；若启用
community update 或 saga association，必须重新做 dependency classification。

源码位置：

```text
membind-validation/.venv/lib/python3.12/site-packages/
  graphiti_core/graphiti.py
  graphiti_core/utils/maintenance/node_operations.py
  graphiti_core/utils/maintenance/edge_operations.py
  graphiti_core/utils/bulk_utils.py
```

Operator classification：

| Operator | Default class | 原因 |
|---|---|---|
| source-prefix projection | evidence-bound candidate | source、reference time、group、last-N 可从 immutable log 投影，但必须证明 Native context parity |
| `extract_nodes` | evidence-bound candidate | prompt 只使用 episode、previous episode contents/timestamps 和 frozen config |
| `resolve_extracted_nodes` | state-bound | candidate lookup、embedding search、LLM dedup 读取 current graph |
| `extract_edges` | conditional | 参数看似只用 raw extracted nodes/evidence，但 Native 在 node resolution 后调用，迁移必须独立 qualification |
| `resolve_edge_pointers` | state-bound continuation | 使用 node resolution 的 UUID map |
| `resolve_extracted_edges` | state-bound | candidate edges、dedup、temporal invalidation 读取 current graph |
| attributes/summaries | state-bound | 使用 resolved nodes/new edges/previous attributes |
| persistence/publication | publication | 产生 mutation 并返回 acknowledgement；externally queryable 时刻尚未由独立 reader witness 实测 |

这里不从函数名或参数列表直接推导“可以移动”。程序顺序、可能的对象 mutation、prompt
serialization、failure behavior 和 model call identity 都属于 semantics。

## 候选机制

候选名称：

```text
MemBind-v1
Evidence-Bounded Semantic Late Binding
```

它是 observation-conditioned candidate，不是已证明的最终方法。最小版本只并行
已经能够严格 qualification 的 prefix。

### Core abstraction

```python
class LateBindingBackend(Protocol):
    async def compile(
        self,
        source: SourceRecord,
        evidence: EvidenceFence,
    ) -> PreparedArtifact: ...

    async def bind_and_commit(
        self,
        artifact: PreparedArtifact,
        latest_committed_state: object,
    ) -> Publication: ...
```

`PreparedArtifact` 不是 Python closure，而是可校验、可恢复的 durable data：

```text
source_sequence
source_sha256
evidence_prefix_sha256
episode_projection_sha256
operation_identity
model / prompt / schema / config identities
semantic payload
artifact_sha256
```

### Immutable source log and Evidence Fence

每个 source 在调度前进入 append-only log：

```text
sequence 0, sequence 1, ... sequence i
```

对 source `i`，Evidence Fence 只能读取 Native-equivalent source prefix。它需要精确
实现并测试：

```text
same group/source filters
reference_time <= current reference_time
same last-N limit
same chronological presentation
same episode content/source description/timestamps
fail-closed handling of equal timestamps at the Native last-N boundary
no current/future source leakage
```

Pinned Native query 只按 `valid_at DESC` 排序，没有稳定的 secondary tie key。若 last-N
边界出现 equal timestamp，Evidence Fence 不能自行发明 tie-breaker 并声称 exact parity；
qualification 必须 fail closed，或让 U0 与候选都使用预先 capture 的 explicit episode UUID
path。

Compiler 结构上禁止持有或访问：

```text
Neo4j driver
Graphiti retrieve_episodes()
canonical node/edge lookup
candidate search
invalidation state
latest graph counts
```

当前 S5 live adapter 会在 `prepare()` 中读取 mutable Graphiti namespace，因此不能
直接复用为正式 Evidence Fence。

### Node-only minimum viable compile

第一版 correctness-first pipeline 固定为：

```text
arrival(source_i)
    -> immutable EvidenceFence_i
    -> extract_nodes
    -> durable PreparedNodeArtifact_i
    -> wait for source i-1 publication acknowledgement
    -> read latest committed state
    -> resolve_extracted_nodes
    -> extract_edges
    -> resolve edges / invalidation
    -> attributes / summaries
    -> Native persistence
    -> source-ordered publication acknowledgement
```

`PreparedNodeArtifact` 至少保存：

```text
source_sequence
source_sha256
evidence_prefix_sha256
episode projection and UUID identity
extracted_nodes
node_episode_index_map
request/prompt/model/schema identities
artifact_sha256
```

Node-only 不预先承诺足够的性能收益。它的价值是建立最小、可证伪的 semantic boundary。
如果测得 overlap 被 state-bound suffix、vLLM saturation 或 runtime overhead 抵消，方法应
STOP，而不是自动扩展机制。

### Edge relocation gate

只有下列条件全部通过，`extract_edges` 才能从 bind 移入 compile：

```text
1. zero mutable graph reads
2. exact prompt/input parity
3. same previous-episode projection
4. same raw extracted-node names, labels, ordering and UUID attribution
5. same custom instructions, edge types and schema
6. same logical-call count, error and retry behavior
7. frozen-provider capture/replay parity
8. deterministic downstream canonical graph and retrieval parity
```

Gate 内容必须在任何 node+edge live outcome 产生前冻结并通过。失败则保持 node-only；
不得因为 node-only speedup 小而在看过结果后放宽 gate。Node-only 没有 signal 时仍按 S4
STOP；若要另行研究 edge relocation，必须形成并披露独立 hypothesis amendment，而不是
在同一实验中追逐正结果。

### Latest-state bind

Binder 只消费当前 visibility frontier 的下一项：

```text
artifact.source_sequence == published_frontier + 1
```

Bind 开始时重新读取 latest committed state，并保持 pinned Native continuation 的调用
顺序、参数、exception behavior 和 work volume。允许相同 runtime UUID 出现多次，只在
“同 UUID、同 canonical projection”时确定性 coalesce；“同 UUID、不同 projection”
必须 fail closed。

`latest` 只在 namespace single-writer 前提下有定义。正式 binder 必须持有覆盖整个
state-bound continuation 到 publication ack 的 namespace-scoped exclusive writer lease
与 fencing token；lease loss、外部 writer、stale token 或无法证明单写者时 fail closed。

### Bounded lookahead and resource admission

Runtime 需要：

```text
compile concurrency C
prepared lookahead W
global construction LLM admission K
bind priority policy
```

`C/W/K` 是工程参数，不是 novelty，也不预先声明最优。它们必须在 held-out 前由
development calibration 冻结。

公平性不能用 worker count 推断。所有 future U0/P/M comparisons 必须共享相同 server
和 transport-request resource envelope，并记录：

```text
observed transport inflight
server running/queued requests when available
LLM calls, input/output tokens and retries
embedding calls/items
DB operations/transactions
graph semantic work volume
```

Graphiti 内部会产生 nested LLM fan-out，所以 `P(C=2)` 不自动等于 `K=2`。如果当前
development P artifact 无法证明与 MemBind 相同的 request envelope，它仍可用于
characterization，但 formal speedup comparison 必须在统一 admission envelope 下重跑。

Bounded lookahead 只保证 prepared buffer、失败面和 speculative work 有界。`W>1` 会在
较早 source 最终失败时产生 Native Serial 不会发出的后续调用；因此 exact logical-call
parity 只适用于成功 accepted prefix。失败路径必须记录并报告
`speculative_calls/items/tokens_discarded`，取消尚未发送的 work，且禁止把 discarded work
合并进 Native work parity。若要求失败路径也 exact parity，则只能使用 `W=1`。是否需要
bind priority，要由 frontier stall 和 bind admission wait telemetry 决定，不能先验升格
为论文贡献。

### Durable states and restart

每个 source 的状态机：

```text
INTENT_DURABLE
  -> PREPARE_RUNNING
  -> PREPARED_DURABLE
  -> BIND_RUNNING
  -> COMMIT_RETURNED
  -> PUBLICATION_DURABLE
```

合法恢复边界只有 durable states。恢复时必须重新验证 source、evidence、artifact、code、
model/config 和 namespace identities。任何 partial/corrupt artifact、重复 publication 或
不连续 published prefix 都拒绝恢复。

Graphiti graph transaction 与 MemBind acknowledgement ledger 不是同一事务。若进程在
`COMMIT_RETURNED` 后、`PUBLICATION_DURABLE` 前崩溃，状态必须变为
`AMBIGUOUS_COMMIT_POISONED`，禁止在原 namespace 猜测或重放；只有 idempotency key、
transaction coupling 或 read-after-crash effect witness 通过后才能缩小该窗口。

现有 S5 pipeline 的 ordered bind、failure ledger 和 compatible-node coalescing tests 可
参考；其一次性入队全部 sources、无统一 admission、无 bounded lookahead 和 mutable
prepare adapter 不能直接成为正式 runtime。

## Correctness specification

### Required invariants

```text
C1 source inventory is exact and contiguous
C2 every source is prepared at most once per attempt
C3 every source has complete terminal accounting: one accepted durable acknowledgement,
   an explicit failure, or AMBIGUOUS_COMMIT_POISONED
C4 publications form a source-ordered durable prefix
C5 compile performs zero mutable graph reads
C6 bind reads the latest state after the previous publication
C7 Native call order and arguments are preserved outside qualified relocation
C8 no hidden fallback, silent retry or dropped semantic work
C9 bounded W and global K are observed, not inferred
C10 failed attempts never merge with successful evidence
```

### Algorithm-preservation evidence ladder

一次 live run 无法控制 LLM nondeterminism，因此证据分层：

```text
Layer 1  exact request/prompt/input/call-order parity
Layer 2  frozen-provider semantic artifact parity
Layer 3  deterministic canonical graph projection parity
Layer 4  retrieval parity and graph-native quality guardrails
Layer 5  live work-volume and quality non-regression
```

只有 Layer 1-4 通过后，才能声称“在冻结 fixture 和明确 canonical projection 下的
bounded observational equivalence”。Layer 5 是外部有效性 evidence，不替代
deterministic proof；这里不升级成一般 `algorithm-preserving` 定理。

Canonical projection 至少处理：

```text
runtime UUID renaming where identity is incidental
`created_at` and other operational metadata only after a complete consumer audit
node name/type/summary/attribute projection
edge endpoints/fact/validity/provenance projection
episode attribution
duplicate coalescing policy
stable ordering and serialization
```

提前执行 `extract_nodes` 会改变随机 UUID 与 `created_at` 的生成时刻。二者要么保持
Native semantics，要么逐 consumer 审计 query、dedup、ordering 和 maintenance 使用后，
才可列入允许归一化的 divergence budget。

## Publication 边界

当前候选只保证：

```text
source-ordered bind
source-ordered publication acknowledgement
at most one accepted durable acknowledgement record per source
fail-closed durable prefix
```

对 source `i+1` 的 state-bound continuation，只有 source `i` 的 Native commit
callback 返回且 publication event 已 durable 后才能开始。

这里不声明 DB atomic publication。虽然 pinned Graphiti 的 bulk persistence 使用一个
Neo4j write transaction，外部 visibility、callback 与 durability 的整体边界仍需独立
rollback/visibility test。只有该 gate 通过，才允许升级 atomic visibility claim。

Bind/commit failure 时：

```text
stop new admission
persist failed source and error class
preserve already published prefix
retain all complete prepared artifacts
mark attempt incomplete_non_mergeable if pollution cannot be excluded
never retry in place on a potentially polluted namespace
```

## TDD 实现门禁

实现严格遵循：

```text
focused RED
  -> minimum implementation
  -> focused GREEN
  -> related regression
  -> full offline GREEN
  -> bounded fresh-namespace live smoke
```

测试清单：

```text
T1  source-prefix membership/order/truncation/tie behavior
T2  Evidence Fence vs captured Native previous-context equivalence
T3  compile zero mutable graph reads
T4  extract_nodes exact request/prompt/input parity
T5  PreparedArtifact canonical hash and exclusive durable write
T6  latest-state access occurs only in bind
T7  bind starts only at frontier+1
T8  same UUID + same projection deterministic coalescing
T9  same UUID + conflicting projection fail closed
T10 source-ordered acknowledgement, duplicate rejection and terminal accounting
T11 bounded lookahead W and shared admission K
T12 bind priority cannot exceed K or starve indefinitely
T13 crash at every durable transition and restart verification
T14 partial DB write pollution detection / fresh-namespace rule
T15 LLM/embedding/DB/graph work-volume accounting parity
T16 session retrieval and graph-native quality parity
T17 edge relocation qualification, only if requested
```

Live smoke 只在上述 offline gates 全绿后运行 3-5 个 `DEVELOPMENT_EXPOSED`
episodes。Adapter bug 按 RED -> fix -> focused GREEN 修复；禁止因为一个 bug 新建大型
qualification campaign。

## 最小评测矩阵

### Methods

| Method | Role |
|---|---|
| U0 Native Serial | primary Native reference |
| A0 Async-Serial | supporting caller-blocking/freshness baseline |
| P(C=2) Whole-Update Parallel | strong coarse-grained falsification baseline |
| MemBind node-only | minimum dependency-aware candidate |
| MemBind node+edge | 仅在 edge relocation gate 预先通过时 |
| one scheduler ablation | 仅由预冻结 telemetry question 选择 `W=1` 或 no-bind-priority 之一 |

当前不实现：

```text
M-CO
M-Spec
predicate validation
selective repair
parallel state-bound bind
general DAG scheduler
second backend
new benchmark family
```

### Metrics

Headline metrics 保持统一 observability contract 中预冻结的六项：

```text
QA Accuracy
Session Evidence Recall@10
Direct Violations
P95 Arrival-to-Publication-Ack Freshness
Successful Goodput
Makespan
```

Predefined secondary metrics：

```text
P99 Freshness
Max Backlog
```

Diagnostic metrics：

```text
mean/P50/P90/P95/P99/max latency
queue delay, service latency, freshness latency
mean/P95/max backlog and queue area
compile/bind/admission wait and frontier stall
interval union, overlap and observed concurrency
LLM/token/retry, embedding, DB and graph work volume
final graph size and semantic work counts
```

所有 latency 聚合必须保留 history 为 experimental unit。Pooled episode distribution
只能 descriptive；不得把 188 episodes 当作 188 个独立 histories 做 significance test。

当前三-baseline development artifact 因 arrival semantics 不同，P95/P99 只进入
diagnostic table，不进入跨方法裁决。正式 online experiment 必须冻结同一个 open-loop
arrival trace、arrival rate/load、warmup/cache policy、server quiescence gate、共享
request-level admission、repeat count 与 blocked/counterbalanced method order。`C/W/K`
calibration 还必须预先给出有限 grid、selection objective 和 deterministic tie-breaker。

### Execution sequence

```text
1. offline TDD and deterministic provider parity
2. 3-5 episode fresh-namespace smoke
3. one 49-episode DEVELOPMENT_EXPOSED history
4. data decision
5. only if supported: frozen four-history development run
6. freeze code/config before PILOT
7. one-shot held-out evaluation under a separate plan
```

每个 history 使用独立 namespace、checkpoint 和 result，可以按 block 恢复；不能把多个
histories 放进一个无 checkpoint 的大事务。

## 停止与证伪条件

遇到下列条件立即 STOP 或降级 claim：

```text
S1 U0 graph-native retrieval/Reader/Judge denominator invalid
   -> BLOCKED_QUALITY_PROTOCOL

S2 P has capacity gain and no observed correctness/quality insufficiency
   -> REASSESS_MEMBIND_NECESSITY

S3 no reproducible freshness/capacity problem
   -> STOP_NO_SYSTEM_SIGNAL

S4 node-only candidate has no paired performance signal
   -> STOP_NO_METHOD_SIGNAL; do not add mechanisms automatically

S5 speedup accompanies material semantic-work reduction
   -> reject pure scheduling claim

S6 resource envelope is not comparable
   -> INVALID_RESOURCE_COMPARISON

S7 loss, duplicate, direct invariant violation or quality drift
   -> METHOD_CORRECTNESS_STOP

S8 edge relocation gate fails
   -> keep node-only; do not claim node+edge compile

S9 rollback/visibility gate absent or fails
   -> keep ordered-ack claim; do not claim atomic publication

S10 benefit only appears in one Graphiti-specific path
    -> narrow applicability; do not claim universal runtime
```

`P(C=2)` 没有观察到 violation 时，合法表述是
`NO_DIRECT_INSUFFICIENCY_OBSERVED`，不是 “proven sufficient”。观察到一次 direct
counterexample 时，合法表述是 existence counterexample，不是 failure probability。

## 实现顺序

### Isolated code layout

正式候选使用新目录，避免把历史 S5/M* prototype 混入生产 identity：

```text
paper-eval-v3/src/paper_eval/membind_v1/
  source_log.py
  evidence_fence.py
  delta.py
  graphiti_adapter.py
  admission.py
  frontier.py
  store.py
  runner.py

paper-eval-v3/scripts/run_membind_v1.py
paper-eval-v3/tests/test_membind_v1_*.py
```

### Ordered implementation steps

```text
Step 1  SourceLog + EvidenceFence pure models and RED tests
Step 2  Native previous-context capture and exact equivalence tests
Step 3  node-only compiler with zero-read guard
Step 4  canonical PreparedNodeArtifact + durable store/restart
Step 5  latest-state Graphiti binder and compatible-node coalescer
Step 6  source-ordered frontier/publication ledger
Step 7  bounded lookahead + uniform request-level admission
Step 8  failure injection and pollution/recovery tests
Step 9  frozen-provider end-to-end canonical parity
Step 10 bounded live smoke
Step 11 one development history and scientific STOP decision
Step 12 optional edge relocation only under a separately disclosed hypothesis amendment;
        its gate is frozen before any node+edge live outcome
```

No live run may begin merely because this document exists. A separate execution command requires
focused GREEN, related/full offline GREEN, a fresh namespace and an explicit run identity.

## Related-work boundary

LongMemEval separates indexing、retrieval 和 reading，并显示 Reader/Judge identity 会显著
影响 QA。Zep 的 public evaluation 使用 facts、validity 和 entity summaries，但其云服务、
dataset、Reader 和 Judge 与当前 OSS Graphiti/Qwen stack 不同。Mnemis 基于 Graphiti 但
改变 reflection、speaker constraints、hierarchy、selection 和 reranking；UnifiedMem 使用
controlled graph 与不同 Judge。这些工作能支持 evaluation shape，不能成为当前 stack 的
exact numeric comparator。

MemForest、GAM、RecMem 等改变 representation、construction policy 或 consolidation
algorithm。MemBind 的候选 gap 只能表述为：

> 对已经决定执行的 stateful update，在不改变 native state-dependent continuation 的
> 前提下，是否存在可 qualification 的 evidence-bound prefix，可以与其他 updates 重叠？

不能把 contribution 写成“发明 parallel extraction”“发明 encoding/consolidation 分层”
或“Graphiti 不支持 batching”。

## Claim boundary and threats to validity

当前允许的最终 development claim 上限是：

> Under the pinned Graphiti backend and frozen development workload, the
> measurements may motivate a dependency-aware execution candidate that overlaps a
> qualified evidence-bound prefix while targeting bounded observational equivalence
> for explicitly tested Native state-bound operations and quality guardrails.

在 MemBind method result 产生前，不允许声称它已经降低 P95、提高 goodput 或保持语义。

主要 threats：

1. 四个 histories、四个 knowledge-update questions 只提供 bounded development signal；
2. local Qwen Judge 没有 official LongMemEval human-agreement qualification；
3. live LLM nondeterminism 会混淆 graph parity，需要 frozen replay；
4. LLM transport span 包含网络、server queue、prefill/decode，不能归因于单一层；
5. 一个远端 vLLM 与一台 Neo4j 的容量结论不能推广到所有部署；
6. source-order 是本协议的 visibility invariant，不是 universal Graphiti theorem；
7. Graphiti transaction code 不等于已经实证 external atomic visibility；
8. node-only 的保守 opportunity 可能太小，这是一项应当允许失败的 hypothesis；
9. 当前 U0 与 A0/P arrival timestamp 语义不同，不能用本轮 P95 做 cross-method freshness claim；
10. 当前 method-major、single-pass execution 会与时间漂移、cache 和远端 serving 状态混杂；
11. UUID/created_at、equal-timestamp boundary 和外部 namespace writers 都需要显式 gate。

## Artifact index

当前已封存依据：

```text
MemBind_CURRENT_EXPERIMENT_REPORT.md
membind-validation/artifacts/native_characterization/e2_dependency_opportunity.json
membind-validation/artifacts/native_characterization/runs/
  c5-e3867c66ba92e7da/e4_whole_parallel.json
paper-eval-v3/UNIFIED_OBSERVABILITY_CONTRACT_v1.0.md
paper-eval-v3/LITERATURE_AND_PUBLIC_CODE_AUDIT_LONGMEMEVAL_GRAPHITI_QWEN_20260816.md
paper-eval-v3/QA_ACCURACY_DIAGNOSIS_AND_REMEDIATION_20260816.md
```

本轮 finalizer 已绑定：

```text
paper-eval-v3/artifacts/paper_eval/baseline_suite/runs/
  bs-dev-20260816-001/THREE_BASELINE_RESULTS.json
paper-eval-v3/artifacts/paper_eval/graph_quality_overlay/runs/
  gq-dev-20260817-001/GRAPH_QUALITY_RESULTS.json
paper-eval-v3/artifacts/paper_eval/development_report/runs/
  report-dev-20260817-001/REPORT.json
paper-eval-v3/artifacts/paper_eval/methodology_finalization/runs/
  methodology-dev-20260817-001/METHODOLOGY_DECISION.json
MemBind_THREE_BASELINE_DEVELOPMENT_EXPERIMENT_REPORT_20260817.md
```

绑定摘要：report payload `ba060bd48fb933319b522ef5196c003919b2a0c0d2a81c3eb9f00f4b264e9c62`；decision payload
`50a76d29ff973b67465940af94d3bc9c3814db04bad2774b4ea834b78ed22f4d`；C5 file `00ebfe67c13758a02fbb2dcbc94a336de92f88dbe25e666b3e069d7737c3594d`；characterization file
`a80ca5a8e763c19eea9d2cde1dbe001425200d04c857384cb862cc65ccf1887f`。

本文档已写入上述 report identities、真实三方法数值、graph-quality denominator 
和 actual decision-matrix cell。`DESIGN_COMPLETE` 只表示 development methodology 
文档完整；live method 与 paper claim 仍服从上面的未授权状态和 TDD gate。
