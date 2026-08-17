"""TDD for shared per-request LLM admission rather than worker-count inference."""

from __future__ import annotations

import asyncio

from paper_eval.membind_v1.admission import AdmittedLLMClient, RequestAdmission


class _InnerClient:
    def __init__(self) -> None:
        self.active = 0
        self.observed_max = 0
        self.release = asyncio.Event()

    async def generate_response(self, *_args, **_kwargs):
        self.active += 1
        self.observed_max = max(self.observed_max, self.active)
        await self.release.wait()
        self.active -= 1
        return {"ok": True}

    def auxiliary(self) -> str:
        return "delegated"


def test_admitted_llm_client_limits_actual_generate_response_calls_and_exposes_safe_observations() -> None:
    async def scenario() -> tuple[_InnerClient, RequestAdmission, list[dict[str, object]], _InnerClient]:
        inner = _InnerClient()
        admission = RequestAdmission(limit=2)
        events: list[dict[str, object]] = []
        client = AdmittedLLMClient(
            inner=inner,
            admission=admission,
            request_id_prefix="mv1-test:U0",
            observer=events.append,
        )
        tasks = [
            asyncio.create_task(client.generate_response("private prompt", prompt_name="extract_nodes"))
            for _ in range(3)
        ]
        while inner.observed_max < 2:
            await asyncio.sleep(0)
        assert client.auxiliary() == "delegated"
        inner.release.set()
        await asyncio.gather(*tasks)
        return inner, admission, events, inner

    inner, admission, events, _ = asyncio.run(scenario())

    assert inner.observed_max == 2
    assert admission.observation()["observed_max_inflight"] == 2
    assert [event["event_type"] for event in events] == [
        "llm_request_start",
        "llm_request_start",
        "llm_request_end",
        "llm_request_end",
        "llm_request_start",
        "llm_request_end",
    ]
    assert all("prompt" not in event for event in events)
    assert {event["prompt_name"] for event in events} == {"extract_nodes"}
