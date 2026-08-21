# MemBind SFWB v1.3 Protocol Cleanup + V5 Root-Cause TDD Workplan

> 日期：2026-08-21  
> 适用仓库：`https://github.com/ysyjsk/MemBind`  
> 当前执行基准：`saturated_fixed_work_baseline_v1_3`  
> 状态：**DEVELOPMENT / PROTOCOL-CLEANUP / V5 ROOT-CAUSE ONLY**  
> 核心原则：**先把公共实验尺子收干净，再重做 v3.1，再决定 V5。不要让 protocol 自己成为研究对象。**

---

# 0. 这份 Workplan 基于什么

本计划只使用以下已确认的真实仓库/工作区事实，不假设不存在的模块已经存在。

## 0.1 当前 GitHub `main` 中真实存在

公开仓库当前仍有：

```text
saturated_fixed_work_baseline_v1_3/
├── configs/
│   ├── protocol_v1_3.yaml
│   └── resource_policy.json
├── src/saturated_fixed_work_baseline_v1_3/
│   ├── __init__.py
│   ├── preflight.py
│   ├── production_sampler.py
│   ├── resource_evidence.py
│   └── test_qualification.py
├── PROTOCOL.md
├── README.md
├── TEST_QUALIFICATION_GATE.md
└── pyproject.toml
```

公开 `README.md` 仍说明 v1.3 主要复用：

```text
saturated_fixed_work_baseline_v1_2/src/saturated_fixed_work_baseline_v1_2/v1_3.py
```

公开 `PROTOCOL.md` / `protocol_v1_3.yaml` 仍包含：

```text
RESOURCE_ENVELOPE_ID
GPU UUID
resource gate
1 Hz telemetry
production sampler
```

因此公开 `main` 明显落后于当前本地实验事实。

公开仓库同时真实存在：

```text
paper-eval-v3/src/paper_eval/membind_v31/
```

包含旧 v3.1 的：

```text
adapter.py
admission.py
coordinator.py
graphiti_adapter.py
live_runtime.py
production_executor.py
scheduler.py
...
```

该目录只作为历史实现和 seam 审计参考；**本计划不修改、不覆盖旧 v3.1**。

公开仓库也真实存在：

```text
mab_quality_v2_final_qa/
```

它已经实现：

```text
one construction -> many read-only QA
paired U0/MemBind reduction
Recall@1/3/5/10
MRR
nDCG@10
Judge-valid accuracy
context-cluster bootstrap
```

后续 quality evaluation 应复用其代码思想/adapter，不重复造一套 QA framework。

---

## 0.2 当前本地工作区已经完成，但公开 GitHub 尚未包含的真实进展

当前本地 `saturated_fixed_work_baseline_v1_3` 已经额外存在并通过测试：

```text
simple campaign / qualification execution path

src/saturated_fixed_work_baseline_v1_3/membind_v5/
├── offline_analyzer.py
├── first_divergence.py
├── semantic_fingerprint.py
├── fingerprint_qualification.py
└── ...
```

当前已完成：

```text
34 passed
git diff --check PASS
py_compile PASS
```

当前已知 sealed qualification 数据：

```text
B0-A     255 logical calls
B0-B     255 logical calls
B1       184 logical calls
v3.1     316 logical calls
```

v3.1 相比 B0-A：

```text
EDGE_RESOLUTION +32
TIMESTAMP       +30
NODE_RESOLUTION -1
```

Serial self-divergence floor：

```text
entity key  2
edge key    4
attribute   6
temporal    6
source link 4
```

v3.1 vs B0-A canonical divergence 远高于 Serial self-divergence。

当前正式 V5 gate：

```text
STOP_V5_FIRST_DIVERGENCE_INSUFFICIENT_OBSERVABILITY
```

已完成 passive semantic fingerprint provider-free qualification：

```text
PASSIVE_FINGERPRINT_NONINTERFERENCE_PASS
```

但现有 sealed B0-A / v3.1 artifact 没有 paired semantic fingerprint telemetry，因此：

```text
FIRST SEMANTIC DIVERGENCE 仍未定位
任何 GO_V5_* mechanism 仍未授权
```

---

# 1. 本计划的唯一目标

把当前项目收成一条清晰、审稿人容易理解、Agent 能稳定执行的研究主线：

```text
公共 SFWB protocol
    ↓
Native Serial reference
    ↓
Naive Whole-Update Async unsafe reference
    ↓
semantic observability
    ↓
定位 v3.1 first semantic divergence
    ↓
根据证据决定 V5 最小机制
    ↓
重新实现 v3.1（独立 lane）
    ↓
实现 V5
    ↓
development evaluation
    ↓
method freeze
    ↓
held-out formal evaluation
```

本轮首先完成：

```text
A. protocol source-of-truth cleanup
B. common backend/lifecycle freeze
C. semantic fingerprint 真实 seam 接入资格
D. source-0 first-semantic-divergence diagnostic
```

**在 D 得到可证明结论前，不实现 V5 runtime。**

---

# 2. 全局冻结项：Agent 不得修改

## 2.1 两个 vLLM 启动命令已经由用户最终冻结

### Construction 8000

```bash
cd ~/liuyi
conda activate /home/lhx/liuyi/.venv

mkdir -p "$HOME/liuyi/logs"

MODEL_DIR="$(realpath ./models/Qwen3-32B-FP8)"

export VLLM_USE_FLASHINFER_SAMPLER=0

CUDA_VISIBLE_DEVICES=1 \
timeout -k 60s 6h \
vllm serve "$MODEL_DIR" \
  --served-model-name qwen3-32b-fp8 \
  --host 10.87.5.247 \
  --port 8000 \
  --hf-overrides '{"rope_parameters":{"rope_type":"yarn","factor":2.0,"original_max_position_embeddings":32768,"rope_theta":1000000}}' \
  --max-model-len 65536 \
  --gpu-memory-utilization 0.75 \
  --structured-outputs-config '{"backend":"xgrammar"}' \
  --enable-prefix-caching \
  --enable-chunked-prefill \
  --scheduling-policy fcfs \
  --default-chat-template-kwargs '{"enable_thinking":false}' \
  2>&1 | tee "$HOME/liuyi/logs/qwen3-32b-fp8-server-gpu0-8000.log"
```

### Embedding 8001

```bash
cd ~/liuyi
conda activate /home/lhx/liuyi/.venv

mkdir -p "$HOME/liuyi/logs"

MODEL_DIR="$(realpath ./models/Qwen3-Embedding-0.6B)"

CUDA_VISIBLE_DEVICES=1 \
timeout -k 60s 6h \
vllm serve "$MODEL_DIR" \
  --runner pooling \
  --served-model-name qwen3-embedding-0.6b \
  --host 10.87.5.247 \
  --port 8001 \
  --dtype bfloat16 \
  --max-model-len 32768 \
  --gpu-memory-utilization 0.15 \
  --max-num-batched-tokens 32768 \
  --max-num-seqs 128 \
  2>&1 | tee "$HOME/liuyi/logs/qwen3-embedding-0.6b-server-gpu0-8001.log"
```

规则：

```text
禁止修改这些命令
禁止为某个 method 单独改变 vLLM 参数
禁止扫描 max_num_seqs / max_num_batched_tokens / gpu_memory_utilization
禁止把 timeout 6h 当 method 参数
```

`timeout` 只属于 operator watchdog：

```text
被 watchdog 终止 -> INFRA_FAILURE
不计入 method design
不计入 T_build
```

---

## 2.2 不允许重新引入 application-side magic concurrency

新的公共 protocol 不允许定义：

```text
K_LLM
compile_workers
lookahead
method-specific semaphore
method-specific concurrency sweep
```

`bind_workers=1` 也不要作为可调参数出现。

如果未来 V5 需要 source-order state effect：

```text
ordered bind / ordered publication
```

这是 semantic invariant，不是超参数。

---

## 2.3 旧 artifact 全部只读

不得修改、补写、reseal、supersede：

```text
已有 B0-A
已有 B0-B
已有 B1
已有 MemBind v3.1
所有旧 STOP
所有 sealed roots
```

尤其保留：

```text
saturated_fixed_work_baseline_v1_3/artifacts/sfwb-v1-3-simple-20260821-004
saturated_fixed_work_baseline_v1_3/artifacts/sfwb-v1-3-membind-ext-20260821-001
saturated_fixed_work_baseline_v1_3/artifacts/sfwb-v1-3-v5-analysis-20260821-001
saturated_fixed_work_baseline_v1_3/artifacts/sfwb-v1-3-v5-first-divergence-*
saturated_fixed_work_baseline_v1_3/artifacts/sfwb-v1-3-v5-semantic-fingerprint-20260821-004
```

任何新结果：

```text
必须进入新的 append-only artifact root
```

---

# 3. 新 Protocol 的科学定义

最终统一成：

\[
\boxed{
\text{Same Workload}
+
\text{Same Backend}
+
\text{Same Lifecycle}
+
\text{Same Measurement}
\rightarrow
\text{Different Execution Policy}
}
\]

主实验研究：

> 在固定 memory history、固定 source order、固定 serving backend、固定 measurement 下，不同 execution policy 完成完整 stateful memory construction 的时间、work volume、correctness 与 downstream quality。

## 3.1 Workload

保持现有：

```text
ordered saturated fixed-work
no synthetic arrival
no rho
no think time
reference_time 保持数据语义，不转成 wall-clock sleep
```

当前四个 history：

```text
07741c45
b6019101
6071bd76
a2f3aa27
```

永久标记：

```text
DEVELOPMENT_EXPOSED
```

它们不得再被包装成最终 held-out paper evidence。

## 3.2 Backend

固定为：

```text
8000 Qwen3-32B-FP8
8001 Qwen3-Embedding-0.6B
same Neo4j
same Graphiti version
same client behavior
```

```text
hardware identity 是 Experimental Setup 描述
不是 runtime gate
```

不要重新引入：

```text
GPU UUID
PID
EngineCore PID
CUDA environment
resource-evidence
resource provenance
provider collector
```

## 3.3 Execution policy

### B0 — Native Serial

```python
for episode in ordered_history:
    await graphiti.add_episode(...)
```

要求：

```text
whole-update active max = 1
source-order durable completion
no application-side semantic rewrite
```

### B1 — Naive Whole-Update Async

```python
tasks = []
for episode in ordered_history:
    tasks.append(asyncio.create_task(graphiti.add_episode(...)))

await gather_with_full_exception_accounting(tasks)
await durable_drain()
```

要求：

```text
source task creation order = source order
no application semaphore
no worker pool cap
no ordered-commit shim
no repair
no semantic coordination
```

B1 发生 work/canonical/ordering divergence 是实验结果，不是自动 runtime invalidation。

### Future v3.1 / V5

本轮不实现。

V5 Core 应保持无 magic number：

```text
semantic-ready work becomes executable
exact predecessor-state-dependent work waits for true dependency
durable effects preserve required Native Serial order
backend decides realized batching/capacity
```

---

# 4. Protocol Cleanup：先解决 source-of-truth 冲突

## P0 — Repository State Audit

Agent 必须先执行：

```bash
pwd
git status --short
git rev-parse HEAD
git diff --check
find saturated_fixed_work_baseline_v1_3 -maxdepth 4 -type f | sort
```

再精确确认：

```text
simple campaign 的真实文件位置
membind_v5 的真实文件位置
当前 tests 的真实文件列表
当前 configs
当前 artifact roots
```

不得根据 GitHub 页面反向覆盖本地更新。

生成：

```text
saturated_fixed_work_baseline_v1_3/LOCAL_PROTOCOL_STATE_AUDIT.md
```

必须明确：

```text
PUBLIC_MAIN_STATE
LOCAL_WORKSPACE_STATE
LOCAL_ONLY_IMPLEMENTATION
HISTORICAL_ONLY_COMPONENTS
ACTIVE_EXECUTION_PATH
```

若本地真实 runner 与用户最新报告不一致：

```text
STOP_PROTOCOL_STATE_AMBIGUOUS
```

---

# 5. P1 — 移除 resource-forensic 正式依赖

要修改的真实文件：

```text
saturated_fixed_work_baseline_v1_3/PROTOCOL.md
saturated_fixed_work_baseline_v1_3/configs/protocol_v1_3.yaml
saturated_fixed_work_baseline_v1_3/README.md
saturated_fixed_work_baseline_v1_3/TEST_QUALIFICATION_GATE.md
```

从 active protocol/config/runner gate 中完全移除：

```text
RESOURCE_ENVELOPE_ID
resource_gate
gpu_uuid
sampler
telemetry lane
provider collector requirement
historical/current resource parity
```

若以下历史文件已完全不在 active execution path：

```text
preflight.py
production_sampler.py
resource_evidence.py
resource_policy.json
```

优先先解除 active import / config / formal contract，再决定是否物理删除。不要为了目录整洁引入额外 regression。

### TDD：先 RED

```text
test_protocol_has_no_resource_envelope_gate
test_protocol_has_no_gpu_uuid_requirement
test_protocol_has_no_provider_sampler_requirement
test_active_campaign_imports_no_resource_evidence
test_block_validity_does_not_depend_on_resource_identity
```

---

# 6. P2 — 冻结 Common Backend Contract

新增 machine-readable：

```text
saturated_fixed_work_baseline_v1_3/configs/frozen_backend_v1_3.json
saturated_fixed_work_baseline_v1_3/configs/frozen_client_v1_3.json
```

Backend 只记录用户启动命令中显式指定值。

**不要 invent** construction 端未显式指定的：

```text
max_num_seqs
max_num_batched_tokens
```

未指定项写成：

```text
vLLM pinned-version default
```

Client config 必须从真实 Graphiti/Qwen client 审计：

```text
temperature
top_p
max_tokens
seed if actually used
structured-output request mode
HTTP timeout
retry policy
```

不要为了确定性发明新 decode。

### TDD

```text
test_b0_b1_use_same_llm_client_config
test_b0_b1_use_same_embedding_config
test_no_method_specific_decode_override
test_no_method_specific_retry_override
test_no_method_specific_backend_override
```

---

# 7. P3 — 统一 Block Lifecycle

标准 lifecycle：

```text
1. fresh namespace
2. backend state preparation
3. service ready
4. fixed disjoint warmup
5. backend idle
6. CLOCK_MONOTONIC
7. submit formal E0
8. construction
9. drain all method-caused work
10. last durable DB acknowledgement
11. CONSTRUCTION_DURABLE_COMPLETE
12. stop T_build
13. validation/canonical/correctness
14. artifact seal
15. QA
```

以下全部在 timer 外：

```text
warmup
model load
service start
canonical projection
correctness checker
artifact hashing
QA
```

### Prefix Cache 初始状态

8000 开启 prefix caching。正式 performance block 前：

```text
使用相同冻结命令重启 construction backend
→ ready
→ fixed disjoint warmup
→ idle
→ benchmark
```

如果 restart 由用户手动执行，runner 只做 ready + warmup + idle；不要再造 remote PID/GPU validation。

### TDD

```text
test_timer_starts_after_warmup
test_timer_starts_after_backend_idle
test_timer_starts_before_formal_e0
test_timer_stops_after_all_registered_work_terminal
test_timer_stops_after_last_durable_write_ack
test_validation_runs_outside_build_timer
test_retry_time_is_inside_build_timer
test_each_block_requires_fresh_namespace
```

---

# 8. P4 — Native Serial Certification

B0 必须尽量走：

```text
upstream Graphiti behavior
+
thin instrumentation
```

不能复制一套“看起来像 Graphiti”的实现再叫 Native。

Provider-free captured fixture 比较：

```text
official-style serial loop
vs
B0 harness
```

比较：

```text
source coverage
logical operator sequence
LLM request cardinality
embedding cardinality
DB effect cardinality
publication order
canonical projected state
```

输出：

```text
NATIVE_SERIAL_CERTIFICATION.json
```

### TDD

```text
test_b0_matches_native_serial_operator_lineage
test_b0_matches_native_serial_work_cardinality
test_b0_matches_native_serial_publication_order
test_b0_matches_native_serial_effects_on_captured_fixture
```

若无法证明：

```text
STOP_NATIVE_SERIAL_REFERENCE_NOT_CERTIFIED
```

---

# 9. P5 — Semantic Fingerprint：从 helper 变成真实 passive observer

当前已有：

```text
semantic_fingerprint.py
fingerprint_qualification.py
first_divergence.py
```

并已有：

```text
PASSIVE_FINGERPRINT_NONINTERFERENCE_PASS
```

下一步**不是继续增加 analyzer**，而是把 observer 接到真实 B0 / legacy v3.1 seam。

## 9.1 已审计真实 seam

v3.1 `prepare()`：

```text
extract_nodes
extract_edges
```

v3.1 `bind()`：

```text
resolve_extracted_nodes
resolve_edge_pointers
resolve_extracted_edges
extract_attributes_from_nodes
_process_episode_data
```

真实 callable 以当前工作区 Graphiti 0.29.3 binding 为准。

## 9.2 最小 fingerprint

每个 source/operator：

```text
source_id
operator_type
semantic_stage
input_semantic_hash
output_semantic_hash
candidate_identity_hash
candidate_order_hash
candidate_count
bound_state_version
batch_membership_hash
batch_order_hash
batch_size
resolution_decision_hash
effect_hash
effect_count
publication_version
```

不可观测写：

```text
NOT_OBSERVABLE
```

禁止使用：

```text
repr(object)
object address
runtime id
request id
timestamp
namespace
run id
trace id
```

## 9.3 非干扰规则

Observer 必须：

```text
不改变 argument
不改变 return value
不改变 await/order
不发 provider request
不查 DB
不增加 retry
不创建 scheduler
不产生额外 semantic operator
```

### TDD

```text
test_real_seam_observer_preserves_args
test_real_seam_observer_preserves_return_value
test_real_seam_observer_preserves_call_count
test_real_seam_observer_preserves_batch_membership
test_real_seam_observer_adds_zero_provider_calls
test_real_seam_observer_adds_zero_db_io
test_real_seam_observer_preserves_publication
```

不能只测 synthetic helper；至少对真实 seam callable 做 monkeypatch/captured fixture non-interference。

---

# 10. P6 — Source-0 First Semantic Divergence Diagnostic

只有以下全部 PASS 才授权：

```text
Protocol cleanup PASS
Native Serial certification PASS
Passive real-seam observer non-interference PASS
all experiment-critical tests PASS
```

然后才允许启动一个 diagnostic：

```text
B0 source 0
vs
legacy v3.1 source 0
```

它是：

```text
SEMANTIC_DIAGNOSTIC
```

不是：

```text
PERFORMANCE_RUN
FORMAL_RESULT
MAIN_TABLE
```

严格按顺序定位：

```text
source evidence
→ node extraction input
→ node extraction output
→ edge extraction input
→ edge extraction output
→ prepared object
→ candidate formation
→ exact state binding
→ resolution batch
→ resolution decision
→ attribute/summary/timestamp
→ effect
→ persistence
→ publication
```

只允许分类：

```text
EXTRACTION_DIVERGENCE
STATE_SNAPSHOT_OR_CANDIDATE_DIVERGENCE
BATCHING_DIVERGENCE
RESOLUTION_DECISION_DIVERGENCE
DUPLICATE_CONSUMPTION
PERSISTENCE_DIVERGENCE
OBSERVABILITY_INSUFFICIENT
UNKNOWN
```

### Sequential escalation

```text
source 0 若已定位 -> 立即停止
source 0 完全等价 -> 才允许 source 1
source 1 等价 -> 才允许 source 2
...
```

不要一次性跑 0..11。

---

# 11. P7 — V5 Mechanism Decision Gate

根据**最早可证明 semantic divergence**决定：

### A. Extraction 已不同

```text
GO_V5_NATIVE_EQUIVALENT_COMPILE
```

### B. Extraction 相同，candidate/state 开始不同

```text
GO_V5_SERIAL_EQUIVALENT_STATE_BIND
```

### C. Candidate/state 相同，batch membership 不同

```text
GO_V5_NATIVE_BATCH_PRESERVATION
```

### D. 前面全一致，只是相同 work 被重复消费

```text
GO_V5_SEMANTIC_WORK_DEDUPLICATION
```

### E. 多因素

```text
GO_V5_COMBINED
PRIMARY_ROOT_CAUSE = ...
SECONDARY_CONSEQUENCE = ...
```

### F. 仍不可观测

```text
STOP_V5_FIRST_DIVERGENCE_INSUFFICIENT_OBSERVABILITY
```

只补最小缺失字段，禁止直接实现机制。

---

# 12. P8 — V5 实现 TDD 原则

只有 P7 明确 GO 才进入。

在 root cause 不要求之前，不实现：

```text
scheduler
priority heuristic
dynamic K
K sweep
lookahead
cache-affinity
SHADOW_READ
stale-state speculation
repair
conflict-aware scheduler
```

V5 Core 理想形式：

```text
semantic-safe work
    ↓
READY
    ↓
eagerly exposed to common backend
    ↓
exact-state-dependent work waits for true semantic dependency
    ↓
ordered durable effects/publication
```

真正 GPU batching/capacity 交给共同 vLLM backend，不增加 method-specific concurrency cap。

TDD 顺序：

```text
RED semantic contract
↓
minimum implementation
↓
provider-free captured equivalence
↓
source-0 deterministic/captured equivalence
↓
prefix equivalence
↓
12-source semantic qualification
↓
live performance
```

不得先跑 speedup 再修 correctness。

---

# 13. v3.1 重新实现的定位

用户会单独重做 v3.1。

本 workplan 只规定：

```text
paper-eval-v3/src/paper_eval/membind_v31/
```

保持历史只读。

新 v3.1：

```text
必须使用同一 SFWB v1.3 harness
必须 fresh namespace
必须 common backend
必须 common lifecycle
必须 common measurement
必须重新 live
不得复用旧 v3.1 性能数字
```

新 v3.1 可以作为 historical design ablation，但不能成为 V5 correctness contract 的 source-of-truth。

---

# 14. P9 — Performance / Semantic Instrumentation 分离

定义：

```text
PERFORMANCE
SEMANTIC_QUALIFICATION
```

## PERFORMANCE

只允许：

```text
monotonic timing
logical operator type/count
LLM calls/attempts/input-output tokens
embedding calls/items
DB work
retry
minimal queue/concurrency counters
publication event
```

## SEMANTIC_QUALIFICATION

额外允许：

```text
semantic fingerprints
candidate identity/order
state version
batch membership
effect hash
```

硬规则：

```text
headline speedup 只能来自 PERFORMANCE mode
heavy fingerprint run 只证明 correctness/root cause
```

---

# 15. P10 — Work Volume 必须进入主结果

Reducer 固定一级输出：

```text
PERFORMANCE
WORK_VOLUME
CORRECTNESS
QUALITY
```

Construction 主表最低字段：

```text
method
history
episodes
source_tokens
makespan
episodes/s
source_tokens/s
speedup_vs_B0
LLM logical calls
transport attempts
LLM input tokens
LLM output tokens
embedding calls/items
DB writes/transactions
retry count
direct semantic violations
semantic equivalence status
canonical relation
```

B1 这种：

```text
983s -> 483s
717k -> 213k LLM input tokens
```

必须让 reviewer 一眼看到，不能只放 `2.03x speedup`。

---

# 16. P11 — Artifact seal 修复

当前已知：

```text
各 block seal 有效
qualification top-level declared payload hash 有 MISMATCH_DIAGNOSTIC
```

不重跑旧 block，只修新的 offline serialization/sealing code。

### RED

```text
test_same_payload_same_hash
test_write_read_same_hash
test_wrapper_declared_hash_matches_payload
test_hash_ignores_nonpayload_write_order
```

禁止：

```text
重新 seal 旧 artifact
覆盖旧 qualification_result.json
增加复杂 provenance framework
```

---

# 17. P12 — Development / Held-out 硬隔离

当前四个 history 永久：

```text
DEVELOPMENT_EXPOSED
```

代码里必须有 scope，而不只是 Markdown。

建议在现有 config 体系加入：

```text
development manifest
heldout manifest reference
```

本轮不得读取/挑选 held-out 内容。

held-out IDs 必须在 method freeze 后、formal evaluation 前由用户/正式 protocol 冻结；Agent 不得自己挑“看起来合适”的 history。

Gate：

```text
method_frozen = false
→ heldout execution forbidden
```

---

# 18. P13 — Run Order

未来可能比较：

```text
B0
B1
new v3.1
V5
```

不能永远固定 B0→B1→V5。

3 方法 development 用预注册 6 permutation：

```text
ABC
BCA
CAB
ACB
CBA
BAC
```

4 方法可用 fixed-seed balanced Latin-square / preregistered rotation。

关键要求：

```text
结果产生前决定
结果产生后不可改
```

---

# 19. P14 — QA Evaluation

不要再造新 QA framework。

优先复用：

```text
mab_quality_v2_final_qa/
```

现有能力：

```text
one construction -> many QA
read-only QA facade
gold label isolation
Recall@1/3/5/10
MRR
nDCG@10
Judge-valid Accuracy
paired disagreement
context-cluster bootstrap
```

当前该目录是 development-only lane，所以：

```text
复用代码
不复用 development result 作为 final held-out claim
```

最终实验单位：

```text
history/context
```

不是 episode 或 QA row。

---

# 20. GPU / KV / APC 在最终 Protocol 中的位置

完全移出 validity gate：

```text
GPU UUID
PID
EngineCore PID
CUDA environment
process mapping
```

Optional mechanism diagnostics：

```text
GPU utilization
vLLM running/waiting
KV usage
APC hit rate
```

能直接拿就记录；缺失不阻塞 main experiment。

只有未来 claim cache mechanism 时，才额外要求：

```text
cached prefix tokens
recomputed prefill tokens
prefill/TTFT
```

V5 Core 当前不要混入 cache-affinity。

---

# 21. Test Qualification：只卡 experiment-critical regression

不要回到 repository-wide `tests_all_green`。

新的 gate：

```text
saturated_fixed_work_baseline_v1_3/tests PASS
affected cross-package tests PASS
no change-induced regression affecting:
    workload
    B0
    B1
    lifecycle
    measurement
    correctness
    reducer
```

无关旧项目失败记录即可，不阻塞。

当前标准命令：

```bash
PYTHONPATH=saturated_fixed_work_baseline_v1_3/src \
paper-eval-v3/.venv/bin/pytest -q \
saturated_fixed_work_baseline_v1_3/tests
```

每阶段至少执行：

```bash
git diff --check

PYTHONPATH=saturated_fixed_work_baseline_v1_3/src \
paper-eval-v3/.venv/bin/pytest -q \
saturated_fixed_work_baseline_v1_3/tests

python -m py_compile \
saturated_fixed_work_baseline_v1_3/src/saturated_fixed_work_baseline_v1_3/membind_v5/*.py

git status --short
```

---

# 22. TDD 执行纪律

每个 stage：

```text
1. 写 RED test
2. 单独运行并确认确实 RED
3. 做最小实现
4. focused GREEN
5. whole v1.3 GREEN
6. git diff --check
7. artifact/report
8. 再进入下一 stage
```

禁止先写几百行实现最后补测试。

---

# 23. 推荐执行顺序

```text
P0  Local repository state audit
    ↓
P1  Protocol source-of-truth cleanup
    ↓
P2  Frozen backend/client contract
    ↓
P3  Common block lifecycle
    ↓
P4  Native Serial certification
    ↓
P5  Real-seam passive semantic fingerprint qualification
    ↓
P6  source-0 B0 vs legacy-v3.1 semantic diagnostic
    ↓
P7  first semantic divergence decision
    ↓
    ├── STOP if still insufficient
    └── GO one minimal V5 mechanism
            ↓
P8  V5 RED→GREEN semantic implementation
            ↓
P9  performance instrumentation lane
            ↓
P10 12-source V5 qualification
            ↓
development full histories
            ↓
new v3.1 rebuild as separate comparison
            ↓
method freeze
            ↓
held-out formal evaluation
            ↓
MAB paired quality
```

---

# 24. 现在绝对不要做的事情

在 first semantic divergence 未定位前：

```text
不要实现 V5 scheduler
不要做 dynamic admission
不要做 K sweep
不要加 lookahead
不要做 backend saturation experiment
不要做 SHADOW_READ
不要做 stale-state speculation
不要做 repair
不要做 conflict-aware scheduler
不要为了 2x 结果 tuning
不要重新跑 4-history formal main table
不要看 held-out
```

---

# 25. 当前 Agent 的第一轮具体任务

Agent 下一轮必须只完成：

```text
A. audit 本地 active protocol path
B. 清理 v1.3 protocol/config 中旧 resource-forensic contract
C. 冻结 backend/client machine-readable config
D. 统一并测试 BlockLifecycle
E. 对 B0 做 Native Serial provider-free certification
F. 把现有 semantic fingerprint 接到真实 seam 的 provider-free fixture
G. whole v1.3 tests 全绿
```

完成后输出：

```text
LOCAL_PROTOCOL_STATE_AUDIT.md
PROTOCOL_SOURCE_OF_TRUTH_AUDIT.md
FROZEN_BACKEND_CONFIG.json
FROZEN_CLIENT_CONFIG.json
BLOCK_LIFECYCLE_CONTRACT.md
NATIVE_SERIAL_CERTIFICATION.json
REAL_SEAM_FINGERPRINT_NONINTERFERENCE.json
PROTOCOL_CLEANUP_DECISION.md
```

只有最终 decision：

```text
GO_SOURCE0_SEMANTIC_DIAGNOSTIC
```

才允许下一轮启动 source-0 live diagnostic。

---

# 26. 可直接交给 Agent 的执行 Prompt

```text
继续推进 MemBind，当前唯一正式实验基准仍为
saturated_fixed_work_baseline_v1_3。

你必须先读取：
- saturated_fixed_work_baseline_v1_3/PROTOCOL.md
- saturated_fixed_work_baseline_v1_3/configs/protocol_v1_3.yaml
- saturated_fixed_work_baseline_v1_3 当前本地 src/tests
- 当前 simple campaign 真实执行路径
- saturated_fixed_work_baseline_v1_3/src/saturated_fixed_work_baseline_v1_3/membind_v5/
  中现有 offline_analyzer.py、first_divergence.py、
  semantic_fingerprint.py、fingerprint_qualification.py
- 最新 semantic fingerprint artifact root
- paper-eval-v3/src/paper_eval/membind_v31/ 仅用于历史 seam 审计，
  不得修改旧 v3.1。
- mab_quality_v2_final_qa/ 仅作为未来 QA 实现复用参考。

首先执行 repository state audit。
不要假设 GitHub public main 与本地工作区一致；
以本地当前真实文件为执行 authority，同时记录 public-vs-local drift。

本轮不实现 V5 runtime，不重新实现 v3.1，不启动 live provider。

用户已经最终冻结 8000/8001 的 vLLM 启动命令。
禁止修改、扫描、调优这些启动参数。
不要新增 K_LLM、compile_workers、lookahead 或任何 method-specific
application concurrency cap。

本轮严格采用 TDD：
每个 contract 先 RED，再做最小 GREEN。

阶段 1：
清理 v1.3 正式 protocol source-of-truth。
从 active protocol/config/runner gate 中完全移除：
RESOURCE_ENVELOPE_ID、GPU UUID、PID、EngineCore PID、CUDA mapping、
resource-evidence、production sampler、historical/current resource parity。
不要改成 optional；不要新增 v1.4。
旧文件若已经不在 active import path，可以历史保留，但不得继续影响 formal validity。

阶段 2：
根据用户冻结命令生成 machine-readable FROZEN_BACKEND_CONFIG。
只记录显式指定值；construction 未显式指定的 max_num_seqs /
max_num_batched_tokens 不得擅自写死。
审计真实 Graphiti client，生成 FROZEN_CLIENT_CONFIG，
记录真实 temperature/top_p/max_tokens/retry/timeout/structured-output
行为，不要 invent 新 decode。

阶段 3：
实现/收口所有方法共用的 BlockLifecycle：
fresh namespace
→ backend state prepared
→ service ready
→ fixed disjoint warmup
→ backend idle
→ start CLOCK_MONOTONIC
→ formal construction
→ drain all method-caused work
→ last durable write ack
→ CONSTRUCTION_DURABLE_COMPLETE
→ stop timer
→ validation/canonical/seal/QA outside timer。

prefix caching 保持用户现有 8000 配置。
正式 performance block 的 cache 初始状态通过相同 frozen backend
restart/operator procedure 保证；不要重新建立 remote resource validation。

阶段 4：
对 B0 建立 Native Serial provider-free certification。
比较 official-style sequential graphiti.add_episode loop 与 B0 harness，
验证 operator lineage、work cardinality、effect、publication。
B0 不得复制/重写 upstream semantics 后再自称 Native。

阶段 5：
保留现有 semantic_fingerprint helper，
但必须证明 observer 能安全 attach 到真实 extraction/candidate/
resolution/effect seam。
provider-free fixture 必须验证：
- args unchanged
- return unchanged
- call count unchanged
- batch membership unchanged
- effect unchanged
- publication unchanged
- zero extra provider calls
- zero extra DB I/O

现有 sealed artifact 不包含 paired fingerprint，因此本轮不得声称
first semantic divergence 已定位。

阶段 6：
运行：
PYTHONPATH=saturated_fixed_work_baseline_v1_3/src \
paper-eval-v3/.venv/bin/pytest -q \
saturated_fixed_work_baseline_v1_3/tests

并执行：
git diff --check
py_compile
git status --short

不要让 tracked __pycache__ / *.pyc 继续污染实验工作树；
如果它们已被 git track，先审计后做最小 gitignore/index cleanup，
不要删除任何科研 artifact。

本轮不得：
- 修改任何已有 sealed B0-A/B0-B/B1/MemBind artifact
- live 调 8000/8001
- live 调 Neo4j
- source-0 diagnostic
- V5 mechanism
- scheduler/admission
- K sweep
- backend saturation
- GPU/KV causality experiment
- held-out evaluation

本轮最终必须输出：
- LOCAL_PROTOCOL_STATE_AUDIT.md
- PROTOCOL_SOURCE_OF_TRUTH_AUDIT.md
- FROZEN_BACKEND_CONFIG.json
- FROZEN_CLIENT_CONFIG.json
- BLOCK_LIFECYCLE_CONTRACT.md
- NATIVE_SERIAL_CERTIFICATION.json
- REAL_SEAM_FINGERPRINT_NONINTERFERENCE.json
- PROTOCOL_CLEANUP_DECISION.md

最终 decision 只允许：
GO_SOURCE0_SEMANTIC_DIAGNOSTIC
或
STOP_PROTOCOL_CORE_NOT_READY

只有 GO_SOURCE0_SEMANTIC_DIAGNOSTIC 才允许下一轮单独授权
B0-vs-legacy-v3.1 source-0 semantic diagnostic。
```

---

# 27. 最终科学主线

当前项目最重要的问题是：

\[
\boxed{
\text{Where is the earliest semantic boundary at which v3.1 stops being Native-Serial-equivalent?}
}
\]

然后 V5 只修这个边界。

最终论文逻辑应当是：

```text
Native Serial:
correct reference, but whole-update boundary over-serializes legal work

Naive Whole-Update Async:
exposes parallel headroom, but changes state-dependent work

v3.1:
shows strong performance potential, but current implementation does not yet
establish semantic-work equivalence

V5:
moves only semantically legal work early,
preserves Native work/state effects,
and lets the common vLLM backend realize the available parallelism
without method-specific concurrency magic numbers
```

**先证明“算什么不变”，再证明“什么时候算”能更快。**
