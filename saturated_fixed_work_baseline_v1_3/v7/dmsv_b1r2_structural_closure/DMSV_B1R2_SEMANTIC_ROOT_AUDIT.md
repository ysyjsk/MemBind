# DMSV B1R2 semantic-root scope audit

Date: 2026-08-31
Input commit: `37871aae8193d994a1642605e3a705712dd786e1`
Frozen V6: `v6-membind-core-v1`
Graphiti: `0.29.3`

## Scope of `strip_certified_previous_context`

The Frozen V6.1 provider wrapper applies `strip_certified_previous_context`
only when `prompt_name` belongs to the frozen `CERTIFIED_CALLSITES` set:

- `extract_nodes.extract_message`
- `extract_nodes.extract_text`
- `extract_nodes.extract_json`
- `extract_edges.edge`

The set does not contain `dedupe_nodes.nodes`. The transform replaces the
`<PREVIOUS MESSAGES>` block in extraction prompts with an empty JSON list and
records a context-selection event. It does not call a provider, alter graph
state, or modify the Node-resolution prompt directly.

## Native Node request path

The repository adapter constructs the Node-resolution context from
`extracted_nodes`, the merged candidate union and its payload, current episode
content, and serialized `previous_episodes`, then calls the real Graphiti
`prompt_library.dedupe_nodes.nodes` template. The template visibly includes
the previous-episode projection, current message, extracted entities, existing
entities, batch cardinality and the joint response requirement.

The Node call therefore receives the state-dependent previous context in both
B0 Native and V6 no-reuse execution. V6 context stripping changes the upstream
extraction logical input and can change the resulting extracted-node set; it is
not a Native-equivalent no-op. The available sealed request artifacts do not
provide a byte-identical B0/V6 pair proving that extraction outputs are
unchanged under this transform.

## Root classification

`SEMANTIC_ROOT_V6_SPECIFIC`

The B1R2 structural question is consequently scoped to the Frozen V6
request/continuation semantics. It cannot be generalized to the unmodified
Native Graphiti algorithm. B0 remains the headline performance baseline, but
any structural result must state that V6's certified extraction context
selection is part of its semantic root.

## Evidence and limitations

| Fact | Source | Provenance |
|---|---|---|
| Certified callsite set excludes Node dedupe | `membind_v5/runtime/adapters/client_proxy.py` | `STATIC_CODE_FACT` |
| Context transform removes only previous-message block | `membind_v6_1/provider.py` | `STATIC_CODE_FACT` |
| Node context includes previous episodes and calls native template | `membind_v6_1/graphiti_compat.py` | `STATIC_CODE_FACT` |
| B0/V6 extraction outputs are byte-identical | no sealed paired artifact | `MISSING` |

No provider call, database write, scheduler change or live experiment was used.
