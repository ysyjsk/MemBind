# S2-R0 Failure Root-Cause Report

Status: finalized offline diagnosis of the terminal, non-mergeable attempt
`s2r0-20260814-001`. This report is not a retry authorization.

## 1. Terminal outcome

The one-shot authorization was consumed before live I/O. The corpus preflight
completed, `Graphiti.search_()` was entered exactly once, and the attempt then
stopped with a sanitized `TypeError` before the Episode full-text query reached
Neo4j.

```text
attempt status                     FAILED_STOPPED
result mergeable                   false
retrieval conclusion               NOT_PRODUCED
S3 authorized                      false
Graphiti.search_ calls             1
Neo4j read requests                1
construction LLM requests          0
embedding requests                 0
cross-encoder requests             0
Reader/Judge requests              0 / 0
database mutation attempts/writes  0 / 0
cleanup calls                      0
retry count                        0
```

Because `graphiti_search_calls` is incremented only after `_preflight_corpus()`
returns, the failure is downstream of the exact 49-episode corpus guard. The
single Neo4j read is that completed guard query. No retrieval score can be
reported from this attempt.

## 2. Evidence chain

```text
S2_R0_OFFLINE_QUALIFICATION.json
  da15919283dddcd9835c112d3e63be8fab8389db5871b9a6b4c8b618bb0869f4

S2_R0_AUTHORIZATION.json
  0a83291a4455013a5476e17ba3e9443eb9761ca55acd05b8fbd6a502f2be023a

S2_R0_AUTHORIZATION_CONSUMPTION.json
  564e2ee43d7810280d40edefa3a9050e9b1025af974161e94482a07c182acb7d

S2_R0_FAILURE.json
  f5709742e6f2209ebfa72d6b8d7b7566af7649774b34adc76819740cd40f71ff
```

The failure artifact binds both the authorization and consumption hashes. The
historical source hashes recorded by the consumed authorization remain the
authority for this failed attempt:

```text
s2_retrieval_probe.py  c95e4c49d87b5e2e32e120a9ebb7bb854f7f1b7fb27fadfff3607e74349b87fb
test_s2_retrieval_probe.py
                       314e8b401b20ad13030aeff086285c8079ccf9f88599528492b9be5d256ff504
```

Neither the qualification, authorization, consumption, nor failure artifact
was overwritten during diagnosis.

## 3. Deterministic root cause

The read-only driver guard used this effective wrapper signature:

```python
execute_read_only(query, *args, **kwargs)
```

Pinned Graphiti 0.29.3's legacy Neo4j Episode BM25 path invokes the driver with
both a positional Cypher query and a named Lucene parameter:

```python
driver.execute_query(
    cypher_query,
    query=fuzzy_query,
    limit=limit,
    routing_="r",
)
```

Python therefore attempted to bind both the positional Cypher value and the
named Lucene value to the wrapper's `query` parameter and raised:

```text
TypeError: execute_read_only() got multiple values for argument 'query'
```

The exception occurs before the wrapper body, explaining why the Episode
search read did not increment `neo4j_read_requests`.

Classification:

```text
HARNESS_QUERY_PARAMETER_NAME_COLLISION
```

This is a test-double/production-signature coverage defect in the S2-R0
read-only guard, not a Neo4j outage, corpus mismatch, retrieval miss, model
failure, or Graphiti quality result.

## 4. TDD repair

The production guard's first parameter was renamed to `cypher_query_`, matching
the pinned `Neo4jDriver.execute_query(cypher_query_, **kwargs)` contract. The
mutation denylist, required read routing, counter behavior, and forwarded
keyword parameters are unchanged.

The test double was also changed to the pinned production driver signature, and
the search fixture now passes `query=<Lucene query>` as the real Graphiti path
does.

RED evidence:

```text
TDD_RED_S2R0_QUERY_PARAMETER_COLLISION_20260814.xml
03d9adfc3a74eeb71f7331a995ff577979692a101065fa9c670fa57665d3c002
```

GREEN evidence:

```text
targeted real-call-shape test     1/1 passed
probe module                      12/12 passed
S2-R0 focused                     51/51 passed
full offline regression           146/146 passed
```

```text
TDD_GREEN_S2R0_QUERY_PARAMETER_COLLISION_20260814.xml
a971c97e0e6c95c6a31b1b69a40bff89076ceb9b2f2e4c5f797de94039b3cd25

TDD_FOCUSED_GREEN_S2R0_QUERY_PARAMETER_REPAIR_20260814.xml
5ef931ccf09958e6c56d9a0fbf964dc2f0c2779425fef203066331b7ba45a4b7

TDD_FOCUSED_GREEN_S2R0_POST_FAILURE_REPAIR_20260814.xml
7b3925df6de4f1c6704883fd74bf8a8e8427bd91f661e09901dbd3917d175e08

TDD_FULL_OFFLINE_GREEN_S2R0_POST_FAILURE_REPAIR_20260814.xml
60090c69f1aa25490f642b3d041c3c89dd3e6d97c8752a40f4265a3bc59e68f9
```

Repaired source identities:

```text
s2_retrieval_probe.py
  b2f68a780a93c95dec154bbad874d32a581333ffad87d63859c75bf3f8d36d7e

test_s2_retrieval_probe.py
  85a990f66a3d0342bf03c93409d82a6215b64f1d1755b3f384ef98b68c6636b0
```

## 5. Retry and research status

The consumed authorization cannot be reused. The failed run remains terminal
and non-mergeable. No cleanup, namespace rebuild, retry, Reader/Judge call,
retrieval-policy decision, or S3 transition was performed.

A future rerun requires all of the following:

```text
new run ID
new result/consumption/failure paths
new offline qualification binding the repaired source and new GREEN evidence
new one-shot authorization
explicit post-failure execution approval
```

Even a successful repaired S2-R0 remains a bounded Episode diagnostic and does
not by itself authorize S3.
