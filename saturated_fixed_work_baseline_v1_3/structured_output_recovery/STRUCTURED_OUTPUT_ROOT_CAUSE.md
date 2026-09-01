# Structured Output Root Cause (Historical, Superseded)

> `HISTORICAL_SUPERSEDED`: this artifact records the preserved 8B failure and
> the diagnosis that preceded the current provider-free certificates. It is
> retained for provenance and must not be read as the current R3 or dataset
> decision. The current policy and gate are in
> `STRUCTURED_OUTPUT_QUALIFICATION_REPORT.md` and `CURRENT_STATE.json`.

The preserved old attempt `05b11f4007a9` failed at source 35 in `extract_edges.edge`. The transport evidence is decisive: `finish_reason=length`, `completion_tokens=8192`, and `effective_max_tokens=8192`. The JSON decoder then reported an unterminated string at the end of the response. The machine classification is therefore `OUTPUT_LENGTH_TRUNCATION`; `JSONDecodeError` is a downstream parser symptom, not the failure class.

The pinned Graphiti 0.29.3 `Edge` schema has unconstrained `source_entity_name`, `target_entity_name`, `relation_type`, `fact`, `valid_at`, and `invalid_at` strings, an unconstrained `episode_indices` array, and open object properties. The local Qwen transport already captures response metadata before parsing, but the generic client parses first and the runtime installs a single-attempt seam. This combination permits a grammar-valid prefix to be capped and then misclassified without a structured recovery path.

The corrected R1 bound follows the actual pinned call path. Graphiti 0.29.3 calls `extract_edges()` with `max_tokens=16384`, not the client-wide `32768` default. Under the conservative one-token-per-compact-ASCII-JSON-character proof, the finite one-edge response with a 1,900-character `fact` is at most 15,862 tokens. A 1,987-character fact would consume the entire 16,384-token bound, so 1,900 is retained with 522 tokens of proof headroom. Endpoint names, relation type, timestamps, episode indices, node extraction, caller-provided attribute models, and candidate flights are also finite. Timestamp batches are capped at 63 responses, whose certified worst case is 32,272 tokens under the 32,768-token callsite budget.

The resulting provider-free runtime certificate covers 47 generated request variants across 17 actual callsites with the local Qwen tokenizer and reports `PASS_ACTUAL_RUNTIME_CALLSITE`. The recovery policy performs no truncation smaller-variant resend and no context-budget correction resend; a certified truncation or context rejection fails closed. Only transient transport failures receive at most two extra physical attempts under a stable semantic operation and request-variant identity, with a unique physical-attempt identity for each wire call.

Proven facts, inferences, and unknown publication details remain machine-readable in `STRUCTURED_OUTPUT_ROOT_CAUSE.json`. No old response or source text is retained or reconstructed. Any R3 and dataset-gate wording from the preserved historical attempt is superseded: the current R3 guarantee is `AT_LEAST_ONCE_WITH_STABLE_IDEMPOTENCY_KEY`, and the current official parity artifact is `OFFICIAL_DATASET_PARITY_REPORT.json` with selection `OFFICIAL_AS_PUBLISHED_5_RECORDS`. This historical file does not authorize a canary or a formal three-arm task.
