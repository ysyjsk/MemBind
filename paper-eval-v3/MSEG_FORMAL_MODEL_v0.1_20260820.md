# Memory Semantic Execution Graph (MSEG) v0.1

Status: DESIGN_ONLY / NOT_RUNTIME_AUTHORIZATION  
Date: 2026-08-20  
Repository baseline: ed227f0 (v5)  
Scope: formal model, semantic contract, certification rules, observability requirements, and falsification gates

This is a new design record. It is not a replacement for a frozen experiment,
does not alter an arrival trace, and does not authorize a new scheduler, live
run, vLLM service, Neo4j namespace, or performance claim.

The following decisions remain frozen:

~~~text
STOP_V4_NODE_RESOLVE
STOP_V4_VDC_NO_LEGAL_WINDOW
STOP_V5_ORACLE_INSUFFICIENT_OBSERVABILITY
GO_MEG_DESIGN_ONLY
STOP_RUNTIME_POLICY_AND_LIVE
~~~

## 1. Research proposition

Persistent agent memory is not only a workflow of LLM calls. A memory
operation consumes immutable evidence, reads a versioned mutable state, creates
a semantic effect, and crosses a visibility boundary. Hiding LLM latency while
preserving serial-reference memory semantics therefore requires a typed
semantic boundary before it requires a scheduling policy.

The central proposition is:

> A Memory Semantic Execution Graph (MSEG) is a backend-neutral intermediate
> representation whose nodes carry evidence, state-version, effect-scope, and
> visibility contracts. A runtime may optimize only certified portions of the
> graph; exact state/effect validation remains the correctness authority.

This proposition is deliberately narrower than a generic workflow scheduler,
LLM serving scheduler, database OCC/MVCC system, or universal agent-memory
abstraction.

## 2. Core contradiction

The system wants to overlap expensive LLM work:

~~~text
more legal work in flight -> more opportunity to hide service time
~~~

The memory semantics require:

~~~text
state-bound work reads the exact predecessor version
effects do not silently conflict
private output is not published early
durable publication follows the reference frontier
~~~

Serial execution satisfies the second set but leaves LLM latency exposed.
Unconstrained episode concurrency may satisfy the first set physically, but
request lifecycle telemetry does not prove which state was read or which
effect was applied. The missing object is therefore a semantic execution
contract, not merely a better queue policy.

## 3. Formal model

Let:

~~~text
X                 immutable evidence store
M^v               logical persistent memory state at version v
P                 durable publication log
V                 operator instances
~~~

An operator instance is:

~~~text
v = (id, type, evidence, read, effect, visibility,
     resource, control, contract, provenance)
~~~

The fields have the following meanings:

| Field | Meaning | Required for certification |
|---|---|---:|
| id | stable instance identity, independent of transport retry | yes |
| type | semantic operator class, not an HTTP/request label | yes |
| evidence | immutable IDs/hashes consumed by the operator | yes for evidence-bound work |
| read | logical read scope and required state version | yes for state-bound work |
| effect | add/update/merge/invalidate and exact target scope | yes before conflict reasoning |
| visibility | PRIVATE_INTERMEDIATE or PUBLISHED_STATE | yes |
| resource | LLM, embedding, database, CPU, or other declared class | diagnostic/admission input |
| control | explicit control predecessors | yes when applicable |
| contract | atomicity, retry, idempotence, cancellation, and publication rules | yes |
| provenance | adapter/backend/schema/certification identity | yes |

For a state-bound operator, materialization and execution are distinct:

~~~text
Q_v       = materialize(type_v, evidence_v, M^(read.version))
Y_v       = execute(Q_v)
Delta_v   = effect_v(Y_v)
~~~

Y_v is private until exact validation succeeds. A durable transition is:

~~~text
M^(v+1) = apply(M^v, Delta_v)
append(P, publication(v, v+1))
~~~

The transition is legal only when the observed predecessor version equals the
contracted version and the effect certificate is valid. A transport request
ID, prompt prefix, token count, or client completion event is not a substitute
for this identity.

The graph is:

~~~text
G = (V,
     E_EVIDENCE,
     E_STATE,
     E_CONTROL,
     E_CONFLICT,
     E_PUBLICATION,
     C)
~~~

C is the set of semantic contracts. A graph with only V and ordinary
control/data edges is a workflow DAG projection, not an MSEG.

## 4. Edge semantics

The v0 edge vocabulary is intentionally small:

| Edge | Meaning | Unknown handling |
|---|---|---|
| EVIDENCE_DATA | immutable evidence consumed by a node | missing evidence blocks certification |
| STATE_VERSION | node reads/requires a logical predecessor version | unknown version blocks certification |
| CONTROL | completion order required by the operator contract | unknown control edge blocks certification |
| EFFECT_CONFLICT | certified or conservatively possible read/write overlap | unknown scope is treated as possible conflict |
| PUBLICATION | a node advances durable visibility/frontier | absent boundary cannot be inferred from return |

Edges may be discovered progressively, but late discovery cannot retroactively
certify an earlier execution. If a dependency is not observable at the point
where a decision would be made, the node remains UNRESOLVED.

## 5. Lifecycle and certification states

MSEG distinguishes physical lifecycle from semantic certification:

~~~text
ARRIVED
  -> PREPARED
  -> MATERIALIZED(version)
  -> EXECUTING_PRIVATE
  -> VALIDATED
  -> PUBLISHED
~~~

The semantic state is one of:

~~~text
UNRESOLVED
CERTIFIED_BLOCKED
CERTIFIED_READY
CERTIFIED_PRIVATE
CERTIFIED_PUBLISHABLE
INVALID
OPAQUE
~~~

CERTIFIED_READY requires all of the following:

~~~text
evidence contract complete
control predecessors complete
required state version is known and present
effect/visibility contract is certified
no unresolved dependency participates in the proposed decision
~~~

CERTIFIED_PUBLISHABLE additionally requires:

~~~text
exact materialization against the current predecessor
exact validation of semantic-call identity and effect context
publication frontier permits the transition
~~~

OPAQUE and INVALID nodes may execute through a conservative serial path if
the backend supports it, but they must not justify parallelism, commutativity,
critical-path reordering, or a performance claim.

## 6. Soundness conditions

The following is the future safety theorem target, not a result claimed by the
current repository.

If every published node satisfies:

1. its read version equals the exact predecessor state;
2. its semantic request and effect context pass exact validation;
3. its effect is atomic and fully journaled;
4. publication follows the serial reference frontier; and
5. no hidden read/write or background mutation exists outside the contract,

then every durable visible prefix is observationally equivalent to the serial
reference execution under the same model/backend envelope.

The theorem is intentionally conditional. It does not cover hidden database
effects, arbitrary nondeterministic model outputs, or an adapter that reports
only request lifecycle events.

For two operators a and b, a sound reorder candidate requires at least:

~~~text
read(a)  ∩ write(b) = empty
read(b)  ∩ write(a) = empty
write(a) ∩ write(b) = empty
compatible predecessor versions
no control/publication dependency
atomic, certified effects
~~~

If any scope or version is UNKNOWN, the proof obligation fails closed. A
conflict classifier may estimate cost, but it cannot establish semantic
independence or bypass exact validation.

## 7. Why this is not a transaction log or a generic DAG

An ordinary DAG records task order but not the state transition that makes an
order legal. Two schedules with the same ordinary DAG can produce different
memory states when both tasks update the same entity.

A database transaction log records committed effects after the fact. MSEG
also represents pending semantic computation, evidence lineage, state-version
requirements, private results, and the boundary at which an effect becomes
durably visible.

MSEG is therefore best described as a:

~~~text
typed + versioned + effect-aware + visibility-aware execution IR
~~~

It may borrow analysis techniques from compiler IR and database plans, but it
does not inherit their assumptions that all reads, writes, relations, or
physical costs are known before execution.

## 8. Contract compiler pipeline

The proposed compiler is a hybrid static/dynamic front-end, not a claim that a
runtime can infer arbitrary semantics from prompts:

~~~text
backend operator declarations
        |
        v
operator-instance materialization
        |
        v
lineage + state-version + effect journal
        |
        v
contract/effect validator
        |
        v
certified MSEG or OPAQUE fallback
~~~

### Static contract

The backend adapter declares operator-level invariants:

~~~text
operator type and stable identity rule
allowed read namespace
allowed effect kinds and scope form
visibility transition
atomicity and retry semantics
publication boundary
backend/schema certification identity
~~~

### Runtime evidence

Each instance must record:

~~~text
instance and parent/child lineage
evidence IDs/hashes
logical state version read
materialization identity
effect journal and completion
publication event
physical lifecycle timestamps
~~~

### Validation

The validator compares declared and observed facts. Any mismatch yields
INVALID; missing facts yield OPAQUE or UNRESOLVED. It never fills a gap from
request order, prompt similarity, token length, queue depth, or client
concurrency.

## 9. Graphiti mapping and current boundary

The pinned Graphiti 0.29.3 path maps as follows:

| Graphiti path | MSEG semantic operator |
|---|---|
| extract_nodes | EntityExtractionOperator |
| previous episode retrieval | MemoryContextRetrievalOperator |
| resolve_extracted_nodes | EntityResolutionOperator |
| extract_edges | RelationExtractionOperator |
| resolve_edge_pointers | RelationPointerMaterializationOperator |
| resolve_extracted_edges | RelationResolutionOperator |
| node attributes/summaries | AttributeEnrichmentOperator / MemorySummarizationOperator |
| episode data processing | MemoryMutationOperator |
| bulk durable write | StatePublicationOperator |

The current adapter and MSEG package already provide useful pieces: stable
operator attribution, passive spans, evidence hashes, dependency types,
publication critical-path scaffolding, and a fail-closed oracle. They do not
yet provide complete certified instances because the sealed trace lacks
per-edge child identity, exact read scope, logical state versions, transaction
effect completion, and exact publication causality.

Consequently, the existing MSEG artifacts remain diagnostic and non-mergeable.
They must not be relabeled as a complete executable IR, and no existing
artifact is changed by this document. Historical artifact-local STOP labels
remain as recorded; the current project-level V5 decision is additive and is
not a rewrite of those sealed files.

## 10. Observability levels

The design separates evidence levels so that a backend can be useful without
pretending to support unsupported optimizations:

| Level | Evidence | Permitted use |
|---|---|---|
| L0 | request lifecycle only | accounting and transport diagnostics |
| L1 | operator identity + evidence lineage | construction graph visualization |
| L2 | exact state version + effect scope + publication journal | certified readiness/conflict analysis |
| L3 | validated backend/resource interaction and exact child dependencies | offline policy oracle |

The current V5 sealed request-DAG trace is primarily L0 with partial L1
projections. It cannot support L2/L3 claims. A future optimization must state
its minimum required level and report coverage explicitly:

~~~text
certified coverage = certified instances / optimization-target instances
unknown coverage    = unresolved or opaque instances / all instances
~~~

No live policy is permitted while the target operator class has incomplete L2
coverage.

## 11. Relation to prior MemBind results

The frozen results form a motivation chain without being retroactively renamed:

~~~text
v3.1 State-Cut
  -> proves evidence-bound work can move early while Bind needs exact state

V4/VDC stop
  -> shows future-state speculation has no established legal/value window

V5 oracle stop
  -> shows request lifecycle does not expose the semantic DAG needed to test
     a scheduler or critical-path claim
~~~

The defensible interpretation is:

> Existing LLM-level telemetry is insufficient for memory-aware execution
> optimization because it lacks semantic state, effect, and publication data.

This is an observability diagnosis, not evidence that the workload has, or
lacks, an unmeasured scheduler opportunity.

## 12. Novelty boundary

The contribution is not any of the following in isolation:

~~~text
an ordinary task DAG
a priority queue
an Agentix-style LLM-call scheduler
a Parrot-style semantic variable
a vLLM/GPU batching policy
a generic database OCC/MVCC layer
~~~

The narrow potential contribution is the combination:

~~~text
memory-specific semantic contract
        +
progressive state/effect certification
        +
private-to-published execution boundary
        +
exact serial-reference validation
~~~

Any future policy, including publication-critical admission, is a consumer of
this IR rather than the definition of MSEG itself.

## 13. Falsification gates

The design must be rejected or narrowed if any gate fails:

| Gate | Required observation | Failure action |
|---|---|---|
| Model | contract distinguishes evidence, state, effect, visibility | stop and narrow to substrate |
| Identity | operator instances and child lineage are exact | no graph-based optimization |
| State | required logical versions are recorded | serial/opaque fallback only |
| Effect | exact or conservatively bounded scopes are journaled | no commutativity claim |
| Publication | durable visibility boundary is explicit | no freshness critical-path claim |
| Opportunity | certified legal ready/reorder width is nonzero | stop runtime-policy work |
| Benefit | offline oracle predicts a measurable critical-path opportunity | stop before live |
| Safety | serial equivalence and publication invariants hold | permanent stop for policy |
| Portability | at least one independent backend or synthetic implementation maps to the same contract | narrow claim to Graphiti |

The current repository has not passed the identity/state/effect/publication
coverage gates for a new runtime policy. That is why the current decision is
design-only rather than live GO.

## 14. Smallest eventual system contribution

The smallest defensible systems paper would require, in order:

1. This formal MSEG contract and a validator with explicit fail-closed states.
2. A passive Graphiti front-end that preserves all frozen v3.1 semantics.
3. An offline certified-graph oracle that checks readiness, conflicts,
   publication criticality, and serial equivalence.
4. One policy only after the opportunity gate passes, preferably
   Publication-Critical Admission with fixed K=2.
5. Correctness, overhead, certification coverage, and end-to-end evaluation
   against serial/v3.1 and unconstrained concurrency baselines.
6. An independent synthetic or second backend to separate the abstraction from
   Graphiti-specific naming.

If the opportunity gate remains false or unobservable, the scientifically
valid outcome is a stopped policy and an observability/semantic-substrate
result, not another candidate or a manipulated workload.

## 15. Isolated TDD prototype

The design-only contract step now has an isolated TDD prototype:

~~~text
src/paper_eval/membind_v4/mseg/semantic_contract.py
src/paper_eval/membind_v4/mseg/semantic_evidence.py
src/paper_eval/membind_v4/mseg/semantic_validator.py
src/paper_eval/membind_v4/mseg/offline_qualification.py
scripts/run_mseg_design_qualification.py
tests/test_membind_v4_mseg_semantic_contract.py
tests/test_membind_v4_mseg_semantic_evidence.py
tests/test_membind_v4_mseg_offline_qualification.py
tests/test_membind_v4_mseg_design_qualification_cli.py
~~~

The prototype is deliberately not exported through the existing package
facade and is not imported by any frozen runtime path. The evidence layer
records adapter provenance, exact state reads, effect-journal entries, and the
durable publication boundary. The qualification layer consumes those records
without changing execution order, admission, retry, transport, or backend.
The current behavior is limited to pure validation and offline gating:

~~~text
exact state/effect/publication evidence -> CERTIFIED_*
missing evidence or unknown scope       -> OPAQUE
identity/version/effect mismatch        -> INVALID
known disjoint scopes                   -> reorder certificate only
synthetic exact evidence                -> GO_OFFLINE_CERTIFIED
real trace without L2 evidence          -> STOP_REAL_TRACE_INSUFFICIENT_OBSERVABILITY
~~~

The focused RED-to-GREEN run is 36 passed. This is a contract and qualification
test result, not a runtime or performance result. The CLI output is written
only under `design_only/mseg_qualification_20260820/` and records
`network_calls: 0`, `services_started: 0`, and
`sealed_artifacts_modified: false`.

The synthetic gate contains two same-version, disjoint private operators and
one exact durable-publication operator. It certifies one private reorder
opportunity. The existing 193-request q0 sealed trace remains below the exact
state/effect/publication observability gate, so the resulting decision is:

~~~text
synthetic: GO_OFFLINE_CERTIFIED
real trace: STOP_REAL_TRACE_INSUFFICIENT_OBSERVABILITY
live_authorized: false
new_scheduler_authorized: false
~~~

## 16. Current action and non-actions

This design record and its isolated prototype do not:

~~~text
modify existing Python execution paths
add a scheduler or admission policy
change W, K, lookahead, or arrival behavior
read future state
start vLLM, Neo4j, tmux, or external APIs
rerun U0/A0/P(C=2)/v3.1/V4/V5
alter sealed artifacts or main-table results
~~~

The next permissible engineering step, if separately authorized, is a TDD
extension with explicit adapter provenance and effect-journal fixtures. It
must preserve the same fail-closed rules and continue to leave the existing
frozen runtime behaviorally unchanged. No live service is needed for that
step.

Final status:

~~~text
DESIGN_RECORD_CREATED: yes
FORMAL_MODEL_DEFINED: yes
SEMANTIC_CONTRACT_DEFINED: yes
CERTIFICATION_RULES_DEFINED: yes
OBSERVABILITY_GATES_DEFINED: yes
NOVELTY_BOUNDARY_DEFINED: yes
ISOLATED_TDD_CONTRACT_VALIDATOR: yes
OFFLINE_DESIGN_QUALIFICATION: yes
FOCUSED_TDD_TESTS: 36 passed
REAL_TRACE_GATE: STOP_REAL_TRACE_INSUFFICIENT_OBSERVABILITY
FROZEN_RUNTIME_TOUCHED: no
NEW_SCHEDULER_AUTHORIZED: no
LIVE_AUTHORIZED: no
~~~
