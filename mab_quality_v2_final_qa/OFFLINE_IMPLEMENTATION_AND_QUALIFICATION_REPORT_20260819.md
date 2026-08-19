# MAB Quality v2 Final QA Offline Implementation and Qualification Report

**Date:** 2026-08-19  
**Scope:** isolated offline implementation only  
**Live execution:** not performed

## Outcome

The final QA analysis path has been implemented in this new directory without
editing existing methodology, Quality v1, MemBind, test, or historical artifact
files. It enforces the workplan's public/private data boundary, one-build-many-QA
lifecycle, read-only QA phase, per-QA resume, result identity, invalid-aware
paired reduction, context-cluster bootstrap, bounded AutoResearch, and report
generation.

Live execution is not authorized for two independent reasons:

1. `127.0.0.1:8002` and `127.0.0.1:8003` both refused connections to
   `/v1/models` (HTTP status `000`).
2. The pinned official MAB LongMemEval subset fails one mandatory dataset
   mapping contract. Under the workplan this is
   `STOP_DATASET_MAPPING_UNQUALIFIED`.

## Official Dataset Qualification

Source inspected:

- Hugging Face dataset: `ai-hyz/MemoryAgentBench`
- dataset revision: `7ea066982b140a19337e17e60d45d4076e042faf`
- split: `Accurate_Retrieval`
- downloaded parquet SHA-256:
  `56c3cd80fb6731a3e53cd1a6be3148f54df60ff2d290ee50e28f8acebf9655c1`
- upstream code repository commit:
  `455306dcabc3842526eb83cd4e225e5d486c5c5d`
- filter: `metadata.source == "longmemeval_s*"`

The subset contains 5 multi-QA contexts and 300 QA items. Qualification found:

| Result | Contexts | QA |
|---|---:|---:|
| Fully mapped | 4 | 240 |
| Rejected | 1 | 60 |

The rejected context is the fifth selected record. Its QA index 38,
`question_id=0ddfec37_abs`, declares two gold sessions. One declared gold
session has no exact content match in the common public context and the session
content does not occur anywhere in that context. Because neither its real
chronology nor a valid session ID can be recovered, the adapter fails closed.

This implementation intentionally does not:

- invent a timestamp;
- fuzzy-match the missing session;
- silently remove the QA;
- run only the four valid contexts and call it the full result;
- use `has_answer` or answer text to repair public runtime inputs.

The machine-readable evidence is in
`evidence/MAB_DATASET_QUALIFICATION_RESULT_20260819.json`.

## Quality v1 Integration

Using the existing `paper-eval-v3` environment as a read-only dependency, the
first qualified official context was projected as 111 aligned session IDs,
dates, and turn arrays for 60 QA items. The existing Quality v1 implementation
successfully produced a ContextPack and rendered the existing Reader prompt.

The generated ContextPack and Reader prompt contained none of:

```text
has_answer
reference_answers
gold_session_ids
qa_pair_id
```

The imported Quality v1 context policy identity was:

```text
6c717c5a39af98e17d1b9fe55f0425f54401c13b9b82d9d1aeab5a2db26eef49
```

## Analysis Improvements

The reducer improves the final QA analysis in four specific ways:

1. Judge-invalid and infrastructure-invalid rows are reported separately and
   excluded from QA accuracy rather than converted to wrong answers.
2. U0 and MemBind are paired by exact `(context_id, qa_pair_id)` inventory;
   mismatched inventories hard-fail.
3. Confidence intervals resample contexts as clusters, respecting dependence
   among multiple QA items served by one constructed memory.
4. The report separates retrieval metrics, valid answer quality, paired
   disagreements, question types, and failure classes. It explicitly prevents
   a small or non-significant delta from being described as equivalence.

## Required Next Gate

No live run should begin from this dataset revision. The next legitimate input
is either an upstream-corrected, pinned MAB revision or a separately versioned
protocol amendment that freezes an exclusion before any U0/MemBind outcomes are
observed. After that, both model ports must pass `/v1/models`, all offline tests
must remain green, and the U0 six-QA smoke must run before the full comparison.
