"""RED contracts for one isolated, bounded Graphiti API experiment.

Every dependency below is synthetic.  This suite must never open the relay,
load the local embedding model, or connect to Neo4j.
"""

from __future__ import annotations

import asyncio
import inspect
import json
import tempfile
from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace
from unittest import IsolatedAsyncioTestCase, TestCase
from unittest.mock import patch

from gpt55_temporary.api_characterization import bounded_runner as runner


FROZEN_HISTORY_ID = "07741c45"
FROZEN_SOURCE_SEQUENCE = 0
FROZEN_EPISODE_SHA256 = (
    "be983c489b10deea9c4d860f1e3203e4fa5d964154e004b814b2b5fee410156a"
)
MAINLINE_C0_NAMESPACE = "nc-c0-d620535ccf0f0f43"


class FakeTransport:
    """Async Chat transport with observable retry and payload arguments."""

    def __init__(self, response: object | BaseException | None = None) -> None:
        self.response = response if response is not None else {"choices": []}
        self.calls: list[dict[str, object]] = []

    async def post_json(self, **kwargs):
        self.calls.append(kwargs)
        if isinstance(self.response, BaseException):
            raise self.response
        return self.response


class FakeInstrumentor:
    """Record lifecycle calls without patching Graphiti or the process."""

    def __init__(self, events: list[str]) -> None:
        self.events = events
        self.installed_on: object | None = None
        self.restore_count = 0

    def install(self, graphiti: object) -> None:
        self.installed_on = graphiti
        self.events.append("instrumentation.install")

    def restore(self) -> None:
        self.restore_count += 1
        self.events.append("instrumentation.restore")


@dataclass(frozen=True)
class FakeEpisode:
    history_id: str
    source_sequence: int
    source_sha256: str
    name: str = "frozen-development-episode-000"
    episode_body: str = "synthetic body; never persisted by the fake runner"
    source_description: str = "synthetic source"
    reference_time: str = "2026-08-11T00:00:00+00:00"
    source: str = "message"


class FakeGraphiti:
    def __init__(
        self,
        events: list[str],
        failure: BaseException | None = None,
        close_failure: BaseException | None = None,
    ) -> None:
        self.events = events
        self.failure = failure
        self.close_failure = close_failure
        self.add_calls: list[dict[str, object]] = []
        self.closed = False
        self.close_count = 0

    async def add_episode(self, **kwargs):
        self.events.append("graphiti.add_episode")
        self.add_calls.append(kwargs)
        if self.failure is not None:
            raise self.failure
        return SimpleNamespace(nodes=[object()], edges=[object()])

    async def close(self) -> None:
        self.close_count += 1
        self.closed = True
        self.events.append("graphiti.close")
        if self.close_failure is not None:
            raise self.close_failure


class FakeHandoff:
    def __init__(self, expected: object, events: list[str]) -> None:
        self.expected = expected
        self.events = events
        self.claim_count = 0

    def claim(self, resource: object) -> None:
        if resource is not self.expected:
            raise AssertionError("bounded runner claimed the wrong Graphiti")
        self.claim_count += 1
        self.events.append("resource_handoff.claim")


def fake_episode_loader(*, history_id, source_sequence, expected_sha256):
    if (
        history_id,
        source_sequence,
        expected_sha256,
    ) != (FROZEN_HISTORY_ID, FROZEN_SOURCE_SEQUENCE, FROZEN_EPISODE_SHA256):
        raise AssertionError("runner did not request the frozen bounded episode")
    return FakeEpisode(history_id, source_sequence, expected_sha256)


class FrozenConfigurationTests(TestCase):
    """Freeze the calibration input and temporary runtime identity."""

    def test_defaults_pin_one_development_episode_and_local_bge_m3(self):
        with tempfile.TemporaryDirectory() as raw_tmp:
            config = runner.BoundedRunConfig(
                attempt_id="api-char-unit-001",
                artifact_root=Path(raw_tmp) / "artifacts",
            )

        self.assertEqual(config.history_id, FROZEN_HISTORY_ID)
        self.assertEqual(config.source_sequence, FROZEN_SOURCE_SEQUENCE)
        self.assertEqual(config.episode_source_sha256, FROZEN_EPISODE_SHA256)
        self.assertEqual(config.model, "gpt-5.4-mini")
        self.assertEqual(config.embedding_provider, "local_bge_m3")
        self.assertEqual(config.embedding_model, "BAAI/bge-m3")
        self.assertEqual(config.embedding_revision, "5617a9f61b028005a4858fdac845db406aefb181")
        self.assertEqual(config.embedding_dimension, 1024)
        self.assertIn(config.embedding_device, {"cuda", "cuda:0"})
        self.assertNotEqual(config.graph_namespace, MAINLINE_C0_NAMESPACE)
        self.assertNotEqual(config.graph_namespace, config.history_id)
        self.assertTrue(config.graph_namespace.startswith("tmp-api-char-"))
        self.assertGreater(config.max_api_attempts, 0)

    def test_overlapping_span_occupancy_is_interval_union_not_naive_sum(self):
        intervals = [(10, 40), (20, 60), (70, 80), (80, 90)]

        self.assertEqual(runner.interval_union_ns(intervals), 70)
        self.assertNotEqual(runner.interval_union_ns(intervals), 90)

    def test_namespace_binds_attempt_id_and_canonical_artifact_root(self):
        with tempfile.TemporaryDirectory() as raw_tmp:
            root = Path(raw_tmp)
            first = runner.BoundedRunConfig(
                attempt_id="shared-attempt-id",
                artifact_root=root / "first" / ".." / "first",
            )
            same = runner.BoundedRunConfig(
                attempt_id="shared-attempt-id",
                artifact_root=(root / "first").resolve(),
            )
            other = runner.BoundedRunConfig(
                attempt_id="shared-attempt-id",
                artifact_root=root / "second",
            )

        self.assertEqual(first.graph_namespace, same.graph_namespace)
        self.assertNotEqual(first.graph_namespace, other.graph_namespace)


class RelayChatContractTests(IsolatedAsyncioTestCase):
    """Preserve Graphiti prompts while excluding unsupported GPT-5 options."""

    def test_build_chat_request_preserves_structural_role_content_and_order(self):
        messages = [
            SimpleNamespace(role="system", content="Graphiti's own system message"),
            {"role": "user", "content": "Graphiti's own rendered prompt"},
            SimpleNamespace(role="assistant", content="Graphiti's prior turn"),
        ]

        payload = runner.build_chat_request(
            model="gpt-5.4-mini",
            messages=messages,
            max_tokens=384,
        )

        self.assertEqual(
            payload["messages"],
            [
                {"role": "system", "content": "Graphiti's own system message"},
                {"role": "user", "content": "Graphiti's own rendered prompt"},
                {"role": "assistant", "content": "Graphiti's prior turn"},
            ],
        )
        self.assertEqual(len(payload["messages"]), len(messages))
        self.assertEqual(payload["model"], "gpt-5.4-mini")
        self.assertEqual(payload["max_tokens"], 384)
        for forbidden in (
            "temperature",
            "top_p",
            "seed",
            "extra_body",
            "chat_template_kwargs",
        ):
            self.assertNotIn(forbidden, payload)

    async def test_relay_client_uses_zero_retries_and_enforces_global_attempt_cap(self):
        transport = FakeTransport({"choices": [{"message": {"content": "{}"}}]})
        client = runner.RelayChatGraphitiClient(
            endpoint="https://relay.example.test/v1/chat/completions",
            api_key="synthetic-secret",
            model="gpt-5.4-mini",
            transport=transport,
            max_api_attempts=2,
        )
        messages = [SimpleNamespace(role="user", content="Graphiti prompt")]

        await client.complete(messages=messages, max_tokens=128)
        await client.complete(messages=messages, max_tokens=128)
        with self.assertRaises(runner.ApiAttemptCapExceeded):
            await client.complete(messages=messages, max_tokens=128)

        self.assertEqual(client.attempt_count, 2)
        self.assertEqual(len(transport.calls), 2)
        for call in transport.calls:
            self.assertEqual(call["max_retries"], 0)
            payload = call["payload"]
            for forbidden in ("temperature", "top_p", "seed", "extra_body"):
                self.assertNotIn(forbidden, payload)

    async def test_transport_failure_consumes_one_attempt_without_hidden_retry(self):
        transport = FakeTransport(TimeoutError("synthetic timeout"))
        client = runner.RelayChatGraphitiClient(
            endpoint="https://relay.example.test/v1/chat/completions",
            api_key="synthetic-secret",
            model="gpt-5.4-mini",
            transport=transport,
            max_api_attempts=3,
        )

        with self.assertRaises(TimeoutError):
            await client.complete(
                messages=[SimpleNamespace(role="user", content="Graphiti prompt")],
                max_tokens=64,
            )

        self.assertEqual(client.attempt_count, 1)
        self.assertEqual(len(transport.calls), 1)
        self.assertEqual(transport.calls[0]["max_retries"], 0)


class BoundedLifecycleTests(IsolatedAsyncioTestCase):
    """Run one fake episode and protect cleanup, restore, and checkpoints."""

    async def _run(
        self,
        root: Path,
        *,
        failure: BaseException | None = None,
    ):
        events: list[str] = []
        graphiti = FakeGraphiti(events, failure=failure)
        instrumentor = FakeInstrumentor(events)
        cleanup_calls: list[str] = []

        async def cleanup_group(*, group_id: str) -> None:
            events.append("cleanup.group")
            cleanup_calls.append(group_id)

        config = runner.BoundedRunConfig(
            attempt_id="api-char-unit-lifecycle",
            artifact_root=root,
            max_api_attempts=4,
        )
        coroutine = runner.run_bounded(
            config=config,
            episode_loader=fake_episode_loader,
            graphiti_factory=lambda _: graphiti,
            instrumentor=instrumentor,
            cleanup_group=cleanup_group,
        )
        return config, graphiti, instrumentor, cleanup_calls, events, coroutine

    async def test_success_calls_exactly_one_add_episode_and_scoped_cleanup(self):
        with tempfile.TemporaryDirectory() as raw_tmp:
            root = Path(raw_tmp) / "artifacts"
            config, graphiti, instrumentor, cleanup_calls, events, coroutine = (
                await self._run(root)
            )
            result = await coroutine
            run_dir = root / config.attempt_id

            self.assertEqual(result["status"], "success")
            self.assertEqual(len(graphiti.add_calls), 1)
            self.assertEqual(
                graphiti.add_calls[0]["group_id"], config.graph_namespace
            )
            self.assertEqual(cleanup_calls, [config.graph_namespace])
            self.assertIs(instrumentor.installed_on, graphiti)
            self.assertTrue(graphiti.closed)
            self.assertEqual(graphiti.close_count, 1)
            self.assertLess(
                events.index("instrumentation.install"),
                events.index("graphiti.add_episode"),
            )
            self.assertLess(
                events.index("graphiti.add_episode"),
                events.index("instrumentation.restore"),
            )
            self.assertLess(
                events.index("cleanup.group"),
                events.index("graphiti.close"),
                "scoped cleanup must use the still-open local Neo4j driver",
            )
            self.assertTrue((run_dir / "00_manifest.json").is_file())
            self.assertTrue((run_dir / "04_summary.json").is_file())
            self.assertGreaterEqual(len(list(run_dir.glob("*.json"))), 4)
            for artifact in run_dir.glob("*.json"):
                json.loads(artifact.read_text(encoding="utf-8"))
            self.assertFalse((root / "CURRENT_STATE.json").exists())

    async def test_claims_resource_before_artifact_dir_and_closes_on_claim_failure(self):
        with tempfile.TemporaryDirectory() as raw_tmp:
            root = Path(raw_tmp) / "artifacts"
            events: list[str] = []
            graphiti = FakeGraphiti(events)
            handoff = FakeHandoff(graphiti, events)
            config = runner.BoundedRunConfig(
                attempt_id="api-char-prepare-failure",
                artifact_root=root,
            )

            with patch.object(
                runner,
                "prepare_attempt_dir",
                side_effect=RuntimeError("synthetic artifact claim failure"),
            ):
                with self.assertRaisesRegex(RuntimeError, "artifact claim failure"):
                    await runner.run_bounded(
                        config=config,
                        episode_loader=fake_episode_loader,
                        graphiti_factory=lambda _: graphiti,
                        instrumentor=FakeInstrumentor(events),
                        cleanup_group=lambda **_kwargs: events.append("cleanup.group"),
                        resource_handoff=handoff,
                    )

            self.assertEqual(handoff.claim_count, 1)
            self.assertEqual(graphiti.close_count, 1)
            self.assertEqual(
                events,
                ["resource_handoff.claim", "cleanup.group", "graphiti.close"],
            )

    async def test_episode_loader_failure_after_handoff_closes_exactly_once(self):
        with tempfile.TemporaryDirectory() as raw_tmp:
            root = Path(raw_tmp) / "artifacts"
            events: list[str] = []
            graphiti = FakeGraphiti(events)
            handoff = FakeHandoff(graphiti, events)
            config = runner.BoundedRunConfig(
                attempt_id="api-char-loader-failure",
                artifact_root=root,
            )

            def failed_loader(**_kwargs):
                events.append("episode_loader")
                raise RuntimeError("synthetic episode loader failure")

            with self.assertRaisesRegex(RuntimeError, "episode loader failure"):
                await runner.run_bounded(
                    config=config,
                    episode_loader=failed_loader,
                    graphiti_factory=lambda _: graphiti,
                    instrumentor=FakeInstrumentor(events),
                    cleanup_group=lambda **_kwargs: events.append("cleanup.group"),
                    resource_handoff=handoff,
                )

            self.assertEqual(handoff.claim_count, 1)
            self.assertEqual(graphiti.close_count, 1)
            summary = json.loads(
                (root / config.attempt_id / "04_summary.json").read_text(
                    encoding="utf-8"
                )
            )
            self.assertEqual(summary["terminal_failure_phase"], "episode_load")
            self.assertEqual(
                events,
                [
                    "resource_handoff.claim",
                    "episode_loader",
                    "cleanup.group",
                    "graphiti.close",
                ],
            )

    async def test_partial_instrumentation_install_failure_restores_once(self):
        with tempfile.TemporaryDirectory() as raw_tmp:
            root = Path(raw_tmp) / "artifacts"
            events: list[str] = []
            graphiti = FakeGraphiti(events)

            class PartialInstallInstrumentor(FakeInstrumentor):
                def install(self, graphiti):
                    self.installed_on = graphiti
                    self.events.append("instrumentation.install")
                    raise RuntimeError("synthetic partial install failure")

            instrumentor = PartialInstallInstrumentor(events)
            config = runner.BoundedRunConfig(
                attempt_id="api-char-install-failure",
                artifact_root=root,
            )

            with self.assertRaisesRegex(RuntimeError, "partial install failure"):
                await runner.run_bounded(
                    config=config,
                    episode_loader=fake_episode_loader,
                    graphiti_factory=lambda _: graphiti,
                    instrumentor=instrumentor,
                    cleanup_group=lambda **_kwargs: events.append("cleanup.group"),
                )

            self.assertEqual(instrumentor.restore_count, 1)
            self.assertEqual(graphiti.close_count, 1)
            self.assertNotIn("graphiti.add_episode", events)
            summary = json.loads(
                (root / config.attempt_id / "04_summary.json").read_text(
                    encoding="utf-8"
                )
            )
            self.assertEqual(
                summary["terminal_failure_phase"],
                "instrumentor_install",
            )

    async def test_scope_exit_failure_after_add_preserves_completed_count(self):
        with tempfile.TemporaryDirectory() as raw_tmp:
            root = Path(raw_tmp) / "artifacts"
            events: list[str] = []
            graphiti = FakeGraphiti(events)

            class FailingScope:
                def __enter__(self):
                    events.append("scope.enter")

                def __exit__(self, exc_type, exc, traceback):
                    events.append("scope.exit")
                    raise RuntimeError("synthetic scope exit failure")

            class ScopedInstrumentor(FakeInstrumentor):
                def episode_scope(self):
                    return FailingScope()

            instrumentor = ScopedInstrumentor(events)
            config = runner.BoundedRunConfig(
                attempt_id="api-char-scope-exit-failure",
                artifact_root=root,
            )

            with self.assertRaisesRegex(RuntimeError, "scope exit failure"):
                await runner.run_bounded(
                    config=config,
                    episode_loader=fake_episode_loader,
                    graphiti_factory=lambda _: graphiti,
                    instrumentor=instrumentor,
                    cleanup_group=lambda **_kwargs: events.append("cleanup.group"),
                )

            summary = json.loads(
                (root / config.attempt_id / "04_summary.json").read_text(
                    encoding="utf-8"
                )
            )
            self.assertEqual(summary["status"], "failed")
            self.assertEqual(summary["episode_status"], "success")
            self.assertEqual(summary["completed_add_episode_count"], 1)
            self.assertEqual(instrumentor.restore_count, 1)
            self.assertEqual(graphiti.close_count, 1)

    async def test_error_restores_instrumentation_and_persists_failure_checkpoint(self):
        failure = RuntimeError("synthetic add failure")
        with tempfile.TemporaryDirectory() as raw_tmp:
            root = Path(raw_tmp) / "artifacts"
            config, graphiti, _, cleanup_calls, events, coroutine = await self._run(
                root, failure=failure
            )

            with self.assertRaisesRegex(RuntimeError, "synthetic add failure"):
                await coroutine

            self.assertEqual(len(graphiti.add_calls), 1)
            self.assertEqual(cleanup_calls, [config.graph_namespace])
            self.assertIn("instrumentation.restore", events)
            run_dir = root / config.attempt_id
            summary_path = run_dir / "04_summary.json"
            self.assertTrue((run_dir / "00_manifest.json").is_file())
            self.assertTrue(summary_path.is_file())
            self.assertGreaterEqual(len(list(run_dir.glob("*.json"))), 3)
            summary = json.loads(summary_path.read_text(encoding="utf-8"))
            self.assertEqual(summary["status"], "failed")
            self.assertEqual(summary["completed_add_episode_count"], 0)
            self.assertFalse((root / "CURRENT_STATE.json").exists())

    async def test_cleanup_failure_after_success_preserves_completed_count(self):
        with tempfile.TemporaryDirectory() as raw_tmp:
            root = Path(raw_tmp) / "artifacts"
            events: list[str] = []
            graphiti = FakeGraphiti(events)
            config = runner.BoundedRunConfig(
                attempt_id="api-char-cleanup-failure",
                artifact_root=root,
            )

            async def cleanup_group(**_kwargs):
                events.append("cleanup.group")
                raise RuntimeError("synthetic cleanup failure")

            with self.assertRaisesRegex(RuntimeError, "cleanup failure"):
                await runner.run_bounded(
                    config=config,
                    episode_loader=fake_episode_loader,
                    graphiti_factory=lambda _: graphiti,
                    instrumentor=FakeInstrumentor(events),
                    cleanup_group=cleanup_group,
                )

            summary = json.loads(
                (root / config.attempt_id / "04_summary.json").read_text(
                    encoding="utf-8"
                )
            )
            self.assertEqual(summary["status"], "failed")
            self.assertEqual(summary["episode_status"], "success")
            self.assertEqual(summary["completed_add_episode_count"], 1)
            self.assertEqual(graphiti.close_count, 1)

    async def test_close_failure_after_success_preserves_completed_count(self):
        with tempfile.TemporaryDirectory() as raw_tmp:
            root = Path(raw_tmp) / "artifacts"
            events: list[str] = []
            graphiti = FakeGraphiti(
                events,
                close_failure=RuntimeError("synthetic graphiti close failure"),
            )
            config = runner.BoundedRunConfig(
                attempt_id="api-char-close-failure",
                artifact_root=root,
            )

            with self.assertRaisesRegex(RuntimeError, "graphiti close failure"):
                await runner.run_bounded(
                    config=config,
                    episode_loader=fake_episode_loader,
                    graphiti_factory=lambda _: graphiti,
                    instrumentor=FakeInstrumentor(events),
                    cleanup_group=lambda **_kwargs: events.append("cleanup.group"),
                )

            run_dir = root / config.attempt_id
            summary = json.loads(
                (run_dir / "04_summary.json").read_text(encoding="utf-8")
            )
            cleanup = json.loads(
                (run_dir / "03_cleanup.json").read_text(encoding="utf-8")
            )
            self.assertEqual(summary["status"], "failed")
            self.assertEqual(summary["episode_status"], "success")
            self.assertEqual(summary["completed_add_episode_count"], 1)
            self.assertEqual(cleanup["cleanup_status"], "success")
            self.assertEqual(cleanup["close_status"], "failed")
            self.assertEqual(graphiti.close_count, 1)

    async def test_cancel_restores_instrumentation_and_persists_cancel_checkpoint(self):
        failure = asyncio.CancelledError("synthetic operator interruption")
        with tempfile.TemporaryDirectory() as raw_tmp:
            root = Path(raw_tmp) / "artifacts"
            config, graphiti, _, cleanup_calls, events, coroutine = await self._run(
                root, failure=failure
            )

            with self.assertRaises(asyncio.CancelledError):
                await coroutine

            self.assertEqual(len(graphiti.add_calls), 1)
            self.assertEqual(cleanup_calls, [config.graph_namespace])
            self.assertIn("instrumentation.restore", events)
            run_dir = root / config.attempt_id
            summary_path = run_dir / "04_summary.json"
            self.assertTrue((run_dir / "00_manifest.json").is_file())
            self.assertTrue(summary_path.is_file())
            self.assertGreaterEqual(len(list(run_dir.glob("*.json"))), 3)
            summary = json.loads(summary_path.read_text(encoding="utf-8"))
            self.assertEqual(summary["status"], "cancelled")
            self.assertEqual(summary["completed_add_episode_count"], 0)
            self.assertFalse((root / "CURRENT_STATE.json").exists())


class SourceIsolationTests(TestCase):
    """Keep the temporary module incapable of database-wide cleanup/state writes."""

    def test_source_has_no_database_wide_clear_or_mainline_state_mutation(self):
        source = inspect.getsource(runner)
        normalized = " ".join(source.casefold().split())

        for forbidden in (
            "current_state.json",
            "clear_data",
            "clear_database",
            "delete_all",
            "detach delete n",
            "match (n)",
        ):
            self.assertNotIn(forbidden, normalized)
        self.assertIn("group_id", source)
