# MemBind Paper-Level Evaluation Workplan v3.0
## Small-First / Baseline-First / Reuse-Aware Final Protocol

> **日期**：2026-08-13  
> **状态**：从当前 C0–C5 已完成状态开始执行  
> **目标**：面向 MLSys / NSDI / OSDI 风格系统论文，先建立可信 Native baseline，再以小规模验证方法有效性，最后按 pilot 方差与置信区间需求冻结正式 paper-scale evaluation。  
> **重要约束**：
> - 不执行 C6。
> - 不修改、删除或重写已有 C0–C5/C6 历史合同与 artifact。
> - C0–C5 作为已有 characterization evidence 保留。
> - 新协议从本文档开始。
> - 不允许因为最终结果“不够显著”而追加样本；扩大规模只能依据预先定义的 precision / variance planning 规则。

---

# 1. 论文主问题

最终论文主实验只回答：

> **在相同 Graphiti、construction LLM、Embedding、Neo4j、硬件与 LongMemEval workload 下，相比 Native Serial、Async-Serial 和 Naive Whole-Update Parallel，MemBind 能否在保持 Native-level memory quality 与 stateful-memory correctness 的同时，降低 memory freshness latency 并提高 successful construction goodput？**

最终 headline 表：

| Method | QA Acc ↑ | Evidence R@10 ↑ | Direct Violations ↓ | P95 Freshness ↓ | Goodput ↑ | Makespan ↓ |
|---|---:|---:|---:|---:|---:|---:|
| U0 Native Serial | | | | | 1.00× | 1.00× |
| A0 Async-Serial | | | | | | |
| P* Whole-Update Parallel | | | | | | |
| **M* MemBind** | | | | | | |

---

# 2. 已有 C0–C5 内容如何复用

## 2.1 直接复用，不重做

若代码/config hash 未发生相关漂移，则直接复用：

```text
C0:
    host / serving viability evidence

C1:
    instrumentation qualification
    median overhead = 1.317%
    clean_pass

C2:
    measurement harness
    phase boundaries
    telemetry schema
    checkpoint / JSONL format
    4 calibration histories
    188 episodes

C3:
    dependency_map
    interval classifier
    arrival-ready / Amdahl analysis framework

C5:
    per-episode durability/checkpoint framework
    naive whole-update concurrency implementation
    problem evidence only
```

### 规则

不得因为进入 paper-level protocol 而重新做已经被 hash/provenance 证明等价的基础设施 qualification。

---

## 2.2 只作为历史证据，不直接作为正式 baseline 结果

```text
C2 timing numbers
C3 quantitative Native bounds
C4 result
C5 performance/correctness result
```

用途：

```text
motivation / characterization / engineering evidence
```

不得直接充当：

```text
formal U0 vs MemBind main-result cells
```

特别地：

- C4 当前不是正式可合并主实验结果；
- C5 的 whole-update parallel 是问题证据，不是 MemBind solution；
- C5 中观察到的 source-order violation 后续可作为 P* 的设计依据，但不能替代正式 P* test。

---

# 3. 数据角色

从现在开始所有真实 benchmark IDs 必须属于以下之一：

```text
DEVELOPMENT_EXPOSED
PILOT
FINAL_PAPER_TEST
```

三者互斥。

## 3.1 DEVELOPMENT_EXPOSED

包括：

- C0–C5 已使用 histories；
- Judge qualification 用过的真实 benchmark items；
- debugging / canary；
- Native qualification；
- U0/D0 calibration；
- P*/M* tuning；
- 人工查看过 method-specific outcome 的实例。

## 3.2 PILOT

用于：

- 验证完整 U0/A0/P*/M* pipeline；
- 检查 effect direction；
- 估计 history-to-history variance；
- 估计 run-to-run performance noise；
- 决定是否值得进入正式 paper evaluation；
- 为正式 N planning 提供 variance estimate。

**PILOT 不进入最终 paper primary statistical test。**

一旦查看 pilot outcome，其 IDs 永久转为 exposed。

## 3.3 FINAL_PAPER_TEST

只有在 pilot 完成且决定继续后才冻结。

必须与：

```text
DEVELOPMENT_EXPOSED
PILOT
```

完全不重叠。

---

# 4. Benchmark

当前核心 benchmark：

```text
LongMemEval-S cleaned
question_type == "knowledge-update"
non-abstention
```

必须 pin：

```text
dataset revision
dataset SHA256
official evaluator revision
official evaluator SHA256
```

保持官方：

```text
question_id
question_type
haystack_session_ids
haystack_dates
answer_session_ids
question
answer
```

不得自行重写 benchmark 数据语义。

---

# 5. 方法定义

## U0 — Upstream Native Graphiti Serial

Headline Native baseline。

```text
Ei
→ pinned upstream Graphiti add_episode(Ei)
→ Ei reaches Native publication boundary
→ Ei+1
```

仅允许：

```text
passive instrumentation
timestamps
work counters
checkpoint/artifact
error classification
```

禁止：

```text
project-only candidate sorting
response replay
embedding replay
semantic cache modification
resolution modification
invalidation modification
publication semantic modification
retrieval modification
```

---

## D0 — Deterministic Serial Control

D0 **不是 headline Native baseline**。

用途：

```text
internal representativeness / correctness control
```

它在 U0 基础上加入声明过的 deterministic stabilization，使后续 correctness reasoning 不被 LLM / candidate-order nondeterminism 污染。

D0 仅出现在 baseline qualification / correctness supporting experiment，不进入 headline performance table。

---

## A0 — Native Async-Serial

```text
arrival
→ FIFO enqueue
→ caller can return
→ one Native worker
→ source-order publication
```

构建逻辑必须与 U0 相同。

---

## P* — Naive Whole-Update Parallel

并发完整：

```text
upstream add_episode()
```

candidate grid：

```text
C ∈ {1,2,4,8}
```

P* 的角色是展示 naive concurrency 的真实 performance–correctness trade-off。

不得因为某个 C 有 violation 就把它从性能比较中静默删除。

---

## M* — MemBind

candidate grid：

```text
C ∈ {1,2,4,8}
```

M* 必须满足：

```text
zero direct hard invariant violation
deterministic correctness qualification passed
zero oracle/fallback miss where replay is required
```

否则不能进入正式 paper main experiment。

---

# 6. Stage S0 — Current-State / Reuse Audit

## 目的

只确认：

```text
哪些 C0-C5 artifact 可以直接复用
哪些因为代码/config drift 必须补验
```

## 必须输出

```text
artifacts/paper_eval/
├── S0_CURRENT_STATE.json
├── S0_REUSE_AUDIT.json
└── DEVELOPMENT_EXPOSED_IDS.json
```

## Audit 至少检查

```text
current git commit
working tree
Graphiti version/commit
construction model
embedding model
vLLM
Neo4j
instrumentation code hashes
phase_map hash
C1 artifact hash
C2 harness hash
C2 frozen calibration IDs
Judge/Reader identities
```

### 复用判定

如果：

```text
same relevant code hash
AND same semantic config
AND same model/runtime identity
```

则：

```text
REUSE
```

否则：

```text
REQUALIFY_ONLY_AFFECTED_COMPONENT
```

禁止无条件从零重做。

---

# 7. Stage S1 — Native U0 Minimal Smoke

这是新的第一项真实运行工作。

## 数据

从既有 `DEVELOPMENT_EXPOSED` calibration manifest 中：

```text
固定选择 manifest 中排序后的第 1 个 history
```

禁止根据其长度或预期性能临时挑选。

## 运行

```text
1 history
×
direct pinned upstream Graphiti
×
instrumented U0 wrapper
```

## 验证

```text
episode count
source order
add_episode call count
completion
lost/duplicate
exception semantics
retrieval callable
artifact completeness
```

如果 deterministic fixture 可用：

```text
canonical graph exact parity
```

## PASS

必须：

```text
0 lost
0 duplicate
100% episode coverage
same upstream call contract
retrieval succeeds
artifacts complete
```

FAIL：

```text
STOP
修 baseline
不得扩大数据规模
```

---

# 8. Stage S2 — Native U0 Qualification + Reference Alignment

S1 PASS 后才能进入。

## 8.1 先做 protocol alignment，不先烧完整 construction

### Dataset parity

在 development IDs 上验证：

```text
question_id exact
question_type exact
session IDs exact
timestamp order exact
answer_session_ids exact
question/answer hash exact
```

要求：

```text
100% parity
```

### Evaluator parity

用固定 development hypothesis fixture：

```text
official LongMemEval prompt/rubric path
vs
MemBind LongMemEvalAdapter
```

验证：

```text
question-type routing
prompt content/hash
headline yes/no semantics
aggregate label semantics
```

要求：

```text
100% rubric/evaluator semantic parity
```

Judge backend 若不同：

```text
单独披露，不算 evaluator-semantic mismatch
```

---

## 8.2 审计 C2 能否直接作为 U0 4-history qualification evidence

检查 C2：

```text
actual Graphiti code path
project patches
candidate ordering
cache behavior
model/embedding identities
instrumentation
fresh namespace semantics
```

### Case A — C2 已经等价于纯 U0

如果可以用 hash/provenance 明确证明：

```text
C2 execution path == U0 contract
```

则：

```text
复用 C2 的 4-history / 188-episode execution evidence
不重复完整 construction
```

只补：

```text
retrieval / QA reference sanity
missing provenance checks
```

### Case B — 无法证明或存在 drift

先补：

```text
1-history U0 formal qualification
```

PASS 后才扩到其余 calibration histories。

最终最多：

```text
4 calibration histories
188 episodes
```

但绝不能在 1-history 失败时继续跑剩余数据。

---

## 8.3 Numeric reference sanity

当前 U0 必须产生真实：

```text
Evidence Recall@10
QA Accuracy
```

用于 sanity check。

### MUST

```text
official cleaned LongMemEval data
official task/evaluation semantics
pinned current upstream Graphiti
fully documented current local stack
```

### BEST EFFORT

只有当以下全部能匹配时才声称 exact numeric reproduction：

```text
same Graphiti/Zep revision
same construction model
same embedding
same retrieval
same Reader
same Judge
same prompt
same evaluation script
```

否则状态必须写：

```text
PROTOCOL_ALIGNED_NOT_NUMERICALLY_MATCHED
```

### 不设置旧论文硬分数门槛

禁止：

```text
U0 must reach Mnemis 91.6
U0 must match early Zep within ±X pp
```

但如果 U0 出现明显异常量级结果：

```text
例如 pipeline 几乎全部失败、retrieval near-zero、QA near-zero
```

且不能由已记录 stack difference 解释：

```text
STOP
root-cause
```

---

# 9. Stage S3 — Freeze Native U0

S1/S2 PASS 后：

```text
NATIVE_BASELINE_FREEZE.json
```

至少包含：

```text
Graphiti version/commit
critical source hashes
model/embedding
Neo4j
vLLM
prompt/schema
retry
pooling
cache
retrieval
Reader
Judge
instrumentation
reference-alignment status
reference sanity result
all config hashes
```

之后：

> **不得因 MemBind 后续结果修改 U0。**

---

# 10. Stage S4 — D0 Minimal Smoke and Qualification

U0 freeze 后再做 D0。

## 10.1 1-history smoke

使用同一个 development calibration history：

```text
U0 model/embedding capture
→ D0 read-only deterministic replay/control
```

验证：

```text
zero oracle miss
zero fallback
same episode coverage
same work contract
```

如果 D0 的 deterministic adapters 声称只消除非语义 nondeterminism：

```text
canonical graph parity 必须达到 100%
```

若不一致：

```text
逐项分类
不得仅用 F1>=0.95 直接放行
```

---

## 10.2 4-history qualification

只有 1-history smoke PASS 后：

```text
4 development histories
```

### Hard guardrails

```text
zero oracle miss/fallback
100% episode/source coverage
LLM call-count contract preserved
no hidden semantic fallback
```

### Work-volume guardrail

可继续使用项目预注册：

```text
token/work ratio ∈ [0.95, 1.05]
```

但必须在文档中写明：

> 这是 MemBind project-specific fairness guardrail，不是领域统一标准。

### Retrieval / QA

在 4 histories 上：

```text
paired descriptive comparison
```

不使用：

```text
D0 >= U0 - 1pp
```

作为“领域标准”。

如果出现任何差异：

```text
逐 history 检查
分类为 stochastic / adapter / semantic
```

D0 只有在没有未解释 semantic drift 时才 freeze。

---

# 11. Stage S5 — Solution / Baseline Minimal Smoke

在扩大 tuning 之前，所有新方法先用：

```text
1 development history
```

跑通。

顺序：

```text
A0 smoke
P concurrency runner smoke
M* smoke
```

## A0

验证：

```text
FIFO
single worker
same U0 construction path
source-order publication
caller-return 与 publish 时间分离正确
```

## P

至少验证：

```text
C=2
```

确认：

```text
whole-update concurrency 真正存在
telemetry 正确
violation checker 正常
```

C5 可复用 implementation/checkpoint framework。

## M*

验证：

```text
complete construction
zero direct invariant violation
correct freshness timestamping
zero unexpected fallback
```

如果 M* smoke FAIL：

```text
STOP
不得开始 C sweep
```

---

# 12. Stage S6 — Development-Only Concurrency Calibration

数据：

```text
现有 4 development/calibration histories
```

不得增加 fresh held-out 数据用于 tuning。

## P

```text
C ∈ {1,2,4,8}
```

记录：

```text
successful goodput
P95 freshness
makespan
direct violations
lost/duplicate
work volume
```

最终 P*：

```text
选择 median successful goodput 最高的 C
```

即使该 C 有 violation：

```text
仍可作为 P* performance baseline
但 violation 必须进入主表
```

不得称其 semantics-preserving。

---

## M*

```text
C ∈ {1,2,4,8}
```

qualified set：

```text
zero direct hard violation
deterministic correctness gate PASS
no hidden fallback
```

从 qualified set 中选择：

```text
median successful goodput 最高的 C
```

若并列 exact tie：

```text
选择更小 C
```

如果没有 qualified C：

```text
STOP
返回 mechanism diagnosis
```

输出：

```text
METHOD_SELECTION_FREEZE.json
```

---

# 13. Stage S7 — Bounded Pilot

## 目的

不是论文最终显著性实验。

只回答：

```text
方法是否有正向 systems signal？
机制是否稳定？
history-to-history variance 多大？
run-to-run noise 多大？
是否值得扩大正式实验？
```

---

## 13.1 Pilot 数据

从剩余未暴露 KU 中：

1. 使用 outcome-independent history size 指标（session count/token count）；
2. 分 4 个 quartile；
3. 每 quartile 按 SHA256(question_id) 选择 2 个；
4. 共：

```text
8 PILOT histories
```

在运行前 freeze：

```text
PILOT_MANIFEST.json
```

---

## 13.2 Pilot 第一轮

```text
8 histories
×
U0 / A0 / P* / M*
×
1 construction
```

同一 frozen stack。

得到：

```text
QA Accuracy
Evidence R@10
Direct Violations
P95 Freshness
Goodput
Makespan
```

---

## 13.3 Pilot performance-noise 子集

在看到结果前，从 8 个 pilot histories 中：

```text
每 quartile 选 1 个
= 4 histories
```

这 4 个运行：

```text
4 histories
× 4 methods
× 3 fixed repeats total
```

第一轮计入 repeat 1，只补 repeat 2/3。

用途：

```text
估计 run-to-run systems variance
```

---

## 13.4 Pilot GO / NO-GO

不得使用：

```text
p-value 是否 < 0.05
```

决定是否扩样本。

### Correctness hard gate

如果 M* 出现：

```text
direct hard invariant violation
oracle/fallback miss
unexplained semantic drift
```

则：

```text
NO-GO
进入诊断
```

### Performance signal

计算每 history：

```text
freshness_ratio = M*/U0
goodput_ratio   = M*/U0
```

如果：

```text
median freshness_ratio >= 1.0
AND
median goodput_ratio <= 1.0
```

即两项都没有正向方向：

```text
NO-GO_FOR_SCALING
```

否则：

```text
GO_TO_SAMPLE_SIZE_PLANNING
```

### Quality

Pilot QA/R@10：

```text
只用于发现明显 failure pattern 和估计 heterogeneity
不用于最终论文 non-inferiority claim
```

任何 method-specific 异常必须记录。

---

# 14. Stage S8 — Formal Paper Sample-Size Planning

Pilot 完成后：

```text
8 PILOT IDs → 永久 exposed
```

不得放入 final paper primary analysis。

## 原则

正式 N 不按：

```text
“顶会一般要20个”
“pilot p=0.08所以再加一些”
```

决定。

而按：

```text
pilot observed variance
+
预冻结 precision target
+
剩余 eligible population
```

决定。

---

## 14.1 Performance N

使用 pilot 的：

```text
per-history log(M*/U0 goodput ratio)
per-history log(M*/U0 freshness ratio)
run-to-run noise
```

做 bootstrap precision simulation。

candidate N：

```text
{8,12,16,20,24,...}
```

从剩余 eligible KU 中模拟 stratified samples。

默认 precision target：

```text
geometric-mean speedup 95% CI
multiplicative half-width <= 1.15
```

解释：

```text
例如 point estimate 2.0×，
目标 CI 大致不宽于约 [2/1.15, 2*1.15]。
```

这是**本项目预注册 precision target**，不是顶会统一标准。

选择满足两个 primary performance metrics 中更大需求的最小 N：

```text
N_perf
```

若整个剩余 KU population 都无法达到目标：

```text
使用全部可用 remaining eligible histories
并如实报告 CI
```

---

## 14.2 Quality N

Quality 不根据 pilot accuracy 的显著性做 adaptive expansion。

优先策略：

```text
N_quality >= N_perf
```

如果预算允许：

```text
使用全部 remaining FINAL_PAPER_TEST KU
```

如果预算不允许：

```text
使用与 N_perf 相同或更大的
预冻结 stratified sample
```

并在论文中明确 claim boundary。

---

# 15. Stage S9 — Freeze Formal Paper Evaluation

冻结：

```text
FINAL_PAPER_TEST_MANIFEST.json
N_quality
N_perf
performance subset
all method configs
main_interarrival
run-order schedule
bootstrap seed
Judge/Reader
retry/failure policy
```

从此：

```text
no tuning
no method modification
no sample addition based on outcome
```

任何方法修改：

```text
protocol version bump
旧 formal results 作废
```

---

# 16. Stage S10 — Formal Paper Main Experiment

## Quality surface

```text
N_quality
×
U0/A0/P*/M*
×
1 construction
```

报告：

```text
QA Accuracy
Evidence R@10
Direct Violations
```

## Performance surface

```text
N_perf
×
U0/A0/P*/M*
×
3 fixed repeats
```

主 operating point：

```text
normalized offered load ≈ 1.0
```

绝对 arrival interval 必须在 development 阶段由 frozen U0 service reference 推导。

报告：

```text
P95 arrival-to-publish
successful goodput
makespan
max backlog
```

---

# 17. Formal Run Order

block：

```text
(history_id, repeat_id)
```

包含：

```text
U0
A0
P*
M*
```

使用预生成 balanced permutation / Latin-square style order。

禁止：

```text
先跑完所有 U0
再跑所有 MemBind
```

---

# 18. Failure Policy

## Pilot

允许更便宜的工程策略：

```text
transient single-method network error
→ immediate bounded retry
→ 完整记录 retry trail
```

---

## Formal performance experiment

### Immediate retry window

如果 transient error 在同一 block 时间窗口内发生：

```text
允许按 frozen retry policy 原地重试
```

### Block-level infrastructure failure

例如：

```text
server process crash
Neo4j outage
machine/network interruption
长时间服务异常
```

处理：

```text
保留失败 artifacts
invalidate entire paired block
新 block instance
四种 methods 全部重跑
```

### Treatment-induced failure

例如：

```text
parallel transaction conflict
lost/duplicate
publication failure
method cannot drain
semantic invariant violation
```

处理：

```text
正式 scientific outcome
不得当 infra failure 删除
```

---

# 19. Work-Volume Fairness

每个 formal run 记录：

```text
LLM calls
input/output tokens
embedding calls/items
DB queries/writes/transactions
HTTP requests
retry counts
```

速度提升如果伴随明显 work reduction：

```text
必须披露
```

不得无条件写：

```text
pure scheduling speedup
```

---

# 20. 统计单位与分析

## Primary unit

```text
history / question_id
```

episode 不是 independent sample。

## Performance

每个 history：

```text
先在 3 repeats 内取 median
再计算 method/U0 paired ratio
```

报告：

```text
median paired ratio
geometric-mean paired ratio
95% history-cluster bootstrap CI
```

固定：

```text
bootstrap samples = 10000
seed = frozen
```

## QA

报告：

```text
Accuracy
paired ΔAccuracy
95% paired CI
McNemar test
```

禁止：

```text
p>0.05 → equivalent
```

如果要正式 claim non-inferiority：

```text
必须在 formal outcomes 前另行冻结 margin
```

---

# 21. Headline Main Table

最终只使用 **FINAL_PAPER_TEST** 生成：

| Method | QA Acc ↑ | Evidence R@10 ↑ | Direct Violations ↓ | P95 Freshness ↓ | Goodput ↑ | Makespan ↓ |
|---|---:|---:|---:|---:|---:|---:|
| U0 Native Serial | | | | | 1.00× | 1.00× |
| A0 Async-Serial | | | | | | |
| P* Whole-Update Parallel | | | | | | |
| **M* MemBind** | | | | | | |

表注必须写：

```text
Quality N = N_quality
Performance N = N_perf histories × 3 repeats
Pilot data excluded from formal primary statistics
```

---

# 22. 停止条件

任何阶段发生以下情况：

```text
U0 identity 无法证明
official benchmark/evaluator parity 失败
U0 sanity result 严重异常且无法解释
D0 存在未解释 semantic drift
M* direct correctness smoke 失败
M* 无任何 qualified concurrency
pilot performance 两个 primary directions 均不优于 U0
```

则：

```text
STOP / DIAGNOSE
```

不得通过简单扩大 N 来“救结果”。

---

# 23. Artifact Layout

```text
artifacts/paper_eval/
├── S0_CURRENT_STATE.json
├── S0_REUSE_AUDIT.json
├── DEVELOPMENT_EXPOSED_IDS.json
│
├── native/
│   ├── U0_SMOKE.json
│   ├── DATASET_PARITY.json
│   ├── EVALUATOR_PARITY.json
│   ├── C2_U0_REUSE_DECISION.json
│   ├── U0_REFERENCE_SANITY.json
│   └── NATIVE_BASELINE_FREEZE.json
│
├── d0/
│   ├── D0_SMOKE.json
│   ├── D0_QUALIFICATION.json
│   └── D0_FREEZE.json
│
├── methods/
│   ├── METHOD_SMOKE.json
│   ├── CALIBRATION_RESULTS.json
│   └── METHOD_SELECTION_FREEZE.json
│
├── pilot/
│   ├── PILOT_MANIFEST.json
│   ├── PILOT_RESULTS.json
│   ├── PILOT_VARIANCE.json
│   └── PILOT_VERDICT.md
│
├── planning/
│   ├── SAMPLE_SIZE_PRECISION_ANALYSIS.json
│   └── FINAL_PAPER_TEST_MANIFEST.json
│
└── formal/
    ├── blocks/
    ├── qa_results.jsonl
    ├── retrieval_results.jsonl
    ├── direct_correctness.jsonl
    ├── performance_results.jsonl
    ├── work_volume.jsonl
    ├── statistics.json
    ├── MAIN_TABLE.csv
    └── MAIN_EXPERIMENT_REPORT.md
```

所有 finalized artifact：

```text
SHA256
git commit
protocol version
run/block ID
terminal status
```

---

# 24. Agent 严格执行顺序

```text
S0  Audit C0-C5 reuse
↓
S1  1-history U0 smoke
↓ PASS
S2  Official LongMemEval protocol alignment
    + C2 U0 reuse audit
    + only missing U0 qualification
    + numeric sanity
↓ PASS
S3  Freeze U0
↓
S4  1-history D0 smoke
    → 4-history D0 qualification
↓ PASS
S5  1-history A0/P/M* smoke
↓ PASS
S6  4-history development-only C sweep
    → freeze P*/M*
↓
S7  8-history bounded pilot
    + pre-frozen 4-history repeat subset
↓
S8  variance/precision-based formal N planning
↓
S9  freeze FINAL_PAPER_TEST
↓
S10 formal quality + performance evaluation
↓
generate headline main table
↓
STOP
```

Agent 不得自动进入：

```text
load sweep
LoCoMo
MemoryAgentBench
MemoryArena
new method design
ablation
second backend
```

这些属于主实验之后的 supporting/generalization evaluation。

---

# 25. 该计划与近期工作的方法学对应

## LongMemEval — ICLR 2025

采用：

- official cleaned benchmark；
- online timestamped history；
- official QA evaluator；
- `answer_session_ids` 做 session-level retrieval evaluation。

因此本计划以 official data/evaluator parity 作为 Native reference qualification 的硬门。

## A-Mem — NeurIPS 2025

公开 reproduction repository 提供：

```text
--ratio 0.1
```

用于 quick test，并单独运行 full evaluation / hyperparameter sweep。

因此本计划采用：

```text
smoke
→ calibration
→ pilot
→ formal
```

而不是一开始直接扩大正式 workload。

## MemoryAgentBench — ICLR 2026

公开 main-experiment reproduction code，并采用：

```text
inject once, query multiple times
```

降低 evaluation 成本。

因此昂贵 memory construction 与后续 evaluation 应尽量解耦并持久化。

## Agentix — NSDI 2026

在相同 workload 与 baseline 条件下以 latency / throughput improvement 作为 headline result。

因此 MemBind 最终主表必须突出：

```text
freshness
goodput
```

同时保持相同 stack。

## HIPPOCAMPUS — MLSys 2026

同时报告：

```text
systems latency / resource efficiency
+
downstream task accuracy
```

因此 MemBind 不能只报告 speedup，也必须保留 retrieval / QA quality columns。

## Graphiti / Zep

Graphiti maintainer 已说明早期论文 LongMemEval 结果使用的是 very early Graphiti。

因此：

```text
protocol alignment = MUST
exact old-number reproduction = only when exact stack is actually available
```

## Mnemis — ACL 2026 Main

Mnemis 是 Graphiti-based，但增加 hierarchical graph / dual-route retrieval，并报告 LongMemEval-S / LoCoMo quality。

因此：

```text
Mnemis score 可作为 contextual Graphiti-family reference
不能作为 U0 Native 的硬目标
```

---

# 26. 最终原则

整个 paper-level workflow 只遵循一句话：

> **先用已有 C0–C5 证据最大化复用；用 1 个 history 跑通可信 Native，再用 4 个 development histories 完成 baseline qualification；然后小规模实现、调优并做 8-history bounded pilot；只有 pilot 证明机制值得继续后，才依据方差与 CI 精度需求冻结正式 paper-scale，而不是预先拍脑袋扩到 20–24 或看到 p-value 后追加样本。**
