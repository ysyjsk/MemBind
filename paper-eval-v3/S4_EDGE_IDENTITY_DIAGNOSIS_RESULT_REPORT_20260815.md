# S4 Edge Identity Diagnosis Result Report

Date: 2026-08-15

Scope: bounded read-only diagnosis of retry-005 source sequence 7. This report
does not make retry-005 mergeable and does not authorize cleanup, retry-006,
fixed-four qualification, S5, or PILOT.

## Verdict

```text
D1 fact-only identity                         NON_INJECTIVE CONFIRMED
D2 exact pre-prompt edge calls                10/10
D2 related partitions                         10 EMPTY
D2 invalidation partitions                    10 UNIQUE
D2 candidates / enriched identities per call  10 / 10
verdict                                       SIDECAR_AMENDMENT_JUSTIFIED
```

The verdict means only that the allowed stable projection is unique on the
preserved replay prefix. Retry-005 did not record capture-side internal
candidate linkage, so this result cannot prove a retroactive capture/replay
bijection. It authorizes offline TDD for a two-sided pre-prompt sidecar.

## Persisted-evidence diagnosis

The sealed capture cache and terminal capture graph independently recomputed:

```text
source sequence                         7
edge extraction records                 1
unique extracted edges                  10
edge resolution prompts                 10
fact-only ambiguous prompts              9
duplicate multiplicity                   2
matching terminal capture edges          2
directed endpoint pairs distinct      true
```

The repeated prompt-visible fact identity is:

```text
6679fc83a11fe8dd2a12856e9278067c7760115882f3058d7efe5290c4165e0d
```

This confirms that `fact` alone is not injective. It does not expose the fact
text, endpoint text, prompt, response, episode body, or runtime UUID.

## Read-only dry run

The successful controller used authority/consumption suffix `_004`. It built
the Graphiti runtime outside an active event loop, retained read-only prompt
and embedding cache wrappers, replaced every live model transport with a
raising sentinel, forced all statically validated Cypher through read routing,
blocked session/transaction/schema/write operations, blocked Graphiti's
publication boundary, and stopped all ten concurrent edge calls at the same
pre-prompt barrier.

```text
Neo4j read queries            62
network calls                  0
live LLM calls                 0
live embedding calls           0
cross-encoder calls            0
database writes                0
publication calls              0
cache writes                   0
```

Namespace and cache evidence was identical before and after:

```text
nodes / relationships / episodes        32 / 48 / 7
namespace snapshot SHA256
90cd850e0452cddebc92d43d4647cbf703b07a4f025a784b7168ea7db508dbd6

prompt cache SHA256
0a0cf225623c1ea4a153516806a8895d7bdd565fc46c4d468f6e357a74071cc9

embedding cache SHA256
47f2a4ad0897ac5ab9298bf11738144e1d59d41aa84e2019035597726876310b
```

Nine invalidation partitions still contained a fact-only duplicate of
multiplicity two; every one of the ten partitions contained ten distinct
enriched logical identities. Stable directed endpoints and the other allowed
semantic components therefore distinguish the collision class observed in
retry-005 without position, rank, UUID, group ID, Neo4j ID, or `created_at`.

## Bootstrap ledger

Three earlier operational attempts are retained rather than overwritten:

1. Default `paper-eval-v3/.venv` lacked `graphiti_core`; failure occurred
   before Graphiti runtime construction and before any database/model call.
2. The correct Graphiti environment reached the fence, which rejected pinned
   Graphiti's unrouted but read-only `retrieve_episodes` query before native
   driver execution. The fence was TDD-amended to validate Cypher and force
   `routing_="r"` itself.
3. Candidate search exposed a Python wrapper parameter collision between the
   positional Cypher argument and Graphiti's `query=` search parameter. The
   wrapper was TDD-amended to match Neo4j's `cypher_query_` shape.

All three attempts stopped before publication. Their authority consumption
files and logs remain immutable. No final diagnosis artifact existed until the
successful `_004` attempt.

## TDD evidence

```text
new focused diagnosis tests     77 passed
post-live complete offline     709 passed
compileall                      passed
git diff --check                passed
public artifact privacy scan    passed
strict artifact verifier        passed
```

Primary files:

- `artifacts/paper_eval/native/S4_EDGE_IDENTITY_DIAGNOSIS_RETRY_005.json`
  - file SHA256:
    `b8ca37b4fcc014027ec5c9111ed9ee73d15f275bb507d1acfd1d510ac9a8c4ae`
  - internal artifact SHA256:
    `dcb224eb92f040405697e7e8e1b98387949923cc6527c7e88c59949e84d4f237`
- `runtime/S4_EDGE_IDENTITY_DIAGNOSIS_AUTHORITY_RETRY_005_004.json`
- `runtime/S4_EDGE_IDENTITY_DIAGNOSIS_AUTHORITY_CONSUMPTION_RETRY_005_004.json`
- `logs/S4_EDGE_IDENTITY_DIAGNOSIS_RETRY_005_004.log`
- `logs/TDD_FULL_OFFLINE_GREEN_S4_EDGE_IDENTITY_D2_POST_LIVE_20260815.xml`

## Next boundary

Implement a two-sided, observational sidecar at the same pre-prompt boundary:

```text
capture internal hash-only candidate projection -> sealed sidecar
replay internal projection -> partition-preserving multiset equality
                           -> unique logical-identity bijection
                           -> positional response translation
```

The next work remains offline TDD. A future live retry requires a separate
amendment, full offline GREEN, fresh cache and namespaces, preflight, contract,
and single-use authority. The existing retry-005 activation implementation may
not consume that future result.
