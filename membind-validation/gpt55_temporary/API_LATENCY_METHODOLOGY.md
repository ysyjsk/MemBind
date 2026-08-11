# Black-Box API Latency Methodology

This note defines what the temporary non-streaming `/chat/completions` lane can
measure and how those observations relate to systems evaluation practice. It is
not authority for the Native vLLM lane.

## Observable Quantity

For each remote request, use the local monotonic clock:

```text
client_observed_remote_api_wait
  = response-complete timestamp - request-start timestamp
```

This interval can include client scheduling and serialization, connection
setup, DNS/TCP/TLS, network transfer, relay and provider queues, model
prefill/decode, and response deserialization. Therefore it must not be named
`model_latency`, `inference_time`, or `server_compute_time`.

For one Graphiti episode, report all three values:

```text
W_sum   = sum of individual remote request intervals
W_union = interval union of remote request intervals
T_wall  = add_episode end - add_episode start

api_wait_wall_fraction = W_union / T_wall
```

`W_sum` is cumulative request work. `W_union` is caller wall-clock occupancy.
Concurrent calls overlap, so `W_sum / T_wall` is not a valid wall-time fraction.
All timestamps use one local monotonic clock; no cross-host clock subtraction is
performed.

The optional `openai-processing-ms` header is stored, when present, only as an
unverified provider hint. Its queue/gateway/model boundary is undocumented for
this relay, so it is never subtracted from end-to-end wait.

## What Is Unavailable

The current request is non-streaming:

```text
TTFT = unavailable
ITL/TPOT = unavailable
time-to-complete = observable
```

DistServe and Llumnix can separate prefill/TTFT and decode/TPOT because their
experiments control the serving stack and use suitable server/streaming
telemetry. A completed JSON Chat response exposes neither boundary. Even a
future SSE experiment would provide only client-observed time to first nonempty
chunk and inter-chunk delay; an SSE chunk is not guaranteed to correspond to
one model token. Streaming would change wire behavior and requires a separate
frozen protocol.

## Failure Semantics

HTTP 401/403/429/5xx and timeouts are retained as failure observations. They do
not enter a successful completion-latency distribution.

- A 401/403 duration is **time-to-rejection**, not inference latency.
- A 429 duration is time-to-rate-limit response; on a shared relay its cause may
  be ambiguous.
- A timeout is right-censored time-to-failure, not a completed request.
- Retry count remains zero. If a later protocol admits retries, failed attempt,
  backoff, and retry time all belong to the same logical trial.

Clockwork separates goodput from the complete request latency distribution and
does not erase rejected/deadline-missed work. The temporary lane follows that
principle by retaining every failed attempt artifact while excluding failures
from successful latency summaries.

Current one-request observations are:

| Attempt | Transport route | Outcome | Caller-observed time |
| --- | --- | --- | ---: |
| `...-001` | environment-proxy urllib | HTTP 403 | 508.06 ms |
| `...-002` | direct urllib | HTTP 403 | 1824.39 ms |
| `...-003` | direct OpenAI SDK | HTTP 403 | 2165.94 ms |

These three values are reported individually as time-to-rejection. They are not
averaged, summarized as model performance, or used to infer provider compute.

## Warm State And Caching

For this bounded screening run:

```text
client_connection_preconditioning = none
provider_warm_state                = unknown
provider_cache_reset               = unavailable
```

The formal prompt is not sent as a warmup. First-request connection setup is a
real part of caller-observed remote wait. Usage fields such as `cached_tokens`
are recorded when supplied, but the client cannot reset or verify provider
prefix caches.

If a later repeated black-box campaign is justified, freeze a separate
synthetic connection canary before observing treatment results. Do not warm up
with the measured prompt. Report method order and `cached_tokens`, and retain
the classification `black_box_diagnostic`; a shared relay cannot provide the
same resettable serving envelope as local vLLM.

## Repeats And Ordering

One episode is bounded screening, not a latency distribution. It supports raw
phase/request values and a descriptive API occupancy fraction only. It does not
support P99, significance, or stability claims.

Any later method comparison should freeze complete-history repeats in advance
and use blocked balanced ordering (AB/BA for two methods; balanced permutations
for more methods). The experimental unit is the complete history/run, not each
correlated LLM call inside one episode. Keep endpoint, alias, prompt hash, token
budget, transport, timeout, proxy policy, and connection policy fixed within a
block. Token-normalized latency may be secondary, but raw end-to-end latency
remains primary.

## Relation To Systems Papers

- **DistServe, OSDI 2024** reports TTFT/TPOT SLO attainment under offered-load
  sweeps by controlling prefill and decode serving. We borrow the explicit
  latency semantics and load discipline, not phase visibility that this relay
  does not provide. [USENIX](https://www.usenix.org/conference/osdi24/presentation/zhong-yinmin)
- **Llumnix, OSDI 2024** uses long request traces and reports end-to-end,
  prefill, and decode tails. This is evidence that one bounded episode cannot
  support tail claims. [USENIX](https://www.usenix.org/conference/osdi24/presentation/sun-biao)
- **Parrot, OSDI 2024** evaluates application-level end-to-end behavior and
  explicitly models network delay around an OpenAI-style Chat API. For a real
  remote relay we retain observed network/API wait rather than subtracting an
  unverifiable RTT estimate. [USENIX](https://www.usenix.org/conference/osdi24/presentation/lin-chaofan)
- **Clockwork, OSDI 2020** distinguishes goodput from the latency of all
  requests, including rejected/deadline-missed work. This motivates preserving
  403/429/timeout artifacts. [USENIX](https://www.usenix.org/conference/osdi20/presentation/gujarati)
- **Clipper, NSDI 2017** treats prediction latency end to end and emphasizes
  bounded tail latency, reinforcing the use of caller-observed time while also
  showing why many samples are required for tail claims. [USENIX](https://www.usenix.org/conference/nsdi17/technical-sessions/presentation/crankshaw)
- **vLLM/PagedAttention, SOSP 2023** evaluates varying request rates with long
  traces and reports raw and output-token-normalized end-to-end latency. We may
  use token normalization as a secondary diagnostic after successful calls,
  never as a replacement for raw caller time. [ACM DOI](https://dl.acm.org/doi/10.1145/3600006.3613165)

## Claim Boundary

After a successful bounded run, this lane can answer:

> What fraction of one Native Graphiti construction's caller wall time was
> occupied by black-box remote API waits under this observed relay state?

It cannot answer provider prefill/decode cost, controlled-server capacity,
causal MemBind speedup, or behavior under a resettable identical serving
envelope. Those claims remain with the local, observable vLLM mainline.

