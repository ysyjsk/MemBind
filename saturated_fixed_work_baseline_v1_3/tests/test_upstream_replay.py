from __future__ import annotations

import asyncio
from pathlib import Path

from saturated_fixed_work_baseline_v1_3.membind_v5.runtime.core.admission import (
    CapacityAuthority,
)
from saturated_fixed_work_baseline_v1_3.membind_v5.runtime.core.binder import (
    NativeBindingScope,
)
from saturated_fixed_work_baseline_v1_3.membind_v5.runtime.core.provider_admission import (
    provider_scope,
)
from saturated_fixed_work_baseline_v1_3.membind_v5.runtime.core.transcript import (
    TranscriptStore,
)
from saturated_fixed_work_baseline_v1_3.membind_v6_1.admission import (
    ForegroundAdmissionArbiter,
)
from saturated_fixed_work_baseline_v1_3.membind_v6_1.resource_credit import (
    ResourceCreditPolicy,
)
from saturated_fixed_work_baseline_v1_3.membind_v6_1.upstream_replay import (
    UpstreamReplayClient,
)


def test_upstream_replay_module_has_no_prohibited_runtime_imports() -> None:
    source = (
        Path(__file__).resolve().parents[1]
        / "src"
        / "saturated_fixed_work_baseline_v1_3"
        / "membind_v6_1"
        / "upstream_replay.py"
    ).read_text(encoding="utf-8")
    prohibited = (
        "structured_output_recovery",
        "bounded_edge_tasks",
        "finite_edge_task",
        "membind_v6_1.mab",
    )
    assert all(value not in source for value in prohibited)


def test_upstream_replay_is_exact_and_never_falls_back() -> None:
    class Delegate:
        def __init__(self) -> None:
            self.calls: list[str] = []

        async def generate_response(
            self,
            _messages,
            *,
            prompt_name=None,
            **_kwargs,
        ):
            self.calls.append(str(prompt_name))
            return {"prompt_name": prompt_name, "physical_call": len(self.calls)}

    async def scenario() -> None:
        delegate = Delegate()
        policy = ResourceCreditPolicy()
        authority = CapacityAuthority(2)
        admission = ForegroundAdmissionArbiter(authority, policy=policy)
        store = TranscriptStore()
        durable = {"value": -1}
        capture = UpstreamReplayClient(
            delegate,
            store=store,
            admission=admission,
            mode="capture",
            durable_frontier=lambda: durable["value"],
        )
        replay = UpstreamReplayClient(
            delegate,
            store=store,
            admission=admission,
            mode="replay",
            durable_frontier=lambda: durable["value"],
        )
        messages = [{"role": "user", "content": "same upstream prompt"}]
        with provider_scope(region="PREPARE", source_sequence=0):
            prepared = await capture.generate_response(
                messages,
                prompt_name="extract_nodes.extract_message",
                max_tokens=16384,
            )
        with provider_scope(region="NATIVE", source_sequence=0):
            with NativeBindingScope(store, source_sequence=0, strict=True):
                consumed = await replay.generate_response(
                    messages,
                    prompt_name="extract_nodes.extract_message",
                    max_tokens=16384,
                )
                auxiliary = await replay.generate_response(
                    messages,
                    prompt_name="dedupe_nodes.resolve_nodes",
                    max_tokens=16384,
                )
        assert consumed == prepared
        assert auxiliary["prompt_name"] == "dedupe_nodes.resolve_nodes"
        assert delegate.calls == [
            "extract_nodes.extract_message",
            "dedupe_nodes.resolve_nodes",
        ]
        assert store.summary() == {
            "logical_captured": 1,
            "logical_consumed": 1,
            "logical_discarded": 0,
            "unconsumed": 0,
            "duplicates": 0,
            "fresh_fallback": 0,
            "mismatch_fallback": 0,
            "missing_fallback": 0,
        }
        assert capture.provider_calls[0]["physical_attempt_count"] == 1
        assert replay.provider_calls[0]["physical_attempt_count"] == 0
        assert replay.provider_calls[0]["replay"] is True
        assert replay.provider_calls[1]["physical_attempt_count"] == 1
        assert admission.outstanding == 0

    asyncio.run(scenario())
