# Native Baseline-First Lightweight Workplan v1.0

> 状态：ACTIVE EXECUTION OVERLAY（2026-08-16）  
> 上位协议：`../（主实验）MemBind_PAPER_EVALUATION_WORKPLAN_v3_SMALL_FIRST_FINAL.md`  
> 目的：先得到一份可独立审计的 Native Graphiti baseline，再决定是否值得继续并发 baseline 与 MemBind 方法实验。

## 0. 这份 overlay 解决什么问题

原 v3 协议的 S4-S10 是完整论文评测协议，但当前最重要的未知量仍然是：

```text
Native Graphiti 在冻结 workload 和当前 runtime 上，
是否稳定、质量是否可解释、成本是否与已有 characterization 同量级？
```

在这个问题回答前，继续构建 D0、A0、P*、M*、S6 32-cell calibration、Pilot 和正式样本规划，会把“方法资格化”置于“问题与 baseline 事实”之前，增加工程量并放大错误 baseline 的风险。

因此本文件是一个**轻量、可回退的执行覆盖层**：

- 不修改、删除或重写任何 C0-C5、S0-S6 历史 artifact；
- 不改变已冻结的 U0/Reader/Judge/retrieval/config identity；
- 暂停所有新方法和并发 sweep 的 live action；
- 只授权 N0-N3 四个 Native-only checkpoint；
- N3 是终止门：`HEALTHY` 才能另开后续方法计划，`DIAGNOSE` 则停在 baseline。

这不是新的 paper result protocol，也不产生最终论文显著性结论。

## 1. 当前执行状态

### 可直接复用的已封存证据

以下 artifact 只做 hash/字段一致性检查，不重跑其历史 live action：

```text
artifacts/paper_eval/S0_CURRENT_STATE.json
artifacts/paper_eval/S0_REUSE_AUDIT.json
artifacts/paper_eval/DEVELOPMENT_EXPOSED_IDS.json
artifacts/paper_eval/native/U0_SMOKE.json
artifacts/paper_eval/native/U0_QUALIFICATION.json
artifacts/paper_eval/native/DATASET_PARITY.json
artifacts/paper_eval/native/EVALUATOR_PARITY.json
artifacts/paper_eval/native/NATIVE_BASELINE_V2_FREEZE.json
```

S1 的 `07741c45` U0 smoke 是生产路径可行性证据，不是本轮四-history baseline 的质量或性能结果。`NATIVE_BASELINE_V2_FREEZE.json` 明确写有 `quality_estimate_status=NOT_ESTIMATED`，不能把它误读成 aggregate quality 已完成。

### 不可直接复用的证据

`C2_U0_REUSE_DECISION.json` 已记录 `construction_model_revision_drift`，因此 C2 的 188-episode 数值只能作为**描述性历史参照**，不能充当本轮 Native baseline 的 measured result。C2 的 phase map、telemetry schema、checkpoint 格式和分析工具仍可复用。

以下 live lane 暂停，不创建新 authority、namespace 或 result：

```text
D0 / S4
A0 / P* / M* / S5
S6 32-cell calibration
S7 pilot
S8 sample-size planning
S9-S10 formal paper evaluation
```

已有 S6 matrix freeze、S6 代码和测试保留为 development evidence，但不授权 live execution。

## 2. 固定数据边界

本轮只使用已暴露、已固定的四个 C2 calibration histories，按以下顺序逐个执行：

```text
07741c45
b6019101
6071bd76
a2f3aa27
```

固定边界：

```text
histories              = 4
episodes               = 188（以 manifest 实际 episode count 为准）
role                   = DEVELOPMENT_EXPOSED
method                 = U0 Native Graphiti Serial only
construction           = one pass per history
fresh namespace        = yes, one namespace per history/run
repeats                = 1
load sweep             = no
held-out IDs           = none
```

不得加入 `c6853660`，也不得从结果反向挑选短 history。四个 ID 与既有 C2 exact ordered-four 绑定保持一致。

## 3. N0-N3 执行顺序

### N0 - Read-only reuse and service check

只读检查：

1. 当前 git commit、working tree 和本 overlay hash；
2. U0 runtime、Graphiti 0.29.3、construction vLLM 0.26.0/65536/YaRN、embedding、Neo4j、Reader、Judge、retrieval identity 与冻结 artifact 的字段一致性；
3. vLLM `/v1/models`、embedding `/v1/models`、Neo4j readiness；
4. 当前无同名 N-run，四个目标 namespace 均为空或尚未创建；
5. secrets 只从既有运行时 `.env` 读取，不写入 log/artifact。

N0 不调用 `add_episode()`，不发 Reader/Judge 请求，不清理旧 namespace。construction vLLM、embedding 或 Neo4j 任一不可达时立即 `BLOCKED_SERVICE`，向用户报告并停止；不自动修改模型参数或协议。

输出：

```text
artifacts/paper_eval/native_baseline/N0_READ_ONLY_CHECK.json
```

### N1 - Single-history gate

优先复用既有 S1 的**合同和 loader**，不复用其结果作为本轮 aggregate 数据。若 N0 发现 runtime identity drift，则只允许先跑 `07741c45` 的单 history U0 smoke；若无 drift，N1 只做 offline binding check。

N1 的 live smoke 必须满足：

```text
direct pinned Graphiti add_episode()
serial source order
fresh namespace
per-episode JSONL + atomic checkpoint
resume-safe, no duplicate/lost source
```

任何 construction/service failure 都结束当前 attempt；保留 checkpoint 和错误类，不自动跨 namespace 重试。

输出（只有发生 live smoke 才生成）：

```text
artifacts/paper_eval/native_baseline/runs/<run-id>/events.jsonl
artifacts/paper_eval/native_baseline/runs/<run-id>/checkpoint.json
artifacts/paper_eval/native_baseline/N1_SINGLE_HISTORY.json
```

任何中断或服务错误的 attempt 必须标记为
`INCOMPLETE_NON_MERGEABLE`，保留已完成 source prefix、error class、checkpoint
和日志 hash；它不能计入 N2/N3 的 Native baseline，也不能通过“从断点继续到另一个
namespace”被拼接成成功结果。若同一 attempt 的恢复合同已存在，只能在服务恢复后
沿用该 attempt 的 durable checkpoint，仍需重新通过完整性验证。

### N2 - Native U0 baseline screen

N1 PASS 后，按固定四-history 顺序串行执行：

```text
for history in [07741c45, b6019101, 6071bd76, a2f3aa27]:
    allocate one single-use run identity
    allocate one fresh isolated namespace
    run pinned upstream Graphiti add_episode() serially
    append every episode event and atomically update checkpoint
    finalize history result before starting the next history
```

每个 history 结束立即写入独立结果；不得等四个 history 全部完成后才落盘。已 finalized 的 history 不覆盖、不重跑。SSH 断开时通过 `tmux` 保持运行；恢复时只使用该 run 的 checkpoint，不创建第二个 controller。

N2 只测 U0，不注入 deterministic ordering、async queue、parallel worker、candidate sidecar、cache replay 或 MemBind 逻辑。Graphiti 调用、Reader、Judge、retrieval 均使用冻结的 common policy。

### N3 - Baseline report and terminal decision

四个 history 的历史结果和独立 observation 全部完整后，构造一次性 report 和 decision：

```text
artifacts/paper_eval/native_baseline/NATIVE_BASELINE_SCREEN.json
artifacts/paper_eval/native_baseline/NATIVE_BASELINE_REPORT.md
artifacts/paper_eval/native_baseline/NATIVE_BASELINE_DECISION.json
```

N3 只允许两个 verdict：

```text
HEALTHY_FOR_NEXT_BASELINE
DIAGNOSE_BEFORE_METHODS
```

`HEALTHY_FOR_NEXT_BASELINE` 只表示 Native path、数据完整性和量级解释足以进入下一份单独 workplan；不表示 MemBind 有效，也不表示质量达到论文 claim 门槛。

如果四个 history 中任一 history 没有完整的 terminal artifact，则不生成
`NATIVE_BASELINE_DECISION.json`；该轮只留下
`INCOMPLETE_NON_MERGEABLE` attempt evidence，并按 `DIAGNOSE_BEFORE_METHODS`
处理。

## 4. Unified observability gate and N2/N3 metrics

`UNIFIED_OBSERVABILITY_CONTRACT_v1.0.md` is the controlling metric and raw
trace contract for this overlay. Its headline/secondary tiers are frozen
before any Native result is inspected. The implementation reuses the passive
C1/C2 span shape where possible; it does not rewrite Graphiti or reopen the
old qualification contracts.

Before N1/N2 live execution, the following focused gates must be green:

```text
common identity and source-sequence validation
lifecycle timestamp monotonicity and latency derivation
content-safe raw quality validation
interval-union and P50/P90/P95/P99 offline reduction
graph-prefix/work-volume projection
incomplete-attempt and checkpoint evidence verification
```

The formal U0 minimum is lifecycle + sanitized phase/LLM/embedding/DB/work
streams. Queue/concurrency time series, resource samples, and detailed
semantic node/edge classifications are schema-reserved diagnostics: they may
be absent with an explicit `NOT_CAPTURED`/`NOT_APPLICABLE` status and do not
block this first serial baseline. If adding live wrappers changes the critical
path, run a small A/A parity and overhead check; do not start a new profiler
qualification loop.

All Level-1/2/3 metrics are deterministic projections from Level-0 streams.
No extra Judge, Reader, embedding, or database request is allowed during
reduction.

The six headline names define the frozen cross-method paper schema, while the
four-history N2 values remain development-only diagnostics. N2 must not use
episode rows as independent samples for significance. Per-history makespan and
goodput are reported individually; any cross-history summary preserves history
as the experimental unit. P95/P99 use the preregistered nearest-rank rule.
`Max Backlog` is `null` with status `NOT_APPLICABLE_SERIAL_BASELINE` for U0,
not numeric zero. Tail amplification (`P99/P50`) is diagnostic only.

### A. 质量与功能

按 history 记录，并同时保存原始 evidence/hash：

```text
QA Accuracy（诊断性）
Evidence Recall@10（unique-session unit）
reader/judge status 与 error class
history/episode success rate
lost / duplicate / unexpected episode
direct invariant violations
```

QA/R@10 不设旧论文分数硬门槛，也不因为一个 history 的 QA=0 就自动否定 baseline；但全零、near-zero retrieval、Reader/Judge error 或结果与 graph evidence 矛盾时必须标记 anomaly 并停止方法扩展。

### B. Native construction 性能

```text
add_episode latency: mean / P50 / P95
per-history makespan
successful episodes/s（goodput）
publication latency
failure/interrupt location
```

性能统计单位是 history；episode latency 只作为分布描述，不能伪装成独立样本显著性。

### C. Online/freshness reference

U0 serial 不做 offered-load sweep。只记录：

```text
arrival-to-publication latency
publication timestamp
P99 arrival-to-publication latency
max backlog = NOT_APPLICABLE_SERIAL_BASELINE（无真实队列时）
```

不在本轮推导 A0/P* 的 load curve，不把 U0 的 service time 直接写成 online improvement。

### D. Work accounting

```text
LLM calls / input tokens / output tokens / retries
embedding calls / item count / retries
Neo4j queries / writes / transactions
HTTP request count
instrumentation overhead marker
```

## 5. 三层 sanity check

### 5.1 内部一致性

必须满足：

```text
published + failed + censored == expected episodes
每个 source sequence 恰好一个 terminal outcome
checkpoint、events、summary、Neo4j observation 的 hash/计数一致
```

### 5.2 Graphiti 行为一致性

报告每 history 的 entity、edge、LLM call、resolution/update 和 publication 量级。发现 episode 几乎不产生 graph material、调用数异常为零、namespace escape 或空 graph 时，标记 `DIAGNOSE_BEFORE_METHODS`，不得用“运行成功”掩盖退化。

### 5.3 历史参照与文献量级

只做描述性比较：

```text
N2 vs C2 median/P95/service-time/work-volume
N2 vs pinned Graphiti/LongMemEval reported order of magnitude
```

由于 construction revision、model serving 和 Reader/Judge 可能不同，不设数值相等阈值，不声称 exact reproduction。若出现约一个数量级的不可解释偏差，停止并诊断环境/数据/调用路径。

## 6. TDD 与运行纪律

本 overlay 的新增实现只允许最小范围：baseline screen runner、metrics projection、report schema、resume/checkpoint glue。每一项遵循：

```text
RED contract test
-> 最小实现
-> focused GREEN
-> 相关 Native baseline regression
-> live N0/N1/N2
```

不重开 1400+ S5/S6 离线合同，不因本 overlay 添加新的 correctness oracle 或方法抽象。最低新增测试覆盖：

```text
固定四 history / role disjointness
episode terminal accounting
checkpoint resume / duplicate refusal
metric aggregation 与 P50/P95
report hash/envelope 与 decision stop rule
tmux launcher 参数和日志路径
```

长任务必须通过 `tmux`：

```bash
tmux new-session -d -s membind-native-baseline-<run-id> '<command>'
tmux capture-pane -pt membind-native-baseline-<run-id>
```

科学状态只从 JSONL/checkpoint/result 读取，不从 terminal 输出推断。每完成一个 episode/history 立即持久化；服务断链时保存已完成前缀和 error class，然后 STOP。

## 7. 明确延期项

只有 N3=`HEALTHY_FOR_NEXT_BASELINE` 才能另起新计划，顺序暂定为：

```text
one-history A0 smoke
-> one-history P(C=2) smoke
-> one-history M* smoke（若仍有研究必要）
-> 再决定是否需要 C={1,2,4,8} calibration
```

以下项目在 N3 前全部禁止：

```text
D0 replay/qualification
S6 32-cell matrix live run
offered-load sweep
8-history pilot、repeat、adaptive N
formal paper sample-size/final run
dependency-graph/M1/M2 扩展
```

如果 N3=`DIAGNOSE_BEFORE_METHODS`，主线停在 Native baseline；先修复并重新冻结受影响的 runtime/config，再由新的小 workplan 授权后续动作。

## 8. 研究结论边界

本 overlay 结束时最多能支持：

> 在四个已暴露 development histories 上，当前冻结配置下 Native Graphiti 的功能、质量、延迟和工作量基线处于可解释或不可解释状态。

它不能支持：

```text
MemBind 优于 Native
并发安全性/普遍性
正式统计显著性
最终论文质量 non-inferiority
```

## 9. 2026-08-16 observability and Reader binding checkpoint

The metric audit retained the six headline names and the two predefined
secondary metrics; it did not add a workload, method, repeat, load point, or
held-out history. Focused TDD corrected four adapter/reducer issues before N2:

```text
U0 max backlog             null + NOT_APPLICABLE_SERIAL_BASELINE, not zero
successful goodput         episodes/second, not episodes/nanosecond
LLM token accounting       logical-call tokens; transport attempts separate
quality adapter            frozen Reader-v2 + frozen Judge component hashes
```

The first N1 process had already started construction with the correct U0
Graphiti/model/embedding path but had instantiated the historical direct
Reader object for its later quality step. That object is not called during
construction. A one-shot, PID/run/checkpoint-bound watchdog therefore pauses
the legacy process only after its complete durable episode prefix and before
quality. The same run ID, namespace, and prefix then continue under the
Reader-v2-bound runner. Construction data are neither discarded nor merged
across namespaces.

History finalization is two-phase: a full source prefix remains resumable until
sanitized `quality.jsonl`, a final namespace observation, and a hash-sealed
`history_result.json` are durable; only then does the checkpoint become
`completed`. Level 0 streams remain the source of truth, so older Level-1 rows
may be deterministically regenerated after the process stops without any new
Graphiti, Reader, Judge, embedding, or database request.

The focused metric/finalization suite passed 20 tests and the related Native/S1
regression passed 39 tests. Their JUnit evidence is stored in:

```text
logs/TDD_GREEN_NATIVE_METRIC_AND_FINALIZATION_20260816.xml
logs/TDD_RELATED_GREEN_NATIVE_METRIC_AND_FINALIZATION_20260816.xml
```
