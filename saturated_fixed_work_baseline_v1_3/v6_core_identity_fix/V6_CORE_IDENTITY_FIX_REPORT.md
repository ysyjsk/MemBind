# Frozen V6 Identity Fix and V7 Two-Source Probe

The preregistered scan found no sealed block satisfying the formal
`MEMBIND_CORE` + `v6-membind-core-v1` + complete-completion identity. Seven old
V6 blocks and 74 V6.1 blocks were therefore excluded rather than silently
reclassified. Historical V6.1 candidate evidence does contain non-empty context
removal (for example, one sealed candidate records 32 certified events,
1,662,480 removed characters in total, and a 96,339-character maximum), but it
is not a Frozen Core headline artifact. The unsealed `baseline-correction-core`
attempt cannot repair that missing provenance.

Because the eligible old effect is `MISSING`, and the preserved artifacts do not
bind a complete Native-equivalent previous window (membership, order, content,
temporal and selector predicates, `last_n`, ties, and prompt serialization), the
preregistered branch is `BRANCH_MISSING_OLD_EFFECT` with
`PREVIOUS_WINDOW_EQUIVALENCE=UNKNOWN_MISSING_BINDING`.

The minimal implementation correction is isolated to the new Core entry path:
it passes no certified message transform, records
`implementation_revision=context-integrity-fix-v1`, rejects context removal in
the Core inventory, and uses non-strict exact binding so a mismatched or missing
transcript is discarded without consumption and delegated to one fresh Native
call. Historical V6.1 paths retain the old helper. Nine focused tests pass.

The authorized two-source observer (`history=07741c45`, sources 0 and 1) was
started with the corrected identity and an isolated namespace. It failed before
forming a pair because the local provider returned truncated JSON during source
1 `extract_edges.edge` capture. This is sealed as `PROBE_INVALID`; it is not a
zero-signal or NULL result, and no six-source run is authorized from it.

No B0 rerun, B1 comparison, held-out access, speculative reuse, or modification
of old sealed artifacts occurred.
