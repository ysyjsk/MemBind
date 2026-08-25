# MemBind V7 Theory-First Methodology Workplan — Theory Freeze Candidate

## 核心纪律

- 顺序固定为：理论 → Graphiti refinement audit → assumptions/characterization → method selection → minimum implementation。
- CORE_THEORY_FROZEN 与 P7 schema freeze 前，只允许形式化、参考模型和只读源码审计。
- R3 Opportunity Gate 前，禁止 replay、read reuse、repair、speculative apply 或 treatment flag。
- 合法结果为 M0 Exact Replay、M1 semantic maintenance、M2 persistent transition 或 NULL。
- 复用 V6 runner/provider/proof/critical-path infrastructure；V5/V6 sealed artifacts 只读。

\[
\text{先证明 conditional correctness；再验证源码 contract；}
\]
\[
\text{再 characterize opportunity；最后实现唯一 minimum method。}
\]

# 0. Executive decision

## 0.1 Research question

\[
(S_{i+1},\tau_i)=Exec_{\Omega,\Gamma}(S_i,e_i)
\]

\[
StateDelta\rightarrow SemanticReadDelta\rightarrow DemandDelta
\rightarrow AffectedTransition\rightarrow CriticalPathImpact
\]

不预设 DCSR、DCIMT 或 Exact Replay。核心问题是：

\[
\Delta State+old\ witness+complete\ semantic\ lineage
\]

能否在完整 native request 重建前证明 semantic read 或 adaptive demand validity。

## 0.2 Legal outcomes

1. M1/M2：early delta-based validity sound、propagation local/reconvergent、online net benefit positive。
2. M0：只有 native exact request 后可 reuse；属于 exact response cache / speculation-plus-validation，novelty ceiling 低。
3. NULL/blocked：proof/refinement、zero-false-STABLE、locality 或 economics 失败。

不得转向 timestamp parallelism、KV cache、prompt compression 等无关优化。

## 0.3 Reviewer test

Memory-specific claim 必须：

- 在完整 native request 前判定；
- 使用 StateDelta、semantic witness 与 Dep lineage；
- 覆盖 demand existence、binding、semantic predecessor context 与 request；
- fresh current-state ground truth 上 zero false STABLE；
- 节省 state-dependent construction，而不只是 provider compute。

## 0.4 Final closure-review verdict

本轮六项建议经 formal consistency 与 pinned code/evidence 双重审查后的结论：

| Proposal | Verdict | Required correction / code fact |
|---|---|---|
| Native continuation congruence | ACCEPT，且为 M1 final-state correctness必要条件 | 必须使用 continuation-observable seam relation \(\equiv_{\alpha,K}\)，不能任意忽略 native tail读取的 UUID/order/effect key；Graphiti tail还含 bulk embedding、saga read/write与可选 community work |
| scoped StateDelta completeness | ACCEPT | completeness应属于 operator/region；否则 BM25 UNKNOWN会不必要地毒化 guarded exact cosine region；遗漏 relevant delta仍使依赖 region UNKNOWN |
| previous episodes as dependency | ACCEPT，pinned code直接确认 | `retrieve_episodes` 的 ordered result进入 node extraction/resolution、edge extraction/resolution、attribute extraction；不能当固定 episode input |
| SCA work denominator | ACCEPT | CP denominator会误判合法 cascade；改为同资源 work ratio，并把 CP只作为 bounded cascade impact |
| counterfactual CP | ACCEPT_WITH_CORRECTION | 必须重算 DAG并允许 path switch；若 costed graph已含 cert/repair，不得在 margin二次扣费。V6 reducer仅提供 timer/publication-chain/overlap discipline，现有 phase attribution不是 semantic DAG |
| provider-contract ReplayAllowed | ACCEPT，且收紧 V6 claim | V6 sealed evidence证明 exact request match/single consume，不证明 response在未记录 provider state下可重放；没有声明性 contract即 UNKNOWN/fresh response |

没有一项建议授权新增 runtime。它们只封闭 proof/refinement/measurement design；任何 P7 不支持的 seam/operator/contract均走 fallback、M0或 NULL。

# 1. Frozen project facts

## 1.1 Pins and evidence

- MemBind：[2832d94b56db72fcf993154bde47e16b31ade724](https://github.com/ysyjsk/MemBind/tree/2832d94b56db72fcf993154bde47e16b31ade724)
- Graphiti v0.29.3：[021d3a57d511f21b10adaf7fa923bd5c1fce5e9d](https://github.com/getzep/graphiti/tree/021d3a57d511f21b10adaf7fa923bd5c1fce5e9d)
- 6071bd76 是 development-exposed history，不是 blind holdout。
- QA INVALID_RETAINED 继续限制质量 claim。

V5 ceiling：

| Item | Value |
|---|---:|
| source-0 preparation | 206.530 s |
| ordered native | 1315.798 s |
| inter-native gaps | 0.187 s |
| summary/attributes | 697.13 s |
| node resolution | 519.50 s |
| edge resolution | 98.11 s |

V6 full-history：

- extraction capture/consume 92/92；
- misses 304/370；
- 全部为 missing_side，没有 aligned field mismatch。

missing_side 表示 shadow coverage 缺失，不表示 request drift。

Code/evidence audit：本地 `main` 与远端 `main` 均为上述 MemBind pin。V6 `critical_path.py` 只对 ordered native interval chain做 exact decomposition，phase spans明确 `overlap_safe=false`；V7 semantic counterfactual reducer是 observer/analyzer扩展，不是重用一个不存在的 exact V6 request DAG。

## 1.2 Graphiti facts

Native path：previous episodes → node extraction/search/resolution → edge extraction/adjacency/hybrid resolution → attributes/summaries → _process_episode_data → persistence。

Pinned-source persistence：

- _process_episode_data 把 embedder 传给 bulk helper；
- bulk tx 对缺失 node/edge embedding 调 embedder 后再写；
- Neo4j bulk core 的四类 writes 在同 execute_write callback；
- saga path 在 bulk 后仍可能 get/create、query previous episode 并追加 saves。

所以完整 tail 不是已有 closed Apply：

- M1 把等价 computation seam 交回 native continuation/publish；
- M2 才需 refinement embedding/bulk/saga 与 recovery；
- A11 初始为 UNKNOWN pending P7。

Semantic reads：

- node/edge cosine 是 filtered full-scan exact cosine top-k，不是 ANN；
- ORDER BY score 无 UUID secondary tie；
- edge hybrid 含 BM25+cosine+RRF；RRF 也无 secondary tie；
- 无 backend proof 时 BM25/hybrid/ANN 必须 UNKNOWN。

源码：

- [node cosine](https://github.com/getzep/graphiti/blob/021d3a57d511f21b10adaf7fa923bd5c1fce5e9d/graphiti_core/driver/neo4j/operations/search_ops.py#L128-L177)
- [edge search](https://github.com/getzep/graphiti/blob/021d3a57d511f21b10adaf7fa923bd5c1fce5e9d/graphiti_core/driver/neo4j/operations/search_ops.py#L232-L343)
- [RRF](https://github.com/getzep/graphiti/blob/021d3a57d511f21b10adaf7fa923bd5c1fce5e9d/graphiti_core/search/search_utils.py#L1763-L1779)
- [_process_episode_data](https://github.com/getzep/graphiti/blob/021d3a57d511f21b10adaf7fa923bd5c1fce5e9d/graphiti_core/graphiti.py#L680-L781)
- [bulk seam](https://github.com/getzep/graphiti/blob/021d3a57d511f21b10adaf7fa923bd5c1fce5e9d/graphiti_core/utils/bulk_utils.py#L128-L260)

# 2. Inherited theory and claim boundary

继承：

- [SAC](https://arxiv.org/abs/1106.0478)、[Adapton](https://matthewhammer.org/adapton/adapton-pldi2014.pdf)、[Nominal Adapton](https://research.cs.queensu.ca/home/jana/papers/noma/)：change propagation、memoization、stable names、FSC。
- [Differential Dataflow](https://www.cidrdb.org/cidr2013/Papers/CIDR13_Paper111.pdf)、[DBSP](https://www.vldb.org/pvldb/vol16/p1601-budiu.pdf)：delta/IVM。
- [Continuous Top-k](https://cse.hkust.edu.hk/~dimitris/PAPERS/SIGMOD06-Topk.pdf)：influence-region。
- [Aria](https://www.vldb.org/pvldb/vol13/p2047-lu.pdf)、[Spectrum](https://www.vldb.org/pvldb/vol17/p2541-zhang.pdf)：snapshot/OCC/ordered execution。
- [Parrot](https://www.usenix.org/conference/osdi24/presentation/lin-chaofan)、[Agentix](https://www.usenix.org/conference/nsdi26/presentation/luo)：agent dataflow/scheduling。
- [Speculative Actions](https://iclr.cc/virtual/2026/poster/10009726)、[CacheBlend](https://www.microsoft.com/en-us/research/uploads/prod/2024/09/eurosys25-final999.pdf)。
- [MemTX](https://arxiv.org/abs/2607.23929)、[MemTxn](https://arxiv.org/abs/2607.27834)、[Continuity Kernel](https://arxiv.org/abs/2608.11632)。

V7 不把 change propagation、DAG、OCC、top-k invalidation、speculation、transaction 或 KV reuse 包装成创新。

SAC 迁移缺口：

| Traditional premise | Agent-memory issue | V7 contract |
|---|---|---|
| addressed read | predicate/top-k/hybrid phantom | witness+affected domain+certificate |
| deterministic primitive | stochastic LLM control | frozen/live oracle split |
| complete DDG | hidden prompt/clock/index/existence | six-kind Dep closure |
| stable structure | dynamic entity/fanout | unique names; ambiguity fail closed |
| internal mutation | external ordered graph writes | Core vs M2 extension |
| deterministic FSC | live independent run differs | ReplayAdmissibility |

Claim axes：

\[
C=SoundCertificate\land AdaptiveDemandValidity
\land TraceFSC\land NativeContinuationCongruence
\]
\[
N=EarlyDeltaValidity\land SemanticOperatorExtension
\land AdaptiveLLMDemandComposition
\]
\[
V=PositiveNetBenefit,\qquad G=CrossSystemMapping
\]

M1/M2 positive systems claim 需 \(C\land N\land V\)。M0 可有 \(C\land V\)，但无 N。G 只用于 general-runtime claim；portable performance 需要第二 backend implementation。

# 3. Closed formal semantics

## 3.1 Domains and scope

- \(S\)：memory state；\(e\)：episode；\(\Gamma\)：code/prompt/schema/model/embedder/search/clock/backend epochs；\(\Omega\)：oracle；\(\tau\)：trace；\(P\)：M2 plan；\(\Delta\)：state delta。
- \(\pi_\rho(S,\Gamma)\) 含所有能影响 read 的 logical/physical inputs。

Core V7 限定：

\[
d=1,\qquad S_{i+1}=S_i\oplus\Delta_i
\]

不 claim multi-delta composition。\(d>1\) 需要 Delta Composition theorem 与新 gate。

## 3.2 Two theorem profiles

\[
BuildTrace_{\Omega,\Gamma}(S,e)\Downarrow(\tau,z)
\]
\[
NativeContinue_\Gamma(S,e,z)\Downarrow S'
\]

M1 证明 \(z_{maintained}\equiv_\alpha z_{fresh}\)，然后使用 native continuation。

M2 only：

\[
Stage(S,e)\Downarrow(\tau,P),\qquad Apply(S,P)\Downarrow S'
\]

| Method | Theory | Publication |
|---|---|---|
| M0 | exact request+artifact+ReplayAdmissibility | native |
| M1 | Core T1–T6b | native；需 P7 native-continuation refinement |
| M2 | Core T1–T6b + T7–T8 | staged adapter |
| NULL | none | native baseline |

## 3.3 Semantic partial-order trace

\[
G=(V,E,\eta,\nu,\lambda),\qquad \prec_G=E^+
\]

Nodes：Input、Read、Pure、Demand、Response、Control、M2 Plan。

Semantic edges：

1. data；
2. control；
3. existence；
4. ordered-collection；
5. environment/oracle；
6. effect/publication。

Async scheduling、coroutine completion、provider arrival order 不自动构成 semantic order。只有进入 prompt、candidate sequence、control 或 effect 的顺序才属于 correctness。

## 3.4 Semantic Dependency Closure

\[
Dep(v)=Dep_D\cup Dep_C\cup Dep_X\cup Dep_O\cup Dep_E\cup Dep_F
\]

\[
Stable(v,S,S')\iff
\bigwedge_{x\in Dep(v)}Can(x@S)=Can(x@S')
\land Identity(v)\ valid
\]

\[
ReadStable\Rightarrow Pure/ControlStable\Rightarrow
DemandStable\Rightarrow ResponseReusable\Rightarrow SubtraceStable
\]

遗漏 observable influence 是 A4 failure，不可用实验“没变”补洞。

## 3.5 Alignment

Stable name：

\[
(source,operatorClass,canonicalSubject,parentLineage,occurrence)
\]

\[
Align\in\{UNIQUE,OLD\_ONLY,NEW\_ONLY,AMBIGUOUS\}
\]

UNIQUE 需 name 与 operator/config 一一匹配；OLD/NEW_ONLY 是结构变化；AMBIGUOUS affected；missing_side 只算 coverage；alignment 本身从不返回 STABLE，也不要求 completion position 相同。

## 3.6 Witness, delta and certificate

\[
W=(kind,q,env,snapshot,result,domain,ranking,cutoff,ties,indexEpoch,proofData)
\]
\[
C(W,\Delta)\in\{STABLE,INVALID,UNKNOWN\}
\]
\[
C=STABLE\Rightarrow \rho(S\oplus\Delta,q)=W.result
\]

对 operator \(\rho\)，令 \(Obs_\rho\) 为能影响其结果及 consumer-visible order/projection 的全部 state observables。实际 native transition \(S\rightarrow S'\) 的 observable change set 与 delta decode为：

\[
Chg_\rho(S,S')=
\{x\mapsto(\pi_x(S),\pi_x(S'))\mid x\in Obs_\rho,\pi_x(S)\ne\pi_x(S')\}
\]

\[
Complete_\rho(\Delta;S,S')\iff
Chg_\rho(S,S')\subseteq Decode_\rho(\Delta)
\land ExactAfterValues_\rho(\Delta)
\]

Completeness要求没有 relevant omission；允许 conservative extra entries，因为它们最多造成 INVALID/UNKNOWN，不能造成 false STABLE。`Decode` 还必须覆盖 domain/order/tie/index/embedder/config epoch 等非对象字段。T2 再从 writer coverage与 primitive-local extractor correctness推出该 predicate及 abstract/native projection equality；因此定义不预设 T2 结论。

对 reuse region \(R\)：

\[
Complete_R(\Delta;S,S')=
\bigwedge_{\rho\in ReusedSemanticOperators(Dep(R))}Complete_\rho(\Delta;S,S')
\]

下文 transition \(S\rightarrow S'\) 明确时简写为 \(Complete_\rho(\Delta)\) 与 \(Complete_R(\Delta)\)。

INVALID/UNKNOWN 都只使其依赖 region fresh execute；无关 unsupported operator 不污染其他 region。若任何遗漏 delta 可能影响 reused dependency，则该 region 必须 UNKNOWN。Delta 包含对象/属性/endpoint/group/embedding/temporal/index/config/frontier changes；用 staged intent 与 post-backend diff 按 operator projection 验证 completeness。

## 3.7 Oracle and ReplayAdmissibility

\[
\Omega_f(q,\Gamma)=r,\qquad \Omega_l(q,\Gamma)\subseteq Response
\]

\[
ReplayAllowed(r,q,\epsilon_\Omega)\in\{true,false,unknown\}
\]

true 要求 exact request/model/config/schema/tool/policy epoch、artifact complete，且 provider 不依赖未记录 session/history/server/tool/stochastic context。

\[
Generated(r,q,\epsilon_\Omega)\land ReplayAllowed=true
\]

才授权 live reuse。这里的 true 只能来自 provider/deployment 的声明性 semantic contract；重复实验只能检查实现是否遵守 contract，不能从有限样本推出 contract。Contract 还必须排除 replay 重复触发外部副作用。证据不足时为 UNKNOWN：semantic read、demand validity 与 request construction仍可 reuse，但 response 必须 fresh provider call。Temperature 0/seed 不替代该 contract。Live claim 仅为 coupled serial legality。

## 3.8 Demand, affected set and reconvergence

Demand STABLE 需 existence/control、binding、semantic predecessor context、builder、inputs、canonical request 均稳定；Response live reuse 还需 ReplayAllowed=true。

`previous episodes` 默认是 state-dependent input，而非 stable episode input：其 retrieval result、window/order 与 prompt-visible projection必须表示为 ReadNode 或 InputStateDependency，进入 witness、\(Obs_\rho\)、Dep、demand validity 与 affected closure。只有 frozen transcript 或等价局部 proof 完全吸收其影响时，才可在该 region 消除此 dependency。

\[
Dirty_0=ChangedInputs\cup InvalidReads\cup UnknownReads
\cup ChangedEnv\cup AlignmentFailures
\]
\[
Propagate(v)\iff
Can(Output^{repaired}(v))\ne Can(Output^{old}(v))
\lor StructureChanged(v)
\]

动态 worklist每次 fresh repair一个 dirty node；仅当 `Propagate(v)` 时把 typed successors加入 worklist。令 \(A^\star\) 为最终实际 repaired/rebuilt nodes，令 \(P^\star\subseteq A^\star\) 为 output/structure确实变化并传播的 nodes。Fixed point是该 guarded transition system的 terminating least execution，不是无条件 successor transitive closure。

Affected control/existence 重建 branch。若 repaired node与 old node具有 unique name、same operator/env、identical canonical output、Dep fingerprint 与 semantic predecessor context，则在该点 exact reconvergence，不传播到 suffix；禁止 fuzzy match。

## 3.9 A15 well-foundedness

Trace bounded/finite，或 dependency relation 有 well-founded order；dynamic expansion 与 repair fixed point 必须终止。

## 3.10 Canonical / α-equivalence

\[
X\equiv_\alpha Y\iff Can(X)=Can(Y)
\]

Canonical form 忽略非语义 UUID、temporary ID、completion order，保留 entity/edge semantics、attributes、summaries、temporal/provenance、prompt/order-visible fields、demand binding/predecessors 与 logical effects。

Frozen harness 可额外做 raw strong equality；core theorem 始终使用 α-equivalence。

## 3.11 MaintainTrace

1. 验证 \(d=1\)、snapshot/env/delta。
2. 对齐 stable names。
3. 运行 typed certificates。
4. 将 changed/INVALID/UNKNOWN/env/alignment roots放入 deterministic worklist。
5. 在新 state fresh repair dirty node并比较 canonical output/control/existence。
6. 仅在结果变化时沿 typed Dep edges传播；相同则 exact reconvergence。
7. 迭代到 terminating fixed point，memoize untouched/reconverged suffix、重建 changed branch。
8. 返回 \(z'\) 给 native continuation。
9. M2 only：形成 \(P'\)、validate、ordered apply。

\[
MaintainTrace(BuildTrace(S,e),\Delta)
\equiv_\alpha FreshBuildTrace(S\oplus\Delta,e)
\]

# 4. Assumption registry

| ID | Assumption | Fail-closed consequence |
|---|---|---|
| A1 | selected BuildTrace 使用单一逻辑 snapshot | fresh native |
| A2 | maintained seam 前无 persistent side effect | seam 前移；失败则禁 M1/M2 |
| A3 | 对 selected operator，所有能改变 \(Obs_\rho\) 的 writers/primitives被拦截，且各 primitive 的 delta decode/after-value规则局部正确 | 无法 discharge则相关 \(Complete_\rho/Complete_R\) 为 UNKNOWN/fresh；遗漏可能影响 reused dependency时禁 reuse |
| A4 | 六类 Dep lineage complete | uncovered region affected |
| A5 | stable names unique/alignment unambiguous | alignment failure affected |
| A6 | pure/control/request builders deterministic under same env | changed env affected |
| A7 | 每个 STABLE read certificate 已证明 sound | disable certificate class |
| A8 | query/rank/filter/tie/backend epoch captured | read UNKNOWN |
| A9 | frozen oracle固定；live `ReplayAllowed=true` 只能来自 provider/deployment semantic contract，排除未记录 session/history/tool/server state 与重复外部副作用 | contract不足为 UNKNOWN，fresh response |
| A10 | canonical request/model/schema/tool/config/policy epoch identity exact；若复用 response，artifact必须完整 | request mismatch则 demand affected；artifact不足则 fresh response |
| A11 | M2 plan closed；Apply 无 hidden embed/search/control read | M2 blocked only |
| A12 | M2 Apply validates frontier/predicate/idempotency | abort/rebuild |
| A13 | M2 ordered publication atomic/recoverable | M2 blocked only |
| A14 | M2 journal/receipt gives logical exactly-once recovery | M2 blocked only |
| A15 | trace/expansion/repair well-founded and terminating | fresh native；method blocked if unbounded |
| A16 | Native continuation 的每个 local step只观察 seam contract \(K\) 与声明的 state/env/oracle；branch/pure/read/effect primitive在 α-ID bijection下 equivariant，且无 uncaptured field/read/timing/stale-state/hidden-effect dependency | 移动/扩充 seam；仍无法建立则 M1/M2 blocked |

Profiles：

- Core trace T1–T6：A1–A10、A15；final native-state closure另需 T6b/A16。
- M1：T1–T6b、relevant scoped assumptions、P7 operator 与 native-continuation refinement。
- M2 T7–T8：M1 core + A11–A14。
- M0：native exact request、A9/A10、ReplayAdmissibility。
- M1 不被 A11–A14 阻塞。

Assumption use-map（Theory Freeze 必须逐项无 orphan、无 implicit use）：

| Assumption | Used by | Kind / discharge point |
|---|---|---|
| A1 | T1、T6、T6b | execution/refinement；P7 snapshot audit |
| A2 | T1、T6 | seam/refinement；P7 write-fence audit |
| A3 | T2、T3、T5、T6 | operator-scoped delta；P7 writer map + R1 mutation tests |
| A4 | T4、T5、T6 | semantic lineage；P7 dependency audit + R1 perturbation |
| A5 | T4、T5、T6 | alignment；formal unique-name proof + R1 ambiguity tests |
| A6 | T4、T5、T6、T6b | deterministic pure/control/builder/continuation step；epoch audit |
| A7 | T3–T6 | certificate-class theorem；proof artifact + fresh oracle falsification |
| A8 | T3–T6 | operator observation/refinement；P7 contract/schema |
| A9 | T4 response corollary、T6/T6b reused responses；M0 | frozen oracle / declared live ReplayAdmissibility contract |
| A10 | T4、T6、T6b；M0 | exact canonical request；response reuse另需 complete artifact |
| A11 | T7 | M2 implementation refinement；closed plan audit |
| A12 | T7 | M2 OCC/apply refinement；stale-frontier tests |
| A13 | T7、T8 | M2 publication refinement；atomicity/fault audit |
| A14 | T8 | M2 recovery refinement；journal/receipt model check |
| A15 | T4–T6 | formal termination；well-founded measure + bounded repair test |
| A16 | T6b | native implementation refinement；P7 source proof + frozen differential |

不存在“实验重复一致即可解除”的 assumption。A9 的 live 部分必须由外部 provider/deployment contract 给出；R1/R5只检查本地 identity/artifact/epoch guard 是否忠实实现该 contract。

# 5. Core T1–T6b and M2-only T7–T8

## T1 Snapshot Soundness

Statement：

\[
BuildTrace(S_v,e)\Downarrow(\tau,z)
\Rightarrow \forall r\in Reads(\tau),snapshot(r)=v
\]

selected seam 返回前 state 仍为 \(S_v\)。

Proof sketch：对 BuildTrace derivation 结构归纳；Read 继承不可伪造 snapshot token，Input/Pure/Demand/Control 不写 persistent state，A2 排除 seam 前写。

Assumptions：A1、A2。

Graphiti refinement obligations：read bookmark/version、write fence、dual namespace/transaction、async boundary concurrent writer。

Counterexample：\(r_1\) 读 \(v\)，concurrent commit 后 \(r_2\) 读 \(v+1\)，二者组合构造 demand。

Fallback：seam 前移；仍混版则 M1/M2 blocked。

## T2 StateDelta Completeness

Statement for \(d=1\) and supported operator \(\rho\)：若 \(ExtractDelta\) 覆盖所有能改变 \(Obs_\rho\) 的 native mutation primitive 与 writer，且每个 primitive extractor局部正确，则对 \(S'=ApplyNative(S,\delta)\)：

\[
\Delta=ExtractDelta(S,\delta,S')
\Rightarrow Complete_\rho(\Delta;S,S')
\land \pi_\rho(S')=\pi_\rho(S\oplus\Delta)
\]

对 region \(R\)，只要求 \(Complete_R(\Delta)\)；该结论不蕴含无关 operator 的 completeness。

Proof sketch：对 \(Obs_\rho\) 投影逐项证明 supported insert/delete/property/endpoint/group/embedding/index/config primitive 的 extractor 保持实际 post-state 与 \(S\oplus\Delta\) 一致，再按有限 mutation sequence 组合。未建模 primitive、未记录 writer 或 backend-derived mutation使对应 \(Complete_\rho=false\)，而非扩大 theorem domain。

Assumptions：A3；所选 \(\rho\) 的 writer/primitive coverage 与 projection schema已经在 P7 封闭。A3 按 operator/region 实例化，不是全系统假设。

Graphiti refinement obligations：从 \(\rho\) 的 query/rank/filter consumer 反向枚举 \(Obs_\rho\)，再 source-audit 所有 reachable writes；用 mutation intent 与 post-backend diff交叉校验。删除任一 required delta field 的 mutation test 必须 RED。BM25/hybrid/ANN UNKNOWN 不得使已封闭的 exact-key/cosine region失效。

Counterexample：name 不变但 name_embedding 或 group 改变，delta 只记录 name。

Fallback：仅依赖不完备 projection 的 region 为 UNKNOWN/fresh；若无法定位影响边界，则其最小 enclosing region fresh。T2 不覆盖 multi-delta composition。

## T3 Semantic Read Certificate Soundness

Statement（general certificate）：

\[
C_\rho(W,\Delta)=STABLE
\Rightarrow \rho(S\oplus\Delta,q)=W.result
\]

Assumptions：A3、A7、A8。

Proof sketch：先由 T2 的 \(Complete_\rho\) 把实际 post-state read 转换为 \(S\oplus\Delta\) 上的 read；再按 certificate class 的 key/predicate/ranking不变量排除结果 membership、projection 与 order 的变化。证明义务没有闭合的 class只能返回 UNKNOWN。

### T3a Exact key/projection

Key/schema unchanged；无同 key insert/delete；无被读 field update。Missing-key 还需证明无同 key insert。

### T3b Predicate/adjacency

Old members 未删/未改 projection/order；delta object 不会进入 predicate；filter/group/time/order unchanged；phantom domain complete。

### T3c Exact top-k

共同前提：

- query vector/embedder epoch/filter/group/\(k\)/min_score/ranking/backend epoch unchanged；
- result member 未删、未变 noneligible，prompt-visible projection/rank unchanged；
- insert/delete/update、eligible transitions 全在 delta；
- total tie key，或无影响 membership/prompt order 的 tie。

Case 1：\(|K|=k\)。存在 kth cutoff \(\theta\)。所有 inserted/updated/newly eligible nonmember 严格排在 \(\theta\) 后，且无 boundary tie。

Case 2：\(|K|<k\)。没有 kth cutoff。必须证明没有任何新增/更新/newly eligible object 通过 filter 与 min_score；不能虚构 cutoff。

Graphiti guard：full-scan cosine可作为 exact candidate；相关 tie 因无 secondary order 而 UNKNOWN，除非证明 order 对 consumer 不可见。

### T3d BM25/hybrid/ANN

BM25 需 corpus/index epoch、analyzer/global stats、score bound、tie contract；hybrid 需每个 channel STABLE、union/fusion/tie稳定；ANN需 backend 原生 proof。缺任一项均 UNKNOWN。

Graphiti refinement obligations：证明 operator 实现与对应 formal key/predicate/ranking semantics一致；冻结 query/filter/rank/tie/index/embedder fields；current-state 同 operator/query/config fresh 重读，报告 3×2 STABLE/INVALID/UNKNOWN vs SAME/CHANGED confusion matrix。Fresh check验证实现前提，不证明 certificate theorem。

\[
FalseStable=0
\]

Counterexamples：short-result 插入过阈值对象；删除 kth member；embedding 更新越 cutoff；filter/group变化；score tie reorder；query-embedding epoch变化。

Fallback：不满足 \(Complete_\rho\)、contract 或 proof guard时仅该 read/region UNKNOWN→fresh。任一 false STABLE 永久禁用该 class，修复需新 campaign。

## T4 Adaptive Demand Validity

Statement：若 stable name unique 且 \(Dep(d)\) 中 existence/control、binding、semantic predecessor、builder 与 request inputs 均 STABLE，则 fresh trace 存在同 demand 且：

\[
q_d^{maintained}=q_d^{fresh}
\]

Frozen response可 exact reuse；live only if声明性 provider/deployment contract给出 ReplayAllowed=true，否则 semantic demand/request仍可 stable，但 response fresh call。

Proof sketch：按 semantic partial order 的 well-founded topological order 归纳。T3 建立 Read，determinism 建立 Pure/Control，Dep closure建立 existence/order/request，A10建立 exact request identity；response-reuse corollary再由 fixed oracle或 A9/A10 contract/artifact rule建立。

Assumptions：Demand validity使用 A4–A8、A10 的 request-identity部分与 A15；frozen response equality使用 fixed oracle；任何 live response reuse另加 A9 与 A10 的 artifact部分。ReplayAllowed UNKNOWN不阻止 read/demand/request maintenance。

Graphiti refinement obligations：capture prompt/schema/model/config、existence causes、binding、semantic predecessors、canonical request；previous-episode retrieval/window/order及其 extraction/resolution/summary consumers必须进入 Dep。Provider audit只验证实现符合已声明 ReplayAdmissibility contract；不得从 repeated agreement推断该 contract。以 frozen fresh BuildTrace comparison查 refinement bug。

Counterexample：source \(i\) commit改变下一 source 的 previous-episode window，进而改变 extraction prompt/demand，但 observer把 previous episodes当 stable input；或 provider依赖 hidden session history。前者直接使 T4/T6失败。

Fallback：相关 demand不能 early reuse；ReplayAllowed unknown时只 fresh response。

## T5 Dynamic Change Propagation / Reconvergence Soundness

Statement：对 reuse region \(R\)，若 \(Complete_R(\Delta)\) 且 Dep graph complete，则上述 guarded worklist从 changed/INVALID/UNKNOWN roots得到 terminating least repaired set \(A^\star\)。每个 \(A^\star\) 外 node stable；只有 repaired canonical output/control/existence改变才传播；满足 exact reconvergence条件的 suffix可复用。

Proof sketch：按 well-founded semantic order归纳。若未 repair node改变，则必有自身 root/env/alignment变化，或某 typed predecessor的 canonical output/structure改变；前者应在 \(Dirty_0\)，后者触发 `Propagate`并入 worklist，矛盾。若 repaired node输出与 old canonical-equal，则所有只经该 output依赖的 successors观察不到变化，memo-free execution与 memo reuse在此重新汇合。A15保证 worklist/repair终止。

Assumptions：region-scoped A3、A4–A8、A15。

Graphiti refinement obligations：dependency perturbation、branch add/delete、empty candidate、one-to-many、duplicate edge、global invalidation与 previous-episode window变化；fresh trace edit；zero false-unaffected。UNKNOWN operator只扩散到依赖它的 enclosing region。

Counterexample：无论 repaired output是否相同都无条件传播会失去合法 reconvergence；反之只比较 set membership而漏 prompt-visible order，会错误停止。另一个 failure是 repair每次创建未命名 demand、无 finite bound，fixed point不终止。

Fallback：ambiguous/nonterminating region fresh native；false-unaffected淘汰 method。

## T6 Trace From-Scratch Consistency

Statement（frozen）：

\[
MaintainTrace(BuildTrace_{\Omega_f}(S,e),\Delta)
\equiv_\alpha FreshBuildTrace_{\Omega_f}(S\oplus\Delta,e)
\]

Statement（live）：

\[
\exists\Omega_c\preceq\Omega_l:
MaintainTrace(BuildTrace_{\Omega_l}(S,e),\Delta)
\equiv_\alpha FreshBuildTrace_{\Omega_c}(S\oplus\Delta,e)
\]

\(\Omega_c\preceq\Omega_l\) 表示每个 oracle choice逐 demand合法；reused response还需 ReplayAllowed=true，否则 fresh execute。

Proof sketch：采用 SAC memo-free counterpart；展开 reused nodes；T3/T4证明 stable nodes等于 fresh；T5确保所有可变 nodes重算/branch重建；按 well-founded trace归纳得到 canonical seam-output equality。

Assumptions：A1–A8、A10 的 request-identity部分、A15、T1–T5，以及被 reuse 部分的 \(Complete_R(\Delta)\)；frozen response使用 fixed oracle，任何 live reused response另加 A9与 complete-artifact guard。全部 response fresh时不要求 ReplayAllowed=true。

Graphiti refinement obligations：frozen full BuildTrace vs MaintainTrace canonical trace/seam output；固定 UUID 时额外 raw equality。T6只闭合 BuildTrace/seam output，不声称 native final state；后者属于 T6b。

Counterexample：candidate set相同但 prompt-visible order改变，raw set comparison误判 stable。

Fallback：canonical mismatch/open proof obligation阻止 M1/M2。

## T6b Native Continuation Congruence

Statement：令 \(\equiv_{\alpha,K}\) 是 seam-specific canonical relation，其中 \(K\) 保留 native continuation可观察的 logical IDs/bijection、ordered collections、effect/idempotency keys 与 prompt-visible fields。对两个隔离执行中 canonical-equivalent、同 frontier/version 的 authoritative post-delta states \(S'_1\equiv_\alpha S'_2\)、相同 episode/environment \((e,\Gamma)\) 及 frozen/coupled-legal oracle：

\[
S'_1\equiv_\alpha S'_2\land z_1\equiv_{\alpha,K}z_2
\Rightarrow
NativeContinue_{\Omega,\Gamma}(S'_1,e,z_1)
\equiv_\alpha
NativeContinue_{\Omega,\Gamma}(S'_2,e,z_2)
\]

因此 T6 与 T6b合成后，M1 maintained execution的 native final state与 from-scratch execution一致。普通 \(\equiv_\alpha\) 只有被证明是 continuation congruence时才可替代 \(\equiv_{\alpha,K}\)。

Proof sketch：对 P7 冻结的 native continuation control-flow derivation归纳。每个 branch/read/oracle/effect只依赖对应 \(S'_j,e,\Gamma,\Omega\) 与 \(K\) 中字段；pure step保持 relation；semantic read在等价 authoritative snapshots得到等价结果；effect通过一致 logical-ID bijection与 ordered/idempotent keys产生 α-equivalent writes。组合各 step得到 final-state congruence。隔离 replicas避免第一次 comparison run污染第二次。

Assumptions：A1、A6、A10 的 request/epoch部分、A16；continuation 使用与 theorem profile一致的 snapshot/oracle语义，且 \(K\) 对全部 continuation-observable fields complete。Frozen response使用 fixed oracle；任何 live reused response另需 A9与 complete artifact。A16只给 step-local equivariance/closed-observation obligations，T6b通过组合证明 whole-continuation congruence，故不循环。

Graphiti refinement obligations：逐项审计 `_process_episode_data`、`add_nodes_and_edges_bulk` embedding、episodic/entity edge IDs、saga get/create/previous/NEXT_EPISODE/HAS_EPISODE、optional community update、clock/backend epoch及异常路径；排除 runtime object identity、uncaptured mutable field、completion timing、stale state、hidden semantic read/oracle/effect。用 frozen differential验证实现映射，但不以相似输出代替 proof。

Counterexample：canonicalizer忽略 temporary UUID，但 native tail用该 UUID建立 edge，两个 seam output虽普通 α-equal却写出不同 endpoint；或 saga previous-episode query在 continuation读取不同 frontier。

Fallback：扩大 \(K\)、把 hidden dependency纳入 seam/Dep，或前移/后移 seam；仍不能证明则 M1/M2 blocked。不得用 empirical final-state agreement代替 congruence。

## M2-only T7 Staged Plan Applicability / Ordered Publication

Statement：若 \(P_i\) 含 source/base frontier/snapshot/predicate preconditions/closed effect/idempotency，且仅在 current frontier \(i-1\) 与 preconditions成立时提交，则 ordered final canonical state等于 serial spec。

Proof sketch：对 source index归纳；frontier保证 predecessor state，T6保证 plan semantics，closed apply产生同 next state；failure只 abort。

Assumptions：Core T6、A11–A13。

Graphiti refinement obligations：embedding必须在 Stage 前置或成为 plan input；bulk core与 saga get/create/previous/write必须完整捕获；adapter只封闭 native primitives，不重写 Graphiti algorithm。

Counterexample：Apply中用当前 embedder生成缺失 embedding；或 saga previous episode在 Apply重查后改变 NEXT_EPISODE target。

Fallback：M2 blocked，M1继续 native publish。

## M2-only T8 Crash Safety / Recovery

State machine：

\[
ABSENT\rightarrow PREPARED\rightarrow CERTIFIED
\rightarrow APPLYING\rightarrow COMMITTED
\]

Statement：任意单点 crash 后为完整 pre-state 或 post-state，无 partial visibility、double logical effect 或 frontier/data split。

Proof sketch：durable journal、idempotency key、atomic receipt不变量；无 receipt rollback/rebuild，有 receipt idempotent finish/no-op。

Assumptions：T7、A13、A14。

Graphiti refinement obligations：每个 bulk/saga/frontier point crash injection；restart graph/frontier/journal digest；bounded interleaving model。

Counterexample：bulk已 commit，saga edge未写但 frontier已推进时 crash。

Fallback：M2 blocked；不影响 M1 native durability claim。

# 6. Theory phases and freeze

## 6.1 Fixed order

- P0 Formal Native Semantics：partial order、α-equivalence、Core/M2 profiles。
- P1 State/Delta/Projection：\(d=1\)。
- P2 Certificate calculus。
- P3 Dep closure。
- P4 Adaptive Demand + ReplayAdmissibility。
- P5 Change propagation/reconvergence + A15。
- P6 Core T1–T6 trace FSC。
- P6c T6b native-continuation congruence contract（在 P7 源码 refinement 前保持 conditional）。
- P6b M2-only T7–T8 conditional theory。

## 6.2 Artifacts

- theory/semantics.md
- theory/assumptions.yaml（A1–A16/profile/use/fallback）
- theory/proofs/T1_T6b_core.md
- theory/proofs/T7_T8_m2_extension.md
- theory/counterexamples.md
- theory/reference_model/
- theory/model_check/
- theory/related_work_matrix.md

## 6.3 Gates

CORE_THEORY_FROZEN：

- T1–T6b conditional proofs closed，T6b Graphiti congruence obligation显式，scope \(d=1\)；
- relevant scoped A1–A10/A15/A16 registered、无 orphan/implicit assumption；
- partial order、Dep、α-equivalence、frozen/live/ReplayAllowed frozen；
- UNKNOWN first-class；
- counterexamples/reference differential/proof audit complete；
- no-treatment CI guard。

M2_EXTENSION_THEORY_FROZEN：

- T7–T8 conditional proofs；
- A11–A14；
- plan/apply/crash model invariants。

P7 在 conditional core proof完成后 discharge Graphiti refinement obligations；只有 T6b/A16 与 selected operator contract得到合格 status，M1 才进入 R1。M2 未 freeze只排除 M2，不阻塞 M0/M1/NULL。不得先写 runtime 再补 proof。

# 7. P7 Graphiti Refinement / Operator Contract Audit

Theory freeze 后、observer schema 前执行；这是只读 source/refinement audit，不是 treatment。

## 7.1 Status

每个 formal operator 只能：

- SUPPORTED；
- SUPPORTED_WITH_GUARD；
- UNSUPPORTED；
- UNKNOWN。

不得修改 correctness definition 迎合 Graphiti。

必审：

- exact/missing key；
- pair/adjacency/predicate；
- node/edge cosine top-k；
- BM25、hybrid/RRF、ANN；
- summary/attribute dependencies；
- timestamp existence/control；
- embedder query/stored vectors；
- _process_episode_data、bulk transaction、saga；
- native continuation seam；
- previous-episode retrieval → extraction/resolution/attribute-summary/prompt/control 的完整依赖；
- provider/deployment ReplayAdmissibility声明与本地 guard 的符合性；
- M2 Stage/Apply seam。

Starting facts：

| Unit | Known fact | Initial status |
|---|---|---|
| node cosine | exact filtered full scan；no secondary tie | SUPPORTED_WITH_GUARD candidate |
| edge cosine | exact filtered full scan；no secondary tie | SUPPORTED_WITH_GUARD candidate |
| BM25 | index/stats/tie proof unavailable | UNKNOWN |
| hybrid/RRF | BM25+cosine+RRF；tie unresolved | UNKNOWN |
| bulk core | may embed before transactional writes | not closed Apply |
| saga tail | post-bulk reads/writes | UNSUPPORTED as closed Apply without adapter |
| M1 seam | output may return to native tail | refinement candidate |
| previous episodes | `retrieve_episodes` result流入 node extraction/resolution、edge extraction/resolution与 attribute extraction | state-dependent dependency；SUPPORTED as mandatory Dep input |
| native tail | `_process_episode_data` bulk write；saga可再 query/write；community update可有 LLM/read/effect | UNKNOWN pending seam-specific congruence audit |
| provider replay | V6有 exact request identity/single-consume evidence，但不是 provider semantic contract | UNKNOWN pending declared deployment contract |

FormalExactTopK 必须映射 domain、query vector/embedder epoch、score numeric semantics、threshold、\(k\)、\(|K|\)、tie、delta fields 与 consumer order visibility。

## 7.2 Outputs

- theory/graphiti_refinement.md
- theory/operator_contracts.yaml
- theory/unsupported_operators.md
- theory/previous_episode_dependency.md
- theory/native_continuation_refinement.md
- theory/provider_replay_contract.md
- schemas/required_observation_fields.json
- P7_REFINEMENT_STATUS.json

这些 sealed 后才冻结 observer schema。

三项专项审计的最小内容：

1. `previous_episode_dependency.md`：列出 retrieval selector（reference time、last_n、group/source、explicit UUIDs）、ordered result/window/projection、所有 direct/transitive consumers、Dep edge kind、witness/delta fields、可消除 dependency 的局部 proof与反例。
2. `native_continuation_refinement.md`：对每个候选 seam 列 continuation-observable \(K\)、read/oracle/effect/clock/backend依赖、ID bijection、异常与 retry；给出 SUPPORTED / SUPPORTED_WITH_GUARD / UNKNOWN / UNSUPPORTED。不能证明则移动 seam或阻止 M1。
3. `provider_replay_contract.md`：记录 contract authority/version、request identity coverage、artifact completeness、session/history/tool/server state、外部副作用和 policy epoch。没有声明性依据时必须 UNKNOWN；重复实验不升级 status。

## 7.3 Theory ↔ experiment closure

| Theory | Guarantee | Refinement condition | Field | Check | Falsifier | Fallback | Consequence |
|---|---|---|---|---|---|---|---|
| T1 | one snapshot | seam前同 version/无 write | bookmark/version/write event | concurrent writer | mixed version | fresh native | M1/M2 blocked |
| T2 | scoped complete delta | selected \(Obs_\rho\) mutations mapped | operator/projection/before/after/epoch | backend diff | omitted relevant change | region UNKNOWN | only dependent region fresh |
| T3 top-k | STABLE=same result | exact domain/score、short/full、no tie | vector/filter/K/k/cutoff/ties | fresh reread | false STABLE | UNKNOWN | operator out of M1 |
| T4 | same demand | complete Dep+builder；response reuse另需 declared ReplayAllowed | previous episodes/existence/binding/predecessors/request/contract id | frozen fresh trace + contract-conformance check | demand mismatch/contract gap | rebuild/fresh response | request可 stable，response不 replay |
| T5 | sound affected set | complete well-founded partial-order lineage | typed edges/names/iterations | trace edit | false unaffected/nontermination | fresh region | suffix reuse blocked |
| T6 | Core FSC | observations refine formal nodes | canonical trace/seam output | frozen differential | canonical mismatch | fresh BuildTrace | M1 blocked |
| T6b | native final-state congruence | seam \(K\) complete、continuation无 hidden influence | seam fields/ID map/read-oracle-effect/state epoch | source refinement + frozen differential | noncongruent continuation | move seam/fresh native | M1/M2 blocked if unresolved |
| T7 | M2 ordered apply | closed embedding/saga plan | plan/frontier/effects | stale/fault schedule | hidden read | native publish | M2 only blocked |
| T8 | M2 recovery | recoverable data/frontier/journal | receipt/crash/digests | crash injection | partial/double apply | native publish | M2 only blocked |

实验不证明 theorem；只验证 refinement assumptions、找 implementation bug、测 opportunity/economics。

# 8. Theory-derived hypotheses

Correctness assumptions：

- snapshot/seam/delta/Dep/alignment/certificate/replay/well-foundedness 可验证；
- M2 only：closed Apply 与 crash recovery 可验证。

Opportunity：

- MutationLocality；
- Read/Demand/Request/Structural Stability；
- bounded AffectedFraction/Depth/Fanout；
- reconvergence；
- CSP nonzero；
- SCA low enough；
- offline margin足以授权 minimum runtime；
- later online NetBenefit positive。

阈值、unit、aggregation、falsifier 在 R0 预注册，不能 R3 后倒推。

# 9. R0–R3 observer-only characterization

## R0 Freeze

- seal code/dependencies/model/prompt/config；
- import V5/V6 facts；
- require P7 status；
- freeze schema from required_observation_fields.json；
- freeze cost formulas、thresholds、stopping rules；
- CI blocks all treatment。

Exit：CORE_THEORY_FROZEN、P7 sealed、V6 regression green、observer-off native-equivalent。M2 candidate 还需 extension freeze。

## R1 Assumption audit

Capture：

- snapshot/bookmark/version/write events；
- read operator/\(Obs_\rho\)/query/filter/limit/rank/tie/index epoch/witness、\(Complete_\rho/Complete_R\) status；
- previous-episode selector/window/order/projection/result digest及到 extraction/resolution/summary/prompt/control 的 typed edges；
- demand existence/binding/request/semantic predecessors；
- six dependency edge kinds；
- raw IDs + canonical map；
- seam-specific continuation-observable \(K\)、logical-ID bijection、native read/oracle/effect epochs；
- provider/deployment contract authority、version与 `ReplayAllowed` status；实验 agreement不得作为 contract evidence；
- mutation intent + post-backend diff；
- provider/token/GPU/read/embedding cost；
- semantic critical-path events。

RED tests：

- observer_off_native_equivalence；
- mixed_snapshot/write_fence；
- missing_delta_field；
- unrelated_unknown_operator_does_not_poison_scoped_region；
- missing control/existence/ordered/environment edge；
- previous_episode_window_change_reaches_demand；
- ambiguous name；
- async_completion_order_not_semantic；
- alpha_equivalence_ignores_runtime_uuid；
- topk_tie_unknown；
- bm25/ann_unknown_without_contract；
- replay_disallowed_on_hidden_session_state；
- replay_contract_cannot_be_inferred_from_repetition；
- native_continuation_observes_ignored_id；
- nonterminating_trace_rejected；
- M2 only：closed plan/frontier/crash invariants。

ASSUMPTION_STATUS.json 对每个 assumption/operator/region记录：SUPPORTED、SUPPORTED_WITH_GUARD、UNKNOWN、UNSUPPORTED，并附 contract/source/test evidence与 affected theorem。

- Scoped A3/A7/A8 failure只使依赖 region fresh；无法界定边界才扩大 fallback。其他 Core failure blocks corresponding M1/M2。
- ReplayAllowed failure only forces fresh response.
- A11–A14 failure blocks M2 only.
- safe observer may continue negative characterization.

## R2 Two-source causal trace

Run source 1 on old \(S_0\) and fresh current \(S_1=S_0\oplus\Delta_0\) with frozen oracle for structure/request ground truth; live observer only measures costs.

Report：

- MutationLocality；
- old/new witness/read equality；
- demand add/remove/binding/request；
- semantic partial-order edit；
- affected closure/depth/fanout；
- first semantic divergence/reconvergence；
- critical-path position；
- certificate prediction/ground truth。

Do not count completion-order differences. Treat embedder/model/config/clock/provider history as inputs. missing_side is coverage only.

## R3 Two independent six-source blocks

Observer-only; histories/seeds preregistered. Instrumentation bug invalidates the block and requires a new campaign.

Fresh ground truth：

1. Read：current-state fresh same operator/query/config，exact result/order。
2. Demand：frozen current-state fresh BuildTrace，compare existence/binding/semantic predecessor/request。
3. Affected set：old/fresh canonical partial-order trace edit。
4. Seam/plan：canonical semantic output/effect；不 publish treatment。

Confusion matrix：

Prediction STABLE/INVALID/UNKNOWN；truth SAME/CHANGED。

\[
FalseStableRate=\frac{\#(STABLE,CHANGED)}{\#STABLE}=0
\]

若 \(\#STABLE=0\)，报告 undefined。

Existing metrics：

- state：MutationLocality、delta bytes/objects/type/epoch；
- read：ReadStability、certificate coverage、operator-weighted span；
- demand：RequestStability、DemandStability、early-certifiable vs exact-only span；
- structure：StructuralStability、AffectedFraction、Depth/Fanout、ReconvergenceRate、CertifiableStableSpan、ReplayableSpan、RepairSpan；
- resources：provider calls、tokens、GPU seconds、embedding/read/CPU/DB work。

## 9.1 Certifiable Stable Portion

令 \(W^{state}_{CP}\) 为 state-dependent critical-path union，\(W^{cert}_{CP}\) 为仅凭 delta+witness+lineage、在 full native request 前证明 stable 的 union：

\[
CSP=\frac{W^{cert}_{CP}}{W^{state}_{CP}}
\]

Rules：

- critical-path union，不按 count；
- overlapping spans只计一次；
- denominator 0 为 N/A；
- duration 与 GPU/read/provider work分开；
- exact-request-only span不属于 CSP。
- CSP 是 baseline state-dependent critical-work coverage，不是 speedup。实际 opportunity 必须由 9.3 的 counterfactual DAG recomputation给出；关键路径切换时 CSP 可高而 SavedCP 仍低。

## 9.2 Semantic Change Amplification

令 \(D\) 为直接 INVALID semantic reads，\(A^\star\) 为归因后的 affected construction closure；\(Work_r(X)\) 是 resource \(r\) 上去重后的 additive work。对每个 \(r\in\{active\ latency/work,provider\ tokens,GPU\ seconds,read/embedding,CPU/DB\}\) 且 \(Work_r(D)>0\)：

\[
SCA^{r}_{work}=\frac{Work_r(A^\star)}{Work_r(D)}
\]

Work-level SCA 描述 small direct semantic change 是否放大成 large adaptive construction cascade；不同资源单位不得相除或合并成一个 ratio。

关键路径只报告 bounded cascade impact：

\[
CascadeImpact_{CP}=
\frac{AffectedCriticalSpan}{TotalStateDependentCriticalSpan}
\]

同时单独报告 `DirectInvalidatedCriticalSpan`，但不把它作为 SCA denominator。另报告 bounded：

\[
CascadeShare_r=
\frac{\max(0,Work_r(A^\star)-Work_r(D))}
{TotalStateDependentWork_r}
\]

- \(DirectWork=0,AffectedWork=0\)：no observed change；SCA undefined。
- \(DirectWork=0,AffectedWork>0\)：先审计 hidden dependency/delta/lineage；若 root 是合法 changed control/environment/episode input，则重新归因到该 root class并报告 absolute cascade，不能自动称 infinite amplification或 correctness failure。
- 小 denominator：同时报告 absolute direct/affected work、`CascadeShare_r`、block consistency与 sensitivity，不以巨大 ratio单独决策。
- wall critical span、provider token、GPU seconds、read/embedding分别报告；只有 wall DAG 使用 CP。

低 SCA/快速 reconvergence 支持 incremental opportunity；高 SCA 表示 cascade。

## 9.3 R3 Opportunity cost and statistics

R3 无 treatment，不能测真实 shadow、runtime interference、repair scheduling 或 actual removal。

对 P7/R1 冻结的 semantic DAG/partial order \(G\) 与 wall cost \(w\)，以 topological longest path定义：

\[
CP(G,w)=\max_{p\in Paths(G)}\sum_{v\in p}w(v)
\]

对 method \(m\) 构造两个不能混用的 counterfactual：

1. `Gross graph` \(G_m^{gross}\)：soundly stable且可在 seam 前 reuse 的 original work置零；affected/UNKNOWN/fresh nodes保留 conservative original cost。`GrossSavedCP_m=CP(G,w)-CP(G_m^{gross},w_m^{gross})`。
2. `Costed graph` \(G_m^{costed}\)：在 gross graph上显式加入 certificate、validation、repair/reexecution nodes及其 precedence，得到 `NetSavedCP_m=CP(G,w)-CP(G_m^{costed},w_m^{costed})`。若成本只可给 bound，使用 upper-bound nodes。

两者都重新运行 longest-path reducer，允许 critical path切换并自然避免 overlapping-node double count。若使用 `NetSavedCP`，不得再次扣 certificate/repair；Gate 为便于保守预注册使用 gross口径并只扣一次成本：

Report conservative：

- \(LB_{blocks}(GrossCounterfactualSavedCP)\)；
- \(UB(EstimatedCertificateCriticalCost)\)；
- \(UB(EstimatedRepairCriticalCost)\)；
- RequiredOnlineHeadroom。

\[
OfflineOpportunityMargin=
LB(GrossCounterfactualSavedCP)
-UB(CertificateCriticalCost)
-UB(RepairCriticalCost)
\]

\[
OpportunityEligible\iff
OfflineOpportunityMargin>RequiredOnlineHeadroom
\]

Implementation obligation：保留 V6 reducer的 timer、ordered publication chain、interval overlap checks与 sealed-input discipline；新增 semantic DAG longest-path reducer和 synthetic tests（overlap、fork/join、critical-path switch、UNKNOWN/repair upper bound）。V6 `phase_attribution` 已明确 `overlap_safe=false`，不能直接当 DAG。Provider calls/tokens、GPU-seconds、read/embedding/CPU/DB 是独立 resource-demand counterfactual；除非另有 service/scheduling model，不把它们伪装成同一条 CP。

两个 blocks只用于 characterization/operator selection/counterexamples/bounds。报告 raw distributions、exact descriptive stats、deterministic bounds、block consistency、leave-one-source-out/sensitivity；不作 publication-level CI/significance。

R3 artifacts：

- PROPAGATION_MATRIX
- CERTIFICATE_CONFUSION
- AFFECTED_SET_ORACLE
- CSP_SCA
- CRITICAL_OPPORTUNITY
- WORK_AMPLIFICATION
- R3_DECISION_INPUT
- sealed manifest

Falsifiers：

- any false STABLE/false unaffected；
- delta/refinement incomplete；
- dominant reads UNKNOWN；
- CSP/early critical span near zero；
- SCA/affected work high、reconvergence late；
- offline margin不超过 headroom；
- 两 blocks方向矛盾且无 preregistered explanation。

# 10. R3 Opportunity Gate

Opportunity Gate 只授权一个 2-source minimum treatment，不声称 online economics。

## Gate A Correctness/refinement

- selected region/operator满足 T1–T6b、relevant scoped A1–A10/A15/A16 与 P7 operator/native-continuation refinement。
- zero false STABLE/unaffected。
- M0 只需 native exact request、complete artifact、声明性 ReplayAllowed contract与 native publication；不要求 semantic certificate。
- M2 额外需 T7–T8/A11–A14/refinement eligible。

## Gate B Early memory-specific validity

Full request 前有 StateDelta+witness+Dep proof；request-only baseline不能同样提前。失败则只考虑 M0/NULL。

## Gate C Structural opportunity

CSP nontrivial；SCA/affected propagation在预注册 bound内；存在 meaningful exact reconvergence；`GrossCounterfactualSavedCP` positive。CSP本身不等于 speedup。

## Gate D Offline margin

\[
OfflineOpportunityMargin>RequiredOnlineHeadroom
\]

不用 treatment LCB，不假装已测 shadow/interference。

## Gate E Minimum sufficient method

1. 单 read/demand certificate删除 dominant potential span → M1。
2. M1 外仍有 dominant exact-reconvergent suffix，且 M2 extension eligible → M2。
3. 无 early proof，但 native exact request有 ReplayableSpan且 ReplayAllowed eligible → M0。
4. UNKNOWN/SCA/affected/margin失败 → NULL。

Seal METHOD_SELECTION.json：唯一 method/operator/seam/theorem profile/headroom/rejected alternatives。不得后验换 operator 或升级复杂方案。

# 11. Candidate method audit

## M1 State-Delta-Certified Semantic Demand Maintenance

Solved problem：合法 commit 后，不完整重做仍有效的 semantic read/request construction；validity 在 full request 前建立。

Difference：继承 SAC/top-k/OCC。可能的新点仅是 sound semantic certificate 经 Dep closure 组合为 adaptive LLM demand validity，再交回 native memory continuation。

Graphiti minimum：

- 只实现一个 P7-eligible、R3 dominant operator；
- likely candidate 是 guarded node cosine top-k；
- BM25/hybrid/ANN 无 proof 时不发 STABLE；
- affected branch fresh execute；
- maintained seam output交回 native Graphiti tail/publish；
- 不修改 persistence。

Correctness：Core T1–T6b、relevant scoped assumptions、P7 selected-operator refinement与 native-continuation congruence；不依赖 T7/T8/A11–A14。

Quick falsifier：2-source frozen differential出现 false reuse/canonical final-state mismatch，或 online LCB≤0，或 request-only baseline有同等提前量。

Generality：VersionedState、Delta、SemanticRead、Witness、Dep lineage、native continuation seam；mapping影响 G，不是 Graphiti correctness前提。

Amplification：delta scan、rescoring、witness/trace storage、fallback/reexecution。Certificate scan不低于 original read则倾向 NULL。

## M2 Dynamic Affected Persistent Transition

Solved problem：局部 delta 后只重算 affected subgraph，在 exact reconvergence 后复用 dominant suffix，并 staged publish。

Difference：affected set/reconvergence本身属于 SAC/Adapton。潜在扩展仅是 dynamic LLM structure + semantic certificate + ordered external memory effect。

Graphiti minimum：

- 只有 M1 成立且 M1 外仍有 dominant suffix才考虑；
- typed trace、terminating closure、branch rebuild、exact reconvergence；
- 前置/封闭 embedding、bulk、saga native primitives；
- plan/frontier/recovery adapter；
- 不重写 Graphiti dedupe/summary/temporal algorithm。

Correctness：Core T1–T6b + M2 T7–T8 + persistence refinement。

Quick falsifier：AffectedFraction/SCA高、reconvergence晚、RepairSpan接近 full transition、crash/refinement failure或 online benefit非正。

Generality：理论接口可跨 memory systems；没有第二 implementation不 claim portable performance。

Amplification：trace persistence、repair、dual work、journal、storage与tail risk；额外收益必须支付复杂度。

## M0 Exact Native-Demand Replay

Solved problem：native 已完整重建 exact request 后避免重复 provider inference。

Difference：本质是 exact response cache / speculation+validation，不是 incremental memory construction。

Graphiti minimum：复用 V6 request identity/provider arbiter；增加完整 response artifact、exact model/schema/tool/config/policy epoch 与 provider-contract guard；native publication不变。没有声明性 contract时不得 live replay。

Correctness：canonical request exact、artifact complete；frozen exact；live only if provider/deployment contract声明 ReplayAllowed=true且本地实现满足它。Repeated match只能测试 guard，不建立 contract。

Quick falsifier：ReplayableSpan不在 critical path，ReplayAllowed无法建立，或 saved provider work不覆盖 shadow/validation/interference。

Generality：可用于有 canonical request 的 agent runtime，但非 memory-specific。

Amplification：shadow calls、artifact storage、validation、cache pollution/waste。

Novelty ceiling：摘要与结论不得声称 state-delta invalidation、SAC extension或 incremental transition。

## NULL

Triggers：Core/refinement blocked、false STABLE、high-cost UNKNOWN、CSP低、SCA高、affected广泛、offline margin不足或 Online Gate失败。

Output：propagation characterization、counterexamples、backend contract gaps、M0 ceiling与不实现 runtime 的理由。

# 12. Code boundary and TDD

## 12.1 Opportunity Gate before

Allowed：

- formal proofs/reference/model checking；
- P7 source/refinement audit；
- observer/snapshot/delta/witness/Dep/alignment；
- fresh ground-truth and offline analyzers；
- report/seal schemas。

Forbidden：

- old read/response return；
- skip native demand；
- plan repair/apply；
- treatment flag，即使 default off。

## 12.2 After method selection

M1 minimum：

- `delta_projection.py`：只实现 selected operator的 \(Obs_\rho\) 与 `Complete_ρ`，不做全 Graphiti delta framework；
- `certificates/selected_read.py`：typed `STABLE/INVALID/UNKNOWN` 与 witness guard；
- `lineage.py` / `validity.py`：previous-episode、existence/control/binding/predecessor到 selected demand的最小 Dep closure；
- `maintain_selected_region.py`：仅复用 Gate选中的 read/demand construction，affected/UNKNOWN执行原 native region；
- `native_seam_adapter.py`：只在 P7选定 seam交换 \(z\)，随后调用未修改 Graphiti continuation；
- T2/T3/T4/T5/T6/T6b theorem-derived tests，含 ignored-ID、previous-window与 native final-state differential。

Interfaces在 R4 冻结：`snapshot_token`、`extract_delta(operator)`、`certify(witness,delta)`、`dependency_fingerprint`、`maintain_region`、`native_continue`。不得顺手加入 speculative scheduler、KV cache、prompt compression或 persistence refactor。

M2 only additionally：

- affected_set.py
- reconvergence.py
- transition_repair.py
- staged_apply.py
- recovery.py
- T5–T8 tests

M0：只薄封装 V6 exact arbiter、artifact/epoch与声明性 ReplayAllowed guard；contract UNKNOWN则不实现 live response replay。

TDD：

1. theorem-derived RED；
2. minimum implementation；
3. frozen canonical differential GREEN；
4. V5/V6 regression；
5. failure injection；
6. observer-off/native equivalence；
7. seal。

# 13. Gate-after experiments

## R4 Freeze selected method

Freeze theorem/assumption profile、operator/seam、fallback、cost budget、arms/seeds、stopping rule与 artifacts。

## R5 Minimum treatment and adversarial differential

Only one selected method. Cover：

- insert/delete/update、key phantom；
- top-k short/full、boundary、tie；
- BM25/hybrid/ANN UNKNOWN；
- alignment ambiguity、partial-order schedule variation；
- branch add/remove、summary fanout；
- previous-episode window/order、ignored seam ID、oracle history/epoch与 provider-contract guard；
- M2 only stale frontier/crash。

Unsupported cases must fallback；all frozen cases canonical-equivalent。

## R6a Two-source Online Economics Gate

Arms：

- native observer-off；
- native observer-on matched control；
- selected treatment；
- when required, exact-request-only baseline。

Use ABBA/BAAB with fixed workload/model/backend. Pre-register enough repeated paired units；a single pair gives only a point estimate and cannot support LCB.

Measure actual：

- certificate execution；
- repair/reexecution；
- shadow/provider cost；
- observer/treatment interference；
- baseline/treatment semantic DAG 与 actual counterfactual critical-path change；
- tokens/GPU/read/embedding amplification；
- p50/p95/p99；
- canonical correctness/fallback。

\[
ActualNetBenefit_{CP}=
CP(G_{matched\ control},w)-CP(G_{treatment},w)
\]

Treatment graph已经包含 certificate、repair、shadow与 interference的实际 precedence/cost，因此 primary net benefit不得再次扣费。另做 `gross saving − certificate − repair − shadow − interference` attribution，并要求与直接 CP difference在预注册 tolerance内 reconciliation；无法 reconcile则 economics INVALID。

\[
PairedLCB(ActualNetBenefit_{CP})>0
\]

还需 zero false reuse、amplification within preregistered budget、memory-specific method相比 request-only有真实提前量。

Failure → NULL/negative result。不得后验换 operator/method；新想法必须新 preregistered campaign。

## R6b Six-to-twelve source

Only after R6a。6-source验证 mechanism consistency；12-source估计 paired economics、tail、amplification。Paired randomization/CI 从这里及 publication campaign使用。

## R7 46-source development qualification

唯一 winner 在 6071bd76 做 matched qualification，只报告 development point estimate。

## R8 Held-out publication campaign

Unexposed histories、preregistered seeds、independent seal。QA failure remains INVALID_RETAINED。

# 14. Reporting and claim rules

Correctness：

- assumption/P7 status；
- confusion matrix、false STABLE/unaffected；
- frozen canonical FSC；
- fallback；
- provider contract authority/version、ReplayAllowed status与 implementation-conformance outcomes；
- M2 only stale abort/crash invariant。

Method selection只用五类 headline；其余均为 supporting raw metrics：

| Headline | Definition / supporting metrics | Purpose |
|---|---|---|
| CSP | full native request前 soundly certifiable的 baseline state-dependent critical-work coverage | characterization；不直接声称 speedup |
| Semantic Change Amplification | per-resource `SCA_work`、absolute direct/affected work、CascadeShare/Impact | characterization；识别 adaptive cascade |
| Reconvergence | rate、depth、fanout、first exact suffix、RepairSpan | characterization；决定 M1 是否足够及 M2是否有额外价值 |
| Counterfactual Critical-Path Saving | gross/costed DAG longest-path recomputation、path switch、conservative LB | characterization + Offline Opportunity Gate；非 online speedup claim |
| Amplification Cost | certificate/repair/shadow/interference、token/GPU/read/embedding/storage | offline bound + online economics |

Correctness metrics单列，不与机会指标混用：`Complete_ρ/R` status、STABLE/INVALID/UNKNOWN confusion、zero false STABLE/unaffected、canonical T6 FSC、T6b final-state differential/fallback。Supporting characterization仍完整报告 MutationLocality、Read/Request/Demand/Structural Stability、AffectedFraction、Depth/Fanout、CertifiableStableSpan、ReplayableSpan与 raw resource distributions。

Economics：

- Certificate/Repair/Shadow/Interference；
- provider calls、tokens、GPU seconds；
- embeddings/reads/CPU/DB；
- waste/storage/tail latency。

Forbidden narratives：

- hit rate代替 critical benefit；
- experiment“证明” theorem；
- missing_side证明 drift；
- live偶然相同证明 ReplayAllowed；
- exact request+epoch在 ReplayAllowed unknown时授权 live replay；
- async completion order当 semantic divergence；
- runtime UUID差异当 semantic failure；
- INVALID冒充 CHANGED；
- 忽略 UNKNOWN；
- R3两个 development blocks作强 significance/CI；
- exact replay包装成 incremental memory；
- Graphiti positive自动证明 generality。

## 14.1 Claim framing

M1 positive时核心定位为：**Self-adjusting memory construction for adaptive LLM memory systems.**

明确继承 SAC/Adapton 的 dependency/change propagation、memoization、reconvergence、stable names与 FSC。可能贡献限于：semantic state-dependency abstraction、operator/region-scoped delta completeness、sound semantic-read certificate、certificate到 adaptive LLM demand validity的组合、native-continuation congruence refinement，以及 memory-specific counterfactual critical-path/economic characterization。

不得声称发明 change propagation、incremental computation、top-k maintenance、transaction、DAG scheduling或 speculation。M2若单独通过，再增加 persistent ordered-transition extension；只有 M0 时只称 state-aware exact response replay ceiling。

# 15. Generality

Core interface：

- current_snapshot
- diff
- semantic_read
- witness
- certify
- build_trace
- native_continue

M2 portable claim additionally：

- staged_plan
- ordered_apply
- recover

至少 mapping 一个非 Graphiti system，写 state/mutation/read/demand/lineage/publication/unsupported/UNKNOWN。Mapping只支持 abstraction plausibility；portable performance需第二 backend implementation。

# 16. Legal terminal states

## V7_POSITIVE_M1_SEMANTIC_MAINTENANCE

Core T1–T6b、relevant scoped A1–A10/A15/A16、P7 operator/native-continuation refinement、zero false STABLE/unaffected、canonical FSC/final-state congruence、Online Gate positive、reviewer test。G单独报告。

## V7_POSITIVE_M2_PERSISTENT_TRANSITION

全部 M1 条件 + T7–T8/A11–A14、persistence refinement、ordered/crash safety、M1之外额外 suffix value。

## V7_EXACT_REPLAY_ONLY

Native exact request、complete artifact、声明性 ReplayAllowed contract、本地 conformance与 Online Gate positive；明确 low novelty，无 memory-specific claim。Contract UNKNOWN时不能进入该终态。

## V7_NULL_NO_MEMORY_SPECIFIC_METHOD

Theory可成立但 Graphiti opportunity/online economics不足，或 M0也不可行。

## V7_THEORY_OR_SYSTEM_BLOCKED

Core blocker阻止 M1/M2；若仅 M2 extension失败，只标 M2 blocked，仍可 M1/M0/NULL。

# 17. Final coding/research-agent order

1. Verify pins/sealed hashes。
2. Related-work inheritance matrix。
3. P0 native semantics/partial order/α-equivalence/\(d=1\)。
4. P1 State/Delta/projection。
5. P2 certificate calculus。
6. P3 Dep closure。
7. P4 demand + ReplayAdmissibility。
8. P5 propagation/reconvergence/A15。
9. P6 Core T1–T6 FSC/reference model。
10. P6c T6b conditional native-continuation congruence proof。
11. P6b M2 conditional Apply/crash theory（不阻塞 M1）。
12. Formal proof audit + FINAL FREEZE CHECKLIST；通过后记录 CORE_THEORY_FROZEN 和 M2 extension status。
13. P7 Graphiti operator/previous-episode/native-continuation/provider-contract refinement audit。
14. Freeze observer schema；write R1 RED tests。
15. Implement observer-only harness/analyzers；V5/V6 regression；仍无 treatment。
16. R0/R1 seal and scoped assumption status。
17. R2 two-source causal trace。
18. Two sealed R3 blocks；confusion/CSP/SCA/reconvergence/counterfactual CP/amplification。
19. R3 Opportunity Gate；select unique M0/M1/M2/NULL。
20. NULL → report only。
21. Implement unique minimum treatment；R5 differential。
22. R6a Online Gate；failure → negative/null，不切 method。
23. R6b 6→12。
24. R7 46-source development。
25. R8 held-out publication。
26. Claims match C/N/V/G and terminal state。

# 18. Definition of Done

- closed formal model、partial order、Dep、α-equivalence、\(d=1\)；
- Core T1–T6b、relevant scoped A1–A10/A15/A16 proofs/counterexamples与 assumption use-map；
- M2 claim才需 T7–T8/A11–A14；
- frozen/live/ReplayAllowed split；
- UNKNOWN→fallback；
- P7 contracts and frozen observation fields；
- fresh current-state ground truth；
- zero false STABLE/unaffected；
- CSP/work-level SCA/reconvergence/counterfactual CP/amplification；
- sealed R1–R3；
- Opportunity + Online Gates；
- unique minimum implementation or explicit NULL；
- V5/V6 regression、matched controls；
- M2 only ordered/crash evidence；
- reproducible manifest。

若无 sound且经济的 memory-specific method，严格结论是：

> 当前 MemBind evidence 与 Graphiti v0.29.3 不支持一个同时满足 correctness、memory-specific novelty 与 systems value 的 state-delta incremental construction method；M0 的上限是 exact response cache，或其价值也不足，因此不实现额外 V7 treatment runtime。

# 19. FINAL FREEZE CHECKLIST

这里的 PASS 表示 **workplan 的定义、依赖、fallback 与执行链已闭合**，不表示 P0–P6 proof、P7 Graphiti refinement或 R1–R3 evidence已经产生。只有以下全部 PASS，才允许停止 methodology brainstorming并执行 P0–P7；实际 `CORE_THEORY_FROZEN` 仍需第 17 节第 12 步的 proof artifacts与审计记录。

1. **PASS — Core formal semantics是否 closed？** State、episode/env/oracle、partial-order trace、Dep、scoped delta、certificate、affected fixed point、seam relation、native continuation与 publication profile均已定义。
2. **PASS — 是否还有 hidden assumptions？** 当前已知前提全部登记 A1–A16并有 use-map；P7新发现 influence必须登记或 block，不得隐式吸收。
3. **PASS — M1是否完全不依赖 M2 persistence refactor？** M1使用 native publication；A11–A14/T7–T8只约束 M2。
4. **PASS — T6b是否足够连接 maintained seam与 native final state？** 使用 continuation-observable \(\equiv_{\alpha,K}\)、隔离但等价的 authoritative states、same env/coupled oracle与 step-local源码 refinement；证明失败即移动 seam或 block。
5. **PASS — StateDelta completeness是否已正确 scoped？** T2/T3/T5/T6按 \(Complete_ρ/Complete_R\)；unsupported operator只污染依赖 region。
6. **PASS — previous episodes是否完整进入 Dep audit？** 已定义为 state-dependent Read/Input dependency，并有 P7 artifact、schema fields、RED counterexample。
7. **PASS — ReplayAllowed是否来自 provider contract而非经验测试？** 只有声明性 provider/deployment contract可给 true；实验只查 conformance，证据不足为 UNKNOWN/fresh response。
8. **PASS — critical-path opportunity是否采用 counterfactual recomputation？** gross/costed semantic DAG均重算 longest path、允许 path switch/overlap去重，并区分 V6 attribution limit。
9. **PASS — SCA是否避免错误的 CP denominator？** 主 ratio使用同资源 direct-vs-affected work；CP只报告 bounded CascadeImpact与 direct span。
10. **PASS — P7能否在写 instrumentation 前冻结所有 required fields？** T1–T8 closure table与三项专项 audit逐字段生成 sealed schema；未封闭不得 instrument。
11. **PASS — R3是否只做 Opportunity Gate？** R3只有 observer/fresh ground truth/offline counterfactual，不声称 online speedup。
12. **PASS — Online economics是否严格后置？** 唯一 minimum runtime后才测 actual CP/cost/interference并要求 paired LCB>0。
13. **PASS — 任一 proof/refinement失败是否都有 fail-closed/null 路径？** operator/region UNKNOWN→fresh；T6b失败移动 seam/block M1；M2失败不绑架 M1；无经济机会→NULL。
14. **PASS — 是否还有为了“让 V7 成功”而引入的非必要复杂机制？** 没有；禁止 speculative scheduler、timestamp parallelism、KV cache、prompt compression及未被 Gate授权的 persistence refactor。

Freeze decision：**THEORY_FREEZE_CANDIDATE_PASS**。下一阶段固定为：formal proof → P7 source refinement → observer schema freeze → R1–R3 characterization。不得继续为保住 positive result新增 method；formal blocker只报告 blocker、最小修正与受影响 theorem。

# 20. Primary sources and artifacts

Project：

- [MemBind](https://github.com/ysyjsk/MemBind)
- [V5 opportunity](https://github.com/ysyjsk/MemBind/blob/2832d94b56db72fcf993154bde47e16b31ade724/paper-eval-v3/artifacts/paper_eval/membind_v4/postmortem/V5_SCHEDULER_OPPORTUNITY.json)
- [V5 request-DAG audit](https://github.com/ysyjsk/MemBind/blob/2832d94b56db72fcf993154bde47e16b31ade724/paper-eval-v3/artifacts/paper_eval/membind_v4/postmortem/V5_REQUEST_DAG_AUDIT.md)
- [V6 main comparison](https://github.com/ysyjsk/MemBind/blob/2832d94b56db72fcf993154bde47e16b31ade724/saturated_fixed_work_baseline_v1_3/artifacts/sfwb-v1-3-v6-autoresearch-20260822-191241/main/V6_MAIN_COMPARISON.json)
- [V6 proof](https://github.com/ysyjsk/MemBind/blob/2832d94b56db72fcf993154bde47e16b31ade724/saturated_fixed_work_baseline_v1_3/artifacts/sfwb-v1-3-v6-autoresearch-20260822-191241/method/V6_PROOF.md)

Implementations：

- [DBSP](https://github.com/vmware/database-stream-processor)
- [Rattle](https://github.com/ndmitchell/rattle)
- [SPFresh](https://github.com/SPFresh/SPFresh)
- [Spectrum](https://github.com/jacklightChen/spectrum)
- [Parrot](https://github.com/microsoft/ParrotServe)
- [Speculative Actions](https://github.com/naimengye/speculative-action)
- [CacheBlend](https://github.com/YaoJiayi/CacheBlend)
