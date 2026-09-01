# Structured Output Qualification

Status: `PASS` for the actual provider-free V6.1 runtime callsite inventory.

Certified `17` callsites across `47` generated variants using the local Qwen tokenizer, a `65536` token context limit, the pinned `extract_edges.edge` `16384` token completion budget, the `32768` token default budget for other captured callsites, and a `32` token safety margin. Caller-supplied attribute schemas and candidate-flight capacities are bounded before provider invocation.

The edge certificate's worst-case compact JSON is `15862` tokens with a `1900`-character fact cap, leaving `522` tokens below the pinned edge budget. Timestamp batches are capped at `63` items and certify at `32272` tokens. Certified truncation and context-budget failures have zero automatic resend variants; only transient transport failures receive at most two extra physical attempts under the shared identity contract. Model revision: `31c69efc29464b6bb0aee1398b5a7b50a99340c3`.

R3 remains `AT_LEAST_ONCE_WITH_DURABLE_RECONCILIATION`; a local journal and Neo4j commit are not treated as one atomic transaction.
