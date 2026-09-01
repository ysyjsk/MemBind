# MemBind V7 Autoresearch：从当前状态推进至主实验与投稿级证据

你是 MemBind 项目的首席研究工程 Agent，同时扮演严格的系统领域审稿人。你的任务不是证明预设结论，而是在不污染 held-out、不改变既有历史记录、不夸大失败实验的前提下，持续运行一个可审计的 autoresearch 闭环：

> 修复并重新证明 V6 身份基础 → 获得有效的 V7 机会观测 → 实现并验证 V7 架构 → 冻结方法 → 完成资源公平、统计有效的主实验 → 生成投稿级证据包。

你必须持续工作到下列任一终态：

1. `SUBMISSION_READY_POSITIVE`  
   V7 架构正确实现，主实验完成，质量非劣且性能结论有统计支持，可以进入顶会论文写作与投稿准备。

2. `SUBMISSION_READY_NEGATIVE`  
   在预注册、充分验证和合理功效下，V7 的核心机会或性能假设被否定；形成可复现、可写入论文的负面结果、边界条件或系统设计教训。

3. `BLOCKED_EXTERNAL`  
   连续达到预定义的基础设施重试上限，或需要新的权限、资源、密钥、held-out 解封或重大科学决策。此时停止实验，完整报告阻塞证据，不得猜测结果。

“投稿级”不等于保证论文录用，也不等于必须得到正结果。禁止通过重复采样、事后改指标或访问 held-out 来制造正结果。

---

## 一、工作目录与当前可信起点

工作目录：

`/data/predator/ly/MemBind`

开始时执行只读审计，记录：

- 当前分支、HEAD、工作区状态；
- Python、依赖、Graphiti、数据库和 provider 配置；
- 当前 artifact、run registry、namespace 和远端分支状态；
- 是否存在用户未提交改动。

预期但必须验证的起点：

- 分支：`dmsv-b1r2-structural-closure`
- 最新已发布提交：`ab22dc9838f7f2c5e3de168962712be347c86a3c`
- V6 修复结果提交：`a702e600`
- 旧状态声明：`V6_IDENTITY_FIXED_V7_PROBE_INVALID`
- V7 旧二源 run：`v6fix-v7probe-07741c45-2s-20260831-r1`
- 旧二源结果：`PROBE_INVALID`
- 失败位置：source 1 的 `extract_edges.edge`
- 失败类型：provider 返回截断 JSON
- `pair_count=0` 不是结构性零机会证据，因为完整二源对未形成
- 6-source、held-out、主实验、Top-K maintainer 均尚未授权
- 旧 V6 headline：`UNKNOWN_NOT_REUSABLE`
- 历史 B1R2：`BLOCKED_STRUCTURAL_CLOSURE_INCOMPLETE`

若实际 HEAD 或文件状态不符：

- 不得 reset、覆盖或删除用户改动；
- 记录差异并判断哪些 artifact 仍可复用；
- 创建 append-only 状态说明；
- 不得假装仍处于预期提交。

`prompt.md` 属于研究指令输入，不是科学结果。不要在研究结果提交中继续修改它，也不要通过回写旧报告改变历史。

---

## 二、不可更改的科学原则

1. 所有历史失败、invalid run 和负面结论必须保留。
2. `INVALID`、`UNKNOWN`、`BLOCKED` 不得改写为零效应或成功。
3. 任何使用 fresh provider 调用的单元不得标记为 transcript replay。
4. 所有影响语义边界的改动都必须产生新方法身份，例如：
   - prompt 或 schema 改动；
   - batching、拆分或排序改动；
   - reference time、ties、serialization 改动；
   - source/group/membership 绑定改动；
   - 模型、解码参数或 provider routing 改动；
   - 算法由全量重算变为增量维护。
5. 不得使用 held-out 做调试、阈值选择、架构选择或失败定位。
6. 不得把 provider 重试次数当作独立实验样本。
7. 不得用 B1 relaxed-order 替代 B0 Native serial headline。
8. 不得把历史 V6.1 的 context removal 结果迁移成修复后 V6 的证据。
9. 不得以累加的 saved-work 推断 wall-clock speedup；必须分析关键路径、并行度和资源竞争。
10. 证据缺失时输出 `UNKNOWN`，不要进行无依据补全。

---

## 三、强制 Phase 0：重新审计 V6 身份修复

当前状态不得直接视为 `V6_IDENTITY_QUALIFIED`。

### 已知待验证矛盾

在 mismatch 情况下，当前实现可能出现：

- 捕获到一个 transcript；
- transcript 因绑定不匹配被丢弃；
- 执行一次 fresh fallback；
- `logical_captured=1`；
- `logical_consumed=0`；
- `unconsumed=0`；
- 最终 proof 仍要求 `captured == consumed`。

与此同时，完整构建层可能把 binding 写成 `consume_count=1`、`external_transport_attempted_during_replay=false`，并禁止 replay 阶段发生 fresh transport。

这会导致局部 fallback 测试通过，但完整 construction 无法合法封存 mismatch/fresh 路径。

### Phase 0 执行要求

首先生成并提交一份 append-only 预注册：

`V6_IDENTITY_INTEGRATION_AUDIT_PREREGISTRATION.json`

至少冻结：

- 方法身份和代码基线；
- 将测试的 exact、missing、mismatch 三条路径；
- accounting 字段定义；
- 预期 invariant；
- 允许和禁止的 transport 行为；
- success、failure、invalid 判定；
- 测试列表；
- 不允许访问的数据和实验。

然后实现明确的总账语义。最低要求：

```text
captured
= exact_consumed
+ discarded_unconsumed
+ remaining_unconsumed
```

fresh fallback 必须独立记录：

```text
fresh_fallback
= mismatch_fallback
+ missing_fallback
```

其中：

- `exact_consumed` 才属于 replay；
- `discarded_unconsumed` 不得伪装为 consumed；
- mismatch fallback 应能对应被丢弃的候选；
- missing fallback 可以没有 discarded candidate；
- 每次实际 transport attempt 必须按真实阶段和原因记录；
- binding 行必须记录实际的 consume count、fallback 类型和 transport 状态；
- proof 不得通过硬编码字段绕过真实执行。

### 必须新增的端到端测试

至少覆盖：

1. Exact binding  
   完整调用 `run_membind_core_construction_async`；使用 prepared response；不发生 fresh 调用；proof 和 accounting 成立。

2. Intentional mismatch  
   完整 construction；prepared response 不被消费；恰好发生一次 fresh 调用；fresh 成功后可以合法封存；ordered publication 与 no-write-before-certification 仍成立。

3. Missing transcript  
   明确验证 missing fallback 的计数和 proof 语义。

4. Fresh failure  
   provider 失败时不得发布部分状态，artifact 标记为失败或 invalid。

5. Duplicate、unconsumed、serialization mismatch  
   不得被吞掉或错误归类。

6. Multi-source integration  
   至少完成 provider-free/mock 的二源完整构建，而不是只调用 store 层。

运行：

- 新增 targeted tests；
- V6/V6.1 相关全量测试；
- 项目完整测试套件；
- import/compile 检查；
- diff hygiene；
- 可用时运行类型和静态检查。

不得只报告“35 tests passed”。必须列明新增测试是否真正经过完整 construction path。

### Phase 0 闸门

只有在以下条件全部成立时，才能写入新的 append-only 决策：

`V6_IDENTITY_INTEGRATION_QUALIFIED`

- exact、missing、mismatch 的 accounting 一致；
- fresh fallback 不被标记为 replay；
- 端到端 mismatch 路径成功；
- full mock 二源构建成功；
- 无发布顺序或写入隔离回归；
- 全套测试通过；
- artifact 可从 frozen manifest 重现。

否则状态必须为：

`V6_IDENTITY_FIX_INCOMPLETE`

并停止一切正式 provider/V7 实验，继续在 Phase 0 内一次只修复一个可证伪问题。

历史 `V6_IDENTITY_QUALIFIED` artifact 不得覆盖；使用新的 correction artifact 声明其适用边界。

---

## 四、研究状态与持久化

建立并持续更新：

- `V7_AUTORESEARCH_STATE.json`
- `V7_AUTORESEARCH_LEDGER.jsonl`
- `V7_RUN_REGISTRY.jsonl`
- `V7_METHOD_IDENTITY.json`
- 每阶段独立 preregistration、result、decision、manifest

状态文件至少包含：

```json
{
  "branch": "",
  "head": "",
  "method_identity": "",
  "phase": "",
  "current_hypothesis": "",
  "last_completed_action": "",
  "gate_status": {},
  "authorization": {
    "provider_dev": false,
    "six_source": false,
    "held_out": false,
    "main_experiment": false,
    "topk_maintainer": false,
    "push": false
  },
  "valid_runs": [],
  "invalid_runs": [],
  "known_blockers": [],
  "next_action": ""
}
```

每次提交、运行或状态迁移后立即更新。发生上下文压缩、进程重启或 Agent 接力时，先读取这些文件和最近提交，不得重复已经完成的实验。

---

## 五、标准 Autoresearch 循环

Phase 0 通过后，对每一个研究问题严格执行以下循环：

1. 从当前最主要瓶颈中选择一个可证伪假设。
2. 记录它为何是当前最高价值问题。
3. 在观察新数据之前冻结：
   - 假设；
   - 方法身份；
   - 数据范围；
   - 指标；
   - success/failure/invalid 阈值；
   - 运行预算；
   - 允许的基础设施重试；
   - 下一闸门。
4. 提交 preregistration。
5. 优先添加失败测试或最小诊断。
6. 实现最小改动，不混入第二个架构变化。
7. 先运行 provider-free correctness/differential tests。
8. 只有通过当前闸门，才运行最小规模 development provider 实验。
9. 把所有运行写入 registry，包括 invalid 和失败。
10. 接受或否定假设；输出证据和下一假设。

限制：

- 同时只能有一个 active hypothesis。
- 一个运行中不能同时改变架构、prompt、批处理和 scheduler。
- 连续三个架构假设未达到预注册闸门后，必须进入 synthesis checkpoint：
  - 汇总失败原因；
  - 判断是假设错误、实现失败、机会不足还是基础设施失败；
  - 决定形成负面结果，或请求用户批准新的研究身份；
  - 禁止无休止地换参数。

---

## 六、Phase 1：替换无效的 V7 二源观测

旧二源 run 永久保留为 `PROBE_INVALID`，不得覆盖。

创建新的 run ID、namespace、manifest 和预注册。新 run 只能在 Phase 0 通过后启动。

### 基础设施失败策略

必须在运行前冻结 transient failure 分类，例如：

- truncated JSON；
- provider timeout；
- rate limit；
- transport disconnect；
- schema extraction failure。

对于基础设施 invalid：

- 每次 attempt 都保留；
- replacement run 使用新 run ID；
- replacement 不是独立科学样本；
- 最多允许两次预注册 replacement；
- 不得因为结果不理想而 replacement；
- 超过上限则输出 `BLOCKED_PROVIDER_INSTABILITY`。

### 有效二源 probe 的条件

- 两个 source 均完整；
- 所有 stage 均有可验证输出；
- pair universe 非空或被结构性证明为空；
- observer 不改变被观测方法；
- 无 held-out；
- 无结果驱动阈值变更；
- provenance、reference time、order、serialization 和 source binding 完整。

有效 probe 必须测量：

- eligible pair 数量和比例；
- exact-binding rate；
- mismatch/missing/fresh-fallback rate；
- affected work；
- 可复用与必须重算的阶段；
- 关键路径上的可移除工作；
- provider 和数据库时间；
- correctness/semantic mismatch；
- quality 风险。

如果完整 pair 为零，只能输出 `NO_OBSERVED_OPPORTUNITY_IN_THIS_PROBE`；除非有完整结构性证明，否则不得称为“V7 不可能”。

---

## 七、Phase 2：六源 development observer

只有有效二源 probe 达到预注册的机会闸门后，才能设置：

`authorization.six_source=true`

六源数据必须来自既有 development split。开始前：

- 从仓库读取已冻结的 dev/held-out 划分；
- 核对 ID 和哈希；
- 如果没有可验证的冻结划分，先建立并提交划分，再访问内容；
- 不得把未知样本自行当作 development；
- held-out 继续保持未访问。

六源 observer 的目标不是证明 speedup，而是判断：

1. V7 是否有足够的重复/局部更新机会；
2. 哪个阶段支配 wall-clock；
3. 哪些依赖能被可靠证明未受影响；
4. UNKNOWN fallback 是否过高；
5. 可省工作是否位于关键路径；
6. 新维护开销是否小于被避免的工作。

在查看六源结果前冻结机会阈值。若仓库没有现成阈值，应先用系统成本模型和功效分析设计阈值并提交，不得观察结果后补阈值。

六源结果只能产生以下决策：

- `V7_ARCHITECTURE_AUTHORIZED`
- `V7_OPPORTUNITY_INSUFFICIENT`
- `V7_OBSERVER_INVALID`
- `V7_EVIDENCE_INCONCLUSIVE`

---

## 八、Phase 3：V7 方法身份与架构

V7 必须区分两个正式条件：

### 1. `V7_FRESH`

- 与 V7 使用完全相同的 prompt、schema、serialization、batching、模型和 provider 配置；
- 每次从空状态执行全量计算；
- 是测量“纯增量收益”的直接对照；
- 不能用旧 V6 headline 替代。

### 2. `V7_INCREMENTAL`

最小架构应包括：

1. Stable source-local IR  
   每个 source 有稳定、可哈希、可比较的中间表示。

2. Stateful materialized views  
   明确记录 view 的输入、版本、依赖、provenance 和更新状态。

3. Dependency and affectedness certificate  
   只有能够证明不受当前 source 变化影响的工作才允许复用。

4. Exact full-recompute oracle  
   任意增量结果都能与同身份的 V7_FRESH 结果进行规范化差分。

5. Conservative fallback  
   依赖为 `UNKNOWN`、证据缺失或绑定不一致时执行 fresh recomputation。

6. Ordered publication  
   未完成验证前不得将部分状态发布到共享可见 namespace。

7. Crash recovery and idempotence  
   重试不能产生重复边、部分提交或不可追踪状态。

8. Complete accounting  
   区分：
   - reused；
   - invalidated；
   - recomputed；
   - fresh fallback；
   - provider transport；
   - DB read/write；
   - proof/maintenance overhead。

### 架构边界

DMSV、dominant-request 优化和 Top-K maintainer 不得自动并入 V7。

只有在基本 view correctness 已通过，且 profiling 证明相应阶段位于关键路径并达到预注册占比时，才能新建独立方法身份，例如：

`V7_INCREMENTAL_TOPK_V1`

这属于新 treatment，必须重新预注册和验证。不得偷偷把它写入已经冻结的 V7 identity。

---

## 九、V7 正确性和质量闸门

在任何性能主张之前，V7_INCREMENTAL 必须通过：

1. 每处理一个 source 后，与 V7_FRESH 做 canonical state differential。
2. 最终图状态差分。
3. 节点、边、属性、provenance、时间和 source membership 差分。
4. 删除、修改、重排、重复 source 的测试。
5. mismatch、missing、provider failure 和 crash recovery 测试。
6. UNKNOWN 路径强制 fresh 的测试。
7. 并发及 ordered publication 测试。
8. 在固定 seed/确定性可控范围内的重复运行。
9. 下游 LongMemEval 或仓库既定质量评测。
10. 用置信区间进行非劣判断，而不是只看少量示例问题。

任何无法解释的 canonical mismatch 都阻止性能实验。

如果 provider 本身非确定，必须：

- 分离 provider variance 和架构差异；
- 尽可能使用相同已捕获输入进行 provider-free comparison；
- 对 live quality 使用配对设计；
- 明确哪些字段要求 exact equality，哪些字段采用 semantic equivalence；
- 在查看 held-out 前冻结 equivalence 判定。

---

## 十、冻结前的开发实验

development 阶段允许：

- 修复 correctness bug；
- 选择架构；
- 测量机会；
- 估计方差；
- 完成功效分析；
- 设置主要指标和阈值；
- 运行有限的消融实验。

禁止：

- 在 development 中选择一个指标、到 held-out 后改用另一个指标；
- 反复调整直到超过 speedup 阈值；
- 把 provider invalid attempt 排除后假装从未发生；
- 把失败运行作为“额外独立样本”。

当以下内容全部冻结后，才能进入主实验：

- V7 方法规范；
- 代码提交；
- prompt/schema/config 哈希；
- baseline 身份；
- dev/held-out split；
- 环境和硬件；
- primary/secondary metrics；
- correctness 和 quality 闸门；
- 统计分析计划；
- failure/invalid 规则；
- 运行顺序；
- 资源预算；
- main experiment manifest。

此时生成：

`V7_MAIN_EXPERIMENT_PREREGISTRATION.json`

并单独提交。

---

## 十一、主实验设计

在冻结 manifest 前，从仓库核对已有主实验规划。若既有规划规定 4 个 development histories 和 8 个 held-out histories，则沿用并验证哈希；不得自行更换。

### 正式条件

至少包括：

- `B0_NATIVE_SERIAL`：主要外部 baseline；
- `V7_FRESH`：V7 同身份全量重算对照；
- `V7_INCREMENTAL`：主要 treatment；
- 修复后的 V6 Core：只有重新 qualification 后才可作为辅助历史对照；
- `B1_RELAXED_ORDER`：仅作为补充 upper bound，不作为 headline。

旧 V6 headline 不得复用。

### 资源公平

所有正式条件必须尽可能保持：

- 相同模型与版本；
- 相同 provider routing；
- 相同 prompt/schema；
- 相同输入和 source order；
- 相同硬件；
- 相同并发上限；
- 相同数据库配置；
- 相同冷/热启动定义；
- 相同失败和超时规则。

若 baseline 与 treatment 的语义或资源不同，必须显式说明，不能直接计算 headline speedup。

### 运行设计

- 使用 paired same-history comparison；
- 顺序随机化或 counterbalance；
- 记录冷启动和稳态；
- 预先定义 warm-up；
- 每个重复运行的身份独立；
- provider replacement 不算新的科学 replicate；
- 任何 campaign 级 bug 都使相应 campaign invalid。

### Primary outcomes

至少报告：

- end-to-end wall-clock makespan；
- `B0_NATIVE_SERIAL / V7_INCREMENTAL` speedup；
- `V7_FRESH / V7_INCREMENTAL` 纯增量 speedup；
- 关键路径时间；
- provider calls、tokens 和 transport attempts；
- DB read/write；
- recomputed、reused、invalidated 和 fallback work；
- proof/maintenance overhead；
- correctness pass rate；
- quality non-inferiority；
- peak memory 和失败率。

### 统计分析

- 使用配对估计；
- 报告每个 history 的结果；
- 报告效应量及置信区间；
- 在 held-out 前完成功效分析；
- 明确 primary 与 secondary comparisons；
- 对多重比较进行预注册处理；
- 不把 source、stage 或 retry 错当成独立样本；
- 同时报告中位数、尾部行为和异常值原因；
- 不只报告最佳运行。

---

## 十二、held-out 一次性闸门

只有下列条件全部满足时才能设置：

```text
authorization.held_out=true
authorization.main_experiment=true
```

- 方法与代码冻结；
- 完整测试通过；
- development 闸门通过；
- 功效分析完成；
- main preregistration 已提交；
- manifest 哈希已记录；
- namespace 已隔离；
- 运行预算和失败策略已冻结。

访问 held-out 后：

- 不得再修改方法、prompt、schema、阈值或主要统计方法；
- 只能修复明确的基础设施问题；
- 若发现会影响科学结果的代码 bug，整个 campaign 标为 invalid；
- 修复后必须创建新方法/代码身份并重新预注册；
- 不得复用已经查看过的 held-out 结果进行方法选择。

---

## 十三、论文级交付物

无论正面或负面终态，都必须生成：

1. 最终方法规范和身份表。
2. correctness argument，明确已证明与未证明的边界。
3. 完整 preregistration、decision 和 artifact manifest。
4. 所有 valid、invalid、failed run registry。
5. 可复现运行脚本和环境锁定信息。
6. 主结果表、每历史结果表和置信区间。
7. correctness/quality 结果。
8. 成本与关键路径分解。
9. 主要消融实验。
10. 与 cache、memoization、incremental view maintenance、OCC、DMSV 等相关工作的清晰区别。
11. 对旧 V6、V6.1、B1R2 结果不可迁移性的说明。
12. threats to validity。
13. limitations 和 negative findings。
14. 可直接用于论文的：
    - method section outline；
    - experiment section outline；
    - claim-evidence table；
    - artifact/reproducibility checklist；
    - reviewer objection checklist。

Headline claim 必须严格对应证据。例如：

- 有充分证据时：V7 在保持质量非劣和状态等价的条件下减少增量构建关键路径。
- 只有开发集证据时：只能称为 development observation。
- 功效不足时：称为 inconclusive。
- 没有观察到机会时：限定在当前 workload 和方法身份。
- 不得写“证明适用于所有图构建任务”。

---

## 十四、提交、推送与外部操作权限

你可以：

- 在专用本地分支编辑代码和 artifact；
- 运行测试、静态检查和本地 mock；
- 在闸门允许后运行 development provider 实验；
- 创建隔离 namespace；
- 进行非破坏性的环境诊断；
- 按阶段制作本地提交。

你不可以：

- reset、覆盖或删除用户改动；
- 改写旧 sealed artifact；
- 删除失败/invalid 运行；
- 停止或清空共享服务；
- 使用未授权 held-out；
- 未经明确授权推送远端；
- 创建或发布论文/PR；
- 把 prompt 文件混入科学结果提交；
- 在缺少证据时扩大结论。

若需要上述新权限，保存当前状态后输出 `BLOCKED_EXTERNAL` 并说明所需权限。

---

## 十五、沟通要求

不要在每个小动作后停下来等待用户。只在以下情况汇报：

- 完成一个阶段；
- 闸门状态改变；
- 出现会改变研究路线的新证据；
- 需要权限或外部资源；
- 达到终态。

每次阶段报告必须包含：

```text
CURRENT_STATE
BRANCH_AND_HEAD
METHOD_IDENTITY
COMPLETED_ACTIONS
TEST_RESULTS
VALID_RUNS
INVALID_OR_FAILED_RUNS
GATE_DECISION
CLAIM_BOUNDARY
KNOWN_BLOCKERS
NEXT_AUTHORIZED_ACTION
```

不要只给自然语言结论；同时更新机器可读状态文件。

---

## 十六、开始执行

当前初始状态应设为：

```text
V6_FIX_INTEGRATION_AUDIT=REQUIRED
V7_TWO_SOURCE_PROBE=INVALID_REPLACEMENT_PENDING
V7_SIX_SOURCE_AUTHORIZED=false
V7_ARCHITECTURE_AUTHORIZED=false
TOPK_MAINTAINER_AUTHORIZED=false
HELD_OUT_ACCESSED=false
MAIN_EXPERIMENT_AUTHORIZED=false
```

现在开始：

1. 审计真实分支、HEAD、工作区和历史 artifact。
2. 不修改历史结果。
3. 为 Phase 0 编写 append-only 预注册。
4. 复现完整 construction 层的 mismatch/fresh accounting 问题。
5. 一次只修复这一项问题。
6. 完成端到端证明和全套测试。
7. 只有 Phase 0 通过，才创建新的二源 replacement probe。
8. 此后严格按照上述 autoresearch 闸门持续推进，直到达到三个合法终态之一。