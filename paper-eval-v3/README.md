# MemBind Paper Evaluation v3

This directory is an isolated execution lane for `MemBind_PAPER_EVALUATION_WORKPLAN_v3_SMALL_FIRST_FINAL.md`.
It intentionally does not import or mutate the old live-action contracts or artifacts.

The lane follows test-driven development:

```text
RED contract test -> minimum implementation -> focused GREEN -> full offline regression -> live stage
```

Long-running live stages must run in `tmux`. Every live episode appends a durable event and atomically updates its checkpoint so a reconnect can resume the completed prefix.

Secrets are loaded only at runtime from the existing project environment. They are never copied into this directory or written to artifacts/logs.

Current status: S2 is stopped after a near-zero Native edge-surface result. The
review in `S2_RETRIEVAL_SURFACE_ANALYSIS_20260814.md` establishes that the old
Edge@10 result was not official LongMemEval session Recall@10. Future code now
binds retrieval units explicitly, but S3 and any replacement live S2 run remain
unauthorized.

The versioned correction is frozen in
`../MemBind_PAPER_EVALUATION_PROTOCOL_AMENDMENT_v3.1.md`; the official-paper and
repository audit is `S2_LITERATURE_AND_CODE_DESIGN_AUDIT_20260814.md`. The
amendment preserves all historical artifacts and permits no live call by
itself. The next candidate action remains one explicitly authorized, read-only
S2-R0 episode-surface probe after the complete offline gate is green.
