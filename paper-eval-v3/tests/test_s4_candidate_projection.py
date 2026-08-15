"""Offline TDD for the production UUID-independent candidate projector."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

from paper_eval.artifacts import payload_sha256
from paper_eval.s4_candidate_projection import (
    PROJECTION_SCHEMA_SHA256,
    GraphitiCandidateProjector,
    activate_resolution_entities,
    install_graphiti_candidate_sidecar_hooks,
)
from paper_eval.s4_candidate_sidecar_runtime import (
    CandidateSidecarRuntimeError,
    current_candidate_projection,
    install_candidate_sidecar_hook,
)
from paper_eval.s4_candidate_sidecar import ReplaySidecarBinder, build_candidate_call_record


def _sha(value: str) -> str:
    import hashlib

    return hashlib.sha256(value.encode()).hexdigest()


NAMESPACE = "pev3-s4-sidecar-test"


def _sources() -> list[SimpleNamespace]:
    return [
        SimpleNamespace(
            source_sequence=index,
            source_hash=_sha(f"source-{index}"),
            name=f"episode-{index}",
            body=f"private body {index}",
        )
        for index in range(49)
    ]


def _node(uuid: str, name: str, *, labels: list[str] | None = None):
    return SimpleNamespace(
        uuid=uuid,
        group_id=NAMESPACE,
        name=name,
        labels=labels or ["Entity"],
        summary=f"summary for {name}",
        attributes={"kind": name.casefold()},
    )


def _episode(uuid: str, sequence: int, *, group_id: str = NAMESPACE):
    source = _sources()[sequence]
    return SimpleNamespace(
        uuid=uuid,
        group_id=group_id,
        name=source.name,
        content=source.body,
    )


def _edge(
    source: str,
    target: str,
    *,
    fact: str,
    episode_uuid: str,
    name: str = "RELATION",
):
    return SimpleNamespace(
        uuid="volatile-edge-id",
        group_id=NAMESPACE,
        source_node_uuid=source,
        target_node_uuid=target,
        name=name,
        fact=fact,
        episodes=[episode_uuid],
        attributes={"confidence": 1},
        valid_at=None,
        invalid_at=None,
        reference_time=None,
        expired_at=None,
        created_at="volatile",
        fact_embedding=[1.0],
    )


class Loaders:
    def __init__(self, nodes: list[SimpleNamespace], episodes: list[SimpleNamespace]):
        self.nodes = {value.uuid: value for value in nodes}
        self.episodes = {value.uuid: value for value in episodes}
        self.entity_calls: list[tuple[list[str], str]] = []
        self.episode_calls: list[list[str]] = []

    async def entities(self, _driver, uuids, *, group_id):
        self.entity_calls.append((list(uuids), group_id))
        return [self.nodes[uuid] for uuid in uuids if uuid in self.nodes]

    async def provenance(self, _driver, uuids):
        self.episode_calls.append(list(uuids))
        return [self.episodes[uuid] for uuid in uuids if uuid in self.episodes]


async def _project(
    *,
    prefix: str,
    reverse_candidates: bool = False,
) -> tuple[dict, Loaders]:
    sources = _sources()
    current_episode = _episode(f"{prefix}-episode-7", 7)
    prior_episode = _episode(f"{prefix}-episode-3", 3)
    current_nodes = [
        _node(f"{prefix}-alice", "Alice", labels=["Person"]),
        _node(f"{prefix}-acme", "Acme", labels=["Organization"]),
    ]
    historical_node = _node(f"{prefix}-bob", "Bob", labels=["Person"])
    loaders = Loaders([historical_node], [prior_episode])
    projector = GraphitiCandidateProjector(
        driver=object(),
        namespace=NAMESPACE,
        episodes=sources,
        entity_loader=loaders.entities,
        episode_loader=loaders.provenance,
    )
    extracted = _edge(
        current_nodes[0].uuid,
        current_nodes[1].uuid,
        fact="Alice works at Acme",
        episode_uuid=current_episode.uuid,
    )
    candidates = [
        _edge(
            current_nodes[0].uuid,
            current_nodes[1].uuid,
            fact="shared fact",
            episode_uuid=prior_episode.uuid,
        ),
        _edge(
            historical_node.uuid,
            current_nodes[1].uuid,
            fact="shared fact",
            episode_uuid=prior_episode.uuid,
        ),
    ]
    if reverse_candidates:
        candidates.reverse()
    with activate_resolution_entities(current_nodes):
        result = await projector.project(
            extracted_edge=extracted,
            related_edges=[],
            invalidation_edges=candidates,
            episode=current_episode,
        )
    return result, loaders


@pytest.mark.asyncio
async def test_projection_is_uuid_independent_and_preserves_candidate_order() -> None:
    capture, capture_loaders = await _project(prefix="capture")
    replay, replay_loaders = await _project(prefix="replay", reverse_candidates=True)

    assert capture["logical_call_sha256"] == replay["logical_call_sha256"]
    assert [
        item["logical_identity_sha256"] for item in capture["invalidation"]
    ] == list(
        reversed(
            [
                item["logical_identity_sha256"]
                for item in replay["invalidation"]
            ]
        )
    )
    assert capture["invalidation"][0]["candidate_id"] == 0
    assert replay["invalidation"][0]["candidate_id"] == 0
    assert capture_loaders.entity_calls[0][1] == NAMESPACE
    assert replay_loaders.episode_calls
    assert len(PROJECTION_SCHEMA_SHA256) == 64


@pytest.mark.asyncio
async def test_projection_accepts_graphiti_pre_attribute_empty_summaries() -> None:
    """Graphiti resolves edges before it populates summaries for new nodes."""

    async def project(prefix: str) -> tuple[dict, Loaders]:
        sources = _sources()
        current = _episode(f"{prefix}-current", 0)
        alice = _node(f"{prefix}-alice", "Alice")
        acme = _node(f"{prefix}-acme", "Acme")
        for node in (alice, acme):
            node.summary = ""
            node.attributes = {}
        loaders = Loaders([], [])
        projector = GraphitiCandidateProjector(
            driver=object(),
            namespace=NAMESPACE,
            episodes=sources,
            entity_loader=loaders.entities,
            episode_loader=loaders.provenance,
        )
        with activate_resolution_entities([alice, acme]):
            result = await projector.project(
                extracted_edge=_edge(
                    alice.uuid,
                    acme.uuid,
                    fact="new",
                    episode_uuid=current.uuid,
                ),
                related_edges=[],
                invalidation_edges=[],
                episode=current,
            )
        return result, loaders

    capture, capture_loaders = await project("capture")
    replay, replay_loaders = await project("replay")

    assert capture["source_sequence"] == 0
    assert capture["logical_call_sha256"] == replay["logical_call_sha256"]
    assert capture_loaders.entity_calls == replay_loaders.entity_calls == []


@pytest.mark.parametrize("summary", [None, 7, [], {}])
def test_resolution_context_rejects_non_string_summary(summary: object) -> None:
    node = _node("alice", "Alice")
    node.summary = summary

    with pytest.raises(CandidateSidecarRuntimeError, match="incomplete"):
        with activate_resolution_entities([node]):
            pass


@pytest.mark.asyncio
async def test_direction_and_provenance_are_semantic_identity_components() -> None:
    sources = _sources()
    current = _episode("current", 7)
    previous_a = _episode("previous-a", 3)
    previous_b = _episode("previous-b", 4)
    alice = _node("alice", "Alice")
    acme = _node("acme", "Acme")
    loaders = Loaders([], [previous_a, previous_b])
    projector = GraphitiCandidateProjector(
        driver=object(),
        namespace=NAMESPACE,
        episodes=sources,
        entity_loader=loaders.entities,
        episode_loader=loaders.provenance,
    )
    extracted = _edge("alice", "acme", fact="new", episode_uuid="current")
    candidates = [
        _edge("alice", "acme", fact="same", episode_uuid="previous-a"),
        _edge("acme", "alice", fact="same", episode_uuid="previous-a"),
        _edge("alice", "acme", fact="same", episode_uuid="previous-b"),
    ]

    with activate_resolution_entities([alice, acme]):
        result = await projector.project(
            extracted_edge=extracted,
            related_edges=[],
            invalidation_edges=candidates,
            episode=current,
        )

    identities = {
        value["logical_identity_sha256"] for value in result["invalidation"]
    }
    assert len(identities) == 3


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "failure",
    ["missing-node", "foreign-edge", "foreign-episode", "body-drift"],
)
async def test_projection_fails_closed_on_incomplete_or_foreign_join(failure: str) -> None:
    sources = _sources()
    current = _episode("current", 7)
    alice = _node("alice", "Alice")
    acme = _node("acme", "Acme")
    prior = _episode("prior", 3)
    if failure == "foreign-episode":
        prior.group_id = "other-namespace"
    if failure == "body-drift":
        prior.content = "different private body"
    nodes = [] if failure == "missing-node" else [_node("missing", "Missing")]
    loaders = Loaders(nodes, [prior])
    projector = GraphitiCandidateProjector(
        driver=object(),
        namespace=NAMESPACE,
        episodes=sources,
        entity_loader=loaders.entities,
        episode_loader=loaders.provenance,
    )
    candidate = _edge("missing", "acme", fact="same", episode_uuid="prior")
    if failure == "foreign-edge":
        candidate.group_id = "other-namespace"

    with activate_resolution_entities([alice, acme]):
        with pytest.raises(CandidateSidecarRuntimeError):
            await projector.project(
                extracted_edge=_edge(
                    "alice", "acme", fact="new", episode_uuid="current"
                ),
                related_edges=[],
                invalidation_edges=[candidate],
                episode=current,
            )
    if failure == "foreign-edge":
        assert loaders.entity_calls == []
        assert loaders.episode_calls == []


@pytest.mark.asyncio
async def test_installer_supplies_outer_entities_without_changing_graphiti_result() -> None:
    sources = _sources()
    current = _episode("current", 7)
    alice = _node("alice", "Alice")
    acme = _node("acme", "Acme")
    loaders = Loaders([], [])
    projector = GraphitiCandidateProjector(
        driver=object(),
        namespace=NAMESPACE,
        episodes=sources,
        entity_loader=loaders.entities,
        episode_loader=loaders.provenance,
    )
    extracted = _edge("alice", "acme", fact="new", episode_uuid="current")
    module = SimpleNamespace()

    async def resolve_one(llm, edge, related, existing, episode, edge_types=None):
        del llm, related, existing, episode, edge_types
        assert edge is extracted
        return (edge, [], [])

    async def resolve_many(clients, edges, episode, entities, edge_types, edge_map):
        del edge_types, edge_map
        return [
            await module.resolve_extracted_edge(
                clients.llm_client, edges[0], [], [], episode, None
            )
        ]

    module.resolve_extracted_edge = resolve_one
    module.resolve_extracted_edges = resolve_many
    with install_graphiti_candidate_sidecar_hooks(module, projector=projector):
        result = await module.resolve_extracted_edges(
            SimpleNamespace(llm_client=object()),
            [extracted],
            current,
            [alice, acme],
            {},
            {},
        )

    assert result == [(extracted, [], [])]
    assert module.resolve_extracted_edge is resolve_one
    assert module.resolve_extracted_edges is resolve_many
    assert loaders.entity_calls == []


@pytest.mark.asyncio
async def test_replay_publication_is_blocked_while_capture_calls_remain() -> None:
    sources = _sources()
    current = _episode("current", 7)
    alice = _node("alice", "Alice")
    acme = _node("acme", "Acme")
    loaders = Loaders([], [])
    projector = GraphitiCandidateProjector(
        driver=object(),
        namespace=NAMESPACE,
        episodes=sources,
        entity_loader=loaders.entities,
        episode_loader=loaders.provenance,
    )
    source_hash = sources[7].source_hash
    logical_call = _sha("logical-call")
    binder = ReplaySidecarBinder(
        identity={
            "attempt_id": "006",
            "cache_id": "test-cache",
            "episode_manifest_sha256": projector.episode_manifest_sha256,
            "history_id": "07741c45",
            "projection_schema_sha256": PROJECTION_SCHEMA_SHA256,
        },
        records=[
            build_candidate_call_record(
                source_sequence=7,
                source_hash=source_hash,
                logical_call_sha256=logical_call,
                prompt_sha256=_sha("prompt"),
                related=[],
                invalidation=[],
            )
        ],
    )
    calls = 0

    async def resolve_one(*args, **kwargs):
        del args, kwargs
        return (None, [], [])

    module = SimpleNamespace(resolve_extracted_edge=resolve_one)
    owner = SimpleNamespace()

    async def resolve_many(*args, **kwargs):
        del args, kwargs
        return []

    async def publish(*args, **kwargs):
        nonlocal calls
        del args, kwargs
        calls += 1
        return "published"

    owner._extract_and_resolve_edges = resolve_many
    owner._process_episode_data = publish

    with install_graphiti_candidate_sidecar_hooks(
        module,
        projector=projector,
        phase_owner=owner,
        replay_binder=binder,
    ):
        with pytest.raises(CandidateSidecarRuntimeError, match="unconsumed"):
            await owner._process_episode_data(current)

        binder.bind(
            source_sequence=7,
            source_hash=source_hash,
            logical_call_sha256=logical_call,
            related=[],
            invalidation=[],
        )
        assert await owner._process_episode_data(current) == "published"

    assert calls == 1


def test_projection_schema_is_hash_only_and_partition_is_not_identity() -> None:
    assert PROJECTION_SCHEMA_SHA256 == payload_sha256(
        {
            "candidate_identity": [
                "fact",
                "relation",
                "directed_source_endpoint",
                "directed_target_endpoint",
                "semantic_time",
                "expired_boolean",
                "semantic_attributes",
                "stable_provenance",
            ],
            "excluded_identity": [
                "candidate_position",
                "created_at",
                "group_id",
                "neo4j_id",
                "rank",
                "runtime_uuid",
            ],
            "partition_is_structural": True,
            "schema_version": "membind.paper-eval-v3.s4-candidate-projection.v1",
        }
    )


@pytest.mark.asyncio
async def test_pre_prompt_projection_is_task_local_under_concurrent_edges() -> None:
    release = asyncio.Event()
    entered = 0
    observed: dict[str, int] = {}

    async def projector(**kwargs):
        marker = kwargs["extracted_edge"]
        return {
            "source_sequence": marker.source_sequence,
            "source_hash": _sha(f"source-{marker.source_sequence}"),
            "logical_call_sha256": _sha(f"call-{marker.source_sequence}"),
            "related": [],
            "invalidation": [],
        }

    async def original(_llm, edge, *_args):
        nonlocal entered
        entered += 1
        if entered == 2:
            release.set()
        await release.wait()
        projection = current_candidate_projection()
        observed[edge.name] = projection["source_sequence"]
        return edge

    module = SimpleNamespace(resolve_extracted_edge=original)
    first = SimpleNamespace(name="first", source_sequence=1)
    second = SimpleNamespace(name="second", source_sequence=2)
    with install_candidate_sidecar_hook(module, projector=projector):
        results = await asyncio.gather(
            module.resolve_extracted_edge(object(), first, [], [], object()),
            module.resolve_extracted_edge(object(), second, [], [], object()),
        )

    assert results == [first, second]
    assert observed == {"first": 1, "second": 2}
