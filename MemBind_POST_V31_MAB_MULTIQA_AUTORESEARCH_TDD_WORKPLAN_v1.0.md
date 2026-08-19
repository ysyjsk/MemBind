# MemBind Post-v3.1 MemoryAgentBench Multi-QA Quality Workplan v1.0

**Status:** NEW ISOLATED MODULE / DEVELOPMENT-ONLY UNTIL FREEZE  
**Date:** 2026-08-19  
**Parent methodology:** `MemBind_FINAL_METHODOLOGY_v3.1_FROZEN.md`  
**Current project state assumed by this plan:** MemBind v3.1 construction path has finished its current development execution; existing baseline/main-experiment/Quality Evaluation v1 results are immutable historical evidence.  
**New lane name:** `mab_quality_v2`  
**Core development discipline:** TDD + bounded AutoResearch + small-first live execution  
**Primary principle:** **add, do not rewrite; reuse, do not duplicate; probe before full run; never mutate historical evidence.**

---

# 0. Why this module exists

The current MemBind project already has two distinct evaluation concerns:

1. **construction/runtime performance**
   - makespan
   - goodput
   - freshness
   - service latency
   - queue delay
   - backlog
   - backend utilization
   - semantic/runtime violations

2. **downstream memory quality**
   - retrieval quality
   - Reader answer quality
   - Judge correctness

The existing Quality Evaluation v1 successfully validates the downstream path on the current four development histories, but each original LongMemEval history effectively provides only one QA target. This makes large-scale quality evaluation unnecessarily construction-heavy.

MemoryAgentBench reformulates long-memory evaluation around an **inject once, query multiple times** workload. For MemBind, this is particularly suitable because the expensive object of study is construction:

```text
one long context
    ↓
session/update stream
    ↓
construct memory ONCE
    ↓
seal namespace
    ↓
Q1 → retrieve → Reader → Judge
Q2 → retrieve → Reader → Judge
...
Qk → retrieve → Reader → Judge
```

Therefore this workplan adds an isolated Multi-QA quality lane without changing any existing performance workload or historical result.

The new module answers only:

> Under the same QA/retrieval policy, does the memory state produced by the completed MemBind method preserve downstream quality relative to the Native/U0 reference when one constructed memory is evaluated by many questions?

It does **not** redefine the MemBind methodology and does **not** replace the existing main experiment.

---

# 1. Absolute non-mutation boundary

## 1.1 Files/modules that must be treated as read-only

The agent MUST NOT modify the following existing methodology/evaluation surfaces merely to support MAB:

```text
MemBind_FINAL_METHODOLOGY_v3.1_FROZEN.md
（主实验）MemBind_PAPER_EVALUATION_WORKPLAN_v3.1_METHODOLOGY_ALIGNED.md

paper-eval-v3/src/paper_eval/quality_evaluation_v1.py
paper-eval-v3/src/paper_eval/quality_evaluation_v1_retrieval.py
paper-eval-v3/src/paper_eval/quality_evaluation_v1_reader.py
paper-eval-v3/src/paper_eval/quality_evaluation_v1_suite.py

paper-eval-v3/src/paper_eval/membind_v31/*
```

Existing modules may be **imported and called as read-only dependencies**.

Do not refactor them for convenience.

If a required compatibility layer cannot be implemented without editing these modules, STOP and document the incompatibility before changing anything.

## 1.2 Results/artifacts that are immutable

The agent MUST NOT:

- overwrite any existing `paper-eval-v3/artifacts/paper_eval/**` artifact;
- delete/recreate existing U0/A0/P(C=2)/MemBind namespaces;
- rewrite previous JSON/JSONL/TSV/report files;
- relabel an old run as a new MAB run;
- recompute an old result and silently replace it;
- mutate old checkpoints;
- change historical hashes;
- clean Neo4j globally;
- reuse an old namespace for the MAB workload.

The MAB lane owns a fresh artifact root and fresh namespaces only.

Example namespace pattern:

```text
pev3-mabqv2-<run_id>-<method>-<context_id>
```

Example artifact root:

```text
paper-eval-v3/artifacts/mab_quality_v2/<run_id>/
```

## 1.3 Existing v3.1 result is a parent, not a workspace

The completed v3.1 result may provide:

- code identity;
- method identity;
- frozen configuration identity;
- source implementation to invoke;
- comparator metadata.

It must never be used as a mutable directory for MAB experiments.

---

# 2. Architectural decision: add one isolated module

Create one new package:

```text
paper-eval-v3/src/paper_eval/mab_quality_v2/
├── __init__.py
├── contracts.py
├── dataset_adapter.py
├── compatibility.py
├── runner.py
├── autoresearch.py
├── reducer.py
└── artifacts.py
```

Create one new script:

```text
paper-eval-v3/scripts/run_mab_quality_v2.py
```

Create only new tests:

```text
paper-eval-v3/tests/mab_quality_v2/
├── test_contracts.py
├── test_dataset_adapter.py
├── test_compatibility.py
├── test_gold_blind.py
├── test_runner_one_build_many_qa.py
├── test_runner_resume.py
├── test_autoresearch.py
├── test_reducer.py
└── test_existing_results_immutable.py
```

Do not distribute new MAB logic across the existing Quality v1 or `membind_v31` packages.

---

# 3. What must be reused instead of reimplemented

The new module is an **orchestration/adapter layer**, not a second QA stack.

## 3.1 Reuse existing Quality v1 retrieval

Import and reuse:

```python
paper_eval.quality_evaluation_v1_retrieval.retrieve_quality_v1
```

The MAB module may provide the required:

```text
query
namespace
episode_uuid_to_session_id
```

but must not create a new Graphiti retrieval algorithm.

## 3.2 Reuse existing ContextPack and ranking metrics

Import and reuse from:

```python
paper_eval.quality_evaluation_v1
```

at minimum:

```text
build_context_pack(...)
session_ranking_metrics(...)
existing temporal diagnostics when applicable
```

The adapter should make MAB data look like the record shape already expected by Quality v1.

Do not create `ContextPackV2` merely because the source dataset changed.

## 3.3 Reuse existing Reader

Reuse:

```python
paper_eval.quality_evaluation_v1_reader.QualityEvaluationV1Reader
```

and its existing prompt/config identity.

No prompt tuning is allowed during this module's initial qualification.

## 3.4 Reuse the already-qualified Judge path

Use the project's frozen LongMemEval Judge path.

Do not:

- rewrite judge rubric;
- change answer labels;
- silently switch model;
- count judge failure as incorrect;
- tune judge based on MAB outputs.

Any final external/GPT re-judging, if later desired for the paper, must be an additional adjudication layer with its own identity and artifact root.

## 3.5 Reuse construction implementations through their existing public runner surface

The MAB module should adapt the MAB context into the same source episode/update type consumed by the existing construction lane.

Target flow:

```text
MAB record
   ↓
MAB dataset adapter
   ↓
existing MemBind workload/episode type
   ↓
existing U0 or completed MemBind construction runner
```

Do not bypass existing construction instrumentation by writing a second:

```python
for session in sessions:
    graphiti.add_episode(...)
```

implementation unless the existing runner truly exposes no callable surface.

If an adapter is required, put it in `mab_quality_v2/compatibility.py`.

---

# 4. Data model

The new module should expose a small internal model.

```python
@dataclass(frozen=True)
class MABSession:
    session_id: str
    source_sequence: int
    timestamp: str
    turns: tuple[dict[str, str], ...]
    source_sha256: str


@dataclass(frozen=True)
class MABQA:
    qa_pair_id: str
    question_id: str
    question: str
    reference_answers: tuple[str, ...]
    question_date: str
    question_type: str
    gold_session_ids: tuple[str, ...]


@dataclass(frozen=True)
class MABContext:
    context_id: str
    sessions: tuple[MABSession, ...]
    qa_items: tuple[MABQA, ...]
    context_sha256: str
```

Use exact upstream fields when available. Do not invent paper-result timestamps or labels.

---

# 5. Hard information-flow boundary: PUBLIC vs PRIVATE

This is a mandatory contract.

## 5.1 Public runtime projection

Only information legal for construction/retrieval/Reader:

```text
context/session identity
source sequence
timestamp/date
role
content
question
question date
```

## 5.2 Private evaluation projection

Only reducer/Judge/metric code may read:

```text
reference answer
has_answer
gold session labels
question type
qa_pair_id
other gold metadata
```

The adapter should explicitly expose two APIs, for example:

```python
adapter.public_context(...)
adapter.private_labels(...)
```

Do not pass one giant raw dataset dictionary through the whole pipeline.

## 5.3 Gold-blind invariant

Serialized construction, retrieval and Reader payloads must not contain:

```text
answer
answers
reference_answer
reference_answers
has_answer
gold_session_ids
```

This is a hard failure, not a warning.

---

# 6. Quality-v1 compatibility view

The most important reuse mechanism is a compatibility projection.

Implement in:

```text
mab_quality_v2/compatibility.py
```

something equivalent to:

```python
def to_quality_v1_record(context: MABContext) -> dict:
    return {
        "haystack_session_ids": [...],
        "haystack_dates": [...],
        "haystack_sessions": [
            [
                {"role": "...", "content": "..."},
                ...
            ],
            ...
        ],
    }
```

This allows the new workload to directly call:

```python
build_context_pack(...)
session_ranking_metrics(...)
```

without changing Quality v1.

The agent MUST verify that the session/date/session-content arrays preserve one-to-one alignment.

If real session chronology cannot be recovered unambiguously from MAB/its source mapping:

```text
STOP_DATASET_MAPPING_UNQUALIFIED
```

Do not synthesize formal timestamps merely to make tests pass.

Synthetic timestamps are allowed only in unit-test fixtures.

---

# 7. Construction semantics: one build, many QA

This is the primary runtime invariant.

For every:

```text
(method, context_id)
```

the allowed lifecycle is:

```text
NEW
  ↓
CONSTRUCTING
  ↓
CONSTRUCTED
  ↓
NAMESPACE_SEALED
  ↓
QA_000
QA_001
...
QA_K
  ↓
QUALITY_COMPLETE
```

Forbidden:

```text
Q1 → reconstruct
Q2 → reconstruct
Q3 → reconstruct
```

Required invariant:

```python
construction_count(method, context_id) == 1
```

and:

```python
qa_execution_count(method, context_id) == len(context.qa_items)
```

after a successful complete run.

---

# 8. TDD discipline

Follow the existing `paper-eval-v3` discipline:

```text
RED contract test
    ↓
minimum implementation
    ↓
focused GREEN
    ↓
full offline regression
    ↓
small live smoke
```

Do not start with live Graphiti/vLLM calls.

## 8.1 TDD-0: immutability guard

Write the test before implementation.

Take a manifest/hash snapshot of protected historical paths.

The new test should verify that running any offline MAB unit does not change those paths.

At minimum guard:

```text
existing Quality v1 artifacts
existing main baseline artifacts
existing v3.1 formal result artifacts
```

A practical implementation may snapshot file size + SHA-256 for all regular files under configured protected roots.

## 8.2 TDD-1: adapter schema

RED tests:

- context IDs are stable and non-empty;
- session IDs unique within context;
- source sequence is deterministic;
- QA IDs unique;
- question/answer inventory lengths match;
- hash is deterministic;
- malformed inputs fail closed;
- chronology mapping is explicit.

Then implement the smallest adapter that passes.

## 8.3 TDD-2: gold blindness

Create a fixture deliberately containing:

```text
has_answer
reference_answers
gold_session_ids
```

Assert that:

```text
public_context()
to_quality_v1_record()
construction payload
retrieval payload
Reader payload
```

contain none of them.

## 8.4 TDD-3: Quality v1 reuse

Mock one MAB context and prove that it can pass through existing:

```text
build_context_pack
session_ranking_metrics
Reader rendering
```

without modifying those implementations.

## 8.5 TDD-4: one-build-many-QA

Fixture:

```text
1 context
3 sessions
5 QA
```

Expected:

```text
construction calls = 1
retrieval calls    = 5
reader calls       = 5
judge calls        = 5
```

This is a hard contract test.

## 8.6 TDD-5: read-only QA phase

After `NAMESPACE_SEALED`, any Graphiti mutation call must cause:

```text
QA_PHASE_WRITE_VIOLATION
```

The QA phase is read-only.

## 8.7 TDD-6: resume

Given completed:

```text
Q0 Q1 Q2
```

and interrupted before `Q3`, restart must:

```text
reuse sealed namespace
skip Q0/Q1/Q2
continue Q3...
```

It must not reconstruct the context.

## 8.8 TDD-7: result identity

Each row key:

```text
(method, context_id, qa_pair_id)
```

must be unique.

Each result must bind:

```text
dataset revision/hash
context hash
construction manifest hash
method implementation hash
retrieval config hash
Reader config hash
Judge config hash
```

## 8.9 TDD-8: regression

Before every live phase run:

```text
new MAB focused tests
+
existing Quality v1 focused tests
+
existing v3.1 offline regression subset
```

No live run is authorized when a regression is red.

---

# 9. AutoResearch philosophy for this module

AutoResearch here is **not unrestricted hyperparameter search**.

Its purpose is:

> use a tiny real workload to expose adaptation/runtime problems early, inspect decomposed signals, make at most a few isolated engineering candidates, and freeze before expensive full evaluation.

This intentionally follows the existing MemBind v3.1 pattern:

```text
small fixed probe
bounded candidates
append-only decision ledger
no automatic merge authority
keep/discard/crash
```

## 9.1 What AutoResearch MAY change

Only new-module implementation details that do not alter the scientific QA policy, e.g.:

- MAB schema parsing;
- official-source session/date mapping;
- compatibility projection bugs;
- namespace/checkpoint/resume bugs;
- transport error handling;
- deterministic batching of independent QA calls;
- read-only execution plumbing;
- artifact persistence;
- memory-safe buffering;
- explicit mapping of MAB identities to existing episode identities.

## 9.2 What AutoResearch MUST NOT tune

During the initial MAB qualification it must not search over:

```text
retrieval Top-K
Graphiti search algorithm
ContextPack selection heuristic
Reader prompt
Reader model
Reader max tokens
Judge prompt
Judge rubric
Judge model
gold labels
question subset chosen by observed accuracy
question type weights
non-inferiority margin after seeing full results
```

Those would transform a compatibility/quality evaluation into development-set optimization.

If later a genuine Quality-v2 algorithmic change becomes necessary, it must be proposed as a **separate versioned protocol**, not silently folded into this workplan.

---

# 10. AutoResearch controller design

Implement:

```text
mab_quality_v2/autoresearch.py
```

with no direct service/database dependency where possible.

Suggested constants:

```python
PROBE_CONTEXT_COUNT = 1
PROBE_QA_COUNT = 6
MAX_CANDIDATES = 3
MERGE_AUTHORITY = "NONE_NON_MERGEABLE_DEVELOPMENT_PROBE"
```

Do not choose probe questions by manually selecting easy/hard outcomes after seeing answers.

Use deterministic selection from metadata/IDs.

Preferred probe QA composition, if metadata supports it:

```text
2 single-session
1 multi-session
1 knowledge-update
1 temporal
1 remaining deterministic category/sample
```

If exact categories differ, sample deterministically across available categories.

## 10.1 Candidate lifecycle

```text
c00 = direct minimal implementation
c01 = at most one evidence-backed engineering fix
c02 = at most one additional evidence-backed engineering fix
```

Each candidate gets:

```text
candidate_id
parent_code_sha256
code_sha256
dataset_manifest_sha256
status = keep | discard | crash
pipeline_valid
gold_blind_valid
construction_count
qa_count
retrieval_valid_count
reader_valid_count
judge_valid_count
retrieval metrics summary
qa accuracy summary
failure decomposition
description
merge_authority = NONE...
payload_sha256
```

## 10.2 Candidate decision policy

This QA AutoResearch loop is not allowed to chase accuracy.

A candidate may be `keep` only if:

```text
all hard invariants pass
AND
pipeline validity does not regress
AND
the candidate fixes a diagnosed engineering failure
AND
it does not change frozen QA semantics
```

Examples of legitimate improvement:

```text
judge_valid: 3/6 → 6/6 because resume/transport bug fixed
session mapping invalid → valid because official date mapping fixed
duplicate QA execution → eliminated
construction called 6 times → corrected to 1
```

Example of illegitimate improvement:

```text
QA accuracy 3/6 → 5/6 because Top-K changed after observing the six answers
```

That must be rejected under this workplan.

## 10.3 Append-only ledger

Persist:

```text
artifacts/mab_quality_v2/<probe_run_id>/autoresearch/results.tsv
```

Never rewrite prior candidate rows.

Recommended columns:

```text
candidate_id
parent_code_sha256
code_sha256
status
pipeline_valid
gold_blind_valid
construction_count
qa_count
retrieval_valid_count
reader_valid_count
judge_valid_count
qa_accuracy
recall_at_1
recall_at_3
recall_at_5
recall_at_10
mrr
ndcg_at_10
failure_class
description
payload_sha256
```

A candidate has no automatic authority to become the formal MAB configuration.

A separate freeze artifact performs that transition.

---

# 11. Execution stages

The entire work is deliberately short and progressive.

Do not add another large validation ladder.

## Stage M0 — Inventory and freeze existing state

**Offline only.**

Tasks:

1. record current git/code identity;
2. locate existing v3.1 final/completed result artifacts;
3. locate existing U0/baseline artifacts;
4. locate Quality v1 sealed artifacts;
5. hash protected artifact roots;
6. record existing namespace inventory without modifying it;
7. create `MAB_QUALITY_V2_PARENT_STATE.json`.

Output:

```text
parent_state_sha256
protected_paths
protected_file_hash_manifest
existing_namespace_inventory
existing_quality_v1_identity
v31_method_identity
```

Exit:

```text
PASS_PARENT_STATE_FROZEN
```

No live call.

---

## Stage M1 — Dataset qualification

**Offline only.**

Load/pin MemoryAgentBench data and inspect only the intended LongMemEval-derived subset.

Produce:

```text
MAB_QUALITY_V2_DATASET_MANIFEST.json
```

Required fields:

```text
dataset source
dataset revision
dataset hash
number of contexts
QA count per context
session count per context
question type counts
question date availability
session chronology availability
question ID uniqueness
qa_pair_id uniqueness
```

The agent must explicitly answer:

> Can each MAB session be mapped to a stable session ID and real chronology/date without invention?

If no:

```text
STOP_DATASET_MAPPING_UNQUALIFIED
```

Do not proceed by making timestamps up.

---

## Stage M2 — TDD adapter + compatibility implementation

Implement only:

```text
contracts.py
dataset_adapter.py
compatibility.py
artifacts.py
```

Run focused tests until green.

Then run existing relevant offline regression.

Exit:

```text
PASS_MAB_ADAPTER_OFFLINE
```

No Graphiti/vLLM.

---

## Stage M3 — TDD runner implementation

Implement:

```text
runner.py
```

using mocked construction/retrieval/Reader/Judge.

Required mock proof:

```text
1 context × 5 QA
construction=1
retrieval=5
reader=5
judge=5
```

Add resume/read-only tests.

Exit:

```text
PASS_ONE_BUILD_MANY_QA_OFFLINE
```

No Graphiti/vLLM.

---

## Stage M4 — AutoResearch smoke

This is the first live phase.

Run:

```text
U0
× 1 MAB context
× 6 deterministic QA
```

Why U0 first:

- it tests dataset adaptation without confounding MemBind runtime;
- it establishes whether the frozen QA stack has a usable signal on MAB;
- it catches schema/date/provenance/Reader/Judge plumbing bugs before expensive MemBind construction.

Record decomposed outcomes:

```text
construction valid?
namespace sealed?
retrieval valid?
gold sessions represented?
ContextPack valid?
Reader valid?
Judge valid?
QA correct?
```

Do not react only to final QA accuracy.

### M4 decision

If pipeline is valid:

```text
FREEZE_ENGINEERING_PATH
```

If a concrete implementation defect exists:

```text
open c01
```

At most 3 candidates total.

If the problem requires changing QA semantics:

```text
STOP_PROTOCOL_CHANGE_REQUIRED
```

Do not silently tune.

---

## Stage M5 — Freeze Quality-v2 execution identity

Once M4 has a valid candidate, write:

```text
MAB_QUALITY_V2_FREEZE.json
```

Bind:

```text
dataset revision/hash
adapter implementation hash
compatibility hash
runner hash
retrieval config hash
ContextPack implementation hash
Reader config hash
Judge config hash
namespace scheme
artifact schema
question inventory hash
AutoResearch selected candidate
```

After this point:

> no parameter/prompt/retrieval-policy adaptation based on MAB results.

Any change requires a new version, e.g. `mab_quality_v2.1`.

---

## Stage M6 — U0 full Multi-QA qualification

Run all selected MAB contexts under U0.

For each context:

```text
construct once
seal namespace
answer all QA
persist per-QA rows
```

Do not reconstruct due to QA failures.

If a Reader/Judge call fails, persist invalid status and allow per-QA resume.

Primary goal:

> establish a stable Native quality reference under the exact frozen MAB QA pipeline.

Outputs:

```text
U0 per-QA rows
U0 per-context summary
U0 overall summary
failure decomposition
```

---

## Stage M7 — Completed MemBind v3.1 Multi-QA run

Use:

```text
same MAB dataset manifest
same question inventory
same retrieval policy
same Reader
same Judge
same construction LLM/embedding/backend envelope as required by the selected comparison
```

but a fresh MemBind namespace per context.

Never reuse U0 namespace as MemBind input.

For each context:

```text
MemBind v3.1 construction once
seal
same QA inventory
```

Outputs go under new method-specific directories.

This stage does not modify the old v3.1 performance result. It creates a new downstream-quality result whose parent method identity points to v3.1.

---

## Stage M8 — Paired reducer

Implement/use:

```text
reducer.py
```

Primary key:

```text
(context_id, qa_pair_id)
```

Compare:

```text
U0 vs MemBind-v3.1
```

per exact same QA.

Report:

```text
both_correct
u0_only_correct
membind_only_correct
both_wrong
invalid_u0
invalid_membind
```

Also report:

```text
QA Accuracy
Recall@1
Recall@3
Recall@5
Recall@10
MRR
nDCG@10
```

plus question-type breakdown.

Do not reduce Judge-invalid rows into ordinary incorrect rows.

---

# 12. Metrics and failure decomposition

## 12.1 Retrieval metrics

Reuse existing session-level metrics:

```text
Recall@1
Recall@3
Recall@5
Recall@10
MRR
nDCG@10
```

Keep existing temporal diagnostics when data mapping supports them.

## 12.2 Answer quality

Primary:

```text
Judge-valid QA Accuracy
```

Always report:

```text
valid judge denominator
invalid judge count
```

## 12.3 Paired quality preservation

Primary comparison:

```text
ΔQ = Accuracy(MemBind) - Accuracy(U0)
```

Also report the paired disagreement table.

Do not claim equivalence merely because a significance test is non-significant.

If a non-inferiority margin is later used, freeze it before looking at the full MemBind-vs-U0 result.

## 12.4 Context-aware uncertainty

Because multiple questions share one constructed memory, QA rows are not fully independent.

For paper-level confidence intervals, prefer cluster-aware/bootstrap resampling by context rather than treating every QA as IID.

This statistical analysis belongs to reducer/reporting only; it must not change runtime behavior.

## 12.5 Failure taxonomy

Every invalid row should be classified as one of:

```text
DATASET_MAPPING_INVALID
CONSTRUCTION_FAILED
NAMESPACE_NOT_SEALED
RETRIEVAL_FAILED
CONTEXT_PACK_INVALID
READER_FAILED
READER_INVALID_FINISH
JUDGE_FAILED
JUDGE_INVALID
GOLD_LEAK_DETECTED
QA_PHASE_WRITE_VIOLATION
RESUME_IDENTITY_MISMATCH
ARTIFACT_HASH_MISMATCH
UNKNOWN_INFRA_FAILURE
```

Do not mix infra failures into quality degradation.

---

# 13. Artifact layout

Use only the new root:

```text
paper-eval-v3/artifacts/mab_quality_v2/<run_id>/
├── parent_state.json
├── dataset_manifest.json
├── freeze.json
├── runtime_manifest.json
│
├── autoresearch/
│   ├── authorization.json
│   ├── c00/
│   ├── c01/
│   ├── c02/
│   └── results.tsv
│
├── construction/
│   ├── U0/
│   │   └── <context_id>/
│   └── MEMBIND_V31/
│       └── <context_id>/
│
├── qa/
│   ├── U0/
│   │   └── rows.jsonl
│   └── MEMBIND_V31/
│       └── rows.jsonl
│
├── summary/
│   ├── u0.json
│   ├── membind_v31.json
│   ├── paired.json
│   ├── by_question_type.json
│   └── failures.json
│
└── FINAL_MAB_QUALITY_V2_REPORT.md
```

All writes should be atomic where the existing artifact helpers support it.

---

# 14. Resume semantics

Construction and QA resume are different.

## 14.1 Construction resume

Use the existing construction runner's own durable semantics.

The MAB module must not invent a second partial-construction recovery model.

## 14.2 QA resume

Once a namespace is verified sealed:

```text
completed QA rows are immutable
```

Restart:

1. verify namespace identity;
2. verify construction manifest hash;
3. verify freeze hash;
4. load completed `(method, context, qa)` keys;
5. execute only missing QA.

Never delete existing valid QA rows and start over for convenience.

---

# 15. STOP rules

Immediately stop the current run if any of the following occurs:

1. protected historical artifact hash changes;
2. an old namespace is selected as a MAB write target;
3. MAB chronology cannot be mapped without fabrication;
4. question/answer/ID inventory is inconsistent;
5. duplicate `qa_pair_id` appears in one execution inventory;
6. gold label enters a public/runtime payload;
7. QA phase performs a Graphiti write;
8. one context is reconstructed because it has multiple QA;
9. resume tries to reconstruct an already sealed context;
10. U0 and MemBind use different QA inventories;
11. retrieval/Reader/Judge identity drifts after freeze;
12. Judge-invalid is counted as normal incorrect;
13. a live AutoResearch candidate changes frozen QA semantics;
14. more than three AutoResearch candidates are attempted without a versioned protocol change;
15. full evaluation begins before AutoResearch engineering freeze.

Failure status:

```text
FAILED_NON_REUSABLE
```

for any run whose identity/semantics cannot be trusted.

---

# 16. What AutoResearch should inspect after the first small run

After M4, the agent should produce a concise diagnostic table:

| Layer | Question |
|---|---|
| Dataset | Are session/date/QA identities valid? |
| Construction | Was one context constructed exactly once? |
| Namespace | Is the expected namespace complete and sealed? |
| Retrieval | Are gold sessions present near the top? |
| ContextPack | Did deterministic selection preserve relevant rounds? |
| Reader | Did the Reader receive valid temporal context and stop normally? |
| Judge | Is the output valid under the frozen Judge? |
| QA | Is the answer correct? |

Decision logic:

```text
retrieval poor
→ diagnose retrieval/source mapping
→ DO NOT immediately tune Reader

retrieval good + Reader wrong
→ diagnose ContextPack/Reader compatibility
→ DO NOT change construction

Reader answer good + Judge invalid
→ diagnose Judge transport/integration
→ DO NOT count as quality failure

all layers valid
→ freeze
→ run full U0
```

The point is to identify the **first failing layer**, not to optimize the final scalar accuracy blindly.

---

# 17. Anti-overengineering rules

This workplan intentionally prevents a repeat of excessive validation.

The agent must NOT add a new validation stage unless it protects one of these hard risks:

```text
historical-result mutation
gold leakage
dataset identity
one-build-many-QA semantics
read-only QA
resume correctness
method/QA identity drift
```

Do not add standalone experiments for facts already provable from tests or manifests.

Examples of unnecessary work:

- benchmarking JSON serialization choices;
- trying five namespace naming schemes;
- creating a generic benchmark framework for future datasets;
- implementing a general DAG engine;
- adding new retrieval algorithms;
- reproducing all old Quality v1 experiments;
- rerunning historical baselines just to unify orchestration;
- constructing all MAB contexts before a one-context smoke works.

The critical path is:

```text
adapter
→ one-build-many-QA test
→ 1-context live smoke
→ freeze
→ U0 full
→ MemBind full
→ paired quality result
```

Anything not serving this path should be deferred.

---

# 18. Definition of Done

The module is complete when all are true:

## Implementation

- [ ] `mab_quality_v2` exists as a new isolated package.
- [ ] Existing Quality v1 and v3.1 source files remain unchanged.
- [ ] Existing result/artifact hashes remain unchanged.
- [ ] MAB adapter produces stable source/session/QA identities.
- [ ] Public/private projections are separated.
- [ ] Quality v1 retrieval/ContextPack/Reader are reused.
- [ ] Existing Judge path is reused.
- [ ] one-build-many-QA is enforced.
- [ ] QA is read-only.
- [ ] per-QA resume works.

## TDD

- [ ] all new focused tests green;
- [ ] gold-blind test green;
- [ ] immutability test green;
- [ ] one-build-many-QA test green;
- [ ] resume test green;
- [ ] existing relevant regression green.

## AutoResearch

- [ ] one deterministic small probe executed;
- [ ] candidate count <= 3;
- [ ] append-only ledger produced;
- [ ] no candidate changes QA semantics;
- [ ] selected engineering path frozen.

## Results

- [ ] U0 Multi-QA result complete;
- [ ] MemBind-v3.1 Multi-QA result complete;
- [ ] same QA inventory verified;
- [ ] paired reducer complete;
- [ ] per-question-type result complete;
- [ ] invalid/infra failures separated;
- [ ] final report generated.

---

# 19. Agent execution prompt

The following is the concise implementation authority for an autonomous coding agent.

> You are adding a new `mab_quality_v2` evaluation lane after the existing MemBind v3.1 work. Treat all existing methodology files, `quality_evaluation_v1*`, `membind_v31/*`, existing namespaces, logs and artifacts as read-only historical evidence. Do not modify or rerun them for convenience.
>
> Implement the new lane as an isolated package under `paper-eval-v3/src/paper_eval/mab_quality_v2/`. MemoryAgentBench is only the new workload/data source. Adapt its LongMemEval-derived multi-QA context into the existing MemBind source episode/update representation and reuse the existing U0/MemBind construction implementations. Do not use a separate generic MAB memory agent as the formal MemBind execution path.
>
> Reuse the existing Quality v1 retrieval, ContextPack/ranking metrics, Reader and already-qualified Judge as read-only dependencies. Build a compatibility projection that supplies the exact `haystack_session_ids`, `haystack_dates`, and `haystack_sessions` shape expected by Quality v1. Keep gold answers/`has_answer`/gold session labels in a private scoring projection; hard-fail if they appear in construction/retrieval/Reader payloads.
>
> Enforce the primary invariant: each `(method, context_id)` is constructed exactly once, then its sealed namespace serves all QA read-only. Implement per-QA resume without reconstruction.
>
> Use TDD: RED contract → minimum implementation → focused GREEN → relevant offline regression → live. Before any live call, tests must cover historical-artifact immutability, dataset identity, gold blindness, Quality-v1 compatibility, one-build-many-QA, read-only QA, resume and result identity.
>
> Use bounded AutoResearch only after offline gates pass: `1 context × 6 deterministic QA`, U0 first, at most 3 candidates, append-only ledger, no merge authority. AutoResearch may fix schema/mapping/resume/transport/artifact engineering issues but must not tune retrieval Top-K, ContextPack policy, Reader prompt/model, Judge prompt/model, gold labels or QA subset based on observed accuracy.
>
> Once the first valid engineering path is obtained, freeze dataset/adapter/runner/retrieval/Reader/Judge/question inventory hashes. Then run full U0 Multi-QA, followed by the completed MemBind-v3.1 method on the exact same MAB QA inventory using fresh namespaces. Produce per-QA rows, Recall@1/3/5/10, MRR, nDCG@10, Judge-valid QA Accuracy, failure decomposition, question-type breakdown and exact U0-vs-MemBind paired disagreements.
>
> Do not create extra validation stages unless they protect a named hard invariant. The critical path is `adapter → offline tests → one-context live smoke → freeze → U0 full → MemBind full → paired reducer`. Prefer a usable result over additional speculative framework work.

---

# 20. Future extension: v4 or later methods

This module should not be hard-coded so that `MEMBIND_V31` is the only non-U0 method forever.

However, do not build a generic plugin framework now.

Use a minimal method adapter interface, e.g.:

```python
Protocol ConstructionMethod:
    method_id: str
    implementation_sha256: str

    async def construct(context, namespace, output_root):
        ...
```

Initially register only:

```text
U0
MEMBIND_V31
```

If v4 later becomes the final method:

```text
add MEMBIND_V4 as a new method identity
use fresh namespaces
use the same frozen MAB QA inventory where scientifically permitted
preserve U0 and v3.1 rows unchanged
produce a new paired comparison
```

Never overwrite `MEMBIND_V31` with v4 results.

This keeps the new quality lane useful while preserving complete historical provenance.

---

# 21. Source basis

Project sources to treat as implementation authority:

- `MemBind_FINAL_METHODOLOGY_v3.1_FROZEN.md`
- `paper-eval-v3/src/paper_eval/quality_evaluation_v1.py`
- `paper-eval-v3/src/paper_eval/quality_evaluation_v1_retrieval.py`
- `paper-eval-v3/src/paper_eval/quality_evaluation_v1_reader.py`
- `paper-eval-v3/src/paper_eval/quality_evaluation_v1_suite.py`
- `paper-eval-v3/src/paper_eval/membind_v31/autoresearch.py`
- `paper-eval-v3/src/paper_eval/membind_v31/optimization_pilot.py`
- `paper-eval-v3/QUALITY_EVALUATION_V1_SOURCE_ADAPTATION_AND_RESULT_REPORT_20260817.md`

External workload authority:

- MemoryAgentBench official repository: `https://github.com/HUST-AI-HYZ/MemoryAgentBench`
- LongMemEval official repository: `https://github.com/xiaowu0162/LongMemEval`

The external benchmark defines the workload; the existing MemBind repository defines how construction, retrieval, Reader and Judge are executed for this project.

---

# Final execution principle

```text
DO NOT TOUCH OLD RESULTS

new isolated MAB module
        ↓
TDD proves hard invariants
        ↓
small U0 AutoResearch smoke
        ↓
fix only diagnosed engineering defects
        ↓
freeze
        ↓
one construction → many QA
        ↓
U0 full quality
        ↓
MemBind-v3.1 full quality
        ↓
paired result
```

**The goal is not to build another evaluation framework. The goal is to obtain a statistically useful multi-QA quality result with the smallest defensible addition to the completed v3.1 project.**
