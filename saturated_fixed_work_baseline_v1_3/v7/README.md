# V7 Fail-Closed Handoff

The V7 theory, reference model, real Graphiti observer, Gate A-E evaluator,
blocked-terminal sealer, and hash-gated live-runner contract are implemented.
No treatment method is selected or authorized. The original V1 campaign branch
remains immutably sealed as `V7_THEORY_OR_SYSTEM_BLOCKED` under
`artifacts/v7-system-blocked-20260825-001`; the terminal manifest SHA-256 is
`d0da4c6bd04e6dfa71310f81c022a8d39b5117fe4511e48d4857464faac58e27`.

The frozen SiliconFlow endpoint and embedding probe passed, but the original
structured extraction timed out in bounded attempts. An intervening attempt
exposed and fixed a harness bug: Graphiti 0.29.3 treats a non-null
`add_episode(uuid=...)` as an existing-node lookup, so fresh native publication
must use `uuid=None`.

The later V2 attempt `v7-real-observer-v2-20260825-003` completed its R1/R2
block, then received unterminated structured JSON during R3-A. It is an invalid
provider-output attempt, not a scientific NULL or positive result. Its journal
and failure SHA-256 values are respectively
`2791cbf2b58ac7910718f79d716fdbcd5b93303d4a9d56576bbe82cf6a897998`
and `08d29ce5af14168b1e87d698fefb483e641e8baab09d0a4ffb5e1f71e567a6c2`.
It made zero treatment and replay calls and is not input to Gate A-E.

The V3 replacement produced durable evidence that one R3-A OLD edge-extraction
response ended with `finish_reason=length` at exactly 8192 completion tokens.
That attempt is invalid for gates, but it authorizes only a transport-limit
reauthorization. The fresh replacement is therefore preregistered in
`R1_R3_PROTOCOL_FREEZE_V4.json` (SHA-256
`8daa5a195ccb8746cc51aa404387c37dd83969a8259cac0054fd49753549ea6c`). V4
keeps the same model, workload, timeout, temperature, zero-retry policy,
structured mode, source-bound observer, and observer-only rules while raising
`requested_max_tokens` from 8192 to 16384. V4 provider-free preflight passes
with five contexts, matching native/Graphiti pins, a bound observer harness,
and zero provider calls.

V4-001 then confirmed a provider-side output cap: with the request bound to
16384, the R1/R2 fresh edge extraction still returned
`finish_reason=length` at 8192 completion tokens. The attempt is invalid and
its Gate outcome is `NOT_EVALUATED`; it made no treatment or replay calls. A
single V5 cap probe is frozen in `R1_R3_PROTOCOL_FREEZE_V5.json` (SHA-256
`a3abb7e6ea481952ed868886bfd958bad9060812e42ca1eb3d96e46a1d77dd0a`), raising
the request to 32768 while retaining every other V4 field. If the provider
still returns its 8192 cap, token-limit autoresearch stops fail-closed as an
infrastructure/provider limit; no further blind doubling is legal.

V5-001 confirmed that limit: with `requested_max_tokens=32768`, R3-A still
returned `finish_reason=length` at exactly 8192 completion tokens. Its attempt
journal SHA-256 is
`d3b55e5bc6fa3f0bd86ebaa847865c37c69824ff5bda1ec7c8b510f10261f925` and its
failure artifact SHA-256 is
`d758b1f04eb23766f5867c8aa30d437a3a03d8215eb38bb170a2d917f1af8d3c`.
The R1/R2 block was complete, R3-A was incomplete, Gate A-E was not evaluated,
and treatment/replay counts were zero. This is a provider infrastructure
blocker, not a scientific NULL and not evidence for any treatment method.

The campaign runs one two-source R1/R2 block followed by two independent
six-source R3 blocks. It never replays a response, returns an old read to
Graphiti, skips native demand construction, or publishes a shadow build.

The runner can be checked provider-free:

```bash
cd saturated_fixed_work_baseline_v1_3
../membind-validation/.venv/bin/python scripts/run_v7_live.py \
  --output-root v7/artifacts/gpu-dry-run-001 \
  --run-id v7-gpu-dry-run
```

This is provider-free and writes redacted `RUN_MANIFEST.json`, `RUN_STATE.json`,
`MANIFEST.json`, and `SEAL.json`. No API key is needed for dry-run. Do not put a
key in a config file, command-line argument, source file, or artifact.

An actual treatment call is blocked. It becomes legal only after a new
preregistered observer campaign completes real R1/R2 and both R3 blocks, Gate
A-E selects M0/M1/M2, and the selected `METHOD_SELECTION.json` is bound by the
R3 evidence manifest plus outer `MANIFEST/SEAL`. A self-asserted JSON gate is
rejected. If Gate E selects NULL, no treatment adapter is implemented and the
runner remains observer/dry-run only. When M0/M1/M2 is uniquely authorized and
its method-specific adapter has passed the theorem-derived differential tests,
the invocation shape is:

```bash
PYTHONPATH=src ../membind-validation/.venv/bin/python scripts/run_v7_live.py \
  --live --method M1 --gate v7/METHOD_SELECTION.json \
  --adapter your_gpu_adapter:run \
  --output-root v7/artifacts/gpu-live-001 --run-id v7-gpu-live-001
```

The adapter is called only after the seal, recomputed Gate A-E, execution
envelope, and key checks. It must return
`membind.v7.live-adapter-result.v1`, including the actual provider/treatment
counts, complete two-source publication frontier, canonical equivalence, zero
false reuse, and its own artifact-manifest digest. Invalid adapter evidence is
persisted as a sanitized fail-closed result and never becomes a live seal.

The gated two-source runner pins `https://api.siliconflow.cn/v1`,
`Qwen/Qwen3-32B`, `Qwen/Qwen3-Embedding-0.6B`, 900-second transport timeouts,
and zero SDK retries. Endpoint/model/source-count drift is rejected. A changed
provider envelope requires a new preregistration, not an override of this
blocked campaign. The key is never returned by `redact_config` or written to an
artifact.
