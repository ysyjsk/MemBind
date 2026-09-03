# Structured Output Qualification

Status: `PASS_ACTUAL_RUNTIME_CALLSITE`; synthetic suite: `PASS_PROVIDER_FREE_SYNTHETIC_CALLSITE_SUITE`.

Certified `17` callsites across `47` generated variants using the local Qwen tokenizer, a `65536` token context limit, the pinned `extract_edges.edge` `16384` token completion budget, the `32768` token default budget for other captured callsites, and a `32` token safety margin. Caller-supplied attribute schemas and candidate-flight capacities are bounded before provider invocation. The formal finite-pair task schema (two pairs, one relation per pair, 1,900-character facts) is independently certified at `11188` tokens with status `PASS`.

The largest captured edge schema is bounded by `10781` UTF-8 bytes/tokens with a `1900`-character fact cap, leaving `5603` tokens below the pinned edge budget. Timestamp batches remain capped at `63` items and certify at `12364` tokens. The proof uses the server-bound xgrammar whitespace mode and fixed `, ` / `: ` separators; tokenizer counts are diagnostic witnesses only. Certified truncation and context-budget failures have zero automatic resend variants; only transient transport failures receive at most two extra physical attempts under the shared identity contract. Model revision: `4da05a8edb55c6046cce958586c33b61da07bb79`.

R3 is `AT_LEAST_ONCE_WITH_STABLE_IDEMPOTENCY_KEY`; no cross-system durable reconciliation or exactly-once claim is made.

Source-discovered callsites: `15`; actual observed: `16`; covered names: `11`; uncovered names: `0`; covered source rows: `14`; unreachable with proof: `1`.
Evaluated source bundle SHA-256: `2fec47b608c0a35a52366d6b5557c90011839fb416cbbcb375cadc7fcdd73248`; generator source SHA-256: `2b23b9e64728b4d07cc2c660991da0c9c06b9d51d4ec44b17724d45d92dd1119`; base code commit: `5f845852198f649f816d424862df257654cf90c9`.
