# MemBind V7 增量更新模块工作计划

版本：`v7-incremental-update-v1`  
工作目录：`/data/predator/ly/MemBind`  
前置冻结：`v6-membind-core-v1`（见 `MemBind_V6_1_8B_Autoresearch_Workplan.md`）

## 研究问题

V6 MemBind-Core 已回答第一个模块的问题：在保持 Native 的 state evolution、应执行
工作和 source-order durable publication 不变时，能否把 dependency-free extraction
提前并与 authoritative update 重叠。V7 只研究第二个增量模块：

> 新 source 到达后，能否通过严格的 d=1 state delta、受影响邻域闭包和
> content-addressed extraction artifact，复用闭包外的既有结果，同时得到与完整 Native
> 重算相同的可观测投影？

这是 work-reduction extension，不属于 V6 Core。任何 V7 加速必须单独归因于减少重复
计算，不能回写 V6 的 headline speedup。

## 方法边界

V7 第一阶段只实现 provider-free reference planner：

- `ArtifactKey` 绑定 object、source、schema、model 和 config hash；任一身份变化都禁止复用。
- `affected_closure` 对 changed object 沿依赖边做确定性传递闭包；闭包内对象全部重算，
  闭包外对象才有资格复用。
- `ArtifactRecord` 只有在 artifact 完整、semantic hash 存在且所有 epoch 匹配时才能命中。
- 仅支持单次增量 `d=1`；多 delta、缺失依赖或不确定 epoch 必须 fail closed。
- 规划器不调用 LLM、Embedding、Graphiti 或 Neo4j，也不写入 Native 状态。

实现文件：

```text
saturated_fixed_work_baseline_v1_3/src/
  saturated_fixed_work_baseline_v1_3/membind_v7/incremental_update.py
```

## TDD / Autoresearch 阶段

1. **离线 RED/GREEN**：覆盖 d=1 校验、传递闭包、artifact hash mismatch、incomplete
   artifact、空变化 no-op，以及确定性排序。所有测试 provider-free。
2. **投影对账**：使用 V7 已有 `StateDelta`、`graphiti_observer` 和 reference model，
   对每个 operator 记录 changed/affected/unaffected 集合；先证明闭包外复用不会改变
   observable projection，再讨论任何 live 调用。
3. **缓存失效压力测试**：注入 source/schema/model/config epoch 变化、同一对象不同 hydration
   和跨 target temporal transition。任何 false reuse、false unaffected 或无法解释的
   unknown 都回退到完整重算。
4. **小规模 provider-free replay**：用固定 fixture 测量 direct work、affected work、
   artifact hit ratio 和 reconvergence；不启动 8B 服务，不改变 V6 运行命名空间。
5. **live 前置条件**：只有离线闭包与投影证明通过，才为 V7 创建新的实验 profile/向量索引
   和显式 namespace。V6 的 B0、Core、B1 artifact 永远只读复用为参考，不作为 V7 状态。

## 评估与停止条件

V7 候选必须同时报告：

- full Native recompute 与 incremental plan 的 changed/affected/unaffected 数；
- artifact hit/miss 原因、source/schema/model/config hash；
- exact projection、temporal validity、QA/top-k 和 publication-order 证据；
- provider/embedding/DB work reduction 及其端到端 makespan。

出现以下任一情况即拒绝复用并保留失败证据：闭包不完整、epoch 漂移、artifact 不完整、
projection 不一致、false unaffected、或只减少工作但无法证明语义等价。V7 不搜索 V6 的
lookahead、future cap、lane、route 或 decoding 参数；V6 Core 的冻结代码和主表不因 V7
实验而改写。

## 交付顺序

当前交付为 provider-free planner、单元测试和本计划。下一步先完成 reference projection
对账和缓存失效测试，再决定是否创建 V7 live campaign；在此之前不启动 full5，也不把
V7 的任何结果写入 V6 `campaign_ledger.jsonl`。
