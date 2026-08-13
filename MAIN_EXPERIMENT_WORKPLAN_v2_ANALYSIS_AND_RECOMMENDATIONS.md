# MemBind主实验计划v2.0分析与优化建议

> **分析时间**: 2026-08-13  
> **分析对象**: `（主实验）MemBind_MAIN_EXPERIMENT_WORKPLAN_v2_FINAL.md`  
> **当前状态**: Native Characterization C5已完成，C6未生成，主实验未启动

---

## 执行摘要

### 总体评价：★★★★☆ (4/5) - 优秀但需务实调整

这个主实验计划体现了**MLSys/NSDI/OSDI级别的方法学意识**，核心设计合理：
- ✅ 先qualification baseline再比较（P1-P3机制）
- ✅ Development/held-out严格分离（避免data leakage）
- ✅ 多baseline ladder对比（U0/A0/P*/M*）
- ✅ Quality与Performance分离评估

**但存在5个需要立即优化的实际问题**，否则会导致：
1. 大量重复工作（忽略C0-C5已完成的qualification）
2. 卡在不现实的exact reproduction要求
3. 统计power不足（仅8个held-out samples）
4. Baseline人为削弱（P*必然violation但规则过严）
5. 过度重跑（failure policy过于保守）

---

## 关键发现

### 发现1：C0-C5已完成大部分P1-P3要求 ✅

**C0-C5已提供的evidence**：
- **C1**: Instrumentation qualification（overhead 1.317% < 2% ✓）
- **C2**: 完整4-history Native breakdown（188 episodes，151.36分钟）
- **C2**: LLM/embedding/DB work volume完整telemetry
- **C3**: Dependency characterization（22.92% arrival-ready）
- **C5**: C={1,2,4,8}并发对比（C=1无violation，C≥2有order violation）
- **Judge**: 14-item synthetic qualification PASS（100% agreement）

**P1-P3要求的mapping**：

| P1-P3要求 | C0-C5已完成 | 仍需补充 |
|-----------|-------------|----------|
| N1 Upstream identity | ✅ C2 reference alignment decision | 形式化manifest |
| N2 Instrumentation parity | ✅ C1 passed (1.317%) | 无 |
| N3 Functional qualification | ✅ C2 188 episodes | 形式化report |
| R1 Official dataset | ⚠️ 使用frozen split v1.3 | 绑定official cleaned release |
| R2 Evaluator parity | ⚠️ LongMemEval adapter存在 | 验证100% parity |
| R3 Dataset mapping | ⚠️ dataset generator完成 | 验证parity |
| R4 Upstream cross-check | ✅ C2 reference alignment | 扩展到full development |
| R5 Reference reproduction | ❌ 未开始 | **这是真正的新工作** |

**优化建议**：P1-P3改为P1*-P3*（Verification模式而非Redo模式）

```text
P1* - 验证C0-C5已满足N1-N3
  - 复用C1 instrumentation evidence
  - 复用C2 reference alignment
  - 生成形式化manifest
  
P2* - 补充R1-R5（重点是R5 reference reproduction）
  - R1-R4: 形式化验证现有工作
  - R5: 执行protocol-aligned reproduction
  
P3* - Freeze Native baseline
  - 输出: C2 manifest + P1*/P2* qualification
```

**时间节省**: 20-30%

---

### 发现2：Exact numeric reproduction不现实且不必要 ⚠️

**Agent分析结论**：

**Priority A (Exact Reproduction): ❌ NOT FEASIBLE**

理由：
- Graphiti maintainer明确："very early version of Graphiti" in early papers
- GPT-4o historical snapshot unavailable
- Old Graphiti revisions incompatible  
- Zep cloud implementation unavailable
- Mnemis明确修改了base extraction + retrieval

**Priority B (Protocol-Aligned): ✅ FEASIBLE and CORRECT**

已完成：
- Reference alignment decision: `artifacts/diagnostics/native_characterization_reference_alignment_decision_20260811.md`
- Reference-aligned freeze: `artifacts/native_characterization/freeze_reference_aligned_64k.json`

仍需：
- Pin official LongMemEval cleaned dataset with SHA256
- Verify 100% evaluator semantic parity
- Document all configuration differences
- Generate `PROTOCOL_ALIGNED_NOT_NUMERICALLY_MATCHED` report

**优化建议**：

```text
P2/R5修改为分级目标：

【MUST】Protocol-Aligned Reproduction
  ✓ Official LongMemEval-S cleaned dataset
  ✓ Official evaluation semantics
  ✓ Pinned Graphiti v0.29.3 commit 021d3a5
  ✓ Full stack documentation
  ✓ Report differences vs published results
  
【BEST EFFORT】Numeric Reference
  - IF Graphiti has public benchmark → compare
  - IF exact historical stack available → exact reproduction
  - ELSE: protocol-aligned only, no forcing numeric match
  
【STOP CONDITION】
  ONLY if "严重异常 AND 无法解释" → FAIL
  NOT "必须达到Mnemis 91.6" 或其他硬性数值target
```

---

### 发现3：实验规模偏小但可接受为bounded pilot 📊

**当前规模**：
- Calibration: 4 histories
- Held-out evaluation: 8 histories
- Total: **12 histories**

**Main experiment load**：
```text
P5 Calibration: 28 runs (4 histories × 7 configs)
P7 Quality:     32 runs (8 histories × 4 methods)
P8 Performance: 96 runs (8 histories × 4 methods × 3 repeats)
---
TOTAL:         156 runs

Time estimate (from C2):
  Per history:   ~38 minutes
  Serial total:  98.4 GPU-hours
  With C=8:      49.2 GPU-hours ≈ 2.0 days
```

**问题**：
1. 8个held-out samples统计power有限
2. 同行对比：Agentix用大规模workload，A-Mem用20 tasks
3. 3 repeats合理，但96 runs占总量61.5%

**优化建议**：

```text
【当前方案】12-history Bounded Pilot
  适用：内部GO/NO-GO decision
  风险：审稿人可能质疑样本量
  应对：明确标记"bounded pilot"，承诺后续扩展
  执行：保持当前计划
  
【推荐方案】20-24 history Paper Version
  P7 Quality:     16 held-out (增加1倍)
  P8 Performance: 12 histories × 3 repeats
  Total runs:     ~220 (增加40%)
  Total time:     ~70 GPU-hours with C=8
  
【长期方案】Production-Level (暂不推荐)
  50+ histories, multi-load, MemoryArena
  超出当前main experiment scope
```

**我的建议**：
- **Phase 1**: 执行12-history pilot完成内部验证
- **Phase 2**: 根据结果决定是否扩展到20-24 for paper
- **重要**: 明确标记为"bounded pilot"，不过度generalize

---

### 发现4：P5方法选择规则需要现实调整 ⚠️

**计划要求**：
```text
P* qualified_set = all C with zero direct hard invariant violation
M* 必须 zero violation + correctness qualification
```

**C5实际结果**：
- C=1: ✅ Pass (no violation)
- C=2: ❌ Source-order violation
- C=4: ❌ Source-order violation  
- C=8: ❌ Source-order violation

**问题**：
- P* qualified_set = **空集**
- 按计划应选"P-fastest-unqualified"
- 但这会让baseline显得人为削弱

**优化建议**：

```text
P5方法选择规则调整：

【P* Whole-Update Parallel】
  Primary: zero direct violation
  
  IF qualified_set 非空:
    选best goodput
    
  IF qualified_set 为空:
    选best goodput from all C
    Label = "P*-best-throughput"
    Must report violation count in main table
    Must classify violation nature:
      - order-only violation (semantic still valid?)
      - data loss violation (hard failure)
      - correctness violation (wrong output)
    
【M* MemBind】
  Hard requirement: zero direct violation
  
  IF no qualified C:
    STOP main experiment
    OR diagnostic mode
```

**关键**：让读者看到真实trade-off：
```text
U0 Native Serial:       slow but preserves order
P* Whole-Update:        2x faster but order violations
A0 Async-Serial:        quick return but stale memory
M* MemBind:             preserves order AND fast (if true)
```

不隐藏P*的limitation，但也不人为削弱它。

---

### 发现5：Failure policy需要分级处理 ⚠️

**计划要求**：
```text
Infrastructure failure:
  保留evidence
  整个paired block作废
  四个methods全部重新运行
```

**风险**：
- P8有96 runs
- 如果5次infra failure
- 每次影响1 block (4 methods)
- 需要重跑20次
- **额外成本: +21% GPU时间**

**优化建议**：

```text
分级Failure Policy：

【Tier 1 - True Infrastructure】
  vLLM crash, Neo4j outage, machine restart
  → 整个block重跑（保持原计划）
  
【Tier 2 - Single-Method Transient】
  单个method网络timeout/429（可恢复）
  → 只重跑该method
  → 保留block内其他成功runs
  → 必须记录asymmetric retry
  
【Tier 3 - Treatment-Induced】
  Method semantic failure
  → 保留为scientific outcome（保持原计划）
  
必须记录所有failure：
  - failure_class
  - affected_methods
  - retry_decision  
  - time_proximity（验证非selective rerun）
```

**风险控制**：增加"retry audit trail"，审稿人可验证公平性

---

## 优化后的执行计划

### Phase 0 - Immediate Prep (1-2 days)

```text
✓ P0 current state snapshot
✓ Audit C0-C5 evidence mapping to P1-P3
✓ 确认GPT-5.5 adapter已从U0 clean
✓ 文档化dataset selection rationale
  （12 histories是bounded pilot还是full population？）
↓
GATE: 确认可以复用C0-C5，避免重做
```

### Phase 1 - Native Qualification (3-5 days)

```text
✓ P1*: 验证C0-C5满足N1-N3
  - N1: 复用C2 reference alignment → formal manifest
  - N2: 复用C1 instrumentation (1.317%) → PASS
  - N3: 复用C2 functional qualification → formal report
  
✓ P2*: 补充R1-R5（重点R5）
  - R1: Pin official LongMemEval cleaned + SHA256
  - R2: Verify 100% evaluator parity
  - R3: Verify dataset mapping parity
  - R4: Expand upstream cross-check to full development
  - R5: Execute protocol-aligned reproduction
  
✓ P3*: Freeze Native baseline
  - Output: NATIVE_BASELINE_FREEZE.json
  
✓ P4: Freeze development/held-out split
  - Output: main_test_manifest.json
↓
GATE: Native baseline qualified and frozen
```

### Phase 2 - Method Calibration (5-7 days)

```text
✓ P5: Calibrate A0/P*/M* on 4 development histories
  - A0: 1 config × 4 = 4 runs
  - P*: 3 C-values × 4 = 12 runs
  - M*: 3 C-values × 4 = 12 runs
  Total: 28 runs
  
✓ Apply adjusted selection rules:
  - P*: 允许选unqualified if qualified_set=空
  - M*: 必须qualified
  
✓ P6: Freeze methods + stack + main_interarrival
  - Output: method_selection_manifest.json
↓
GATE: Method configurations frozen
```

### Phase 3 - Main Execution (7-10 days + 20% buffer)

```text
✓ P7: Quality Surface
  - 8 held-out histories × 4 methods = 32 runs
  - Fresh graph per method/history
  - QA Accuracy + Evidence Recall@10
  - Direct correctness violations
  
✓ P8: Performance Surface  
  - 8 histories × 4 methods × 3 repeats = 96 runs
  - Blocked randomization
  - P95 freshness, goodput, makespan
  - Work volume fairness tracking
  
✓ Apply tiered failure policy
  - Infrastructure: whole block rerun
  - Transient: method-only rerun with audit
  - Treatment: keep as outcome
↓
GATE: All data collected (预留20% buffer for failures)
```

### Phase 4 - Analysis & Report (3-5 days)

```text
✓ P9: Statistics + main table
  - History-level paired comparison
  - Quality: McNemar test
  - Performance: cluster bootstrap
  - Work volume disclosure
  
✓ P10: Main experiment report
  - Headline table (Quality + Performance unified)
  - Methods: U0/A0/P*/M*
  - Interpretation: STRONG/PARTIAL/NOT_SUPPORTED
  
✓ P11: STOP (不自动进入ablation/MemoryArena/etc.)
```

**总时间估算**：
- Aggressive: 20-30 days
- Conservative: 35-45 days  
- GPU hours: 50-70 hours (with C=8 parallel + 20% failure buffer)

---

## 风险与缓解

### 风险1：C6未完成导致主实验正当性不足 ⚠️

**现状**：C5已完成，但C6 verdict未生成

**影响**：主实验计划明确说"不等待C6"，但：
- C6是正式problem verdict（SUPPORTED/PARTIAL/NOT_SUPPORTED）
- 没有C6，直接做主实验可能被质疑"为什么认为问题存在"

**缓解**：
```text
Option A (推荐):
  在P0-P1*阶段同时完成C6 verdict
  用C0-C5 evidence生成CHARACTERIZATION_REPORT + DESIGN_DECISION
  即使C6是PARTIAL，也能justify bounded pilot
  
Option B (次优):
  明确标记main experiment为"exploratory"
  承认characterization未完全closure
  风险：审稿人质疑motivation
```

### 风险2：8-sample统计power不足 📊

**影响**：
- 审稿人可能质疑generalizability
- CI可能很宽
- 个别outlier影响大

**缓解**：
```text
Phase 1 (当前):
  执行12-history pilot
  明确标记"bounded pilot"
  承诺扩展路径
  
Phase 2 (如果pilot成功):
  扩展到20-24 histories
  增量执行（复用pilot的12）
  Total: 32 new + 220 total runs
  Time: +30-40 GPU-hours
```

### 风险3：P*必然unqualified导致baseline弱化 ⚠️

**影响**：
- C5显示C≥2全部order violation
- 如果P*标记"unqualified"，读者可能认为baseline不公平

**缓解**：
```text
在main table中：
  1. 报告P*的violation count（透明）
  2. 分类violation nature（order vs data-loss）
  3. 报告P*的actual goodput（不削弱数字）
  4. Discussion说明order violation的semantic impact
  
关键：让审稿人看到真实trade-off，不是strawman
```

### 风险4：Reference reproduction失败导致P2卡住⛔

**影响**：
- 如果坚持exact numeric match → 不可能完成
- 如果P2 FAIL → 主实验无法启动

**缓解**：
```text
明确接受protocol-aligned reproduction为PASS条件
不设硬性数值target
重点：证明当前U0是valid、documented、reproducible baseline
不需要：匹配无法获得的历史stack数值
```

---

## 与同行工作对比的正确姿态

### 不应该做：

❌ "我们的U0必须达到Mnemis 91.6% QA accuracy"
  → Mnemis修改了extraction和retrieval

❌ "我们要exact reproduce早期Graphiti paper数字"
  → Maintainer明确说那是very early version

❌ "我们的baseline比Zep cloud差，所以不valid"
  → Zep cloud != OSS Graphiti v0.29.3

### 应该做：

✅ **Protocol-aligned reproduction**
  - Same LongMemEval dataset & evaluation
  - Pinned Graphiti v0.29.3 upstream
  - 完整documented stack
  - 报告与published results的差异

✅ **Contextual reference**
  - 引用public benchmark results作为context
  - 说明配置差异
  - 不claim exact reproduction when infeasible

✅ **Focus on baseline validity**
  - U0代表pinned upstream Graphiti
  - 不是最强Graphiti variant
  - 但是reproducible、documented、fair baseline

---

## 最终建议优先级

### 【P0 - 立即执行】

1. ✅ **完成P0 snapshot**
   - 记录git状态、所有版本、exposed IDs
   - Audit C0-C5 evidence可复用性
   
2. ✅ **调整P1-P3为P1*-P3***
   - Verification模式而非Redo模式
   - 明确mapping C0-C5 → P1-P3要求
   
3. ✅ **明确dataset rationale**
   - 12 histories是bounded pilot还是full LongMemEval-S？
   - 如果是subset，说明selection不基于performance

### 【P1 - 近期调整】

4. 🔄 **放宽P2/R5 exact reproduction要求**
   - Accept protocol-aligned as PASS
   - 不设硬性numeric target
   
5. 🔄 **调整P5 violation handling**
   - 允许P*选unqualified if必要
   - 透明报告violation nature
   
6. 🔄 **优化failure policy**
   - 分级处理（infrastructure vs transient）
   - 增加retry audit trail

### 【P2 - 如果时间允许】

7. 📊 **考虑12→20 history扩展**
   - 先执行12-history pilot
   - 根据结果决定是否扩展
   
8. 📊 **考虑增加load sweep**
   - 当前只有load≈1.0
   - 可能增加0.5, 1.5测试robustness

### 【P3 - Paper submission前】

9. 📝 **评估pilot是否足够**
   - 12 histories对于MLSYS/NSDI是否可接受
   - 或需要扩展到20-24
   
10. 📝 **补充broader workload roadmap**
    - MemoryArena integration
    - 其他memory architectures
    - 但明确这些是future work

---

## 总结

这个主实验计划的**核心方法学是正确的**：
- ✅ 先qualification再比较
- ✅ 数据严格分离
- ✅ 多baseline ladder
- ✅ Quality + Performance分离

但需要**5个务实调整**：
1. 🔧 复用C0-C5（节省20-30%时间）
2. 🔧 Accept protocol-aligned reproduction（避免卡住）
3. 🔧 调整violation handling（不削弱baseline）
4. 🔧 优化failure policy（减少重跑）
5. 🔧 明确bounded pilot定位（管理审稿期望）

**执行路径**：
```text
Phase 1: 完成12-history bounded pilot (20-30 days)
         ↓
Phase 2: 内部GO/NO-GO verdict
         ↓
Phase 3: 根据结果决定是否扩展到20-24 for paper
```

**关键成功因素**：
1. 不要重做C0-C5已完成的工作
2. 接受protocol-aligned reproduction为valid
3. 透明报告所有baseline limitations
4. 明确bounded pilot定位

这样的计划既保持了顶会方法学标准，又具备现实可执行性。
