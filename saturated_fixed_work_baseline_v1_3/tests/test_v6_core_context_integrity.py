from __future__ import annotations

import asyncio

from saturated_fixed_work_baseline_v1_3.membind_v5.runtime.core.binder import (
    NativeBindingScope,
)
from saturated_fixed_work_baseline_v1_3.membind_v5.runtime.core.request_identity import (
    build_request_identity,
)
import saturated_fixed_work_baseline_v1_3.membind_v6_1.core as core
from saturated_fixed_work_baseline_v1_3.membind_v6_1.mab import (
    V61MABError,
    _assert_core_context_integrity,
)


def _identity(content: str):
    return build_request_identity(
        source_sequence=0,
        callsite="extract_nodes.extract_message",
        ordinal=0,
        messages=[{"role": "user", "content": content}],
        response_model={"type": "object"},
        max_tokens=32,
        model_size="medium",
        group_id="g",
        prompt_name="extract_nodes.extract_message",
        flags={"attribute_extraction": False},
        client_identity={"class": "test", "source_hash": "test"},
        transport_identity={"seed": 1},
        cache_salt="",
        previous_context_digest="",
    )


def test_core_identity_disables_context_removal_and_has_revision() -> None:
    identity = core.core_identity()
    assert identity["implementation_revision"] == "context-integrity-fix-v1"
    assert identity["certified_message_transform"] is None
    assert identity["context_removal_allowed"] is False
    assert identity["same_logical_request_required"] is True


def test_core_nonempty_context_removal_fails_closed() -> None:
    try:
        _assert_core_context_integrity(
            "MEMBIND_CORE", {"certified_previous_context_chars_removed": 1}
        )
    except V61MABError as exc:
        assert "context integrity" in str(exc)
    else:
        raise AssertionError("Core accepted non-empty previous-context removal")


def test_non_strict_core_binding_mismatch_falls_back_fresh_without_consuming() -> None:
    from saturated_fixed_work_baseline_v1_3.membind_v5.runtime.core.transcript import (
        TranscriptStore,
    )

    store = TranscriptStore()
    store.capture(_identity("prepared"), {"answer": "captured"})
    calls = 0

    async def fresh():
        nonlocal calls
        calls += 1
        return {"answer": "fresh"}

    async def invoke():
        with NativeBindingScope(store, source_sequence=0, strict=False) as scope:
            return await scope.invoke(_identity("native changed"), fresh, certified=True)

    assert asyncio.run(invoke()) == {"answer": "fresh"}
    assert calls == 1
    assert store.summary()["logical_consumed"] == 0
    assert store.summary()["unconsumed"] == 0


def test_core_runner_wires_no_transform_and_non_strict_binding(monkeypatch) -> None:
    captured = {}

    async def fake_runner(**kwargs):
        captured.update(kwargs)
        return {"status": "PASS"}

    monkeypatch.setattr(core, "run_mab_v61_construction_async", fake_runner)

    async def invoke():
        return await core.run_membind_core_construction_async(
            policy=core.core_policy(),
            run_id="r",
            context_id="c",
            namespace="n",
            episodes=(),
            runtime_builder=lambda: None,
            instrumentation_installer=lambda *_: None,
            recorder_factory=lambda: None,
            graph_exporter=lambda *_: None,
            output_root="/tmp/unused",
            authority={},
            workload_manifest=None,
            frozen_config={},
            environment={},
            preflight={},
        )

    result = asyncio.run(invoke())
    assert result["method"] == "MEMBIND_CORE"
    assert captured["certified_message_transform"] is None
    assert captured["binding_strict"] is False
    assert captured["implementation_revision"] == "context-integrity-fix-v1"
    assert captured["method_boundary"] == "MEMBIND_CORE"
