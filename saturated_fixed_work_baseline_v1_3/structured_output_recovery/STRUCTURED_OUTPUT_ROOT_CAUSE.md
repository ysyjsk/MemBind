# Structured Output Root Cause

The preserved old attempt `05b11f4007a9` failed at source 35 in `extract_edges.edge`. The transport evidence is decisive: `finish_reason=length`, `completion_tokens=8192`, and `effective_max_tokens=8192`. The JSON decoder then reported an unterminated string at the end of the response. The machine classification is therefore `OUTPUT_LENGTH_TRUNCATION`; `JSONDecodeError` is a downstream parser symptom, not the failure class.

The pinned Graphiti 0.29.3 `Edge` schema has unconstrained `source_entity_name`, `target_entity_name`, `relation_type`, `fact`, `valid_at`, and `invalid_at` strings, an unconstrained `episode_indices` array, and open object properties. The local Qwen transport already captures response metadata before parsing, but the generic client parses first and the runtime installs a single-attempt seam. This combination permits a grammar-valid prefix to be capped and then misclassified without a structured recovery path.

Proven facts, inferences, and unknown publication details are machine-readable in `STRUCTURED_OUTPUT_ROOT_CAUSE.json`. No old response or source text is retained or reconstructed. The next hypothesis is a finite, recursively validated schema and conservative tokenizer certificate, tested provider-free before any development live call.
