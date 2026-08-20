# MEG Semantic Adapter / Compiler v0.1

Status: DESIGN_ONLY / OFFLINE QUALIFICATION ONLY  
Date: 2026-08-21  
Graphiti source audited: `graphiti-core==0.29.3`  
Runtime policy authorized: no  
Live execution authorized: no

## 0. Scope and frozen decisions

This record answers a new question: can a memory backend explicitly emit a
certifiable semantic execution representation while it executes? It does not
try to reconstruct a DAG from an old request trace and it does not design a
scheduler.

The following decisions remain unchanged:

```text
STOP_V4_NODE_RESOLVE
STOP_V4_VDC_NO_LEGAL_WINDOW
STOP_V5_ORACLE_INSUFFICIENT_OBSERVABILITY
STOP_REAL_TRACE_INSUFFICIENT_OBSERVABILITY
GO_MEG_DESIGN_ONLY
STOP_RUNTIME_POLICY_AND_LIVE
```

No vLLM, Neo4j, tmux, external API, benchmark, sealed artifact, arrival trace,
U0, A0, P(C=2), v3.1, V4, or V5 execution was started or modified for this
work. The implementation added here is a pure record/compiler prototype under
`membind_v4/mseg/` plus synthetic unit tests.

## 1. Current gap

The existing v3.1 split is useful but too coarse to be a MEG front-end:

- `membind_v31/graphiti_adapter.py:364-440` compiles immutable extraction output
  into `PreparedArtifact`.
- `membind_v31/prepared_artifact.py:82-186` binds that output to source,
  evidence, certification, and artifact hashes.
- `membind_v31/graphiti_adapter.py:442-584` binds the artifact against latest
  state and returns a bind observation.
- `membind_v31/coordinator.py:371-447` enforces the publication frontier.
- `membind_v31/request_runtime.py:374-522` naturally records request submission,
  admission, start, terminal state, and transport identity.

Those records prove workflow and request lifecycle facts. They do not prove
which semantic child owned a request, which committed memory version a query
read, which exact IDs a transaction changed, or which backend commit caused a
durable publication.

The current MSEG overlay has the same limitation. It wraps only the top-level
Graphiti binding functions at `membind_v4/mseg/instrumented_adapter.py:206-245`.
Its observer allocates an ordinal when a top-level span is entered. That can
identify an operation group, but it cannot identify the approximately 150
per-edge children inside one `resolve_extracted_edges` call. The Q0 result is
therefore correctly frozen as insufficient observability.

### 1.1 Provenance acquisition audit

| Field or fact | Current natural source | Required authority | v0 result |
|---|---|---|---|
| graph/namespace ID | Graphiti `group_id` and source record | adapter normalization | available |
| stream ID | v3.1 coordinator scope | adapter/runtime | available |
| source sequence and source hash | source log and `PreparedArtifact` | immutable source authority | available |
| evidence/artifact hash | `PreparedArtifact` | compile authority | available |
| top-level operation role | Graphiti semantic binding wrapper | adapter | available, group only |
| request submit/start/end | `AdmittedLLMClientV31` | request runtime | available |
| stable per-edge semantic identity | none | child-boundary runtime hook | missing today |
| ready materialization time | VDC has selected coarse observations | child-boundary runtime hook | partial |
| exact state version read | source sequence is only a workflow label | version/read guard | missing today |
| exact read scope | Graphiti query inputs/results | query/candidate wrapper | missing today |
| dependency provenance | Python nesting and timing are insufficient | compiler plus child hooks | missing today |
| actual entity/edge/episode effect | returned objects and transaction inputs | mutation/transaction wrapper | partially available |
| mutation commit completion | Neo4j session/transaction | driver transaction hook | missing today |
| durable publication causality | coordinator emits an event after a probe | commit evidence plus publication journal | missing today |

The ownership split is consequently:

```text
adapter declaration       -> L0 possible semantics
runtime child hooks       -> L1 instance, parent, child, coroutine, request lineage
read/transaction wrappers -> L2 exact version, scope, effect and commit evidence
publication journal       -> L2 durable causal publication
```

An adapter cannot self-assert an observed state version or effect scope. A
function return cannot self-assert durable publication.

## 2. Semantic operator definition

A MEG operator is a stable semantic transition, not a Python function, request,
or source episode. A boundary qualifies only if all seven properties are
defined:

1. Stable instance identity derived before scheduling.
2. A versioned static semantic contract.
3. Identifiable immutable inputs or input hashes.
4. An explicit state requirement, including `UNBOUND` or an exact version.
5. An identifiable effect/effect class, including an explicit no-effect case.
6. Explicit completion semantics.
7. Explicit visibility semantics.

Qualification rules:

- Split a function when its children have different state requirements,
  effects, completion conditions, or visibility.
- Merge functions when they jointly implement one indivisible transition and
  no intermediate result has an independent completion/visibility meaning.
- Keep pure projection, index construction, UUID pointer replacement, parsing,
  and local sorting as helpers unless their output itself controls a semantic
  state transition.
- Conditional children are materialized only when the condition is observed.
  L0 declares that they may exist; it does not invent absent L1 instances.
- An operator whose child set, state version, effect, or completion is unknown
  remains `OPAQUE`.

The pure prototype encodes L0 in `semantic_adapter.py:478-528`, L1 operator
lineage in `semantic_adapter.py:277-474`, subrequest lineage in
`semantic_adapter.py:208-274`, and compiler input in
`semantic_compiler.py:94-178`.

## 3. Three-layer semantic provenance

### 3.1 L0 - static semantic contract

L0 is version-controlled adapter data:

```text
operator_role
operator_type
dependency_class
effect_class
resource_class
visibility
state_bound
idempotence
retry semantics
atomicity expectation
child identity recipe
```

L0 says what may happen. It may not contain an actual state token, entity ID,
edge ID, invalidation ID, transaction result, or observed effect scope. The
prototype rejects those dynamic fields with
`dynamic_fact_in_static_contract`.

### 3.2 L1 - dynamic operator and request lineage

The operator instance identity is:

```text
H(adapter_revision, graph_id, stream_id, source_sequence,
  semantic_role, parent_operator_instance_id,
  canonical_child_key, pre-schedule ordinal)
```

The canonical child key is made from immutable semantic inputs. For an edge it
is:

```text
(extracted_edge_uuid, source_node_uuid, target_node_uuid, fact_hash,
 duplicate_ordinal_if_required)
```

If an upstream backend emits duplicate identical records, the adapter assigns
`duplicate_ordinal` while enumerating the immutable input list, before any
coroutine is scheduled. Completion order, request order, latency, timestamp,
and token count are explicitly forbidden identity inputs.

One semantic child can issue several sequential requests. Their identities are:

```text
H(operator_instance_id, semantic_subrole, request_ordinal)
```

`RequestLineage` then binds that logical ID to a coroutine, transport request
ID, and created/enqueue/start/end timestamps. Transport retries can be recorded
as attempts under the same logical request; they do not rename the semantic
operator.

The required implementation pattern is:

```python
child = lineage_builder.add_child(child_key=key, ...)
with operator_context(child):
    coroutine = resolve_one_edge(...)
await semaphore_gather(*coroutines)
```

Python `contextvars` are suitable for propagation, but only after the child
identity has been created from structured input. A context variable carrying a
group ordinal allocated on request entry is not sufficient.

### 3.3 L2 - state, effect, and publication evidence

L2 contains observed facts:

```text
exact MemoryVersionToken read
actual read identifiers, or UNKNOWN
actual effect identifiers, or UNKNOWN
mutation start and commit completion
backend transaction/bookmark evidence
post-commit version token
durable PublicationEvent
```

Sources are deliberately independent:

| L2 item | Preferred source | Fallback | Failure behavior |
|---|---|---|---|
| read version | version guard around materialization | backend snapshot/transaction token | OPAQUE |
| read scope | candidate/query wrapper | validated result-ID projection | OPAQUE |
| actual effect scope | transaction input plus result summary | post-effect snapshot/diff | OPAQUE |
| commit completion | driver transaction callback/bookmark | backend commit API | INVALID if contradicted; otherwise OPAQUE |
| publication | commit-linked publication journal | no fallback to function return | OPAQUE |

## 4. Graphiti 0.29.3 boundary audit

The audited installed source is under
`membind-validation/.venv/lib/python3.12/site-packages/graphiti_core/`.

| Graphiti region | MEG treatment | Reason |
|---|---|---|
| source episode materialization | helper/input evidence | no independent semantic effect |
| `extract_nodes` | one extraction operator per episode/batch | one LLM extraction flight; output is private evidence |
| node candidate collection | per-node state-read children | distinct DB/search inputs and exact candidate scopes |
| similarity indexes and exact matching | helpers | deterministic local computation |
| unresolved node LLM resolution | one operator keyed by sorted unresolved input set | current code performs a coupled batch resolution |
| `extract_edges` | one extraction operator per episode/batch | one extraction flight; output keys downstream children |
| `resolve_edge_pointers` | helper | pure UUID rematerialization |
| edge candidate reads/searches | per-edge state-read children | independent structured edge keys and returned scopes |
| edge dedupe/contradiction LLM | per-edge semantic child | stable edge input and candidate set |
| edge attribute enrichment | conditional per-edge child | separate prompt and output field set |
| edge timestamp extraction | conditional per-edge child | separate prompt and temporal output |
| contradiction/expiry intent assembly | private mutation-intent operator | determines exact invalidation effects |
| per-node attribute extraction | per-node children | explicit node keys and independent requests |
| batch node summarization | one sorted-node-set child | coupled prompt, not one child per node |
| episodic-edge construction/projections | helper/private intent | no persistence until transaction |
| `add_nodes_and_edges_bulk_tx` | persist-and-publish operator on Neo4j | commit is both effect completion and immediate backend visibility |
| optional saga writes | separate mutation operators | occur after the main transaction and are not atomic with it |
| publication | event attached to the Neo4j transaction operator | a separate operator exists only for delayed visibility backends |

Important code evidence:

- Node resolution collects candidates then performs deterministic resolution
  and one unresolved-set LLM step at
  `node_operations.py:627-708`.
- Node attributes fan out per node and summaries execute as a batch at
  `node_operations.py:726-775`.
- Edge resolution runs per-edge DB reads at `edge_operations.py:365-370`,
  per-edge searches at `edge_operations.py:392-418`, and creates per-edge
  `resolve_extracted_edge` coroutines at `edge_operations.py:488-508`.
- One edge child can call dedupe, attribute, and timestamp prompts sequentially
  at `edge_operations.py:623-847`.
- `semaphore_gather` only wraps coroutines with a semaphore and `asyncio.gather`
  at `helpers.py:122-133`; it does not preserve semantic child metadata.
- The main graph write uses `session.execute_write(add_nodes_and_edges_bulk_tx)`
  at `bulk_utils.py:128-148`; transaction writes are assembled at
  `bulk_utils.py:151-260`.
- `_process_episode_data` performs optional saga writes after the main graph
  transaction at `graphiti.py:720-781`. In the MemBind v3.1 adapter, saga is
  passed as `None` at `membind_v31/graphiti_adapter.py:558-570`; this keeps the
  audited MemBind path on the single main transaction.

`function == operator` would therefore be wrong in both directions. The edge
resolution function contains multiple semantic children, while several helper
functions jointly form one private mutation intent.

## 5. Graphiti to MEG compiler mapping

The actual MemBind path becomes:

```text
source episode + exact evidence prefix
  -> node extraction [EVIDENCE_BOUND, PRIVATE]
  -> edge extraction [EVIDENCE_BOUND, PRIVATE]
  -> node candidate reads [STATE_READ(V)]
  -> unresolved-node resolution [STATE_BOUND(V), PRIVATE]
  -> edge pointer helper
  -> edge candidate reads [STATE_READ(V)]
  -> per-edge dedupe/contradiction children [STATE_BOUND(V), PRIVATE]
       -> optional edge attributes [PRIVATE]
       -> optional timestamps [PRIVATE]
  -> per-node attributes + batch summaries [STATE_BOUND(V), PRIVATE]
  -> mutation intent [PRIVATE]
  -> graph transaction + publication event [MUTATION, COMMITTED, PUBLISHED_STATE]
```

For every node the compiler consumes:

| Operator | L0 | L1 | L2 | Common UNKNOWN | Certification condition |
|---|---|---|---|---|---|
| extraction | adapter role/no-effect/private | source/batch identity and timing | empty state/effect scopes | prompt/input identity missing | immutable evidence and complete output lineage |
| candidate read | state-read contract | node/edge child key | predecessor token and returned IDs | query result IDs unavailable | same version before/after read guard and known scope |
| semantic resolution | state-bound/private | child/batch key plus requests | exact read token/scope, no hidden effect | conditional request lineage incomplete | terminal requests and exact state evidence |
| enrichment/timestamp | private child contract | parent child and subrequest lineage | exact inputs and no persistent effect | condition or child set unknown | observed conditional materialization and terminal result |
| mutation intent | merge/invalidate possibility | causal private parent IDs | intended IDs | effect target not projectable | all actual intended targets known |
| persist/publish | mutation/atomic/published expectation | transaction child identity | before/after token, exact effect IDs, commit time/bookmark and publication event | partial or multi-transaction effect | one committed transaction, complete scope and continuous predecessor chain |

### 5.1 `resolve_extracted_edges` minimum patch

An outer adapter wrapper cannot solve the existing 150-request ambiguity. The
minimum Graphiti-internal instrumentation is:

1. At `edge_operations.py:348-358`, retain a deterministic duplicate ordinal
   when exact duplicate extracted edges are collapsed.
2. Before each candidate read/search coroutine is created at lines 365, 392,
   and 407, create a child from the extracted edge structured key.
3. Before each `resolve_extracted_edge` coroutine is created at lines 488-508,
   enter that child context; do not allocate identity inside
   `semaphore_gather`.
4. Inside `resolve_extracted_edge`, give dedupe, attribute, and timestamp calls
   stable subroles and request ordinals. Their transport request IDs are L1
   links, not semantic identities.
5. On return, record the resolved edge ID, duplicate IDs, invalidation IDs, and
   terminal time. These are private effects/intents until the transaction
   journal commits them.

This is a small context/hook patch around child creation and requests. It does
not alter prompts, models, inputs, branching, coroutine order, retrieval,
dedupe, timestamps, or mutation semantics.

## 6. MemoryEffectJournal

The prototype record is implemented at `effect_journal.py:130-218` and is
append-only through `MemoryEffectJournal`.

Minimum entry:

```text
effect_id
graph_id
source_sequence
operator_instance_id
state_version_before
effect_type
effect_scope:
  namespace
  entity_ids
  edge_ids
  episode_ids
  UNKNOWN flag
mutation_started_ns
mutation_committed_ns
mutation_committed
publication_visible
state_version_after
transaction_id / backend commit evidence
evidence_hash
durable
```

Rules:

- `UNKNOWN` is preserved, never replaced with the function arguments guessed
  after the fact.
- `UNKNOWN` scope makes conflict/reorder certification fail closed.
- A committed effect requires commit time, transaction evidence, a newer
  version, and durability.
- The after-version token, effect entry, and publication event must bind the
  same backend transaction/bookmark ID.
- A visible effect without commit/durability is invalid.
- A publication timestamp before its effects' commit timestamps is invalid.
- The compiler checks the exact journal entry, not merely another entry with
  the same operator ID.
- State-bound effects must name the same `state_version_before` the operator
  read.

Insertion order, from least to most intrusive:

1. Adapter wrapper creates intent IDs and expected target classes.
2. Mutation wrapper projects actual node/edge/episode UUID inputs.
3. `session.execute_write` wrapper records start, terminal outcome, and commit
   bookmark/transaction evidence.
4. A content-addressed sidecar journal persists the evidence record.
5. A post-effect snapshot/diff is used only for a backend that cannot expose
   mutation inputs or commit results; it increases overhead and should remain
   OPAQUE if the diff is not exact.

For Graphiti, steps 2 and 3 are sufficient on the audited saga-free path. A
generic Neo4j `execute_query` wrapper alone is not sufficient because it loses
the semantic transaction aggregation and would mistake individual query
returns for publication.

## 7. MemoryVersionToken

The pure token model is implemented at `version_token.py:52-169`. It binds:

```text
namespace, backend_id, backend_epoch, logical_counter,
backend transaction/bookmark ID, evidence hash, exact predecessor token
```

It explicitly rejects wall-clock timestamps and arbitrary external strings as
versions.

### 7.1 Candidate comparison

| Scheme | Correctness | Complexity | Graphiti/Neo4j | Portability | Decision |
|---|---|---:|---|---|---|
| A. global monotonic publication version | strong under one controlled namespace writer; orders all visible mutations | low/medium | bind commit bookmark to counter | high | selected for v0 |
| B. stream-local source publication version | insufficient when streams share memory or retries/non-source writes occur | low | easy | medium | reject as exact memory version |
| C. transaction commit version | strongest backend-local commit identity | medium | Neo4j bookmark/tx metadata available via session hook | low/medium | retain as evidence inside token |
| D. namespace/entity-scoped versions | permits finer conflicts but multi-entity atomic transitions are complex | high | requires metadata/version index | medium | defer |

The v0 selection is A, scoped to one logical memory namespace, with C embedded
as transaction evidence. It is not `source_sequence` and is not wall-clock
time.

### 7.2 Exact-read protocol

For the current single-frontier, namespace-exclusive MemBind writer:

1. Read durable current publication token `V` from the adapter journal.
2. Enter a read guard and record `V` in operator context.
3. Execute candidate materialization while query wrappers record exact result
   IDs.
4. Re-read the publication token before leaving the guard.
5. Certify the read only if the token is still `V`, no commit was in progress,
   and the adapter has verified exclusive control of namespace writes.

Neo4j commit is atomic. A token change during a multi-query Graphiti read makes
that read `OPAQUE`; the compiler does not guess a snapshot. If external writers
cannot be excluded, a backend read transaction/snapshot token is required.
That is a qualification precondition, not a scheduler policy.

The durable v0 counter and predecessor link belong to the sidecar publication
journal. The Neo4j session hook captures the post-commit bookmark without an
extra Cypher query or metadata node, so passive graph/query invariants can
remain unchanged.

## 8. Publication evidence

The following events are distinct:

```text
function completed          != mutation persisted
mutation query returned     != whole transaction committed
transaction committed       != causal publication evidence recorded
coordinator callback        != backend durability proof
```

`PublicationEvent` is implemented at `publication.py:47-143`. It contains the
source sequence, predecessor/publication tokens, effect IDs, causal operator
IDs, transaction evidence, durable timestamp, frontier position, and durable
flag. Validation at `publication.py:146-205` joins it to exact committed effect
entries.

For Graphiti + Neo4j on the audited saga-free MemBind path, backend state becomes
durable and readable when `session.execute_write(add_nodes_and_edges_bulk_tx)`
successfully commits. Transaction completion and publication therefore qualify
as one indivisible `PERSIST_AND_PUBLISH` semantic operator. The MemBind
publication event is evidence attached to that operator, minted from the
session's commit/bookmark callback, then appended to the publication journal.
The later v3.1 `publication_durable` coordinator event confirms
frontier order, but it is not the transaction authority and must reference the
already minted event.

A backend with a delayed visibility barrier may materialize a distinct
publication operator after its mutation operator. That is not the Neo4j mapping.

The minimum wrapper is an optional commit observer on
`add_nodes_and_edges_bulk`/the driver session proxy. It observes session commit
and bookmark after `execute_write`; it does not add a Cypher statement. If a
backend exposes no durable commit boundary, publication remains `OPAQUE`.

Optional Graphiti saga writes are separate commits after the main transaction.
They must be separate effects, and publication is not certifiable as one atomic
transition unless all readers are hidden behind an outbox/publication barrier.
The current MemBind adapter passes `saga=None`; other paths are fail-closed.

## 9. Certification model

Conceptual operator states are:

```text
CERTIFIED_PRIVATE
CERTIFIED_STATE_READ
CERTIFIED_MUTATION
CERTIFIED_PUBLISHABLE
OPAQUE
INVALID
```

The existing validator currently serializes all non-publication certified
operators as `CERTIFIED_PRIVATE`; the more specific class can be derived from
the contract and is not used by a runtime in this design-only round.

State machine:

```text
L0 valid + L1 complete + L2 not yet complete -> OPAQUE
complete mutually consistent evidence         -> CERTIFIED_*
missing optional optimization evidence         -> OPAQUE
contradictory identity/version/effect/causality -> INVALID
```

Only certified operators may enter a future legal ready set or effect conflict
analysis. `OPAQUE` and `INVALID` are never converted to ready work.

A graph is `CERTIFIED` only if:

- every operator is certified for its declared role;
- every L1 parent is present and has an explicit dependency edge;
- dependency provenance is complete;
- no dependency is missing, duplicated, or cyclic;
- all state tokens and effect-before tokens agree;
- exact journal entries exist;
- every publication has a continuous predecessor chain and committed effects.

An evidence contradiction makes the graph `INVALID`. Missing observability
makes it `OPAQUE`. The compiler implementation at
`semantic_compiler.py:311-565` enforces this distinction and does not infer
dependencies from call nesting or time.

## 10. Passive equivalence protocol

Instrumentation must first be shown to preserve the uninstrumented execution.
The pure comparison schema is at `passive_equivalence.py:61-176`.

Run protocol for a separately authorized qualification:

1. Freeze source records, evidence, model configuration, prompts, retry
   configuration, and backend namespace snapshot.
2. Run the original adapter and capture a content-safe baseline snapshot.
3. Restore the exact backend snapshot.
4. Run the adapter with instrumentation only and capture the same fields.
5. Compare all fields exactly; timing may be reported as overhead but cannot
   substitute for a semantic invariant.
6. Sign a certificate only if every invariant passes.

Required invariants:

| Invariant | Comparison |
|---|---|
| request count unchanged | exact integer equality |
| prompt bytes unchanged | ordered SHA-256 equality |
| model unchanged | ordered model/config identity equality |
| response unchanged | ordered response SHA-256 equality |
| DB query behavior unchanged | ordered normalized query+parameter hash equality |
| published graph unchanged | canonical graph snapshot hash equality |
| publication order unchanged | exact frontier/event sequence equality |
| source exactly-once unchanged | source sequence multiset and exactly-once flag |
| no extra LLM calls | instrumented count equals baseline |
| no extra embedding calls | instrumented count equals baseline |
| no extra memory mutation | mutation count and graph snapshot equality |

Certificate envelope:

```json
{
  "certificate_type": "PASSIVE_EQUIVALENCE_CERTIFICATE",
  "adapter_revision": "...",
  "backend_revision": "...",
  "baseline_manifest_hash": "...",
  "instrumented_manifest_hash": "...",
  "invariants": {"request_count_unchanged": "PASS"},
  "overhead": {"wall_time_delta_ns": 0, "cpu_time_delta_ns": 0},
  "status": "PASS_OR_FAIL",
  "live_runtime_authorized": false
}
```

No production `PASSIVE_EQUIVALENCE_CERTIFICATE` is issued in this round. The
synthetic tests validate the comparison logic only; claiming live equivalence
without paired executions would be invalid.

## 11. Cross-backend mapping: Mem0

Mem0 is used only as a structurally different architecture mapping. This
repository does not vendor or pin a Mem0 source revision, so this is not a
runtime implementation qualification.

At the architecture level, a Mem0-style update contains fact extraction, an
action decision (`ADD`, `UPDATE`, `DELETE`, or no-op), per-memory vector-store
operations, and a history/audit write. It maps as follows:

| MEG core field/operator | Graphiti adapter | Mem0-style adapter |
|---|---|---|
| namespace | graph/group ID | user/agent/run plus collection ID |
| immutable evidence | episode and evidence-prefix hashes | message/input hash |
| extraction | node/edge extraction | fact extraction |
| state read | graph candidates at token V | vector memories retrieved at token V |
| resolution | node/edge dedupe/contradiction | ADD/UPDATE/DELETE/no-op decision |
| child key | node/edge UUID and content hash | proposed fact hash plus resolved memory ID/action ordinal |
| effect scope | entity/edge/episode IDs | vector memory IDs and history IDs |
| transaction evidence | Neo4j commit/bookmark | vector-store commit/result plus history-store result |
| publication | graph commit-linked event | update visibility barrier/outbox event |

Backend-neutral fields remain unchanged: graph/namespace, stream/source,
operator and parent IDs, child key, request lineage, state token, read/effect
scope, effect class, completion, visibility, transaction evidence, publication,
and certification status.

Adapter-specific items are the semantic role catalog, structured child key
recipe, resource IDs, version provider, commit observer, and publication
barrier. A Mem0 deployment whose vector store and history store are not atomic
would emit two effects and remain non-publishable/`OPAQUE` until a durable
outbox or validated visibility barrier joins them. This is an atomicity fact,
not a need for a different MEG schema.

Cross-backend schema conclusion:

```text
PASS_CROSS_BACKEND_SCHEMA
MEM0_RUNTIME_QUALIFICATION: NOT_RUN
```

## 12. Required instrumentation surface

| Surface | Data produced | Change size | Semantic risk |
|---|---|---:|---|
| v3.1 adapter wrapper | L0 revision, graph/stream/source root lineage | small | low |
| PreparedArtifact projection | immutable evidence/operator inputs | none or small | low |
| child creation hooks | structured child IDs, ready time, coroutine context | small local patches | low if passive |
| LLM client context bridge | logical request lineage to transport attempts | existing extension | low |
| query/candidate wrapper | exact read IDs and normalized query hashes | wrapper | low/medium |
| transaction/session hook | effect inputs, commit time, bookmark | wrapper/local hook | medium |
| sidecar effect/publication journal | durable evidence and token chain | new sidecar | low to memory semantics |

Minimum Graphiti internal hook sites are:

- node candidate child creation and unresolved-set resolution in
  `node_operations.py:627-689`;
- node attribute child creation at `node_operations.py:743-759` and batch
  summary boundary at lines 766 onward;
- edge read/search and resolution child creation at
  `edge_operations.py:365-418` and `488-508`;
- edge subrequest boundaries at `edge_operations.py:623-847`;
- transaction completion at `bulk_utils.py:128-159` and the Neo4j session
  wrapper.

The changes must be optional observer callbacks/context managers. They must not
change prompt text, model selection, retrieval configuration, edge/node
algorithms, mutation queries, or return values.

## 13. Intrusiveness analysis

The design does require Graphiti-internal child hooks; an adapter-only wrapper
cannot recover stable per-edge attribution. It does not require a rewrite of
Graphiti or Neo4j algorithms.

Low-intrusion evidence:

- The structured IDs already exist in extracted node/edge objects before
  `semaphore_gather`.
- `contextvars` naturally propagate through `asyncio` child tasks once the
  correct child context is established.
- Transaction inputs already contain exact entity/edge/episode identifiers.
- Neo4j `execute_write` already defines the main atomic boundary.
- A post-commit bookmark can be observed without an extra graph query.
- The journal is a sidecar and does not mutate the memory graph.

Limits:

- Exact read certification assumes exclusive namespace write control or a
  backend snapshot/read transaction. Without it, reads remain `OPAQUE`.
- Optional saga mode is multi-transaction and is not certified by the v0
  atomic contract.
- Query result projection and hashing add CPU/memory overhead.
- A durable sidecar append adds publication-path latency even though it does
  not change memory semantics.

This is sufficiently bounded for a prototype, but passive equivalence and
overhead remain mandatory gates before any live authorization.

## 14. Reviewer attack

1. **Is this OpenTelemetry plus a DAG?** Defensible. OpenTelemetry can carry L1
   spans, but it does not define or validate exact memory versions, effect
   scopes, commit evidence, or publication causality. MEG can export to OTel;
   its semantic contracts and fail-closed validator are the additional object.

2. **Is this a Graphiti internal trace schema?** Defensible at schema level.
   Graphiti names exist only in its adapter catalog. The core token, lineage,
   effect, visibility, publication, and certification records also map to a
   vector/action memory backend.

3. **Why not use a Ray DAG?** Defensible. Ray can execute a known DAG, but it
   cannot certify dynamic memory dependencies or effects that the backend has
   not materialized. It could be a future executor after MEG certification,
   not a replacement for the compiler.

4. **Why not let backend developers handwrite dependencies?** Defensible.
   Developers should declare L0 possibilities and key recipes. Actual targets,
   versions, conditional children, and commits are L1/L2 observations; allowing
   them to be handwritten would turn the proof into an assertion.

5. **Why is an effect journal required?** Defensible. A returned object is an
   intent/result, not proof that the intended IDs were atomically committed and
   became visible. Conflict and publication certification need the postcondition.

6. **Why not use source order as state version?** Defensible. Shared namespaces,
   retries, administrative writes, multiple streams, and failed commits break
   source-order/state equivalence. Source sequence remains lineage, not version.

7. **Why is request tracing insufficient?** Defensible. One edge child can issue
   multiple requests, one group can contain many edge children, and state/effect
   work occurs outside the LLM transport. Request timing cannot identify a
   candidate set, version, or commit.

8. **Could instrumentation overhead exceed the gain?** `OPEN_RISK`. Query
   projection, hashing, a durable journal, and context events add overhead. No
   performance claim is valid until passive paired runs measure it.

9. **What if most operators remain OPAQUE?** Defensible as a correctness result,
   but `OPEN_RISK` for performance contribution. The runtime must serialize or
   ignore opaque regions. Coverage is reported, never improved by weakening the
   rules.

10. **Does a second backend really reuse this?** Schema-level defense only. The
    Mem0 architecture uses the same core fields with different adapter hooks,
    but a pinned implementation and passive qualification have not been run.
    `OPEN_RISK` remains for integration cost and certification coverage.

## 15. Offline qualification and final gate

The isolated TDD prototype adds:

```text
src/paper_eval/membind_v4/mseg/semantic_adapter.py
src/paper_eval/membind_v4/mseg/semantic_compiler.py
src/paper_eval/membind_v4/mseg/effect_journal.py
src/paper_eval/membind_v4/mseg/version_token.py
src/paper_eval/membind_v4/mseg/publication.py
src/paper_eval/membind_v4/mseg/passive_equivalence.py
tests/test_membind_v4_meg_semantic_compiler.py
```

Synthetic coverage includes deterministic child and subrequest identity,
forbidden timing heuristics, L0/L2 separation, Graphiti boundary
classification, unknown-effect fail-closed behavior, commit/publication order,
logical version rejection of wall-clock evidence, per-edge fan-out, exact
journal matching, state/effect version agreement, parent dependency
provenance, lifecycle completeness, invalid publication causality, and passive
comparison invariants.

Offline verification performed in this round:

```text
focused semantic compiler tests: 27 passed
focused compiler plus non-artifact MSEG regression: 101 passed
Python compileall: passed
network calls: 0
services started: 0
sealed artifacts modified: no
```

The separate `test_membind_v4_mseg_artifacts.py` gate remains fail-closed because
its registered SHA-256 for the pre-existing sealed `V4_FINAL_DECISION.md` is
`7e971c...`, while the current repository file is `cc42a1...`. This round did
not change that sealed file or its expected hash. The mismatch is reported as
an existing artifact-integrity blocker, not repaired as part of MEG design.

Gate assessment:

| Requirement | Result |
|---|---|
| stable operator boundaries | yes, with semantic splits documented above |
| deterministic per-instance identity | yes, structured pre-schedule keys |
| explicit state version | yes in contract/prototype; production hook not installed |
| low-intrusion effect journal | yes on saga-free path via mutation/transaction wrapper |
| reliable publication evidence | yes via commit/bookmark plus causal journal design |
| shared Graphiti/second-backend core schema | yes at schema level |
| passive instrumentation can preserve semantics | yes in principle; live certificate not run |

The selected gate authorizes only the next offline/compiler prototype step. It
does not authorize a scheduler, policy, live candidate, performance claim, or
change to a frozen decision.

```text
GO_SEMANTIC_COMPILER_PROTOTYPE
```
