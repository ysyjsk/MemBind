请基于MemBind当前本地分支，执行一次严格限时、限范围的：

# Frozen V6 Identity Fix → Immediate V7 2-Source Probe

本轮不是新的V6研究阶段，也不是继续扩展DMSV methodology。

唯一目标是：

```text
确认旧headline V6是否实际删除过非空previous context
→ 恢复Frozen V6的same-logical-work实现身份
→ 做最小qualification
→ 同一轮立即启动并尽量完成2-source V7 semantic observer
```

不得在完成V6 identity fix后继续增加新Gate、theorem、closure或审计阶段。

---

# 0. 最高优先级执行规则

本轮必须遵循：

```text
一次最小V6 baseline identity修复
→ 立即恢复V7实验
```

禁止将本轮扩展成：

```text
V6 semantic audit
→ output audit
→ graph audit
→ quality campaign
→ B1R3
→ B1R4
→ 新theorem
→ 新Gate体系
```

本轮最多完成：

```text
1. Existing V6 artifact dynamic-effect scan
2. Provider-free previous-window equivalence
3. Frozen V6 identity fix
4. Minimal corrected-V6 qualification
5. One 2-source V7 observer
6. Seal and stop
```

总wall-clock上限：

```text
5 hours
```

到4小时30分钟时禁止启动任何新实验，剩余30分钟只允许封存artifact、写报告和停止本轮任务。

---

# 1. 输入身份

预期本地历史：

```text
PUBLIC_BASE_COMMIT=
37871aae8193d994a1642605e3a705712dd786e1

B1R2_PREREG_COMMIT=
5031f10dcd37df1f6f199ee1125e1fae1760d580

B1R2_RESULT_COMMIT=
a1aee32cc76e6c60a39c3aa28451a3241a6f9e63

expected_branch=
dmsv-b1r2-structural-closure
```

首先检查：

```bash
git rev-parse HEAD
git status --short
git log --oneline --decorate -5
git merge-base --is-ancestor 37871aae8193d994a1642605e3a705712dd786e1 HEAD
```

必须保留用户未跟踪的：

```text
prompt.md
```

不得修改、删除、覆盖、提交或stash该文件。

不得：

```text
git reset --hard
git checkout -- user files
git clean
push
```

若HEAD不是`a1aee32...`且存在无法归属的新提交，停止并输出：

```text
FINAL_STATE=BLOCKED_INPUT_IDENTITY
```

---

# 2. 冻结科学身份

Frozen V6方法身份继续是：

```text
method_identity=v6-membind-core-v1
```

允许的变化只有：

```text
dependency-aware PREPARE/NATIVE overlap
dependency-aware admission
exact certified replay of the same logical extraction request
bounded speculative frontier
source-order authoritative publication
```

明确禁止：

```text
删除Native previous context
修改prompt语义
减少Native logical work
用不同request的相同output冒充exact replay
把work reduction算成concurrency收益
```

当前公开实现事实已经确定：

```text
strip_certified_previous_context=ON_CORE_PATH
```

历史关系已经确定：

```text
984ea2d:
early V6.1引入strip

8fba929:
冻结v6-membind-core-v1时错误继承该transform
```

本轮不再重复证明静态调用链，不再生成新的call-path theorem。

本轮只回答动态问题：

```text
过去headline V6正式artifact中，
previous_context_chars_removed是否大于0？
```

方法身份不重新定义。

如果修改实现，只增加：

```text
implementation_revision=context-integrity-fix-v1
```

不得静默把旧commit与新实现称为字节相同版本。

---

# 3. 最小Stage A：冻结三分支决策

在读取旧artifact统计结果前，创建一个紧凑preregistration：

```text
saturated_fixed_work_baseline_v1_3/v6_core_identity_fix/
V6_CORE_IDENTITY_FIX_PREREGISTRATION.json
```

只冻结以下内容：

```text
input commit
eligible headline artifact definition
NOOP/NONEMPTY_REMOVAL/MISSING分类
previous-window equality字段
A/B/C修复分支
qualification条件
2-source probe字段
forbidden actions
5-hour deadline
```

不得新增claim taxonomy、E1–E13扩展、理论Gate或论文related-work章节。

允许的artifact分类：

```text
NOOP
NONEMPTY_REMOVAL
MISSING
```

允许的修复分支：

```text
BRANCH_A_NOOP
BRANCH_B_RECONSTRUCTABLE
BRANCH_C_NOT_RECONSTRUCTABLE
BRANCH_MISSING_OLD_EFFECT
```

preregistration写入并计算SHA-256后，再扫描旧artifact。

可以创建一个本地prereg commit，但禁止push。若创建，commit message固定为：

```text
preregister frozen V6 context identity fix
```

---

# 4. Step 1：扫描已有headline V6 artifact

只扫描满足以下身份的正式V6 blocks：

```text
method=MEMBIND_CORE
core version=v6-membind-core-v1
construction sealed
expected=submitted=completed
使用headline Frozen V6入口
```

不得把V6.1 autoresearch候选、ablation、V7 observer、失败run混入headline统计。

读取已有：

```text
CERTIFIED_CONTEXT_SELECTION
previous_context_chars_removed
previous_context_block_count
retained_previous_episode_count
prompt_name
region
source_sequence
```

对每个正式block输出：

```text
run_id
history/context
certified_call_count
context_event_count
calls_with_nonempty_removal
total_removed_chars
max_removed_chars
missing_event_count
classification
artifact hash
```

分类规则：

## NOOP

只有同时满足：

```text
所有预期certified extraction call都有context event
AND
所有previous_context_chars_removed=0
AND
所有previous_context_block_count对应空body或零删除
```

才能分类为：

```text
NOOP
```

事件缺失不能算NOOP。

## NONEMPTY_REMOVAL

只要存在：

```text
previous_context_chars_removed > 0
```

就分类为：

```text
NONEMPTY_REMOVAL
```

## MISSING

如果正式block缺少决定性字段，分类为：

```text
MISSING
```

禁止为了确认旧run而重跑旧headline实验。

artifact扫描最多允许30分钟。超过30分钟仍找不到完整证据，直接进入：

```text
BRANCH_MISSING_OLD_EFFECT
```

不得继续全盘搜索半天。

输出合并到：

```text
V6_CORE_IDENTITY_FIX_DECISION.json
```

不要为每个小结论创建一个新文件。

---

# 5. Step 2：Provider-free previous-window equivalence

此步骤不调用LLM provider，不执行Neo4j写入，不重新跑B0/V6 live。

唯一问题：

```text
PREPARE阶段的_native_previous_window(...)
能否构造与Native retrieve_episodes(...)相同的
prompt-visible previous window？
```

至少比较：

```text
membership
order
content projection
valid_at/reference_time
group_id/source filter
last_n
tie behavior
prompt serialization
```

必须比较实际Graphiti 0.29.3调用约定，不得只比较Python对象数量。

按certified callsite分别判断：

```text
extract_nodes.extract_message
extract_nodes.extract_text
extract_nodes.extract_json
extract_edges.edge
```

输出状态：

```text
SAME_LOGICAL_REQUEST_PROVEN
NOT_EQUIVALENT
UNKNOWN_MISSING_BINDING
NOT_INVOKED_IN_WORKLOAD
```

注意：

```text
相同response
≠ 相同request

相同episode IDs
≠ 相同prompt bytes

两次独立LLM输出不同
≠ request不等价
```

request identity比较必须在provider调用前完成。

如果现有冻结workload和artifact足以重建，不得启动新provider实验。

---

# 6. Step 3：立即选择并实施A/B/C分支

## Branch A：旧正式V6全部NOOP

条件：

```text
所有eligible headline blocks=NOOP
```

操作：

1. 旧V6正式结果继续有效；
2. 从Frozen Core路径移除`strip_certified_previous_context`；
3. historical V6.1/ablation路径可保留helper，但必须与Core隔离；
4. Core增加硬合同：

```text
certified_message_transform=None
same_logical_request_required=true
context_removal_allowed=false
```

5. 增加：

```text
implementation_revision=context-integrity-fix-v1
```

6. 不重跑完整V6 campaign；
7. 只做最小request-identity qualification。

旧artifact状态：

```text
OLD_V6_HEADLINE_STATUS=REUSABLE_NOOP
```

## Branch B：存在NONEMPTY_REMOVAL，但previous window可重建

条件：

```text
存在NONEMPTY_REMOVAL
AND
SAME_LOGICAL_REQUEST_PROVEN
```

操作：

1. 从Core路径移除strip；
2. PREPARE使用Native-equivalent previous window；
3. capture与NATIVE replay比较原始logical request；
4. request exact才允许consume prepared response；
5. mismatch必须fresh执行Native call；
6. 保留提前extraction能力；
7. 旧受影响artifact降级为：

```text
V6_CONTEXT_ELIDED_DIAGNOSTIC
```

8. 只重跑受影响的最小正式V6 baseline block/qualification，不重跑B0。

旧artifact状态：

```text
OLD_V6_HEADLINE_STATUS=INVALID_FOR_TIMING_ONLY_HEADLINE
```

## Branch C：存在NONEMPTY_REMOVAL且previous window不可重建

条件：

```text
存在NONEMPTY_REMOVAL
AND
previous-window equivalence=NOT_EQUIVALENT或UNKNOWN
```

操作：

1. 从Frozen Core路径移除strip；
2. 将无法证明same-logical-request的callsite移出Core-specific certified set；
3. 对应call在NATIVE阶段fresh执行；
4. 不修改全局历史`CERTIFIED_CALLSITES`来破坏旧replay；
5. 新建Core-specific集合，例如：

```text
MEMBIND_CORE_CERTIFIED_CALLSITES
```

6. 只包含被证明dependency-free的callsite；
7. mismatch或unknown统一fresh；
8. 重跑一个最小corrected-V6 baseline/qualification；
9. 不重跑B0。

旧artifact状态：

```text
OLD_V6_HEADLINE_STATUS=INVALID_FOR_TIMING_ONLY_HEADLINE
```

## Branch MISSING：旧artifact缺少动态字段

条件：

```text
旧headline effect=MISSING
```

操作：

1. 旧V6结果不能继续作为timing-only正式baseline；
2. 不再追查旧run；
3. 根据provider-free previous-window结果选择Branch B式修复或Branch C式降级；
4. 修复后跑一个最小corrected-V6 baseline/qualification。

状态：

```text
OLD_V6_HEADLINE_STATUS=UNKNOWN_NOT_REUSABLE
```

禁止出现：

```text
既然代码strip了，就把V6论文重定义成context-elided算法
```

---

# 7. 实现约束

不得删除旧helper或旧测试，以免破坏历史复现。

正确结构应是：

```text
historical V6.1/extension path
→ 可以显式启用旧strip policy
→ artifact identity必须标记context-elided

Frozen MemBind-Core path
→ transform=None
→ same logical request required
→ mismatch/unknown fresh
```

`work_reduction_extensions_enabled=False`必须由运行时断言支持，不能只存在metadata中。

至少增加以下测试：

1. headline Core入口不能安装strip；
2. Core出现`previous_context_chars_removed>0`立即失败；
3. historical V6.1 path仍可复现旧行为；
4. PREPARE/NATIVE request exact时只进行一次物理provider调用；
5. request mismatch时不consume transcript，转fresh；
6. unknown previous-window binding时转fresh；
7. ordered authoritative publication保持；
8. replay不进行pre-publication DB write；
9. Core identity包含implementation revision；
   10.旧sealed artifact字节不变。

不要求完整repository suite，除非修改范围导致相关回归无法局部覆盖。

---

# 8. Step 4：最小Corrected-V6 Qualification

qualification只验证修复后的Core是否恢复Frozen合同，不扩成新campaign。

必须验证：

```text
CORE_CONTEXT_TRANSFORM=NONE
same-logical-request comparison before replay
exact request → single consume
mismatch/unknown → fresh
no pre-publication DB write
source-order durable publication
work-reduction extension disabled
```

若进行2-source或最小prefix live qualification，还需记录：

```text
logical request count
exact replay count
fresh fallback count
provider physical call count
context removed chars=0
durable publication count
makespan（diagnostic only）
```

不要求两次独立LLM推理输出完全相同。

正确性oracle是：

```text
相同canonical logical request
→ single captured response可以exact replay
```

不是：

```text
对同一request独立调用两次LLM
→ response必须相同
```

qualification允许状态：

```text
V6_IDENTITY_QUALIFIED
V6_IDENTITY_FIX_FAILED
V6_IDENTITY_FIX_BLOCKED_ENVIRONMENT
```

只有：

```text
V6_IDENTITY_QUALIFIED
```

才允许进入下一步2-source V7 probe。

如果失败，停止代码扩张，报告具体失败点；不得继续V7。

---

# 9. Step 5：同一轮立即执行2-source V7 semantic observer

只要：

```text
V6_IDENTITY_QUALIFIED
```

就必须继续执行2-source observer。不得因为“报告已经很多”而在V6 qualification后停止。

这不是V7 treatment，不做reuse，不修改authoritative state语义。

选择一个non-held-out development history，冻结：

```text
source 0
source 1
```

运行关系：

```text
corrected Frozen V6 prepares source 1
        ↓
在合法旧state上记录stateful semantic view/request
        ↓
authoritatively publish source 0
        ↓
在新state上对同一个prepared source 1 fresh resolve
        ↓
比较old/new semantic view和canonical request
        ↓
只记录机会，不执行reuse
```

必须保持：

```text
same PreparedArtifact
same source
same model/config/template/schema/index epoch
ordered publication
no speculative publication
no held-out
```

2-source observer至少记录：

```text
base_view_ready_before_authoritative_need
old/new state version
previous_episodes membership/order exact
previous prompt projection exact
Node candidate membership exact
Node candidate order exact
candidate payload exact
unresolved batch shape exact
canonical dedupe_nodes.nodes request exact
request changed fields
dominant Node LLM service time
validation time
fresh recomputation time
potentially preservable critical-path time
visible repair time
output reconvergence（diagnostic only）
ordered continuation status
```

必须使用实际critical-path accounting：

```text
ReusableHiddenCP
+
ReconvergenceSavedDescendantCP
-
VisibleRepairCP
-
ValidationCost
```

不得把：

```text
request变化后重新执行了Node LLM
但输出最后相同
```

记成Node LLM saved work。

---

# 10. 2-source结果解释

2-source只承担两个任务：

```text
验证observer instrumentation
发现机制是否存在非零信号
```

它不能承担正式机会率或最终NULL结论。

允许状态：

## PROBE_INVALID

```text
binding不完整
mixed snapshot
epoch不一致
same PreparedArtifact未满足
ordered publication失败
```

输出：

```text
V7_TWO_SOURCE_PROBE=INVALID
V7_6_SOURCE_AUTHORIZED=false
```

只修instrumentation bug，不增加methodology Gate。

## PROBE_VALID_POSITIVE_SIGNAL

满足：

```text
observer绑定完整
AND
存在request exact、局部affectedness或正的potentially preservable CP
```

输出：

```text
V7_TWO_SOURCE_PROBE=VALID_POSITIVE_SIGNAL
V7_6_SOURCE_AUTHORIZED=true
```

下一轮直接进入6-source characterization。

## PROBE_VALID_ZERO_SIGNAL

```text
observer有效
但该单pair没有保留dominant CP
```

输出：

```text
V7_TWO_SOURCE_PROBE=VALID_ZERO_SIGNAL_SINGLE_PAIR
```

不得根据一个pair输出DMSV NULL。

若没有代码级结构证明机会恒为零，则：

```text
V7_6_SOURCE_AUTHORIZED=true
```

由6-source决定分布性机会。

只有本轮同时得到严格结构反证，例如：

```text
所有合法transition下dominant request必然变化
AND
无Native localization
AND
base view不可能及时存在
```

才允许：

```text
V7_6_SOURCE_AUTHORIZED=false
```

不得用单个2-source实验替代这种证明。

本轮不得自动运行6-source。

---

# 11. 停止审计规则

本轮最多新增：

```text
1个workplan append-only section
1个preregistration JSON
1个V6 closure/fix decision JSON
1个2-source observer JSONL/JSON
1个合并report
1个ledger
必要测试
```

不要把每个中间判断拆成独立报告。

禁止新增：

```text
B1R3/B1R4 taxonomy
E1-E13扩展
新operator selection Gate
新经济公式
新theorem document
Top-K maintainer
batch splitting
summary/edge treatment
scheduler/admission search
6-source/full development campaign
held-out evaluation
论文related-work扩写
```

一旦完成：

```text
V6 dynamic effect
V6 fix branch
V6 qualification
2-source probe
```

就必须停止。

---

# 12. Workplan更新方式

只在`workplan_v7.md`追加一个简短section：

```text
Frozen V6 Identity Fix and Experiment Resumption
```

说明：

1. intended method identity仍为timing-only；
2. public implementation继承了早期strip；
3. 本轮按A/B/C修复；
4. pre-fix DMSV semantic root保持历史记录；
5. post-fix DMSV observer使用corrected Frozen V6；
6. 完成identity qualification后立即恢复V7实验；
7. 不再新增methodology closure。

不得删除或重写：

```text
旧V6证据
旧V7 NULL
B1R2 BLOCKED
失败attempt
historical context-elided artifacts
```

---

# 13. Git与artifact纪律

建议在当前本地分支继续工作，或创建：

```text
v6-core-context-integrity-fix
```

允许本地commit，禁止push。

至少形成：

```text
PREREG_COMMIT
RESULT_COMMIT
```

旧artifact必须字节级不变。

如果启动live probe，使用独立output目录和run ID；不得覆盖旧run。

本轮只停止本轮启动的进程，不停止共享LLM、Embedding或Neo4j服务。

若live probe在5小时上限前无法完成：

1. 不启动新的实验；
2. 封存当前run ID、PID/session、output root和进度；
3. 输出：

```text
V7_TWO_SOURCE_PROBE=STARTED_NOT_COMPLETED
```

4. 不将不完整结果纳入机会结论。

---

# 14. 最终输出格式

最终回复必须先输出：

```text
INPUT_COMMIT=
PREREG_COMMIT=
RESULT_COMMIT=
FINAL_STATE=

V6_METHOD_IDENTITY=v6-membind-core-v1
V6_IMPLEMENTATION_REVISION=
OLD_V6_DYNAMIC_EFFECT=NOOP|NONEMPTY_REMOVAL|MISSING
OLD_V6_HEADLINE_STATUS=
PREVIOUS_WINDOW_EQUIVALENCE=
FIX_BRANCH=
V6_IDENTITY_QUALIFICATION=

V7_TWO_SOURCE_PROBE=
V7_PROBE_RUN_ID=
V7_6_SOURCE_AUTHORIZED=

B0_RERUN=false
B1_ROLE=RELAXED_ORDER_UPPER_BOUND
HELD_OUT_ACCESSED=false
TOPK_MAINTAINER_IMPLEMENTED=false
V7_TREATMENT_EXECUTED=false
```

随后简明报告：

1. 扫描了哪些正式V6 artifacts；
2. 是否实际删除过非空context；
3. previous window能否Native-equivalent重建；
4. 选择A/B/C哪条修复；
5. 修改了哪些production files；
6. 哪些旧V6结果仍可使用；
7. corrected V6 qualification结果；
8. 2-source observer是否启动/完成；
9. previous window、Node request和dominant CP的实际观测；
10. 下一步是否直接进入6-source。

最终必须明确：

```text
本轮没有重新定义V6，
只修复了实现与Frozen方法合同的偏离。

本轮没有继续增加methodology Gate，
V6 identity闭环后已经恢复V7 empirical execution。
```
