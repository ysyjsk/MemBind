"""TDD for tracing direct MemBind-v1 Graphiti semantic calls.

The normal Native phase patch instruments aliases used by ``Graphiti.add_episode``.
MemBind-v1 holds the semantic functions directly, so this narrow decorator is
required to preserve the same raw phase evidence without changing semantics.
"""

from __future__ import annotations

import asyncio
from contextlib import contextmanager

import pytest

from paper_eval.membind_v1.semantic_trace_binding import (
    SemanticTraceBindingError,
    trace_semantic_binding,
)
from paper_eval.s5_graphiti_semantic_binding import S5GraphitiSemanticBinding


class TraceRecorder:
    """Small structural recorder double; production injects the legacy recorder."""

    def __init__(self) -> None:
        self.records: list[dict[str, object]] = []
        self._scope: tuple[str, str, int] | None = None
        self._stack: list[str] = []

    @contextmanager
    def episode_scope(self, run_id: str, episode_id: str, source_sequence: int):
        self._scope = (run_id, episode_id, source_sequence)
        try:
            yield
        finally:
            self._scope = None

    @contextmanager
    def span(self, phase: str):
        span_id = f"span-{len(self.records) + len(self._stack)}"
        parent = self._stack[-1] if self._stack else None
        self._stack.append(span_id)
        status = "ok"
        error_code = None
        try:
            yield
        except BaseException as error:
            status = "error"
            error_code = f"{type(error).__module__}.{type(error).__qualname__}"
            raise
        finally:
            self._stack.pop()
            self.records.append(
                {
                    "phase": phase,
                    "span_id": span_id,
                    "parent_span_id": parent,
                    "status": status,
                    "error_code": error_code,
                }
            )

    def episode_envelope(self, run_id: str, episode_id: str, source_sequence: int) -> dict[str, object]:
        assert self._scope is None
        return {"spans": list(self.records)}


def _binding(
    calls: list[str], *, fail: str | None = None, pointer_is_sync: bool = False
) -> S5GraphitiSemanticBinding:
    def operation(name: str):
        async def wrapped(*_args, **_kwargs):
            calls.append(name)
            if fail == name:
                raise RuntimeError("private upstream detail")
            await asyncio.sleep(0)
            return name

        return wrapped

    def pointer_operation(*_args, **_kwargs):
        calls.append("resolve_edge_pointers")
        if fail == "resolve_edge_pointers":
            raise RuntimeError("private upstream detail")
        return "resolve_edge_pointers"

    return S5GraphitiSemanticBinding(
        extract_nodes=operation("extract_nodes"),
        resolve_extracted_nodes=operation("resolve_extracted_nodes"),
        extract_attributes_from_nodes=operation("extract_attributes_from_nodes"),
        extract_edges=operation("extract_edges"),
        resolve_extracted_edges=operation("resolve_extracted_edges"),
        resolve_edge_pointers=(
            pointer_operation if pointer_is_sync else operation("resolve_edge_pointers")
        ),
        process_episode_data=operation("process_episode_data"),
        loader_verified=True,
    )


def test_direct_semantic_binding_records_native_phase_names_under_one_episode_root() -> None:
    async def scenario() -> tuple[list[str], list[dict[str, object]]]:
        calls: list[str] = []
        recorder = TraceRecorder()
        traced = trace_semantic_binding(_binding(calls), recorder)
        with recorder.episode_scope("run", "episode", 0):
            with recorder.span("add-episode"):
                assert await traced.extract_nodes() == "extract_nodes"
                assert await traced.resolve_extracted_nodes() == "resolve_extracted_nodes"
                assert await traced.extract_edges() == "extract_edges"
                assert await traced.resolve_edge_pointers() == "resolve_edge_pointers"
                assert await traced.resolve_extracted_edges() == "resolve_extracted_edges"
                assert await traced.extract_attributes_from_nodes() == "extract_attributes_from_nodes"
                assert await traced.process_episode_data() == "process_episode_data"
        return calls, recorder.episode_envelope("run", "episode", 0)["spans"]

    calls, spans = asyncio.run(scenario())

    assert calls == [
        "extract_nodes",
        "resolve_extracted_nodes",
        "extract_edges",
        "resolve_edge_pointers",
        "resolve_extracted_edges",
        "extract_attributes_from_nodes",
        "process_episode_data",
    ]
    phases = [span["phase"] for span in spans]
    assert phases == [
        "node-extraction",
        "node-resolution",
        "edge-extraction",
        "edge-pointer-resolution",
        "edge-resolution",
        "attributes-summary",
        "publication",
        "add-episode",
    ]
    root = spans[-1]
    assert all(span["parent_span_id"] == root["span_id"] for span in spans[:-1])
    assert all(span["status"] == "ok" for span in spans)


def test_direct_semantic_binding_preserves_upstream_exception_while_recording_safe_error_class() -> None:
    async def scenario() -> list[dict[str, object]]:
        recorder = TraceRecorder()
        traced = trace_semantic_binding(_binding([], fail="extract_edges"), recorder)
        with recorder.episode_scope("run", "episode", 0):
            with recorder.span("add-episode"):
                with pytest.raises(RuntimeError, match="private upstream detail"):
                    await traced.extract_edges()
        return recorder.episode_envelope("run", "episode", 0)["spans"]

    spans = asyncio.run(scenario())

    assert spans[0]["phase"] == "edge-extraction"
    assert spans[0]["status"] == "error"
    assert spans[0]["error_code"] == "builtins.RuntimeError"


def test_direct_semantic_binding_preserves_the_sync_pointer_resolution_contract() -> None:
    calls: list[str] = []
    recorder = TraceRecorder()
    traced = trace_semantic_binding(
        _binding(calls, pointer_is_sync=True), recorder
    )

    with recorder.episode_scope("run", "episode", 0):
        with recorder.span("add-episode"):
            assert traced.resolve_edge_pointers() == "resolve_edge_pointers"

    spans = recorder.episode_envelope("run", "episode", 0)["spans"]
    assert calls == ["resolve_edge_pointers"]
    assert spans[0]["phase"] == "edge-pointer-resolution"
    assert spans[0]["status"] == "ok"


def test_trace_binding_rejects_invalid_dependencies() -> None:
    with pytest.raises(SemanticTraceBindingError, match="semantic binding"):
        trace_semantic_binding(object(), TraceRecorder())
    with pytest.raises(SemanticTraceBindingError, match="trace recorder"):
        trace_semantic_binding(_binding([]), object())
