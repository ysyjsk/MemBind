# MemBind V7 Sealed Evidence Audit

Date: 2026-08-29  
Scope: read-only matched audit of the sealed `B0/NATIVE_SERIAL` anchor and
`V7_FRESH_CONTROL_V1` prefix-30 control on `local-qwen3-8b-awq-dualreplica-v1`.

The machine-readable result is
`/data/predator/ly/Mem/experiments/local-qwen3-8b-awq-dualreplica-v1/v7_audit/sealed-evidence-audit-20260829/RESULT.json`.

## Executive decision

The audit passes the public resource/fairness contract and the matched
engineering QA overlay. It does **not** authorize a V7 incremental treatment:
the real observer remains fail-closed (`V7B_ARCHITECTURE_NULL`), and D0/D1
online economics are unknown rather than positive.

The most important performance fact is an algorithm tax in the current FRESH
control, not a hardware mismatch:

| Arm | Role | Sources | Wall-clock construction |
|---|---|---:|---:|
| B0 / `NATIVE_SERIAL` | Native headline anchor | 30 | 2636.463018176 s |
| `V7_FRESH_CONTROL_V1` | from-scratch control | 30 | 3958.332938057 s |

`T_B0 / T_FRESH = 0.666053882`; equivalently, FRESH is `1.501380035x`
the B0 wall time, a `50.138%` slowdown. This is not reported as a V7
speedup because FRESH is a control, not the incremental treatment.

## Fairness contract

The two sealed runs use the same:

- profile and two physical RTX 3090 Ti GPUs (same GPU UUIDs);
- Qwen3-8B-AWQ checkpoint/revision and tokenizer/weight catalog;
- Qwen3-Embedding-0.6B BF16 model, 1024 dimensions, and embedding endpoint;
- Neo4j backend (`bolt://127.0.0.1:7687`, database `neo4j`);
- software versions (`vllm 0.26.0`, `torch 2.11.0`, `openai 3.3.1`,
  `httpx 0.28.1`, driver `580.173.02`);
- 30-source workload. The canonical workload JSONL digest is
  `4c3c336586195316ee6639c7f6aab3649378df37b5e930825f470aa4015380d7`.

The platform manifest payload hashes differ (`87f1d22e...` for B0 and
`e0f35607...` for FRESH) because they were captured at different times and
contain method-specific routing entries. Comparing the public resource
projection gives an empty diff. B0 and FRESH both preserve
`B0_SERIAL_STATEFUL_ORDERED_PUBLICATION`; their route-policy entries are
method metadata, not hidden hardware changes.

## Matched quality overlay

The same frozen Quality-v1 retrieval, Reader, and official LongMemEval Judge
contract was run against each persisted namespace. Construction latency was
excluded and all database operations were read-only.

| Metric | B0 | V7-FRESH |
|---|---:|---:|
| Questions | 11 | 11 |
| Valid Judge results | 11 | 11 |
| Accuracy | 0.5454545455 | 0.5454545455 |
| Mean Recall@10 | 0.9136363636 | 0.9136363636 |
| Database mutations | 0 | 0 |
| Namespace unchanged | true | true |

This is a prefix engineering qualification only. It is not full-five-history
non-inferiority and `headline_noninferiority_authorized=false` remains true.

## Algorithm-tax accounting

The observed work counts are:

| Counter | B0 | FRESH | FRESH / B0 |
|---|---:|---:|---:|
| Logical LLM calls | 858 | 863 | 1.0058x |
| Physical transport attempts | 1732 | 2050 | 1.1836x |
| Embedding calls | 1088 | 1592 | 1.4632x |
| Neo4j reads | 3161 | 4811 | 1.5220x |
| Neo4j writes | 150 | 150 | 1.0000x |

Logical-call span sums (these are trace accounting sums and may contain
overlap) identify where the FRESH decomposition pays for its boundary:

| Logical operator | B0 calls / span sum | FRESH calls / span sum |
|---|---:|---:|
| `extract_nodes.extract_message` | 30 / 794.632 s | 30 / 203.475 s |
| `extract_edges.edge` | 30 / 1117.993 s | 30 / 541.403 s |
| `dedupe_nodes.nodes` | 19 / 205.167 s | 29 / 2591.111 s |
| `dedupe_edges.resolve_edge` | 456 / 521.640 s | 704 / 894.049 s |
| `extract_nodes.extract_summaries_batch` | 29 / 460.716 s | 37 / 641.923 s |
| `extract_edges.extract_timestamps` | 294 / 181.031 s | 33 / 25.849 s |

FRESH makes source-local extraction cheaper, but its stateful node/edge
resolution and associated database/embedding work expand substantially. The
dominant observed tax is therefore the current algorithm/work decomposition,
not insufficient GPU concurrency. B0 `prompt_tokens` and FRESH
`llm_input_tokens` have different accounting scopes (physical attempts versus
observed request records), so their ratio is intentionally not used as a
performance claim.

## V7 gate status

- Real observer: fail-closed. `node_cosine` has no certifiable stable reads;
  reconvergence is `0`, CSP is `null`, and critical opportunity is
  `UNKNOWN_INCOMPLETE_SEMANTIC_DAG`.
- Provider-free H1 differential: `13/13` canonical differentials pass. The
  H1 source-local identity fix is an offline correctness repair only.
- D0: `UNKNOWN`; no live V7 incremental execution DAG or safe critical-path
  margin exists.
- D1: `UNKNOWN`; no online incremental economics exists.
- Treatment authorization: `false`.
- Terminal method decision: `V7B_ARCHITECTURE_NULL / NULL_NO_ECONOMIC_OPPORTUNITY`.

The audit therefore closes the current V7 identity cleanly. Any future change
to the semantic boundary must create a new algorithm identity and repeat source
audit, quality qualification, observer characterization, and D0/D1 gates.
