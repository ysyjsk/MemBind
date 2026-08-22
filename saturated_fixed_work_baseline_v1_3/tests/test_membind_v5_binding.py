from __future__ import annotations

import asyncio

import pytest

from saturated_fixed_work_baseline_v1_3.membind_v5.runtime.adapters.client_proxy import V5LLMClientProxy
from saturated_fixed_work_baseline_v1_3.membind_v5.runtime.core.binder import BindingMismatch, NativeBindingScope
from saturated_fixed_work_baseline_v1_3.membind_v5.runtime.core.request_identity import build_request_identity
from saturated_fixed_work_baseline_v1_3.membind_v5.runtime.core.transcript import TranscriptStore


class FakeClient:
    def __init__(self) -> None:
        self.calls = 0
        self.seen_messages = []

    async def generate_response(self, messages, **kwargs):
        self.calls += 1
        self.seen_messages.append(messages)
        messages[0]["content"] = "mutated"
        return {"nodes": [{"name": "A"}]}


@pytest.mark.asyncio
async def test_proxy_captures_then_replays_without_second_provider_call() -> None:
    client = FakeClient()
    store = TranscriptStore()
    capture = V5LLMClientProxy(client, store, source_sequence=0, mode="capture", client_identity={"class": "Fake", "source_hash": "x"})
    messages = [{"role": "user", "content": "source"}]
    await capture.generate_response(messages, prompt_name="extract_nodes.extract_message")
    assert messages[0]["content"] == "source"
    assert client.calls == 1
    replay = V5LLMClientProxy(client, store, source_sequence=0, mode="replay", client_identity={"class": "Fake", "source_hash": "x"})
    with NativeBindingScope(store, source_sequence=0):
        value = await replay.generate_response(messages, prompt_name="extract_nodes.extract_message")
    assert value == {"nodes": [{"name": "A"}]}
    assert client.calls == 1


@pytest.mark.asyncio
async def test_proxy_delegates_uncertified_call_and_strictly_rejects_mismatch() -> None:
    client = FakeClient()
    store = TranscriptStore()
    capture = V5LLMClientProxy(client, store, source_sequence=0, mode="capture", client_identity={"class": "Fake", "source_hash": "x"})
    await capture.generate_response([{"role": "user", "content": "x"}], prompt_name="extract_nodes.extract_message")
    replay = V5LLMClientProxy(client, store, source_sequence=0, mode="replay", client_identity={"class": "Fake", "source_hash": "x"})
    with pytest.raises(BindingMismatch):
        with NativeBindingScope(store, source_sequence=0):
            await replay.generate_response([{"role": "user", "content": "different"}], prompt_name="extract_nodes.extract_message")
    delegate_store = TranscriptStore()
    delegate_proxy = V5LLMClientProxy(client, delegate_store, source_sequence=0, mode="replay", client_identity={"class": "Fake", "source_hash": "x"})
    with NativeBindingScope(delegate_store, source_sequence=0, strict=True):
        value = await delegate_proxy.generate_response([{"role": "user", "content": "uncertified"}], prompt_name="extract_nodes.extract_attributes")
    assert client.calls == 2
    assert value["nodes"]
