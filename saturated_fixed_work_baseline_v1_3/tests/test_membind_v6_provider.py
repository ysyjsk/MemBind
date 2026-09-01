from __future__ import annotations

import asyncio

from saturated_fixed_work_baseline_v1_3.membind_v5.runtime.core.admission import AdmissionArbiter, CapacityAuthority
from saturated_fixed_work_baseline_v1_3.membind_v5.runtime.core.binder import NativeBindingScope
from saturated_fixed_work_baseline_v1_3.membind_v5.runtime.core.provider_admission import provider_scope
from saturated_fixed_work_baseline_v1_3.membind_v5.runtime.core.transcript import TranscriptStore
from saturated_fixed_work_baseline_v1_3.membind_v6.provider import V6ProviderClient


class _Delegate:
    def __init__(self) -> None:
        self.calls = 0

    async def generate_response(self, messages, response_model=None, max_tokens=None, model_size=None, group_id=None, prompt_name=None, *, attribute_extraction=False):
        self.calls += 1
        await asyncio.sleep(0)
        return {"prompt_name": prompt_name, "messages": messages, "calls": self.calls}


def _kwargs(prompt_name: str):
    return {
        "response_model": {"type": "object"},
        "max_tokens": 32,
        "model_size": "medium",
        "group_id": "g",
        "prompt_name": prompt_name,
    }


def test_certified_replay_bypasses_provider_and_noncertified_native_is_real() -> None:
    async def run() -> tuple[int, dict, dict]:
        delegate = _Delegate()
        store = TranscriptStore()
        arbiter = AdmissionArbiter(CapacityAuthority(2))
        capture = V6ProviderClient(delegate, store=store, arbiter=arbiter, mode="capture", durable_frontier=lambda: -1)
        with provider_scope(region="PREPARE", source_sequence=0):
            await capture.generate_response([{"role": "user", "content": "x"}], **_kwargs("extract_nodes.extract_message"))
        replay = V6ProviderClient(delegate, store=store, arbiter=arbiter, mode="replay", durable_frontier=lambda: 0)
        with provider_scope(region="NATIVE", source_sequence=0):
            with NativeBindingScope(store, source_sequence=0):
                await replay.generate_response([{"role": "user", "content": "x"}], **_kwargs("extract_nodes.extract_message"))
            await replay.generate_response([{"role": "user", "content": "y"}], **_kwargs("dedupe_nodes.nodes"))
        return delegate.calls, store.summary(), arbiter.evidence()

    calls, summary, evidence = asyncio.run(run())
    assert calls == 2
    assert {
        key: summary[key]
        for key in ("logical_captured", "logical_consumed", "unconsumed", "duplicates")
    } == {"logical_captured": 1, "logical_consumed": 1, "unconsumed": 0, "duplicates": 0}
    assert summary["logical_discarded"] == 0
    assert summary["fresh_fallback"] == 0
    assert evidence["outstanding"] == 0
    assert evidence["events"]


def test_future_provider_outstanding_reserves_one_permit() -> None:
    async def run() -> dict:
        delegate = _Delegate()
        store = TranscriptStore()
        arbiter = AdmissionArbiter(CapacityAuthority(4))
        client = V6ProviderClient(delegate, store=store, arbiter=arbiter, mode="capture", durable_frontier=lambda: -1)
        started = asyncio.Event()
        release = asyncio.Event()

        async def call(source: int):
            with provider_scope(region="PREPARE", source_sequence=source):
                started.set()
                # Keep all calls in flight long enough to exercise the cap.
                task = asyncio.create_task(client.generate_response([{"role": "user", "content": str(source)}], **_kwargs("extract_nodes.extract_message")))
                await started.wait()
                await release.wait()
                return await task

        tasks = [asyncio.create_task(call(source)) for source in range(1, 8)]
        await asyncio.sleep(0.02)
        release.set()
        await asyncio.gather(*tasks)
        return arbiter.evidence()

    evidence = asyncio.run(run())
    admits = [row for row in evidence["events"] if row["event"] == "ADMISSION_ADMIT"]
    assert max(row["outstanding"] for row in admits) <= 4
    assert max(row["future_outstanding"] for row in admits) <= 3
    assert evidence["outstanding"] == 0
