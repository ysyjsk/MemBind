"""Offline and fake-runtime contracts for the single bounded C0 episode."""

from __future__ import annotations

import asyncio
import json
import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace
from unittest import IsolatedAsyncioTestCase, TestCase


ROOT = Path(__file__).resolve().parents[1]
REPO = ROOT.parent
SOURCE = Path(
    "/data/predator/ly/Mem/data/raw/longmemeval-cleaned/"
    "longmemeval_s_cleaned.json"
)
sys.path.insert(0, str(ROOT / "src"))

from current_state_gate import LiveActionDenied  # noqa: E402
from native_characterization_c0 import (  # noqa: E402
    execute_c0,
    prepare_c0_invocation,
    validate_c0_result,
    write_c0_result,
)


class C0PreparationTests(TestCase):
    def test_real_offline_preview_matches_freeze_without_semantic_content(self) -> None:
        invocation = prepare_c0_invocation(
            validation_root=ROOT,
            source_path=SOURCE,
        )
        preview = invocation.to_preview()
        freeze = json.loads(
            (ROOT / "artifacts/native_characterization/freeze.json").read_text()
        )
        self.assertEqual(preview["history_id"], "07741c45")
        self.assertEqual(preview["source_sequence"], 0)
        self.assertEqual(
            preview["episode_source_sha256"],
            freeze["screening"]["c0"]["episode_source_sha256"],
        )
        self.assertEqual(
            preview["graph_namespace"],
            freeze["screening"]["c0"]["graph_namespace"],
        )
        serialized = json.dumps(preview, sort_keys=True).lower()
        for forbidden in ("body", "content", "session_id", "api_key", "authorization"):
            self.assertNotIn(forbidden, serialized)


class _FakeRuntime:
    def __init__(
        self,
        events: list[str],
        failure: BaseException | None = None,
        readiness_failure: BaseException | None = None,
    ) -> None:
        self.events = events
        self.failure = failure
        self.config = SimpleNamespace(
            to_artifact=lambda: {
                "classification": "U0",
                "policies": {"prompt_cache": False, "embedding_cache": False},
            }
        )

        async def build_indices_and_constraints() -> None:
            events.append("ready")
            if readiness_failure is not None:
                raise readiness_failure

        async def add_episode(**kwargs):
            events.append("add_episode")
            self.kwargs = kwargs
            if failure is not None:
                raise failure
            return SimpleNamespace(
                nodes=[object(), object()],
                edges=[object()],
                episodic_edges=[object(), object()],
                communities=[],
                community_edges=[],
            )

        async def close() -> None:
            events.append("close")

        self.graphiti = SimpleNamespace(
            driver=SimpleNamespace(
                _init_task=None,
                build_indices_and_constraints=build_indices_and_constraints,
            ),
            add_episode=add_episode,
            close=close,
        )


def _invocation():
    return prepare_c0_invocation(validation_root=ROOT, source_path=SOURCE)


class C0ExecutionTests(IsolatedAsyncioTestCase):
    async def test_denial_precedes_invocation_factory_and_sink(self) -> None:
        events: list[str] = []

        def deny(_action):
            events.append("gate")
            raise LiveActionDenied("denied_for_test")

        with self.assertRaisesRegex(LiveActionDenied, "denied_for_test"):
            await execute_c0(
                authorization_checker=deny,
                invocation_loader=lambda: events.append("invocation"),
                runtime_factory=lambda **_kwargs: events.append("factory"),
                result_sink=lambda _result: events.append("sink"),
            )
        self.assertEqual(events, ["gate"])

    async def test_success_runs_exactly_one_episode_and_closes_before_sink(self) -> None:
        events: list[str] = []
        invocation = _invocation()
        runtime = _FakeRuntime(events)

        def factory(**_kwargs):
            events.append("factory")
            return runtime

        captured = []
        result = await execute_c0(
            authorization_checker=lambda _action: events.append("gate"),
            invocation_loader=lambda: (events.append("invocation"), invocation)[1],
            runtime_factory=factory,
            result_sink=lambda value: (events.append("sink"), captured.append(value)),
        )
        validate_c0_result(result)
        self.assertEqual(
            events,
            ["gate", "invocation", "factory", "ready", "add_episode", "close", "sink"],
        )
        self.assertEqual(result["status"], "pass")
        self.assertEqual(result["result_counts"]["nodes"], 2)
        self.assertEqual(runtime.kwargs["group_id"], invocation.graph_namespace)
        self.assertEqual(len(captured), 1)

    async def test_failure_preserves_exception_closes_and_sinks_sanitized_code(self) -> None:
        events: list[str] = []
        invocation = _invocation()
        failure = RuntimeError("PRIVATE-LIVE-FAILURE")
        runtime = _FakeRuntime(events, failure=failure)
        captured = []

        with self.assertRaises(RuntimeError) as raised:
            await execute_c0(
                authorization_checker=lambda _action: events.append("gate"),
                invocation_loader=lambda: invocation,
                runtime_factory=lambda **_kwargs: runtime,
                result_sink=lambda value: (events.append("sink"), captured.append(value)),
            )
        self.assertIs(raised.exception, failure)
        self.assertEqual(events[-2:], ["close", "sink"])
        self.assertEqual(captured[0]["status"], "error")
        self.assertEqual(captured[0]["error_code"], "builtins.RuntimeError")
        self.assertNotIn("PRIVATE-LIVE-FAILURE", json.dumps(captured[0]))

    async def test_readiness_failure_is_closed_checkpointed_and_rethrown(self) -> None:
        events: list[str] = []
        invocation = _invocation()
        failure = ConnectionError("PRIVATE-NEO4J-FAILURE")
        runtime = _FakeRuntime(events, readiness_failure=failure)
        captured = []

        with self.assertRaises(ConnectionError) as raised:
            await execute_c0(
                authorization_checker=lambda _action: events.append("gate"),
                invocation_loader=lambda: invocation,
                runtime_factory=lambda **_kwargs: runtime,
                result_sink=lambda value: (events.append("sink"), captured.append(value)),
            )
        self.assertIs(raised.exception, failure)
        self.assertEqual(events[-3:], ["ready", "close", "sink"])
        self.assertEqual(captured[0]["status"], "error")
        self.assertEqual(captured[0]["add_episode_latency_ns"], 0)
        self.assertNotIn("PRIVATE-NEO4J-FAILURE", json.dumps(captured[0]))

    async def test_writer_uses_frozen_run_namespace_and_canonical_checkpoints(self) -> None:
        events: list[str] = []
        invocation = _invocation()
        result = await execute_c0(
            authorization_checker=lambda _action: None,
            invocation_loader=lambda: invocation,
            runtime_factory=lambda **_kwargs: _FakeRuntime(events),
            result_sink=lambda _value: None,
        )
        with tempfile.TemporaryDirectory() as tmp:
            written = write_c0_result(result, Path(tmp))
            run_dir = Path(tmp) / result["run_id"]
            self.assertEqual(set(written), {"manifest.json", "checkpoint.json"})
            for name in written:
                payload = json.loads((run_dir / name).read_text(encoding="ascii"))
                self.assertEqual(payload["run_id"], result["run_id"])
                self.assertRegex(written[name], r"^[0-9a-f]{64}$")


if __name__ == "__main__":
    import unittest

    unittest.main()
