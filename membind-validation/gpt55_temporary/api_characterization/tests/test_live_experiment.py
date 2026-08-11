"""Offline TDD contracts for the immutable-preflight live orchestrator."""

from __future__ import annotations

import json
import tempfile
from pathlib import Path
from unittest import IsolatedAsyncioTestCase

from gpt55_temporary.api_characterization import live_experiment as experiment


def _write_preflight(
    root: Path,
    *,
    status: str,
    http_status: int,
    returned_model: str | None,
) -> dict[str, bytes]:
    root.mkdir(parents=True)
    payloads = {
        "00_manifest.json": {
            "model": "gpt-5.4-mini",
            "provider_name": "synthetic",
            "endpoint": "https://relay.example.test/chat/completions",
            "effective_wire_api": "chat",
        },
        "02_transport.json": {
            "http_status": http_status,
            "attempt_count": 1,
        },
        "04_summary.json": {
            "status": status,
            "returned_model": returned_model,
            "attempt_count": 1,
        },
    }
    original: dict[str, bytes] = {}
    for name, payload in payloads.items():
        encoded = (json.dumps(payload, sort_keys=True) + "\n").encode("utf-8")
        (root / name).write_bytes(encoded)
        original[name] = encoded
    return original


class LiveExperimentGateTests(IsolatedAsyncioTestCase):
    async def test_http_403_writes_blocked_checkpoint_before_any_live_factory(self):
        with tempfile.TemporaryDirectory() as raw_tmp:
            root = Path(raw_tmp)
            preflight_dir = root / "preflight" / "judge-403"
            originals = _write_preflight(
                preflight_dir,
                status="failed",
                http_status=403,
                returned_model=None,
            )
            artifact_root = root / "live-runs"
            forbidden_calls: list[str] = []

            def forbidden(name):
                def call(*args, **kwargs):
                    forbidden_calls.append(name)
                    raise AssertionError(f"{name} must not run after a rejected preflight")

                return call

            result = await experiment.run_live_experiment(
                config=experiment.LiveExperimentConfig(
                    attempt_id="live-gate-403",
                    preflight_attempt_dir=preflight_dir,
                    artifact_root=artifact_root,
                ),
                dataset_loader=forbidden("dataset"),
                embedding_factory=forbidden("embedding"),
                neo4j_factory=forbidden("neo4j"),
                graphiti_factory=forbidden("graphiti"),
                experiment_runner=forbidden("runner"),
            )

            self.assertEqual(result["status"], "blocked_preflight")
            self.assertEqual(result["http_status"], 403)
            self.assertEqual(forbidden_calls, [])
            checkpoint = json.loads(
                (artifact_root / "live-gate-403" / "checkpoint.json").read_text(
                    encoding="utf-8"
                )
            )
            self.assertEqual(checkpoint["status"], "blocked_preflight")
            self.assertEqual(checkpoint["http_status"], 403)
            self.assertFalse(checkpoint["mainline_state_advanced"])
            self.assertEqual(
                sorted(checkpoint["preflight_file_sha256"]),
                ["00_manifest.json", "02_transport.json", "04_summary.json"],
            )
            for name, encoded in originals.items():
                self.assertEqual((preflight_dir / name).read_bytes(), encoded)
            self.assertFalse((artifact_root / "CURRENT_STATE.json").exists())

    async def test_successful_artifact_is_read_before_runtime_dependencies(self):
        with tempfile.TemporaryDirectory() as raw_tmp:
            root = Path(raw_tmp)
            preflight_dir = root / "preflight" / "judge-success"
            _write_preflight(
                preflight_dir,
                status="success",
                http_status=200,
                returned_model="gpt-5.4-mini",
            )
            events: list[str] = []

            def preflight_reader(*, attempt_dir, expected_model):
                events.append("preflight")
                self.assertEqual(Path(attempt_dir), preflight_dir)
                self.assertEqual(expected_model, "gpt-5.4-mini")
                return {
                    "ok": True,
                    "status_code": 200,
                    "classification": "single_request_chat_compatible",
                    "model": expected_model,
                    "attempt_count": 1,
                }

            def dataset_loader():
                events.append("dataset")
                return object()

            def embedding_factory():
                events.append("embedding")
                return object()

            def neo4j_factory():
                events.append("neo4j")
                return object()

            def graphiti_factory(*, dataset, embedding, neo4j):
                events.append("graphiti")
                self.assertIsNotNone(dataset)
                self.assertIsNotNone(embedding)
                self.assertIsNotNone(neo4j)
                return object()

            async def experiment_runner(
                *, graphiti, dataset, run_dir, resource_handoff
            ):
                events.append("runner")
                self.assertIsNotNone(graphiti)
                self.assertIsNotNone(dataset)
                self.assertTrue(Path(run_dir).is_dir())
                return {"status": "success", "bounded": True}

            result = await experiment.run_live_experiment(
                config=experiment.LiveExperimentConfig(
                    attempt_id="live-gate-success",
                    preflight_attempt_dir=preflight_dir,
                    artifact_root=root / "live-runs",
                ),
                preflight_reader=preflight_reader,
                dataset_loader=dataset_loader,
                embedding_factory=embedding_factory,
                neo4j_factory=neo4j_factory,
                graphiti_factory=graphiti_factory,
                experiment_runner=experiment_runner,
            )

            self.assertEqual(result, {"status": "success", "bounded": True})
            self.assertEqual(
                events,
                ["preflight", "dataset", "embedding", "neo4j", "graphiti", "runner"],
            )

    async def test_graphiti_factory_failure_closes_neo4j_exactly_once(self):
        with tempfile.TemporaryDirectory() as raw_tmp:
            root = Path(raw_tmp)
            preflight_dir = root / "preflight" / "judge-success"
            _write_preflight(
                preflight_dir,
                status="success",
                http_status=200,
                returned_model="gpt-5.4-mini",
            )

            class Neo4j:
                close_count = 0

                async def close(self):
                    self.close_count += 1

            neo4j = Neo4j()

            def graphiti_factory(**_kwargs):
                raise RuntimeError("synthetic graphiti construction failure")

            with self.assertRaisesRegex(RuntimeError, "graphiti construction"):
                await experiment.run_live_experiment(
                    config=experiment.LiveExperimentConfig(
                        attempt_id="live-graphiti-construction-failure",
                        preflight_attempt_dir=preflight_dir,
                        artifact_root=root / "live-runs",
                    ),
                    preflight_reader=lambda **_kwargs: {
                        "ok": True,
                        "status_code": 200,
                        "attempt_count": 1,
                    },
                    dataset_loader=object,
                    embedding_factory=object,
                    neo4j_factory=lambda: neo4j,
                    graphiti_factory=graphiti_factory,
                    experiment_runner=lambda **_kwargs: self.fail("runner must not run"),
                )

            self.assertEqual(neo4j.close_count, 1)

    async def test_runner_failure_before_handoff_closes_graphiti_exactly_once(self):
        with tempfile.TemporaryDirectory() as raw_tmp:
            root = Path(raw_tmp)
            preflight_dir = root / "preflight" / "judge-success"
            _write_preflight(
                preflight_dir,
                status="success",
                http_status=200,
                returned_model="gpt-5.4-mini",
            )
            events: list[str] = []

            class OwnedResource:
                def __init__(self, name):
                    self.name = name
                    self.close_count = 0

                async def close(self):
                    self.close_count += 1
                    events.append(f"{self.name}.close")

            neo4j = OwnedResource("neo4j")
            transport = OwnedResource("transport")

            class Graphiti:
                close_count = 0

                async def close(self):
                    self.close_count += 1
                    events.append("graphiti.close")
                    await neo4j.close()
                    await transport.close()

            graphiti = Graphiti()

            async def experiment_runner(
                *, graphiti, dataset, run_dir, resource_handoff
            ):
                events.append("runner.before-handoff")
                raise RuntimeError("synthetic instrumentor construction failure")

            with self.assertRaisesRegex(RuntimeError, "instrumentor construction"):
                await experiment.run_live_experiment(
                    config=experiment.LiveExperimentConfig(
                        attempt_id="live-before-handoff-failure",
                        preflight_attempt_dir=preflight_dir,
                        artifact_root=root / "live-runs",
                    ),
                    preflight_reader=lambda **_kwargs: {
                        "ok": True,
                        "status_code": 200,
                        "attempt_count": 1,
                    },
                    dataset_loader=object,
                    embedding_factory=object,
                    neo4j_factory=lambda: neo4j,
                    graphiti_factory=lambda **_kwargs: graphiti,
                    experiment_runner=experiment_runner,
                )

            self.assertEqual(
                events,
                [
                    "runner.before-handoff",
                    "graphiti.close",
                    "neo4j.close",
                    "transport.close",
                ],
            )
            self.assertEqual(graphiti.close_count, 1)
            self.assertEqual(neo4j.close_count, 1)
            self.assertEqual(transport.close_count, 1)

    async def test_claimed_runner_failure_does_not_double_close_graphiti(self):
        with tempfile.TemporaryDirectory() as raw_tmp:
            root = Path(raw_tmp)
            preflight_dir = root / "preflight" / "judge-success"
            _write_preflight(
                preflight_dir,
                status="success",
                http_status=200,
                returned_model="gpt-5.4-mini",
            )

            class Graphiti:
                close_count = 0

                async def close(self):
                    self.close_count += 1

            graphiti = Graphiti()

            async def experiment_runner(
                *, graphiti, dataset, run_dir, resource_handoff
            ):
                resource_handoff.claim(graphiti)
                await graphiti.close()
                raise RuntimeError("synthetic bounded failure")

            with self.assertRaisesRegex(RuntimeError, "bounded failure"):
                await experiment.run_live_experiment(
                    config=experiment.LiveExperimentConfig(
                        attempt_id="live-after-handoff-failure",
                        preflight_attempt_dir=preflight_dir,
                        artifact_root=root / "live-runs",
                    ),
                    preflight_reader=lambda **_kwargs: {
                        "ok": True,
                        "status_code": 200,
                        "attempt_count": 1,
                    },
                    dataset_loader=object,
                    embedding_factory=object,
                    neo4j_factory=object,
                    graphiti_factory=lambda **_kwargs: graphiti,
                    experiment_runner=experiment_runner,
                )

            self.assertEqual(graphiti.close_count, 1)

    async def test_structured_compatibility_failure_blocks_before_local_dependencies(self):
        with tempfile.TemporaryDirectory() as raw_tmp:
            root = Path(raw_tmp)
            preflight_dir = root / "preflight" / "judge-success"
            _write_preflight(
                preflight_dir,
                status="success",
                http_status=200,
                returned_model="gpt-5.4-mini",
            )
            events: list[str] = []

            async def compatibility_preflight(*, run_dir):
                events.append("structured-chat-preflight")
                self.assertTrue(Path(run_dir).is_dir())
                return {
                    "ok": False,
                    "status_code": 400,
                    "classification": "json_schema_not_supported",
                    "attempt_count": 1,
                }

            def forbidden(name):
                def call(*args, **kwargs):
                    events.append(name)
                    raise AssertionError(
                        f"{name} must not run after structured Chat rejection"
                    )

                return call

            result = await experiment.run_live_experiment(
                config=experiment.LiveExperimentConfig(
                    attempt_id="live-structured-rejected",
                    preflight_attempt_dir=preflight_dir,
                    artifact_root=root / "live-runs",
                ),
                compatibility_preflight=compatibility_preflight,
                dataset_loader=forbidden("dataset"),
                embedding_factory=forbidden("embedding"),
                neo4j_factory=forbidden("neo4j"),
                graphiti_factory=forbidden("graphiti"),
                experiment_runner=forbidden("runner"),
            )

            self.assertEqual(events, ["structured-chat-preflight"])
            self.assertEqual(result["status"], "blocked_compatibility_preflight")
            self.assertEqual(result["http_status"], 400)
            self.assertEqual(result["attempt_count"], 1)
            self.assertFalse(result["live_dependency_construction_started"])
            self.assertFalse(result["mainline_state_advanced"])
