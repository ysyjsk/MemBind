# Baseline Results Summary

Date: 2026-08-20

Scope: the existing frozen baseline states and the authored-QA extension in
`BASELINE_REUSE_4_HISTORY_AUTHORED_EXTENSION`. This summary keeps the
original four-question reuse diagnostic, the new 16-question QA extension,
and the C246 construction smoke separate. None of these is an official
MemoryAgentBench Multi-QA or 240-question result.

## 1. QA Results

### Original frozen-baseline reuse diagnostic

This is the earlier four-history diagnostic with one question per history and
method. It reused existing Reader/Judge outputs and revalidated the Judge with
`Qwen/Qwen3-32B`.

| Method | Questions | Correct | Invalid | Primary accuracy | R@1 | R@3 | R@5 | R@10 | MRR | nDCG@10 |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| U0 | 4 | 2 | 0 | 50.0% | 0.500 | 1.000 | 1.000 | 1.000 | 1.000 | 1.000 |
| P(C=2) | 4 | 2 | 0 | 50.0% | 0.500 | 1.000 | 1.000 | 1.000 | 1.000 | 1.000 |

The paired result is 4/4 agreement and a 0-point P(C=2)-minus-U0 delta. This
is a small development diagnostic, not an equivalence test.

### Authored-QA extension over the same four histories

This is the completed live extension with four authored questions per history,
16 questions per method. It did not replace the source dataset, rebuild
memory, or change the frozen namespaces.

| Method | Questions | Correct | Valid | Invalid | Primary accuracy | Valid-only accuracy |
|---|---:|---:|---:|---:|---:|---:|
| U0 | 16 | 13 | 16 | 0 | 81.25% | 81.25% |
| P(C=2) | 16 | 12 | 15 | 1 Reader | 75.00% | 80.00% |
| P(C=4) | 4* | 3 | 4 | 0 | 75.00%* | 75.00%* |

`*` P(C=4) is only a verified single-history probe for `07741c45`; three of
the four planned P(C=4) namespaces are absent. Its 75.0% score is descriptive
only and is not a four-history method result.

For U0 and P(C=2), retrieval is identical:

| Method | R@1 | R@3 | R@5 | R@10 | MRR | nDCG@10 | Gold-session context coverage |
|---|---:|---:|---:|---:|---:|---:|---:|
| U0 | 0.750 | 0.9375 | 0.9375 | 1.000 | 0.8507 | 0.8871 | 1.000 |
| P(C=2) | 0.750 | 0.9375 | 0.9375 | 1.000 | 0.8507 | 0.8871 | 1.000 |

On the one available P(C=4) history, retrieval is R@1 0.250, R@3 0.750,
R@5 0.750, R@10 1.000, MRR 0.5278, and nDCG@10 0.6407. The gold ranks are
2, 9, 2, and 1 for the four authored questions.

## 2. What The QA Says

The main retrieval layer is functioning: all 32 U0/P(C=2) rows recalled the
gold session within top-10, and the post-hoc gold-session context coverage was
1.0. The observed failures are therefore mostly Reader/context failures:

- evidence selection and context packing omitted an exact authored quote for
  some questions;
- conflicting or temporally stale graph facts caused wrong answer selection;
- one P(C=2) Reader response was operationally invalid;
- the P(C=4) miss answered `Teva + Keen` instead of the gold `Teva + Merrell`
  with the gold session at rank 9 and the exact quote absent from final context.

P(C=2) does not show a QA improvement over U0 in this extension. Its primary
accuracy is 6.25 percentage points lower, while the valid-only rates are close
(80.0% versus 81.25%). The two valid semantic discordances split one win each;
the net primary gap is driven by the single invalid P(C=2) Reader row. This is
diagnostic evidence, not a non-inferiority or equivalence result.

The original 50.0% result and the extension's 81.25%/75.0% results are not
contradictory: they use different authored question inventories. They must not
be pooled as though they were repeated measurements of the same test set.

## 3. Construction And Graph Smoke

The C246 aligned construction smoke completed only blocks 0-2 for history
`07741c45`:

| Aligned block | Status | Direct graph violations | Observation |
|---|---|---:|---|
| U0 | PASS | 0 | no measured correctness violation |
| P(C=2) | PASS | 1 | one source-publication-order violation |
| P(C=4) | PASS | 1 | one source-publication-order violation |

The remaining planned C246 blocks were not completed in the recorded smoke.
The current P(C=4) QA probe therefore uses only the existing source-bound
`07741c45` namespace and does not substitute another method's namespace.

For the older Native S5 smoke chain, Native A0 completed 49/49 publications
with zero observed direct violations, and the P* (C=2) smoke also completed
49/49 with zero observed direct violations. Those are construction-path
qualification observations, not comparable end-to-end QA accuracy results.

## 4. Contract And TDD Checks

- Reader and Judge were fixed to `Qwen/Qwen3-32B`.
- Embedding was fixed to `Qwen/Qwen3-Embedding-0.6B`, dimension 1024.
- SiliconFlow model preflight passed for both QA runs.
- No construction calls occurred in the QA extension.
- Neo4j access was read-only; the available P(C=4) namespace snapshot was
  unchanged.
- Gold labels and evidence were excluded from the public Reader projection.
- The isolated TDD suite passes: 16 tests.
- Existing Neo4j ports 7474/7687 and the recorded tmux sessions remain live;
  no 8002/8003 service is required for this completed SiliconFlow-based QA
  artifact.

## 5. Project-Expectation Verdict

**Methodological expectation: PASS.** The work correctly reused the baseline
states, added QA without changing the dataset, held model and embedding
contracts fixed, kept retrieval and Reader/Judge accounting separate, used a
conservative invalid-output denominator, and completed the requested TDD
checks in an isolated folder.

**Diagnostic expectation: PASS.** The results identify that top-10 session
retrieval is strong while answer-time context selection, temporal conflict
resolution, and one operational Reader failure remain the limiting factors.

**Full comparative-method expectation: BLOCKED/PARTIAL.** U0 and P(C=2) have
complete four-history authored-QA diagnostics. P(C=4) does not have complete
namespace coverage, so no four-history P(C=4) accuracy, equivalence,
non-inferiority, or MAB Multi-QA generalization claim is permitted.

The appropriate next scientific action is to restore or construct the three
missing P(C=4) namespaces under the plan's authority, then rerun the same
paired inventory. It is not valid to fill the gap with U0, P(C=2), native, or
another C246 namespace.

## 6. Construction Speed And Freshness

The earlier three-baseline development suite did record construction
performance. These numbers are part of the baseline evidence and should be
reported alongside QA, but they are not a formal paper performance table.

### Three-baseline development suite

This run processed the same 188 development episodes per method:

| Method | Episodes | Makespan (s) | Goodput (episodes/s) | P95 freshness (s) | P99 freshness (s) | Max backlog | QA accuracy |
|---|---:|---:|---:|---:|---:|---:|---:|
| U0 | 188 | 8501.162 | 0.022115 | 99.918 | 205.629 | N/A | 0.250 |
| A0 / Async-Serial | 188 | 8523.113 | 0.022058 | 2258.750 | 2367.464 | 49 | 0.250 |
| P(C=2) | 188 | 7355.528 | 0.025559 | 1867.920 | 2156.406 | 49 | 0.250 |

Relative to U0, P(C=2) drained this particular burst in about 13.5% less
time and had about 15.6% higher aggregate goodput. A0 had essentially the
same makespan as U0, but a much larger freshness backlog because it admitted
the history as an intent burst. P(C=2) also had a large freshness backlog,
despite its shorter aggregate drain time.

These are directional development observations, not pure scheduling speedups:

- U0 records arrival immediately before each serial service, while A0/P(C=2)
  issue a history-wide intent burst;
- P(C=2) performed more work than U0/A0: 2,019 versus 1,900 LLM calls,
  2,012 versus 1,870 embedding calls, and 5,666 versus 5,261 DB operations;
- P(C=2) also produced a larger graph: 1,253 nodes and 1,838 relationships,
  versus U0's 1,199 nodes and 1,705 relationships;
- therefore its lower makespan cannot be interpreted as a normalized
  per-update scheduling speedup without a matched-work analysis.

The shared work-volume context was:

| Method | LLM calls | Input tokens | Output tokens | Embedding calls/items | DB operations/transactions | Candidate count | Nodes | Relationships |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| U0 | 1,900 | 17,861,037 | 207,768 | 1,870 / 4,718 | 5,261 / 188 | 21,171 | 1,199 | 1,705 |
| A0 | 1,900 | 17,861,327 | 207,485 | 1,870 / 4,718 | 5,261 / 188 | 21,171 | 1,193 | 1,705 |
| P(C=2) | 2,019 | 17,832,623 | 203,119 | 2,012 / 5,120 | 5,666 / 188 | 22,480 | 1,253 | 1,838 |

### C246 aligned construction smoke

The C246 smoke used a common aligned execution envelope and completed only
the first three blocks, all for `07741c45`. It is a separate run from the
188-episode suite and must not be averaged into it.

| Method | Episodes | Makespan (s) | Goodput (episodes/s) | P95 freshness (s) | P99 freshness (s) | Max backlog | Max queue | Direct graph violations |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| U0-aligned | 49 | 2636.846 | 0.018583 | 758.071 | 770.386 | 19 | 18 | 0 |
| P(C=2)-aligned | 49 | 2062.754 | 0.023755 | 226.156 | 908.030 | 7 | 5 | 1 source-publication-order |
| P(C=4)-aligned | 49 | 3430.577 | 0.014283 | 1396.098 | 2786.987 | 15 | 11 | 1 source-publication-order |

Within this one-history C246 smoke, P(C=2) completed about 21.8% faster than
U0, while P(C=4) took about 30.1% longer. These values are useful diagnostic
signals only: P(C=4) coverage is incomplete, and the blocks have different
observed work trajectories and graph correctness observations.

The associated vLLM telemetry also shows why speed should not be summarized by
one number. For U0/P(C=2)/P(C=4), respectively, measured durations were about
`2640.454/2065.375/3432.730s`; generation throughput was
`25.87/34.46/43.83 tokens/s`; prompt throughput was
`1619.43/2153.11/1338.27 tokens/s`; and prefix-cache hit rates were
`0.289/0.332/0.272`. These are service telemetry observations, not end-to-end
quality metrics.

### Native A0 and P* historical method smokes

The older Native S5 chain provides another bounded speed observation over 49
episodes. Deriving the interval from the sealed construction event ledger
(first intent to final publication) gives:

| Method | Episodes | Event-ledger construction interval (s) | Goodput (episodes/s, event interval) | Workers | Whole-update overlap | Published | Direct violations |
|---|---:|---:|---:|---:|---|---:|---:|
| Native A0 | 49 | 1936.294 | 0.025306 | 1 | no | 49/49 | 0 |
| Native P* (C=2) | 49 | 2056.062 | 0.023832 | 2 | yes | 49/49 | 0 |

These event-ledger intervals exclude the later independent post-observation
and finalization steps. The report's roughly 34-minute P* figure includes
construction plus bounded post-observation, so it is not identical to the
event-ledger interval above. This Native smoke was a method qualification
observation, not a formal throughput sample.

### Later feasibility observations

The separate MemBind v3.1 single-history feasibility gate processed 49
episodes with two compile workers, makespan `2062.533s`, goodput
`0.023757 episodes/s`, p95 freshness `161.192s`, p99 freshness `184.625s`,
and zero direct violations. It is explicitly marked
`SINGLE_HISTORY_FEASIBILITY_GATE_NOT_FINAL_TABLE`, so it is not substituted
for the U0/P(C=2)/P(C=4) baseline numbers above. The later six-episode v4
autoresearch candidate is likewise not a baseline measurement.

## 7. Updated Expectation Verdict

Including construction speed changes the interpretation in an important way:

- **QA expectation:** met diagnostically. U0/P(C=2) retrieval is strong, and
  downstream Reader/context handling is the main limitation.
- **Construction throughput expectation:** P(C=2) shows a promising
  development directional signal, with lower burst makespan than U0/A0 in the
  188-episode suite and again lower makespan in the 49-episode C246 smoke.
  However, its work volume, graph size, and arrival semantics differ, so the
  result is not yet a normalized speedup claim.
- **Freshness expectation:** parallel admission creates substantial backlog;
  lower aggregate makespan does not imply lower per-update freshness. A0 and
  P(C=2) need to be evaluated under the final freshness definition before a
  latency claim is made.
- **P(C=4) expectation:** currently partial. Its only available C246 smoke is
  slower than U0 and has one source-publication-order violation, but no
  four-history conclusion is allowed because three namespaces are absent.
- **Paper-level expectation:** not yet met. The evidence remains development
  characterization, not a final formal benchmark or MAB Multi-QA result.
