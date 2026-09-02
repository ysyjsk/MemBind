# Engineering Canary Result

Status: `FAIL_NATIVE_AB_REPRODUCIBLE_OUTPUT_BLOCKER`.

Fresh canary `engineering-canary-20260902T0410` was run at implementation
commit `72633ca53dc5984d7e7f224921cbe97292410c05`, with the re-bound current
identity and authenticated local 8B dual-replica platform. It used context `0`
and the first two official sources, with fresh namespaces and
`--force-reference-rerun`.

`GRAPHITI_UPSTREAM_SERIAL` failed in attempt `ed9746e2166c` and
`RELAXED_ORDER_PARALLEL` failed in attempt `74e7fd3afffc`. Both failed with
the same native Graphiti edge JSON parse error at character 50,901 after the
fixed 16,384-token edge request. Their failed roots and route evidence are
preserved. `MEMBIND_V6_1` completed in attempt `0f83a13e4e4b` and produced a
valid construction and route seal with the frozen `V6_FIXED_POLICY`.

This is not a canary PASS: formal authorization and
`FINAL_METHOD_FROZEN.json` generation are intentionally withheld. The
provider-only diagnostic in `NATIVE_8B_EDGE_DIAGNOSTIC.json` shows that even a
temporary 32,768-token edge budget is exhausted with another truncated JSON
response. The blocker therefore cannot be repaired by retrying, queue
draining, or ordinary service restart. Bounding/paging the A/B schema or
switching the model would change the strict Native arm contract and requires
separate authority.
