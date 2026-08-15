"""Offline TDD for S4's position-aware semantic replay oracle."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from itertools import permutations

import pytest

from paper_eval.s4_candidate_oracle import (
    CandidateAwareReplayCache,
    CandidateRemapError,
)


@dataclass(frozen=True)
class Parts:
    model_revision: str
    decoding_config: dict
    structured_output_schema: dict
    system_prompt: str
    user_prompt: str


@dataclass(frozen=True)
class Record:
    prompt_hash: str
    raw_response: str
    parsed_response: dict
    token_usage: dict
    prompt_parts: dict


class Cache:
    def __init__(self, records: list[Record]) -> None:
        self.read_only = True
        self._records = {record.prompt_hash: record for record in records}
        self.unexpected_prompt_diagnostics: list[dict] = []

    def get(self, parts: Parts):
        selected = asdict(parts)
        return next(
            (
                record
                for record in self._records.values()
                if record.prompt_parts == selected
            ),
            None,
        )

    def record_unexpected(self, parts: Parts) -> dict:
        diagnostic = {"prompt_hash": f"miss-{len(parts.user_prompt)}"}
        self.unexpected_prompt_diagnostics.append(diagnostic)
        return diagnostic

    def resolve(self, parts: Parts, *args, **kwargs):
        selected = self.get(parts)
        if selected is not None:
            return selected
        return "inner-miss"


def _parts(prompt_name: str, user_prompt: str, *, system: str = "system") -> Parts:
    return Parts(
        model_revision="revision",
        decoding_config={
            "temperature": 0.0,
            "prompt_name": prompt_name,
            "group_id": "__S4_ISOLATED_NAMESPACE__",
        },
        structured_output_schema={"type": "object"},
        system_prompt=system,
        user_prompt=user_prompt,
    )


def _record(parts: Parts, parsed: dict, *, suffix: str = "one") -> Record:
    return Record(
        prompt_hash=f"capture-{suffix}",
        raw_response="private-response-not-inspected",
        parsed_response=parsed,
        token_usage={"prompt_tokens": 1},
        prompt_parts=asdict(parts),
    )


def _node_prompt(candidates: list[dict], *, message: str = "fixed") -> str:
    import json

    return (
        f"<CURRENT MESSAGE>\n{message}\n</CURRENT MESSAGE>\n"
        "<EXISTING ENTITIES>\n"
        f"{json.dumps(candidates, ensure_ascii=False)}\n"
        "</EXISTING ENTITIES>\n"
        "fixed suffix"
    )


def _edge_prompt(
    related: list[dict],
    invalidation: list[dict],
    *,
    new_fact: str = "fixed",
) -> str:
    return (
        "<EXISTING FACTS>\n"
        f"{related!r}\n"
        "</EXISTING FACTS>\n"
        "<FACT INVALIDATION CANDIDATES>\n"
        f"{invalidation!r}\n"
        "</FACT INVALIDATION CANDIDATES>\n"
        f"<NEW FACT>\n{new_fact}\n</NEW FACT>"
    )


def _node(candidate_id: int, name: str) -> dict:
    return {
        "candidate_id": candidate_id,
        "name": name,
        "entity_types": ["Entity"],
        "summary": f"summary-{name}",
    }


def _edge(idx: int, fact: str) -> dict:
    return {"idx": idx, "fact": fact}


def test_exact_hit_is_returned_without_semantic_parsing() -> None:
    parts = _parts("dedupe_nodes.nodes", "not even a parseable candidate prompt")
    record = _record(parts, {"entity_resolutions": []})
    oracle = CandidateAwareReplayCache(Cache([record]))

    assert oracle.get(parts) is record
    assert oracle.exact_prompt_hit_count == 1
    assert oracle.candidate_remap_hit_count == 0
    assert oracle.candidate_remap_rejection_count == 0


def test_replay_wrapper_rejects_a_writable_cache() -> None:
    cache = Cache([])
    cache.read_only = False

    with pytest.raises(ValueError, match="read-only"):
        CandidateAwareReplayCache(cache)


def test_node_candidate_permutation_remaps_only_duplicate_candidate_ids() -> None:
    capture = _parts(
        "dedupe_nodes.nodes",
        _node_prompt([_node(0, "Alpha"), _node(1, "Beta")]),
    )
    replay = _parts(
        "dedupe_nodes.nodes",
        _node_prompt([_node(0, "Beta"), _node(1, "Alpha")]),
    )
    parsed = {
        "entity_resolutions": [
            {"id": 0, "name": "first", "duplicate_candidate_id": 0},
            {"id": 1, "name": "second", "duplicate_candidate_id": -1},
            {"id": 2, "name": "third", "duplicate_candidate_id": 1},
        ]
    }
    record = _record(capture, parsed)
    oracle = CandidateAwareReplayCache(Cache([record]))

    remapped = oracle.get(replay)

    assert remapped is not record
    assert remapped.parsed_response == {
        "entity_resolutions": [
            {"id": 0, "name": "first", "duplicate_candidate_id": 1},
            {"id": 1, "name": "second", "duplicate_candidate_id": -1},
            {"id": 2, "name": "third", "duplicate_candidate_id": 0},
        ]
    }
    assert record.parsed_response == parsed
    assert oracle.candidate_remap_hit_count == 1
    assert oracle.remap_hit_counts == {"dedupe_nodes.nodes": 1}


def test_edge_partitions_and_continuous_indices_are_remapped_independently() -> None:
    capture = _parts(
        "dedupe_edges.resolve_edge",
        _edge_prompt(
            [_edge(0, "related-a"), _edge(1, "related-b")],
            [_edge(2, "invalid-x"), _edge(3, "invalid-y")],
        ),
    )
    replay = _parts(
        "dedupe_edges.resolve_edge",
        _edge_prompt(
            [_edge(0, "related-b"), _edge(1, "related-a")],
            [_edge(2, "invalid-y"), _edge(3, "invalid-x")],
        ),
    )
    record = _record(
        capture,
        {
            "duplicate_facts": [0, 1, 0],
            "contradicted_facts": [3, 0, 2],
        },
    )
    oracle = CandidateAwareReplayCache(Cache([record]))

    remapped = oracle.get(replay)

    assert remapped.parsed_response == {
        "duplicate_facts": [1, 0, 1],
        "contradicted_facts": [2, 1, 3],
    }
    assert record.parsed_response == {
        "duplicate_facts": [0, 1, 0],
        "contradicted_facts": [3, 0, 2],
    }
    assert oracle.remap_hit_counts == {"dedupe_edges.resolve_edge": 1}


def test_node_translation_is_bijective_for_every_three_candidate_permutation() -> None:
    names = ("Alpha", "Beta", "Gamma")
    capture = _parts(
        "dedupe_nodes.nodes",
        _node_prompt([_node(index, name) for index, name in enumerate(names)]),
    )
    parsed = {
        "entity_resolutions": [
            {"id": index, "name": name, "duplicate_candidate_id": index}
            for index, name in enumerate(names)
        ]
    }

    for ordering in permutations(names):
        replay = _parts(
            "dedupe_nodes.nodes",
            _node_prompt(
                [_node(index, name) for index, name in enumerate(ordering)]
            ),
        )
        oracle = CandidateAwareReplayCache(Cache([_record(capture, parsed)]))
        resolved = oracle.get(replay)
        expected = [ordering.index(name) for name in names]
        actual = [
            item["duplicate_candidate_id"]
            for item in resolved.parsed_response["entity_resolutions"]
        ]
        assert actual == expected


def test_edge_translation_is_bijective_for_independent_partition_permutations() -> None:
    related = ("related-a", "related-b")
    invalidation = ("invalid-x", "invalid-y")
    capture = _parts(
        "dedupe_edges.resolve_edge",
        _edge_prompt(
            [_edge(index, fact) for index, fact in enumerate(related)],
            [
                _edge(len(related) + index, fact)
                for index, fact in enumerate(invalidation)
            ],
        ),
    )
    parsed = {"duplicate_facts": [0, 1], "contradicted_facts": [0, 2, 3]}

    for related_order in permutations(related):
        for invalidation_order in permutations(invalidation):
            replay = _parts(
                "dedupe_edges.resolve_edge",
                _edge_prompt(
                    [
                        _edge(index, fact)
                        for index, fact in enumerate(related_order)
                    ],
                    [
                        _edge(len(related) + index, fact)
                        for index, fact in enumerate(invalidation_order)
                    ],
                ),
            )
            oracle = CandidateAwareReplayCache(Cache([_record(capture, parsed)]))
            resolved = oracle.get(replay)
            assert resolved.parsed_response["duplicate_facts"] == [
                related_order.index("related-a"),
                related_order.index("related-b"),
            ]
            assert resolved.parsed_response["contradicted_facts"] == [
                related_order.index("related-a"),
                len(related) + invalidation_order.index("invalid-x"),
                len(related) + invalidation_order.index("invalid-y"),
            ]


@pytest.mark.parametrize(
    ("capture_candidates", "replay_candidates", "code"),
    [
        (
            [_node(0, "Alpha"), _node(1, "Beta")],
            [_node(0, "Alpha")],
            "CANDIDATE_MEMBERSHIP_DRIFT",
        ),
        (
            [_node(0, "Alpha"), _node(1, "Alpha"), _node(2, "Beta")],
            [_node(0, "Alpha"), _node(1, "Beta"), _node(2, "Alpha")],
            "AMBIGUOUS_CANDIDATE_IDENTITY",
        ),
        (
            [_node(1, "Alpha")],
            [_node(0, "Alpha")],
            "NONCONTIGUOUS_CANDIDATE_IDS",
        ),
    ],
)
def test_node_membership_identity_and_id_drift_fail_closed(
    capture_candidates: list[dict],
    replay_candidates: list[dict],
    code: str,
) -> None:
    capture = _parts(
        "dedupe_nodes.nodes",
        _node_prompt(capture_candidates),
    )
    replay = _parts(
        "dedupe_nodes.nodes",
        _node_prompt(replay_candidates),
    )
    oracle = CandidateAwareReplayCache(
        Cache([_record(capture, {"entity_resolutions": []})])
    )

    with pytest.raises(CandidateRemapError, match=code) as raised:
        oracle.get(replay)

    assert raised.value.code == code
    assert oracle.candidate_remap_rejection_count == 1
    assert all("Alpha" not in repr(value) for value in oracle.remap_diagnostics)


@pytest.mark.parametrize(
    ("parsed", "code"),
    [
        (
            {
                "entity_resolutions": [
                    {"id": 0, "name": "x", "duplicate_candidate_id": 9}
                ]
            },
            "CACHED_RESPONSE_INDEX_OUT_OF_RANGE",
        ),
        (
            {
                "entity_resolutions": [
                    {"id": 0, "name": "x", "duplicate_candidate_id": -2}
                ]
            },
            "CACHED_RESPONSE_INDEX_OUT_OF_RANGE",
        ),
        (
            {
                "entity_resolutions": [
                    {"id": 0, "name": "x", "duplicate_candidate_id": True}
                ]
            },
            "CACHED_RESPONSE_INDEX_TYPE",
        ),
    ],
)
def test_invalid_node_response_references_fail_closed(parsed: dict, code: str) -> None:
    capture = _parts(
        "dedupe_nodes.nodes",
        _node_prompt([_node(0, "Alpha"), _node(1, "Beta")]),
    )
    replay = _parts(
        "dedupe_nodes.nodes",
        _node_prompt([_node(0, "Beta"), _node(1, "Alpha")]),
    )
    oracle = CandidateAwareReplayCache(Cache([_record(capture, parsed)]))

    with pytest.raises(CandidateRemapError, match=code):
        oracle.get(replay)


@pytest.mark.parametrize(
    ("capture_related", "capture_invalidation", "replay_related", "replay_invalidation", "code"),
    [
        (
            [_edge(0, "a")],
            [_edge(1, "x")],
            [_edge(0, "a"), _edge(1, "x")],
            [],
            "CANDIDATE_PARTITION_DRIFT",
        ),
        (
            [_edge(0, "a"), _edge(1, "a"), _edge(2, "b")],
            [],
            [_edge(0, "a"), _edge(1, "b"), _edge(2, "a")],
            [],
            "AMBIGUOUS_CANDIDATE_IDENTITY",
        ),
        (
            [_edge(1, "a")],
            [],
            [_edge(0, "a")],
            [],
            "NONCONTIGUOUS_CANDIDATE_IDS",
        ),
    ],
)
def test_edge_partition_identity_and_id_drift_fail_closed(
    capture_related: list[dict],
    capture_invalidation: list[dict],
    replay_related: list[dict],
    replay_invalidation: list[dict],
    code: str,
) -> None:
    capture = _parts(
        "dedupe_edges.resolve_edge",
        _edge_prompt(capture_related, capture_invalidation),
    )
    replay = _parts(
        "dedupe_edges.resolve_edge",
        _edge_prompt(replay_related, replay_invalidation),
    )
    oracle = CandidateAwareReplayCache(
        Cache(
            [
                _record(
                    capture,
                    {"duplicate_facts": [], "contradicted_facts": []},
                )
            ]
        )
    )

    with pytest.raises(CandidateRemapError, match=code):
        oracle.get(replay)


@pytest.mark.parametrize(
    ("parsed", "code"),
    [
        (
            {"duplicate_facts": [2], "contradicted_facts": []},
            "CACHED_RESPONSE_WRONG_PARTITION",
        ),
        (
            {"duplicate_facts": [], "contradicted_facts": [3]},
            "CACHED_RESPONSE_INDEX_OUT_OF_RANGE",
        ),
        (
            {"duplicate_facts": [True], "contradicted_facts": []},
            "CACHED_RESPONSE_INDEX_TYPE",
        ),
    ],
)
def test_invalid_edge_response_references_fail_closed(parsed: dict, code: str) -> None:
    capture = _parts(
        "dedupe_edges.resolve_edge",
        _edge_prompt(
            [_edge(0, "a"), _edge(1, "b")],
            [_edge(2, "x")],
        ),
    )
    replay = _parts(
        "dedupe_edges.resolve_edge",
        _edge_prompt(
            [_edge(0, "b"), _edge(1, "a")],
            [_edge(2, "x")],
        ),
    )
    oracle = CandidateAwareReplayCache(Cache([_record(capture, parsed)]))

    with pytest.raises(CandidateRemapError, match=code):
        oracle.get(replay)


def test_non_candidate_drift_and_unsupported_prompts_remain_exact_only() -> None:
    capture = _parts(
        "dedupe_nodes.nodes",
        _node_prompt([_node(0, "Alpha"), _node(1, "Beta")], message="one"),
    )
    changed_message = _parts(
        "dedupe_nodes.nodes",
        _node_prompt([_node(0, "Beta"), _node(1, "Alpha")], message="two"),
    )
    unsupported = _parts("dedupe_nodes.node", "different")
    oracle = CandidateAwareReplayCache(
        Cache([_record(capture, {"entity_resolutions": []})])
    )

    assert oracle.get(changed_message) is None
    assert oracle.get(unsupported) is None
    assert oracle.candidate_remap_hit_count == 0
    assert oracle.candidate_remap_rejection_count == 0


def test_resolve_uses_candidate_translation_before_the_inner_miss_path() -> None:
    capture = _parts(
        "dedupe_nodes.nodes",
        _node_prompt([_node(0, "Alpha"), _node(1, "Beta")]),
    )
    replay = _parts(
        "dedupe_nodes.nodes",
        _node_prompt([_node(0, "Beta"), _node(1, "Alpha")]),
    )
    oracle = CandidateAwareReplayCache(
        Cache(
            [
                _record(
                    capture,
                    {
                        "entity_resolutions": [
                            {
                                "id": 0,
                                "name": "x",
                                "duplicate_candidate_id": 0,
                            }
                        ]
                    },
                )
            ]
        )
    )

    resolved = oracle.resolve(replay)

    assert resolved.parsed_response["entity_resolutions"][0][
        "duplicate_candidate_id"
    ] == 1


def test_multiple_semantic_cache_matches_fail_closed() -> None:
    capture_a = _parts(
        "dedupe_nodes.nodes",
        _node_prompt(
            [_node(0, "Alpha"), _node(1, "Beta"), _node(2, "Gamma")]
        ),
    )
    capture_b = _parts(
        "dedupe_nodes.nodes",
        _node_prompt(
            [_node(0, "Beta"), _node(1, "Gamma"), _node(2, "Alpha")]
        ),
    )
    replay = _parts(
        "dedupe_nodes.nodes",
        _node_prompt(
            [_node(0, "Gamma"), _node(1, "Alpha"), _node(2, "Beta")]
        ),
    )
    records = [
        _record(capture_a, {"entity_resolutions": []}, suffix="a"),
        _record(capture_b, {"entity_resolutions": []}, suffix="b"),
    ]
    oracle = CandidateAwareReplayCache(Cache(records))

    with pytest.raises(CandidateRemapError, match="SEMANTIC_CACHE_COLLISION"):
        oracle.get(replay)


def test_malformed_candidate_tags_fail_without_persisting_private_content() -> None:
    capture = _parts(
        "dedupe_nodes.nodes",
        _node_prompt([_node(0, "Alpha"), _node(1, "Beta")]),
    )
    malformed = _parts(
        "dedupe_nodes.nodes",
        "<EXISTING ENTITIES>\nprivate-alpha-without-closing-tag",
    )
    oracle = CandidateAwareReplayCache(
        Cache([_record(capture, {"entity_resolutions": []})])
    )

    with pytest.raises(CandidateRemapError, match="MALFORMED_CANDIDATE_PROMPT"):
        oracle.get(malformed)

    assert oracle.candidate_remap_rejection_count == 1
    assert "private-alpha" not in repr(oracle.remap_diagnostics)


@pytest.mark.parametrize(
    "malformed",
    [
        "<EXISTING ENTITIES>\n[]\n</EXISTING ENTITIES>\n<EXISTING ENTITIES>\n[]\n</EXISTING ENTITIES>",
        "</EXISTING ENTITIES>\n[]\n<EXISTING ENTITIES>",
        "<EXISTING ENTITIES>\n[NaN]\n</EXISTING ENTITIES>",
    ],
)
def test_malformed_or_nonstandard_node_payloads_fail_closed(malformed: str) -> None:
    capture = _parts(
        "dedupe_nodes.nodes",
        _node_prompt([_node(0, "Alpha"), _node(1, "Beta")]),
    )
    replay = _parts("dedupe_nodes.nodes", malformed)
    oracle = CandidateAwareReplayCache(
        Cache([_record(capture, {"entity_resolutions": []})])
    )

    with pytest.raises(CandidateRemapError, match="MALFORMED_CANDIDATE_PROMPT"):
        oracle.get(replay)
