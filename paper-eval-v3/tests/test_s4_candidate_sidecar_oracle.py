"""Sidecar-aware edge translation tests for the existing S4 replay cache."""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass

import pytest

from paper_eval.s4_candidate_oracle import (
    CandidateAwareReplayCache,
    CandidateRemapError,
)
from paper_eval.s4_candidate_sidecar import (
    ReplaySidecarBinder,
    activate_replay_binding,
    build_candidate_call_record,
    replay_binding_sha256,
)
from paper_eval.artifacts import payload_sha256


def _sha(value: str) -> str:
    import hashlib

    return hashlib.sha256(value.encode()).hexdigest()


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
    read_only = True

    def __init__(self, record: Record) -> None:
        self._records = {record.prompt_hash: record}

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

    def resolve(self, *args, **kwargs):
        return "miss"


def _parts(prompt: str) -> Parts:
    return Parts(
        model_revision="revision",
        decoding_config={"prompt_name": "dedupe_edges.resolve_edge"},
        structured_output_schema={"type": "object"},
        system_prompt="system",
        user_prompt=prompt,
    )


def _prompt(facts: list[str], *, json_candidates: bool = False) -> str:
    candidates = [{"idx": index, "fact": fact} for index, fact in enumerate(facts)]
    rendered = json.dumps(candidates) if json_candidates else repr(candidates)
    return (
        "<EXISTING FACTS>\n[]\n</EXISTING FACTS>\n"
        "<FACT INVALIDATION CANDIDATES>\n"
        f"{rendered}\n"
        "</FACT INVALIDATION CANDIDATES>\n"
        "<NEW FACT>\nnew\n</NEW FACT>"
    )


def _candidate(index: int, identity: str, *, fact: str = "shared") -> dict:
    return {
        "candidate_id": index,
        "fact_sha256": _sha(fact),
        "logical_identity_sha256": _sha(identity),
    }


def _binding(
    *,
    capture_prompt_sha256: str,
    capture_candidates: list[dict] | None = None,
    replay_candidates: list[dict] | None = None,
):
    capture_candidates = capture_candidates or [
        _candidate(0, "left"),
        _candidate(1, "right"),
    ]
    replay_candidates = replay_candidates or [
        _candidate(0, "right"),
        _candidate(1, "left"),
    ]
    capture = build_candidate_call_record(
        source_sequence=7,
        source_hash=_sha("source-7"),
        logical_call_sha256=_sha("call"),
        prompt_sha256=capture_prompt_sha256,
        related=[],
        invalidation=capture_candidates,
    )
    binder = ReplaySidecarBinder(
        identity={
            "attempt_id": "006",
            "cache_id": "s4-d0-sidecar-07741c45-20260815-006",
            "history_id": "07741c45",
            "episode_manifest_sha256": _sha("manifest"),
            "projection_schema_sha256": _sha("projection"),
        },
        records=[capture],
    )
    return binder.bind(
        source_sequence=7,
        source_hash=_sha("source-7"),
        logical_call_sha256=_sha("call"),
        related=[],
        invalidation=replay_candidates,
    )


def _oracle(
    *,
    capture_facts: list[str] | None = None,
    contradicted_facts: list[int] | None = None,
):
    capture = _parts(_prompt(capture_facts or ["shared", "shared"]))
    capture_hash = payload_sha256(asdict(capture))
    record = Record(
        prompt_hash=capture_hash,
        raw_response="private",
        parsed_response={
            "duplicate_facts": [],
            "contradicted_facts": contradicted_facts or [0],
        },
        token_usage={},
        prompt_parts=asdict(capture),
    )
    return CandidateAwareReplayCache(Cache(record)), capture_hash, record


def test_sidecar_resolves_duplicate_fact_permutation_without_cache_mutation() -> None:
    oracle, capture_hash, record = _oracle(
        capture_facts=["shared", "shared", "other"]
    )
    replay = _parts(_prompt(["other", "shared", "shared"]))
    binding = _binding(
        capture_prompt_sha256=capture_hash,
        capture_candidates=[
            _candidate(0, "left"),
            _candidate(1, "right"),
            _candidate(2, "other", fact="other"),
        ],
        replay_candidates=[
            _candidate(0, "other", fact="other"),
            _candidate(1, "right"),
            _candidate(2, "left"),
        ],
    )

    with activate_replay_binding(binding):
        remapped = oracle.get(replay)

    assert remapped.parsed_response == {
        "duplicate_facts": [],
        "contradicted_facts": [2],
    }
    assert record.parsed_response == {
        "duplicate_facts": [],
        "contradicted_facts": [0],
    }
    assert oracle.sidecar_remap_hit_count == 1
    assert oracle.candidate_remap_rejection_count == 0
    assert remapped.sidecar_binding_sha256 == replay_binding_sha256(binding)
    assert remapped.sidecar_logical_call_sha256 == binding["logical_call_sha256"]


def test_sidecar_exact_hit_remaps_hidden_identity_order_without_cache_mutation() -> None:
    oracle, capture_hash, record = _oracle()
    capture_parts = Parts(**record.prompt_parts)
    binding = _binding(capture_prompt_sha256=capture_hash)

    with activate_replay_binding(binding):
        selected = oracle.get(capture_parts)

    assert selected is not record
    assert selected.parsed_response == {
        "duplicate_facts": [],
        "contradicted_facts": [1],
    }
    assert record.parsed_response == {
        "duplicate_facts": [],
        "contradicted_facts": [0],
    }
    assert oracle.sidecar_exact_hit_count == 1
    assert oracle.sidecar_remap_hit_count == 1


def test_sidecar_prompt_hash_or_visible_fact_drift_fails_closed() -> None:
    oracle, capture_hash, _ = _oracle()
    replay = _parts(_prompt(["shared", "shared"]))

    with activate_replay_binding(_binding(capture_prompt_sha256=_sha("wrong"))):
        with pytest.raises(CandidateRemapError, match="SIDECAR_CAPTURE_PROMPT"):
            oracle.get(replay)

    oracle, capture_hash, _ = _oracle()
    drifted = _parts(_prompt(["shared", "different"], json_candidates=True))
    with activate_replay_binding(_binding(capture_prompt_sha256=capture_hash)):
        with pytest.raises(CandidateRemapError, match="SIDECAR_PROMPT_PROJECTION"):
            oracle.get(drifted)


def test_sidecar_recomputes_capture_hash_and_rejects_joint_forgery() -> None:
    oracle, _, record = _oracle()
    record = Record(**{**asdict(record), "prompt_hash": _sha("forged")})
    oracle = CandidateAwareReplayCache(Cache(record))

    with activate_replay_binding(
        _binding(capture_prompt_sha256=record.prompt_hash)
    ):
        with pytest.raises(CandidateRemapError, match="SIDECAR_CAPTURE_PROMPT"):
            oracle.get(Parts(**record.prompt_parts))


def test_no_sidecar_keeps_existing_ambiguous_identity_rejection() -> None:
    oracle, _, record = _oracle()
    replay = _parts(_prompt(["shared", "shared"], json_candidates=True))

    with pytest.raises(CandidateRemapError, match="AMBIGUOUS_CANDIDATE_IDENTITY"):
        oracle.get(replay)

    assert oracle.sidecar_remap_hit_count == 0
    assert record.parsed_response["contradicted_facts"] == [0]


def test_sidecar_binding_does_not_leak_after_context_exit() -> None:
    oracle, capture_hash, _ = _oracle()
    replay = _parts(_prompt(["shared", "shared"], json_candidates=True))

    with activate_replay_binding(_binding(capture_prompt_sha256=capture_hash)):
        assert oracle.get(replay) is not None

    with pytest.raises(CandidateRemapError, match="AMBIGUOUS_CANDIDATE_IDENTITY"):
        oracle.get(replay)


def test_malformed_sidecar_binding_fails_closed() -> None:
    oracle, _, record = _oracle()

    with activate_replay_binding({"capture_prompt_sha256": record.prompt_hash}):
        with pytest.raises(CandidateRemapError, match="SIDECAR_BINDING_MALFORMED"):
            oracle.get(Parts(**record.prompt_parts))


def test_sidecar_non_candidate_prompt_or_id_map_drift_fails_closed() -> None:
    oracle, capture_hash, record = _oracle()
    replay = _parts(_prompt(["shared", "shared"]))
    replay = Parts(
        **{
            **asdict(replay),
            "user_prompt": replay.user_prompt.replace("\nnew\n", "\nchanged\n"),
        }
    )
    with activate_replay_binding(_binding(capture_prompt_sha256=capture_hash)):
        with pytest.raises(CandidateRemapError, match="SIDECAR_NON_CANDIDATE"):
            oracle.get(replay)

    binding = dict(_binding(capture_prompt_sha256=capture_hash))
    binding["invalidation_id_map"] = {0: 0, 1: 1}
    with activate_replay_binding(binding):
        with pytest.raises(CandidateRemapError, match="SIDECAR_BINDING"):
            oracle.get(Parts(**record.prompt_parts))
