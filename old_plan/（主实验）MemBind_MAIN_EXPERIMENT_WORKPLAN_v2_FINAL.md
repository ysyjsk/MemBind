# MemBind Paper-Level Main Experiment Workplan v2.0（Final）

> **状态**：从当前最新项目状态开始执行的论文级主实验协议  
> **目标 venue 风格**：MLSys / NSDI / OSDI 类 systems paper 的 end-to-end headline evaluation  
> **核心原则**：先证明 Native baseline 是可信、可复现、符合上游与 benchmark 规范的“尺子”，再冻结 Native，之后才允许调 competing baselines / MemBind，并最终在 held-out workload 上做统一主实验。
>
> **重要说明**：
>
> - 本计划**不再等待或执行 C6**。
> - 不修改、删除或重写已有 C0–C6 workplan / contract / artifact；它们作为历史 characterization 证据保留。
> - 从本计划开始，后续 paper-level execution 以本文档为准。
> - 已经被旧实验、调试、Judge qualification 或人工检查暴露的数据，全部视为 `DEVELOPMENT_EXPOSED`，不得重新作为 paper held-out test。

---

## 0. 主实验最终要回答的问题

唯一 headline research question：

> **在完全相同的 Graphiti、construction LLM、embedding、Neo4j、硬件和 LongMemEval workload 下，相比 Native Serial、Async-Serial 和合理优化后的 Naive Whole-Update Parallel，MemBind 能否在保持 Native-level retrieval / QA quality 和 stateful-memory correctness 的同时，显著降低 memory freshness latency，并提高 successful memory-construction goodput？**

最终主表必须让审稿人直接看到：

```text
Native:
    quality/correctness strong
    systems performance slow

Async-Serial:
    front-end return faster
    but true memory freshness / capacity not fundamentally improved

Whole-Update Parallel:
    can improve performance
    but may pay correctness / quality cost

MemBind:
    Native-level quality/correctness
    + better freshness
    + better successful goodput
```

如果真实结果不是这个模式，必须如实报告，不得修改 protocol 追求预期结果。

---

# 1. 顶会实验规范依据

本计划采用以下近期顶会/官方 benchmark 中已经出现的实验原则。

## 1.1 先保证 baseline 与公开 protocol 可复现

- **LongMemEval，ICLR 2025**：官方公开 cleaned dataset、evaluation-only script、retrieval / generation code，并明确允许自定义 memory system 输出 hypothesis 后走统一 evaluator。
- **MemoryAgentBench，ICLR 2026**：官方仓库明确发布 “code for reproducing the main experiment”，并对不同任务固定 benchmark-native metric。
- **A-Mem，NeurIPS 2025**：公开仓库明确用于 paper reproduction，并要求在公开 sweep 上选择超参数；memory construction 可缓存，后续 evaluation 与 construction 解耦。

因此：

> Native baseline 在进入正式 comparison 前必须经过独立 qualification / reproduction，而不能仅以“能跑通”为合格标准。

## 1.2 主结果必须是 matched-stack end-to-end comparison

- **Agentix，NSDI 2026**：在相同 workload / baseline 条件下以 program-level latency–throughput 为 headline result。
- **HIPPOCAMPUS，MLSys 2026**：同时报告 latency / resource footprint 和 downstream task accuracy，强调 systems gain 不应以 quality degradation 换取。

因此 MemBind 主实验使用：

```text
same workload
same model stack
same hardware
same benchmark
same quality evaluator

only runtime execution schedule changes
```

## 1.3 不把旧 Graphiti / Zep 的绝对数字当硬 target

Zep/Graphiti 早期论文在 LongMemEval 上公开过结果，但 Graphiti maintainer 已明确说明相关论文使用的是 **very early version of Graphiti**。

同时：

- 当前 Graphiti revision 已变化；
- construction model 不同；
- embedding 不同；
- Reader / Judge 不同；
- 当前 Graphiti-derived 方法（如 ACL 2026 Main 的 Mnemis）还进一步修改了 base graph extraction / retrieval。

因此本计划要求：

```text
必须复现 protocol / implementation semantics；
如果 exact historical stack 可获得，再做 numeric reproduction；
如果 exact stack 不可获得，不允许伪造“复现了论文数字”的结论。
```

---

# 2. 执行总顺序

严格按照：

```text
P0  SNAPSHOT CURRENT STATE
↓
P1  NATIVE UPSTREAM QUALIFICATION
↓
P2  NATIVE REFERENCE REPRODUCTION / ALIGNMENT
↓
P3  FREEZE NATIVE BASELINE
↓
P4  FREEZE DEVELOPMENT / HELD-OUT SPLIT
↓
P5  CALIBRATE P* AND MEMBIND ON DEVELOPMENT ONLY
↓
P6  FREEZE ALL METHODS + STACK + MAIN WORKLOAD
↓
P7  MAIN QUALITY SURFACE
↓
P8  MAIN PERFORMANCE SURFACE
↓
P9  GENERATE ONE HEADLINE MAIN TABLE
↓
P10 STOP MAIN EXPERIMENT
```

禁止跳过 P1/P2 直接进入 MemBind 主实验。

---

# 3. P0 — Snapshot Current State

本阶段只做记录，不修改历史协议。

必须记录：

```text
current git commit
working tree status
Graphiti installed version
Graphiti source commit
construction model identity
embedding model identity
vLLM version
Neo4j version
Judge identity
Reader identity
current exposed question/history IDs
existing C0-C5 artifacts
```

输出：

```text
artifacts/paper_main/p0_current_state_snapshot.json
```

同时生成：

```text
DEVELOPMENT_EXPOSED_IDS.json
```

其中必须包括所有已经：

- 在 C0–C5 使用；
- 用于调试；
- 用于 canary；
- 用于 Judge qualification；
- 被人工检查 method outcome；
- 用于旧 calibration；

的真实 benchmark IDs。

历史 C0–C6 contract 文件保持原样，不修改。

---

# 4. P1 — Native Upstream Qualification

## 4.1 Native baseline 定义

正式 Native baseline：

```text
U0 = Upstream-Qualified Graphiti Serial
```

定义：

```text
E0
→ official upstream add_episode(E0)
→ E0 reaches Native publication boundary
→ E1
→ official upstream add_episode(E1)
→ ...
```

严格 source-order serial。

## 4.2 U0 允许的修改

只允许：

```text
passive instrumentation
timestamps
work counters
artifact/checkpoint writing
error classification
```

禁止：

```text
project-specific deterministic candidate sorting
response replay
embedding replay
additional semantic cache
modified entity resolution
modified edge resolution
modified invalidation
modified graph publication semantics
modified retrieval semantics
```

任何上述修改都不能叫 `U0 Native`。

---

## 4.3 N1 — Upstream identity

Agent 必须验证并持久化：

```text
Graphiti package version
Graphiti source commit
critical source-file hashes

actual add_episode function/module
actual entity resolution implementation
actual edge resolution implementation
actual invalidation implementation

Graphiti internal concurrency config
```

输出：

```text
native_identity_manifest.json
```

PASS 条件：

> 当前执行路径可以追溯到 pinned upstream Graphiti implementation，不包含 project-only semantic patch。

---

## 4.4 N2 — Instrumentation parity

在 synthetic deterministic fixture + `DEVELOPMENT_EXPOSED` histories 上比较：

```text
direct upstream execution
vs
instrumented U0 execution
```

必须验证：

```text
same arguments
same call ordering
same exceptions
same return semantics
same deterministic fixture graph
```

并测 instrumentation overhead。

冻结 guardrail：

```text
overhead <= 2%:
    PASS

2% < overhead <= 5%:
    PASS_WITH_DISCLOSURE
    必须持久化 overhead 和原因

overhead > 5%:
    FAIL
```

任何 semantic difference：

```text
FAIL
```

无论 overhead 多小。

---

## 4.5 N3 — Native functional qualification

在 `DEVELOPMENT_EXPOSED` 上跑真实 Native serial。

要求：

```text
all expected episodes accounted for
no unexplained lost episode
no duplicate publication
fresh graph starts empty
construction reaches terminal state
retrieval callable after construction
source/provenance mapping complete
no unexplained infrastructure failure
```

该阶段不产生论文 main result。

---

# 5. P2 — Native Reference Reproduction / Alignment

这是正式主实验前的硬门。

目标不是追求某个“漂亮分数”，而是证明：

> **我们的 Native U0 与公开 benchmark / upstream evaluation semantics 对齐，并且没有出现无法解释的异常质量行为。**

---

## 5.1 R1 — Pin official LongMemEval benchmark

必须使用官方 cleaned release。

冻结：

```text
longmemeval_s_cleaned.json
longmemeval_oracle.json

dataset revision
dataset SHA256
official evaluation source revision
official evaluator source SHA256
```

LongMemEval-S 的：

```text
question_id
question_type
haystack_session_ids
haystack_dates
answer_session_ids
```

必须保持官方定义。

不得重新生成一份“看起来相似”的数据替代 official cleaned release。

---

## 5.2 R2 — Evaluator parity reproduction

使用一组固定、已暴露的 development hypotheses。

对完全相同的 hypothesis file：

```text
LongMemEval official evaluator
vs
MemBind LongMemEvalAdapter
```

逐 item 比较：

```text
question_id
question_type
generated judge prompt hash/content
parsed label
aggregate accuracy
```

要求：

```text
100% evaluator semantic parity
```

如果 Judge backend 因可获得性不同：

- rubric / prompt generation 必须完全对齐；
- backend replacement 必须单独标记；
- 不得把 backend difference 混成 evaluator parity。

输出：

```text
native_reference/evaluator_parity.json
```

---

## 5.3 R3 — Dataset / session mapping parity

对 development IDs 验证：

```text
question_id exact match
session count exact match
session timestamp order exact match
answer_session_ids exact match
question / answer hash match
```

要求：

```text
100% mapping parity
```

---

## 5.4 R4 — Upstream execution cross-check

在 development histories 上同时运行：

```text
A. direct pinned upstream Graphiti serial runner
B. MemBind project U0 wrapper
```

要求：

```text
same source episode order
same add_episode call count
same terminal episode coverage
same Native errors
same retrieval-call interface
```

若使用 deterministic fixture：

```text
canonical graph must match exactly
```

若使用 live LLM：

```text
不要求 graph bitwise equal；
只检查 harness 是否改变 upstream execution contract。
```

---

## 5.5 R5 — Published/public numeric reference

### 优先级 A：Exact reproduction

只有在以下全部可匹配时才允许使用：

```text
same dataset revision
same Graphiti/Zep implementation revision
same construction model
same embedding
same retrieval policy
same Reader
same Judge
same prompt
same evaluation script
```

此时可以运行公开代码并比较论文数字。

如果论文报告 mean ± std / 多 repeats：

```text
必须采用相同 repeat policy。
```

PASS：

```text
结果落入论文报告的 variance / CI；
或只存在可解释的极小工程差异。
```

输出状态：

```text
EXACT_REFERENCE_REPRODUCED
```

### 优先级 B：Protocol-aligned reproduction

如果 exact historical stack 不可获得，例如：

```text
GPT-4o historical snapshot unavailable
old Graphiti revision incompatible
Zep cloud implementation unavailable
```

则不能假装 exact reproduction。

必须：

```text
1. 使用 official dataset；
2. 使用 official evaluation semantics；
3. 使用 pinned current upstream Graphiti；
4. 使用完整记录的当前 local model stack；
5. 报告与公开 Graphiti/Zep/Graphiti-derived results 的差异；
6. 列出所有不匹配配置。
```

输出状态：

```text
PROTOCOL_ALIGNED_NOT_NUMERICALLY_MATCHED
```

这是合法的 qualification outcome。

### FAIL 条件

如果出现：

```text
Native QA / retrieval 严重异常
AND
无法由版本 / model / retrieval / judge difference 解释
```

则：

```text
REFERENCE_QUALIFICATION_FAILED
STOP
```

不得进入 P3。

---

## 5.6 Published references 的使用边界

允许作为 contextual reference：

```text
Zep / Graphiti LongMemEval results
Mnemis LongMemEval-S results
current Graphiti/Zep public benchmark documentation
```

但禁止：

```text
U0 必须达到 Mnemis 91.6
U0 必须达到 current proprietary Zep 90.2
U0 必须精确达到早期 Zep paper 的某个分数
```

原因：

```text
Mnemis 修改了 Graphiti；
current Zep != OSS Graphiti v0.29.3；
old Zep paper 使用早期 Graphiti。
```

---

# 6. P3 — Freeze Native Baseline

P1/P2 PASS 后生成：

```text
NATIVE_BASELINE_FREEZE.json
```

必须包含：

```text
Graphiti version / commit
source hashes
construction model
embedding model
Neo4j config
prompt/schema hashes
retry policy
HTTP/DB pool
Graphiti internal concurrency
retrieval config
Reader config
Judge config

qualification artifacts
reference reproduction status
reference configuration differences
```

从这一刻开始：

> **U0 不允许再因 MemBind 后续结果而修改。**

如果必须修 U0：

```text
increment protocol version
invalidate downstream paired runs
restart from P3/P4
```

---

# 7. P4 — Freeze Development / Held-out Split

完成 U0 freeze 后，才冻结最终 paper test。

定义：

```text
DEVELOPMENT
=
all previously exposed IDs
+
all IDs used in Native qualification/reproduction
+
all IDs used for method calibration
```

主 benchmark：

```text
LongMemEval-S cleaned
question_type == knowledge-update
non-abstention
```

最终：

```text
FINAL_HELDOUT
=
all eligible KU IDs
-
DEVELOPMENT
```

冻结：

```text
main_test_manifest.json
dataset SHA256
exact held-out IDs
N_quality
```

规则：

> 所有剩余 eligible held-out KU 都进入 Main Quality Surface。

不得根据 U0/MemBind 表现删题。

---

# 8. P5 — Calibrate Competing Methods

只能使用 DEVELOPMENT。

正式方法：

```text
U0  Native Graphiti Serial
A0  Native Async-Serial
P*  Best Qualified Whole-Update Parallel
M*  MemBind
```

---

## 8.1 A0

固定：

```text
durable FIFO enqueue
single Native worker
source-order publication
```

不需要 tuning。

---

## 8.2 P* Whole-Update Parallel

候选：

```text
C ∈ {2,4,8}
```

所有 C 使用：

```text
same upstream add_episode
same internal Graphiti config
same model stack
```

只改变 outer whole-update concurrency。

记录：

```text
successful goodput
makespan
direct hard invariant violations
retrieval quality
```

选择规则：

```text
qualified_set
=
all C with zero direct hard invariant violation

if qualified_set 非空:
    选择 successful goodput 最高的 C
    若在预冻结 2% tie tolerance 内，选择更小 C

if qualified_set 为空:
    选择 successful goodput 最高的 C
    method label = P-fastest-unqualified
```

不得隐藏 “unqualified”。

---

## 8.3 M* MemBind

候选：

```text
C ∈ {2,4,8}
```

进入候选必须：

```text
zero direct hard invariant violation
deterministic correctness qualification passed
no known semantic-parity defect
```

从 qualified set 选择 successful goodput 最高的 C。

2% tie tolerance 内选择更小 C。

如果没有 qualified C：

```text
STOP_MAIN_EXPERIMENT
```

---

## 8.4 Method freeze

输出：

```text
method_selection_manifest.json
```

从此：

```text
C_P
C_M
A0 behavior
```

全部固定。

Final test 不得重新 tuning。

---

# 9. P6 — Freeze Main Stack + Workload

## 9.1 Frozen stack

四种方法共享：

```text
Graphiti commit
construction LLM
embedding
Neo4j
GPU/machine
vLLM
prompt/schema
retry
HTTP pool
DB pool
Graphiti internal concurrency
retrieval
Reader
Judge
```

唯一 treatment：

```text
memory-construction runtime schedule
```

---

## 9.2 Main offered load

只使用 DEVELOPMENT Native traces 决定。

对每个 development history `h`：

```text
S_h
=
mean valid U0 add_episode service time
```

然后：

```text
S_ref_main
=
median_h(S_h)

main_interarrival
=
S_ref_main
```

即：

```text
normalized offered-load point ≈ 1.0
```

这里只是：

```text
pre-frozen Native-service-rate reference point
```

不得称为真实生产 utilization 或真实 workload arrival distribution。

---

# 10. P7 — Main Quality Surface

## 10.1 范围

使用：

```text
ALL FINAL_HELDOUT KU histories
```

方法：

```text
U0
A0
P*
M*
```

每个：

```text
(history, method)
```

构建一次 fresh memory。

---

## 10.2 Quality metrics

同一 construction output 上执行：

```text
final graph
→ frozen retrieval
→ frozen Reader
→ hypothesis
→ frozen LongMemEval evaluator
```

报告：

```text
QA Accuracy ↑
Evidence Recall@10 ↑
```

只有在 Graphiti result 可以无歧义映射回官方 session ID 时才计算 Evidence Recall。

不得使用 heuristic mapping 后仍声称 official Recall。

---

## 10.3 Direct correctness

同时记录：

```text
lost_episode_count
duplicate_episode_count
transaction_failure_count
publication_loss_count
source/provenance_violation_count
temporal_invariant_violation_count
```

主表：

```text
Direct Violations ↓
```

不是简单 `✓/✗`。

---

# 11. P8 — Main Performance Surface

Quality 需要最大样本覆盖；systems performance 需要 run-to-run variance。

因此 performance 单独使用预冻结 subset，但仍属于同一个 Main Experiment。

---

## 11.1 Performance subset

从 `FINAL_HELDOUT` 中：

1. 根据 history token count 排序；
2. 分成 4 个 size quartiles；
3. 每个 quartile 按 `SHA256(question_id)` 选前 4 个；
4. 共选择：

```text
N_perf = min(16, N_quality)
```

如果 `N_quality < 16`：

```text
使用全部 held-out histories
```

选择过程必须在 method performance outcome 前完成。

输出：

```text
main_performance_subset.json
```

---

## 11.2 Repetitions

每个：

```text
history × method
```

固定：

```text
3 independent formal repetitions
```

不得：

```text
只有看到 variance 大才补第 3 次
```

---

## 11.3 Arrival schedule

对于每次 repeat：

```text
arrival(E_i)
=
t0 + i * main_interarrival
```

arrival generator 独立于 method service completion。

禁止：

```text
等待 Native 完成
→ 再生成下一条 arrival
```

---

## 11.4 Performance metrics

Primary：

```text
P95 arrival-to-publish latency ↓
successful construction goodput ↑
history makespan ↓
```

Secondary：

```text
max backlog
backlog at final arrival
drain time
queue wait
caller return latency
```

注意：

```text
A0 caller latency
!=
memory freshness latency
```

---

# 12. Work-Volume Fairness

每个 formal run 记录：

```text
LLM calls
input tokens
output tokens
structured retries

embedding calls
embedding item count

DB queries
DB transactions
DB writes

HTTP requests
transport retries
```

如果方法间 work volume 不对称：

```text
必须披露。
```

不能把明显“少做工作”导致的速度提升直接称为 scheduling speedup。

---

# 13. Paired Block + Run Order

一个 block：

```text
history_id × repeat_id
```

包含：

```text
U0
A0
P*
M*
```

正式运行前使用固定 seed 生成 balanced Latin-square/permutation schedule。

要求：

> 四个方法在 run position 1/2/3/4 的出现次数尽可能均衡。

禁止：

```text
all U0 first
all MemBind last
```

---

# 14. Failure Policy

## 14.1 Infrastructure failure

包括：

```text
vLLM process outage
embedding process outage
Neo4j outage
machine/network interruption
artifact filesystem failure
```

处理：

```text
保留失败 evidence
标记 INFRA_FAILURE
整个 paired block 作废
生成新 block instance
四个 methods 全部重新运行
```

禁止只重跑某一个 method。

---

## 14.2 Treatment-induced failure

例如：

```text
parallel transaction conflict
episode lost
duplicate
publication loss
temporal violation
method cannot drain
```

处理：

```text
保留为 method scientific outcome
```

不得当作 infra failure 删除。

---

# 15. Statistics

## 15.1 独立统计单位

唯一 primary unit：

```text
history / question_id
```

episode 不能作为独立 sample。

---

## 15.2 Quality

报告：

```text
QA Accuracy per method

ΔAcc(M* - U0)
ΔAcc(P* - U0)

paired 95% CI
McNemar test
```

不得使用：

```text
p > 0.05
→ 两者等价
```

如果以后要正式声称 non-inferiority：

```text
必须在 final outcome 前单独冻结 margin。
```

---

## 15.3 Performance

对每个 history 先跨 3 repeats 汇总，例如：

```text
median P95 freshness
median goodput
median makespan
```

再做 history-level paired comparison。

报告：

```text
median paired ratio
geometric mean paired ratio
95% cluster-bootstrap CI
```

bootstrap：

```text
unit = history
samples = 10000
seed = frozen
```

---

# 16. Headline Main Table

论文最终只生成一张 headline table：

| Method | QA Acc ↑ | Evidence R@10 ↑ | Direct Violations ↓ | P95 Freshness ↓ | Goodput ↑ | Makespan ↓ |
|---|---:|---:|---:|---:|---:|---:|
| U0 Native Serial |  |  |  |  | 1.00× | 1.00× |
| A0 Async-Serial |  |  |  |  |  |  |
| P* Whole-Update Parallel |  |  |  |  |  |  |
| **M* MemBind** |  |  |  |  |  |  |

表下注明：

```text
Quality:
    all FINAL_HELDOUT KU histories
    one construction per method/history

Performance:
    pre-frozen stratified subset
    up to 16 histories
    3 formal repetitions per method/history

Goodput / Makespan:
    absolute value + relative-to-U0 ratio
```

不能让读者误以为 quality 和 performance 来自不同 benchmark。

它们来自：

```text
same FINAL_HELDOUT population
same methods
same frozen stack
```

performance 只是为了方差控制使用其预冻结 subset。

---

# 17. Main Experiment 完成后的解释

## STRONG_SUPPORT

满足：

```text
M* zero direct violations
retrieval/QA close to U0
P95 freshness materially lower than U0
goodput materially higher than U0
comparison against A0/P* favorable
work-volume differences cannot explain the gain
```

## PARTIAL_SUPPORT

例如：

```text
speedup modest
QA uncertainty large
P* matches M*
only subset of histories benefits
```

## HEADLINE_NOT_SUPPORTED

例如：

```text
MemBind direct correctness failure
material quality degradation
no meaningful freshness/goodput improvement
gain dominated by asymmetric work reduction
```

不得为了改变 verdict 调整 final-test protocol。

---

# 18. Artifact Layout

必须生成：

```text
artifacts/paper_main/
├── p0_current_state_snapshot.json
├── DEVELOPMENT_EXPOSED_IDS.json
│
├── native_reference/
│   ├── native_identity_manifest.json
│   ├── instrumentation_parity.json
│   ├── functional_qualification.json
│   ├── dataset_parity.json
│   ├── evaluator_parity.json
│   ├── upstream_execution_crosscheck.json
│   └── reference_reproduction_report.md
│
├── NATIVE_BASELINE_FREEZE.json
├── main_test_manifest.json
├── main_performance_subset.json
├── method_selection_manifest.json
├── main_stack_manifest.json
├── method_order_manifest.json
│
├── blocks/
│   └── <question_id>/
│       └── <repeat_id>/
│           ├── U0/
│           ├── A0/
│           ├── P/
│           └── M/
│
├── qa_results.jsonl
├── retrieval_results.jsonl
├── direct_correctness.jsonl
├── work_volume.jsonl
├── per_history_metrics.jsonl
├── statistical_summary.json
├── main_table.csv
└── MAIN_EXPERIMENT_REPORT.md
```

所有 finalized artifact 必须记录：

```text
SHA256
git commit
protocol version
producer
run/block ID
terminal status
```

禁止写入 secret。

---

# 19. Agent 的严格执行顺序

Agent 必须逐阶段执行，不得越级。

```text
STEP 1
Snapshot current state
↓
STOP if current environment identity cannot be established

STEP 2
Implement/verify pure U0 Native
↓
Run upstream identity qualification
↓
Run instrumentation parity
↓
Run functional qualification
↓
STOP on failure

STEP 3
Pin official LongMemEval
↓
Run dataset mapping parity
↓
Run evaluator parity
↓
Run upstream execution cross-check
↓
Attempt exact public reference reproduction if exact stack exists
otherwise perform protocol-aligned reproduction
↓
Generate reference_reproduction_report.md
↓
STOP on unexplained major failure

STEP 4
Freeze U0
↓
Write NATIVE_BASELINE_FREEZE.json

STEP 5
Finalize DEVELOPMENT_EXPOSED
↓
Freeze FINAL_HELDOUT KU
↓
Write main_test_manifest.json

STEP 6
Calibrate P* and M* on DEVELOPMENT only
↓
Freeze C_P and C_M
↓
Write method_selection_manifest.json

STEP 7
Freeze main system stack
↓
Derive main_interarrival from DEVELOPMENT U0 only
↓
Freeze run order
↓
Freeze performance subset

STEP 8
Run Main Quality Surface over ALL FINAL_HELDOUT

STEP 9
Run Main Performance Surface
up to 16 histories × 4 methods × 3 repeats

STEP 10
Compute paired statistics

STEP 11
Generate one headline main table

STEP 12
Generate MAIN_EXPERIMENT_REPORT.md

STEP 13
STOP
```

Agent 不得自动继续：

```text
load sweep
concurrency sensitivity
D0 deterministic correctness experiment
FactConsolidation
LoCoMo
MemoryArena
ablation
new method design
```

这些不属于本文档。

---

# 20. 最终论文 claim 边界

如果该主实验成功，允许的 headline claim 是：

> **On the frozen Graphiti temporal-memory stack and held-out LongMemEval Knowledge-Update workload, MemBind improves memory-construction freshness and successful goodput over Native and simple execution baselines while preserving Native-level downstream memory utility and observing no direct stateful-memory correctness violations.**

如果后续没有第二个 memory backend / broader benchmark：

禁止写：

```text
MemBind is universally effective for all Agent Memory systems.
```

---

# 21. 关键文献对本计划各设置的对应关系

| 本计划设置 | 依据 |
|---|---|
| 使用 official LongMemEval cleaned dataset / evaluator | LongMemEval, ICLR 2025 |
| baseline 先 reproduction / protocol qualification | MemoryAgentBench, ICLR 2026；A-Mem, NeurIPS 2025 的公开 reproduction workflow |
| tuning 只在 development 上完成后 freeze | A-Mem, NeurIPS 2025 的公开 k-sweep / reproduction protocol |
| 主结果同时报告 systems gain + task quality | HIPPOCAMPUS, MLSys 2026 |
| matched workload 下比较 latency / throughput | Agentix, NSDI 2026 |
| Graphiti-based downstream benchmark 需要 QA，而不能只看 graph | Mnemis, ACL 2026 Main |
| 不把 Mnemis 当 Native Graphiti target | Mnemis 明确修改 base graph extraction + hierarchical retrieval |
| 不硬复现早期 Graphiti/Zep 数值 | Graphiti maintainer 明确说明早期 paper 使用 very early Graphiti |
| history 作为 primary statistical unit | stateful evolving-memory workload 的依赖结构；避免 episode-level pseudo-replication |
| performance 使用 fixed repeats | systems evaluation 需要报告 run-to-run variability；避免 single-run 偶然性 |

---

# 22. 最终原则

整个执行流程可以压缩成一句话：

> **先证明 Native baseline 是真正的 upstream Graphiti，并通过官方 benchmark / public reference qualification；冻结这把尺子；再在 development 上公平选择 competing configurations；最后只在 held-out workload 上进行一次统一的 end-to-end main experiment，并用一张主表同时展示 quality、correctness 和 systems performance。**
