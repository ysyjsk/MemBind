# Bounded Edge Substrate Design Decision

## Decision

MemBind selects `CANONICAL_CURSOR_WITH_EVIDENCE_PAIR_COVER` as the shared edge-extraction substrate for Native, Ours, and Async. Its current adapter identity is `shared-bounded-structured-output-v5-xgrammar-physical-bound`, with `single_attempt_cursor_violation_fail_closed_v1` retry semantics. There is no arm-specific branch.

This decision establishes a bounded physical request surface. It does not claim that an LLM will recover every semantically valid fact, and it does not replace the required full-history live qualification.

## Compared Designs

### Rejected: cumulative exclusion history

The earlier design appended all returned edges to `<ALREADY_RETURNED_EDGES>` and could place a duplicate edge, including a fact of up to 1900 characters, into a request-specific schema `not/const` branch. Page capacity limited output cardinality but did not limit prompt, schema, or continuation growth. After `p` pages, the continuation was proportional to the cumulative serialized edges, so no history-independent upper bound existed. A duplicate-confirmation call also introduced a hidden second model attempt without proving exhaustion.

The old excluded campaign records 11 edge-budget failures. This is consistent with the design defect: a locally bounded edge did not imply a globally bounded request after cumulative exclusion state was added. The live p3 reproducer also exposed a provider-contract defect: vLLM 0.26.0's `XgrammarBackend` ignores request-level `disable_any_whitespace`, and the pattern `^[\\x00-\\x7f]*$` both defeated `maxLength` and admitted JSON delimiters inside the first string. That allowed later array/object syntax to be consumed as string content and bypassed the nominal `maxItems` bound.

### Selected: canonical cursor with evidence/pair cover

The selected design bounds each physical request with:

- one role-preserving evidence window;
- one complete binary endpoint domain containing at most two candidate entities;
- one fixed `status/edge` discriminator schema plus a single rolling `not` constraint for the immediate predecessor;
- one edge or explicit `no_additional_edge` response;
- one bounded `EDGE_CURSOR` tuple carrying only the immediately preceding canonical edge.

The cursor must advance strictly. The immediate predecessor is also excluded in the wire schema, so a constrained decoder cannot repeat it. An equal or decreasing value that nevertheless arrives fails closed immediately; it does not trigger duplicate recovery or a confirmation call. Page 64 is only an observability epoch boundary. The runtime emits `EDGE_CURSOR_EPOCH_ADVANCE` and continues with the same bounded state until the provider explicitly returns `no_additional_edge` with `edge=null`.

Coverage is constructed rather than sampled. Oversized turns are divided into contiguous, role-preserving payload chunks whose concatenation reproduces the original body. Adjacent evidence windows retain boundary evidence. Graphiti defines an extracted fact between two distinct entities, so enumerating the complete unordered pair cover for each evidence-local candidate entity set covers every declared binary endpoint domain. Results are merged and deduplicated deterministically. This argument does not cover entities omitted by upstream entity extraction.

## Offline Evidence

`test_shared_runtime_continues_past_page_epoch_with_1900_char_cursor` exercises three endpoint domains. Each emits 65 progressing pages containing a 1900-character fact, followed by explicit exhaustion: 195 edge pages and 3 terminal pages in total. The fixture crosses both the historical page-26 failure region and the page-64 epoch boundary.

Within every endpoint domain, the schema has a fixed shape and a bounded single-predecessor exclusion; its hash changes only with that one canonical tuple and its serialized size remains bounded. Continuation and prompt length remain constant after the first page, no `<ALREADY_RETURNED_EDGES>` block appears, every generated structured-output certificate passes, and exactly one epoch-advance event is recorded per domain.

## Provider Contract and Token Bound

The server is launched with `structured_outputs_config={"backend":"xgrammar","disable_any_whitespace":true}`. This server-level setting is the authority; no request-level hint is treated as effective. xgrammar uses fixed JSON separators `, ` and `: `, which are included in the certificate. Bounded string fields use the finite ASCII wire pattern `^(?:[\\x20-\\x21\\x23-\\x5b\\x5d-\\x7e]|\\\\["\\\\/bfnrt]){MIN,MAX}$`: raw quote/backslash and structural delimiters are excluded from the ordinary class, while short JSON escapes remain legal. The conservative proof is a UTF-8 byte upper bound (up to two bytes per logical character for the safe wire pattern); tokenizer witnesses are diagnostics only. The largest captured edge schema is `10781` bytes/tokens against the pinned `16384` completion budget, and the 63-item timestamp batch is `12364`.

## Source Evidence

Installed Graphiti `0.29.3` defines `ExtractedEdges.edges` as an unbounded list and constrains each fact to two distinct entities while requiring concrete source details to be preserved. The inspected prompt source SHA-256 is `df268b68ae4ae3c1e515b4c3b4ee29efd78b7c8902947e91a76767f3c6657582`.

Installed vLLM `0.26.0` compiles request-local JSON schema/grammar into constrained decoding state and applies grammar bitmasks while decoding. Therefore schema size and content are part of the physical request surface, rather than harmless host-only metadata. The inspected SHA-256 values are `196b7038...c198` for `request.py`, `231f6b9d...cb67` for `backend_xgrammar.py`, and `a4d0532c...df7e` for `utils.py`.

The design also uses established systems ideas as precedent, not as proof of this exact protocol: bounded paged state from Kwon et al., SOSP 2023; controlled streaming context from Xiao et al., ICLR 2024; long-context position sensitivity from Liu et al., TACL 2024; and deterministic partition/merge from Dean and Ghemawat, OSDI 2004.

## Claim Boundary

The static admission certificate, not a tokenizer witness, remains authoritative for the worst-case bound. `SHARED_FACT_MAX_LENGTH=1900`, the 65,536 context, and the 16,384 edge completion limit remain unchanged. The offline result proves bounded state and deterministic coverage of declared evidence/entity domains. Provider behavior, complete full-history construction, and 60-row FULL QA per arm must still pass a fresh live qualification before formal data collection starts.
