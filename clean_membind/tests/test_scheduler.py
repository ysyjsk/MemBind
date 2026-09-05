import asyncio

import pytest

from membind.core.contracts import PreparedWork, RequestIdentity
from membind.core.scheduler import MemBindScheduler


def make_identity(sequence: int, *, state: str = "state"):
    return RequestIdentity(
        logical_id=f"e{sequence}", method="NATIVE", episode_sha256=f"episode-{sequence}",
        previous_state_sha256=state, model_identity="model", graphiti_identity="graphiti",
        schema_sha256="schema", config_sha256="config",
    )


@pytest.mark.asyncio
async def test_scheduler_publishes_in_order_and_reuses_valid_preparation():
    items = list(range(4))
    prepared = []
    published = []

    async def prepare(item):
        await asyncio.sleep(0 if item != 1 else 0.01)
        value = PreparedWork(make_identity(item), {"sequence": item}, "test", item + 1)
        prepared.append(item)
        return value

    async def reuse(item, work):
        published.append(("reuse", item, work.payload["sequence"]))

    async def native(item):
        published.append(("native", item))

    result = await MemBindScheduler(lookahead=2).run(
        items, identity_for=lambda item: make_identity(item), prepare=prepare,
        reuse_publish=reuse, native_publish=native,
    )
    assert [row[1] for row in published] == items
    assert result.reused == 4
    assert result.fallback == 0
    assert sorted(prepared) == items


@pytest.mark.asyncio
async def test_scheduler_fails_closed_to_native_on_stale_or_failed_work():
    items = list(range(3))
    published = []

    async def prepare(item):
        if item == 0:
            return PreparedWork(make_identity(item, state="stale"), {}, "test", 1)
        if item == 1:
            raise RuntimeError("provider unavailable")
        return PreparedWork(make_identity(item), {}, "test", 1)

    async def reuse(item, _work):
        published.append(("reuse", item))

    async def native(item):
        published.append(("native", item))

    result = await MemBindScheduler(lookahead=1).run(
        items, identity_for=lambda item: make_identity(item), prepare=prepare,
        reuse_publish=reuse, native_publish=native,
    )
    assert published == [("native", 0), ("native", 1), ("reuse", 2)]
    assert [row.validation.reason for row in result.records] == [
        "REQUEST_IDENTITY_MISMATCH", "PREPARE_FAILURE:RuntimeError", "VALID"
    ]
