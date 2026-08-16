# S5 A0 retry-004 isolated-attempt decision

Date: 2026-08-16

## Decision

Prepare exactly one fresh A0 attempt with this identity:

```text
run_id     s5-a0-20260816-004
namespace  pev3-s5-a0-20260816-004
purpose    bounded isolated correctness/predecessor qualification
```

This is not an authorization to launch the attempt. Preparation is limited to
one bounded read-only preflight followed, only if that preflight passes, by one
fresh single-use A0 authority. The preparation must then stop before tmux,
model generation, embedding generation, or Neo4j mutation.

## Basis

The decision is a bounded discriminator, not blind repetition:

1. `s5-a0-20260816-001` canonically completed its native attempt with 49/49
   publications. Its later post-observation failure does not show that native
   construction is incapable of completing under the frozen A0 path.
2. `s5-a0-20260816-002` and `s5-a0-20260816-003` both failed closed at
   `source_sequence=8` after 8/49 publications with
   `json.decoder.JSONDecodeError`.
3. Retry-002 and retry-003 overlapped on the shared construction vLLM for at
   least 31m08.305s. Their latency, throughput, queueing, and service-time
   observations are confounded and are excluded from performance evidence.
4. At 2026-08-16 04:02:00 +0800, the preparation audit observed no local
   connection to `10.87.5.247:8000`, no A0 controller/postprocess process, and
   no A0 tmux session. This is operational launch-readiness context, not a
   scientific measurement or a substitute for the fresh preflight.

Retry-004 may answer only whether the exact frozen A0 construction path can
complete once without a concurrent local consumer of the shared construction
service. It is not a performance run. Even if it later completes, this
decision alone does not authorize headline latency, throughput, or queueing
claims.

## Frozen identity

No protocol, code, schema, completion cap, workload, source closure, freeze,
or current-stage pointer is changed by this decision:

```text
git commit
568afb26053a5f8fb133e29f0583eaa524dad1bd

runtime config file SHA256
2e92e4e88152ed113b6c923335fd863689f19ffc5c3e01e1cd549379e99f1591

production materialization file SHA256
d85c868608f70c9a278f685da4c5b38a357523584359523aa3582fa3c7e16c32

production materialization payload SHA256
2aa9eb9d3e9e8e901513b87ebb4eb6206041f875a5c7761002e1a47c47950af1

production qualification file SHA256
c13383785404f865391ee0c323c446d13e11aa27432ce7dc7319fc85f315fad9

production qualification payload SHA256
940926149f4d41fffce566e01df6afe39ecc49956fd9593d7a0be12d73d78f01

14-role source closure digest
440943d82bae73a1ff87e2afcb18995dd191f84d6ecdfd31a22c46ca24527605

current-stage pointer file SHA256
3cb7edad4bab3ac6fe961a3d9e8768cbb962cf61cf946cb7e0015d74c0edc26d
```

The current materialization was reconstructed from the current source bytes,
and all 14 source-role identities were compared with the existing production
qualification before this decision was persisted.

Supporting prior evidence:

```text
A0-001 attempt result file SHA256
7d4fbc06d5a1c24d96638d5cf73d418228aff631a45443a98e1a54277e829861

A0-001 postprocess checkpoint file SHA256
8e4fa429009b221b288edb618cb2d1efb78a462ce7710703df8d1b0bba6a2d7f

retry-002/003 terminal collision report SHA256
4ab4e03722ee547e9cd0122235d35c52f363b1004395c2bd4b0dfd4a7e3f6c45
```

## Preparation contract

1. The retry-004 preflight must be a single bounded read-only production
   preflight using the existing canonical API.
2. It must verify the frozen construction and embedding services, Neo4j
   connectivity, and exactly zero nodes and relationships in
   `pev3-s5-a0-20260816-004`.
3. A failed preflight ends preparation without creating an authority.
4. A passing preflight may produce exactly one fresh single-use A0 authority
   bound to current source hashes and the unchanged current-stage pointer.
5. Creating that authority ends this preparation. No launcher may be invoked
   under this decision artifact.
6. No old namespace may be inspected for mutation, cleaned, reused, or merged.
7. No credential, prompt, model response, or private environment value may be
   persisted in the decision, preflight, authority, or report output.

If retry-004 is launched later under separate explicit direction, it must
first recheck that no competing local construction-service connection exists.
A repeated source-8 structured-output failure must terminate the attempt and
return to diagnosis; this decision does not authorize retry-005 or any further
blind repetition.

Planned preparation outputs:

```text
artifacts/paper_eval/native/S5_A0_LIVE_PREFLIGHT_RETRY_004_20260816.json
artifacts/paper_eval/native/S5_A0_LIVE_AUTHORITY_RETRY_004_20260816.json
```
