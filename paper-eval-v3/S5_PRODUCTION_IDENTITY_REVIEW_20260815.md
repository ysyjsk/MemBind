# S5 Production Identity Review

Date: 2026-08-15

Status: offline review only. This document does not authorize a model call,
Neo4j read/write, a live method smoke, an authority issuance, or a current
stage update.

## Decision

The S5 method identity must be a method-specific, public, hash-sealed
description of the complete production entry-point closure. A source hash by
itself is not a qualification result and must not promote the historical M2
prototype to production. Qualification status and evidence remain separate
from identity construction.

The three identities are distinct and must never be aliased to `M0`, `M1`, or
`M2`:

```text
A0   FIFO durable enqueue + one Native worker
P*   exactly two whole-update Native workers
M*   shared prepare/bind core + ordered publication
```

## Minimum Identity Contract

Every method identity should bind the following public fields:

1. `method_id`, method concurrency, scheduler name, construction path, and
   an explicit `identity_schema` version.
2. The common Native runtime factory entry point and its source hash.
3. The exact `graphiti_native` module name, `add_episode` qualname,
   `graphiti_episode_kwargs` qualname, and module source hash.
4. Graphiti version `0.29.3` and repository commit
   `021d3a57d511f21b10adaf7fa923bd5c1fce5e9d`.
5. A method-specific source closure. Each executable role needs a source
   hash and, where a test exists, a test hash. At minimum this covers the
   scheduler, adapter/runner, durable store, common native binding, and
   invariant checker for `P*`.
6. `runtime_config`: only sanitized, non-secret values (for example model
   revision, embedding revision, context policy, structured-output policy,
   database routing label, and timeout/retry policy), plus a hash of the exact
   serialized config. A hash with no bound config artifact is not sufficient
   for later audit.
7. The failure-classification policy/version, including the distinction
   between infrastructure/adapter failures and a scientifically retained
   `P*` direct-invariant violation.
8. For `M*`, the production core, ordered binder, durable publication journal,
   FX0 harness, and exact-parity artifact identities. The same core hash must
   be used for FX0 and live execution.
9. A canonical identity digest computed over every field except the digest
   itself. Verification must reject field, hash, method, Graphiti pin, or
   callable drift.

The identity verifier must recursively reject credentials, API keys, request
bodies, prompts, messages, raw model output, episode bodies, namespaces,
authority tokens, and other private fields. It should reject absolute host
paths where a stable role/entry-point identifier is sufficient; the source
digest is the reproducible binding.

## Qualification Boundary

The identity artifact should include a status such as
`OFFLINE_IDENTITY_ONLY` or `PRODUCTION_IDENTITY_FROZEN`, but status alone is
not evidence. Live authority may be issued only after the corresponding
method-specific gates pass:

```text
A0: focused adapter/durability checks + full offline regression + one-history
    smoke contract ready
P*: same, with two real overlapping intervals and complete accounting; any
    direct invariant violation is retained as a scientific outcome
M*: durable intent/commit/publication boundaries + failure poison/recovery +
    FX0 production-path exact parity + full offline regression
```

The artifact must bind the hashes of those sealed qualification artifacts and
the current S5 plan, but must not infer qualification merely from a matching
source hash. A failed or incomplete attempt remains non-mergeable and cannot
be resumed in place.

## Review Findings Against the Initial Runner Test Shape

The initial identity test shape correctly required the Graphiti version/commit,
U0 builder, Native add-episode entry point, source hashes, and recursive
private-field rejection. Before the runner can be used for a production
identity, add or verify the following:

```text
graphiti_episode_kwargs entry point and graphiti_native module source hash
runtime factory source hash/entry point
scheduler and durable-store test hashes
method-specific adapter/runner source closure
sanitized runtime-config payload bound to its config hash
failure-classification policy identity
M* FX0/core/parity artifact bindings
qualification artifact/status binding separate from identity
```

These are contract-level requirements only. No live service, database, model,
namespace, authority, or current-stage pointer was touched for this review.

### Current runner implementation note

The in-progress `s5_production_runner.py` now adds runtime-factory and test
hash fields and correctly keeps `qualification_status` at
`IDENTITY_ONLY_UNQUALIFIED`. Two details still require a focused RED test and
repair before considering the identity contract green:

* `runtime_factory_entrypoint` is an entry-point string, not a SHA256. Its
  validation must use a stable dotted-symbol/path shape; passing it through the
  SHA validator rejects the required value.
* The current identity remains hash-only for runtime configuration and does
  not yet include the explicit Graphiti private-API signature, method-specific
  adapter/source closure, failure-classification version, or M* FX0/core
  artifact bindings described above.

The existing test helper also needs to supply the newly required runtime
factory entry point and scheduler/durable-store test hashes. Until that
focused suite is green, this is an intentional TDD RED state and no method
authority may be issued.

The focused runner suite is now green for the currently implemented A0/P*
composition. That is only an offline composition result: the runner still
explicitly rejects M* (`mstar_requires_fx0_production_adapter`), and the P*
path does not yet inject or persist a separate direct-invariant checker result.
Therefore neither fact qualifies the corresponding live method. A0/P* source
identity may be used as an implementation artifact, while M* remains blocked
until its shared FX0/live core and parity artifact exist.

## TDD Acceptance Order

The implementation should follow the existing TDD lane:

```text
RED: reject missing closure/config/failure-policy/FX0 fields
focused GREEN: accept one complete identity per method
QA RED: mutate each binding, digest, method, and private field
focused GREEN: fail closed on every mutation
full offline regression
only then build method-specific live authority
```
