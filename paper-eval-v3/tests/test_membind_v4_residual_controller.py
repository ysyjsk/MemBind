"""RED contracts for reachable FRONTIER > SPEC > future-COMPILE arbitration."""

from __future__ import annotations

import asyncio

from paper_eval.membind_v31.admission import (
    AdmissionPolicy,
    RequestKind,
    RequestSpec,
)
from paper_eval.membind_v31.prefix_affinity import PrefixMetadata
from paper_eval.membind_v31.request_runtime import (
    AdmittedLLMClientV31,
    llm_request_scope,
)
from paper_eval.membind_v4.residual_controller import (
    V4ResidualRequestAdmissionController,
    install_v4_residual_controller,
    v4_speculative_transport_scope,
)


def _request(
    request_id: str,
    *,
    kind: RequestKind,
    source_sequence: int,
    frontier_group: str | None = None,
) -> RequestSpec:
    return RequestSpec(
        request_id=request_id,
        kind=kind,
        stream_id="stream",
        source_sequence=source_sequence,
        affinity_signature=f"sig-{request_id}",
        frontier_group=frontier_group,
    )


def test_reserved_residual_slot_makes_spec_reachable_ahead_of_compile_backlog() -> None:
    gate = V4ResidualRequestAdmissionController(
        limit=2,
        policy=AdmissionPolicy.CACHE_AFFINE,
    )
    gate.submit(_request("compile-0", kind=RequestKind.COMPILE, source_sequence=1))
    gate.submit(_request("compile-1", kind=RequestKind.COMPILE, source_sequence=2))
    assert [item.request_id for item in gate.admit_available()] == [
        "compile-0",
        "compile-1",
    ]

    gate.set_residual_reservation(True)
    gate.submit(
        _request(
            "frontier",
            kind=RequestKind.FRONTIER,
            source_sequence=0,
            frontier_group="stream:0",
        )
    )
    gate.submit(_request("compile-2", kind=RequestKind.COMPILE, source_sequence=3))
    gate.finish("compile-0")
    assert [item.request_id for item in gate.admit_available()] == ["frontier"]

    gate.finish("compile-1")
    assert gate.admit_available() == ()
    assert gate.observation()["waiting_compile_count"] == 1

    with v4_speculative_transport_scope():
        gate.submit(_request("spec", kind=RequestKind.COMPILE, source_sequence=1))
    assert [item.request_id for item in gate.admit_available()] == ["spec"]
    assert gate.observation()["active_speculation_count"] == 1


def test_releasing_rejected_candidate_restores_normal_compile_progress() -> None:
    gate = V4ResidualRequestAdmissionController(
        limit=2,
        policy=AdmissionPolicy.CACHE_AFFINE,
    )
    gate.set_residual_reservation(True)
    gate.submit(
        _request(
            "frontier",
            kind=RequestKind.FRONTIER,
            source_sequence=0,
            frontier_group="stream:0",
        )
    )
    gate.submit(_request("compile", kind=RequestKind.COMPILE, source_sequence=1))
    assert [item.request_id for item in gate.admit_available()] == ["frontier"]
    assert gate.admit_available() == ()

    gate.set_residual_reservation(False)

    assert [item.request_id for item in gate.admit_available()] == ["compile"]


def test_waiting_frontier_wins_before_tagged_speculation() -> None:
    gate = V4ResidualRequestAdmissionController(
        limit=2,
        policy=AdmissionPolicy.CACHE_AFFINE,
    )
    gate.submit(
        _request(
            "frontier-0",
            kind=RequestKind.FRONTIER,
            source_sequence=0,
            frontier_group="stream:0",
        )
    )
    assert [item.request_id for item in gate.admit_available()] == ["frontier-0"]
    gate.submit(
        _request(
            "frontier-retry",
            kind=RequestKind.FRONTIER,
            source_sequence=0,
            frontier_group="stream:0",
        )
    )
    with v4_speculative_transport_scope():
        gate.submit(_request("spec", kind=RequestKind.COMPILE, source_sequence=1))

    assert [item.request_id for item in gate.admit_available()] == [
        "frontier-retry"
    ]
    assert gate.observation()["waiting_speculation_count"] == 1


def test_reserved_spec_handoff_survives_last_frontier_completion() -> None:
    """A pre-authorized SPEC cannot be stranded by the submit/finish race."""

    gate = V4ResidualRequestAdmissionController(
        limit=2,
        policy=AdmissionPolicy.CACHE_AFFINE,
    )
    gate.set_residual_reservation(True)
    gate.submit(
        _request(
            "frontier",
            kind=RequestKind.FRONTIER,
            source_sequence=0,
            frontier_group="stream:0",
        )
    )
    assert [item.request_id for item in gate.admit_available()] == ["frontier"]
    with v4_speculative_transport_scope():
        gate.submit(_request("spec", kind=RequestKind.COMPILE, source_sequence=1))
    gate.submit(_request("compile", kind=RequestKind.COMPILE, source_sequence=2))

    # The conflict/resource decision reserved the residual slot while the
    # frontier was active, but the frontier finishes before SPEC dispatch.
    gate.finish("frontier")

    assert [item.request_id for item in gate.admit_available()] == ["spec"]
    assert gate.observation()["waiting_compile_count"] == 1


def test_tag_does_not_escape_its_context() -> None:
    gate = V4ResidualRequestAdmissionController(
        limit=2,
        policy=AdmissionPolicy.CACHE_AFFINE,
    )
    with v4_speculative_transport_scope():
        gate.submit(_request("spec", kind=RequestKind.COMPILE, source_sequence=1))
    gate.submit(_request("compile", kind=RequestKind.COMPILE, source_sequence=2))

    observation = gate.observation()
    assert observation["waiting_speculation_count"] == 1
    assert observation["waiting_compile_count"] == 1


def test_installed_live_controller_prevents_compile_self_starvation() -> None:
    class Controlled:
        def __init__(self) -> None:
            self.started: list[str] = []
            self.release: dict[str, asyncio.Event] = {}

        async def generate_response(self, *_args, **kwargs):
            name = str(kwargs["prompt_name"])
            self.started.append(name)
            await self.release.setdefault(name, asyncio.Event()).wait()
            return name

    def prefix(*_args, **_kwargs):
        return PrefixMetadata.from_token_ids(
            [1, 2, 3, 4],
            prefix_match_unit=2,
            tokenizer_identity_sha256="a" * 64,
            cache_identity_sha256="b" * 64,
            trace_hmac_key=b"k" * 32,
        )

    async def request(client, kind, source, name):
        with llm_request_scope(
            kind=kind,
            stream_id="stream",
            source_sequence=source,
        ):
            return await client.generate_response([], prompt_name=name)

    async def scenario() -> None:
        inner = Controlled()
        client = AdmittedLLMClientV31(
            inner=inner,
            limit=2,
            policy=AdmissionPolicy.CACHE_AFFINE,
            request_id_prefix="v4-residual-test",
            prefix_encoder=prefix,
        )
        reservation = install_v4_residual_controller(client)
        compile_0 = asyncio.create_task(
            request(client, RequestKind.COMPILE, 1, "compile-0")
        )
        compile_1 = asyncio.create_task(
            request(client, RequestKind.COMPILE, 2, "compile-1")
        )
        await asyncio.sleep(0)
        assert set(inner.started) == {"compile-0", "compile-1"}

        await reservation.reserve(0)
        frontier = asyncio.create_task(
            request(client, RequestKind.FRONTIER, 0, "frontier")
        )
        compile_2 = asyncio.create_task(
            request(client, RequestKind.COMPILE, 3, "compile-2")
        )
        await asyncio.sleep(0)
        inner.release["compile-0"].set()
        await asyncio.sleep(0)
        await asyncio.sleep(0)
        assert "frontier" in inner.started

        inner.release["compile-1"].set()
        await asyncio.sleep(0)
        await asyncio.sleep(0)
        assert "compile-2" not in inner.started

        with v4_speculative_transport_scope():
            speculation = asyncio.create_task(
                request(client, RequestKind.COMPILE, 1, "spec")
            )
        await asyncio.sleep(0)
        await asyncio.sleep(0)
        assert inner.started[-1] == "spec"

        await reservation.release(0)
        inner.release["spec"].set()
        await asyncio.sleep(0)
        await asyncio.sleep(0)
        assert inner.started[-1] == "compile-2"
        inner.release["compile-2"].set()
        inner.release["frontier"].set()
        await asyncio.gather(compile_0, compile_1, compile_2, frontier, speculation)
        assert client.observation()["observed_max_inflight"] == 2

    asyncio.run(scenario())
