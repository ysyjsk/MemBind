# Quality Evaluation v1：源码适配、TDD 与三基线结果

日期：2026-08-17  
状态：`PASS`，12/12 个只读评测单元已 sealed  
Run ID：`qev1-dev-20260817-001`  
最终结果 payload SHA-256：`305c540a26c3255d84b9a8ff0bb78baca8d093cdce9a6317934010c3c553b286`

## 1. 本轮边界

本轮只修复 quality evaluation 链路，并直接复用已经落盘的 U0、A0、P(C=2)
共 12 个 Neo4j namespace。没有重跑 construction，没有清理或修改 namespace，
没有修改 Graphiti、三个 baseline 的构建算法、冻结 Qwen Judge 的 rubric/prompt/判分逻辑，
也没有把 Reader/Judge/context 构造耗时计入 construction makespan、freshness、goodput 或 backlog。

执行顺序为：

1. U0 四题；
2. 若四题 Reader/Judge 全部有效且至少答对 2 题，则立即冻结 Quality Evaluation v1；
3. 用完全相同的 retrieval/context/Reader/Judge identity 执行 A0 和 P(C=2)；
4. 任一服务或 pipeline 错误均停止，不做自动调参。

U0 得到 2/4，且 4/4 均为有效 Judge 输出，因此满足预先给定的停止条件并冻结。

## 2. 公开源码依据与精确适配

### LongMemEval

- Repository：<https://github.com/xiaowu0162/LongMemEval>
- 固定 commit：`9e0b455f4ef0e2ab8f2e582289761153549043fc`
- `src/retrieval/run_retrieval.py`：
  - source SHA-256：`efd7fc5969a904717741fadca3c7dc73611ddbb2aaf3ef33117ebb6943b3e346`
  - `flat-bm25` 使用 `rank_bm25.BM25Okapi`；
  - corpus 与 query 都使用源码中的 `str.split(" ")`。
- `src/generation/run_generation.py`：
  - source SHA-256：`4f1eb3c69d7ad40f04065b9c0bc86f6582441018fc6ff751d162d66c95baf672`
  - `flat-turn` 将检索到的 user turn 自动扩展为“该 user turn + 紧随其后的 turn”；
  - 删除 `has_answer` 后再送入 Reader；
  - 检索后按 session date 做 chronological reorder；
  - 支持 JSON history 表示。

### rank-bm25

- Distribution：`rank-bm25==0.2.2`
- Repository：<https://github.com/dorianbrown/rank_bm25>
- 固定上游 commit：`47aa3ddf8dc1ebeb7ef4e65f2b4536af44594099`
- 上游 source SHA-256：`0de6c46a8d5a9ad63ff7034012cda1b296a12b7000fdee4479101375fdf62968`
- 当前两个 venv 中的 installed source SHA-256：
  `2f28cc795415c01e9f3db5a8ed019774f9cba747b272c9c304271589b8081ac6`

### Zep / Graphiti

- Zep Repository：<https://github.com/getzep/zep>
- 固定 commit：`be263ee23085410185835e0d8508b47fd35e9abb`
- `benchmarks/longmemeval/zep_longmem_eval.py` source SHA-256：
  `785eacdfd9a388ea00f636074579f7409e04a48d0c1bf5685022f3830a6b72d4`
- 复用其 LongMemEval 评测中的 Top-20 graph fact 与 `valid_at/invalid_at`
  表示方式。
- Graphiti 使用仓库已 pinned 的 0.29.3 源码：edge surface 为 BM25 + cosine → RRF，
  episode surface 为 BM25 → RRF；node/community surface 在本轮关闭。

### 本项目的适配边界

最终链路为：

```text
Graphiti edge BM25+cosine→RRF, Top-20 facts
Graphiti episode BM25→RRF, Top-20 candidate sessions
  ↓
候选 sessions 内所有 USER turns
  ↓
LongMemEval flat-turn BM25Okapi, global Top-10
  ↓
每个 USER turn 扩展到紧随其后的 turn
  ↓
确定性去重 + chronological reorder
  ↓
facts/rounds structured JSON
  ↓
统一 Qwen Reader + 已冻结 Qwen Judge
```

没有使用当前四题的 gold session ID、reference answer 或答案标签进行 selection。
LongMemEval 原实现用 NumPy `argsort()[::-1]`；本项目唯一有意的稳定化差异是：
BM25 同分时保持 Graphiti candidate rank 与原 source-turn 顺序，避免 NumPy/版本相关的
零分候选反转。这是确定性 contract，不是基于四题结果的调参。

冻结的组件 identity 为：

| Component | SHA-256 |
|---|---|
| Retrieval config | `62535d82129d01d6cbf8c3d5c13f656e60d89d821a9d9b9ca7bc1ed51c53fa7d` |
| Context policy | `6c717c5a39af98e17d1b9fe55f0425f54401c13b9b82d9d1aeab5a2db26eef49` |
| Qwen Reader | `e8b78482c28b9096d790ecff789bcf2ae5a023868ee1471b1fa485bbf60412b7` |
| Frozen Qwen Judge | `03dca463e760d957054973c56302d259a3e66e4397f26be5da753e0f775479e6` |

## 3. Reader 与指标合同

Reader 使用统一 prompt，不区分题型，不注入额外 system prompt：

```text
Answer the question using only the provided memory evidence.
Consider evidence in chronological order.
When information changes, use the latest effective information before the
question date and distinguish current facts from future plans.
Return only a concise final answer.
```

固定配置：Qwen3-32B-FP8、`thinking=false`、temperature 0、max_tokens 256；
`finish_reason != stop` 时记为 invalid，不能混入 QA denominator。

正式输出包含：

- Session Recall@1/@3/@5/@10、MRR、nDCG@10；
- edge provenance precision/coverage proxy；
- stale/active/future fact、conflict group、latest-valid rank 等 temporal diagnostics；
- 每题 Reader prompt/completion tokens、Judge validity 与 failure category。

LongMemEval 没有 gold fact labels，因此 edge 指标被明确命名为
`PROVENANCE_PROXY_NOT_GOLD_FACT_RECALL`，不能写成 gold fact Recall/Precision。

## 4. TDD 证据

本轮遵循 RED → targeted GREEN → related regression：

- 旧 per-session `_best_round()` 合同 RED：旧实现把 4 个合法 user rounds 压成 3 个，
  并在 BM25 前截断 sessions，导致第 11 个相关 candidate 无法被选中；
- LongMemEval flat-turn ContextPack GREEN：7 个核心 metrics/context tests 通过；
- private-first recovery bundle RED/GREEN：中断后只恢复 public projection，禁止重采样；
- phased runner RED/GREEN：强制 U0 gate → A0 → P(C=2)，并恢复 incomplete stage；
- mixed timestamp RED/GREEN：LongMemEval 的 `2023/06/23 (Fri) 07:31` 与 Graphiti
  ISO-8601 timestamps 可共同进行 temporal classification 与 chronological sort；
- focused：32 passed；
- related regression：98 passed。

JUnit 证据位于 `logs/TDD_*QUALITY_V1*20260817.xml` 与
`logs/TDD_*QUALITY_EVALUATION_V1_20260817.xml`。

最初两个 U0 attempts 在任何 Reader 请求前因 mixed timestamp adapter 错误 fail-closed；
修复后从新 attempt 执行。旧 attempts 保持 `incomplete_non_mergeable`，没有删除、覆盖或
合并，也没有重跑 construction。

## 5. 主结果

| Method | QA | Valid | Recall@1 | Recall@3 | Recall@5 | Recall@10 | MRR | nDCG@10 | Avg Reader prompt tokens |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| U0 | 2/4 = 0.50 | 4/4 | 0.50 | 1.00 | 1.00 | 1.00 | 1.00 | 1.00 | 9,323.8 |
| A0 | 2/4 = 0.50 | 4/4 | 0.50 | 1.00 | 1.00 | 1.00 | 1.00 | 1.00 | 9,337.0 |
| P(C=2) | 2/4 = 0.50 | 4/4 | 0.50 | 1.00 | 1.00 | 1.00 | 1.00 | 1.00 | 9,392.0 |

与已有 U0 QA decomposition 对比：

| U0 quality path | QA | Total prompt tokens |
|---|---:|---:|
| 旧 Top-10 full sessions | 1/4 | 115,223 |
| Gold-only oracle | 3/4 | 23,305 |
| 新 Quality Evaluation v1 | 2/4 | 37,295 |

新链路相对旧 full-session path 提升 25 个百分点，并将总 prompt tokens 降低约 67.6%；
同时没有使用 gold 做 selection。它达到预设的 2/4–3/4 停止区间，因此已冻结，不能继续
针对这四个 development cases 调 Top-K 或写题目特例。

### U0 逐题结果

| History | Gold session ranks | Prompt tokens | Prediction 摘要 | Judge | 诊断 |
|---|---|---:|---|---:|---|
| `07741c45` | [1,2] | 8,846 | old sneakers 在 closet shoe rack | 正确 | Success |
| `b6019101` | [1,2] | 8,451 | 5 部 MCU films | 正确 | Success |
| `6071bd76` | [1,2] | 11,101 | 数值写成 6→5，但语言判断为 “more water” | 错误 | Reader current-state/comparison interpretation |
| `a2f3aa27` | [1,2] | 8,897 | 1,250 followers，而 reference 为 1,300 | 错误 | latest quantitative state interpretation |

四题的 post-hoc context gold-session coverage 都为 1.0；因此后两题不是“gold session 完全没召回”，
而是细粒度证据中的比较方向/最新数值解释仍失败。冻结 Judge 对这两题的 negative 判决与答案内容一致，
本轮没有观察到新的明显 Judge false negative。

### 三方法之间的诊断差异

- 三方法的 session ranking 与最终 QA 在 4 个 development histories 上相同；当前样本过小，
  不能据此宣称三种 construction semantics 等价。
- P(C=2) 在 `a2f3aa27` 的 Top-20 facts 中出现 2 条 stale facts，因此其
  `stale_fact_count_macro=0.5`；U0/A0 为 0。
- edge gold-source provenance proxy 的 macro precision@10 为：U0 0.15、A0 0.15、
  P(C=2) 0.225；coverage@10 为：U0 0.375、A0 0.375、P(C=2) 0.625。
  这些是来源归因 proxy，不代表 fact correctness，也不能用于“P 优于 U0”的结论。
- Recall@3 之后仍饱和，说明新增 Recall@1/MRR/nDCG 能揭示 Top-1 与多-gold 排名，但这四题
  仍不足以让所有 quality 差异显著；正式 held-out 规模必须继续使用同一冻结链路。

## 6. 持久化位置

- 最终总结果：
  `artifacts/paper_eval/quality_evaluation_v1/runs/qev1-dev-20260817-001/QUALITY_EVALUATION_V1_RESULTS.json`
- U0 freeze：
  `artifacts/paper_eval/quality_evaluation_v1/runs/qev1-dev-20260817-001/U0_FREEZE_DECISION.json`
- 中间进度：
  `artifacts/paper_eval/quality_evaluation_v1/runs/qev1-dev-20260817-001/progress.json`
- 每题 private/public bundle：
  `artifacts/paper_eval/quality_evaluation_v1/runs/qev1-dev-20260817-001/units/{u0,a0,pc2}/<history>/attempt-*/`
- live log：`logs/QUALITY_EVALUATION_V1_qev1-dev-20260817-001.log`
- 旧 decomposition：
  `artifacts/paper_eval/qa_decomposition/runs/qd-dev-20260817-001/QA_DECOMPOSITION_RESULTS.json`

所有 12 个 sealed bundles 与最终 report 的 payload hash 均已离线复验通过。

## 7. 当前科研结论

Quality Evaluation v1 解决了旧 full-session Reader path 的主要工程问题：上下文过长、旧/新
状态混杂，以及 Recall@10 单指标饱和。它把 U0 从 1/4 恢复到 2/4，并显著降低 prompt 规模，
但没有人为追到 gold-only oracle 的 3/4。

当前四题只适合开发期 pipeline qualification，不足以证明 A0/P 或未来 MemBind 的正式质量差异。
后续 U0、A0、P(C=2)、MemBind/M* 必须统一复用上述四个 component hashes；正式 runtime
主表仍使用 construction/freshness/goodput/backlog，quality overlay 独立报告，避免 Reader/Judge
延迟污染系统性能结论。
