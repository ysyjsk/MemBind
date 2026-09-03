# Structured Output Qualification

Status: `PASS_ACTUAL_RUNTIME_CALLSITE`; synthetic suite: `PASS_PROVIDER_FREE_SYNTHETIC_CALLSITE_SUITE`.

Certified `17` callsites across `47` generated variants using the local Qwen tokenizer, a `65536` token context limit, the pinned `extract_edges.edge` `16384` token completion budget, the `32768` token default budget for other captured callsites, and a `32` token safety margin. Caller-supplied attribute schemas and candidate-flight capacities are bounded before provider invocation.

The largest captured edge schema is bounded by `10781` UTF-8 bytes/tokens with a `1900`-character fact cap, leaving `5603` tokens below the pinned edge budget. Timestamp batches remain capped at `63` items and certify at `12364` tokens. The proof uses the server-bound xgrammar whitespace mode and fixed `, ` / `: ` separators; tokenizer counts are diagnostic witnesses only. Certified truncation and context-budget failures have zero automatic resend variants; only transient transport failures receive at most two extra physical attempts under the shared identity contract. Model revision: `4da05a8edb55c6046cce958586c33b61da07bb79`.

R3 is `AT_LEAST_ONCE_WITH_STABLE_IDEMPOTENCY_KEY`; no cross-system durable reconciliation or exactly-once claim is made.

Source-discovered callsites: `15`; actual observed: `16`; covered names: `11`; uncovered names: `0`; covered source rows: `14`; unreachable with proof: `1`.
Evaluated source bundle SHA-256: `0dfad814ea0b43c998d99ede16c107dd988e64278145a525bab14a4a2470124b`; generator source SHA-256: `bdbb4850f87d91aa8b13e1b418b6cb70aa2b829c6a711cb1266cd25819f2335f`; base code commit: `d9cc4f3c25b48e9cb28ba33e0ec43f5993adb277`.
