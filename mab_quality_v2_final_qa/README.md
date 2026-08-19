# Isolated MAB Quality v2 Final QA

This directory is a new, development-only QA analysis lane based on
`MemBind_POST_V31_MAB_MULTIQA_AUTORESEARCH_TDD_WORKPLAN_v1.0.md`. It is
deliberately separate from the existing `paper-eval-v3` packages and artifacts.

The lane provides:

- an official MemoryAgentBench/LongMemEval-shaped adapter;
- explicit public/runtime and private/scoring projections;
- one construction followed by many read-only QA requests;
- append-only, resumable QA attempts;
- Quality Evaluation v1 compatibility calls (read-only imports only);
- paired U0/MemBind reduction with invalid rows kept out of accuracy;
- context-cluster bootstrap intervals and question-type breakdowns;
- bounded AutoResearch candidate bookkeeping with no accuracy tuning;
- an auditable Markdown final QA report.

No live request is made by default. The optional CLI `--live` gate requires both
`127.0.0.1:8002` and `127.0.0.1:8003` to answer `GET /v1/models`; it refuses to
run otherwise. The current workspace check found neither port available, so
only the offline test suite is authorized for this implementation.

## Offline usage

```bash
cd mab_quality_v2_final_qa
python -m pip install -e '.[test]'
pytest
python run_mab_quality_v2.py inspect path/to/mab.json --source 'longmemeval_s*'
```

The input may be a JSON list, a JSON object with `data`, a JSONL stream, or one
MAB record. Parquet/Hugging Face loading is intentionally left to the caller so
that a pinned dataset file and hash are always explicit in the run manifest.

## Hard boundaries

The runner never gives `reference_answers`, `gold_session_ids`, `has_answer`,
`question_type`, or `qa_pair_id` to construction, retrieval, or Reader
callbacks. Judge and metric callbacks receive a private label only after the
runtime phase has returned. QA callbacks receive a read-only graph facade;
mutation methods raise `QA_PHASE_WRITE_VIOLATION`.
