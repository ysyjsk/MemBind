# C2 Measurement Qualification Overhead: Engineering Diagnostic

Status: non-decisional engineering evidence. This document does not replace,
reinterpret, or overwrite the canonical qualification. The canonical result
remains `blocked_overhead` at a 5.870971845973012% median paired overhead.

## What was measured

The same deterministic offline fixture was measured in three modes:

1. `off`
2. `base_only`
3. `base_adapter`

Installation and restoration stayed outside the timed `add_episode` region.
The main diagnostic used all six mode permutations, three repetitions per
permutation, two untimed warmup cycles, fresh fixtures, both wall and process
CPU clocks, and the existing fail-closed network guard. Return, event-sequence,
and state hashes each had exactly one value. No network attempt occurred.

## Main decomposition

At 20,000 work units, the median wall durations were:

| Mode | Median wall time | Spans |
| --- | ---: | ---: |
| off | 105.235 ms | 0 |
| base only | 105.555 ms | 17 |
| base + adapter | 105.457 ms | 26 |

Across the 18 balanced blocks, median paired wall ratios were:

| Comparison | Median | Standard deviation | Range |
| --- | ---: | ---: | ---: |
| base / off | +0.898% | 2.622% | -3.228% to +6.676% |
| combined / off | +0.371% | 2.055% | -3.229% to +3.750% |
| adapter / base | -0.221% | 2.948% | -8.352% to +4.029% |

The negative adapter median is not evidence of a speedup. It establishes that
the adapter increment is below the resolution of independent approximately
100 ms executions on this host. Process CPU ratios show the same behavior,
so replacing wall time alone does not eliminate the variance.

## Fixed cost

An isolated span microbenchmark measured about 6.318 microseconds per recorded
span. This projects to about 107 microseconds for the 17 base spans and 57
microseconds for the adapter's nine additional spans.

A separate zero-payload batch benchmark, using the correct average of the two
middle values for its 12 batches, measured median fixed costs of:

| Mode | Median per episode |
| --- | ---: |
| off | 15.7 us |
| base only | 88.0 us |
| base + adapter | 143.3 us |

The corresponding increments were about 72.3 us for the base wrappers and
55.3 us for the adapter, or 127.6 us combined. cProfile confirms nine extra
adapter spans and additional wrapper/context/metadata calls, but identifies no
single adapter hotspot. cProfile timings are retained only as structural
evidence because profiling materially perturbs these small functions.

## Why the canonical result was noisy

The canonical five ratios were 24.993%, -0.434%, 4.850%, 5.871%, and 6.686%.
The first pair is a large outlier, while the fixed-cost diagnostic predicts a
combined increment on the order of 0.13 ms rather than the canonical median
difference of roughly 5.5 ms.

The environment sample during diagnosis showed a 1-minute load average of
52.0 on a 32-logical-CPU host, an affinity set containing 24 CPUs, and the
`powersave` frequency governor. Main-mode CPU durations themselves varied by
roughly 5-7 ms standard deviation. Host load and CPU-frequency variation are
therefore larger than the wrapper cost the qualification is trying to resolve.

The timer boundary itself is correct: installation is excluded, both modes
execute the same operation, `perf_counter_ns` is monotonic, and semantic parity
is enforced. The methodological weakness is resolution: one episode per leg,
no untimed warmup before the first pair, no CPU/environment control, no CPU
clock companion in the evidence, and only five pair ratios around hard 2%/5%
boundaries.

## Minimal repair recommendation

Do not optimize the production adapter based on this result; no hotspot was
found. Preserve the blocked canonical artifact and make any follow-up a new,
predeclared qualification run rather than selecting a favorable rerun.

The minimum harness repair is:

1. Add untimed warmup for both modes before pair 0.
2. Time a fixed batch of episodes per leg while retaining exactly five
   alternating pairs, so the approximately 0.13 ms fixed cost is resolvable.
3. Record both wall and process CPU duration, CPU affinity, load average, and
   governor in the qualification artifact.
4. Run under a declared low-load condition with fixed affinity. Treat a noisy
   environment as inconclusive rather than silently retrying until PASS.
5. Preserve the current blocked artifact and link the new run to it.

Changing the hard classification or its five-pair contract is a protocol
decision, not an engineering cleanup, and was not performed here.

## Secondary maintainability finding

The first diagnostic script failed before producing results because the local
`src/statistics.py` shadows Python's standard-library `statistics` module when
`PYTHONPATH=src`. The diagnostic was rerun with explicit local aggregation
functions. No source was changed for this unrelated naming issue.
