# MemBind V5 工程实施计划

> 计划状态：implementation authority（尚未实现）
>
> 审计基线：MemBind `c4c9577208ab41d1cd148778e0a6eab4daafe6ac`；Graphiti `v0.29.3` / `021d3a57d511f21b10adaf7fa923bd5c1fce5e9d`
>
> 目标协议：`saturated_fixed_work_baseline_v1_3`
>
> 最终稿标识：`V5-WORKPLAN-FINAL-20260821`（完整保留V3方法主体；仅补齐source trace attribution与V5 `T_build`计时闭环）
>
> V5 方法名建议：`V5_VERSIONED_ORACLE_HOIST`

本文件是交给 coding agent 的执行计划，不是已经完成的实验报告。路径、调用边界和判断以本次审计到的上述 revision 为准；开始实现时若 HEAD、Graphiti revision、冻结配置或 formal baseline seal 发生变化，必须先在 P0 重做差异审计，不得沿用过期结论。

---

## 1. Goal

本轮唯一工程目标是：

> 实现 V5，使其作为独立扩展合法接入 `saturated_fixed_work_baseline_v1_3`，完成 provider-free qualification、scripted integration、相关 regression 和 minimal live；随后在不污染既有 baseline 的前提下，将正式 V5 live 正确排队或启动，使现有 protocol/reducer 能产出与 B0/B1 可直接比较的结果。

V5 的具体机制收敛为：

> **Versioned Semantic Maintenance + Certified Oracle-Effect Hoisting + Exact Native Callsite Binding + Frontier-Critical Admission + Ordered Native Publication**

如果论文叙事需要保持四个组成项，则将最后两项合并为：

> **Critical-Frontier Scheduling = Frontier-Critical Admission + Ordered Native Publication**

这里的关键不是手写一个“并行抽取、串行构图”的 Graphiti 替代品，而是：

1. 从固定源码证明哪些 external-oracle effects 只依赖 source/config/source-derived predecessor context；
2. 在正式计时区间内提前执行这些 oracle effects 并保存精确 logical transcript；
3. 等待前驱 memory version durable；
4. 仍然调用未经改写的原生 `Graphiti.add_episode()`；
5. 仅在被认证的原生 LLM callsite 精确重放已准备响应；
6. 让 Graphiti 自己完成解析、去重、属性生成、持久化和原生内部并发；
7. 对有限的共享 LLM admission capacity 为当前 native frontier 保留容量，避免 future preparation 阻塞 memory visibility；
8. 只按 source sequence 推进 durable frontier。

移动单位必须表述为 **oracle effect**，而不是整个 Python operator。preparation 与 native continuation 都会运行原生 prompt 构造/解析代码；V5 有意接受少量本地重复 work，换取原生 stateful suffix 完全不被重写。论文标题建议优先使用：

> **MemBind: Certified Oracle Hoisting for Versioned Memory Maintenance**

本轮不做 K/lookahead/worker/scheduler sweep，不引入新 benchmark、新 QA framework、第二 memory backend 或大规模 ablation。完整 live 结果与论文级实验矩阵留到后续轮次；本轮到 `QUEUED` 或稳定 `RUNNING` 即结束。

---

## 2. Current Repository Ground Truth

### 2.1 当前 source of truth

| 能力 | 当前真实入口 / source of truth | 审计结论 |
|---|---|---|
| v1.3 formal baseline CLI | `saturated_fixed_work_baseline_v1_3/scripts/run_formal_baseline.py` | 当前正式运行入口；不要把旧 `simple_campaign.py` 当作 4-history formal runner |
| formal orchestration | `saturated_fixed_work_baseline_v1_3/src/saturated_fixed_work_baseline_v1_3/formal_baseline.py::run_formal_baseline_async` | 每个 history 依次 B0、B1，再做 read-only QA/reduction/seal |
| B0/B1 execution policy | `saturated_fixed_work_baseline_v1_2/src/saturated_fixed_work_baseline_v1_2/schedules.py` | B0 逐 episode `await add_episode()`；B1 对全部 episode `create_task` + `gather`；不改变 Graphiti 方法 |
| native live block | `saturated_fixed_work_baseline_v1_2/src/saturated_fixed_work_baseline_v1_2/live_block.py` | lifecycle、fresh namespace、timing、artifact、canonical export 的稳定路径 |
| native Graphiti adapter | `saturated_fixed_work_baseline_v1_2/src/saturated_fixed_work_baseline_v1_2/graphiti_adapter.py` | `build_graphiti_kwargs(...)` 后调用原生 `graphiti.add_episode(...)` |
| v1.3 dependency composition | `saturated_fixed_work_baseline_v1_3/src/saturated_fixed_work_baseline_v1_3/live_dependencies.py` | 复用 v1.2 runtime，并安装 v1.3 validation/canonical dependencies |
| lifecycle contract | `saturated_fixed_work_baseline_v1_3/BLOCK_LIFECYCLE_CONTRACT.md` 与 `.../block_lifecycle.py` | fresh namespace → backend prepared → service ready → warmup → idle → formal start → construction/durable → validation |
| frozen backend/client/resource | `saturated_fixed_work_baseline_v1_3/configs/frozen_backend_v1_3.json`、`frozen_client_v1_3.json`、`resource_policy.json` | V5 必须继承，不得静默变更 provider、model、endpoint、resource condition |
| workload | v1.2 `dataset.py` 及 v1.3 formal matrix | workload 已冻结；不得改 source、顺序、token accounting 或 QA questions |
| instrumentation | `membind-validation/src/native_characterization_instrumentation.py`，v1.2 `instrumentation.py` | 已覆盖 LLM logical call、低层 transport、embedding、driver 和原生 Graphiti aliases；V5 必须在其外层绑定 |
| append-only artifacts | v1.2 `AttemptStore` 及 v1.3 `_SimpleAttemptStore` | O_EXCL/fsync/hash chain/seal 已验证；继续复用，失败 attempt 不覆盖 |
| canonical correctness | v1.2 `canonical_diff.py` 与 v1.3 canonical exporter | 比较 namespace-independent canonical projection；不得改 correctness definition |
| production QA | v1.2 `production_qa.py` | 当前默认只接受 B0/B1；V5 需要最小、向后兼容的 `expected_methods` 泛化 |
| v1.3 reducer | `formal_baseline.py::reduce_baseline_outputs` | 保留 B0/B1 baseline 结果不变；V5 extension reducer 读取其 sealed raw rows 后合并展示 |
| qualification/V3.1 extension | v1.3 `simple_campaign.py`、`membind_adapter.py` | 是历史 qualification/extension，不是当前 formal baseline 主入口，也不是 V5 runtime 基础 |
| V5 历史诊断 | v1.3 `membind_v5/{semantic_fingerprint,first_divergence,offline_analyzer}.py` 及 artifacts | 作为失败证据与诊断工具保留；不能把 passive fingerprint 当 V5 机制 |

### 2.2 当前 formal workload 与运行语义

当前 4 个 frozen histories 共 188 个 sources：

| history | sources | frozen tokens |
|---|---:|---:|
| `07741c45` | 49 | 104014 |
| `b6019101` | 49 | 106914 |
| `6071bd76` | 46 | 105786 |
| `a2f3aa27` | 44 | 105977 |

formal baseline 每个 history 产生 B0/B1 两个 block，共 8 个 block row，并产生 QA、reducer output、`qualification/baseline_results.json` 与 `formal_run_seal.json`。V5 不应把自己加入原 baseline 的 `FORMAL_METHODS`，而应作为依赖一个已验证 baseline seal 的 append-only extension campaign，每个 history 增加一个 V5 block。

计时边界必须保持一致：普通服务启动、generic warmup 和 namespace 准备可以在 `FORMAL_START` 之前；但 V5 的 semantic preparation 是方法工作，必须在 `FORMAL_START` 或之后开始，不能藏进 warmup。

V5的block级build makespan唯一合法定义为：

\[
T_{build}^{V5}=t(\text{final PUBLICATION\_DURABLE})-t(\text{FORMAL\_START})
\]

因此timer必须在创建/admit任何V5 preparation task之前启动，并在最后一个source完成原生durable publication后停止。实现上必须复用B0/B1现有`execute_instrumented_block`口径：`timer_start_ns=t0_ns`，`timer_stop_ns=t_durable_complete_ns`；raw journal中的最后一个`PUBLICATION_DURABLE.monotonic_ns`应不晚于stop，二者之间不得存在任何额外semantic/provider/DB work，只允许共享runner已有的返回与记账开销并记录该delta。这样既满足上述语义边界，又不为V5另造一个比sealed baseline更短的clock boundary。该区间包含preparation、admission/provider queue、frontier wait、replay/capture/local parse开销与native stateful suffix；不包含`FORMAL_START`前的service startup/warmup/namespace准备，也不包含最终durable publication后的canonical validation、QA和seal工作。禁止仅累加native`add_episode()`spans、仅统计native suffix或从首个provider request开始计时。

### 2.3 当前 live/backend 事实

冻结配置使用当前 v1.3 记录的 Qwen3-32B-FP8 LLM endpoint、Qwen3-Embedding-0.6B embedding endpoint、vLLM/Neo4j 资源约束。实际 host、port、model identifier 必须由 P0 从冻结 JSON 与 endpoint model list 双重读取，禁止在新代码里复制常量。

construction-side并发authority的仓库事实必须单独冻结：`membind-validation/src/native_characterization_runtime.py`定义`MAX_COROUTINES=8`，`U0Config.max_coroutines`从`GRAPHITI_MAX_COROUTINES`做严格整数读取，并由`build_u0_graphiti_from_env()`传给`Graphiti(max_coroutines=...)`；v1.3通过现有protocol runtime构造路径复用它。相反，Graphiti固定revision的`semaphore_gather()`每次调用会新建一个局部`asyncio.Semaphore(max_coroutines or SEMAPHORE_LIMIT)`，所以`SEMAPHORE_LIMIT=20`不是跨callsite全局authority；v1.3 frozen backend里的`max_num_seqs="vLLM pinned-version default"`也不是冻结的logical submission数值。P4据此从当前runtime值8建立V5统一`C_admit`，但不得把8表述成GPU物理capacity。

仓库中的 committed artifacts 只说明历史事实，不说明进程现在仍在运行：

- `sfwb-v1-3-formal-baseline-20260821-001`：`GroupIdValidationError`；
- `...-002` / `...-003`：取消/中断类失败；
- `20260822-001.log`：出现 service direct-get failure/coroutine warning；
- `sfwb-v1-3-formal-baseline-20260822-002`：只保留首个 B0 的部分 trace，尚无 committed formal seal/failure；日志还有 edge endpoint 和 provider JSON retry 警告。

因此 coding agent 不能仅凭目录名判定 baseline 状态。进入 live 前必须检查真实 tmux、PID、endpoint、GPU、Neo4j 和 output 增长，并把当前 baseline/session/output/completion condition 写入 queue manifest。

### 2.4 稳定、问题与过期文档

稳定并应最大化复用：冻结 workload/config、native kwargs、fresh namespace/lifecycle、instrumentation、AttemptStore、canonical projection、QA question/evaluator、formal reducer 的 B0/B1 projection、preflight/resource evidence。

已被历史实验或源码证明不能直接作为 V5 的部分：

- V3.1 手工 `prepare → bind` 后重建 Graphiti suffix，容易偏离 native path；
- V3.1 固定 K=2/lookahead=2/cache-affinity 是历史 candidate policy，不是当前 V5 理论要求；
- V4 MEG/compiler/effect journal/version-token 体系过重，且无法提供 Graphiti 真正 transaction version；
- passive semantic fingerprint/first-divergence 能诊断，但不能确保 exact binding 或 correctness；
- `LOCAL_PROTOCOL_STATE_AUDIT.md` 针对较旧 commit，关于 active runner 的描述已过期。

执行时以源码、测试和 sealed artifact 为准；文档冲突时记录冲突并更新 workplan evidence，不得机械照旧文档实现。

### 2.5 已有运维/失败处理模式

这些不是重新设计的空间，V5 应直接沿用仓库已经形成的处理方式：

| 历史问题 | 当前仓库已有机制/证据 | V5 执行规则 |
|---|---|---|
| endpoint 可连通但 model/context 不对 | v1.2 `services.py::probe_model_catalog` 同时校验 model id、max model length、root 与 response hash，并显式禁用 proxy | `curl` 只做即时诊断；正式 preflight 复用 strict catalog evidence，不把 HTTP 200 当 PASS |
| sandbox/proxy/direct GET 差异 | `services.py::direct_get_text` 和历史 `SERVICE_DIRECT_GET_FAILED` artifact | 先复现 direct/no-proxy path，再查项目历史与网络环境；不得为跑通而绕过 service identity gate |
| provider host/GPU/process identity受限 | v1.2 `recovery_probe.py`、`external_diagnosis.py` 会做多轮只读 probe、redaction/hash、tmux/service/log inventory | 沿用只读证据与最小 authority 请求；不猜 GPU 映射、不看到占用就 kill |
| invalid Graphiti group/namespace | 历史 `GroupIdValidationError` attempt | namespace 必须走现有 lifecycle/validation helper并在 provider-free test覆盖；失败后新 namespace，不直接拼接未经验证的长字符串 |
| cancellation/KeyboardInterrupt/partial attempt | historical `failure.json`、raw/native trace 与无 seal partial roots | 无合法 seal 就不是可比较结果；保留失败 attempt，不 resume/覆盖成成功，不作为 baseline reference |
| shared vLLM/Neo4j 并发污染 | v1.2 idle/service metrics、v1.3 resource policy | 启动前要求连续 idle/resource evidence；baseline占用时只建立 gated queue |
| provider malformed JSON/retry warning | partial formal log 与 Graphiti logical-call内部 retry | 单条 warning 不直接判失败；检查 logical call最终结果和transport attempts。capture以最终 logical response为单位，同时保留原transport计数 |

遇到同类问题先搜索这些模块、对应 tests、git history 和 artifacts；只有已有机制不能覆盖新根因时才新增恢复代码。

---

## 3. V5 Method Invariants

以下 invariant 是实现 gate；任何一个在 strict paper mode 中被破坏都必须使当前 attempt 失败并换 fresh namespace，不允许 fallback 后仍标记为合格。

1. **Frozen-input invariant**：source 内容、顺序、valid_at、source_description、group/namespace、entity types、excluded types 和 client config 与 native baseline 相同。
2. **Certified-dependency invariant**：只 hoist 被当前 pinned source 证明不读取/写入 derived memory、正常路径上的callsite reachability/control predicate也只依赖source/config、具有可重放 oracle effect且存在exact native binding seam的logical call；当前候选仅限node extraction与edge extraction内的LLM oracle effects。
3. **Formal-timing invariant**：preparation 是被计量的 V5 work，不能发生在 `FORMAL_START` 前。
4. **Exact-request invariant**：只有完整 request identity 命中时才能 replay；近似 prompt、仅文本 hash 或仅 callsite 名称都不够。
5. **Native-path invariant**：publication 阶段调用原生 `Graphiti.add_episode()`，不复制 `_process_episode_data`，不手写 resolve/persist suffix。
6. **Single-consume invariant**：每个 certified transcript 只能被预期 call ordinal 消费一次；missing、duplicate、mismatch、unconsumed 均失败。
7. **Delegation invariant**：未认证的 LLM call（node/edge resolution、attributes、community 等）原样调用 provider；不得误命中 transcript。
8. **Source-closure invariant**：preparation 的 previous-context 只能来自 frozen source prefix；fresh isolated namespace、无 external writer、group/source/filter/order/limit/timestamp-tie 条件必须被证书覆盖。source projection 与 native request 不一致时 strict abort。
9. **Non-escaping-local-effect invariant**：preparation 可以产生临时对象/UUID/解析结果，但不得进入 persistent state、native request identity、共享 RNG/clock state或后续publication；prepared parsed objects全部丢弃。
10. **Logical-semantic-work-conservation invariant**：对成功完成并进入seal/reducer的source transition，每个hoisted logical call只实际进入logical provider client一次；native replay不再进入provider；非hoisted logical/embedding/driver work仍走原生路径。该invariant不声称本地prompt/parse CPU work或不同live run的transport retries完全相同。失败attempt可有已提交但未消费的preparation，必须显式计为wasted/unconsumed failure evidence，且不得推进frontier或进入paper reducer。
11. **Frontier-critical-admission invariant**：admission优先级固定为`NATIVE_FRONTIER > FRONTIER_PREPARE > FUTURE_PREPARE`。V5以当前冻结protocol runtime的`max_coroutines`机械建立统一logical submission envelope `C_admit`；它不是GPU physical capacity、vLLM active-sequence capacity或Graphiti已经存在的全局semaphore。`C_admit>=2`时为frontier-critical work保留至少一个credit，`C_admit=1`时安全退化为无重叠；critical waiter存在时不得继续admit off-path future preparation。
12. **Ordered-version invariant**：source `i` 只有在 durable frontier 为 `i-1` 时才能进入原生 `add_episode()`；返回并通过 durability checkpoint 后才推进为 `i`。
13. **Failure-atomic frontier invariant**：source `i` native failure 时不得推进 frontier，后续 source 不得 publication；准备任务应取消或封存为 unconsumed failure evidence。
14. **Append-only evidence invariant**：不得改写 baseline root、sealed attempt、已有事实或失败；V5 使用独立 root/attempt/namespace。
15. **Protocol-comparability invariant**：V5 使用相同 frozen workload/backend/client/resource/correctness/QA/reducer projection，差异只来自 execution method。
16. **Fail-closed invariant**：strict mode 无静默 fallback。调试/production-safe模式如需 fallback，必须有不同 method/config identity，且结果不得进入 paper reducer；本轮实现和正式 protocol 只要求 strict mode。
17. **Source-attribution invariant**：同一source可以有时间上分离的`PREPARE`与`NATIVE`执行region，但二者必须使用与B0相同的`TraceRecorder.episode_scope(namespace, episode_id, source_sequence)`身份。scope必须进入对应async task并传播到全部hoisted logical/transport spans；任何无scope、错source或跨source泄漏都fail closed。native replay不得产生第二份provider span；每个source最终只物化一份包含其全部region的trace envelope。
18. **Build-timer invariant**：`timer_start_ns = FORMAL_START = t0_ns`，`timer_stop_ns = t_durable_complete_ns`（语义上是final durable publication completion），`build_makespan_ns = timer_stop_ns - timer_start_ns`，与B0/B1共享同一runner clock boundary。所有V5 semantic preparation必须满足`span.start_ns >= timer_start_ns`；最后一个raw `PUBLICATION_DURABLE`不得晚于stop，且其后到stop之间不得有semantic work；validation/QA/seal必须在timer停止后执行。

### 3.1 Operator contract 与 hoistability

每个原生 operator/effect unit `u` 使用以下最小 contract：

\[
Contract(u)=\langle R_S,R_M,W_M,E_L,E_O,D,C_D,B,P\rangle
\]

- `R_S`：读取的 immutable source/config；
- `R_M`：读取的 derived-memory version；
- `W_M`：persistent memory write/effect；
- `E_L`：本地临时 effect（对象分配、解析、临时 UUID 等）；
- `E_O`：LLM 等 external-oracle effect；
- `D`：本地数据依赖；
- `C_D`：callsite reachability与control dependency，包括正常control predicate和异常前驱；
- `B`：是否存在 exact native binding seam；
- `P`：是否构成 durable publication。

只有满足下式的 oracle effect 可以移动：

\[
Hoistable(u)\iff
R_M(u)=\varnothing
\land W_M(u)=\varnothing
\land Inputs(u)\subseteq S_{\le i}\cup Outputs(H_i)
\land SourceClosed(ControlPred(u))
\land Replayable(E_O(u))
\land NonEscaping(E_L(u))
\land Bindable(u)
\land Certified(u)
\]

未知 effect、无法闭合的source input、依赖derived state的正常control predicate、不可隔离的local effect或无exact callsite seam一律为`OPAQUE`，留在native path。异常前驱单独记录：V5不要求提前oracle work在失败attempt中也与Native Serial守恒，但要求失败不publication、frontier不推进、wasted preparation可审计且不进入正式结果。证书是revision-pinned adapter proof obligation，不宣称对任意Python程序做形式化验证。

### 3.2 Theorem 1 — Oracle-conditioned semantic serial equivalence

把 Native Serial 的第 `i` 个状态转移写为：

\[
M_i = F(M_{i-1},S_i,H_i(S_{\le i},C,O),G_i(M_{i-1},S_i,C,O))
\]

其中 `H_i` 是certified source-closed oracle transcript，`G_i`是state-bound native work，`F`是原生`add_episode()`控制流。对成功完成的transition，在相同source/config、相同request-keyed oracle outputs、source-closed正常control reachability、有效certificate、non-escaping local effects、exact single-consume binding和ordered frontier条件下，对`i`归纳可得：

\[
\Pi_{sem}(Trace_{V5})=\Pi_{sem}(Trace_{NativeSerial})
\]

`Π_sem` 使用项目现有 canonical projection：忽略 namespace、随机 UUID 与不影响语义的run-local metadata，但保留entities/edges、属性、temporal validity、invalidation、source attribution和publication order。不要声称两个独立live run产生byte-identical DB；provider-free scripted qualification验证证明前提，live protocol验证canonical/QA结果。

### 3.3 Theorem 2 — Logical semantic work conservation

在strict模式、定理1前提及成功完成并被sealed的transition集合上，Native Serial logical semantic calls与V5 logical provider executions存在双射：hoisted call在preparation执行一次并在native callsite零provider replay；non-hoisted call只在native path执行。于是：

\[
L_{V5}=L_{Serial}
\]

`L` 是logical request identity/response transcript的多重集合。由定理1的相同native控制流再推出native embedding与DB semantic-operation投影相同；preparation的driver/embedder trap保证不会增加这两类work。V5会增加少量capture、prompt reconstruction、response parsing和临时对象CPU work，因此不得写成“所有物理work完全相同”。不同live运行的transport retry和实际GPU batching也可能不同；现有instrumentation必须分别报告logical calls、transport attempts与local overhead。若原生前驱在到达certified callsite前异常，V5可能已经执行preparation；这属于失败attempt的wasted work，不属于上述双射，也不得被错误包装成合格结果。

### 3.4 Theorem 3 — Dependency-DAG span reduction

令 `h_i` 为source `i`的hoistable sub-DAG span，`s_i`为必须在准确`M_{i-1}`上运行的native suffix span。所有source在`t0`可用且忽略有限资源竞争时：

\[
Span(D_{Serial})=\sum_{i=1}^{n}(h_i+s_i)
\]

\[
Span(D_{V5})=\max_{1\le k\le n}\left(h_k+\sum_{j=k}^{n}s_j\right)\le Span(D_{Serial})
\]

若source有arrival time `a_i`，递推为 `T_i=max(a_i+h_i,T_{i-1})+s_i`。这是transformed dependency DAG的span，不是饱和GPU上的wall-clock保证；batching、FCFS、prefix cache、retry、straggler与资源竞争由现有protocol测量。

有限资源下再要求 **frontier non-bypass**：阻塞`durable_frontier+1`的`NATIVE_FRONTIER`或`FRONTIER_PREPARE`先于off-path `FUTURE_PREPARE`；critical waiter出现后，future preparation不能先获得admission。它最多被已经admitted的有限future work阻塞，不能被无界future work阻塞。该性质保证liveness/有界阻塞，不承诺抢占已经在vLLM执行的request，也不宣称在线调度达到critical-path optimality。

### 3.5 为什么不是“简单并行抽取 + 串行构建”

外观上确有提前 semantic work 和有序 native transition 两段，但实现边界不同：

- 不把 prepared nodes/edges 直接喂给自建 graph builder；
- 不复制 Graphiti resolve/attribute/persist 控制流；
- 不假设 raw result 可替换 native parsed object；
- 移动的是位于state-bound operator两侧、非连续的oracle effects：当前Graphiti中edge extraction物理上位于node resolution之后，但只依赖raw nodes/source；
- 通过 pinned callsite 的 exact transcript binding，把提前oracle response重新嵌回原生执行；
- 对source closure、effect、local non-escape、bindability、单次消费、版本顺序和logical work conservation给出fail-closed certificate；
- 通过frontier-critical admission保护当前memory visibility关键路径，而不是把所有future extraction无界提交给FCFS backend。

因此贡献是 memory-system runtime 的 **certified oracle-effect code motion + native callsite binding + critical-frontier scheduling**；Graphiti 只是第一个 typed adapter，不应把方法包装成 Graphiti 私有工程优化。

---

## 4. Source/Data Dependency Audit

### 4.1 pinned Graphiti `add_episode()` 的实际数据流

审计对象：Graphiti `v0.29.3`，`graphiti_core/graphiti.py::Graphiti.add_episode`。当前调用顺序为：

1. 校验/路由 group，查询 recent previous episodes；
2. materialize current episodic node；
3. `extract_nodes(clients, episode, previous_episodes, ...)`；
4. `resolve_extracted_nodes(...)`；
5. `_extract_and_resolve_edges(...)`，其中 `extract_edges(...)` 接收的是 **raw extracted nodes** 与 previous episodes；
6. edge candidate search / pointer resolution / LLM resolution；
7. `extract_attributes_from_nodes(...)`；
8. `_process_episode_data(...)` 持久化；
9. 可选 community maintenance。

这一点修正了旧 V4 audit 的一个关键结论：当前 0.29.3 的 edge extraction 依赖 raw extracted nodes，并不依赖 resolved node identity。固定源码的正常成功路径中，`_extract_and_resolve_edges()`也没有根据node-resolution结果决定是否调用`extract_edges()`的derived-state条件分支，因此当前callsite同时通过data dependency与normal control dependency候选审计。异常前驱仍需按P1单独认证。代码是 authority，旧 audit 只能保留为历史证据。

### 4.2 operator classification

| operator | 真实输入 | current memory read/effect | V5 决策 |
|---|---|---|---|
| recent previous-episode prompt projection | frozen earlier source episode content/timestamp，latest valid 10 | native 实现从 DB 读，但 prompt 所需投影可由 frozen durable source prefix 重建 | 可作为 preparation 的 source-prefix adapter；必须证明排序、limit、timestamp tie 与 native 一致，并在 binding 时 fail closed |
| `extract_nodes` 内的 logical LLM call | current source content/time/description、previous source projection、entity config | 函数体不需 driver/embedder；LLM oracle是昂贵外部effect；本地prompt/parse/UUID为非逃逸临时effect；正常callsite reachability不依赖resolution result | 只hoist oracle effect；函数本地scaffolding会在native再次执行 |
| `extract_edges` 内的 logical LLM call | current source、raw extracted node names/labels、previous source projection、edge config | 函数体不需 driver/embedder；raw nodes 来自第一步source-derived response；prompt不应依赖临时UUID；固定revision正常路径上无derived-state control predicate | 只hoist oracle effect，且preparation中位于node response解析之后；native仍执行原函数 |
| node resolution | candidates、embedding、Neo4j state、LLM dedupe | 读取 current derived graph | 不移动，留在 native path |
| edge resolution | graph candidates/pointers/timestamps/attributes、LLM | 读取 current derived graph | 不移动 |
| node attributes/summary | resolved state、新 edges、LLM | 依赖 stateful native result | 不移动 |
| persistence / communities | graph writes/current graph | persistent effect | 不移动；只由 ordered native frontier 执行 |
| embeddings | current candidate/state-dependent text and native control flow | 有 external work，且不少调用依赖 current state | V5 不 hoist |

### 4.3 previous source context reconstruction

实现 `build_source_previous_episodes(sequence, frozen_sources)` 时必须复制 **语义** 而不是复制 DB 查询。不能把`durable_source_frontier`作为preparation输入，否则会把hoisting重新阻塞在前驱publication之后；preparation使用全部已冻结且`source_sequence < sequence`的source prefix，而ordered native publication保证真正调用source `i`时该prefix已经durable：

- 只考虑当前 sequence 之前、属于同一 frozen history 的 episode；
- 使用与 native episode 相同的 name/content/source/source_description/valid_at；
- 证明fresh isolated namespace、无external writer、同group/source filter，使native DB episode projection与frozen source prefix一一对应；
- 应用 Graphiti 0.29.3 相同的 `valid_at <= current.valid_at`、`ORDER BY valid_at DESC` 与 latest limit `RELEVANT_SCHEMA_LIMIT=10`；
- P1必须扫描frozen workload中的valid_at ties；如果存在tie且native query没有稳定secondary key，则certificate不得猜测顺序，应使adapter `OPAQUE/INVALID`或找到源码支持的确定化seam；
- preparation 可基于 frozen source prefix，而不是读取 Neo4j；publication 时 exact request identity 是最终动态证明：预测 context 与 native request 有任何差异都不 replay。

source closure 是当前fresh single-history protocol下的adapter结论，不是Graphiti在任意multi-writer production namespace中的普遍性质。外部writer、额外episode、不同source type或不确定tie都会使该certificate失效。

### 4.4 dependency certificate

`hoist_certificate.json` 至少包含：

- MemBind commit、Graphiti commit/tag、相关函数 source hashes；
- LLM client class/module/source hash 与 frozen client/model/config hashes；
- certified callsites 与允许 capability（仅 LLM）；
- forbidden capabilities（driver、embedder、persistent store、network 除 LLM delegate）；
- previous-source projection rule、fresh namespace/no-writer假设、filter/limit/order/tie evidence；
- local temporary effects、non-escape evidence及edge prompt不依赖临时UUID的测试；
- 正常control predecessors、callsite reachability/control predicate的source closure、异常前驱与abort/wasted-work policy；
- oracle effect replayability与native callsite bindability；
- logical request identity schema、wire evidence schema、cache_salt/client-finalizer identity；
- instrumentation install order；
- strict/fail-closed policy；
- provider-free test IDs 和结果 hashes。

证书不是通用静态 effect analyzer。P1 用最小 source assertions + runtime capability traps 验证上述具体边界；revision/hash 漂移则 certificate 无效并回到源码审计。

### 4.5 当前 trace 的可行性证据与使用边界

现有 sealed `sfwb-v1-3-simple-20260821-004` 12-source B0-A qualification trace给出：

| 项目 | 观测值 |
|---|---:|
| Native `add_episode` spans | 983.407 s |
| Node extraction spans | 72.328 s |
| Edge extraction spans | 762.267 s |
| Node+Edge movable-oracle所在phase spans | 834.595 s |
| 剩余原生stateful suffix差值 | 148.812 s |
| 固定B0观测duration、忽略资源竞争的dependency-only projection | 651.198 s（约1.51×） |

这只证明当前prefix存在non-trivial movable cut，并提示source 8的edge extraction是主要straggler。source 8记录约550.267 s、`output_tokens=19,512`、`retry_count=1`；19,512是该logical call跨retry的instrumentation累计值，不能描述成单个最终response长度。

`651.198 s / 1.51×`不是live预测、理论下界或严格speedup ceiling，也不是4-history formal结果。并发后service time会受batching、FCFS、chunked prefill、prefix cache和retry影响。禁止据此承诺1.3–1.6×或2×；现有protocol负责给出真实wall-clock结果。

---

## 5. Repository Reuse & Refactoring Audit

| 能力 | 决策 | 具体来源 | 原因/约束 |
|---|---|---|---|
| frozen dataset/source ordering/token facts | 原样复用 | v1.2 `dataset.py`、v1.3 protocol/config | 已是比较合法性的核心，不应复制 |
| Graphiti kwargs/native episode conversion | 原样复用 | v1.2 `graphiti_adapter.py` | 保证 V5 publication 与 B0 入口相同 |
| lifecycle/fresh namespace/AttemptStore/canonical exporter | 原样复用 | v1.2 live runtime + v1.3 lifecycle | 已有 append-only 与 durability 语义 |
| native instrumentation/C2 measurement | 原样复用并规定安装顺序 | validation instrumentation、v1.2 instrumentation | 保持现有 protocol 指标；避免 preparation 漏计或 replay 双计 |
| v1.3 preflight/live dependency factory/resource evidence | 原样复用 | v1.3 | 避免重写 runner/runtime |
| B0/B1 reducer projection | 抽取/复用纯函数，非必要不改 | v1.3 `formal_baseline.py` | V5 需要同列比较，但不得改 baseline rows/seal |
| production QA method validation | 最小向后兼容重构 | v1.2 `production_qa.py` | 增加可选 `expected_methods`，默认仍 B0/B1；测试旧调用输出不变 |
| production runtime protocol identity | 仅 RED 证明需要时泛化 | v1.2 `production_runtime.py` | 可增加默认保持旧值的 `expected_protocol_version`，或在 V5 composition 层封装；不伪装成 B0 authority |
| V3.1 frontier/admission/artifact tests | 借鉴思路 | v3.1/v1.3 extension | 可复用有序发布、failure evidence 的测试模式，不复用手工 suffix |
| V4 semantic fingerprint/request capture/source hash | 借鉴局部实现 | v1.3 `membind_v5` 历史模块 | canonicalization、single-consume、source hash 有用；runtime gate 不足 |
| v1.3 `membind_adapter.py` | 不作为 V5 execution path | V3.1 adapter | 它手工 bind/构造，违反 native-path invariant |
| V3.1 K=2/lookahead=2/cache affinity | 不复用 | historical coordinator | 是 candidate policy，不是理论必要条件，也会引入无关调参 |
| V4 MEG/compiler/effect journal | 不复用 runtime | historical V4 | 过重且无真实 Graphiti version token；只保留设计教训 |
| passive first-divergence/fingerprint | 保留诊断，不作 correctness gate | current v1.3 modules/tests | 只能观察相似性，不能证明 exact native replay |
| V5 typed runtime core | 新建且禁止依赖Graphiti | `membind_v5/runtime/core/` | 承载contract、transcript、binder、frontier/admission；保证方法不是Graphiti私有优化 |
| Graphiti 0.29.3 adapter | 新建 | `membind_v5/runtime/adapters/graphiti_0293.py` | 只负责revision-pinned certification、source projection、native callsite seam |
| V5 live campaign | 新建并复用现有runner设施 | `membind_v5/live_block.py`、`campaign.py` | 当前仓库没有满足所有invariant的execution path，但不应复制v1.2 runner |

原则：先让 RED test 证明公共模块确实需要变化，再修改现有代码；否则 V5 在新 namespace 下组合现有能力。所有公共重构必须先锁住 B0/B1 golden behavior。

---

## 6. Minimal V5 Architecture

### 6.1 建议文件边界

保留当前`membind_v5/{semantic_fingerprint,first_divergence,offline_analyzer}.py`作为历史诊断，不移动、不删除。在其下新增小型runtime分层：

| 文件 | 最小责任 |
|---|---|
| `runtime/core/contracts.py` | immutable operator/effect contract、`SourceVersion`、`PreparedTranscript`、`HoistCertificate`、publication/frontier contracts |
| `runtime/core/request_identity.py` | logical request typed canonicalization/hash；拒绝不支持对象；wire identity只接收instrumentation evidence |
| `runtime/core/transcript.py` | transcript entries、ordinal queues、deep-copy、serialization/redaction、single-consume ledger |
| `runtime/core/binder.py` | scoped capture/replay proxy、strict match/delegate/finalization errors；不import Graphiti |
| `runtime/core/frontier.py` | source/prepared/durable frontier、ordered publication、failure cancellation |
| `runtime/core/admission.py` | `NATIVE_FRONTIER > FRONTIER_PREPARE > FUTURE_PREPARE`、`C_admit`统一submission envelope、reserved critical credit、bounded outstanding、deterministic admission ledger |
| `runtime/adapters/base.py` | `MemoryMaintenanceAdapter` Protocol；若可直接放在contracts则不额外建文件 |
| `runtime/adapters/graphiti_0293.py` | pinned 0.29.3 certificate/source-prefix projection；原生extraction preparation与`add_episode` invocation |
| `qualification/certificate_check.py` | source hashes、import/effect/source-closure/non-escape qualification |
| `qualification/scripted_conformance.py` | actual native path的scripted serial-equivalence qualification helper |
| `live_block.py` | 复用 v1.2/v1.3 lifecycle/instrumentation/AttemptStore/canonical exporter，运行一个 V5 history block |
| `campaign.py` | 验证 sealed baseline reference；4-history V5 extension；QA；合并 reducer；V5 seal |

核心Protocol保持小而明确：

```python
class MemoryMaintenanceAdapter(Protocol):
    def certify(self) -> AdapterCertificate: ...
    def source_snapshot(self, source, frozen_prefix) -> SourceSnapshot: ...
    async def prepare(self, source, snapshot, oracle) -> OracleTranscript: ...
    async def invoke_native(self, source, binding_scope) -> NativeResult: ...
    async def durable_publication(self, result) -> PublicationToken: ...
```

必须有静态测试：`membind_v5/runtime/core/**`禁止import `graphiti_core`，Graphiti专有类型只能出现在adapter/live composition层。不要为了第二个未来adapter预先引入plugin registry、通用IR compiler或复杂base class。

CLI/scripts：

- `saturated_fixed_work_baseline_v1_3/scripts/run_v5_qualification.py`
- `saturated_fixed_work_baseline_v1_3/scripts/run_v5_campaign.py`
- `saturated_fixed_work_baseline_v1_3/scripts/run_v5_campaign_tmux.sh`
- `saturated_fixed_work_baseline_v1_3/scripts/queue_v5_after_baseline_tmux.sh`

测试建议：

- `tests/test_membind_v5_dependency.py`
- `tests/test_membind_v5_oracle_binding.py`
- `tests/test_membind_v5_frontier_runtime.py`
- `tests/test_membind_v5_native_equivalence.py`
- `tests/test_membind_v5_live_block.py`
- `tests/test_membind_v5_campaign.py`

若最小实现适合合并文件可以合并，但不得混淆request identity、transcript、binding、frontier/admission与live orchestration的职责。不要复制一套v1.2 runner。

### 6.2 preparation 与 native continuation

对 source `i`：

1. 根据 frozen source prefix 构造与 native prompt 等价的 previous episodic projection；
2. 构造只有 LLM capability 的 Graphiti clients façade；driver/embedder 访问立即抛出 `PreparationEffectViolation`；
3. 在capture scope中调用pinned `extract_nodes(...)`，捕获其中的LLM oracle effect；
4. 将preparation产生的parsed raw nodes传给pinned `extract_edges(...)`，捕获第二个LLM oracle effect；P1必须证明edge request只使用name/labels等source-derived字段，不使用临时UUID；
5. capture proxy 对每个 logical LLM request 在调用原 provider 前冻结 identity/deep copy，调用 provider 并深拷贝最终 response；
6. 丢弃 preparation 产生的 parsed graph objects，只保存 transcript/certificate；
7. source `i-1` durable 后，在 replay scope 中调用 v1.2 adapter 的原生 `Graphiti.add_episode(...)`；
8. node/edge extraction 的原生 request 精确命中后返回 transcript；其余 provider call delegate；
9. `add_episode` 返回且 durability evidence 完成后推进 frontier。

丢弃parsed objects是有意设计：它避免prepared Python object、Pydantic parsing或后续Graphiti内部变换成为新的非原生ABI；response在原生callsite重新进入同一parsing/control flow。这意味着被移动的是oracle effect，本地prompt构造/解析会重复一次；该有界CPU开销必须留在`T_build`，不能被work-conservation定理抹去。

### 6.3 exact request identity

binding使用的 **logical request identity** 至少覆盖：

- schema version、MemBind/Graphiti adapter revision；
- history/source hash、source sequence、native callsite/prompt name、per-source logical call ordinal；
- canonical deep copy of `messages`；
- response model module/qualname 与 stable JSON schema；
- `max_tokens`、`model_size`、`group_id`、`prompt_name`、`attribute_extraction` 及所有实际 keyword/positional args；
- frozen LLM client class/source hash/config/model/base URL/structured-output mode identity（secret 只 hash，不落盘）；
- frozen transport/finalizer environment snapshot identity，包括当前实现逐request读取的construction decoding字段（至少`CONSTRUCTION_TOP_P`、`CONSTRUCTION_SEED`）；
- block-specific construction `cache_salt` hash；
- source previous-context digest。

identity必须在delegate之前取得，因为Graphiti LLM client会修改messages、注入schema/multilingual instruction并clean input。V5绑定在logical `generate_response`外层：相同logical arguments + pinned client finalizer/config推出相同effective provider request。不要在binder中复制Graphiti finalizer来生成“rendered prompt”；那会形成第二套易漂移逻辑。response必须深拷贝，防止native consumer修改transcript。

现有低层instrumentation另记录 **wire request evidence**：finalized payload/config hash、cache_salt、transport attempt ordinal等。wire evidence用于审计logical→wire映射，不参与binder匹配。certificate同时pin logical identity schema、client finalizer source hash和冻结transport env/config snapshot。

仅pin finalizer源码hash不够，因为当前`graphiti_native.py::_QwenCompletionsTransport.create`会在每次request读取`CONSTRUCTION_TOP_P`与`CONSTRUCTION_SEED`。P2必须证明：相同logical args + schema + pinned client/finalizer + frozen env/config snapshot产生相同normalized semantic wire payload hash；测试中变更seed/top-p或其他finalizer输入必须触发authority drift/request mismatch并strict abort。不得因此把wire hash提升为binder key或在V5复制finalizer。

同一 source 出现多个完全相同请求时，以 callsite + ordinal 的 FIFO queue 区分。Graphiti 的 transport retry 位于一次 logical `generate_response` 内部：capture 保存一个最终 logical response，现有 instrumentation 继续记录所有 transport attempts；replay 不制造第二组 transport attempts。

### 6.4 instrumentation 安装顺序

严格按以下顺序安装：

1. build Graphiti/runtime；
2. 安装既有 native characterization instrumentation；
3. 安装既有 C2 measurement；
4. 最外层安装 V5 capture/replay proxy。

这样 preparation miss/delegate 穿过既有 instrumentation 并计入 logical/transport work；native replay hit 在进入 instrumentation 前返回，避免重复计数；未命中 stateful call 继续穿过 instrumentation。恢复顺序相反：V5 → C2 → native。测试必须锁住这一点。

source attribution沿用现有`TraceRecorder`，不得另造一套LLM计量器。`TraceRecorder.span()`在没有episode context时会返回`_NullSpanHandle`且不记录span，因此V5 composition必须把每个source的两个region显式放入相同identity scope：

```python
def source_scope(episode):
    episode_id = f"{episode.history_id}:{episode.source_sequence}"
    return recorder.episode_scope(
        block.namespace,
        episode_id,
        episode.source_sequence,
    )

async def prepare_one(episode):
    with source_scope(episode):
        return await v5_adapter.prepare(...)

async def invoke_native_one(episode, transcript):
    with source_scope(episode):
        return await v5_adapter.invoke_native(...)
```

scope必须在对应async task内部、首次instrumented call之前进入；不能在父循环中短暂进入scope后再创建/保存一个脱离context的coroutine。provider-free并发fixture必须同时运行多个source，证明`ContextVar`传播后不存在source串包。可以用parent span或现有允许的metadata标记`v5_region=PREPARE|NATIVE`，但不得复制logical/transport span。

每个source的preparation完成后才可能进入其native region，因此`native_trace.jsonl`应在该source的native/durable region完成后调用一次`episode_envelope(...)`，使同一envelope包含两个时间上分离的region；不得在prepare和native各写一份后让reducer重复计数。最终要求：每个expected source恰好一个envelope，所有captured/delegated provider spans都有匹配的history/source identity，按source聚合后的logical calls、tokens和transport attempts与`logical_work_summary.json`一致。

block timer使用现有v1.3 lifecycle字段和共享runner clock boundary作为唯一authority：在任何V5 semantic task creation/admission之前记录`FORMAL_START/t0_ns/timer_start_ns`；最后一个source完成durability checkpoint且runner返回后记录`t_durable_complete_ns/timer_stop_ns`。raw journal中的最终`PUBLICATION_DURABLE`是对该语义边界的事件证据，不另作V5专属、更短的stop clock。必须直接计算：

```text
build_makespan_ns = timer_stop_ns - timer_start_ns
```

该区间包含全部`PREPARE`/`NATIVE`region、admission与frontier等待；per-source span union/sum只用于work attribution，不能替代block wall-clock。实现必须断言首个V5 task/span不早于start、`final_publication_event_ns <= timer_stop_ns`、该delta内无semantic span/provider/DB work、所有source均已durable后才进入validation。现有formal baseline中“preparation outside T_build”的注释只适用于其`FORMAL_START`前的普通准备，不授权把V5 semantic preparation移出timer。

### 6.5 ordered frontier 与资源许可

状态最少包含：

- `source_frontier = N-1`（frozen input 在 formal start 已可见）；
- `prepared_set`（可乱序完成）；
- `durable_frontier`（初始 `-1`，只单调 `+1`）；
- `failed_sequence` 与 cancellation reason。

publication loop 始终只选择 `durable_frontier + 1`。不能因为 source 7 先 prepared 就让它在 source 6 前调用 `add_episode()`。

为了避免preparation把当前vLLM/Graphiti submission与FCFS backend queue全部占满而让frontier suffix饿死，实现小型、通用的frontier-critical admission。这里不寻找一个事实上不存在的“Graphiti全局C”，而由V5建立统一logical submission envelope：

- 记`C_admit := runtime.config.max_coroutines`。P0/P4必须验证该值来自当前冻结protocol runtime，且`runtime.config.max_coroutines == graphiti.max_coroutines == native runtime authority`；当前审计pin为`8`。不允许在V5另选数值；若真实runtime漂移或三者不一致则fail closed并回到authority审计；
- `C_admit`是**protocol/runtime-derived submission budget**，不是GPU physical capacity、vLLM active-sequence ceiling或HTTP server真实并行度。v1.3 frozen backend里的`max_num_seqs="vLLM pinned-version default"`既非冻结数值，也只描述engine iteration中的sequence ceiling，禁止用它推导`C_admit`；
- Graphiti `helpers.SEMAPHORE_LIMIT=20`也禁止作为authority：固定源码的`semaphore_gather()`每次调用都会新建局部`asyncio.Semaphore`，不能证明跨preparation/native callsite的共享全局envelope；
- V5 admission proxy把这一已冻结runtime scalar提升为本方法全部delegated construction logical provider calls共享的统一enforcement layer。capture中的certified provider delegate先取preparation class permit；replay hit不访问provider也不取permit；native scope中需要真实provider的logical call先取native class permit，再进入既有instrumentation/client；
- `d=durable_frontier`时，`FRONTIER_PREPARE`是source `d+1`尚缺、直接阻塞其transcript ready的certified oracle work；`NATIVE_FRONTIER`是source `d+1` transcript已ready后native suffix中的真实provider work；`FUTURE_PREPARE`是sequence `> d+1`的off-path preparation。优先级固定为`NATIVE_FRONTIER > FRONTIER_PREPARE > FUTURE_PREPARE`，同类按source sequence/call ordinal稳定排序；
- 任意时刻V5 delegated outstanding总数不超过`C_admit`。`C_admit>=2`时`FUTURE_PREPARE`最多占`C_admit-1`个credits，始终保留一个frontier-critical credit；critical waiter存在时不再admit新的future preparation。等待中的source tasks不占credit；
- `C_admit=1`时安全退化为串行；priority只约束尚未提交的work，不抢占已进入外部FCFS provider的request，因此只声称critical non-bypass与bounded blocking，不声称critical-path optimality；
- construction cross-encoder是独立transport seam而非`llm_client.generate_response`。当前构图路径预期不调用它，P4必须以callsite inventory和provider-free/live instrumentation证明`construction_cross_encoder_rank_calls == 0`；若固定revision/config实际非零，必须把该seam纳入统一admission coverage或使certificate无效；
- 这不是新的论文K参数，不做sweep，不暴露可调lookahead/worker knob，也不改变Graphiti内部局部semaphore或v1.3 resource policy。

reserved critical credit是V5有限资源关键路径机制，不得因“backend已有FCFS”而删除。`CapacityAuthority`证明的是“V5从冻结runtime值8建立并覆盖统一submission envelope”，而不是“Graphiti/vLLM原本已有物理capacity=8”。若统一coverage无法证明，P4 fail closed；不得退回局部常量20、`max_num_seqs`、无界提交或自选K。

### 6.6 artifacts

每个 V5 attempt 至少写：

- `manifest.json` / `resume_identity.json` / `live_authority.json`；
- `hoist_certificate.json`；
- `capacity_authority.json`（`C_admit`来源=`runtime.config.max_coroutines`、当前值8、runtime/Graphiti equality assertions、submission-only语义、明确的non-claims、相关callsite/cross-encoder coverage、source/config hashes与qualification结果）；
- `oracle_binding_summary.json`（expected/captured/consumed/delegated/mismatch/duplicate/unconsumed counts）；
- `frontier.jsonl`（source/prepared/native-wait/admitted/native-enter/durable/failure events）；
- `admission.jsonl`（class、waiter、credit、outstanding、reserved-credit/non-bypass evidence）；
- `logical_work_summary.json`（logical identities、provider executions、replay hits、non-hoisted delegates、本地capture/reparse overhead）；
- wire/transport evidence继续写入现有instrumentation artifact，不在V5重建第二套transport recorder；
- 现有 `raw_events.jsonl`、`native_trace.jsonl`、`block_metrics.json`、`canonical_graph.json`、QA；其中`native_trace.jsonl`要求每个source恰好一个envelope并覆盖PREPARE/NATIVE两个region；
- lifecycle/block metrics必须保存`timer_start_ns/t0_ns`、`timer_stop_ns/t_durable_complete_ns`、`build_makespan_ns`、最终`PUBLICATION_DURABLE`timestamp及二者delta，并通过上述equality/order/no-semantic-work assertions；
- `private/oracle_transcripts/<sequence>.json`：完整 prompts/responses，仅本地 private evidence；公开 artifact 只保留 hashes/counts；
- attempt `seal.json` 或 `failure.json`，绝不同时写成功 seal 与 strict violation。

campaign root 另写：

- `baseline_reference.json`（baseline root、seal/hash、commit、config/workload identity）；
- `qualification/v5_results.json`、`.md` 和 machine-readable tables；
- `queue_manifest.json`（若排队）；
- `v5_run_seal.json`（仅所有 4-history blocks、QA、reducer 全部完成后）。

---

## 7. TDD Strategy

统一执行节奏：

```text
Inspect
→ RED
→ Minimal GREEN
→ Local Regression
→ Integration
→ Minimal Live
→ Full Legal Live
```

每个 RED 只验证一个理论前提或一个实现 contract。不要创建通用 theorem prover、通用 event-sourcing framework 或全量 first-divergence 平台。provider-free tests 使用 fake driver/embedder traps、scripted logical LLM、最小 in-memory event sink；native-equivalence test 才加载 pinned Graphiti functions。

最小 proof-obligation 集合固定为：

1. certified oracle effect 的source/data closure、正常control reachability closure、persistent-effect freedom、local-effect non-escape与异常abort-work policy；
2. `membind_v5/runtime/core/**` 不 import `graphiti_core`，Graphiti revision-specific 逻辑只在 adapter；
3. previous-source projector 在 fresh namespace / single-writer / prefix / limit / order / timestamp-tie 条件下与 native request context 一致；
4. logical request identity 与finalized wire evidence分层；同logical args与冻结client/finalizer/env snapshot产生稳定semantic wire hash；binder不重复实现Graphiti client内部prompt finalization；
5. transcript exact-key、single-consume、duplicate/missing/mismatch/unconsumed、multi-call/retry；
6. predecessor version、ordered publication、`C_admit`authority/coverage、`NATIVE_FRONTIER > FRONTIER_PREPARE > FUTURE_PREPARE`、reserved critical credit与future-preparation non-bypass；
7. scripted Native Serial/V5 canonical equivalence与成功transition上的logical semantic work conservation；失败attempt不推进frontier且wasted preparation被隔离。
8. PREPARE/NATIVE两个region均进入原source的`episode_scope`，并发task不串包、无null/unattributed span、每source只写一个完整trace envelope；
9. `T_build`严格复用shared runner的`t_durable_complete_ns - t0_ns`（语义上为`final durable publication - FORMAL_START`），覆盖全部semantic preparation/等待/native work且排除durable之后的validation/QA/seal。

这些测试只检查定理前提是否被实现，不用 live 统计“猜测”源码依赖，也不扩张为独立 verification framework。

测试执行环境必须先从 repo docs/pyproject 找到真实 Python。当前审计所在 scratch 没有 `paper-eval-v3/.venv/bin/pytest`，因此本次 workplan 编写阶段未伪称重跑测试。项目文档记录的 scoped v1.3 gate 为：

```bash
PYTHONPATH=saturated_fixed_work_baseline_v1_3/src \
  paper-eval-v3/.venv/bin/pytest -q saturated_fixed_work_baseline_v1_3/tests
```

coding agent 在实际 `/data` 环境应先确认该解释器存在或按项目已有环境修复；repo-wide pytest 曾因未安装的可选依赖/路径产生大量 collection errors，只作环境诊断，不可替代 scoped gate，也不可把 collection errors 误报成 V5 failure。

---

## 8. P0 — Repository Qualification

**Objective:** 固定当前实现 authority、环境与 live state，防止在过期代码、错误解释器或正在运行的 baseline 上开发/启动。

**Inspect:** `git status/log/diff`、Graphiti installed/checkout revision、v1.3 formal runner、v1.2 reused runtime、frozen configs、protocol docs、historical artifacts、Python environment、tmux/process/endpoints/GPU/Neo4j。

**Files likely involved:** 只读审计；必要时新增 `saturated_fixed_work_baseline_v1_3/artifacts/<v5-qualification-root>/p0_repository_qualification.json`，不修改 sealed artifacts。

**Hypothesis:** HEAD 与本计划审计 revision 一致，且能定位唯一合法 Python/runtime；current live state 可从进程和 session 证据确定。

**RED:** 新增/运行一个 qualification check，故意给错误 Graphiti hash、错误 frozen config hash、unsealed baseline root、未知 Python dependency，断言明确拒绝；若实际 HEAD 漂移，真实 P0 应先 RED。

**Expected failure:** 报出具体 mismatch（repo revision、Graphiti revision、config hash、missing dependency、baseline unsealed），而不是 import stack trace 或继续运行。

**Minimal implementation:** 复用 v1.3 preflight/hash utilities，生成只读 qualification report；记录当前 live inventory，不启动/停止任何服务。

**GREEN:** 正确 revision/config/env 通过；错误 fixture fail closed；真实 tmux/PID/output/completion condition 被唯一识别或明确记录 `NONE`。

**Regression:** 现有 v1.3 protocol contract/preflight tests；`git diff --check`；确认无 sealed artifact 被改。

**Evidence:** commit hashes、source hashes、Python executable/package versions、frozen config hashes、live inventory、baseline candidate seal status。

**Failure recovery:** 缺包/路径/网络先查 README、git history、已有 shell scripts 和 historical logs；再查 upstream install docs。不要立即问用户。权限/credential 是真实 authority blocker时才报告。

**Exit condition:** repository authority 与执行环境可复现，且确定当前 baseline 是 `RUNNING`、`QUEUED`、`FAILED/PARTIAL` 或 `NONE`。

**Next:** P1；live inventory 保留给 P8/P9 重新检查，不能沿用旧快照。

---

## 9. P1 — Semantic Dependency Certification

**Objective:** 用pinned Graphiti源码和最小动态trap证明node/edge extraction是data/effect/control上均可hoist的边界，并证明previous-source projection可重建。

**Inspect:** `graphiti_core/graphiti.py::add_episode`、`utils/maintenance/node_operations.py::extract_nodes`、edge extraction/resolution modules、prompt builders、episode query/order、LLM client `generate_response` 与 retry implementation；对照旧 V4 audit 的冲突。

**Files likely involved:** 新增 `membind_v5/runtime/core/contracts.py`、`membind_v5/runtime/adapters/graphiti_0293.py`、`membind_v5/qualification/certificate_check.py`、`tests/test_membind_v5_dependency.py`。

**Hypothesis:** pinned 0.29.3中`extract_nodes`与`extract_edges`的 **LLM oracle effects** 只依赖frozen source/config/source-prefix output；固定revision正常成功路径上的callsite reachability也不依赖derived-state result。围绕调用的prompt assembly/parsing/raw-object construction虽可重复执行，但其local effects不逃逸。resolution/attributes/persistence读取derived state，不能移动。

**RED:**

- source hash/call graph 与 certificate 不一致时拒绝；
- preparation clients 的 driver/embedder 被访问时 trap；
- previous projection 在 fresh namespace、single-writer、同 group、`sequence < i`、`valid_at <= current`、10-item limit、chronological direction或 `valid_at` tie-break 上与 native fixture不一致时失败；
- extraction 的 local object/persistent effect从 preparation scope逃逸时失败；
- edge request若意外依赖 resolved UUID/candidate state时失败；
- synthetic fixture把edge call放到derived-state control predicate之后时certificate拒绝；certificate遗漏正常control predecessor、异常前驱或abort-work policy时拒绝；
- 把 resolution 函数放进 certified set 时 capability test 失败。
- core runtime import `graphiti_core` 时静态 architecture test失败。

**Expected failure:** 当前尚无 certificate/projection implementation；错误 operator 会触发 forbidden effect，错误顺序会产生 request digest mismatch。

**Minimal implementation:** 为两个具体native callsite写source hash assertions、source-prefix projector、LLM-only clients façade和non-escape guard；生成`HoistCertificate`。certificate明确记录read/effect/input/control/binding/publication obligations，区分正常reachability与异常abort policy，但不实现通用effect system。projector读取frozen source log prefix，不以当前durable frontier裁剪；ordered native invocation保证该prefix在真正绑定时已经durable。

**GREEN:** 两个extraction fixtures在driver/embedder设为抛错时仍产生预期logical requests，正常control predicate由source/config闭合，且preparation产生的本地对象全部被丢弃；resolution或state-bound control fixture必然被拒绝；在certificate前提内projection与native request context全等；timestamp tie若无法证明则fail closed；revision drift fail closed；core architecture test通过。

**Regression:** 现有 Graphiti adapter/protocol tests；旧 V5 diagnostic tests仍能运行，但其 classification 不再作为 authority。

**Evidence:** data/control dependency table、source hashes、trap events、normal/exception reachability说明、projection fixture hashes、certificate JSON。

**Failure recovery:** 若 edge extraction 实际读取 resolved state或 projector 无法复制 native context，先最小复现和查 upstream revision/issues；缩小 certified set。只有最终不存在任何 non-trivial source-only semantic call 才触发 methodology STOP。

**Exit condition:** certified set精确到oracle callsite/effect并有源码与test双重证据；source/data/control-closure assumptions被显式验证；异常wasted-work policy明确；uncertified operator/local continuation留在native path。

**Next:** P2。

---

## 10. P2 — Transcript Capture

**Objective:** 在preparation原生extraction路径捕获完整、不可变、可审计的logical oracle transcript，并把全部provider work准确归属到原source trace scope。

**Inspect:** Graphiti LLM client signature/input mutation/retry、`graphiti_native.py::_QwenCompletionsTransport.create`逐request环境读取、`native_characterization_tracing.py::{TraceRecorder.episode_scope,span,episode_envelope}`、v1.2 `live_block.py::traced_add`、v1.3旧`membind_adapter.py::_RecordingAdapter`的source-scope经验、existing native instrumentation patch points、V4 request fingerprint code。

**Files likely involved:** 新增 `membind_v5/runtime/core/request_identity.py`、`membind_v5/runtime/core/transcript.py`、`membind_v5/runtime/core/binder.py`、`tests/test_membind_v5_oracle_binding.py`。

**Hypothesis:** 在外层`generate_response`调用入口canonicalize **pre-finalization logical arguments**，并pin client source/config/cache salt与冻结transport env/config snapshot，可以稳定标识native logical request；相同logical输入与冻结finalizer输入产生相同normalized semantic wire payload。每个prepare task在原source `episode_scope`内运行后，Graphiti client内部message mutation/finalization和transport retry可由现有instrumentation完整记录并保持source attribution，不改变logical transcript count。

**RED:**

- message list 被 delegate mutate 后 transcript 也变化；
- response model/schema、max_tokens、model_size、group_id、prompt name 或 flag 不同却 hash 相同；
- client/model/decoding source identity或 `cache_salt` 不同却 hash相同；
- 相同logical args与冻结env/config得到不同semantic wire payload hash；或`CONSTRUCTION_SEED`/`CONSTRUCTION_TOP_P`被修改却未触发authority drift；
- binder用自制 rendered-prompt hash匹配，从而与Graphiti内部finalizer产生双重实现；
- 单 logical call 内两个 transport retry 被误存成两份 transcript；
- identical requests 的 ordinal 丢失；
- preparation 绕过现有 instrumentation。
- preparation在无`episode_scope`、错误`source_sequence`或父scope退出后才运行，导致`_NullSpanHandle`/跨source attribution却未被qualification拒绝；
- 并发source的ContextVar串包，或prepare/native各写一个envelope导致按source聚合重复计数；

**Expected failure:** 现有 fingerprint 只做被动分析，不能提供完整 capture lifecycle/single logical response contract。

**Minimal implementation:** deterministic typed canonicalizer、pre-delegate deep copy、post-success response deep copy、per-source/callsite ordinal、capture context manager；logical identity只使用native callsite可见参数和pinned client/frozen env configuration。V5 live composition在每个prepare/native async task内部安装相同source identity的`episode_scope`，capture/delegate前验证当前trace context存在且匹配expected source；source native/durable region结束后只物化一次完整envelope。增加scripted transport conformance：对同logical args重复运行真实finalizer并比较normalized semantic wire payload hash；seed/top-p/config mutation必须改变authority/identity。finalized prompt/body、provider request id、attempt/token/latency继续由现有transport instrumentation记录为wire evidence，不复制Graphiti finalizer。只支持当前Graphiti request types，不静默`repr()`任意对象。

**GREEN:** logical identity的每个语义字段变化都改变digest；冻结输入下semantic wire hash确定；env/config漂移fail closed；caller/delegate mutation不污染transcript；retry仍是一份logical transcript；多source并发fixture中每个prepare logical/transport span的`source_sequence`等于prepared source、无null/unattributed/cross-source span；每source恰好一个envelope且同时覆盖PREPARE/NATIVE；logical ledger与wire ledger可通过source/call ordinal关联但职责不混淆；native replay不产生第二组provider spans。

**Regression:** P1 dependency tests；existing instrumentation tests；secret redaction test。

**Evidence:** private transcript fixtures、public digest/count summary、frozen-env snapshot/hash、semantic-wire determinism fixture、per-source trace/envelope coverage table、logical ledger与trace按source聚合的一致性断言。

**Failure recovery:** 遇到不可 canonicalize 参数先检查真实 signature/serialization；增加 typed encoder，不降级成不稳定 repr。provider retry 异常先用 scripted minimal reproduction，再查 Graphiti/LLM client upstream。

**Exit condition:** 对每个certified call能得到稳定logical identity + 最终response；provider execution按logical call只计算一次，transport attempts另行保真计量；全部preparation work可被现有trace按原source完整且不重复地归因。

**Next:** P3。

---

## 11. P3 — Exact Native Binding

**Objective:** 在未经改写的原生 `add_episode()` callsite 精确消费 transcript；所有 stateful/non-certified calls继续走 provider。

**Inspect:** v1.2 adapter kwargs、Graphiti module aliases、instrumentation/C2 patch order、Graphiti parsing/control flow。

**Files likely involved:** 扩展 `membind_v5/runtime/core/binder.py`、`membind_v5/runtime/adapters/graphiti_0293.py`、oracle-binding/native-equivalence tests。

**Hypothesis:** scoped outer proxy 能在原生 node/edge extraction call准确匹配 capture identity，返回深拷贝 response，并在 scope finalization验证完全消费。

**RED:**

- exact happy path 当前仍调用 provider 第二次；
- missing transcript、wrong source/context/config/schema、wrong ordinal、duplicate consume、unconsumed transcript 未失败；
- node resolution 等非 hoisted request 被错误 replay；
- nested/concurrent source scope串包；
- restore 后 patched client未恢复。

**Expected failure:** 当前仓库没有 strict native-callsite replay proxy。

**Minimal implementation:** context-local source binding、keyed FIFO transcript queue、strict hit/miss policy、scope finalizer、typed errors；调用现有native adapter，不实现graph suffix。正式 qualification/paper path只允许 `STRICT`：certified call miss立即中止block。可选 `SAFE_DEBUG`仅用于生产诊断，miss时delegate并记录wasted preparation，必须使用不同method identity且不得进入formal reducer。

**GREEN:** `STRICT`中 certified request零额外provider call且single consume；所有negative cases fail closed；uncertified requests恰好delegate；`SAFE_DEBUG`不会被formal campaign接受；patch install/restore可重入且无跨task泄漏。

**Regression:** P1/P2；existing native instrumentation aliases；B0/B1不安装 proxy 时行为与计数不变。

**Evidence:** `oracle_binding_summary.json` fixture，provider call ledger，strict error snapshots。

**Failure recovery:** mismatch 先输出 redacted field-level diff和最小复现；检查 prompt mutation、previous context、call ordinal、client config、Graphiti alias。不得扩大 fuzzy matching。

**Exit condition:** exact match、single consume、delegate和finalization contracts全部 provider-free 通过。

**Next:** P4。

---

## 12. P4 — Version / Frontier Runtime

**Objective:** 并发准备source-only transcript，同时只允许下一个durable version进入原生`add_episode()`；由V5从冻结runtime authority建立统一`C_admit` submission envelope，并优先推进阻塞`durable_frontier+1`的semantic work。

**Inspect:** V3.1 coordinator/frontier tests；Graphiti全部`generate_response` call paths、cross-encoder transport与`semaphore_gather()`实现；`native_characterization_runtime.py::{MAX_COROUTINES,U0Config.max_coroutines,build_u0_graphiti_from_env}`及v1.3复用路径；`Graphiti.max_coroutines`；frozen backend中vLLM字段的语义边界；lifecycle/durability point与async cancellation patterns。

**Files likely involved:** 新增 `membind_v5/runtime/core/frontier.py`、`membind_v5/runtime/core/admission.py`、`membind_v5/runtime/core/executor.py`、`tests/test_membind_v5_frontier_runtime.py`。

**Hypothesis:** 当前protocol runtime已有唯一、冻结且传入Graphiti实例的`max_coroutines=8`。V5不把它误称为现存全局或物理capacity，而机械取`C_admit := runtime.config.max_coroutines`并在所有delegated construction logical provider calls外新建统一enforcement layer；monotonic durable frontier、三类priority、reserved critical credit与future-preparation non-bypass可保持Native Serial状态演化并重叠source-only semantic latency，不引入新sweep参数。

**RED:**

- sequence 2 先准备后在 sequence 1 前 publication；
- predecessor failure 后 frontier仍推进；
- duplicate durable publish；
- missing preparation导致 silent wait/deadlock无诊断；
- cancellation遗留 background tasks；
- runtime用Graphiti局部`SEMAPHORE_LIMIT=20`、vLLM `max_num_seqs`或手工数字替代`runtime.config.max_coroutines`，却仍启动；
- `runtime.config.max_coroutines != graphiti.max_coroutines`或current pin不是预期authority而未fail closed；
- preparation或native的任一真实logical LLM call绕过已认证的shared admission envelope；
- construction cross-encoder实际发起rank call但未纳入coverage或未invalidate certificate；
- 总outstanding超过`C_admit`，或`C_admit>=2`时future preparation占用超过`C_admit-1`，导致frontier-critical work无保留credit；
- `durable_frontier+1`仍缺的`FRONTIER_PREPARE`被后到`FUTURE_PREPARE`越过；native waiter出现后，future preparation仍先获得credit；
- `C_admit == 1`时系统死锁而不是安全退化为串行；
- runtime声称能够抢占已经提交到外部FCFS provider的请求；
- hard-coded K/lookahead出现在 public config。

**Expected failure:** 当前 V3.1 coordinator耦合历史 policy和手工 bind，不能满足 V5 native continuation。

**Minimal implementation:** 先生成`CapacityAuthority`：记录authority path/hash、`C_admit=8`、runtime/Graphiti equality assertions、submission-only语义、明确的non-claims、callsite/cross-encoder coverage。V5在现有client instrumentation外建立一个共享admission arbiter；provider-free callsite inventory/capability test证明所有preparation与native delegated logical provider calls均经过它，并证明当前construction cross-encoder count为零，否则扩展coverage或invalidate。随后实现source/prepared/native-wait/admitted/native-enter/durable/failure state machine、condition/event、一个ordered native/publication coroutine、bounded preparation tasks、structured cancellation和frontier/admission event sink。以`d=durable_frontier`动态分类`NATIVE_FRONTIER`、`FRONTIER_PREPARE(d+1)`、`FUTURE_PREPARE(>d+1)`；总outstanding不超过`C_admit`，future outstanding不超过`C_admit-1`，critical waiter存在时停止admit future；`C_admit=1`安全串行。只保证未提交work的non-bypass/bounded blocking，不宣称抢占或在线最优。

**GREEN:** `CapacityAuthority`证明V5从runtime authority值8建立统一envelope，runtime/Graphiti值相等，且不把20/`max_num_seqs`误作authority；callsite与cross-encoder coverage全部PASS。任意preparation completion order下native-enter/durable严格`0..N-1`；failure停止在前一frontier；无task leak；所有trace满足total/future bounds，`NATIVE_FRONTIER > FRONTIER_PREPARE > FUTURE_PREPARE`，critical waiter不被later future work越过；`C_admit=1`完成串行fixture；无新sweep参数。

**Regression:** P1–P3；V3.1 tests作为历史 regression仅在共享模块受影响时运行。

**Evidence:** `capacity_authority.json`、runtime/Graphiti equality fixture、拒绝20与`max_num_seqs`的negative tests、logical-call/cross-encoder coverage fixture、deterministic frontier event sequences、three-class credit/admission ledger、future-non-bypass proof fixtures、failure/cancellation fixtures。

**Failure recovery:** 若值/hash漂移先回P0确认真实protocol authority，不搜索不存在的Graphiti global semaphore，也不从vLLM物理scheduler猜数；deadlock加仅用于test的超时并抓asyncio task stacks，建立最小3-source reproduction，检查动态classification、condition通知与permit释放。不要以增大并发掩盖锁错误。

**Exit condition:** `C_admit`值由冻结runtime唯一导出、V5 unified enforcement和完整callsite/cross-encoder coverage已被证明；frontier safety/liveness/provider-free tests通过，runtime没有手写Graphiti suffix。若authority equality或coverage不成立，停留在P4 autoresearch；不得以默认20、`max_num_seqs`或自选K进入P5/live。

**Next:** P5。

---

## 13. P5 — Scripted Serial-Equivalence Qualification

**Objective:** 使用pinned Graphiti真实 `add_episode()` 路径和scripted oracle，对Native Serial与V5做provider-free canonical equivalence与 **logical semantic work conservation** 验证。

**Inspect:** canonical exporter/diff、Graphiti fake/test driver可用路径、native adapter、current QA/canonical contracts。

**Files likely involved:** 新增 `tests/test_membind_v5_native_equivalence.py` 与最小 test fixtures；必要时扩展 graphiti adapter seam，不复制生产逻辑。

**Hypothesis:** 对成功完成的source transition，在相同source/config/request-keyed oracle outputs与受控clock/ID环境下，V5和Native Serial在现有canonical/α-renaming projection上等价；V5只移动certified logical oracle calls，不增加/丢失logical semantic work。preparation与native path重复的本地prompt/parse工作不属于该双射，但必须无持久逃逸并单独计量。原生前驱异常fixture用于验证failure atomicity，不要求失败attempt也满足logical-work双射。

**RED:**

- 两个或三个 source（含 entity重复/edge/attribute）下现有 V5尚不可运行；
- 故意改变 previous context/transcript时 exact binder拒绝；
- 故意让preparation local object泄漏到native path时non-escape assertion拒绝；
- 故意令native前驱在certified callsite前异常：允许出现已提交但未消费的preparation，但必须写failure/wasted ledger、不得推进frontier、不得seal或进入reducer；
- 只比较最终 counts会漏掉内容差异，因此 canonical diff必须失败。

**Expected failure:** 缺 V5 integrated path或 canonical projection不相等。

**Minimal implementation:** 同一scripted request-keyed oracle与受控clock/ID trace，两个fresh namespace；Native Serial正常调用原生add_episode；V5先capture再由原生callsite exact replay；导出现有canonical projection并exact diff。记录logical calls按callsite/key的multiset、provider executions/replay hits、local capture/reparse overhead和transport attempts。若UUID随机，仅比较现有canonical/α-renaming语义投影，不虚构byte-identical数据库证明。

**GREEN:** 成功fixture canonical diff为空；source order/durable events一致；对每个certified logical key，Native Serial provider execution = 1、V5 preparation provider execution = 1、V5 native replay provider execution = 0；non-certified logical calls满足同一scripted native path；成功attempt没有transcript leftover；local duplicate work有记录但无persistent effect。失败fixture停在前一frontier并产生不可进入正式reducer的wasted/unconsumed evidence。

**Regression:** P1–P4；canonical-diff tests；至少一个 multi-call/retry fixture。

**Evidence:** canonical graphs/diff、oracle ledger、成功transition logical semantic work conservation table、失败attempt wasted-work ledger、local-overhead table、frontier/admission trace。

**Failure recovery:** 用 first differing native request/state event做最小定位，但不启动大型 first-divergence实验；判断是 dependency premise、binding还是test oracle问题，修最小层后重跑全部 P1–P5。

**Exit condition:** actual pinned native path的scripted semantic equivalence、logical work conservation和local-effect non-escape通过；不把live transport retry/batching一致性误设为定理前提。

**Next:** P6。

---

## 14. P6 — Existing Regression

**Objective:** 证明 V5与必要公共重构不改变 B0/B1、现有 v1.3 qualification、instrumentation、artifact与QA语义。

**Inspect:** v1.3 tests、相关 v1.2 tests、pyproject/test docs、changed files diff。

**Files likely involved:** existing tests；如果修改 `production_qa.py`/`production_runtime.py`，必须同目录新增 backward-compat tests。

**Hypothesis:** V5默认不安装、不影响 B0/B1；可选参数默认保留旧行为和序列化输出。

**RED:** 修改公共 API 前先写旧默认 behavior test和新 V5 method test；例如 `expected_methods=None`仍只接受 B0/B1，显式 V5 list才接受 V5。

另加 architecture regression：`membind_v5/runtime/core/**` import graphiti-specific module时失败；B0/B1未显式启用V5时不得安装binder/admission instrumentation。

**Expected failure:** 新能力 test失败，旧 behavior先保持绿；若旧 test先红，说明环境或非 V5 regression，先修环境。

**Minimal implementation:** 只做 RED 需要的向后兼容参数/纯函数抽取；不扩展 baseline `Method` enum，不更改 `FORMAL_METHODS`、workload或sealed结果格式。

**GREEN:** P1–P5、`saturated_fixed_work_baseline_v1_3/tests` scoped gate、受影响 v1.2/validation tests零失败；`git diff --check`通过。

**Regression:** 本阶段本身就是 regression；repo-wide collection仅作诊断并分类 missing optional deps，不作为 scoped gate替代品。

**Evidence:** exact commands、Python executable、pass/fail/skip counts、环境修复记录、git diff summary。

**Failure recovery:** 逐一分类 code regression / environment / flaky external；provider-free gate不得依赖 live provider。普通 test失败进入 autoresearch，不机械 STOP。

**Exit condition:** 所有 relevant scoped tests通过，任何 skip有明确非核心理由，无 baseline semantic diff。

**Next:** P7。

---

## 15. P7 — v1.3 Integration

**Objective:** 把 V5作为 v1.3 append-only extension接入 frozen 4-history protocol、QA和 reducer，保持 baseline定义与结果不可变。

**Inspect:** `formal_baseline.py::{build_lifecycle_evidence,reduce_baseline_outputs}`、v1.2 `execute_instrumented_block`/`live_block.py::traced_add`、`simple_campaign.py` extension pattern、production QA、AttemptStore、canonical projection、protocol configs。

**Files likely involved:** 新增 `membind_v5/live_block.py`、`membind_v5/campaign.py`、V5 scripts、`tests/test_membind_v5_live_block.py`、`tests/test_membind_v5_campaign.py`；仅在必要时最小改现有QA/reducer纯函数。不要把历史12-source simple qualification runner改造成formal runner。

**Hypothesis:** V5 campaign可验证并只读引用sealed formal baseline，运行同样4 histories/QA；只要V5 semantic preparation全部位于`FORMAL_START`之后、最后durable publication之前，且trace按原source完整归因，现有reducer即可用统一block wall-clock和work metrics输出B0/B1/V5 comparables。

**RED:**

- unsealed/tampered/wrong-commit/wrong-config/wrong-workload baseline root必须拒绝；
- V5不能写 baseline root；
- 少一个 history、QA或canonical graph时不能 seal；
- reducer混用不同resource identity时拒绝；
- method label被误当 B0/B1或改进原 baseline seal时失败。
- 任一preparation task/span早于`FORMAL_START`，或`T_build`只统计native `add_episode`/native suffix时拒绝；
- `timer_stop_ns/t_durable_complete_ns`没有紧随最终source的`PUBLICATION_DURABLE`、两者delta内仍有semantic work、validation落入build timer、每source trace envelope缺失/重复或trace按source聚合与logical ledger不一致时拒绝。

**Expected failure:** 当前没有 V5 extension campaign或三方法 reducer。

**Minimal implementation:**

1. 验证 `formal_run_seal.json`及 `qualification/baseline_results.json` hash/identity；
2. 在独立V5 root按相同history顺序运行4个V5 blocks；每个block先记录`FORMAL_START/timer_start_ns`，再创建任何semantic preparation task；
3. 最后一个source通过durability checkpoint并写最终`PUBLICATION_DURABLE`后，让共享runner按B0/B1相同位置记录`t_durable_complete_ns/timer_stop_ns`，按`timer_stop_ns-timer_start_ns`生成唯一`T_build`；记录event-to-stop delta并证明其中无semantic work，之后才执行canonical validation、QA与seal；
4. 每history使用相同4个read-only QA questions/evaluator；
5. 每source只输出一份覆盖PREPARE/NATIVE regions的trace envelope，并在reduce前交叉验证source coverage及trace/logical ledger聚合；
6. 读取baseline raw rows而非重算/覆盖它们；
7. 输出B0/B1/V5 `T_build`、tokens、LLM logical/transport、embedding/driver、work ratios、speedup vs B0、canonical V5-vs-B0、QA；
8. 全部完成后写V5 seal。

这里的正式protocol authority是v1.3的 `B0/B1 × 4 histories`（baseline 8 blocks）以及append-only的 `V5 × 4 histories`。历史 `B0-A/B0-B/B1` 12-source结果只用于qualification/diagnostic，不得被写入formal method matrix，也不得把其中 `255 logical calls / 572 embedding items` 当成full-live硬编码常数；正式gate比较同一history、同一workload下的logical work identity/ratio。

**GREEN:** scripted campaign fixture产生4个V5 rows并和8个baseline rows组成一致表；每个V5 row满足`build_makespan_ns = t_durable_complete_ns - t0_ns`，raw final publication event不晚于stop且其后无semantic work，全部semantic spans位于timer内、validation位于timer外，每source trace envelope恰好一份且source聚合与logical ledger一致；baseline tree hash前后相同；seal只在所有gate通过后出现。

**Regression:** P1–P6；formal baseline reducer golden test；append-only/seal tests；B0/B1原有timer、trace envelope和输出schema保持不变。

**Evidence:** baseline reference、combined results、canonical diffs、QA rows、per-source trace coverage/aggregation report、timer boundary assertions、V5 seal fixture。

**Failure recovery:** schema drift先读取真实 baseline result/seal，不手写猜测；必要时抽取纯 reducer helper并锁住旧 golden output。partial baseline（如 committed `20260822-002`）绝不可用作正式 reference。

**Exit condition:** provider-free/scripted v1.3 extension从CLI到reducer全链通过，source attribution与`T_build`定义可审计且baseline不可变。

**Next:** P8。

---

## 16. P8 — Minimal Live Qualification

**Objective:** 用最少 source验证真实 provider、Graphiti、Neo4j、instrumentation、transcript binding和durable publication，不先跑完整 campaign。

**Inspect:** P0 live inventory（重新采样）、tmux/service scripts、frozen endpoints、Neo4j canary、GPU/process、latest logs/artifacts。

**Files likely involved:** `scripts/run_v5_qualification.py`、tmux helper、独立 `artifacts/sfwb-v1-3-v5-minimal-<timestamp>/`。

**Hypothesis:** 1–2个frozen source足以暴露真实call-path/request-identity/provider/DB/instrumentation integration错误；通过后才有资格进入正式live。它不用于估计最终speedup或重做dependency论证。

**RED:** 先以 `--preflight-only`/scripted mode确认 live command拒绝错误 endpoint、busy conflict、invalid namespace、missing certificate；实际 minimal live在新 namespace启动前没有成功 seal。

**Expected failure:** 第一次真实调用可能暴露 provider JSON、Graphiti alias、Neo4j、request mismatch或instrumentation问题；这不是 STOP，而是 research loop输入。

**Minimal implementation:** 在与formal相同frozen backend/resource下，先记录`FORMAL_START/t0_ns`再对1–2个真实frozen sources运行V5；检查PREPARE/NATIVE均进入各自source scope、transcript被capture/consume、DB有episode/state、frontier/admission推进、最终`PUBLICATION_DURABLE`后由共享runner记录`t_durable_complete_ns/timer_stop_ns`、canonical export、logical/transport/embedding/driver events、attempt seal。minimal结果标为qualification，绝不进入论文主表；不以12-source历史绝对call/embedding数作gate。

**GREEN:** 进程正常结束；无mismatch/duplicate/missing/unconsumed；canonical/DB/publication与source count一致；每source恰好一个完整trace envelope、无null/错source span，trace聚合与logical ledger一致；`T_build=t_durable_complete_ns-t0_ns`且覆盖全部semantic work，raw final publication到stop之间无semantic work；instrumentation满足成功transition上的logical semantic work conservation并分别报告transport retries/local overhead；`C_admit`authority/coverage与three-class reserved-credit/non-bypass trace合法；fresh namespace清晰；artifact seal完整。

**Regression:** minimal前后运行 provider-free smoke；若修代码，必须回到受影响的最早 P phase并重跑 P1–P7。

**Evidence:** minimal attempt root/seal、tmux log、endpoint/GPU/Neo4j snapshots、binding summary、per-source trace coverage、timer/lifecycle assertions、frontier、canonical graph。

**Failure recovery:** 立即停止且只停止已识别的 invalid V5 process/session；保留 failure attempt，换新 namespace/output；最小复现→修复→RED/GREEN/regression→重启。不得 kill baseline、shared vLLM或未知GPU进程。

**Exit condition:** minimal live成功；如果 baseline正在占用合法固定资源，则 minimal live必须作为 gated queue步骤，不能与之并发，此时状态为 `QUEUED_WITH_GATED_MINIMAL`，不能谎称 minimal已成功。

**Next:** P9。

---

## 17. P9 — Legal V5 Live Launch / Queue

**Objective:** 在固定资源条件下将完整4-history V5 campaign合法排队或启动；startup sanity通过后结束本轮，不等待最终结果。

**Inspect:** 当前baseline seal/session/PID/output、是否已有queue、resource/backend identity、P8状态/seal（或待执行的gated-minimal step）、V5 command/output paths。

**Files likely involved:** `scripts/run_v5_campaign.py`、`run_v5_campaign_tmux.sh`、`queue_v5_after_baseline_tmux.sh`、`queue_manifest.json`、新 V5 campaign root。

**Hypothesis:** P9可按qualification状态选择三种互斥路径：已有P8 seal时立即启动full或只排队full；无P8 seal且baseline占用资源时，将`minimal → verify minimal seal → full`作为一个不可跳过的gated chain排队，从而既不并发污染，也不把“当前尚无P8 seal”误判为非法。

**RED:**

- 立即启动full V5或直接排队full campaign时，missing/invalid P8 seal必须拒绝；
- `QUEUED_WITH_GATED_MINIMAL`允许当前没有P8 seal，但queue中缺少minimal step、minimal seal验证、验证失败即停止或full对该gate的严格依赖时必须拒绝；
- 当前baseline仍在合法运行时允许其seal尚未生成，但queue必须绑定已识别的session/PID/output root，并在执行任何V5步骤前验证最终baseline seal；已有错误seal、无法识别的producer或无seal仍继续均拒绝；
- duplicate queue、同output root、resource/backend/workload mismatch必须拒绝；
- startup checker对立即exception或无artifact progress失败。

**Expected failure:** 当前可能已有baseline运行/排队，或committed partial root没有seal；此时不能直接启动full V5。若P8也尚未完成，只能生成带minimal强制前置gate的`QUEUED_WITH_GATED_MINIMAL`，不能伪装成`QUEUED`。

**Minimal implementation:**

- baseline运行且已有有效P8 seal：建立`QUEUED` session，等待baseline实际completion/seal，重新preflight/idle check后直接运行full；
- baseline运行且没有P8 seal：建立`QUEUED_WITH_GATED_MINIMAL` session，严格执行`verify baseline seal → recheck idle/resources → run minimal → verify minimal seal/identity → exec full`；minimal失败或无合法seal时queue写failure并停止，绝不启动full；
- baseline不运行且资源idle：必须先取得并验证P8 seal，随后才能在独立tmux立即启动full；不能以资源idle为由跳过minimal；
- 写queue manifest，包含mode、session、PID/command、顺序、baseline producer/reference、backend/resource/workload/config hashes、minimal/full output roots、每个completion gate及失败动作。

资格状态机固定为：

| P9 mode | 当前P8 seal | queue必须包含 | 是否可进入full |
|---|---|---|---|
| `RUNNING`（立即启动full） | 必须已有且有效 | 不适用 | 验证P8 seal后可以 |
| `QUEUED`（只排队full） | 必须已有且有效 | baseline seal/idle recheck → full | gates通过后可以 |
| `QUEUED_WITH_GATED_MINIMAL` | 当前可以不存在 | baseline seal/idle recheck → minimal → verify P8 seal → full | 仅新P8 seal验证通过后可以 |

**GREEN:** 两种合法终态之一：

1. `QUEUED` / `QUEUED_WITH_GATED_MINIMAL`：queue session真实存在，mode、顺序和gates正确，不与baseline并发；前者已有有效P8 seal，后者明确包含并强制验证minimal seal；确认后立即结束agent run；
2. `RUNNING`：full V5进程/session存在，endpoint/Neo4j/GPU符合protocol，无立即exception，artifact已建立且最初真实请求开始；确认后立即结束 agent run。

**Regression:** 启动前使用最后一次 green commit/test evidence；live期间不得修改同一 V5代码、复用namespace或启动其他 candidate。

**Evidence:** queue manifest（含mode与gate DAG）、P8 seal或planned minimal seal path/verification command、tmux pane command/capture、PID、resource snapshots、startup log、first artifact/event。

**Failure recovery:** startup立即失败则只停止 invalid V5，写 failure evidence，回 autoresearch和最早相关 phase，换 fresh output/namespace后重启，直到稳定进入 live。baseline/shared services不动。

**Exit condition:** 正确`QUEUED`、`QUEUED_WITH_GATED_MINIMAL`或稳定`RUNNING`；`QUEUED_WITH_GATED_MINIMAL`不要求当前已有P8 seal，但必须证明full无法绕过minimal seal gate。不等待几十分钟/数小时的最终结果。

**Next:** 下一轮读取 tmux/log/artifact/seal/reducer；本轮不做完整结果分析。

---

## 18. Autoresearch Protocol

所有非平凡问题执行：

```text
Observe
→ Form hypothesis
→ Inspect source/history
→ Search upstream/papers/code
→ Build minimal reproduction
→ Modify minimally
→ Test
→ Reflect
→ Continue
```

优先级：

1. **当前项目**：源码、git history、tests、V3.1/V4、v1.2/v1.3、artifacts、workplans、scripts、logs；
2. **上游实现**：pinned Graphiti、vLLM、Neo4j driver、当前依赖源码、issues/PR；
3. **外部高质量系统/论文及代码**：只吸收已验证机制，按 MemBind约束最小适配。

重要失败必须追加 research ledger：

```text
Symptom:
Observed evidence:
Hypothesis:
Relevant prior implementation / paper / code:
Minimal reproduction:
Root cause:
Change:
Validation:
What was learned:
Next action:
```

不要重复已经被同一证据否定的命令或方案。每次修改必须说明它改变了哪个假设，并从受影响的最早 RED重新跑。

### 18.1 可借鉴的外部 implementation patterns

| 来源 | 借鉴点 | 本轮不照搬的部分 |
|---|---|---|
| [MLIR Side Effects and Speculation](https://mlir.llvm.org/docs/Rationale/SideEffectsAndSpeculation/) / [LLVM MemorySSA](https://llvm.org/docs/MemorySSA.html) | 用具体 effects/dependencies证明 code motion合法；revision-pinned certificate | 不实现通用compiler IR/alias analysis |
| [Calvin](https://www.cs.princeton.edu/courses/archive/fall19/cos418/papers/calvin.pdf) / [ROCOCO](https://www.usenix.org/system/files/conference/osdi14/osdi14-paper-mu.pdf) | 预先确定依赖、ordered transaction/state transition | 不声明Graphiti拥有分布式transaction protocol |
| [Naiad](https://www.microsoft.com/en-us/research/publication/naiad-a-timely-dataflow-system-2/) / [Chardonnay](https://www.usenix.org/conference/osdi23/presentation/eldeeb) | monotonic frontier/versioned progress、异步work与ordered visibility分离 | 不构建通用dataflow engine |
| [rr](https://www.usenix.org/system/files/conference/atc17/atc17-o_callahan.pdf) | capture/replay必须精确、单次消费、fail closed | 不做全进程record-replay |
| [Parrot](https://www.usenix.org/conference/osdi24/presentation/lin-chaofan) / [ParrotServe code](https://github.com/microsoft/ParrotServe) / [Agentix](https://www.usenix.org/conference/nsdi26/presentation/luo) | LLM dataflow/semantic variable与runtime ordering；学习其代码组织 | 不把V5主张成通用agent scheduler |
| [vLLM](https://arxiv.org/abs/2309.06180) / [Sarathi-Serve](https://www.usenix.org/conference/osdi24/presentation/agrawal) | 共享serving容量、避免长prefill/priority work相互饥饿的调度教训 | 不修改vLLM，不做chunk/scheduler sweep |
| [Graphiti paper](https://arxiv.org/abs/2501.13956) / [Graphiti source](https://github.com/getzep/graphiti) | memory construction的原生operator/callsite和typed adapter | 不将Graphiti私有细节写成通用证明前提 |
| [MemForest](https://arxiv.org/abs/2605.23986) | parallel extraction/freshness说明“并行抽取”本身不足以构成V5 novelty | 不改变memory architecture；V5聚焦certified non-contiguous oracle motion与native semantic preservation |
| [Lazy View Maintenance](https://www.vldb.org/conf/2007/papers/research/p231-zhou.pdf) | source log与derived view/frontier解耦 | 不引入通用DB view-maintenance engine |
| [Text2Mem](https://aclanthology.org/2026.findings-acl.100/) | typed contract + backend adapter的软件边界 | 不把跨backend API specification当作V5执行贡献 |
| [LongMemEval](https://openreview.net/forum?id=pZiyCaVuti) | 保持既有long-term memory QA/correctness evaluation | 本轮不新建benchmark/QA framework |

技术问题使用 primary source/官方源码。论文引用的作用是指导具体机制与代码结构，不是堆 related work；若公开代码存在，优先读实现与tests。

---

## 19. Live / Tmux / Resource Coordination

进入任何 live phase 前，至少执行并保存等价证据（命令按机器实际环境调整）：

```bash
git status --short
git rev-parse HEAD
tmux ls
tmux list-panes -a -F '#S:#I.#P #{pane_pid} #{pane_current_command} #{pane_current_path}'
tmux capture-pane -pt <candidate-session>:<window>.<pane> -S -200
ps -eo pid,ppid,etimes,cmd
ss -ltnp
curl -fsS <llm-endpoint>/v1/models
curl -fsS <embedding-endpoint>/v1/models
nvidia-smi
```

另用项目已有 Neo4j canary/driver执行只读 service check。endpoint URL从 frozen config读取，不把上例占位符原样执行。

### 19.1 baseline 正在运行时

先确定：baseline method/history、tmux/session/PID、output root、seal/completion condition、backend/resource identity、已有queued tasks。不要看到GPU占用就 kill，也不要与 baseline并行启动 minimal V5。

合法 queue 顺序：

```text
current baseline reaches valid seal
→ recheck endpoints/GPU/Neo4j/idle/resource identity
→ V5 minimal live (unless already sealed under same revision/config)
→ minimal seal verified
→ full V5 campaign
→ V5 reducer/seal
```

queue script必须以 seal/hash为gate，不能只用 `wait <pid>`；进程异常退出但无合法 seal时不得自动开始 V5，应写 queue failure并停在 gate。V5成功加入队列并验证 manifest/session后，本轮立即结束，不等待 baseline结束或V5开始。

### 19.2 V5 可以立即运行时

P8通过后启动 full campaign，短暂观察：session/PID、backend endpoints、GPU memory/utilization、Neo4j、immediate exception、artifact root、最初logical request/frontier event。确认稳定进入真实执行后，本轮立即结束，不等待 final result。

### 19.3 live期间规则

- 不修改同一套 V5代码；
- 不启动另一个candidate或共享资源实验；
- 不复用失败 namespace/output；
- immediate bug时停止 **已确认的V5 process/session**，保留failure evidence后autoresearch；
- shared vLLM/Neo4j/baseline只有用户明确授权且protocol允许时才管理，不擅自重启/kill。

---

## 20. Failure Recovery and STOP Policy

以下均为普通 autoresearch 输入，不是 STOP：assertion、request mismatch、dependency判断错误、import/Python环境、network/sandbox、HuggingFace/GitHub、vLLM、Neo4j、provider endpoint、tmux、instrumentation、artifact、live startup或Graphiti行为与预期不同。

恢复顺序：

1. 保全当前失败 attempt/log/trace；
2. 停止且只停止 invalid V5进程；
3. 写 research ledger；
4. 用 provider-free 或最小 live reproduction定位；
5. 查项目历史 → upstream source/issues → external implementation；
6. 最小修改；
7. 从最早受影响 phase RED→GREEN→regression；
8. 新 namespace/output重新 minimal/live。

只有以下情况才形成真正 methodology failure report并 STOP：

1. pinned/目标可支持版本源码最终证明不存在任何 non-trivial state-independent semantic work；
2. exact native binding不可实现，提前计算必然改变native semantic path，且无可消除 correctness冲突；
3. 对同一 fundamental root cause完成多个相互独立fix，仍无新证据、无替代边界并进入明确死循环。

权限、缺失credential或用户控制的受保护资源可作为 execution authority blocker；此时报告精确所需authority，不假装methodology失败。

STOP report必须包含失败ledger、源码证据、尝试过的独立方案、为什么不能缩小 certified set/更换 seam、保留下来的 artifacts 与可恢复下一步。

---

## 21. Final Acceptance Criteria

### 21.1 implementation qualification

必须同时满足：

- [ ] V5 certified set精确到oracle effect/native callsite，并由pinned source + data/effect/control dependency capability tests证明；
- [ ] fresh namespace/single-writer/source-prefix/filter/order/limit/timestamp-tie的source-closure premises通过；
- [ ] preparation不访问 Neo4j/embedder/persistent state；
- [ ] preparation local effects不逃逸，parsed objects不进入native suffix/persistent state；
- [ ] logical identity与wire evidence分层；client-finalizer/cache-salt及transport env/config snapshot被pin；相同冻结输入的semantic wire hash稳定，seed/top-p漂移fail closed；binder不复制finalizer；
- [ ] exact identity、capture、retry、multiple call、single consume、duplicate/missing/mismatch/unconsumed tests通过；
- [ ] non-certified calls正确delegate；
- [ ] instrumentation安装顺序和成功transition上的logical semantic work conservation通过，transport retries/local overhead/失败attempt wasted preparation分开报告；
- [ ] PREPARE/NATIVE均在原source的`episode_scope`中执行；并发ContextVar不串包，无null/unattributed span，每source恰好一个完整trace envelope，trace按source聚合与logical ledger一致；
- [ ] V5 `timer_start_ns=t0_ns=FORMAL_START`、`timer_stop_ns=t_durable_complete_ns`并复用B0/B1 shared runner boundary；raw final `PUBLICATION_DURABLE`不晚于stop且其后无semantic work；全部semantic preparation/等待/native work计入`T_build`，validation/QA/seal位于timer外；
- [ ] `CapacityAuthority`证明V5从冻结`runtime.config.max_coroutines`机械建立统一`C_admit` envelope，当前authority值为8且与`graphiti.max_coroutines`一致，并覆盖全部delegated construction callsite；不得采用局部`SEMAPHORE_LIMIT=20`或vLLM `max_num_seqs`；
- [ ] construction cross-encoder coverage被证明为零，或该seam已纳入统一admission；
- [ ] predecessor/durable frontier、ordered publication、failure cancellation、`NATIVE_FRONTIER > FRONTIER_PREPARE > FUTURE_PREPARE`、reserved critical credit、future-preparation non-bypass与`C_admit=1`退化通过；
- [ ] generic core无Graphiti import；Graphiti revision-specific逻辑只在adapter/composition层；
- [ ] Native Serial vs V5 scripted canonical equivalence通过；
- [ ] relevant v1.3/v1.2 regression通过；
- [ ] baseline definitions、frozen workload/config、correctness、sealed artifacts未改变。

### 21.2 v1.3/live qualification

- [ ] V5 extension验证 sealed baseline reference；
- [ ] formal authority保持为baseline `B0/B1 × 4 histories` + append-only `V5 × 4 histories`；历史12-source `B0-A/B0-B/B1`不混入正式矩阵；
- [ ] 4-history campaign、相同QA、B0/B1/V5 reducer可以从scripted端到端运行；
- [ ] logical work gate按同history/request identities或ratio计算，不硬编码历史12-source的`255/572`；
- [ ] minimal live真实通过，或作为baseline之后的强制gated步骤正确排队；
- [ ] full V5合法启动或正确排队，不与baseline污染固定资源；
- [ ] `RUNNING/QUEUED`在进入full前已有有效P8 seal；`QUEUED_WITH_GATED_MINIMAL`可在排队时无P8 seal，但full严格依赖queue中新产生且验证通过的minimal seal；
- [ ] queue/startup artifact、session、commands、backend/resource/workload identity可审计。

本轮最终状态必须是：

```text
V5 implementation complete
AND TDD/relevant regression pass
AND v1.3 extension integrated
AND (
  status == QUEUED_WITH_GATED_MINIMAL
  OR status == QUEUED
  OR (status == RUNNING AND startup_sanity == PASS)
)
```

`QUEUED_WITH_GATED_MINIMAL`只表示由于合法baseline资源占用，`minimal → verify P8 seal → full`已按强制gate排队；当前可以没有P8 seal，且不能报告“minimal live succeeded”。普通`QUEUED`表示P8 seal已经存在，只等待baseline/resource gates后运行full。`RUNNING`表示P8 seal已验证且full已开始，只要求startup sanity，不要求等待完整4-history结果。

### 21.3 最终 handoff report

coding agent结束本轮前报告：

- 修改/新增文件与核心机制；
- 原样复用、重构、拒绝复用的部分及原因；
- correctness theorem及其source/test premises；
- exact test commands/results与任何环境修复；
- baseline reference/seal；
- v1.3 integration/reducer状态；
- live状态：`QUEUED_WITH_GATED_MINIMAL`、`QUEUED`或`RUNNING`；
- tmux session/PID/queue order；
- minimal/full artifact/output paths；
- backend/model/endpoint/resource/GPU/Neo4j identity；
- per-source trace attribution/coverage与`T_build`边界证据；
- RUNNING时的startup sanity证据；
- 下一轮应读取的log、attempt、seal和reducer位置。

---

## 22. Execution Order

严格按以下gate推进；普通失败回到受影响的最早phase，不跳过：

| 顺序 | Phase | 必须产出的 gate |
|---:|---|---|
| 0 | P0 Repository Qualification | revision/config/env/live inventory可审计 |
| 1 | P1 Semantic Dependency Certification | oracle-effect certificate + source/data/control closure + non-escape/abort policy + architecture boundary |
| 2 | P2 Transcript Capture | logical identity/wire separation + frozen-env wire determinism + per-source trace attribution + capture/retry/work accounting |
| 3 | P3 Exact Native Binding | strict native replay/single consume/delegate |
| 4 | P4 Version / Frontier Runtime | runtime-derived `C_admit` + unified coverage + three-class frontier priority + ordered publication/failure safety/liveness |
| 5 | P5 Scripted Serial Equivalence | actual Graphiti path canonical equivalence/logical work conservation |
| 6 | P6 Existing Regression | v1.3/relevant v1.2零回归 |
| 7 | P7 v1.3 Integration | sealed baseline reference + exact `T_build` lifecycle + trace-complete 4-history extension/reducer |
| 8 | P8 Minimal Live | real seam/source attribution/timer qualification，或合法gated queue |
| 9 | P9 Legal Live / Queue | mode-specific P8 gate正确；`QUEUED*`或稳定`RUNNING`后结束本轮 |

禁止倒置成“先实现完整 V5，再补测试”；禁止为了赶 live放宽 exact matching、允许 fallback、改 baseline定义或跳过 minimal qualification；禁止在 live运行中继续修改同一版本。

### 22.1 当前实施范围与投稿前泛化轨

第二个真实系统adapter **不是本轮 Graphiti V5 implementation/live 的前置gate**，否则会把当前可验证工程目标扩大成开放式系统搜索。但它是保持“general versioned memory maintenance runtime”投稿claim的重要证据。P9合法排队/启动后，下一研究轮执行source-only feasibility audit，不先实现candidate：

1. incremental ordered update；
2. 存在昂贵 external-oracle work；
3. 至少一个非平凡oracle sub-DAG不读取当前derived state；
4. 其后存在state-dependent merge/reconciliation；
5. 有可拦截且可精确标识的native callsite；
6. 有明确durable-completion/publication boundary。

每个候选只输出revision-pinned operator table与 `POSITIVE / NEGATIVE / OPAQUE` certificate。当前源码经验已提示：Mem0的extraction若包含`existing_memories`应给negative/state-bound certificate；LightRAG若原生已经并发extraction，必须先证明仍存在非平凡新增cut，不能当默认正例。可继续审计A-MEM、Cognee或其他满足六项标准的系统，但不得为了凑第二adapter放宽certificate。

投稿前分支规则：

- 找到正例并完成同样的source closure、exact binding和scripted serial-equivalence qualification：可保留一般“versioned memory maintenance”claim；
- 没有正例：收缩为“graph-memory construction runtime / Graphiti adapter”，不伪造泛化性；
- 第二adapter的benchmark/sweep不属于当前P0–P9 acceptance，也不能延迟本轮合法live。

最终执行哲学：

> **理论分析指导实现，源码验证理论假设，TDD保证实现正确，autoresearch解决工程问题，现有protocol负责最终验证真实性能。**
