# MemBind Saturated Fixed-Work Construction Baseline Workplan v1.2

> 文档版本：`MEMBIND_SATURATED_FIXED_WORK_BASELINE_WORKPLAN_V1_2`  
> 待实现协议：`SATURATED_FIXED_WORK_CONSTRUCTION_PROTOCOL_V1_2`  
> 目标：在当前 MemBind v5 上，以测试驱动开发（TDD）实现隔离的新协议，完成 `B0 Native Serial` 与 `B1 Naive Whole-Update Async`，得到 development 主实验构建表和对齐的 Multi-QA 质量表后才可成功停止。  
> 权威性：本文件取代 `MemBind_SATURATED_FIXED_WORK_BASELINE_WORKPLAN_v1.1.md` 作为下一轮 baseline 的执行协议；v1.1 只作历史记录，不得混用。  
> 本文件是执行指令；此前工作计划只是需求来源。遇到冲突时，以本文件、实际 v5 代码和本次预注册的实验合同为准。

### 权威输入与远程拓扑

- 规划依据只限于：用户提供的 `MemBind_SATURATED_FIXED_WORK_BASELINE_WORKPLAN_v1.1.md`，以及 [MemBind GitHub 仓库](https://github.com/ysyjsk/MemBind) 当前 v5 代码、其中已提交的配置/实验资产和本文列出的论文官方代码。
- 不把控制端、本机旧 checkout、本机 GPU/Neo4j、相邻目录或未提交文件当作项目事实，也不从中导入实现或实验数据。
- 这是完全远程项目：代码 checkout、实现、测试、数据、runner、Neo4j、vLLM、GPU 采样和最终 artifacts 都必须位于远程实验环境。当前 Codex/Windows 环境只负责发起远程命令、观察和收取结果，不参与被测 workload。
- 文中的相对路径均相对远程 MemBind v5 checkout；`localhost` 仅指远程 runner 进程所在主机，绝不指当前控制端。

### v1.2 相对 v1.1 的冻结修正

1. Feeder 不再有跨方法的“统一非阻塞”合同：B0 的 admission 是逐条等待，B1 的 admission 才是源序 eager submission。
2. `future_source_payload_read` 是 harness/protocol violation；并发产生的 `future_persistent_state_read` 是可报告的 semantic outcome。
3. completion/publication inversion 先归类为 ordering observation；只有存在可证明的错误读写或显式 source-order visibility 合同被破坏时，才计入 direct semantic violation。
4. Multi-QA 改用当前 v5 的 4-history/16-QA authored extension，直接查询正式 8 个封存 namespace；`mab_quality_v2_final_qa` 只提供设计参考，不再启动额外 MAB construction suite。
5. 12-source Serial A/A 只检查 harness/instrumentation 的非确定性，不作为 44–49 episode 完整图的数值“floor”；完整图差异保持描述性，直接因果证据优先。
6. 固定远程历史物理资源身份、缓存/warmup 政策、交替顺序和 first-valid-attempt 选择规则。
7. 固定跨 history 聚合公式、QA invalid denominator 和成功终态；无 8 个正式构建块、32 条 QA 与两张实数主表不得完成。

---

## 0. 不可误读的总要求

执行 agent 必须同时做到以下事项：

1. 以当前 GitHub `main` 的 v5 状态为项目上下文。编写本计划时审计到的提交是 `22017eb2e9772898b11d2519968005d7d243868c`（提交信息 `v5`）。开始实施时先再次记录实际 `HEAD`；若已变化，做只读差异审计并在 manifest 中记录，不得静默假装仍是旧提交。
2. 在远程实验环境定位或创建由 `https://github.com/ysyjsk/MemBind` 得到的 v5 checkout；以 remote URL、commit 和 Git 状态取证，不接受同名但无法证明来源的目录。
3. 在远程当前 MemBind 仓库根目录新建且只在新目录实现：`saturated_fixed_work_baseline_v1_2/`。除非只是导入既有模块，不修改 v5、v4、v3.1、S5、APC 或旧 baseline 的代码和产物。
4. 严格 TDD：每项能力先有可观察失败的测试（RED），再最小实现通过（GREEN），再重构；把 RED/GREEN 命令、退出码和时间写入 `tdd_evidence.jsonl`。只补测试但没有看到测试先失败，不算完成 TDD。
5. 仅比较两个方法：
   - `B0_NATIVE_SERIAL`
   - `B1_NAIVE_WHOLE_UPDATE_ASYNC`
6. 不实现 MemBind 调度器、不使用应用层并发上限、不做修复或补偿重放、不改变 Graphiti `add_episode` 语义。
7. development 数据固定为 4 个 histories、每个方法每个 history 1 次有效运行，共 8 个有效构建块；必须另有资格测试，但资格测试不能混入主表，结果不得称为 final-paper numbers。
8. Multi-QA 必须复用当前项目已经优化和审计过的 4-history/16-QA authored extension：每个方法在每个 history 只构建一次，然后在封存图上回答该 history 的 4 个问题，不得为问题重建图。最终应有 32 条方法级 QA 行（每方法 16 条）。
9. 远程物理与服务资源必须固定并取证。agent 负责通过远程执行通道检查、启动或恢复 `:8000`、`:8001` 和 Neo4j；不能把控制端沙箱访问失败直接记成服务失败，也不能退回本机运行。
10. 唯一的成功终态是：8 个有效正式构建块、32 条 QA 行、两张主表、诊断附表和可复算原始证据都生成并通过验收。只完成代码、单元测试、smoke、一个 history 或空表，均不得标记完成。

---

## 1. 科学问题与边界

### 1.1 科学问题

在完全相同的 episodes、Graphiti 原生更新路径、模型服务、Neo4j、物理 GPU 和测量合同下：

- 相比逐 episode 串行等待，按源序一次性提交所有完整 `add_episode` 任务能获得多少构建吞吐提升？
- 提升来自提交重叠、LLM/embedding/DB 重叠中的哪一层？是否伴随排队、KV 压力、重试或尾部延迟恶化？
- 无界的 whole-update 异步提交是否引入可证明的语义错误、工作量膨胀、最终图差异或 QA 质量下降？

### 1.2 明确不回答的问题

- 不证明 MemBind v5 oracle 或任何在线调度器优于 baseline。
- 不比较不同模型、不同 GPU、不同 vLLM 参数或不同 Graphiti 版本。
- 不把 16-QA authored development extension 宣称为官方 MemoryAgentBench、完整 LongMemEval 或可泛化的最终 benchmark。
- 只有 4 个 history cluster、每块 1 次正式运行，不做显著性、等价性或优越性结论；置信区间仅作描述性不确定性展示。

---

## 2. 两个 baseline 的不可变定义

### 2.1 共同输入合同

- histories 固定为：
  - `07741c45`：49 episodes
  - `b6019101`：49 episodes
  - `6071bd76`：46 episodes
  - `a2f3aa27`：44 episodes
- 总计 188 episodes/方法。
- 每个 episode 保留原始 `session_id/history_id`、`source_sequence`、`source_hash`、`reference_time`、消息正文和角色。
- 唯一允许变化的是新建、方法隔离、history 隔离的 `group_id/namespace`。
- 在进入 runner 前冻结 ordered manifest；校验连续 source sequence、逐条 hash 和整个 manifest hash。
- 正式阶段禁止改 prompt、top-k、Graphiti 配置、模型参数、缓存政策和 timeout。

### 2.2 B0：Native Serial

对 source sequence 升序遍历，直接调用当前 Graphiti 的原生 `add_episode`，每一条完整返回后才提交下一条：

```python
for episode in ordered_episodes:
    await graphiti.add_episode(...unchanged episode fields...)
```

约束：

- 必须走项目已经使用的同一 Graphiti 构造和 `add_episode` 入口。
- 不得插入批处理、并行、预取、修复、额外缓存或替代数据库写入。
- 需用 adapter certification 证明新 B0 与 Graphiti 官方/项目原生串行循环在调用序列、参数和异常传播上相同。

### 2.3 B1：Naive Whole-Update Async

按 source sequence 依次创建完整 `add_episode` 任务；提交循环中不等待任一任务完成。全部任务创建后，统一等待所有任务到达终态：

```python
tasks = []
for episode in ordered_episodes:
    tasks.append(asyncio.create_task(graphiti.add_episode(...)))
outcomes = await asyncio.gather(*tasks, return_exceptions=True)
```

约束：

- 禁止应用层 semaphore、worker pool、令牌桶、动态 admission、显式 `max_inflight`、人工 arrival gap 和 `sleep`。
- Graphiti、HTTP client、vLLM、embedding server、Neo4j 自身的天然队列/连接限制保持封存配置，不算应用层并发上限。
- `return_exceptions=True` 仅用于确保所有已提交任务被 drain 和逐项归因；任何异常仍使该 block 失败，不能吞掉异常或只报告成功子集。
- 任务创建顺序必须等于 source sequence。执行、完成和发布顺序允许自然变化并被测量。
- harness 不能把未来 episode 正文显式传给较早 episode。若 Graphiti 因并发而读到未来已发布图状态，这是 B1 的被测语义现象，不是删掉数据或偷偷修复的理由。
- B1 是协议外层的 eager wrapper，不是 Graphiti 官方提供的 native async mode；代码、artifact 和报告中不得把它简称为 `Native Async`。

### 2.4 两个方法共同禁止项

- 禁止导入或运行 `membind_v5_oracle` 作为调度器。
- 禁止使用 S5/APC 的 U0/A0/P(C=2) 调度实现代替本协议。
- 禁止写入旧 namespace，禁止读取另一个方法的图。
- 禁止在结果出来后改变失败定义、denominator、canonicalization、QA prompt 或缓存策略。
- 禁止将缺测填为 0；禁止将重排序观察自动等同于语义错误。

---

## 3. 当前 v5 代码审计与复用边界

### 3.1 复用级别

- `REUSE`：从当前模块直接导入，最多做薄适配；不得复制一份后悄悄修改。
- `ADAPT`：复用数据结构/纯函数/组合方式，在新目录写协议专用 wrapper；必须有兼容性测试。
- `REFERENCE`：只参考思想、字段或测试，不导入其运行时调度路径。
- `DO_NOT_REUSE`：会污染本协议语义或结果，明确禁止。

### 3.2 项目代码复用矩阵

| 级别 | 当前 v5 路径/符号 | 新协议如何使用 | 不能做什么 |
|---|---|---|---|
| REUSE | `paper-eval-v3/src/paper_eval/native_baseline_runner.py` 的 development history 身份、manifest/hash、checkpoint、seal 和只读 QA guard | 冻结 4 个 history、namespace、hash、resume/封存合同 | 不照搬其中旧 U0 的实验语义 |
| ADAPT | `paper-eval-v3/src/paper_eval/baseline_suite_block_live.py` | 复用当前 Graphiti/episode/trace/quality 组件的组合方式 | 不调用其旧 U0/A0/P(C=2) schedule |
| REUSE | `paper-eval-v3/src/paper_eval/baseline_suite_artifacts.py::BaselineBlockStore` | append-only block、校验、hash、fsync/atomic seal | 不写入旧 baseline artifact root |
| ADAPT | `paper-eval-v3/src/paper_eval/apc_aligned_baseline.py` 的 lifecycle reducer、性能纯函数、direct violation 汇总 | 在新 metric schema 上做兼容 wrapper 和黄金测试 | 不复用 APC plan、C=2/4/6、arrival trace 或其 cache salt 作为协议定义 |
| REUSE/ADAPT | `paper-eval-v3/src/paper_eval/apc_aligned_correctness.py`、`real_workload_correctness_contract.py` | 图不变量、public/private、correctness contract/hash | 不能只比较节点/边数量 |
| REUSE | `membind-validation/src/native_characterization_instrumentation.py::install_native_characterization_instrumentation` | phase、LLM logical/transport、embedding、DB/transaction 原始 spans | 不另写一套低精度总计时器替代它 |
| REUSE | `membind-validation/src/native_characterization_tracing.py::{TraceRecorder,DurableJsonlEnvelopeWriter,interval_union_ns,exclusive_duration_ns,critical_path_ns}` | 单调时钟原始事件、区间并集、exclusive 和关键路径 | 不把 phase duration 简单相加当 makespan |
| REUSE | `membind-validation/src/native_characterization_c2_measurement.py::install_c2_measurement_adapter` | graph prefix/work/candidate 计数 | 缺测不得填 0 |
| REUSE/ADAPT | `membind-validation/src/native_characterization_c1_qualification.py`、`native_characterization_c2.py` | A/A instrumentation qualification、phase/work-volume reducer | 必须对新 schema 写回归测试，不能直接假设兼容 |
| ADAPT | `paper-eval-v3/src/paper_eval/unified_observability.py` | operation/episode/history 聚合、interval union | 将旧 arrival/freshness 名称改为本协议 submission/residence，不引入人工 arrival |
| REUSE | `membind-validation/src/live_outputs.py::export_canonical_graph`、`canonicalize_graph.py::{canonicalize_graph,canonical_graph_hash,compare_canonical_graphs}` | 导出、canonicalize、hash | 在新目录补充 key/attribute/temporal/source-link 级 diff；禁止用 count 相等代替语义相等 |
| REUSE | `paper-eval-v3/src/paper_eval/apc_vllm_telemetry.py` | vLLM model identity、Prometheus parser、running/waiting/KV/APC/preemption/token delta | parser 必须锁定 vLLM 0.26.0；进程全局指标不能无条件归因到 block |
| REUSE/ADAPT | `quality_evaluation_v1.py`、`quality_evaluation_v1_retrieval.py`、`quality_evaluation_v1_reader.py`、`quality_evaluation_v1_suite.py` | 只读 retrieval、context pack、Reader/Judge、质量 bundle | 不调 prompt/top-k，不让 private gold 进入 retrieval/Reader |
| REUSE | `baseline_reuse_qa_analysis_20260819/expanded/expanded_qa_inventory.json` | 本实验必跑的 4-history × 4 QA 清单 | 不复制旧方法结果，不称为官方 MAB |
| REUSE/ADAPT | 同目录 `expanded_analysis.py`、`run_expanded.py`、`expanded_runtime.py`、`result_analysis.py` | provenance、gold-blind projection、same-build multi-QA、结果分层 | 新 B0/B1 必须在新图上重跑，不能沿用旧 U0/P(C=2) 数字 |
| REFERENCE | `mab_quality_v2_final_qa` 的 contracts、runner、reducer、qualification | 借鉴 inject-once/query-many、sealed read-only、context-cluster bootstrap | 当前已知 5 contexts/300 QA 中一组 60 QA 有映射缺陷；不得把 4/240 偷换成完整官方 MAB |
| DO_NOT_REUSE | `paper-eval-v3/src/paper_eval/membind_v5_oracle/*` 和 `run_membind_v5_request_dag_oracle.py` | 仅在报告中说明当前 v5 是 request-DAG sealed-trace offline oracle 背景 | 不导入、不运行、不修改。其 `configured K`、request DAG 和 replay 不属于本协议 |
| DO_NOT_REUSE | 旧 S5 scheduler、APC admission、A0/P(C=2) runner | 无 | 不得用旧方法换名字充当 B1 |

若实际 v5 中符号移动，先通过 `git grep`/测试确认等价物，将“计划路径 → 实际路径 → 语义差异”写入 `reuse_manifest.json`。不能因路径变化重写一个近似版本。

---

## 4. 相关论文与官方代码：只参考哪些部分

| 来源 | 应参考的具体内容 | 禁止的误用 |
|---|---|---|
| [Graphiti 官方 `eval_e2e_graph_building.py`](https://github.com/getzep/graphiti/blob/main/tests/evals/eval_e2e_graph_building.py) | session/message 顺序、`reference_time` 传递、原生 `await graphiti.add_episode`；用于 B0 adapter certification | 不把官方跨 subgraph 的 semaphore 当成本协议 B1 的上限 |
| [LongMemEval（ICLR 2025）论文](https://proceedings.iclr.cc/paper_files/paper/2025/file/d813d324dbf0598bbdc9c8e79740ed01-Paper-Conference.pdf) 与 [官方代码](https://github.com/xiaowu0162/LongMemEval) | timestamped history、知识更新/时序/多 session/拒答类别，以及 construction 后独立 QA | 不声称 16 authored QA 等于完整 LongMemEval；不替换当前项目已经冻结的 history/episode 边界 |
| [MemoryAgentBench（ICLR 2026）论文](https://openreview.net/forum?id=DT7JyQC3MR) 与 [官方代码](https://github.com/HUST-AI-HYZ/MemoryAgentBench) | 官方明确的 inject once、query multiple times；public context/private label 隔离；按任务固定 scoring 字段 | 不导入其 memory algorithm；不运行当前项目未完整资格化的官方 MAB 子集；不把本结果标成官方 MAB |
| [LightMem 论文](https://proceedings.iclr.cc/paper_files/paper/2026/hash/a05b72653ec5b473732129829ae04195-Abstract-Conference.html) 与 [官方代码](https://github.com/zjunlp/LightMem) | 分开报告 construction time、token/API calls、retrieval/QA 的呈现方式 | 不复用其存储算法、prompt、backend 或 benchmark harness 来生成本基线 |
| [vLLM 官方 metrics 说明](https://docs.vllm.ai/en/latest/design/metrics/) | counter delta、gauge time series、prefix-cache query/hit counter 与版本变化注意事项 | 不将启动以来的全局 counter 直接当作某个 block；不跨版本猜测指标名 |

所有外部代码仅能在许可证允许范围内使用；若复制非平凡代码，记录上游 URL、提交和许可证。优先导入本项目已封装并测试过的实现。

顶会方法学在本协议中落实为五条可测试规则：

1. benchmark identity 与 method implementation 分离；history、episode、timestamp、QA inventory 先冻结。
2. construction、retrieval、Reader/Judge 分开计时和归因；不把 QA 或 validation 成本混入 build makespan。
3. 同一封存 memory 服务多个 QA，避免每题重建造成伪重复和额外成本。
4. runtime 必须与 tokens、API calls、embedding/DB work volume 同时报，防止用少做工作换取表面加速。
5. development one-run 结果只作协议资格化和基本数据；不做显著性、等价性或广泛泛化声明。

---

## 5. 新目录和产物布局

只新增以下隔离树；名称可以小幅调整，但职责不可混合：

```text
saturated_fixed_work_baseline_v1_2/
├── README.md
├── PROTOCOL.md
├── configs/
│   ├── protocol_v1_2.yaml
│   ├── provider_envelope.json
│   ├── resource_envelope.json
│   └── qa_contract.json
├── src/saturated_fixed_work_baseline_v1_2/
│   ├── contracts.py
│   ├── dataset.py
│   ├── graphiti_adapter.py
│   ├── schedules.py
│   ├── lifecycle.py
│   ├── instrumentation.py
│   ├── telemetry.py
│   ├── correctness.py
│   ├── canonical_diff.py
│   ├── qualification.py
│   ├── qa_lane.py
│   ├── artifacts.py
│   ├── reducer.py
│   ├── report.py
│   └── cli.py
├── tests/
│   ├── unit/
│   ├── contract/
│   ├── integration/
│   └── fixtures/
├── scripts/
│   ├── preflight.*
│   ├── run_qualification.*
│   ├── run_main.*
│   ├── run_qa.*
│   └── build_report.*
└── artifacts/                         # 默认 gitignore；每次 run append-only
    └── <run_id>/
```

旧产物只读。所有新 namespace 至少包含 `protocol_version/method/history_id/run_id`，禁止覆盖和复用半成品 namespace。该目录树和全部产物都创建在远程 checkout/远程 artifact volume，不在控制端镜像实现。

---

## 6. 固定资源与服务封存合同

### 6.1 已有可验证 provider envelope

以当前项目文件 `paper-eval-v3/artifacts/paper_eval/membind_v31/PROVIDER_EXECUTION_ENVELOPE_XGRAMMAR_20260819.json` 为参数基准，其 payload SHA-256 为：

`31f1a8476650767bc391215675924ceed972e10153df02feeaf44eb9fa54e0ee`

构建/Reader 服务必须固定：

| 项 | 固定值 |
|---|---|
| OpenAI-compatible base | `http://10.87.5.247:8000/v1` |
| served model | `qwen3-32b-fp8` |
| vLLM | `0.26.0` |
| max model length | 65536 |
| YaRN | factor 2.0，original 32768，rope theta 1000000 |
| generation | max tokens 16384，seed 20260806，temperature 0，top_p 1，thinking disabled |
| structured output | JSON Schema + xgrammar |
| GPU memory utilization | 0.75 |
| scheduler | FCFS，prefix caching on，chunked prefill on |
| recorded KV capacity | 127280 tokens；启动后重新核验 |

embedding 服务必须固定：

| 项 | 固定值 |
|---|---|
| OpenAI-compatible base | `http://10.87.5.247:8001/v1` |
| served model | `qwen3-embedding-0.6b` |
| model fingerprint | `5f5a8400eeaa2f07d167d8b5b7e63d615945a8f54f506e02342840cd4e3fe626` |
| dtype/dimension | bfloat16 / 1024 |
| max length | 32768 |
| batching | max batched tokens 32768，max sequences 128 |
| pooling | last-token，normalization on，pooling runner |
| GPU memory utilization | 0.15 |

图数据库固定：

| 项 | 固定值 |
|---|---|
| Neo4j | Community 5.26.0，非 Docker，远程项目既有安装 |
| Bolt / HTTP | `bolt://localhost:7687` / `http://localhost:7474`；这里的 localhost 是远程 runner 主机 |
| 既有历史路径 | `/data/predator/ly/MemBind/membind-validation/runtime/neo4j/neo4j-community-5.26.0`；使用前验证，不盲信 |

Graphiti 固定为 `0.29.3`、提交 `021d3a57d511f21b10adaf7fa923bd5c1fce5e9d`；模型 checkpoint revision 固定为项目当前记录的 `aa55da1ecc13d006e8b8e4f54579b1ea8c3db2df`。

### 6.2 物理资源必须在远程重新取证再冻结

MD 和 GitHub 中的 provider 参数不能代替 live 物理设备身份。控制端的任何硬件信息均与实验无关。执行 agent 必须在第一次 live qualification 前，通过远程 shell 从实际承载 `8000/8001` 的主机取得并封存：

- hostname、OS/kernel、CPU 型号/物理核、总内存；
- NVIDIA driver、CUDA runtime；
- 每个可见 GPU 的 name、GPU UUID、显存、MIG 状态、power limit；
- `8000` 和 `8001` PID → 完整 argv → `CUDA_VISIBLE_DEVICES` → GPU UUID 映射；
- vLLM/Python/torch/xgrammar 版本；
- Neo4j PID、版本、配置 hash、数据目录；
- 模型 checkpoint 解析后的路径、revision/hash；
- Graphiti 安装路径、版本和 git commit。

先生成 `HISTORICAL_RESOURCE_ENVELOPE_ID`：它必须由此前有效主实验的远程启动日志、进程 argv、GPU UUID、模型/checkpoint、Neo4j 配置和 provider envelope 复原，不能用“同型号”或 CUDA ordinal 代替。再生成本次 live 的 `RESOURCE_ENVELOPE_ID = sha256(canonical resource_envelope.json)`。

正式资源门固定为：

```text
historical_resource_match == true
and live_resource_envelope_verified == true
and all_formal_blocks_share_one_resource_envelope == true
```

8 个正式 block 必须具有相同 GPU UUID 集合、进程→GPU 映射、模型 revision、服务 argv、版本和资源限制；若服务重启，PID 可变，其余身份必须不变并记录 restart event。若无法从远程证据恢复历史资源身份，只能报告外部阻塞，不能擅自把本次第一块定义为“和之前相同”，也不能借用控制端信息补空。

### 6.3 cache 与运行次序政策

在正式运行前只选择一次缓存隔离政策，并冻结：

1. 首选：保持同一热引擎，每个 block 使用唯一且可验证传播到 vLLM 的 `cache_salt`，block 前后等待 `running=0 && waiting=0` 连续两个采样点；或
2. 若当前 Graphiti/vLLM 路径不能在不改变请求语义的前提下传播 salt，则每个 block 前对两个服务执行完全相同的冷重启程序。

不得对 B0 用冷缓存、对 B1 用热缓存，也不得在看到结果后切换政策。vLLM 若没有可用的 cache reset endpoint，不得伪造 reset 成功。

无论采用哪一种政策，每个正式 block 前都执行同一份 `DISJOINT_WARMUP_MANIFEST`：分别预热 construction 和 embedding 的 lazy path；warmup 内容不得来自 4 个 histories/16 QA，不写正式 namespace，不计入 `T_build`。Neo4j 保持同一远程进程和相同 page-cache 政策，只使用 fresh namespace，不做 method-specific restart。warmup 后必须再次验证 vLLM running/waiting=0、embedding 无未决请求、Neo4j 无本实验未决事务。

正式 block 顺序预注册并交替平衡：

1. `07741c45`: B0 → B1
2. `b6019101`: B1 → B0
3. `6071bd76`: B0 → B1
4. `a2f3aa27`: B1 → B0

若 block 失败，失败记录保留。恢复服务后使用全新 namespace 重跑该同一 block，不得覆盖；主表只选第一条满足全部 validity gates 的 attempt，并公开失败 attempts。

---

## 7. 指标合同：先定义字段，再写采集器

### 7.1 每个指标的强制元数据

`metric_dictionary.json` 中每个指标必须包含：

- `name`、`version`、`level`（run/history/episode/phase/request/sample/qa）；
- `unit`、`better_direction`；
- 精确公式、numerator、denominator；
- 原始事件/endpoint/query 来源；
- clock（monotonic wall、process CPU、server clock）和跨机对齐方法；
- attribution scope（block-exclusive、process-global、sampled）；
- availability：`MEASURED | DERIVED | NOT_EXPOSED_BY_PINNED_STACK | NOT_EVALUATED | INVALID | AMBIGUOUS_PROCESS_GLOBAL`；
- 是否 core validity gate；
- 典型异常的解释提示。

任何缺测均保留状态和 reason，不能填 0。core 指标缺失使 block 无效；backend 没有暴露的诊断指标允许 `NOT_EXPOSED...`，但必须公开 coverage。

### 7.2 原始事件公共键

所有 JSONL envelope 至少有：

`schema_version, run_id, block_id, attempt_id, method, history_id, namespace, source_sequence, episode_id, source_hash, operation_id, phase, span_id, parent_span_id, request_id, attempt, event, monotonic_ns, wall_time_utc, outcome, error_type`。

跨进程 wall clock 只用于关联；所有 duration 和并发积分用同一进程的 monotonic timestamps。服务端事件若无法可靠时钟对齐，只作独立 time series，不伪造 per-request 临界路径。

### 7.3 Feeder 与生命周期指标

逐 episode 记录：

- `t_submit`、`t_task_created`、`t_execution_start`（只有 hook 确实可见时）、各 phase start/end、`t_caller_return`、`t_publication_visible`、`t_publication_durable`、异常终点；
- `submit_gap_ms`、`submit_loop_cpu_ms`、`submission_span_ms`；
- `submit_to_start_ms`、`service_ms`、`submit_to_return_ms`、`submit_to_visible_ms`、`submit_to_durable_ms`；
- `caller_return_to_durable_ms`（隐藏尾部）；
- task-create failure、cancelled、timeout、exception 和 unobserved exception 数；
- artificial sleep/gate 次数、feeder 内 backpressure await 次数及时间。B1 这些值必须为 0，否则属于 harness 违规。

history/block 记录：

- `t0` 在 fixed disjoint warmup 完成、服务重新 idle、fresh namespace ready 后，紧邻 E0 admission 前取得；
- `t_durable_complete` 只在所有 method-owned episode tasks、LLM/embedding attempts、retries、background tasks、DB transactions 都 terminal，且最后一个 construction commit/ack 返回时取得；
- `build_makespan_s = t_durable_complete - t0`，随后立即停止 build timer；
- `submission_span_s`、`drain_tail_s = t_durable_complete - t_last_submit`；
- `t_validated_seal` 在 completeness scan、canonical projection/diff、correctness reduction、immutability check、artifact hash 完成后取得；`validation_seal_latency_s = t_validated_seal - t_durable_complete`，不得进入 build makespan；
- episodes/s、source tokens/s；source tokens 由封存 tokenizer/revision 对原始 episode 正文确定性计算；
- success/failure/cancelled episode 数和 completion fraction。

不能用固定 sleep 猜测 durable completion。每个 terminal 条件必须绑定已注册 task/span/request/transaction 或 driver commit ack；无法观测的 core terminal 条件使 block 无效，而不是标 0 或假定完成。

### 7.4 并发、重排和时间分解

- whole-update active integral、mean、max；`active_k_time_s` 分布；任何更新重叠的 wall-time 比例；
- phase 级 active mean/max、interval union、exclusive duration、occupancy；
- submit/completion/publication 序列的 inversion count、inversion density、Kendall tau、最大位移；
- 关键路径时长及各 phase 对关键路径的 share；
- completion/publication 重排默认是 `ordering_observation`。只有合同预定义的 source-order visibility 被可证明破坏并影响状态时，才计入 `direct_semantic_violation`；不得事后改变分类。

### 7.5 每个 Graphiti phase 的细粒度指标

按当前 instrumentation 实际 phase 名保存原名，并映射到以下稳定分类：

- node/entity extraction；
- edge/relation extraction；
- node resolution/dedup；
- edge resolution/dedup；
- attribute/summary generation；
- temporal invalidation/edge handling；
- persistence/transaction；
- publication/visibility。

每个分类至少输出：calls、attempts、success、failure、inclusive sum、interval union、exclusive duration、p50/p95/p99/max、occupancy、active mean/max、retry time、critical-path share。phase 不存在时写正确 availability/reason。严禁把相互重叠的 phase 时长相加成总运行时间。

### 7.6 LLM 层指标

按 phase、episode、block 分层：

- logical call 数与 HTTP attempt 数；retry count/rate、retry backoff、timeout、transport/status/parse error；
- input/prompt、output/completion、total tokens；若 API 不暴露 cached tokens 则显式缺测；
- latency p50/p95/p99/max；TTFT、queue、prefill、decode 仅在当前 stack 可可靠获取时报告；
- finish reason、truncation、structured-output validation failure、repair/retry（只允许客户端原有行为，协议不新增 repair）；
- logical/transport in-flight integral、mean、max；
- 每个 phase 的 tokens、calls、latency share；
- vLLM `running`/`waiting` time-weighted mean/max、waiting>0 时间比例；KV usage p50/p95/max；preemption delta；prompt/generation token counter delta 与吞吐；prefix-cache query/hit delta 和 hit rate。

vLLM Prometheus 是进程全局状态。只有 block 前后引擎空闲、无其他客户端且采样窗口完整时才标 `block-exclusive`；否则标 `AMBIGUOUS_PROCESS_GLOBAL`，不能进入因果解释。

### 7.7 Embedding 层指标

- logical calls、HTTP attempts、items、batches、batch size p50/p95/max；
- tokens（若 API 暴露）、latency p50/p95/p99/max、in-flight mean/max；
- retry、timeout、HTTP error、dimension mismatch；
- 返回维度分布、有限值检查、norm 诊断抽样；
- items/episode、items/source token、B1/B0 work ratio。

### 7.8 Neo4j/图工作量指标

- driver query、read/write operation、transaction begin/commit/rollback/retry/conflict/timeout；
- latency、active transaction/in-flight、连接池等待（若暴露）；
- extracted/resolution candidate/new/reused/duplicate/invalidation 的 node/edge 数；
- final nodes/edges、episodes、provenance links、temporal edges；
- LLM input tokens、logical calls、embedding items、DB writes 的 B1/B0 paired work ratio。

如果 B1 完成了更多或更少的语义工作，必须以 work ratio 限定速度解释，不能把工作缺失称为加速。

### 7.9 资源与采样质量

每秒采样（目标 1 Hz，保留实际 timestamp）：

- 每个 GPU：utilization、memory used/total、power、temperature、SM/memory clocks、throttle/MIG（可用时）；
- vLLM 进程和 Neo4j：CPU、RSS、线程/文件描述符（平台可用时）；
- 主机总 CPU/内存/swap；网络字节只在能按接口和窗口可靠归因时报告；
- sampler coverage、sample count、gap p50/p95/max、expected/actual ratio。

资源指标输出 mean/p50/p95/max；采样 gap 超阈值或 coverage 不足时，该资源列无效但原始构建 block 可按预注册 gate 判断是否仍有效。

### 7.10 正确性和可靠性指标

分开记录 `harness_violation`、`ordering_observation`、`direct_semantic_violation`：

- missing/lost/duplicate episode；source hash/sequence/provenance mismatch；
- harness 显式 future-input leakage；
- Graphiti 并发导致的可证明 future-state read、stale-predecessor/wrong-state write；
- completion/publication inversion 先进入 `ordering_observation`；只有直接证据证明它导致错误 persistent-state observation、错误 temporal/provenance 状态，或违反预注册的 source-order visibility contract 时，才另计 `direct_semantic_violation`；
- caller returned 后的隐藏 mutation、seal 后写入、orphan task/open span/open transaction；
- node/edge key 差异、属性差异、temporal interval/invalidation 差异、source-link 差异；
- retry exhausted、cancelled、timeout、uncaught/unobserved exception，按 feeder/Graphiti/LLM/embedding/DB/telemetry/QA 分层。

最终图比较是相同 history 的 B1 对 B0：同时报告 canonical hash parity 和结构化 diff；count parity 只能是诊断项。

### 7.11 非确定性与因果归因

- 在 12-episode qualification 上运行两个独立 B0 namespace，检查 request/config identity、instrumentation 是否改变输出，并报告 `D_serial_serial_12`。
- `D_serial_serial_12` 只适用于该 12-episode qualification，不得外推为 44–49 episode 正式 history 的数值 floor，也不得从单个 pair 推断随机性分布。
- 正式 history 只有一次 B0/B1，因此 canonical mismatch 是结构性描述证据，不自动证明 concurrency causality。
- 证据优先级固定为：可复核的直接因果 violation > canonical structural diff > QA downstream difference。
- 若未来要形成 final-paper correctness 结论，应对全部方法和完整 histories 统一增加预注册、随机化/平衡的重复运行，而不是只给某个方法补跑。

---

## 8. 主表只保留关键指标

细粒度指标全部进入 machine-readable 附表和诊断报告；论文式主表不得塞入几十列。

### 8.1 Development 构建主表 `main_table_construction.csv/md`

固定列：

| Method | Valid histories | Episodes | Total build makespan (s) | Speedup vs B0 | Source tokens/s | LLM input-token ratio vs B0 | Direct semantic violations | Canonical exact-match histories (descriptive) |
|---|---:|---:|---:|---:|---:|---:|---:|---:|

聚合合同：

- `Total build makespan` 是 4 个有效 history 的 makespan 之和；不把 qualification 计入。
- `Speedup(B1) = sum(B0 paired makespan) / sum(B1 paired makespan)`；B0 固定为 1.000。
- `Source tokens/s = sum(source tokens) / sum(build makespan)`。
- `LLM input-token ratio = sum(method LLM input tokens) / sum(B0 LLM input tokens)`，用于识别工作膨胀；缺测则整个主表对应单元格显示状态，不能填 1。
- `Direct semantic violations` 不包括单纯 completion inversion；详细 ordering 表单独给出。
- `Canonical exact-match histories` 格式 `x/4`；它只是 B1 对 B0 的结构性描述，不是 ground truth，也不使用 12-source Serial A/A 作为 full-history floor。

另生成 `per_history_construction.csv`，至少包含每个 history 的 B0/B1 makespan、speedup、work ratios、inversions、violations、canonical diff 和 validity，防止聚合掩盖异常。

### 8.2 Development Multi-QA 主表 `main_table_quality.csv/md`

固定列：

| Method | QA N | R@1 | R@5 | R@10 | MRR | nDCG@10 | Accuracy (invalid=wrong) | Invalid |
|---|---:|---:|---:|---:|---:|---:|---:|---:|

- 每个方法 `QA N=16` 才能进入 development 主表。
- primary accuracy 的 denominator 固定为全部 16，invalid 计错；同时在附表报告 valid-only accuracy，不能取代 primary。
- paired QA 附表给出 B0/B1 agreement、B0-only correct、B1-only correct、both wrong、invalid layer，并按 history 聚合。
- 可按 4 个 history 做 cluster bootstrap 的描述性区间，但明确 `n_clusters=4`，不做显著性声明。
- 两张表标题、文件 metadata 和报告正文都必须标记 `development / protocol-qualified / one run per method-history`；不得称为 final-paper result。

---

## 9. Multi-QA：复用当前优化设计

### 9.1 固定清单与身份

必跑清单：

`baseline_reuse_qa_analysis_20260819/expanded/expanded_qa_inventory.json`

预期身份：

- scope：`BASELINE_REUSE_4_HISTORY_AUTHORED_EXTENSION`
- 4 histories × 4 questions = 16 QA
- 当前问题类型均为 `knowledge-update`
- source hash：`a1e3088193eaf6b866fceb62343ebe09beddc8ad0ed57bc70176232f16b3454b`

实际运行前重新计算 hash、验证每个 QA 的 history/session/source provenance。任何不匹配立即 fail closed，不能自动修题。

### 9.2 一次构建，多次查询

每个 `(method, history)` 的正式构建完成且 namespace seal 后：

1. 记录 QA 前 canonical graph hash 和 graph counts。
2. 对该 history 的 4 个 public questions 依次执行同一 frozen retrieval → context pack → Reader/Judge 链。
3. private gold/evidence 只进入 scorer，不能进入 retrieval query、context pack 或 Reader prompt；执行 `assert_gold_blind`/等价 guard。
4. QA 不得调用 construction writer，不得创建新 episode；数据库写 hook 的次数必须为 0。
5. QA 后再次导出 canonical graph；hash、结构化 diff、counts 必须与 QA 前完全一致，否则该 namespace 的全部 QA 无效。
6. 4 个 QA 共享同一构建，不重启/重建图。Reader/Judge 资源消耗单独计入 QA 表，绝不能混入 construction makespan。

### 9.3 QA 细粒度诊断

每条 QA 保存：

- inventory/question/history identity、public/private projection hash；
- retrieval validity、候选数、首个 relevant rank、R@1/R@3/R@5/R@10、RR、nDCG@10；
- context source 数、context tokens、截断、private scorer 计算的 gold-evidence retained/coverage；
- Reader/Judge model identity、prompt hash、latency、tokens、finish reason、parse/validation 状态；
- final correctness、invalid reason、`failure_layer = retrieval | context_pack | reader | judge | graph | contract`；
- QA 前后 namespace hash 和 write-attempt count。

历史报告中的旧 U0/P(C=2) 的 13/16、12/16 等数字仅能作为 lineage 说明，不得复制到新 B0/B1 表。

---

## 10. TDD 实施顺序与硬门禁

每一阶段都必须保存 RED/GREEN 证据；任何 live 运行前，所有 offline/contract 测试必须通过。

### P0：版本与数据只读审计

先写会在错误 repo/错误数据上失败的 tests，然后实现：

- remote URL、HEAD、dirty status、Graphiti/provider envelope 身份采集；
- 4 history episode counts、sequence/hash、总计 188；
- 16-QA inventory hash、4×4 映射、gold provenance；
- 非 GitHub remote、错误 commit、控制端本地执行的拒绝保护；
- v5 oracle import deny-list 测试。

门禁：`audit_manifest.json` 可复查，且没有修改现有项目文件。

### P1：协议和 schema

先写以下失败测试：

- B0 必须逐条 await；
- B1 必须 source-order create 任务、提交循环零 workload await、无 app cap；
- `test_b0_feeder_is_blocking_serial` 与 `test_b1_feeder_is_eager_nonblocking` 必须分开；禁止再使用一个 `test_nonblocking_feeder` 同时约束两种方法；
- future body 不进入早期调用参数；
- future persistent-state observation 进入 correctness outcome，不使 protocol gate 失败；
- 所有任务异常被收集且 block 失败；
- metric availability 不可隐式变 0；
- manifest/config/resource hash 变化使 resume fail closed。

再实现 `contracts.py`、`schedules.py` 和 schema。

### P2：生命周期、并发与 reducer

用 deterministic fake Graphiti/clock 构造重叠、重排、异常和 post-return hidden work：

- 验证 submit/service/visible/durable/drain/seal duration；
- 验证 interval union、exclusive duration、active integral/mean/max；
- 验证 inversion、Kendall tau、最大位移；
- 验证 completion/publication inversion 默认只进入 ordering observation；没有因果证据时不得增加 direct semantic violation；
- 验证 phase sum 不会被误作 makespan；
- 验证 process-global telemetry 的 attribution 状态；
- 验证主表 sum-ratio 公式和 invalid denominator。

### P3：Graphiti 适配与测量资格

- 用 mock/小型测试证明 B0 的调用参数、顺序、异常与官方式原生串行循环一致。
- 对当前 instrumentation/tracing/C2 adapter 做兼容性 contract test。
- 测试 vLLM 0.26.0 metrics fixture 和缺字段行为。
- 测试 Neo4j canonical export/diff 能识别 key、attribute、temporal、source-link 差异。
- 做小型 A/A：相同 B0 路径分别关闭/开启 instrumentation，检查输出相同、overhead 不超过预注册阈值；历史约 1.317% 只能作参考，不能当本次结果。
- 做 12-episode B0/B0 独立 namespace comparison；测试 reducer 不会把该单个 prefix distance 外推成 full-history nondeterminism floor。

### P4：封存、resume 与失败恢复

- abrupt termination 后 append-only journal 可恢复；
- partial/failed attempt 永不覆盖；
- namespace/config/hash 不匹配拒绝 resume；
- seal 必须检查所有任务终态、open spans/requests/transactions=0、服务空闲、重复图快照稳定；
- timeout 会产生可行动 diagnosis，不会伪造成功。

### P5：Multi-QA

- 16-QA inventory 身份与 public/private 隔离；
- 一个 build 对 4 QA，construction writer 在 QA 阶段不可调用；
- QA 前后 graph hash 不变且 write attempts=0；
- retrieval/Reader/Judge invalid 精确分层；
- primary accuracy 将 invalid 计错；
- paired reducer 和 4-cluster bootstrap fixture 正确。
- 测试 L4 只能读取 L3 的 8 个 namespace，任何额外 construction call 都使 QA lane 失败。

### P6：项目级回归

- 运行新目录全部 unit/contract/integration tests；
- 运行会被新 import 路径影响的当前项目 targeted tests；
- 运行当前项目可执行的完整 test suite，记录 collection 数、通过/跳过/失败，不能硬编码旧测试数量；
- 现有失败若与本分支无关，需用 clean HEAD 重现并给证据；不能删除测试或放宽断言。

只有 P0–P6 全绿，才可进入 live 阶段。

---

## 11. 全远程服务访问、启动和沙箱网络诊断

### 11.1 先区分观察点

所有 workload 命令都必须通过远程执行通道落在实验环境。执行 agent 先记录远程 runner 主机与 vLLM 主机的 hostname/IP、`nvidia-smi`、端口监听和进程，确认控制端没有被误当成实验主机。服务探针至少从两个观察点进行：

1. 当前控制/沙箱进程，仅用于判断网络可见性；
2. 实际远程实验主机 shell（既有 SSH config、tmux/session 或项目既有远程执行入口），它是服务健康和实验状态的权威观察点。

控制端访问 `10.87.5.247` 失败，但远程目标主机访问 `/v1/models`、`/metrics` 正常时，状态必须是 `SANDBOX_NETWORK_VISIBILITY_LIMITATION`，不能记成 vLLM crash。检查控制端代理变量和 `NO_PROXY`，使用 direct/no-proxy probe；不修改全局代理配置，不在控制端启动替代服务。

不得索要或打印 secret。优先使用已有凭据/session；不能猜密码、不能在产物保存 API key/Neo4j 密码。

### 11.2 自动恢复顺序

以下操作全部在远程环境进行。对 `8000`、`8001`、Neo4j 分别执行：

1. 查看监听、PID、完整 argv、tmux/service 状态和最近日志；
2. 若健康且身份完全匹配，复用；若身份不匹配，先隔离错误实例，不能对其跑数据；
3. 若未运行，从当前项目历史成功启动脚本/日志解析真实模型路径和完整 argv；禁止猜 checkpoint 路径；
4. 在可持久的 tmux/service 中用封存参数启动；
5. 等待端口、`/v1/models`、`/metrics`、最小 JSON-schema canary 或 embedding dimension canary 通过；
6. Neo4j 用既有 `NEO4J_HOME` 启动，验证版本和 `RETURN 1`，再验证新 namespace 写读；
7. 将经过 secret redaction 的 argv、版本、model response、metrics header、日志摘要和 GPU mapping 保存到远程 run artifact；控制端只收取最终只读副本。

不得换到 `8002/8003`、不同模型、较小 context、不同 GPU、控制端本地服务或云 API 来“跑通”。若必须重启，两个方法使用完全相同政策。

### 11.3 恢复失败的状态语义

网络/主机外部状态失败不算 baseline 结果。至少完成 3 轮有间隔且有新证据的恢复循环，并尝试目标机侧 probe、既有 tmux/service、历史启动命令三个路径。仍无法获得权限或主机不可用时：

- 保存 `STOP_WITH_EXTERNAL_DIAGNOSIS.json`、精确观察点、命令摘要和下一步；
- 保持 run 可恢复；
- 不能生成空主表、不能写 `COMPLETE`、不能宣称获得主实验数据。

一旦外部状态恢复，应从门禁继续，而不是重写实现。

---

## 12. Live 执行阶段

### L0：严格 preflight

- 所有 P0–P6 tests 全绿；
- project/data/provider/resource/QA contract hashes 固定；
- `historical_resource_match=true`，远程历史资源证据与本次 live GPU UUID/模型/服务/Neo4j 身份一致；
- 8000/8001/Neo4j 健康并通过 canary；
- 目标 GPU UUID/进程映射、服务 argv、模型 revision 与资源政策封存；
- cache isolation 政策已选定；
- construction 与 embedding 的 fixed disjoint warmup 已执行，warmup manifest/hash 已封存；
- 无其他 client，vLLM running/waiting 和 Neo4j 活动回到 idle；
- telemetry sampler 做 60 秒连续覆盖测试。

任何一项失败都不得启动正式计时。

### L1：12-episode 双方法资格测试

在全新 qualification namespaces 上对同一固定 12-episode prefix 跑 `B0-A`、`B0-B` 和 `B1`：

- 验证 feeder 行为、所有任务 drain、trace 完整、服务 telemetry、graph seal、canonical diff 和直接违规检测；
- 验证 B1 task count=12、source-order task creation、app gate/sleep/backpressure=0；
- 验证两个 B0 active whole update max=1，且 feeder 逐条等待；
- 输出 B0-A/B0-B canonical diff 作为 qualification diagnostic；明确该单个 prefix pair 不构成 full-history floor；
- 验证 QA 只读链可在 qualification 图运行，但其 QA 不进入主表。

资格失败时先修复/新增回归测试，不能直接进入正式 history。

### L2：单 history 端到端 rehearsal

按预注册顺序对 `07741c45` 跑一次非正式 rehearsal（新 namespace），生成全套原始事件、reducer、canonical diff、QA 和报告。rehearsal 不进入主表，目的是验证长尾、磁盘空间、seal 和 resume。

若资源允许，也可将 L1/L2 压缩为一个严格标记的 qualification run；但不得把 qualification 产物冒充 L3 development 数据。

### L3：正式 8 个构建块

按第 6.3 节固定次序执行。每个 block：

1. 验证相同 resource/provider/config/data hashes；等待证据化 idle；准备唯一 cache salt 或执行固定冷启动。
2. 新建 namespace，写入 `BLOCK_STARTED` 并 fsync。
3. 启动 raw trace、vLLM、GPU/CPU/Neo4j 采样；记录 baseline snapshot。
4. 运行 B0 或 B1；不得人工 pacing。
5. 等待所有 method-owned futures、requests、retries、transactions 和 background tasks 终态；取得 `t_durable_complete` 并立即停止 build timer。
6. 在计时外验证服务 idle、namespace completeness、重复 canonical snapshot/hash 稳定、无 post-build mutation；记录 `validation_seal_latency_s`。
7. 在计时外导出 canonical graph/diff、correctness、metric coverage、work volume；原子写入 `t_validated_seal` 和 block seal。
8. 任何 core gate 失败，保留失败 attempt 并恢复；只用新 namespace 重跑。validation 失败会使该 block 无效，但 validation 时间永不回填进 `T_build`。

### L4：在同一 8 个封存 namespace 上跑 Multi-QA

- 不重建任何 history；严格运行每个 `(method, history)` 的 4 个问题；
- Reader/Judge 使用冻结的同一身份和参数，两方法一致；QA 消耗单独记录；
- 生成 32 条 QA 行、paired rows、QA graph read-only evidence；
- 任何 inventory/hash/gold-blind/write guard 失败都 fail closed。

### L5：复算、主表和报告

- reducer 必须只从 sealed raw artifacts 复算，不能读取手工填写数字；
- 独立运行两次 reducer，输出 hash 必须相同；
- 生成两张主表、per-history/per-QA 附表、metric dictionary、coverage、failure ledger、canonical diffs 和 resource evidence；
- report 中明确 16-QA 是 authored development extension、不是官方 MAB；
- 列出所有 `NOT_EXPOSED/AMBIGUOUS/INVALID`，不隐藏坏行。

---

## 13. 有效 block、seal 和数据选择规则

正式 block 进入主表必须同时满足：

- project/data/provider/resource/config/cache hashes 与预注册一致；
- episode input count/hash/sequence 完整；
- runner 调用成功或失败已逐 episode完备记录；主表性能行要求所有 episodes 成功；
- B0/B1 schedule contract 通过；
- core lifecycle spans 无缺口，monotonic duration 合法；
- 无 orphan task、unobserved exception、open request/span/transaction；
- graph namespace 唯一、seal 后稳定；
- telemetry core coverage 达到预注册阈值；
- 没有其他 client 污染 process-global 服务窗口；若有则 performance block 无效并重跑；
- 不存在 harness violation。

注意：B1 的 direct semantic violations、canonical mismatch 或 QA 下降是要报告的实验结果，本身不应被当作 harness invalid 而删除。只有输入、资源、测量、隔离或 schedule 合同失败才使 block 无效。

首次有效 attempt 是正式选择；不能从多个 attempt 中挑最快的。所有失败/无效 attempts 在 ledger 中公开。

---

## 14. 最低交付产物

`artifacts/<run_id>/` 至少包含：

- `audit_manifest.json`
- `reuse_manifest.json`
- `protocol_manifest.json`、`config_hashes.json`
- `provider_envelope.json`、`resource_envelope.json`、`RESOURCE_ENVELOPE_ID`
- `service_evidence/`（redacted argv、versions、models、metrics、GPU mapping、Neo4j）
- `tdd_evidence.jsonl`、`test_summary.json`
- `blocks/<block_id>/raw_events.jsonl`、telemetry、snapshots、canonical graph/diff、metrics、seal
- `failed_attempts.jsonl`
- `qa/qa_rows.jsonl`（32 行）、paired rows、read-only evidence
- `metric_dictionary.json`
- `block_metrics.parquet|jsonl`
- `per_history_construction.csv`
- `diagnostic_phase_llm_embedding_db.csv`
- `diagnostic_concurrency_ordering.csv`
- `diagnostic_resource_telemetry.csv`
- `correctness_ledger.csv`
- `main_table_construction.csv` 和 `.md`
- `main_table_quality.csv` 和 `.md`
- `FINAL_REPORT.md`
- `FINAL_SEAL.json`：列出所有文件 SHA-256、reducer version、选择的 attempt 和验收状态。

CSV 中百分比同时保留精确 numerator/denominator；机器数据保留未四舍五入值，Markdown 才格式化。

---

## 15. 完成判定和停止规则

只有以下断言全部为真，才能写入 `SATURATED_FIXED_WORK_BASELINE_V1_2_COMPLETE` 并停止：

```text
tests_all_green
and qualification_passed
and historical_resource_match
and valid_construction_blocks == 8
and formal_construction_calls == 8
and valid_histories_per_method == 4
and qa_rows_B0 == 16
and qa_rows_B1 == 16
and qa_graph_write_attempts == 0
and qa_extra_construction_calls == 0
and build_timer_excludes_validation_and_qa
and construction_main_table_has_real_numbers
and quality_main_table_has_real_numbers
and tables_marked_development
and reducer_is_deterministic
and final_seal_verified
```

`real numbers` 指由本次新 B0/B1 sealed artifacts 复算出的有限值或合同允许的明确 availability 状态；不能是 TODO、占位符、历史数字或空单元格。构建 makespan、speedup、source tokens/s、violations、canonical parity，以及 QA 的 N、retrieval metrics、accuracy、invalid 都必须有本次证据。

成功终报应先给出两张主表，然后简述：

1. B1 相对 B0 的总加速和各 history 一致性；
2. 加速主要来自哪个 phase/资源层；
3. work ratio、排队/KV/preemption 是否说明代价转移；
4. 直接语义违规、canonical 差异和排序异常；
5. 16-QA retrieval/accuracy 的 paired 变化；
6. 资源与方法公平性、缺测和结论边界；
7. 产物目录和 `FINAL_SEAL` hash。

若外部权限/主机故障最终阻断，只能报告“未完成且可恢复”的诊断状态，不能以无主实验数的报告冒充完成。

---

## 16. 执行 agent 最终核对表

- [ ] 远程 checkout 来自指定 GitHub 仓库且是 v5，实际 HEAD/dirty state 已记录；没有使用控制端或其他本地目录作为事实源。
- [ ] 所有实现位于 `saturated_fixed_work_baseline_v1_2/`，v5/oracle/旧 baseline 均未修改。
- [ ] B0 是原生串行完整更新；B1 是源序无上限 whole-update tasks；无人工 arrival、无应用层 cap。
- [ ] TDD 的 RED/GREEN 证据齐全，targeted 和全量回归已记录。
- [ ] 实现、测试、runner、8000/8001/Neo4j、数据和 artifacts 全在远程；服务由 agent 检查和必要时恢复；控制端沙箱问题与目标机服务问题已区分。
- [ ] provider/resource/cache/data/QA contracts 已冻结，全部正式块使用相同 GPU UUID 和启动参数。
- [ ] 4 histories × 2 methods 的 8 个正式构建块有效；失败 attempts 未隐藏。
- [ ] 4 questions/history 在同一封存图上执行，得到 16 QA/method，未重建、未写图、gold blind。
- [ ] 细粒度 metric dictionary、phase/LLM/embedding/DB/resource/correctness 附表可定位问题层。
- [ ] 构建主表和质量主表只含预定义关键列，所有数字来自 sealed raw artifacts。
- [ ] 16-QA 结果没有被误标为官方 MAB/LongMemEval。
- [ ] `FINAL_SEAL.json` 验证通过后才写 `SATURATED_FIXED_WORK_BASELINE_V1_2_COMPLETE`。
