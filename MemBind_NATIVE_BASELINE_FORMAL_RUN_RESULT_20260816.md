# MemBind Native Baseline Formal Run Result

Date: 2026-08-16  
Lane: `paper-eval-v3` S5 A0 method smoke  
Run ID: `s5-a0-20260816-004`  
Namespace: `pev3-s5-a0-20260816-004`

## Outcome

The fresh, isolated Native A0 retry completed the complete production chain:

```text
single-use authority consumption
  -> pinned Native Graphiti A0 controller
  -> 49/49 durable source-ordered publications
  -> independent Neo4j namespace observation
  -> exclusive scientific-result finalization
```

Canonical verdict:

```text
attempt status                 complete
attempt payload status         PASS
attempt events                 148
published episodes             49 / 49
post-observation status        PASS
global direct violations       0
postprocess status             complete
final result verdict           PASS
scientific_pass_authorized     true
next_method_authorized         true
resume_authorized              false
namespace_cleanup_authorized   false
pilot/formal authorized        false / false
```

This is the first A0 attempt in this S5 wave to satisfy both the Native
construction terminal contract and the independent post-observation/finalizer
contract. It therefore qualifies the Native A0 baseline as the predecessor for
the separately authorized P* method smoke. It does not authorize a pilot,
formal benchmark, namespace cleanup, current-stage pointer update, or reuse of
this attempt.

## Independent Observation

The post-run Neo4j observation found:

| Observation | Count |
| --- | ---: |
| Expected/durable/observed Episodic nodes | 49 / 49 / 49 |
| Entity nodes | 246 |
| `RELATES_TO` relationships | 183 |
| Lost Episodic nodes | 0 |
| Duplicate Episodic nodes | 0 |
| Unexpected Episodic nodes | 0 |
| Entity namespace escapes | 0 |
| Relationship namespace escapes | 0 |
| Endpoint escapes | 0 |
| Dangling provenance | 0 |
| Cross-namespace provenance | 0 |
| `valid_at` / `invalid_at` reversals | 0 |

The warning lines of the form `Target entity not found in nodes for edge
relation` are Graphiti candidate-edge filtering messages. They did not create a
terminal exception, publication loss, or independently observed direct
invariant violation.

## Retry Interpretation

The result must be interpreted with the prior attempts kept separate:

- `s5-a0-20260816-001` published 49/49 but its old post-observation failed, so
  it remains incomplete/non-mergeable.
- `s5-a0-20260816-002` and `003` both stopped after 8/49 publications with
  `json.decoder.JSONDecodeError` while they overlapped on the construction
  service; they remain incomplete/non-mergeable and are excluded from
  performance evidence.
- `s5-a0-20260816-004` ran as the bounded isolated discriminator, passed the
  previous source-8 failure point, and completed 49/49 plus postprocessing.

This supports production-path viability and predecessor qualification. It is
not a latency, throughput, service-capacity, or failure-rate result.

## Evidence Files

```text
paper-eval-v3/artifacts/paper_eval/native/runs/s5-a0-20260816-004/
  authority_consumption.json
  controller/events.jsonl
  controller/checkpoint.json
  attempt/manifest.json
  attempt/events.jsonl
  attempt/checkpoint.json
  attempt/result.json
  post_observation.json
  postprocess/checkpoint.json
  S5_A0_RESULT.json

paper-eval-v3/logs/s5-a0-20260816-004.log
```

File SHA256 evidence:

```text
attempt/result.json
7e4d0c3931168cf539dd2fc561cb169e112c5a08d1f279fa1757fa1468d83ba8

post_observation.json
597023f3d68322a739dafeef84df4b0dbdc5c5af449caf14830130a56d8f64ca

S5_A0_RESULT.json
3e057325c1c31cce059a3a50b48c5dfc461d98eb25d0949aa1a835d373f12482

postprocess/checkpoint.json
7d93e31913d394bc63190dfc69273501e10bc13798807b6fac6e9a1f3b1a9f28

logs/s5-a0-20260816-004.log
29c92308ff8ee70e1f5eed1b9a159e65b9f67f47bd7f4c3fb29978c0f1de76d9
```

## TDD Work Completed In Parallel

While the isolated A0 run executed, the future M* result path was completed
offline without contacting live services. New modules cover the three-way
attempt/journal/Neo4j observation binding, exclusive result format, and
side-effect-free progress projection.

```text
RED collection errors                     3
focused GREEN                             12 passed
related GREEN                             107 passed
complete paper-eval-v3 offline regression 1405 passed, 1 upstream warning
compileall / git diff --check              passed / passed
```

Primary TDD evidence:

```text
paper-eval-v3/logs/TDD_RED_S5_MSTAR_RESULT_CHAIN_20260816.xml
paper-eval-v3/logs/TDD_GREEN_S5_MSTAR_RESULT_CHAIN_20260816.xml
paper-eval-v3/logs/TDD_RELATED_GREEN_S5_MSTAR_RESULT_CHAIN_20260816.xml
paper-eval-v3/logs/TDD_FULL_OFFLINE_GREEN_S5_MSTAR_RESULT_CHAIN_20260816.xml
```

The full-regression JUnit SHA256 is
`9f0abd3fcd9e28f02818d75a15d1146b138259e650e03c0ff76020008b215375`.
This offline M* work does not itself authorize an M* live run.

## P*(C=2) Native Whole-Update Baseline

The A0 PASS result authorized exactly one fresh next-method smoke. The
authority-bound P* run then completed the full production chain:

```text
run_id                         s5-p-star-20260816-001
namespace                      pev3-s5-p-star-20260816-001
configured / observed workers  2 / 2
maximum active calls           2
whole-update overlap observed  true
published episodes             49 / 49
treatment failed / censored    0 / 0
attempt event count            148
post-observation status        PASS
global direct violations       0
final envelope verdict         SCIENTIFIC_OUTCOME_COMPLETE
scientific outcome             PASS
next method authorized         true
resume / cleanup authorized    false / false
pilot / formal authorized      false / false
```

The authority was consumed at `2026-08-16 04:48:56 +08:00`; the exclusive
result was sealed at `2026-08-16 05:23:21 +08:00`. This approximately
34-minute method-smoke duration includes construction and final bounded
post-observation. It is not a formal performance sample.

The two-worker path did not silently serialize. Its durable completion order
was:

```text
1,2,0,4,5,3,7,6,9,8,10,12,13,11,15,14,16,18,19,17,20,21,23,22,25,
24,26,28,27,30,31,32,29,34,33,36,37,38,35,40,41,39,43,42,45,44,46,
47,48
```

All 49 sources nevertheless received exactly one `PUBLISHED` terminal row.
No infrastructure or telemetry failure was relabeled as a scientific result.

## P* Independent Observation

The canonical postprocessor reopened the terminal source ledger and queried
the exact fresh namespace independently of the construction runner. A
subsequent read-only count reproduced the following public projection:

| Observation | Count |
| --- | ---: |
| Episodic nodes | 49 |
| Entity nodes | 278 |
| `RELATES_TO` relationships | 205 |
| Lost Episodic nodes | 0 |
| Duplicate Episodic nodes | 0 |
| Entity namespace escapes | 0 |
| Relationship namespace escapes | 0 |
| Endpoint escapes | 0 |
| Dangling provenance | 0 |
| Cross-namespace provenance | 0 |

The sealed observer reports `global_violation_total=0`. The final smoke
summary also reports coverage `1.0`, zero lost/duplicate/fallback rows, and
zero direct-invariant violations.

## P* Interpretation

This single DEVELOPMENT_EXPOSED method smoke supports only the bounded
statement:

> `NO_NAIVE_PARALLEL_INSUFFICIENCY_OBSERVED` in the current direct-invariant
> screening run.

It does not prove that whole-update parallelism is generally sufficient or
semantically equivalent to A0. In particular, A0 materialized 246 Entity and
183 `RELATES_TO` rows while P* materialized 278 and 205. Those are separate
live LLM executions, so the difference is an analysis lead, not evidence that
concurrency alone caused trajectory divergence. No graph-parity, retrieval-
parity, latency-speedup, failure-rate, or population-level claim follows from
this one smoke.

The terminal authority permits preparation of the next S5 method only. It
does not authorize current-pointer advancement, namespace cleanup, PILOT, or
formal paper evaluation. The P* namespace must therefore remain intact.

## P* Evidence Files

```text
paper-eval-v3/artifacts/paper_eval/native/
  S5_PSTAR_LIVE_PREFLIGHT_20260816.json
  S5_PSTAR_LIVE_AUTHORITY_20260816.json

paper-eval-v3/artifacts/paper_eval/native/runs/s5-p-star-20260816-001/
  authority_consumption.json
  controller/events.jsonl
  controller/checkpoint.json
  attempt/manifest.json
  attempt/events.jsonl
  attempt/checkpoint.json
  attempt/result.json
  post_observation.json
  postprocess/checkpoint.json
  S5_PSTAR_RESULT.json

paper-eval-v3/logs/s5-p-star-20260816-001.log
```

File SHA256 evidence:

```text
S5_PSTAR_LIVE_PREFLIGHT_20260816.json
7127d5b409b1b0f4ea46e78cabcea34402b3b84e6cef3c0e67ef0e7e8f60e1f8

S5_PSTAR_LIVE_AUTHORITY_20260816.json
4a9c02e0d271674b55debef9cf525006b38b4639ed78cec1190d689de08a88f0

attempt/result.json
c805dd9c2d298ca987da735e8d17eb13e1be331d383d5c5a60965ed0baff5d80

post_observation.json
5a273ef246d5b3f0f19977f9aebd2850b9b75f0a7ade0cbce6662c003cc64006

S5_PSTAR_RESULT.json
3cb2c27902ec2825fc9894d8855cc79e601f3518188593ed7d1a990092576df5

postprocess/checkpoint.json
a5e528942e966cb8f730b2e59464129d780146c0ecb5a97677f7fbcce6c1848e

logs/s5-p-star-20260816-001.log
bc0ac3547cf26f4f185e61a431f262163dc955f3731ddc7f1d704e616eb48cd1
```
