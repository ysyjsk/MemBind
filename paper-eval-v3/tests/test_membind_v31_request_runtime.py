"""Async transport-level tests for v3.1 request admission."""

from __future__ import annotations

import asyncio
import hashlib
import json
from types import SimpleNamespace

import pytest

from paper_eval.membind_v31 import AdmissionPolicy, RequestKind
from paper_eval.membind_v31.request_runtime import (
    AdmittedChatCompletionsV31,
    AdmittedLLMClientV31,
    MemBindV31RequestRuntimeError,
    llm_request_scope,
)
from paper_eval.membind_v31.prefix_affinity import PrefixMetadata


class _ControlledLLM:
    def __init__(self) -> None:
        self.started: list[str] = []
        self.active = 0
        self.max_active = 0
        self.release: dict[str, asyncio.Event] = {}

    async def generate_response(self, *_args, **kwargs):
        prompt_name = str(kwargs["prompt_name"])
        self.started.append(prompt_name)
        self.active += 1
        self.max_active = max(self.max_active, self.active)
        event = self.release.setdefault(prompt_name, asyncio.Event())
        await event.wait()
        self.active -= 1
        return {"ok": prompt_name}


def _tokenizer(*args, **kwargs):
    prompt_name = str(kwargs["prompt_name"])
    groups = {
        "warm-a": [1, 2, 3, 4, 5, 6, 7, 8],
        "near-a": [1, 2, 3, 4, 5, 6, 7, 9],
        "far-b": [9, 9, 9, 9, 8, 8, 8, 8],
    }
    tokens = groups.get(prompt_name, [ord(char) for char in prompt_name])
    return PrefixMetadata.from_token_ids(
        tokens,
        prefix_match_unit=4,
        tokenizer_identity_sha256="e" * 64,
        cache_identity_sha256="8" * 64,
        trace_hmac_key=b"e" * 32,
    )


async def _request(client, kind, sequence, prompt_name):
    with llm_request_scope(
        kind=kind,
        stream_id="history-a",
        source_sequence=sequence,
    ):
        return await client.generate_response(
            [{"role": "user", "content": "private prompt"}],
            prompt_name=prompt_name,
        )


def test_transport_gate_is_nonpreemptive_and_frontier_first_at_next_permit() -> None:
    async def scenario() -> None:
        inner = _ControlledLLM()
        client = AdmittedLLMClientV31(
            inner=inner,
            limit=1,
            policy=AdmissionPolicy.FIFO,
            request_id_prefix="v31-test",
            prefix_encoder=_tokenizer,
        )
        first = asyncio.create_task(
            _request(client, RequestKind.COMPILE, 1, "compile-first")
        )
        await asyncio.sleep(0)
        frontier = asyncio.create_task(
            _request(client, RequestKind.FRONTIER, 0, "frontier-next")
        )
        compile_next = asyncio.create_task(
            _request(client, RequestKind.COMPILE, 2, "compile-next")
        )
        await asyncio.sleep(0)

        assert inner.started == ["compile-first"]
        inner.release["compile-first"].set()
        await asyncio.sleep(0)
        await asyncio.sleep(0)
        assert inner.started == ["compile-first", "frontier-next"]
        inner.release["frontier-next"].set()
        await asyncio.sleep(0)
        await asyncio.sleep(0)
        assert inner.started == ["compile-first", "frontier-next", "compile-next"]
        inner.release["compile-next"].set()
        await asyncio.gather(first, frontier, compile_next)
        assert client.observation()["observed_max_inflight"] == 1

    asyncio.run(scenario())


def test_transport_wrapper_admits_each_actual_attempt_and_fails_unscoped() -> None:
    class Transport:
        def __init__(self) -> None:
            self.calls = 0

        async def create(self, **kwargs):
            self.calls += 1
            return {"messages": len(kwargs["messages"])}

    async def scenario() -> None:
        inner = _ControlledLLM()
        gate = AdmittedLLMClientV31(
            inner=inner,
            limit=1,
            policy=AdmissionPolicy.FIFO,
            request_id_prefix="transport-test",
            prefix_encoder=_tokenizer,
        )
        transport = Transport()
        wrapped = AdmittedChatCompletionsV31(inner=transport, admission=gate)
        with pytest.raises(MemBindV31RequestRuntimeError, match="llm_request_scope_missing"):
            await wrapped.create(messages=[], prompt_name="hidden")
        with llm_request_scope(
            kind=RequestKind.FRONTIER,
            stream_id="history-a",
            source_sequence=0,
        ):
            assert await wrapped.create(
                messages=[{"role": "user", "content": "private prompt"}],
                prompt_name="warm-a",
            ) == {"messages": 1}
            assert await wrapped.create(
                messages=[{"role": "user", "content": "private prompt"}],
                prompt_name="near-a",
            ) == {"messages": 1}
        assert transport.calls == 2
        assert gate.observation()["completed_count"] == 2

    asyncio.run(scenario())


def _response_format() -> dict[str, object]:
    return {
        "type": "json_schema",
        "json_schema": {
            "name": "tiny_result",
            "strict": True,
            "schema": {
                "type": "object",
                "properties": {"n": {"type": "integer"}},
                "required": ["n"],
                "additionalProperties": False,
            },
        },
    }


def _canonical_sha256(value: object) -> str:
    return hashlib.sha256(
        json.dumps(
            value,
            ensure_ascii=True,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()


@pytest.mark.parametrize(
    ("content", "finish_reason", "completion_tokens"),
    [
        ('{"n":1}', "stop", 7),
        ('{"n":"private truncated completion', "length", 16_384),
    ],
)
def test_transport_response_telemetry_captures_envelope_without_content(
    content: str,
    finish_reason: str,
    completion_tokens: int,
) -> None:
    class Transport:
        async def create(self, **_kwargs):
            return SimpleNamespace(
                choices=[
                    SimpleNamespace(
                        finish_reason=finish_reason,
                        message=SimpleNamespace(content=content),
                    )
                ],
                usage=SimpleNamespace(
                    prompt_tokens=25_243,
                    completion_tokens=completion_tokens,
                    total_tokens=25_243 + completion_tokens,
                ),
            )

    async def scenario() -> None:
        events: list[dict[str, object]] = []
        gate = AdmittedLLMClientV31(
            inner=_ControlledLLM(),
            limit=1,
            policy=AdmissionPolicy.FIFO,
            request_id_prefix="response-telemetry",
            prefix_encoder=_tokenizer,
        )
        wrapped = AdmittedChatCompletionsV31(
            inner=Transport(),
            admission=gate,
            response_observer=events.append,
            structured_backend_identity="xgrammar",
        )
        response_format = _response_format()
        with llm_request_scope(
            kind=RequestKind.FRONTIER,
            stream_id="history-a",
            source_sequence=31,
        ):
            selected = await wrapped.create(
                messages=[{"role": "user", "content": "private prompt"}],
                prompt_name="warm-a",
                max_tokens=16_384,
                response_format=response_format,
            )

        assert selected.choices[0].message.content == content
        assert len(events) == 1
        event = events[0]
        assert event == {
            "schema_version": "membind.paper-eval-v3.transport-response.v1",
            "event_type": "llm_transport_response",
            "transport_attempt_index": 0,
            "retry_index": None,
            "request_kind": "FRONTIER",
            "stream_id": "history-a",
            "source_sequence": 31,
            "requested_max_tokens": 16_384,
            "effective_max_tokens": 16_384,
            "response_format_sha256": _canonical_sha256(response_format),
            "json_schema_sha256": _canonical_sha256(
                response_format["json_schema"]["schema"]
            ),
            "response_byte_length": len(content.encode("utf-8")),
            "response_sha256": hashlib.sha256(content.encode("utf-8")).hexdigest(),
            "finish_reason": finish_reason,
            "prompt_tokens": 25_243,
            "completion_tokens": completion_tokens,
            "total_tokens": 25_243 + completion_tokens,
            "structured_backend_identity": "xgrammar",
        }
        assert "private prompt" not in repr(events)
        assert content not in repr(events)
        assert wrapped.public_response_events == (event,)

    asyncio.run(scenario())


def test_actual_transport_attempts_share_one_global_k_limit() -> None:
    class Transport:
        def __init__(self) -> None:
            self.started: list[str] = []
            self.active = 0
            self.max_active = 0
            self.release: dict[str, asyncio.Event] = {}

        async def create(self, **kwargs):
            name = str(kwargs["prompt_name"])
            self.started.append(name)
            self.active += 1
            self.max_active = max(self.max_active, self.active)
            await self.release.setdefault(name, asyncio.Event()).wait()
            self.active -= 1
            return {"ok": name}

    async def invoke(transport, index):
        with llm_request_scope(
            kind=RequestKind.COMPILE,
            stream_id="history-a",
            source_sequence=index,
        ):
            return await transport.create(
                messages=[{"role": "user", "content": "private prompt"}],
                prompt_name=f"compile-{index}",
            )

    async def scenario() -> None:
        gate = AdmittedLLMClientV31(
            inner=_ControlledLLM(),
            limit=2,
            policy=AdmissionPolicy.FIFO,
            request_id_prefix="actual-transport-k",
            prefix_encoder=_tokenizer,
        )
        inner = Transport()
        transport = AdmittedChatCompletionsV31(inner=inner, admission=gate)
        tasks = [asyncio.create_task(invoke(transport, index)) for index in range(3)]
        await asyncio.sleep(0)
        await asyncio.sleep(0)
        assert inner.max_active == 2
        assert len(inner.started) == 2
        for name in tuple(inner.started):
            inner.release[name].set()
        await asyncio.sleep(0)
        await asyncio.sleep(0)
        assert len(inner.started) == 3
        inner.release[inner.started[-1]].set()
        await asyncio.gather(*tasks)
        assert gate.observation()["observed_max_inflight"] == 2

    asyncio.run(scenario())


def test_barrier_blocks_new_compile_during_non_llm_frontier_bind_region() -> None:
    async def scenario() -> None:
        inner = _ControlledLLM()
        client = AdmittedLLMClientV31(
            inner=inner,
            limit=2,
            policy=AdmissionPolicy.BARRIER,
            request_id_prefix="barrier-test",
            prefix_encoder=_tokenizer,
        )
        async with client.frontier_bind_region("history-a", 0):
            task = asyncio.create_task(
                _request(client, RequestKind.COMPILE, 1, "compile-blocked")
            )
            await asyncio.sleep(0)
            await asyncio.sleep(0)
            assert inner.started == []

        await asyncio.sleep(0)
        await asyncio.sleep(0)
        assert inner.started == ["compile-blocked"]
        inner.release["compile-blocked"].set()
        await task

    asyncio.run(scenario())


def test_one_frontier_bind_region_allows_multiple_nested_graphiti_attempts() -> None:
    """A logical bind may issue several actual LLM calls, still bounded by K."""

    async def scenario() -> None:
        inner = _ControlledLLM()
        client = AdmittedLLMClientV31(
            inner=inner,
            limit=2,
            policy=AdmissionPolicy.CACHE_AFFINE,
            request_id_prefix="nested-frontier",
            prefix_encoder=_tokenizer,
        )
        async with client.frontier_bind_region("history-a", 0):
            first = asyncio.create_task(
                _request(client, RequestKind.FRONTIER, 0, "frontier-child-a")
            )
            second = asyncio.create_task(
                _request(client, RequestKind.FRONTIER, 0, "frontier-child-b")
            )
            await asyncio.sleep(0)
            await asyncio.sleep(0)
            assert inner.started == ["frontier-child-a", "frontier-child-b"]
            assert inner.max_active == 2
            inner.release["frontier-child-a"].set()
            inner.release["frontier-child-b"].set()
            await asyncio.gather(first, second)

        assert client.observation()["observed_max_inflight"] == 2

    asyncio.run(scenario())


def test_transport_gate_enforces_k_and_emits_content_safe_events() -> None:
    async def scenario() -> None:
        inner = _ControlledLLM()
        events: list[dict[str, object]] = []
        client = AdmittedLLMClientV31(
            inner=inner,
            limit=2,
            policy=AdmissionPolicy.CACHE_AFFINE,
            request_id_prefix="content-safe",
            observer=events.append,
            prefix_encoder=_tokenizer,
        )
        tasks = [
            asyncio.create_task(
                _request(client, RequestKind.COMPILE, index, f"compile-{index}")
            )
            for index in range(3)
        ]
        await asyncio.sleep(0)
        await asyncio.sleep(0)
        assert len(inner.started) == 2
        assert inner.max_active == 2
        for prompt_name in list(inner.started):
            inner.release[prompt_name].set()
        await asyncio.sleep(0)
        await asyncio.sleep(0)
        inner.release["compile-2"].set()
        await asyncio.gather(*tasks)

        assert client.observation()["observed_max_inflight"] == 2
        assert "private prompt" not in repr(events)
        assert all("prompt" not in key for event in events for key in event)

    asyncio.run(scenario())


def test_optional_admission_snapshots_expose_waiting_and_active_by_kind() -> None:
    async def scenario() -> None:
        inner = _ControlledLLM()
        snapshots: list[dict[str, object]] = []
        client = AdmittedLLMClientV31(
            inner=inner,
            limit=1,
            policy=AdmissionPolicy.FIFO,
            request_id_prefix="admission-snapshot",
            prefix_encoder=_tokenizer,
            admission_observer=snapshots.append,
        )
        first = asyncio.create_task(_request(client, RequestKind.COMPILE, 0, "first"))
        await asyncio.sleep(0)
        second = asyncio.create_task(_request(client, RequestKind.FRONTIER, 1, "second"))
        await asyncio.sleep(0)
        waiting = [
            event
            for event in snapshots
            if event["waiting_frontier_count"] == 1
        ]
        assert waiting
        assert waiting[-1]["active_compile_count"] == 1
        assert waiting[-1]["configured_limit"] == 1
        assert waiting[-1]["frontier_bind_region_count"] == 0
        inner.release["first"].set()
        await asyncio.sleep(0)
        await asyncio.sleep(0)
        assert any(
            event["active_frontier_count"] == 1
            for event in snapshots
        )
        inner.release["second"].set()
        await asyncio.gather(first, second)
        assert all("private" not in repr(event) for event in snapshots)
        assert [event["event_sequence"] for event in snapshots] == list(range(len(snapshots)))

    asyncio.run(scenario())


def test_public_request_events_receive_nonnegative_monotonic_timestamps() -> None:
    async def scenario() -> None:
        timestamps = iter(range(100, 200))
        inner = _ControlledLLM()
        client = AdmittedLLMClientV31(
            inner=inner,
            limit=1,
            policy=AdmissionPolicy.FIFO,
            request_id_prefix="timestamp-test",
            prefix_encoder=_tokenizer,
            clock_ns=lambda: next(timestamps),
        )
        task = asyncio.create_task(
            _request(client, RequestKind.COMPILE, 0, "timestamped")
        )
        await asyncio.sleep(0)
        inner.release["timestamped"].set()
        await task

        observed = [int(event["timestamp_ns"]) for event in client.public_events]
        assert observed
        assert observed == sorted(observed)
        assert min(observed) >= 0

    asyncio.run(scenario())


def test_transport_call_without_declared_scope_is_hidden_fallback() -> None:
    inner = _ControlledLLM()
    client = AdmittedLLMClientV31(
        inner=inner,
        limit=2,
        policy=AdmissionPolicy.FIFO,
        request_id_prefix="scope-test",
        prefix_encoder=_tokenizer,
    )

    with pytest.raises(MemBindV31RequestRuntimeError, match="llm_request_scope_missing"):
        asyncio.run(client.generate_response([], prompt_name="hidden"))
    assert inner.started == []


def test_cache_affinity_uses_completed_token_prefix_not_prompt_name() -> None:
    async def scenario() -> None:
        inner = _ControlledLLM()
        client = AdmittedLLMClientV31(
            inner=inner,
            limit=1,
            policy=AdmissionPolicy.CACHE_AFFINE,
            request_id_prefix="prefix-test",
            prefix_encoder=_tokenizer,
        )
        warm = asyncio.create_task(_request(client, RequestKind.COMPILE, 0, "warm-a"))
        await asyncio.sleep(0)
        inner.release["warm-a"].set()
        await warm

        # Submit the unrelated request first. Both remain queued behind a frontier
        # request, then the completed-provider LCP must select near-a first.
        frontier = asyncio.create_task(_request(client, RequestKind.FRONTIER, 1, "frontier"))
        await asyncio.sleep(0)
        far = asyncio.create_task(_request(client, RequestKind.COMPILE, 2, "far-b"))
        near = asyncio.create_task(_request(client, RequestKind.COMPILE, 3, "near-a"))
        await asyncio.sleep(0)
        assert inner.started[-1] == "frontier"
        inner.release["frontier"].set()
        await asyncio.sleep(0)
        await asyncio.sleep(0)
        assert inner.started[-1] == "near-a"
        inner.release["near-a"].set()
        await asyncio.sleep(0)
        await asyncio.sleep(0)
        assert inner.started[-1] == "far-b"
        inner.release["far-b"].set()
        await asyncio.gather(frontier, far, near)

        submitted = [
            event for event in client.public_events if event["event_type"] == "llm_request_submitted"
        ]
        assert all("token_sequence_hmac_sha256" in event for event in submitted)
        assert all("token_prefix_block_hmac_sha256s" in event for event in submitted)
        assert "private prompt" not in repr(submitted)

    asyncio.run(scenario())


def test_admission_snapshots_report_active_and_waiting_counts_by_kind() -> None:
    async def scenario() -> None:
        inner = _ControlledLLM()
        events: list[dict[str, object]] = []
        client = AdmittedLLMClientV31(
            inner=inner,
            limit=1,
            policy=AdmissionPolicy.FIFO,
            request_id_prefix="admission-snapshot",
            observer=events.append,
            admission_observer=events.append,
            prefix_encoder=_tokenizer,
        )
        compile_task = asyncio.create_task(
            _request(client, RequestKind.COMPILE, 1, "compile-active")
        )
        await asyncio.sleep(0)
        frontier_task = asyncio.create_task(
            _request(client, RequestKind.FRONTIER, 0, "frontier-waiting")
        )
        await asyncio.sleep(0)

        snapshots = [
            event for event in events if event["event_type"] == "admission_snapshot"
        ]
        assert any(
            snapshot["active_compile_count"] == 1
            and snapshot["waiting_frontier_count"] == 1
            and snapshot["active_frontier_count"] == 0
            for snapshot in snapshots
        )

        inner.release["compile-active"].set()
        await asyncio.sleep(0)
        await asyncio.sleep(0)
        snapshots = [
            event for event in events if event["event_type"] == "admission_snapshot"
        ]
        assert any(
            snapshot["active_compile_count"] == 0
            and snapshot["active_frontier_count"] == 1
            and snapshot["waiting_frontier_count"] == 0
            for snapshot in snapshots
        )
        inner.release["frontier-waiting"].set()
        await asyncio.gather(compile_task, frontier_task)

        snapshots = [
            event for event in events if event["event_type"] == "admission_snapshot"
        ]
        assert snapshots[-1]["active_count"] == 0
        assert snapshots[-1]["waiting_count"] == 0
        assert [snapshot["event_sequence"] for snapshot in snapshots] == sorted(
            snapshot["event_sequence"] for snapshot in snapshots
        )
        assert "private" not in repr(snapshots)

    asyncio.run(scenario())


def test_barrier_snapshot_distinguishes_local_frontier_region_from_llm_wait() -> None:
    async def scenario() -> None:
        inner = _ControlledLLM()
        events: list[dict[str, object]] = []
        client = AdmittedLLMClientV31(
            inner=inner,
            limit=1,
            policy=AdmissionPolicy.BARRIER,
            request_id_prefix="barrier-snapshot",
            observer=events.append,
            admission_observer=events.append,
            prefix_encoder=_tokenizer,
        )
        async with client.frontier_bind_region("history-a", 0):
            region_snapshot = [
                event for event in events if event["event_type"] == "admission_snapshot"
            ][-1]
            assert region_snapshot["frontier_transport_phase"] == (
                "FRONTIER_LOCAL_OR_UNINSTRUMENTED"
            )
            assert region_snapshot["barrier_holds"] is True

            compile_task = asyncio.create_task(
                _request(client, RequestKind.COMPILE, 1, "compile-blocked")
            )
            await asyncio.sleep(0)
            await asyncio.sleep(0)
            blocked = [
                event for event in events if event["event_type"] == "admission_snapshot"
            ][-1]
            assert blocked["waiting_compile_count"] == 1
            assert blocked["active_compile_count"] == 0
            assert blocked["frontier_transport_phase"] == (
                "FRONTIER_LOCAL_OR_UNINSTRUMENTED"
            )

        await asyncio.sleep(0)
        await asyncio.sleep(0)
        inner.release["compile-blocked"].set()
        await compile_task

    asyncio.run(scenario())


def _causal_metadata() -> dict[str, object]:
    return {
        "operator_role": "graphiti.resolve_extracted_nodes",
        "operator_id": "07741c45:3:FRONTIER:resolve_extracted_nodes:0",
        "parent_bind_id": "07741c45:3:FRONTIER",
        "parent_operator_id": None,
        "operator_phase": "FRONTIER",
    }


def test_opt_in_causal_metadata_is_snapshotted_across_complete_transport_lifecycle() -> None:
    class Transport:
        def __init__(self) -> None:
            self.calls = 0

        async def create(self, **_kwargs):
            self.calls += 1
            return {"ok": True}

    async def scenario() -> None:
        request_events: list[dict[str, object]] = []
        response_events: list[dict[str, object]] = []
        provider_calls = 0

        def provider() -> dict[str, object]:
            nonlocal provider_calls
            provider_calls += 1
            return _causal_metadata()

        gate = AdmittedLLMClientV31(
            inner=_ControlledLLM(),
            limit=1,
            policy=AdmissionPolicy.FIFO,
            request_id_prefix="causal-lifecycle",
            observer=request_events.append,
            causal_metadata_provider=provider,
            prefix_encoder=_tokenizer,
        )
        transport = Transport()
        wrapped = AdmittedChatCompletionsV31(
            inner=transport,
            admission=gate,
            response_observer=response_events.append,
        )
        with llm_request_scope(
            kind=RequestKind.FRONTIER,
            stream_id="07741c45",
            source_sequence=3,
        ):
            assert await wrapped.create(
                messages=[{"role": "user", "content": "private prompt"}],
                prompt_name="warm-a",
            ) == {"ok": True}

        correlated = [
            event
            for event in request_events
            if event["event_type"]
            in {
                "llm_request_submitted",
                "llm_request_start",
                "llm_request_terminal",
            }
        ]
        assert len(correlated) == 3
        assert len(response_events) == 1
        for event in [*correlated, *response_events]:
            for key, value in _causal_metadata().items():
                assert event[key] == value
        assert provider_calls == 1
        assert transport.calls == 1
        assert "private prompt" not in repr(correlated)
        assert "private prompt" not in repr(response_events)

    asyncio.run(scenario())


def test_opt_in_request_telemetry_carries_prompt_name_without_changing_default_shape() -> None:
    async def scenario() -> None:
        events: list[dict[str, object]] = []
        gate = AdmittedLLMClientV31(
            inner=_ControlledLLM(),
            limit=1,
            policy=AdmissionPolicy.FIFO,
            request_id_prefix="prompt-telemetry",
            observer=events.append,
            causal_metadata_provider=_causal_metadata,
            prefix_encoder=_tokenizer,
        )
        task = asyncio.create_task(
            _request(gate, RequestKind.FRONTIER, 0, "prompt-visible-only-in-overlay")
        )
        await asyncio.sleep(0)
        gate._inner.release["prompt-visible-only-in-overlay"].set()
        await task
        correlated = [
            row
            for row in events
            if row["event_type"] in {"llm_request_submitted", "llm_request_terminal"}
        ]
        assert correlated
        assert all(
            row["prompt_name"] == "prompt-visible-only-in-overlay"
            for row in correlated
        )
        assert all(
            row["semantic_subrequest_role"] == "prompt-visible-only-in-overlay"
            for row in correlated
        )

    asyncio.run(scenario())


@pytest.mark.parametrize(
    ("metadata", "error_code"),
    [
        ({}, "causal_metadata_incomplete"),
        ({"operator_role": "graphiti.resolve_extracted_nodes"}, "causal_metadata_incomplete"),
        ({**_causal_metadata(), "unsupported": "x"}, "causal_metadata_field_unsupported"),
        ({**_causal_metadata(), "operator_id": "contains whitespace"}, "causal_metadata_value_invalid"),
    ],
)
def test_explicit_causal_provider_fails_closed_before_transport(
    metadata: dict[str, object],
    error_code: str,
) -> None:
    class Transport:
        def __init__(self) -> None:
            self.calls = 0

        async def create(self, **_kwargs):
            self.calls += 1
            return {"ok": True}

    async def scenario() -> None:
        gate = AdmittedLLMClientV31(
            inner=_ControlledLLM(),
            limit=1,
            policy=AdmissionPolicy.FIFO,
            request_id_prefix="causal-fail-closed",
            causal_metadata_provider=lambda: metadata,
            prefix_encoder=_tokenizer,
        )
        inner = Transport()
        wrapped = AdmittedChatCompletionsV31(inner=inner, admission=gate)
        with llm_request_scope(
            kind=RequestKind.FRONTIER,
            stream_id="07741c45",
            source_sequence=3,
        ):
            with pytest.raises(MemBindV31RequestRuntimeError, match=error_code):
                await wrapped.create(
                    messages=[{"role": "user", "content": "private prompt"}],
                    prompt_name="warm-a",
                )
        assert inner.calls == 0
        assert gate.public_events == ()

    asyncio.run(scenario())


def test_default_request_events_retain_frozen_v31_shape_without_provider() -> None:
    async def scenario() -> None:
        inner = _ControlledLLM()
        client = AdmittedLLMClientV31(
            inner=inner,
            limit=1,
            policy=AdmissionPolicy.FIFO,
            request_id_prefix="default-event-shape",
            prefix_encoder=_tokenizer,
        )
        task = asyncio.create_task(
            _request(client, RequestKind.FRONTIER, 0, "default-shape")
        )
        await asyncio.sleep(0)
        inner.release["default-shape"].set()
        await task

        causal_keys = set(_causal_metadata())
        assert all(causal_keys.isdisjoint(event) for event in client.public_events)
        assert all("prompt_name" not in event for event in client.public_events)
        assert all("semantic_subrequest_role" not in event for event in client.public_events)

    asyncio.run(scenario())
