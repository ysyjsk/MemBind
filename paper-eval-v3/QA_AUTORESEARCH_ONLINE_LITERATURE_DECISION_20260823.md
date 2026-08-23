# QA Autoresearch Online Literature Decision

Date: 2026-08-23

Status: `GO_FREEZE_LAYERED_REVIEWER_SAFE_QA_PROTOCOL`

This note records the online literature checks used to choose the final QA
contract. It is a methodology decision record, not a claim that the current
four formal graph pairs demonstrate an unsafe B1.

## LongMemEval (ICLR 2025)

Source: [arXiv 2410.10813v2](https://arxiv.org/abs/2410.10813v2)

Pinned public implementation:

- repository: `https://github.com/xiaowu0162/LongMemEval`
- commit: `9e0b455f4ef0e2ab8f2e582289761153549043fc`
- generation source: `src/generation/run_generation.py`
- source SHA-256: `4f1eb3c69d7ad40f04065b9c0bc86f6582441018fc6ff751d162d66c95baf672`

The paper explicitly decomposes memory evaluation into indexing, retrieval, and
reading. It reports session-level `Recall@k` and `NDCG@k` separately from final
question-answering accuracy and evaluates Reader quality under an oracle
retrieval condition. The paper's §5.5 and the pinned code define the selected
reading variant:

```text
history_format=json
cot=true
con=true
```

With `con=true`, every retrieved session is first converted into a reading note;
the final Reader then reasons over JSON note objects. The final prompt asks the
model to extract relevant information first and reason over it second. This is
the basis for Candidate E, not a locally invented prompt variation.

The paper also reports a high-agreement GPT-4o Judge meta-evaluation and gives a
knowledge-update-specific Judge rule: an answer containing an older value and
the correct updated value is accepted. Our fixed Judge adapter preserves this
official authority; strict graph-current-state checks remain separate.

## Chain-of-Note (EMNLP 2024)

Source: [arXiv 2311.09210v2](https://arxiv.org/abs/2311.09210v2)

The paper's central finding is that sequential reading notes improve robustness
under noisy or irrelevant retrieved documents and improve rejection behavior.
This supports using CoN as a fixed Reader contract when retrieval recall is high
but the context contains many irrelevant sessions. It does not authorize
changing retrieval, choosing examples after seeing outcomes, or treating notes
as a graph-state oracle.

## Zep / Graphiti (2025)

Source: [arXiv 2501.13956v1](https://arxiv.org/abs/2501.13956v1)

Pinned public benchmark code:

- repository: `https://github.com/getzep/zep`
- commit: `be263ee23085410185835e0d8508b47fd35e9abb`
- source: `benchmarks/longmemeval/zep_longmem_eval.py`
- source SHA-256: `785eacdfd9a388ea00f636074579f7409e04a48d0c1bf5685022f3830a6b72d4`

The public implementation retrieves up to 20 graph edges and 20 entity nodes,
renders temporal validity fields, and sends the resulting facts/entities to a
fixed Reader and a question-type-aware Judge. This exactly motivates Candidate
B. Candidate B is retained as a graph-usability diagnostic because its evidence
surface is not equivalent to official session-value QA.

## MemoryAgentBench (ICLR 2026)

Source: [arXiv 2507.05257v4](https://arxiv.org/abs/2507.05257v4)

MemoryAgentBench separates Accurate Retrieval from Selective Forgetting and
FactConsolidation. Its selective-forgetting construction assigns explicit
serial numbers to facts, states that larger serial numbers are newer, and
requires the agent to resolve contradictions in favor of the newer fact. Its
LongMemEval evaluation still uses a Judge, while FactConsolidation uses
substring-exact-match-style scoring for short entity answers.

This audit establishes an important boundary for MemBind: the current four
formal LongMemEval graph pairs do not expose a sufficiently explicit serial
number/state-transition oracle to claim MemoryAgentBench-style overwrite
correctness. A future attack cohort would need to be selected and frozen from
that explicit workload structure; it must not be retrofitted into the current
four graphs.

## Decision

Candidate E (`JSON + Chain-of-Note`) is the preferred headline Reader because
it is directly grounded in LongMemEval's pinned implementation and improves the
current B0 calibration from `2/4` to `3/4` without changing construction,
retrieval, Graphiti, or B0/B1 execution policy. Candidate A remains the no-CoN
ablation. Candidate B and strict Candidate C remain diagnostics. Candidate D is
an oracle Reader upper bound.

The current paired evidence remains:

```text
Candidate E B0 = 3/4
Candidate E B1 = 3/4
Candidate E session Recall@10 = 1.0 (same retrieval identity as Candidate A)
B0 PASS -> B1 FAIL = 0
```

Therefore the reviewer-facing protocol is frozen, while the B1 unsafe claim is
not authorized on this cohort. No further prompt sweep is methodologically
justified. A new claim requires a new, explicitly state-dependent workload, not
another Reader variant over the same incomplete graph evidence.
