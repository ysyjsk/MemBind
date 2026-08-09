# MemBind 当前基础验证执行计划 v1.2

> **用途**：这是当前阶段唯一允许 Agent 直接执行的计划。
>
> **目标**：先完成 Graphiti 上的基础可行性验证。验证完成之前，不设计、不实现、不运行任何后续扩展机制。
>
> **当前阶段**：`V3 — Full Correctness Smoke`
>
> **当前阻塞**：`v3_smoke_002_m0_structured_output_failure`
>
> **当前动作范围**：`blocked_waiting_for_explicit_protocol_deviation`。
>
> **当前唯一允许动作**：保持 structured-output blocker，并等待明确批准的 protocol
> deviation 或有新证据的 service-side correction。唯一冻结 probe
> `005_fresh_restart` 已复现历史截断，sanitized post-request evidence 已审阅；
> `v3_smoke_003 remains forbidden`。
>
> `forbidden_until_pass: V4/V5/V6/future_work`
>
> **权威关系**：
> - 本文件：决定“现在按什么顺序执行”。
> - `MemBind_basic_validation_experiment.md`：提供已冻结的数据集、模型、Graphiti 版本、指标定义等背景合同。
> - 历史 `smoke01...`：只作为失败证据，不决定下一步任务。
> - 如果旧文档与本文件的执行顺序冲突，以本文件为准；任何涉及模型、数据集、Graphiti 版本或方法语义的实质变更仍视为 protocol deviation，不能由 Agent 自行修改。

模型主机取证只允许使用 `ssh zju-liuyi '<forced-command>'`，合同固定为：

```text
remote_scope: /home/lhx/liuyi/**
allowed_forced_commands: status/list/read/tail/follow
```

所有命令均为只读。禁止请求 ordinary shell、绕过 forced-command、扩大权限，或访问
`/home/lhx` 其他目录及系统目录。当前没有 remote write permission；如任务确需修改
允许范围内文件，必须先明确报告并等待受限脚本扩展写权限，不得自行绕过。

---

# 0. 当前只回答四个问题

本轮基础验证只回答：

1. **带公共 deterministic candidate-ordering adapter 的 Graphiti 连续 memory construction 是否存在明显性能瓶颈？**
2. **M2 的拆分执行在冻结神经模型输出后，是否能够保持 M0 的最终语义状态？**
3. **直接并发完整 `add_episode()` 的 M1 是否已经足够，还是会产生语义差异？**
4. **在相同真实运行环境下，M2 是否降低 `arrival_to_publish` 和 makespan？**

除此之外一律不回答。

---

# 1. 本轮明确禁止事项

在 `V7` 结束前，Agent **不得**：

- 增加第二 memory backend；
- 实现 conflict-aware Bind / Commit；
- 实现 selective repair / rebind；
- 设计通用 memory IR；
- 设计多租户、fault tolerance、MVCC、visibility-frontier scheduler；
- 扩展到 MemoryArena / LoCoMo；
- 增加大规模 concurrency sweep；
- 增加大规模 load sweep；
- 因为某次结果不好而修改 frozen dataset split；
- 因为某次结果不好而修改 GO/NO-GO 阈值；
- 为 M2 单独修改 Graphiti prompt、search algorithm、模型参数或数据库配置；
- 为解决 correctness miss 而继续增加新的 semantic normalization，除非已经证明这是 M0 自身跨 run 的无语义物理不稳定，并单独记录 protocol deviation；
- 在当前阶段未 PASS 时提前执行下一阶段。

如果发现可能值得做的新机制，只记录到 `artifacts/notes/deferred_ideas.md`，**不得实现**。

---

# 2. 已冻结、不再讨论的实验合同

除非发生明确环境故障，以下内容不再重新研究：

```text
Backend: Graphiti v0.29.3
Commit: 021d3a5
Database: Neo4j Community 5.26
Construction: Qwen3-32B-FP8
Serving: vLLM 0.26.0
max_model_len: 40960
Embedding: Qwen3-Embedding-0.6B, 1024 dim
Dataset: LongMemEval-S cleaned
Task subset: knowledge-update, excluding *_abs
Calibration instances: frozen 4
Evaluation instances: frozen 8
Construction global concurrency cap: 8
Batch Invariance: OFF / default serving path
```

要求：

- M0/M1/M2 使用完全相同的远程 construction endpoint；
- M0/M1/M2 使用完全相同的 embedding endpoint；
- M0/M1/M2 使用完全相同的 Graphiti commit、prompt、schema、模型参数和 Neo4j 配置；
- 不再为了 reproducibility 改成 Batch Invariance；
- correctness 与 performance 分开。

命名合同：内部 method ID 继续使用 `M0`，以兼容已有 artifact 和代码；当前
Pilot 的公开报告名固定为 **`Deterministic-Graphiti-Serial`**。这个 baseline 是
Graphiti v0.29.3 加上对 M0/M1/M2 全部相同的 deterministic candidate-ordering
adapter，不声称是 untouched upstream Graphiti。

---

# 3. 单线执行状态机

```text
V1 Correctness nondeterminism closure
        ↓ PASS
V2 Correctness oracle freeze
        ↓ PASS
V3 Full correctness smoke
        ↓ PASS
V4 Deterministic Graphiti calibration + minimal profiling
        ↓ PASS
V5 M1 one-instance smoke
        ↓ PASS
V6 Formal evaluation
        ↓ COMPLETE
V7 Analysis + validation verdict
        ↓ STOP
```

**禁止跳阶段。**

---

# V1. Correctness nondeterminism closure

## V1.1 当前问题

当前已经观察到：

- 相同 source 5；
- 相同历史输入前缀；
- full-text query hash 相同；
- logical edge count 相同；
- 但部分 existing-edge embedding 与 query embedding 的原始浮点 hash 跨 run 不同。

目前只能证明：

```text
same logical embedding input
→ live embedding output is not guaranteed bitwise identical across runs
```

**还不能直接宣称 embedding 一定是最终 prompt divergence 的根因。**

## V1.2 只允许做 retained-artifact closure

V1 是一次 **retained-artifact closure**。只读取已经保存的两份 source-5
forensic snapshot、对应 run/trace artifact、source-8 prompt-divergence 证据和
smoke14 prompt cache；不得启动新的 live embedding recapture，不得重跑 6 或
46 episodes，也不得调用 construction、embedding 或 Neo4j 服务。

旧 artifact 不含原始向量，只保留 SHA256、dimension 和 norm。新的 live run
只能得到第三、第四次 execution 的向量，不能恢复历史 smoke14 向量，也不会改变
V2 必须冻结 model oracle 的决策。因此禁止为了补数值而重新采样。

离线分析必须对每类 retained evidence 明确记录：

```text
1. source artifact path + SHA256 + immutable run status
2. retained logical input/key 是否能够精确配对；不能配对时显式 not_available
3. embedding SHA256 是否相同、dimension 是否相同、norm 与 norm delta
4. saved backend candidate membership 是否变化
5. saved backend/Python ranking order 是否变化
6. retained downstream prompt hash 是否变化
7. ranking/prompt 变化是否能由现有证据归因给 embedding variation
```

没有 raw vector 的三项必须原样持久化为：

```json
{
  "cosine_cross_run": "not_computable_from_retained_artifacts",
  "l2_cross_run": "not_computable_from_retained_artifacts",
  "max_abs_diff": "not_computable_from_retained_artifacts"
}
```

缺失数据必须标成 `not_available` 或
`not_computable_from_retained_artifacts`，禁止伪造数值、从 hash 反推距离，或把
新的 live vector 当成历史 vector。API key、Authorization header、环境变量 dump
和原始 secret 不得进入输出。

## V1.3 V1 终止规则

无论 retained evidence 是否足以证明 top-K/prompt 因果链，完成上述一次离线诊断
后即结束 V1。

不要继续无限追逐 live GPU bitwise determinism。

### V1 PASS 条件

- 所有可配对 retained evidence 已比较；
- 所有不可计算字段已显式标记而非估算；
- 已记录现有证据是否足以建立 ranking / prompt 因果链；
- artifact 已持久化；
- 不再把 live embedding bitwise identity 当作 correctness 前提。

输出：

```text
artifacts/diagnostics/embedding_nondeterminism_source5.json
```

然后立即进入 V2。

---

# V2. Correctness oracle freeze

## V2.1 目的

Correctness lane 只验证：

> 当外部神经模型计算结果固定时，M1/M2 相对 M0 的执行组织是否产生相同 Graphiti semantic state。

因此 correctness lane 必须冻结所有实际参与 construction、且不是本实验研究对象的 model-derived outputs。

## V2.2 必须实现：LLM + Embedding Capture/Replay

### M0 capture

持久化：

```text
LLM:
canonical prompt request -> exact raw/parsed response

Embedding:
canonical single-item embedding input -> exact vector
```

### M1/M2 replay

```text
LLM cache hit       -> exact captured response
Embedding cache hit -> exact captured vector

任何 miss -> 禁止 live fallback，并按方法分类
禁止 live fallback
```

M1 read-only replay 与 M2 read-only replay 必须共享对应 instance 的同一个 M0
capture oracle。这样 M1 的 semantic divergence 才能归因于执行组织，而不是
另一次 live model sample。correctness replay 不用于性能计时。

### V2 current bounded pilot

在 V3 full correctness smoke 之前，当前 V2 只执行一个专用的 harness integration：

```text
M0 capture -> M0 read-only replay
```

命令为：

```text
.venv/bin/python src/replay_driver.py v2-oracle-integration \
  --attempt v2_oracle_integration_001
```

该 pilot 只使用固定单 episode integration instance，验证模型 oracle、零 live
fallback、fresh Neo4j、cross-encoder audit 和 prompt/embedding cache hash 不变。
它不运行 M1，也不提前执行 V3 的 M0 -> M2。manifest 的 runtime 字段必须有实际
远端 argv、启动日志或部署配置的证据；无法证明的字段进入 `unresolved_fields`，
live gate 保持 fail closed。

### 重要边界

**不得 replay：**

```text
Neo4j graph state
candidate retrieval result
entity resolution state
edge resolution state
invalidation
DB commit
```

这些必须由 M1/M2 在各自的最新 committed graph 上真实执行。

## V2.3 Embedding key

Embedding correctness key 至少包含：

```text
served embedding model ID
endpoint-reported revision OR operator-supplied immutable deployment fingerprint
embedding dimension
dtype / pooling / normalization / instruction / input-transform configuration
normalization/instruction configuration
exact single-item input bytes
```

按**单个 embedding item**缓存，不把 API batch composition 放进 semantic key。
模型身份优先使用 endpoint-reported revision；若 endpoint 不提供，使用
operator-supplied immutable deployment fingerprint。后者必须来自一次性部署清单，
例如 model/config/tokenizer/weight-index hash manifest 和 vLLM launch-config hash，
不能仅用 served alias、endpoint URL 或一次行为 probe 代替。不能猜测 checkpoint revision。
身份来源与清单写入
`artifacts/environment/embedding_model_fingerprint.json`，并绑定 cache namespace；
两种来源都没有时 V2 hard block。

## V2.4 M1/M2 oracle miss 语义

所有 miss 都立即停止当前 replay，且 live model call 必须仍为 0，但方法结果不同：

- **M1 oracle miss**：run outcome 记为 `execution_path_divergence`，lifecycle 状态记为
  `completed_with_divergence`。它证明 whole-update parallelism 改变了某个
  state-dependent operation 的输入/trajectory；不得据此宣称最终 graph semantic divergence。
  字段固定为
  `final_semantic_parity = not_evaluable_due_to_oracle_miss`，也不得为了得到最终
  graph 而 live fallback。保存 first divergent
  source sequence、oracle 类型、prompt name 或 embedding input hash/length、此前
  successful hit count、graph-state digest、PromptParts/candidate diff（能取得的字段）。
  M0 对照记录按 `prompt_name + source_sequence + invocation ordinal`（并在可取得时
  加 call-site identity）确定性对齐，不得用“最近一条 cache record”猜测。
- **M2 oracle miss**：记为 correctness failure；它阻断 performance，并按 V3/V6
  规则保留 artifact 后停止。
- M1 无 miss 且完整结束时，才比较 canonical graph、invalidation 和 retrieval；这些
  final-state 差异是比 execution-path divergence 更强的语义结果证据。

## V2.5 Cross-encoder audit

只做一次静态/trace audit：

- 如果当前 frozen construction/retrieval path 没有调用 cross encoder：记录 `not_invoked`，结束；
- 如果调用：将其视为另一个 model-derived oracle，采用同样 capture/replay；
- 不因为 audit 结果改 Graphiti search recipe。

## V2.6 必须先 TDD

至少新增：

```text
test_embedding_capture_replay.py
test_embedding_replay_miss_fails.py
test_no_live_embedding_fallback.py
test_embedding_cache_single_item_key.py
test_model_oracle_mode_contract.py
test_m1_oracle_miss_is_completed_path_divergence.py
test_m2_oracle_miss_blocks_performance.py
```

### V2 PASS 条件

```text
all unit tests PASS
LLM replay miss => FAIL
Embedding replay miss => FAIL
M2 replay live LLM calls = 0
M2 replay live embedding calls = 0
M1 replay live LLM calls = 0
M1 replay live embedding calls = 0
Neo4j remains live/fresh
```

完成后冻结 correctness harness；除明确 bug 外不再修改 oracle semantics。

---

# V3. Full correctness smoke

## V3.1 只做 M0 → M2

为了避免一次 smoke 同时验证太多东西，**本阶段不运行 M1**。

固定使用当前已经用于 smoke 的同一个完整 LongMemEval instance。

执行：

```text
1 × M0 full capture
1 × M2 full read-only replay
```

包含完整 history，不截短为 2/6/9 episodes。

## V3.2 PASS 条件

必须全部满足：

```text
M0 full history success
M2 full history success
unexpected LLM prompt = 0
unexpected embedding input = 0
M2 live LLM fallback = 0
M2 live embedding fallback = 0
episode count equal
exactly-once equal
source mapping equal
canonical graph parity = true
retrieval guardrail passes
post-run DB cleanup = 0 nodes
```

## V3.3 失败处理

若失败：

1. 保留完整 attempt；
2. 只定位**第一个 divergence**；
3. 判断属于：
   - harness bug；
   - upstream physical nondeterminism；
   - actual M0/M2 semantic divergence；
   - frozen request 下的 upstream/model structured-output failure；
4. 只允许修复 harness bug；
5. 若修复需要改变 Graphiti semantic behavior、prompt 或 candidate membership，停止并记录 protocol deviation，不自行继续；
6. 新 attempt 必须新 run_id。

新 attempt 只有在服务侧修复已由冻结协议下的证据证明，或用户明确批准
protocol deviation 后才允许启动；拥有一个新的 run ID 本身不构成重跑授权。

**禁止同时修多个未证明问题。**

### V3 截断记录与当前 structured-output blocker

`v3_smoke_002` 在 M0 的第二个 source episode 进行结构化抽取时失败。冻结的
2048 与 8192 completion budgets 均以 `finish_reason=length` 结束，M2 未启动，
M1 仍按协议禁止。当前证据只支持“冻结 Graphiti 请求下的 upstream/model
structured-output failure”，尚不能在 vLLM guided decoding、模型行为和其他
request/runtime interaction 之间完成归因。

完整证据保存在：

```text
membind-validation/artifacts/diagnostics/v3_smoke_002_failure_report_20260809.md
SHA256 060e59eeb5e68015f8b0a022b5e266e19be15dd16dcac7fe240e7c20e8a5b09e
```

只允许离线诊断和收集服务侧配置证据。不得复用该 attempt 的 partial prompt /
embedding cache，不得修改 Graphiti prompt、schema、decoding policy、模型或冻结
retry budget 来追求通过。

当前离线诊断已经持久化为：

```text
artifacts/diagnostics/v3_smoke_002_structured_failure_diagnosis_20260809.md
artifacts/diagnostics/v3_smoke_002_structured_failure_diagnostic_20260809.json
```

诊断证明四轮 `2048 -> 8192` 响应逐字节重复且均以 `length` 截断；实际
`ExtractedEntities.extracted_entities` 数组没有有限 `maxItems`。这解释了
“schema-compatible prefix”现象，但不证明 deployed guided-decoding backend 的
选择或配置。metadata attempt02 后来被证明错误经过了外网 proxy，不能作为服务
状态证据。直连 attempt03 的 version/models/health 均通过，`/server_info` 未开放。

construction service 恢复后执行的只读直连 attempt04 再次证明
version/models/health 可用，但 `/server_info` 仍为 404，且没有调用 generation：

```text
artifacts/environment/v3_vllm_metadata_probe_20260809_attempt04_restored.json
classification: service_restored_backend_config_unavailable
```

因此“服务恢复”只关闭 availability 条件，不关闭 backend/config 证据 gate，也不
授权 compatibility probe 或 `v3_smoke_003`。

随后通过受限 `ssh zju-liuyi` 取得的 startup log 证明当前重启实例配置为
`StructuredOutputsConfig(backend='auto', ...)`，且在哈希日志快照中尚无 generation：

```text
artifacts/environment/v3_construction_runtime_evidence_20260809.json
classification: configured_backend_auto_fresh_service_no_generation_observed
```

这不证明实际 request-selected backend，也不证明历史失败已修复；它曾把 gate 推进
为单次 `frozen_public_path_compatibility_probe_only`。该 one-shot 现已执行完毕：

```text
artifacts/environment/v3_actual_schema_compatibility_probe_20260809_005_fresh_restart.json
classification: exact_historical_truncation_reproduced
```

它在 5795 prompt tokens 下再次逐字节复现四组 `2048 -> 8192` 截断，post-request
restricted log 同时记录 8/8 HTTP 200 且没有 server error。当前动作范围因此收紧为
`blocked_waiting_for_explicit_protocol_deviation`；`v3_smoke_003 remains forbidden`。

最初的 source-0/source-1 probe 直接调用 private `_generate_response`，漏掉 public
`generate_response` wrapper 注入的 227 字符 language instruction；其 `-43` token
结果及 runtime-drift 诊断无效。修正后的 public-path probe 恢复历史 5795 prompt
tokens，并在四次 high-level attempts 中逐字节复现历史 `2048 -> 8192` 两个失败
response hashes。当前 blocker 因而仍是已确认的 structured-output truncation。

机器证据为：

```text
artifacts/environment/v3_actual_schema_compatibility_probe_20260809_004_reclassified.json
SHA256 d3caf163af7639f2dcbc5322d4f1e3e5a3d23067f2638bb4398d15c4c2b9bcfb
```

### V3 PASS 后动作

标记：

```text
CORRECTNESS_HARNESS_FROZEN=true
```

之后才允许开始性能相关 calibration。

---

# V4. Deterministic Graphiti calibration + minimal profiling

## V4.1 目的

这里只回答两个问题：

1. 正式 open-loop workload 的 `DELTA_MS` 应该是多少？
2. `Deterministic-Graphiti-Serial` 的时间主要花在哪里？

不做任何新方法设计。

## V4.2 运行范围

只运行已经冻结的 **4 个 calibration instances 的 M0**；M0 是内部 method ID，
报告名固定为 **`Deterministic-Graphiti-Serial`**。

这些运行同时承担：

```text
arrival calibration
+
native bottleneck characterization
```

不要为了 profiling 再额外选数据。

只允许一项不调用 live model 的最小 guardrail，不构成额外 characterization
campaign：使用同一 frozen model oracle 的固定短 prefix，对 M0/M2 分别做
instrumentation OFF/ON 的四次 counterbalanced paired replay，先确认 semantic
parity，再计算：

```text
method-specific overhead_m = median(ON_m) / median(OFF_m) - 1
differential overhead = overhead_M2 - overhead_M0
```

任何方法的 method-specific overhead `>5%` 就阻断 formal performance；否则全部
如实报告。这是本 Pilot 预注册 engineering gate，不是 OSDI/SOSP/EuroSys 的通用
阈值。

当前 M0 已透明命名为 `Deterministic-Graphiti-Serial`，所以本阶段不运行 upstream semantic guardrail，
也不为保留 “Native” 名称增加 live/replay 对照。该 adapter
的 publication-scale upstream comparison 不属于当前 Pilot。

## V4.3 只记录最小必要 phase

不要一开始拆十几二十个 span。

最少记录：

```text
A. total add_episode
B. semantic extraction
C. embedding + candidate search
D. state-dependent resolution / invalidation
E. DB publication
F. other/unclassified
```

如果 Graphiti 源码实际无法无侵入地拆成上述某一项，宁可保持 coarse span，不要重写 upstream 逻辑。

每个 span 保存：

```text
start_ns
end_ns
episode_sequence
phase
```

不要只保存 duration，以免重叠阶段被重复计数。

## V4.4 同时记录基本调用量

```text
LLM calls
LLM input tokens
LLM output tokens
embedding calls
DB query count（如果现有 instrumentation 已支持）
```

不要新增复杂 GPU profiler、kernel profiler 或大规模 telemetry。

## V4.5 网络处理

真实部署是本地 Graphiti → LAN → 远程 vLLM，因此：

- E2E latency **包含网络**；
- 不减 RTT；
- 不用 ping/health probe 去修正每次请求；
- run 前只做一次 endpoint health check；
- 记录 transport error / timeout；
- 保持 same LAN route、same endpoints，并通过 `NO_PROXY` 确认内网 endpoint 不经过代理；
- 仅做必要 endpoint health 检查，且确认 server not known shared；
- 正式运行时不要并发启动额外网络探针。

如果发生明确 connection failure / server restart：该 run 标记 infrastructure failure，不进入 calibration。

普通毫秒级 jitter 作为真实运行噪声保留。

禁止 RTT subtraction；不恢复 100-probe baseline、每 run pre/post probe 或并发
telemetry campaign。

## V4.6 冻结 DELTA

从 4 个 calibration M0 中的成功 episode：

```text
DELTA_MS = round_to_100ms(median(native_episode_service_ms))
```

冻结后禁止根据 M1/M2 结果修改。

## V4.7 输出

```text
artifacts/calibration/arrival_interval.json
artifacts/calibration/native_episode_latency.parquet
artifacts/calibration/native_phase_profile.parquet
artifacts/calibration/native_phase_summary.json
artifacts/calibration/instrumentation_overhead.json
```

### V4 PASS 条件

```text
4/4 calibration instances complete
DELTA_MS frozen
phase timing internally consistent
no instrumentation-induced semantic change
all method-specific overhead <= 5%
baseline label recorded as Deterministic-Graphiti-Serial
```

当前阶段只需要回答：

> Graphiti 的主要时间是否确实存在于可被 M2 拆分/重叠的部分。

**不要依据 profiling 结果立即实现新机制。**

---

# V5. M1 one-instance oracle-replay smoke

## V5.1 目的

只验证：

> 直接并发完整 `Graphiti.add_episode()` 是否保持 M0 的 state-dependent execution
> trajectory；若 trajectory 完整命中同一 oracle，再判断是否出现最终 semantic
> divergence。

## V5.2 设置

固定：

```text
同一个 full smoke instance
M1 WholeUpdate-Parallel-C8
construction global concurrency <= 8
M1 read-only replay of the V3 M0 LLM response + embedding vector oracle
live LLM calls = 0
live embedding calls = 0
fresh Neo4j
```

不做：

```text
C1/C2/C4/C8 sweep
best-concurrency tuning
repair
ordered patch
```

## V5.3 输出

```text
runtime success/failure
oracle outcome: completed / completed_with_divergence
first execution_path_divergence evidence（如有）
canonical graph vs M0
retrieval vs M0
source completion order（diagnostic only）
LLM/token cost
```

这里的 LLM/token cost 是被 replay 的逻辑 work volume；不得把 replay wall-clock
当作 M1 性能。M1 的真实性能只来自 V6 performance lane 的 live run。

重要：

> `source completion order` 被打乱本身不能作为 M1 semantic failure。

证据分两级：

```text
execution_path_divergence:
  证明 M1 改变 state-dependent operation 的输入/trajectory
  不直接证明 final graph divergence

final semantic divergence:
  canonical graph / invalidation / retrieval / temporal final-state divergence
```

### V5 PASS 条件

这里只要求实验 harness 能产生一个合同允许的终止结果：完整 replay，或在第一个
oracle miss 处以 `completed_with_divergence` 结束并持久化证据。

M1 是否语义一致是实验结果，不是执行 gate。

---

# V6. Formal evaluation

只有 V1–V5 全部完成后才生成 run plan。

## V6.1 Correctness lane

```text
8 evaluation instances
× (M0 capture + M1 read-only replay + M2 read-only replay)
= 24 correctness runs
```

Correctness lane：

```text
LLM capture/replay
Embedding capture/replay
live fresh Neo4j
不报告性能
```

M0 capture 的同一个 `LLM response + embedding vector` oracle 同时供 M1/M2
只读 replay；任一 miss 都不得调用 live fallback。M1 miss 以
`completed_with_divergence` / `execution_path_divergence` 计为 treatment outcome，
不伪称 final semantic error；M2 oracle miss 是 correctness failure。

## V6.2 Performance lane

```text
8 evaluation instances
× 3 methods (M0/M1/M2)
× 2 repeats
= 48 live runs
```

全部：

```text
live LLM
live embedding
Batch Invariance OFF/default
same endpoints
same resource cap
same DB configuration
same DELTA_MS
```

不得使用 correctness persistent oracle cache。

因此正式 evaluation 总计：

```text
24 correctness runs + 48 live performance runs = 72 runs
```

## V6.3 Run order

替换旧的全局随机 shuffle。

使用：

```text
block = (question_id, repeat)
```

每个 block 内包含：

```text
M0, M1, M2
```

用固定 seed 对方法顺序做平衡/随机排列，使同一 instance 的 paired methods 尽量在相近 wall-clock 时段执行。
16 个 performance block 必须各自连续包含一次 M0/M1/M2；六种排列的使用次数
最大差不超过 1，禁止退回全局 shuffle。

正式执行先完成全部 24 个 correctness runs。只有 M2 correctness 达到 8/8
且 oracle miss/fallback 均为 0，才允许启动 48 个昂贵 live performance runs。
M1 correctness 的 `execution_path_divergence` 或 final semantic divergence 是实验
结果，不阻断 performance；M2 oracle miss 或 parity failure 必须阻断 performance。

## V6.4 每个 performance run 前

最小检查：

```text
model endpoint healthy
embedding endpoint healthy
no known unrelated GPU job
fresh logical Neo4j state
same client/server config
counters reset
```

不要在此阶段临时添加新的 warm-up/cache策略；使用 V4 前已经冻结的统一 lifecycle。

## V6.5 Failure policy

### 明确 infrastructure failure

例如：

```text
connection reset
server restart
endpoint unreachable
unrelated GPU OOM
```

处理：

- 保留失败 artifact；
- 标记 `infra_failed=true`；
- 不静默排除；
- 确认 infrastructure contamination 后，`MUST rerun the entire block`：环境恢复时
  使用新 run_id 和新 block ID 重跑整个 `(question_id, repeat)`
  的 M0/M1/M2 block；原失败 block 只进入 failure appendix，不进入 primary
  paired statistics，且不得按结果方向挑选 replacement；无法补齐完整 replacement
  时 performance 标为 incomplete/inconclusive。

### 方法自身引起的失败

例如：

```text
M1/M2 并发导致 queue overload
DB conflict
method deadlock
method-induced OOM
```

这是实验结果，不得归类为网络噪声。

两次 repeat 必须报告
`relative_repeat_gap = abs(x1-x2)/mean(x1,x2)`；不自动补第三次，也不按结果
方向选择性补跑。每个 `(instance, method)` 的 gap `>10%` 标记
`stability_warning=true`，并报告 warning 数量与比例；这是描述性的 Pilot engineering
warning，不冒充通用顶会阈值，也不单独触发补跑。V7 必须结合置信区间如实讨论
性能证据是否 noisy。

---

# V7. Analysis + Validation Verdict

只生成基础验证结论，不开始下一阶段开发。

## V7.1 必须回答

### Q1. Deterministic-Graphiti-Serial 的主要 bottleneck 在哪里？

使用 V4 phase profile。

### Q2. M2 是否保持 M0 语义？

使用：

```text
8/8 canonical graph parity
retrieval guardrail
zero unexpected model-oracle inputs
exactly once
source mapping
```

### Q3. M2 是否有端到端收益？

主指标：

```text
P95 arrival_to_publish_ms
instance makespan_ms
```

辅助：

```text
P50
drain
throughput
LLM/token/embedding cost
```

统计单元仍为 `question_id`，不能把 episode 当独立样本。

### Q4. M1 是否已经足够？

使用 8 个 instance 的 M1 read-only correctness replay 判断 semantic parity，
使用 live performance lane 判断性能。如果 M1 又快又保持 M0 semantic parity，
则必须明确记录：

```text
当前实验没有证明 Late Binding 的必要性。
```

M1 oracle miss 只支持“执行路径已分叉”，不得改写成“最终 graph 已错误”；只有
完整 replay 后的 canonical graph/invalidation/retrieval 差异才是 final semantic
divergence。不能用 completion-order 变化替代这两级证据。

## V7.2 最终输出

```text
artifacts/final/VALIDATION_REPORT.md
```

并只给：

```text
GO
INCONCLUSIVE
NO-GO
```

以及对应证据。

**生成报告后立即停止。**

本文件不允许 Agent 根据 GO 结果继续实现任何后续机制。

---

# 4. 对现有主协议必须做的最小修改

不要重写全部 1000 行协议，只 patch 以下内容。

## Patch A — §3.4 Embedding model

旧表述：

```text
相同输入必须通过 embedding cache 返回同一向量。
```

改成两条：

```text
Correctness lane：M0 capture exact embedding vector，M1/M2 对相同 canonical single-item embedding input 只读 replay；miss 即失败，禁止 live fallback。

Performance lane：M0/M1/M2 使用相同 live embedding server 和相同 cache policy；不要求跨 run bitwise-identical vectors。
```

## Patch B — §6.1 Correctness lane

从：

```text
LLM response replay
```

改成：

```text
model-oracle replay = LLM response + embedding vector；M1 read-only replay 与 M2 read-only replay 共享对应 M0 capture。
```

如果 cross encoder 实际没有被调用，只记录 audit，不增加额外代码。

## Patch C — §7.2 run order

删除：

```python
random.Random(20260806).shuffle(run_plan)
```

替换为：

```text
blocked randomized / counterbalanced order by (question_id, repeat)
```

## Patch D — §13 M1 necessity

删除：

```text
source-order violation 单独即可证明 M1 失败
```

改成：

```text
M1 completion/source-order 仅为 diagnostic；M1 oracle miss 只证明
`execution_path_divergence`，final semantic claim 需要完整 replay 后的
graph/invalidation/retrieval divergence。Late Binding necessity 由上述分层证据与
live M1/M2 性能共同判断。
```

## Patch E — §16 execution order

以本文件 V1→V7 替换旧 Phase 0→6 的当前执行顺序。

旧环境/数据 gate 可以标记为 `DONE`，不要重复执行整个搭建过程，只在正式 run 前做轻量 contract check。

## Patch F — §19 future work

从 Agent 当前执行文档中删除。

如需保留，移动到：

```text
FUTURE_WORK.md
```

并明确：

```text
CURRENT VALIDATION AGENT MUST NOT READ OR EXECUTE THIS FILE.
```

## Patch G — 历史 smoke 记录

把 `smoke01...smoke14...` 的长历史从当前执行顺序中移到：

```text
artifacts/history/SMOKE_HISTORY.md
```

当前计划只保留：

```text
current_stage
current_blocker
next_allowed_action
```

避免 Agent 从历史 debug 记录推断新的待办。

---

# 5. Agent 每次开始工作前只读取这三个状态

建议增加：

```text
membind-validation/CURRENT_STATE.json
```

当前格式固定，并以实际 `CURRENT_STATE.json` 为准：

```json
{
  "protocol_version": "current-validation-v1.2",
  "current_stage": "V3",
  "status": "blocked_v3_structured_output_reproduced",
  "current_blocker": "v3_smoke_002_m0_structured_output_failure",
  "current_action_scope": "blocked_waiting_for_explicit_protocol_deviation",
  "next_allowed_action": "no further live execution; retain the structured-output blocker and wait for an explicit protocol deviation or evidenced service-side correction; v3_smoke_003 remains forbidden",
  "forbidden_until_pass": ["V4", "V5", "V6", "future_work"]
}
```

摘要合同：`forbidden_until_pass: V4/V5/V6/future_work`。

每个阶段 PASS 后只更新：

```text
current_stage
status
current_blocker
next_allowed_action
```

Agent 不应该根据聊天历史或旧 smoke 记录自行重排任务。

---

# 6. 当前此刻的唯一下一步

Agent **现在只允许维持 blocker 并等待明确决策**：

```text
blocked_waiting_for_explicit_protocol_deviation
→ 不再运行 live probe；等待明确批准的 deviation 或有证据的 service-side correction
```

冻结 probe `005_fresh_restart` 已失败并完成证据审阅，当前 blocker 保持不变。
不得修改 construction service；如确需改变冻结合同或远端配置，先停止并请求明确
批准 deviation，远端修改还必须先扩展受限脚本的 write permission。

现在不要：

```text
复用 v3_smoke_002 partial cache
重复运行 frozen public-path compatibility probe
跑新的 full smoke / v3_smoke_003
跑 calibration
跑 M1
跑 72 runs
做 phase profiling
做 load/concurrency sweep
改 Batch Invariance
设计新方法
```

---

# 7. 一句话原则

> **先让 correctness harness 能稳定回答“同样的外部模型结果下，M1/M2 是否保持 M0 的 execution trajectory 与语义”；再测 Deterministic-Graphiti-Serial 的真实瓶颈；再做 M1/M2 基础性能比较；完成正式 pilot 后停止。任何后续方法设计都不属于当前 Agent 的任务。**
