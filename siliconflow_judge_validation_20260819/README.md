# SiliconFlow Judge validation (2026-08-19)

This directory independently re-judges the frozen Quality Evaluation v1 answers for
`U0` (native) and `P(C=2)`. It does not modify the source run, any existing paper
artifact, Neo4j, or the MemBind implementation.

## Evaluation contract

- Source run: `qev1-dev-20260817-001`.
- Exact paired histories: `07741c45`, `b6019101`, `6071bd76`, `a2f3aa27`.
- Two methods and four answers per method: eight Judge requests after one model-list
  preflight request.
- Rubric: the pinned official LongMemEval `get_anscheck_prompt` vendored at
  `membind-validation/src/evaluation/vendor/longmemeval_evaluate_qa.py`.
- Generation: temperature `0`, `max_tokens=16`, thinking disabled, and one HTTP
  attempt per operation with no application retry.
- Parsing: after trimming whitespace and normalizing case, only `yes`, `yes.`, `no`,
  and `no.` are valid. Invalid outputs are reported and excluded from the accuracy
  denominator.
- Privacy: the key is read only from `SILICONFLOW_API_KEY`. Artifacts contain neither
  the key, Authorization headers, nor raw model/API responses. Judge text is retained
  only as a SHA-256 digest plus its character count.
- Scope: this four-question result is a development diagnostic, not a paper-level
  significance claim.

The most important adjudication case is `6071bd76`: both frozen predictions say
"more water" while describing a change from 6 ounces to 5 ounces per tablespoon;
the reference answer says "less water (5 ounces)".

## Test and run

From this directory:

```bash
python -m unittest discover -s tests -v
python run_validation.py --dry-run --output-dir artifacts/dry-run-contract-check
```

For a live call, enter the key without placing it in shell history or command-line
arguments:

```bash
read -rsp "SiliconFlow API key: " SILICONFLOW_API_KEY
printf '\n'
export SILICONFLOW_API_KEY
python run_validation.py \
  --proxy-mode direct \
  --model Qwen/Qwen3-32B \
  --output-dir artifacts/siliconflow-validation-20260819-002
unset SILICONFLOW_API_KEY
```

The runner first calls `GET https://api.siliconflow.cn/v1/models`. With the explicit
model argument above it verifies that `Qwen/Qwen3-32B` is available, then sends the
eight paired prompts. A successful run writes `RESULTS.json`; a preflight or runtime
failure writes `FAILURE.json`, with partial results separated when applicable.

## Current execution status

The live run completed successfully after the network became available. `GET /models`
returned 91 models, and the run explicitly selected `Qwen/Qwen3-32B` (the text model;
the VL and Thinking variants were not used). It sent exactly eight Judge requests,
one per frozen answer, with no invalid outputs.

The result is in `artifacts/siliconflow-validation-20260819-002/RESULTS.json`:

| Method | Valid | Correct | SiliconFlow accuracy | Agreement with frozen Qwen label |
|---|---:|---:|---:|---:|
| `U0` | 4 | 2 | `0.500` | `4/4 = 1.000` |
| `P(C=2)` | 4 | 2 | `0.500` | `4/4 = 1.000` |

The returned labels for both methods were `yes, yes, no, no` in the frozen history
order. The focus case `6071bd76` remained `no` for both methods: each prediction says
"more water" while describing a change from 6 ounces to 5 ounces per tablespoon; the
reference answer says "less water (5 ounces)".

The earlier network-blocked attempt is retained separately as an auditable diagnostic
at `artifacts/siliconflow-validation-20260819-001/FAILURE.json`; it is not used as the
final result.
