# Native Characterization Lightweight Execution Decision

> Status: active execution interpretation
> Scope: workplan v1.1 section 4.1 overhead classification only
> Live authorization: false

This decision does not add a stage, metric, treatment, compatibility matrix, or
authority layer. The frozen workplan, `freeze.json`, `phase_map.json`, historical
attempts, and their hashes remain unchanged.

## Evidence Interpretation

- The canonical five-pair C2 measurement fixture reported a median overhead of
  `5.870971845973012%` with semantic parity and zero network attempts. The
  artifact remains valid under its original method and keeps its historical
  `blocked_overhead` label.
- That fixture's ratios ranged from `-0.4336%` to `24.9933%` in approximately
  100 ms timing arms. It is too noisy and low-resolution to justify using the
  5% engineering guardrail as a C2 execution cutoff.
- The later engineering diagnostic is non-decisional. Its combined/off median
  was `0.371%`, its adapter/base median was `-0.221%`, and the estimated
  combined fixed wrapper work was approximately `127.6 us` per synthetic
  episode. These values show no material adapter hotspot at the available host
  noise resolution; they are not an estimate of live C2 overhead.
- The earlier C1 qualification remains closed: semantic parity passed and its
  median paired overhead was `1.317%`.

## Execution Decision

1. Semantic parity remains mandatory for prompts, effective schemas, bound
   arguments, call order, retries, parsed and returned values, exceptions, and
   deterministic graph state.
2. Evidence that tracing changes phase attribution, critical-path accounting,
   call behavior, exception behavior, or durable checkpoint correctness is a
   hard measurement-correctness blocker.
3. Instrumentation overhead is measured, retained, and disclosed as
   perturbation evidence. No fixed percentage alone blocks C2 screening.
4. No further overhead estimator, qualification validator, provenance layer,
   or requalification iteration is allowed unless a demonstrated defect would
   directly make C2 timings or work-volume counts incorrect.
5. The only structured-output compatibility candidate is `json_object`, using
   `response_format={"type":"json_object"}`, no additional system prompt, and
   the existing constrained effective schema. No parser or retry fallback stack
   may be added if this candidate fails.

## Next Bounded Path

1. Preserve the existing focused RED/GREEN evidence for the minimal
   `json_object` adapter and run one final offline regression before live work.
2. Combine any required mode freeze, exact polluted-namespace cleanup grant,
   and fresh C2-only grant into one necessary state transition.
3. Clean only the already identified frozen C2 namespace, then start a fresh
   `c2-[0-9a-f]{16}` run from source sequence 0.
4. Treat episode 0 as the bounded compatibility canary and persist every
   episode checkpoint. If it succeeds, continue the same C2 run.
5. On vLLM disconnection, stop and ask the operator to restart it. On another
   structured-output failure, stop and report without adding another fallback.

The immediate research objective is the first valid Native Graphiti phase
breakdown. Qualification work is frozen unless it is necessary for the
correctness of that result.
