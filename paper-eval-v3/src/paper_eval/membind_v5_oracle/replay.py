from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal

from .model import DependencyKind, ReplayResult, RequestRecord, TraceBundle
from .request_dag import RequestDAG


class ReplayError(ValueError):
    """The trace cannot support a conservative request-level replay."""


Policy = Literal["ACTUAL", "FIFO", "ORACLE"]


def _fail(code: str) -> ReplayError:
    return ReplayError(code)


@dataclass(slots=True)
class _Running:
    request: RequestRecord
    start_ns: int
    terminal_ns: int


def _validate_bundle(bundle: TraceBundle, dag: RequestDAG) -> None:
    if bundle.configured_k <= 0:
        raise _fail("configured_k_invalid")
    if not dag.oracle_evaluable:
        raise _fail("oracle_not_evaluable")
    for request in bundle.requests:
        if request.service_duration_ns != request.terminal_ns - request.started_ns:
            raise _fail("service_duration_mismatch")
        if request.service_duration_ns < 0:
            raise _fail("service_duration_negative")
    for edge in dag.edges:
        if edge.kind == DependencyKind.UNKNOWN_DEPENDENCY:
            raise _fail("unknown_dependency_blocks_replay")


def _criticality(dag: RequestDAG, request_id: str) -> int:
    return dag.criticality_ns(request_id)


def _legal_waiting(
    waiting: dict[str, RequestRecord],
    *,
    completed: set[str],
    dag: RequestDAG,
    published_sources: set[int],
) -> list[RequestRecord]:
    ready = [
        request
        for request in waiting.values()
        if request.request_kind != "FRONTIER"
        or all(source in published_sources for source in range(request.source_sequence))
        if all(predecessor in completed for predecessor in dag.predecessors(request.request_id))
    ]
    frontier = [request for request in ready if request.request_kind == "FRONTIER"]
    return sorted(frontier or ready, key=lambda item: (item.submitted_ns, item.request_id))


def _selection(
    legal: list[RequestRecord],
    *,
    policy: Policy,
    dag: RequestDAG,
) -> RequestRecord:
    if not legal:
        raise _fail("no_legal_request")
    if policy in {"ACTUAL", "FIFO"}:
        return legal[0]
    return max(
        legal,
        key=lambda item: (
            _criticality(dag, item.request_id),
            len(dag.successors(item.request_id)),
            -item.submitted_ns,
            item.request_id,
        ),
    )


def _actual_order(bundle: TraceBundle) -> tuple[str, ...]:
    return tuple(
        request.request_id
        for request in sorted(
            bundle.requests,
            key=lambda item: (item.started_ns, item.request_id),
        )
    )


def _actual_replay(bundle: TraceBundle, *, dag: RequestDAG) -> ReplayResult:
    """Read-only validation/replay of the observed request lifecycle."""

    _validate_bundle(bundle, dag)
    requests = bundle.request_by_id
    order = _actual_order(bundle)
    completed: set[str] = set()
    starts = {request.request_id: request.started_ns for request in bundle.requests}
    terminals = {request.request_id: request.terminal_ns for request in bundle.requests}
    active_max = 0
    decisions: list[dict[str, Any]] = []
    choice_count = 0
    inversion_count = 0
    max_width = 1 if order else 0
    multi_duration = 0

    for request_id in order:
        request = requests[request_id]
        timestamp = request.started_ns
        active = sum(
            1
            for other in bundle.requests
            if other.request_id != request_id
            and other.started_ns <= timestamp < other.terminal_ns
        )
        if active >= bundle.configured_k:
            raise _fail("actual_k_violation")
        for predecessor in dag.predecessors(request_id):
            if predecessor in requests and terminals[predecessor] > timestamp:
                raise _fail("actual_dependency_violation")
        if request.request_kind == "FRONTIER":
            prior_publications = [
                record.publication_ns
                for source, record in bundle.publication_by_source.items()
                if source < request.source_sequence
            ]
            if prior_publications and min(prior_publications) > timestamp:
                raise _fail("actual_frontier_publication_order_violation")
        waiting = [
            candidate
            for candidate in bundle.requests
            if candidate.request_id not in completed
            and candidate.request_id not in order[: order.index(request_id)]
            and candidate.submitted_ns <= timestamp
            and candidate.request_id != request_id
            and all(
                predecessor not in requests or terminals[predecessor] <= timestamp
                for predecessor in dag.predecessors(candidate.request_id)
            )
            and (
                candidate.request_kind != "FRONTIER"
                or all(
                    record.publication_ns <= timestamp
                    for source, record in bundle.publication_by_source.items()
                    if source < candidate.source_sequence
                )
            )
        ]
        legal = [request, *waiting]
        frontier = [candidate for candidate in legal if candidate.request_kind == "FRONTIER"]
        legal = frontier or legal
        if request not in legal:
            raise _fail("actual_frontier_priority_violation")
        width = len({candidate.request_id for candidate in legal})
        max_width = max(max_width, width)
        if width > 1:
            choice_count += 1
            criticalities = {
                candidate.request_id: _criticality(dag, candidate.request_id)
                for candidate in legal
            }
            maximum = max(criticalities.values())
            selected_criticality = criticalities[request_id]
            if selected_criticality < maximum:
                inversion_count += 1
            decisions.append(
                {
                    "timestamp_ns": timestamp,
                    "waiting_request_ids": sorted(criticalities),
                    "selected_request_id": request_id,
                    "selected_criticality_ns": selected_criticality,
                    "maximum_legal_criticality_ns": maximum,
                    "active_count_before_selection": active,
                }
            )
            next_terminal = min(
                (other.terminal_ns for other in bundle.requests if other.started_ns > timestamp),
                default=timestamp,
            )
            multi_duration += max(0, next_terminal - timestamp)
        completed.add(request_id)
        active_max = max(active_max, active + 1)

    publication_times = {
        source: record.publication_ns for source, record in bundle.publication_by_source.items()
    }
    freshness = {
        source: publication_times[source] - record.arrival_ns
        for source, record in bundle.publication_by_source.items()
    }
    makespan = max(publication_times.values(), default=0) - min(
        (record.arrival_ns for record in bundle.publication_by_source.values()), default=0
    )
    goodput = None if makespan <= 0 or not publication_times else len(publication_times) / (makespan / 1_000_000_000)
    return ReplayResult(
        policy="ACTUAL",
        request_count=len(bundle.requests),
        request_start_ns=starts,
        request_terminal_ns=terminals,
        request_start_order=order,
        publication_ns=publication_times,
        freshness_ns=freshness,
        makespan_ns=makespan,
        goodput_episodes_per_second=goodput,
        max_active_count=active_max,
        request_service_duration_ns={request.request_id: request.service_duration_ns for request in bundle.requests},
        extra_llm_calls=0,
        extra_input_tokens=0,
        speculative_waste=0,
        scheduler_choice_count=choice_count,
        criticality_inversion_count=inversion_count,
        max_legal_choice_width=max_width,
        multi_choice_duration_ns=multi_duration,
        decision_points=tuple(decisions),
        actual_publication_delta_ns={source: 0 for source in publication_times},
    )


def replay(bundle: TraceBundle, *, dag: RequestDAG, policy: Policy) -> ReplayResult:
    """Replay request admission without changing work or service durations."""

    if policy not in {"ACTUAL", "FIFO", "ORACLE"}:
        raise _fail("policy_invalid")
    if policy == "ACTUAL":
        return _actual_replay(bundle, dag=dag)
    _validate_bundle(bundle, dag)

    requests = bundle.request_by_id
    all_requests = tuple(bundle.requests)
    release_order = tuple(
        sorted(all_requests, key=lambda item: (item.submitted_ns, item.request_id))
    )
    actual_order = _actual_order(bundle)
    actual_rank = {request_id: index for index, request_id in enumerate(actual_order)}
    waiting: dict[str, RequestRecord] = {}
    pending_release = list(release_order)
    completed: set[str] = set()
    published_sources: set[int] = set()
    running: list[_Running] = []
    start_times: dict[str, int] = {}
    terminal_times: dict[str, int] = {}
    start_order: list[str] = []
    decision_points: list[dict[str, Any]] = []
    scheduler_choice_count = 0
    inversion_count = 0
    max_choice_width = 0
    multi_choice_duration_ns = 0
    max_active = 0
    clock = min((request.submitted_ns for request in all_requests), default=0)

    source_request_ids: dict[int, set[str]] = {}
    for request in all_requests:
        source_request_ids.setdefault(request.source_sequence, set()).add(request.request_id)

    def update_published_sources(now: int) -> None:
        for source, request_ids in source_request_ids.items():
            if source in published_sources or not request_ids.issubset(completed):
                continue
            record = bundle.publication_by_source.get(source)
            if record is None:
                continue
            latest = max(terminal_times[request_id] for request_id in request_ids)
            publication_time = latest + dag.publication_tail_ns(source)
            if publication_time <= now:
                published_sources.add(source)

    def add_releases(now: int) -> None:
        while pending_release and pending_release[0].submitted_ns <= now:
            request = pending_release.pop(0)
            waiting[request.request_id] = request

    def finish_until(now: int) -> None:
        nonlocal running, clock
        while running and min(item.terminal_ns for item in running) <= now:
            finish_at = min(item.terminal_ns for item in running)
            finished = [item for item in running if item.terminal_ns == finish_at]
            running = [item for item in running if item.terminal_ns != finish_at]
            for item in finished:
                completed.add(item.request.request_id)
                terminal_times[item.request.request_id] = finish_at
            clock = max(clock, finish_at)
            update_published_sources(clock)

    while waiting or pending_release or running:
        next_release = pending_release[0].submitted_ns if pending_release else None
        next_finish = min((item.terminal_ns for item in running), default=None)
        if not waiting and len(running) >= bundle.configured_k:
            if next_finish is None:
                raise _fail("replay_deadlock")
            finish_until(next_finish)
            add_releases(clock)
            continue
        if not waiting and next_release is not None and (next_finish is None or next_release < next_finish):
            clock = max(clock, next_release)
            add_releases(clock)
        else:
            add_releases(clock)

        update_published_sources(clock)
        legal = _legal_waiting(
            waiting,
            completed=completed,
            dag=dag,
            published_sources=published_sources,
        )
        if not legal:
            if running:
                finish_until(min(item.terminal_ns for item in running))
                continue
            publication_deadlines = [
                max(terminal_times[request_id] for request_id in request_ids)
                + dag.publication_tail_ns(source)
                for source, request_ids in source_request_ids.items()
                if request_ids.issubset(completed) and source not in published_sources
            ]
            if publication_deadlines:
                clock = max(clock, min(publication_deadlines))
                update_published_sources(clock)
                continue
            if pending_release:
                clock = max(clock, pending_release[0].submitted_ns)
                add_releases(clock)
                continue
            raise _fail("dependency_deadlock")

        available = bundle.configured_k - len(running)
        if available <= 0:
            finish_until(min(item.terminal_ns for item in running))
            continue

        width = len(legal)
        max_choice_width = max(max_choice_width, width)
        if width > 1:
            scheduler_choice_count += 1
            if next_finish is not None:
                multi_choice_duration_ns += max(0, next_finish - clock)

        selected = _selection(legal, policy=policy, dag=dag)
        selected_criticality = _criticality(dag, selected.request_id)
        maximum_criticality = max(_criticality(dag, candidate.request_id) for candidate in legal)
        if policy == "ACTUAL" and selected_criticality < maximum_criticality:
            inversion_count += 1

        if policy == "ACTUAL":
            # Reconstruct the observed choice only when it is legal. A sealed
            # trace with an unobservable queue identity fails closed.
            legal_by_rank = sorted(legal, key=lambda item: actual_rank.get(item.request_id, 10**18))
            selected = legal_by_rank[0]
            if selected.request_id not in actual_rank:
                raise _fail("actual_request_identity_missing")

        del waiting[selected.request_id]
        start_ns = max(clock, selected.submitted_ns)
        terminal_ns = start_ns + selected.service_duration_ns
        start_times[selected.request_id] = start_ns
        terminal_times[selected.request_id] = terminal_ns
        start_order.append(selected.request_id)
        running.append(_Running(selected, start_ns, terminal_ns))
        max_active = max(max_active, len(running))
        clock = start_ns

        # Admit another request into the same residual K=2 width when legal.
        if len(running) < bundle.configured_k:
            add_releases(clock)

    publication_by_source = bundle.publication_by_source
    publication_times: dict[int, int] = {}
    freshness: dict[int, int] = {}
    for source, publication in publication_by_source.items():
        source_requests = [
            request_id
            for request_id, request in requests.items()
            if request.source_sequence == source
        ]
        request_done = max((terminal_times[request_id] for request_id in source_requests), default=publication.arrival_ns)
        tail = dag.publication_tail_ns(source)
        publication_times[source] = request_done + tail
        freshness[source] = publication_times[source] - publication.arrival_ns

    makespan = max(publication_times.values(), default=0) - min(
        (publication.arrival_ns for publication in publication_by_source.values()),
        default=0,
    )
    goodput = None if makespan <= 0 or not publication_times else len(publication_times) / (makespan / 1_000_000_000)
    actual_publications = {source: record.publication_ns for source, record in publication_by_source.items()}
    return ReplayResult(
        policy=policy,
        request_count=len(all_requests),
        request_start_ns=start_times,
        request_terminal_ns=terminal_times,
        request_start_order=tuple(start_order),
        publication_ns=publication_times,
        freshness_ns=freshness,
        makespan_ns=makespan,
        goodput_episodes_per_second=goodput,
        max_active_count=max_active,
        request_service_duration_ns={request.request_id: request.service_duration_ns for request in all_requests},
        extra_llm_calls=0,
        extra_input_tokens=0,
        speculative_waste=0,
        scheduler_choice_count=scheduler_choice_count,
        criticality_inversion_count=inversion_count,
        max_legal_choice_width=max_choice_width,
        multi_choice_duration_ns=multi_choice_duration_ns,
        decision_points=tuple(decision_points),
        actual_publication_delta_ns={
            source: publication_times[source] - actual_publications[source]
            for source in actual_publications
        },
    )
