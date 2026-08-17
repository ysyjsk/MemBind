# MemBind 三基础 Baseline Development 实验报告

本报告汇总 Native U0、Async-Serial A0 与 Whole-Update Parallel P(C=2) 在同一组 development/calibration histories 上的结果。没有访问 PILOT 或 FINAL_PAPER_TEST；因此这是系统 characterization 与方法设计依据，不是最终论文显著性结论。

## 运行身份

- Native run: `nb-20260816-001`
- Three-baseline suite: `bs-dev-20260816-001`
- Graph-quality overlay: `gq-dev-20260817-001`
- Report payload SHA256: `ba060bd48fb933319b522ef5196c003919b2a0c0d2a81c3eb9f00f4b264e9c62`
- Claim boundary: `PROTOCOL_ALIGNED_NOT_NUMERICALLY_MATCHED`
- Graph QA boundary: `PROTOCOL_RUBRIC_COMPATIBLE_LOCAL_JUDGE_DIAGNOSTIC`

## 数据访问边界

本次 live graph-quality evaluation 只打开固定四条记录、188 episodes 的 `DEVELOPMENT_EXPOSED` 独立 artifact，不打开 combined LongMemEval container，也未评估任何已分配为 PILOT 或 FINAL_PAPER_TEST 的记录。

该独立 artifact 的一次性 materialization 曾从 combined source container 导出四个预先指定的 development IDs。因此本报告不作“项目生命周期从未扫描 combined container”的更强声明；这里的 `heldout_data_accessed=false` 仅表示本次 evaluation 没有评估 PILOT/FINAL role 数据。

## 核心结果

| Method | Episodes | QA Accuracy | Session Evidence Recall@10 | Graph-native QA | Edge source coverage@10 | P95 freshness (s) | P99 freshness (s) | Makespan (s) | Goodput (ep/s) | Max backlog |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| U0 | 188 | 0.250 | 1.000 | 0.000 | 0.375 | 99.918 | 205.629 | 8501.162 | 0.022115 | N/A |
| A0 | 188 | 0.250 | 1.000 | 0.000 | 0.375 | 2258.750 | 2367.464 | 8523.113 | 0.022058 | 49 |
| P(C=2) | 188 | 0.250 | 1.000 | 0.000 | 0.625 | 1867.920 | 2156.406 | 7355.528 | 0.025559 | 49 |

这里的 `QA Accuracy` 与 `Session Evidence Recall@10` 是三方法共同的冻结 session-reader/Judge 路径；`Graph-native QA` 是完成 construction 后统一执行的 top-20 temporal facts + top-20 entity summaries 只读诊断 overlay。Edge source coverage 不是官方 Session Evidence Recall@10，不能混写。

当前 suite 的 arrival timestamp 语义不同：U0 在每次 serial service 前记录 arrival，A0/P 则先发出整个 history 的 intent burst。因此本轮 P95/P99 不能计算跨方法 freshness delta；这些值只描述各 execution mode 的 observed 行为。相同 188 episodes 下的 aggregate makespan/goodput 只能作为 burst-drain capacity 的 descriptive directional signal，不是 open-loop online latency 或显著性结论。

## 调度与正确性证据

| Method | Workers | Observed max active updates | Whole-update overlap | Direct violations | Direct-violation status |
|---|---|---:|---|---:|---|
| U0 | 1 | 1 | no | 0 | MEASURED |
| A0 | 1 | 1 | no | N/A | NOT_EVALUATED_IN_LIGHTWEIGHT_BASELINE_SUITE |
| P(C=2) | 2 | 2 | yes | N/A | NOT_EVALUATED_IN_LIGHTWEIGHT_BASELINE_SUITE |

A0 的目标是观察 caller 去阻塞以后是否形成 freshness backlog；它不是吞吐优化。P(C=2) 只证明粗粒度 whole-update 并发在当前 development screening 中的系统行为。如果 direct violations 未测量，报告保留 N/A，不能把它解释为 0。

## Work Volume 与图规模

| Method | LLM calls | Input tokens | Output tokens | Embedding calls/items | DB operations/transactions | Candidate count | Nodes | Relationships |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| U0 | 1900 | 17861037 | 207768 | 1870/4718 | 5261/188 | 21171 | 1199 | 1705 |
| A0 | 1900 | 17861327 | 207485 | 1870/4718 | 5261/188 | 21171 | 1193 | 1705 |
| P(C=2) | 2019 | 17832623 | 203119 | 2012/5120 | 5666/188 | 22480 | 1253 | 1838 |

性能差异必须与 work volume 和最终图规模一起解释。若某方法通过少做 LLM、embedding、DB 或 graph work 获得加速，不能无条件称为 pure scheduling speedup。

## Per-history 结果

| Method | History | Episodes | QA | R@10 | P95 freshness (s) | P99 freshness (s) | Makespan (s) | Max backlog | Nodes | Relationships |
|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| U0 | `07741c45` | 49 | 0.000 | 1.000 | 105.873 | 561.338 | 2409.882 | N/A | 293 | 438 |
| U0 | `b6019101` | 49 | 0.000 | 1.000 | 127.584 | 205.629 | 2368.025 | N/A | 371 | 571 |
| U0 | `6071bd76` | 46 | 1.000 | 1.000 | 76.337 | 111.694 | 1770.817 | N/A | 253 | 338 |
| U0 | `a2f3aa27` | 44 | 0.000 | 1.000 | 82.602 | 129.079 | 1952.438 | N/A | 282 | 358 |
| A0 | `07741c45` | 49 | 0.000 | 1.000 | 2336.837 | 2411.512 | 2411.700 | 49 | 294 | 438 |
| A0 | `b6019101` | 49 | 0.000 | 1.000 | 2279.824 | 2367.464 | 2367.714 | 49 | 363 | 571 |
| A0 | `6071bd76` | 46 | 1.000 | 1.000 | 1725.939 | 1786.289 | 1786.488 | 46 | 254 | 338 |
| A0 | `a2f3aa27` | 44 | 0.000 | 1.000 | 1866.771 | 1957.032 | 1957.210 | 44 | 282 | 358 |
| P(C=2) | `07741c45` | 49 | 0.000 | 1.000 | 1867.920 | 1928.623 | 1928.728 | 49 | 306 | 480 |
| P(C=2) | `b6019101` | 49 | 0.000 | 1.000 | 2119.442 | 2262.907 | 2262.954 | 49 | 413 | 653 |
| P(C=2) | `6071bd76` | 46 | 1.000 | 1.000 | 1401.493 | 1478.367 | 1478.449 | 46 | 275 | 382 |
| P(C=2) | `a2f3aa27` | 44 | 0.000 | 1.000 | 1651.244 | 1685.322 | 1685.397 | 44 | 259 | 323 |

## 原始制品

- Native U0: `paper-eval-v3/artifacts/paper_eval/native_baseline/runs/nb-20260816-001`
- A0/P suite: `paper-eval-v3/artifacts/paper_eval/baseline_suite/runs/bs-dev-20260816-001`
- Graph-quality overlay: `paper-eval-v3/artifacts/paper_eval/graph_quality_overlay/runs/gq-dev-20260817-001`

Level-0 JSONL 与每个 checkpoint/result 是可离线重算的 source of truth；本报告只是这些 sealed artifacts 的确定性投影。Reader/Judge 原文保留在 git-ignored 私密制品中。

## 解释边界

- 这 4 个问题都是 development/calibration 数据，不能据此报告论文置信区间或显著性。
- 本地 Qwen Reader/Judge 与公开 LongMemEval/Zep 的 GPT-4o 配置不同，因此只能写 `PROTOCOL_ALIGNED_NOT_NUMERICALLY_MATCHED`。
- 低 QA 若同时伴随高 Session Evidence Recall@10，应归因到 retrieval 之后的 context assembly、Reader 或 Judge 路径，不能表述为 Graphiti 丢失了 gold sessions。
- Graph-native overlay 是预定义诊断，不替换冻结主指标，也不访问 gold answer 进行 retrieval。
