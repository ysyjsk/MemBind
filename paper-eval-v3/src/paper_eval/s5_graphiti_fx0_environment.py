"""Generic controlled environment for production-path Graphiti FX0 cases.

The environment owns every non-semantic adapter hook.  Provider plans describe
only external nondeterminism, while the strict source decoder accepts Graphiti
episode data and rejects fixture directives or expected outcomes.
"""

from __future__ import annotations

import re
import time
from collections.abc import Iterator, Mapping, Sequence
from contextlib import contextmanager
from copy import deepcopy
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from graphiti_core.edges import EntityEdge
from graphiti_core.nodes import EntityNode, EpisodeType, EpisodicNode
from pydantic import create_model

from .artifacts import payload_sha256
from .fx0_mechanism_fixture import ControlledNondeterminism, Fx0ExecutionCase
from .s5_graphiti_controlled_fixture import (
    ControlledGraphitiFixture,
    ControlledGraphitiProviders,
    build_controlled_graphiti_fixture,
)
from .s5_graphiti_mstar_semantics import GraphitiEpisodeInput
from .s5_mstar_production_adapter import Fx0DecodedSource, S5MStarProductionAdapter


_FORBIDDEN_SOURCE_KEYS = {
    "error_code",
    "expected_error_code",
    "expected_history",
    "expected_state",
    "expected_status",
    "fault_mode",
    "raise",
    "result",
    "transition",
    "verdict",
}
_EPISODE_FIELDS = {
    "uuid",
    "name",
    "content",
    "source",
    "source_description",
    "group_id",
    "valid_at",
    "edge_types",
}
_EDGE_TYPE_NAME = re.compile(r"^[A-Za-z][A-Za-z0-9_]*$")
_PUBLICATION_ACTIONS = {"APPEND", "DROP", "DUPLICATE"}


class S5GraphitiFx0EnvironmentError(ValueError):
    """The production FX0 environment or provider plan is malformed."""


def _fail(code: str) -> S5GraphitiFx0EnvironmentError:
    return S5GraphitiFx0EnvironmentError(code)


def _mapping(value: object, code: str) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise _fail(code)
    return deepcopy(dict(value))


def _sequence(value: object, code: str) -> tuple[object, ...]:
    if isinstance(value, (str, bytes)) or not isinstance(value, Sequence):
        raise _fail(code)
    return tuple(value)


def _datetime(value: object, code: str) -> datetime:
    if not isinstance(value, str) or not value:
        raise _fail(code)
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        raise _fail(code) from None
    if parsed.tzinfo is None:
        raise _fail(code)
    return parsed.astimezone(timezone.utc)


def _logical_time_ns(value: object) -> int:
    parsed = _datetime(value, "LOGICAL_TIME_INVALID")
    return int(parsed.timestamp() * 1_000_000_000)


def _reject_source_directives(value: object) -> None:
    if isinstance(value, Mapping):
        for key, child in value.items():
            if (
                not isinstance(key, str)
                or key.casefold() in _FORBIDDEN_SOURCE_KEYS
                or key.casefold().startswith("expected_")
            ):
                raise _fail("FX0_SOURCE_DIRECTIVE_FORBIDDEN")
            _reject_source_directives(child)
    elif isinstance(value, Sequence) and not isinstance(value, (str, bytes)):
        for child in value:
            _reject_source_directives(child)


@dataclass(frozen=True)
class ControlledTransactionIOSchedule:
    """Attempt numbers that fail after the real transaction callback runs."""

    fail_after_callback_attempts: tuple[int, ...] = ()


@dataclass(frozen=True)
class ControlledPublicationSinkSchedule:
    """Per-source behavior of the external publication evidence sink."""

    actions_by_source: tuple[str, ...] = ()

    def action(self, source_sequence: int) -> str:
        if source_sequence < len(self.actions_by_source):
            return self.actions_by_source[source_sequence]
        return "APPEND"


@dataclass(frozen=True)
class S5GraphitiFx0ActiveProviders:
    """Typed, hash-bound provider plan passed through the semantic runtime."""

    graphiti_providers: ControlledGraphitiProviders
    logical_times_ns: tuple[int, ...]
    transaction_io_schedule: ControlledTransactionIOSchedule
    publication_sink_schedule: ControlledPublicationSinkSchedule
    prepare_rendezvous_parties: int
    provider_plan_sha256: str


def _transaction_schedule(value: object) -> ControlledTransactionIOSchedule:
    row = _mapping(value, "TRANSACTION_IO_SCHEDULE_INVALID")
    if set(row) - {"fail_after_callback_attempts"}:
        raise _fail("TRANSACTION_IO_SCHEDULE_INVALID")
    raw = _sequence(
        row.get("fail_after_callback_attempts", ()),
        "TRANSACTION_IO_SCHEDULE_INVALID",
    )
    attempts: list[int] = []
    for attempt in raw:
        if isinstance(attempt, bool) or not isinstance(attempt, int) or attempt < 1:
            raise _fail("TRANSACTION_IO_SCHEDULE_INVALID")
        attempts.append(attempt)
    if len(attempts) != len(set(attempts)):
        raise _fail("TRANSACTION_IO_SCHEDULE_INVALID")
    if attempts not in ([], [1]):
        raise _fail("TRANSACTION_IO_SCHEDULE_UNSUPPORTED")
    return ControlledTransactionIOSchedule(tuple(sorted(attempts)))


def _publication_schedule(value: object) -> ControlledPublicationSinkSchedule:
    row = _mapping(value, "PUBLICATION_SINK_SCHEDULE_INVALID")
    if set(row) - {"actions_by_source"}:
        raise _fail("PUBLICATION_SINK_SCHEDULE_INVALID")
    raw = _sequence(
        row.get("actions_by_source", ()),
        "PUBLICATION_SINK_SCHEDULE_INVALID",
    )
    actions = tuple(str(action).upper() for action in raw)
    if any(action not in _PUBLICATION_ACTIONS for action in actions):
        raise _fail("PUBLICATION_SINK_SCHEDULE_INVALID")
    return ControlledPublicationSinkSchedule(actions)


def _entity_node(value: object) -> EntityNode:
    row = _mapping(value, "CANDIDATE_NODE_INVALID")
    allowed = {"uuid", "name", "group_id", "summary", "labels", "created_at"}
    if set(row) - allowed or not {"uuid", "name", "group_id"} <= set(row):
        raise _fail("CANDIDATE_NODE_INVALID")
    if "created_at" in row:
        row["created_at"] = _datetime(row["created_at"], "CANDIDATE_NODE_INVALID")
    try:
        return EntityNode(**row)
    except Exception:
        raise _fail("CANDIDATE_NODE_INVALID") from None


def _episode_node(value: object) -> EpisodicNode:
    row = _mapping(value, "INITIAL_EPISODE_INVALID")
    required = {
        "uuid",
        "name",
        "content",
        "source",
        "source_description",
        "group_id",
        "valid_at",
    }
    if set(row) != required:
        raise _fail("INITIAL_EPISODE_INVALID")
    try:
        source = EpisodeType(row["source"])
    except (TypeError, ValueError):
        raise _fail("INITIAL_EPISODE_INVALID") from None
    row["source"] = source
    row["valid_at"] = _datetime(row["valid_at"], "INITIAL_EPISODE_INVALID")
    try:
        return EpisodicNode(**row)
    except Exception:
        raise _fail("INITIAL_EPISODE_INVALID") from None


def _invalidation_edge(value: object) -> EntityEdge:
    row = _mapping(value, "INVALIDATION_EDGE_INVALID")
    required = {
        "uuid",
        "group_id",
        "source_node_uuid",
        "target_node_uuid",
        "created_at",
        "name",
        "fact",
        "episodes",
        "valid_at",
    }
    if set(row) != required:
        raise _fail("INVALIDATION_EDGE_INVALID")
    row["created_at"] = _datetime(row["created_at"], "INVALIDATION_EDGE_INVALID")
    row["valid_at"] = _datetime(row["valid_at"], "INVALIDATION_EDGE_INVALID")
    try:
        return EntityEdge(**row)
    except Exception:
        raise _fail("INVALIDATION_EDGE_INVALID") from None


class S5GraphitiFx0ControlledEnvironment:
    """Own the generic provider, source, sink, snapshot, and witness hooks."""

    def __init__(self, *, fixture: ControlledGraphitiFixture | None = None) -> None:
        self.fixture = fixture or build_controlled_graphiti_fixture()
        self.runtime = self.fixture.runtime
        self.runtime.controlled_provider_scope = self.controlled_provider_scope
        self.runtime.latest_state_retriever = self.retrieve_latest_state
        self._active: S5GraphitiFx0ActiveProviders | None = None
        self._publication_history: list[dict[str, object]] = []
        self._event_ledger: list[dict[str, object]] = []
        self._decoded_episode_nodes: dict[int, EpisodicNode] = {}
        self._visible_episode_nodes: list[EpisodicNode] = []
        self._latest_state_observations: list[tuple[str, ...]] = []
        self._clock_ns = 0

    def controlled_provider_factory(
        self, providers: ControlledNondeterminism
    ) -> S5GraphitiFx0ActiveProviders:
        if not isinstance(providers, ControlledNondeterminism):
            raise _fail("CONTROLLED_PROVIDER_PLAN_INVALID")
        logical_times = tuple(_logical_time_ns(value) for value in providers.logical_times)
        if not logical_times:
            raise _fail("LOGICAL_TIME_MISSING")

        embeddings = _mapping(providers.embeddings, "EMBEDDING_PROVIDER_INVALID")
        if set(embeddings) != {"vector"}:
            raise _fail("EMBEDDING_PROVIDER_INVALID")
        raw_vector = _sequence(embeddings["vector"], "EMBEDDING_PROVIDER_INVALID")
        if not raw_vector or any(
            isinstance(value, bool) or not isinstance(value, (int, float))
            for value in raw_vector
        ):
            raise _fail("EMBEDDING_PROVIDER_INVALID")
        vector = tuple(float(value) for value in raw_vector)

        initial = _mapping(providers.initial_state, "INITIAL_GRAPH_STATE_INVALID")
        if set(initial) - {"episodes", "invalidation_edges", "nodes"}:
            raise _fail("INITIAL_GRAPH_STATE_INVALID")
        initial_episodes = tuple(
            _episode_node(value)
            for value in _sequence(
                initial.get("episodes", ()), "INITIAL_GRAPH_STATE_INVALID"
            )
        )
        invalidation_edges = tuple(
            _invalidation_edge(value)
            for value in _sequence(
                initial.get("invalidation_edges", ()),
                "INITIAL_GRAPH_STATE_INVALID",
            )
        )
        candidate_sets: list[tuple[EntityNode, ...]] = []
        for value in providers.candidate_sets:
            row = _mapping(value, "CANDIDATE_SET_INVALID")
            if set(row) != {"nodes"}:
                raise _fail("CANDIDATE_SET_INVALID")
            candidate_sets.append(
                tuple(
                    _entity_node(node)
                    for node in _sequence(row["nodes"], "CANDIDATE_SET_INVALID")
                )
            )
        llm_responses = deepcopy(dict(providers.llm_responses))
        rendezvous = llm_responses.pop("__prepare_rendezvous_parties__", 0)
        if (
            isinstance(rendezvous, bool)
            or not isinstance(rendezvous, int)
            or rendezvous not in {0, 2}
        ):
            raise _fail("PREPARE_RENDEZVOUS_INVALID")
        graphiti_providers = ControlledGraphitiProviders(
            llm_responses=llm_responses,
            embedding_vector=vector,
            logical_time_ns=logical_times[0],
            initial_state=initial_episodes,
            candidate_node_sets=tuple(candidate_sets),
            invalidation_edges=invalidation_edges,
        )
        return S5GraphitiFx0ActiveProviders(
            graphiti_providers=graphiti_providers,
            logical_times_ns=logical_times,
            transaction_io_schedule=_transaction_schedule(
                providers.transaction_io_schedule
            ),
            publication_sink_schedule=_publication_schedule(
                providers.publication_sink_schedule
            ),
            prepare_rendezvous_parties=rendezvous,
            provider_plan_sha256=payload_sha256(
                providers.production_hash_projection()
            ),
        )

    async def reset_case(self, active: S5GraphitiFx0ActiveProviders) -> None:
        if not isinstance(active, S5GraphitiFx0ActiveProviders):
            raise _fail("ACTIVE_PROVIDER_PLAN_INVALID")
        self.fixture.reset_case()
        retry_once = active.transaction_io_schedule.fail_after_callback_attempts == (1,)
        self.fixture.fail_transaction = False
        self.fixture.retry_transaction_once = retry_once
        self.fixture.idempotent_retry = retry_once
        self.fixture.mutate_retry_payload = False
        self.fixture.prepare_rendezvous_parties = active.prepare_rendezvous_parties
        self._publication_history.clear()
        self._event_ledger.clear()
        self._decoded_episode_nodes.clear()
        self._visible_episode_nodes.clear()
        self._latest_state_observations.clear()
        self._active = active
        self._clock_ns = 0

    @contextmanager
    def controlled_provider_scope(
        self, active: object
    ) -> Iterator[None]:
        if not isinstance(active, S5GraphitiFx0ActiveProviders):
            raise _fail("ACTIVE_PROVIDER_PLAN_INVALID")
        with self.fixture._provider_scope(active.graphiti_providers):
            yield

    def source_decoder(
        self,
        case: Fx0ExecutionCase,
        active: object,
    ) -> tuple[Fx0DecodedSource, ...]:
        if not isinstance(case, Fx0ExecutionCase):
            raise _fail("FX0_SOURCE_INVALID")
        if not isinstance(active, S5GraphitiFx0ActiveProviders):
            raise _fail("ACTIVE_PROVIDER_PLAN_INVALID")
        _reject_source_directives(case.source)
        source = _mapping(case.source, "FX0_SOURCE_INVALID")
        if set(source) != {"episodes"}:
            raise _fail("FX0_SOURCE_SHAPE_INVALID")
        episodes = _sequence(source["episodes"], "FX0_SOURCE_SHAPE_INVALID")
        if not episodes or len(episodes) != len(active.logical_times_ns):
            raise _fail("FX0_SOURCE_LOGICAL_TIME_COUNT_MISMATCH")

        decoded: list[Fx0DecodedSource] = []
        for index, value in enumerate(episodes):
            row = _mapping(value, "FX0_EPISODE_INVALID")
            if set(row) != _EPISODE_FIELDS:
                raise _fail("FX0_EPISODE_SHAPE_INVALID")
            for field in (
                "uuid",
                "name",
                "content",
                "source_description",
                "group_id",
            ):
                if not isinstance(row[field], str) or not row[field]:
                    raise _fail("FX0_EPISODE_INVALID")
            try:
                source_type = EpisodeType(row["source"])
            except (TypeError, ValueError):
                raise _fail("FX0_EPISODE_INVALID") from None
            valid_at = _datetime(row["valid_at"], "FX0_EPISODE_INVALID")
            edge_type_names = _sequence(row["edge_types"], "FX0_EPISODE_INVALID")
            if any(
                not isinstance(name, str) or _EDGE_TYPE_NAME.fullmatch(name) is None
                for name in edge_type_names
            ):
                raise _fail("FX0_EPISODE_INVALID")
            edge_types = {
                name: create_model(f"Fx0{name}") for name in edge_type_names
            }
            episode = EpisodicNode(
                uuid=row["uuid"],
                name=row["name"],
                content=row["content"],
                source=source_type,
                source_description=row["source_description"],
                group_id=row["group_id"],
                valid_at=valid_at,
            )
            self._decoded_episode_nodes[index] = episode
            decoded.append(
                Fx0DecodedSource(
                    source_sha256=payload_sha256(row),
                    opaque_source=GraphitiEpisodeInput(
                        episode_node=episode,
                        previous_episodes=active.graphiti_providers.initial_state,
                        group_id=row["group_id"],
                        edge_types=edge_types or None,
                    ),
                    logical_time_ns=active.logical_times_ns[index],
                )
            )
        return tuple(decoded)

    async def persist_event(self, event: Mapping[str, object]) -> None:
        if self._active is None:
            raise _fail("ACTIVE_PROVIDER_PLAN_MISSING")
        row = deepcopy(dict(event))
        self._event_ledger.append(row)
        if row.get("event_type") != "publication":
            return
        sequence = row.get("source_sequence")
        if isinstance(sequence, bool) or not isinstance(sequence, int) or sequence < 0:
            raise _fail("PUBLICATION_SOURCE_SEQUENCE_INVALID")
        projection = {"source_sequence": sequence, "event": "publish"}
        episode = self._decoded_episode_nodes.get(sequence)
        if episode is None:
            raise _fail("PUBLICATION_SOURCE_EPISODE_MISSING")
        self._visible_episode_nodes.append(episode)
        action = self._active.publication_sink_schedule.action(sequence)
        if action == "APPEND":
            self._publication_history.append(projection)
        elif action == "DUPLICATE":
            self._publication_history.extend((projection, deepcopy(projection)))

    def snapshot(self) -> tuple[Mapping[str, Any], Sequence[Mapping[str, Any]]]:
        return (
            self.fixture.canonical_logical_state(),
            tuple(deepcopy(self._publication_history)),
        )

    def witness_snapshot(self, _case_id: str) -> Mapping[str, object]:
        projections = self.fixture.retry_commit_projections
        retry_observed = len(projections) >= 2 and projections[0] == projections[1]
        return {
            "prepare_to_bind_state_change_observed": any(
                len(observation)
                > len(self._active.graphiti_providers.initial_state)
                for observation in self._latest_state_observations
            )
            if self._active is not None
            else False,
            "retry_replay_observed": retry_observed,
            "transaction_attempt_count": max(1, self.fixture.transaction_attempts),
        }

    def publication_fault_detector(
        self,
        source_count: int,
        _state: Mapping[str, Any],
        history: Sequence[Mapping[str, Any]],
    ) -> str | None:
        published = [
            row.get("source_sequence")
            for row in history
            if row.get("event") == "publish"
        ]
        if len(published) != len(set(published)):
            return "DUPLICATE_PUBLICATION"
        if not published:
            return "LOST_PUBLICATION"
        if len(published) < source_count:
            return "PARTIAL_PUBLICATION"
        if published != list(range(source_count)):
            return "PARTIAL_PUBLICATION"
        return None

    async def retrieve_latest_state(
        self, _source: GraphitiEpisodeInput
    ) -> list[EpisodicNode]:
        if self._active is None:
            raise _fail("ACTIVE_PROVIDER_PLAN_MISSING")
        self.fixture.provider_ledger.consume("initial_state")
        state = [
            *self._active.graphiti_providers.initial_state,
            *self._visible_episode_nodes,
        ]
        self._latest_state_observations.append(tuple(node.uuid for node in state))
        return list(state)

    def clock_ns(self) -> int:
        self._clock_ns += 1
        return self._clock_ns

    def build_adapter(
        self,
        *,
        production_core_identity: Mapping[str, object],
    ) -> S5MStarProductionAdapter:
        return S5MStarProductionAdapter(
            production_core_identity=production_core_identity,
            production_core_identity_sha256=str(
                production_core_identity.get("identity_sha256", "")
            ),
            semantic_prepare=self.runtime.prepare,
            latest_state_bind=self.runtime.bind,
            snapshot=self.snapshot,
            persist_event=self.persist_event,
            clock_ns=self.clock_ns,
            source_decoder=self.source_decoder,
            reset_case=self.reset_case,
            witness_snapshot=self.witness_snapshot,
            controlled_provider_factory=self.controlled_provider_factory,
            publication_fault_detector=self.publication_fault_detector,
        )


__all__ = [
    "ControlledPublicationSinkSchedule",
    "ControlledTransactionIOSchedule",
    "S5GraphitiFx0ActiveProviders",
    "S5GraphitiFx0ControlledEnvironment",
    "S5GraphitiFx0EnvironmentError",
]
