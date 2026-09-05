# Main Experiment Plan

## Arms and fairness

The plan is conditional. It cannot start while `NATIVE_VALIDATION_SUMMARY.json`
reports `NATIVE_NOT_READY`. After Native freeze, use the same model, Ollama runtime, Graphiti revision,
embedding, database, workload adapter, source order, evaluator, and total GPU
budget for all arms:

* **A `SERIAL_NATIVE`:** direct ordered `Graphiti.add_episode` calls.
* **B `ASYNC_NATIVE`:** the same calls with measured bounded concurrency. B is
  a Native ceiling; it has no prepared-work reuse.
* **C `MEMBIND`:** the clean scheduler prepares future Native work, validates
  complete request identity, reuses valid work, and falls back to Native on any
  mismatch or failure. Publication is ordered.

## Scale

Use five complete histories and three randomized replicate orders (45 cells),
because the paired A/C effect is the primary comparison and three replicates
separate method variance from one history's content. Run one history atomically;
within each replicate use Native -> Ours -> Async, then the next replicate.
Every cell gets a fresh namespace, attempt id, and append-only telemetry. A
failed cell is invalidated and rerun in a new namespace; it is never resumed or
silently replaced.

If the frozen dataset contains fewer than five eligible histories, use every
complete history and report the reduced paired design before starting. Do not
substitute a prefix for a formal history.

## Validation sequence

1. Reproduce the external Graphiti/Ollama shape with one short episode.
2. Run the real MAB/LongMemEval adapter on a short prefix and then through the
   full predecessor state around source 79.
3. Run a fresh full-H0 Serial Native construction.
4. Freeze all identities and run the 45-cell three-arm campaign.
5. Run the common FULL QA/evaluator and reduce construction and quality tables.

Long operations must run in `tmux` or an equivalent persistent supervisor.
Record PID, run root, namespace, heartbeat, provider metrics, and terminal
artifact for each attempt. An observation-window timeout is not a provider
timeout; continue while the process and artifacts advance.

## Primary metrics

Report construction makespan, speedup over A, gain over B, prepared-work reuse
and fallback, wasted preparation, and GPU/LLM utilization. Queue and cache
events are diagnostic. Quality is evaluated with the same retrieval, reader,
judge, and question set for all arms, including explicit paired delta and
disagreement reporting.

No result is labelled paper-ready automatically. The conclusion may be
supported, mixed, quality-inconclusive, or a valid negative.
