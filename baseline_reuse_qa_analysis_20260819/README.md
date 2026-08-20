# Baseline-reuse QA analysis

This isolated directory reduces the existing four-history Quality Evaluation v1
evidence together with the completed SiliconFlow `Qwen/Qwen3-32B` Judge
validation. It performs no construction, retrieval, Reader, Judge, Neo4j write,
or historical-artifact mutation.

The claim scope is deliberately frozen as
`BASELINE_REUSE_4_HISTORY_NOT_MAB_MULTIQA`: the four historical baseline
contexts do not match the four selected MemoryAgentBench Multi-QA contexts.

Run the tests:

```bash
python -m unittest discover -s tests -v
```

Generate the report from frozen evidence:

```bash
python analyze.py \
  --input-manifest ../siliconflow_judge_validation_20260819/artifacts/siliconflow-validation-20260819-002/INPUT_MANIFEST.json \
  --judge-results ../siliconflow_judge_validation_20260819/artifacts/siliconflow-validation-20260819-002/RESULTS.json \
  --output-dir artifacts/baseline-reuse-qa-20260819-001
```
