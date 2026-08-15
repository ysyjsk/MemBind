"""Offline contracts for bilateral pre-prompt sidecar plumbing."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from pathlib import Path
from types import SimpleNamespace

import pytest

from paper_eval.artifacts import payload_sha256
from paper_eval.s4_candidate_oracle import CandidateAwareReplayCache
from paper_eval.s4_candidate_sidecar import (
    CaptureSidecarStore,
    CandidateSidecarError,
    ReplaySidecarBinder,
    current_replay_binding,
    replay_binding_sha256,
)
from paper_eval.s4_candidate_sidecar_runtime import (
    CandidateSidecarPromptCache,
    CandidateSidecarRuntimeError,
    activate_candidate_projection,
    current_candidate_projection,
    install_candidate_sidecar_hook,
)


def _sha(value: str) -> str:
    import hashlib

    return hashlib.sha256(value.encode()).hexdigest()


IDENTITY = {
    "attempt_id": "006",
    "cache_id": "s4-d0-sidecar-07741c45-20260815-006",
    "history_id": "07741c45",
    "episode_manifest_sha256": _sha("manifest"),
    "projection_schema_sha256": _sha("projection-schema"),
}


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
    parsed_response: dict
    prompt_parts: dict


class Cache:
    def __init__(self, *, read_only: bool = False) -> None:
        self.read_only = read_only
        self._records: dict[str, Record] = {}
        self.get_parts: list[Parts] = []
        self.put_parts: list[Parts] = []

    def get(self, parts: Parts) -> Record | None:
        self.get_parts.append(parts)
        return self._records.get(payload_sha256(asdict(parts)))

    def put(
        self,
        parts: Parts,
        *,
        raw_response: str,
        parsed_response: dict,
        token_usage: dict,
    ) -> Record:
        del raw_response, token_usage
        self.put_parts.append(parts)
        prompt_hash = payload_sha256(asdict(parts))
        record = Record(prompt_hash, parsed_response, asdict(parts))
        self._records[prompt_hash] = record
        return record


class ReplayCacheSpy:
    read_only = True

    def __init__(self) -> None:
        self.bindings: list[dict] = []
        self.parts: list[Parts] = []
        self.record = SimpleNamespace(parsed_response={"ok": True})

    def get(self, parts: Parts):
        binding = current_replay_binding()
        if binding is None:
            raise AssertionError("replay binding was not active at cache lookup")
        self.parts.append(parts)
        self.bindings.append(dict(binding))
        self.record = SimpleNamespace(
            parsed_response={"ok": True},
            sidecar_binding_sha256=replay_binding_sha256(binding),
            sidecar_logical_call_sha256=binding["logical_call_sha256"],
        )
        return self.record


def _parts(*, prompt_name: str = "dedupe_edges.resolve_edge") -> Parts:
    return Parts(
        model_revision="revision",
        decoding_config={"prompt_name": prompt_name},
        structured_output_schema={"type": "object"},
        system_prompt="system",
        user_prompt="private prompt",
    )


def _edge_parts() -> Parts:
    candidates = [
        {"idx": 0, "fact": "shared"},
        {"idx": 1, "fact": "shared"},
    ]
    return Parts(
        model_revision="revision",
        decoding_config={"prompt_name": "dedupe_edges.resolve_edge"},
        structured_output_schema={"type": "object"},
        system_prompt="system",
        user_prompt=(
            "<EXISTING FACTS>\n[]\n</EXISTING FACTS>\n"
            "<FACT INVALIDATION CANDIDATES>\n"
            f"{candidates!r}\n"
            "</FACT INVALIDATION CANDIDATES>\n"
            "<NEW FACT>\nnew\n</NEW FACT>"
        ),
    )


def _candidate(index: int, identity: str, *, fact: str) -> dict:
    return {
        "candidate_id": index,
        "fact_sha256": _sha(fact),
        "logical_identity_sha256": _sha(identity),
    }


def _projection(*, replay: bool = False) -> dict:
    candidates = (
        [
            _candidate(0, "right", fact="shared"),
            _candidate(1, "left", fact="shared"),
        ]
        if replay
        else [
            _candidate(0, "left", fact="shared"),
            _candidate(1, "right", fact="shared"),
        ]
    )
    return {
        "source_sequence": 7,
        "source_hash": _sha("source-7"),
        "logical_call_sha256": _sha("logical-call"),
        "related": [],
        "invalidation": candidates,
    }


def _capture_store(path: Path) -> CaptureSidecarStore:
    return CaptureSidecarStore.create(path, identity=IDENTITY)


def _sealed_binder(path: Path, capture_parts: Parts) -> ReplaySidecarBinder:
    store = _capture_store(path)
    cache = Cache()
    wrapper = CandidateSidecarPromptCache.capture(cache, store=store)
    with activate_candidate_projection(_projection()):
        wrapper.put(
            capture_parts,
            raw_response="private",
            parsed_response={"ok": True},
            token_usage={},
        )
    store.seal()
    return ReplaySidecarBinder(identity=IDENTITY, records=store.records)


def test_capture_prompt_cache_records_actual_prompt_hash_without_mutation(
    tmp_path: Path,
) -> None:
    parts = _parts()
    inner = Cache()
    store = _capture_store(tmp_path / "sidecar.jsonl")
    wrapper = CandidateSidecarPromptCache.capture(inner, store=store)

    with activate_candidate_projection(_projection()):
        record = wrapper.put(
            parts,
            raw_response="private",
            parsed_response={"answer": 1},
            token_usage={},
        )

    assert inner.put_parts == [parts]
    assert record.prompt_hash == payload_sha256(asdict(parts))
    assert store.records[0]["prompt_sha256"] == record.prompt_hash
    assert wrapper.capture_append_count == 1
    assert current_candidate_projection() is None


def test_capture_resume_repairs_or_reuses_exact_sidecar_record(
    tmp_path: Path,
) -> None:
    path = tmp_path / "sidecar.jsonl"
    parts = _parts()
    inner = Cache()
    first = CandidateSidecarPromptCache.capture(inner, store=_capture_store(path))
    with activate_candidate_projection(_projection()):
        first.put(
            parts,
            raw_response="private",
            parsed_response={"answer": 1},
            token_usage={},
        )

    resumed_store = CaptureSidecarStore.resume(path, identity=IDENTITY)
    resumed = CandidateSidecarPromptCache.capture(inner, store=resumed_store)
    with activate_candidate_projection(_projection()):
        assert resumed.get(parts) is not None

    assert len(resumed_store.records) == 1
    assert resumed.capture_reuse_count == 1


def test_edge_prompt_requires_projection_before_any_cache_side_effect(
    tmp_path: Path,
) -> None:
    parts = _parts()
    inner = Cache()
    wrapper = CandidateSidecarPromptCache.capture(
        inner, store=_capture_store(tmp_path / "capture.jsonl")
    )

    with pytest.raises(CandidateSidecarRuntimeError, match="projection"):
        wrapper.get(parts)
    with pytest.raises(CandidateSidecarRuntimeError, match="projection"):
        wrapper.put(
            parts,
            raw_response="private",
            parsed_response={"answer": 1},
            token_usage={},
        )
    assert inner.get_parts == []
    assert inner.put_parts == []

    binder = _sealed_binder(tmp_path / "replay.jsonl", parts)
    replay = CandidateSidecarPromptCache.replay(
        ReplayCacheSpy(), binder=binder
    )
    with activate_candidate_projection(_projection(replay=True)):
        with pytest.raises(CandidateSidecarRuntimeError, match="write"):
            replay.put(parts, parsed_response={})
    assert binder.consumed_count == 0


def test_same_process_duplicate_call_and_corrupt_capture_record_fail_closed(
    tmp_path: Path,
) -> None:
    parts = _parts()
    inner = Cache()
    wrapper = CandidateSidecarPromptCache.capture(
        inner, store=_capture_store(tmp_path / "sidecar.jsonl")
    )
    with activate_candidate_projection(_projection()):
        wrapper.put(
            parts,
            raw_response="private",
            parsed_response={"answer": 1},
            token_usage={},
        )
    with activate_candidate_projection(_projection()):
        with pytest.raises(CandidateSidecarRuntimeError, match="correlation"):
            wrapper.get(parts)

    corrupt = Record(
        prompt_hash=payload_sha256(asdict(parts)),
        parsed_response={"answer": 1},
        prompt_parts={**asdict(parts), "system_prompt": "changed"},
    )
    broken_inner = Cache()
    broken_inner._records[corrupt.prompt_hash] = corrupt
    broken = CandidateSidecarPromptCache.capture(
        broken_inner, store=_capture_store(tmp_path / "broken.jsonl")
    )
    with activate_candidate_projection(_projection()):
        with pytest.raises(CandidateSidecarRuntimeError, match="record"):
            broken.get(parts)


@pytest.mark.asyncio
async def test_non_edge_prompt_and_no_prompt_fast_path_do_not_touch_sidecar(
    tmp_path: Path,
) -> None:
    store = _capture_store(tmp_path / "sidecar.jsonl")
    cache = Cache()
    wrapper = CandidateSidecarPromptCache.capture(cache, store=store)
    other = _parts(prompt_name="extract_edges.extract_timestamps")
    with activate_candidate_projection(_projection()):
        wrapper.put(
            other,
            raw_response="private",
            parsed_response={"answer": 1},
            token_usage={},
        )
    assert store.records == []

    calls: list[str] = []

    async def original(*args, **kwargs):
        del args, kwargs
        calls.append("original")
        return "fast-path-result"

    async def projector(**kwargs):
        del kwargs
        return _projection()

    module = SimpleNamespace(resolve_extracted_edge=original)
    with install_candidate_sidecar_hook(module, projector=projector):
        assert await module.resolve_extracted_edge(
            object(), object(), [], [], object(), None
        ) == "fast-path-result"
    assert calls == ["original"]
    assert store.records == []
    assert current_candidate_projection() is None


def test_replay_cache_binds_once_only_during_target_prompt(tmp_path: Path) -> None:
    parts = _parts()
    binder = _sealed_binder(tmp_path / "sidecar.jsonl", parts)
    inner = ReplayCacheSpy()
    wrapper = CandidateSidecarPromptCache.replay(inner, binder=binder)

    with activate_candidate_projection(_projection(replay=True)):
        assert wrapper.get(parts) is inner.record

    assert binder.consumed_count == 1
    assert wrapper.replay_binding_count == 1
    assert inner.parts == [parts]
    assert inner.bindings[0]["invalidation_id_map"] == {0: 1, 1: 0}
    assert current_replay_binding() is None

    with activate_candidate_projection(_projection(replay=True)):
        with pytest.raises(CandidateSidecarError, match="CONSUMED"):
            wrapper.get(parts)


def test_replay_oracle_failure_rolls_back_prepared_binding(tmp_path: Path) -> None:
    parts = _parts()
    binder = _sealed_binder(tmp_path / "sidecar.jsonl", parts)

    class FailedReplayCache(ReplayCacheSpy):
        def get(self, parts):
            del parts
            assert current_replay_binding() is not None
            raise RuntimeError("oracle-stop")

    failed = CandidateSidecarPromptCache.replay(
        FailedReplayCache(), binder=binder
    )
    with activate_candidate_projection(_projection(replay=True)):
        with pytest.raises(RuntimeError, match="oracle-stop"):
            failed.get(parts)
    assert binder.consumed_count == 0

    recovered = CandidateSidecarPromptCache.replay(
        ReplayCacheSpy(), binder=binder
    )
    with activate_candidate_projection(_projection(replay=True)):
        assert recovered.get(parts) is not None
    assert binder.consumed_count == 1


def test_replay_cache_must_acknowledge_the_exact_binding(tmp_path: Path) -> None:
    parts = _parts()
    binder = _sealed_binder(tmp_path / "sidecar.jsonl", parts)

    class IgnoringReplayCache:
        read_only = True

        def get(self, parts):
            del parts
            return SimpleNamespace(parsed_response={"unsafe": True})

    wrapper = CandidateSidecarPromptCache.replay(
        IgnoringReplayCache(), binder=binder
    )
    with activate_candidate_projection(_projection(replay=True)):
        with pytest.raises(CandidateSidecarRuntimeError, match="acknowledgement"):
            wrapper.get(parts)
    assert binder.consumed_count == 0


def test_full_replay_cache_chain_remaps_exact_hidden_identity_swap(
    tmp_path: Path,
) -> None:
    parts = _edge_parts()
    store = _capture_store(tmp_path / "sidecar.jsonl")
    prompt_cache = Cache()
    capture = CandidateSidecarPromptCache.capture(prompt_cache, store=store)
    with activate_candidate_projection(_projection()):
        persisted = capture.put(
            parts,
            raw_response="private",
            parsed_response={
                "duplicate_facts": [],
                "contradicted_facts": [0],
            },
            token_usage={},
        )
    store.seal()
    prompt_cache.read_only = True
    oracle = CandidateAwareReplayCache(prompt_cache)
    binder = ReplaySidecarBinder(identity=IDENTITY, records=store.records)
    replay = CandidateSidecarPromptCache.replay(oracle, binder=binder)

    with activate_candidate_projection(_projection(replay=True)):
        selected = replay.get(parts)

    assert selected.parsed_response == {
        "duplicate_facts": [],
        "contradicted_facts": [1],
    }
    assert persisted.parsed_response["contradicted_facts"] == [0]
    assert binder.consumed_count == 1
    assert oracle.sidecar_exact_hit_count == 1


@pytest.mark.asyncio
async def test_hook_preserves_arguments_order_result_and_context_on_success() -> None:
    llm = object()
    extracted = object()
    related = [object(), object()]
    invalidation = [object()]
    episode = object()
    edge_types = {"type": object()}
    observed: list[tuple] = []

    async def projector(**kwargs):
        assert kwargs == {
            "extracted_edge": extracted,
            "related_edges": related,
            "invalidation_edges": invalidation,
            "episode": episode,
        }
        return _projection()

    async def original(*args, **kwargs):
        observed.append((args, kwargs, current_candidate_projection()))
        return ("resolved", "invalidated", "duplicates")

    module = SimpleNamespace(resolve_extracted_edge=original)
    with install_candidate_sidecar_hook(module, projector=projector):
        result = await module.resolve_extracted_edge(
            llm,
            extracted,
            related,
            invalidation,
            episode,
            edge_types,
        )

    assert result == ("resolved", "invalidated", "duplicates")
    assert observed[0][0] == (
        llm,
        extracted,
        related,
        invalidation,
        episode,
        edge_types,
    )
    assert observed[0][1] == {}
    assert observed[0][2] == _projection()
    assert module.resolve_extracted_edge is original
    assert current_candidate_projection() is None


@pytest.mark.asyncio
async def test_hook_restores_after_original_or_projector_failure() -> None:
    async def original(*args, **kwargs):
        del args, kwargs
        raise RuntimeError("original-stop")

    async def projector(**kwargs):
        del kwargs
        return _projection()

    module = SimpleNamespace(resolve_extracted_edge=original)
    with install_candidate_sidecar_hook(module, projector=projector):
        with pytest.raises(RuntimeError, match="original-stop"):
            await module.resolve_extracted_edge(object(), object(), [], [], object())
    assert current_candidate_projection() is None

    original_calls = 0

    async def counted_original(*args, **kwargs):
        nonlocal original_calls
        del args, kwargs
        original_calls += 1

    async def failed_projector(**kwargs):
        del kwargs
        raise CandidateSidecarRuntimeError("projection-stop")

    module = SimpleNamespace(resolve_extracted_edge=counted_original)
    with install_candidate_sidecar_hook(module, projector=failed_projector):
        with pytest.raises(CandidateSidecarRuntimeError, match="projection-stop"):
            await module.resolve_extracted_edge(object(), object(), [], [], object())
    assert original_calls == 0
    assert current_candidate_projection() is None


def test_projection_context_copies_input_and_rejects_malformed_shape() -> None:
    projection = _projection()
    with activate_candidate_projection(projection):
        projection["invalidation"].reverse()
        assert current_candidate_projection() == _projection()
    assert current_candidate_projection() is None

    with pytest.raises(CandidateSidecarRuntimeError, match="projection"):
        with activate_candidate_projection({"source_sequence": 7}):
            pass
