"""Offline contracts for the executable GPT-5.4-mini production wiring."""

from __future__ import annotations

import json
import tempfile
from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace
from unittest import IsolatedAsyncioTestCase, TestCase
from unittest.mock import AsyncMock, patch

from gpt55_temporary.api_characterization import production_runner as production
from gpt55_temporary.simple_judge.config_chat_judge import RelayConfig


FROZEN_SHA = "be983c489b10deea9c4d860f1e3203e4fa5d964154e004b814b2b5fee410156a"


class FakeStructuredTransport:
    def __init__(self, response=None, failure: BaseException | None = None) -> None:
        self.response = response or {
            "model": "gpt-5.4-mini",
            "choices": [
                {
                    "message": {"content": '{"compatible":true}'},
                    "finish_reason": "stop",
                }
            ],
        }
        self.failure = failure
        self.calls: list[dict[str, object]] = []
        self.closed = False

    async def post_json(self, **kwargs):
        self.calls.append(kwargs)
        if self.failure is not None:
            raise self.failure
        return self.response

    async def close(self) -> None:
        self.closed = True


class StructuredPreflightTests(IsolatedAsyncioTestCase):
    async def test_one_user_message_zero_retry_and_sanitized_artifact(self):
        relay = RelayConfig(
            base_url="https://relay.example.test/v1",
            api_key="synthetic-runtime-secret",
            model="gpt-5.4-mini",
            wire_api="chat",
            config_declared_wire_api="responses",
            provider_name="synthetic",
            timeout_s=10,
        )
        transport = FakeStructuredTransport()
        with tempfile.TemporaryDirectory() as raw_tmp:
            run_dir = Path(raw_tmp)
            report = await production.run_structured_chat_preflight(
                relay_config=relay,
                run_dir=run_dir,
                transport_factory=lambda _relay: transport,
            )
            artifact_text = (run_dir / "01_structured_chat_preflight.json").read_text(
                encoding="utf-8"
            )

        self.assertTrue(report["ok"])
        self.assertEqual(report["attempt_count"], 1)
        self.assertTrue(transport.closed)
        self.assertEqual(len(transport.calls), 1)
        call = transport.calls[0]
        self.assertEqual(call["max_retries"], 0)
        payload = call["payload"]
        self.assertEqual([item["role"] for item in payload["messages"]], ["user"])
        self.assertEqual(payload["response_format"]["type"], "json_schema")
        for forbidden in ("temperature", "top_p", "seed", "extra_body"):
            self.assertNotIn(forbidden, payload)
        self.assertNotIn("synthetic-runtime-secret", artifact_text)
        self.assertNotIn(payload["messages"][0]["content"], artifact_text)
        self.assertNotIn('{"compatible":true}', artifact_text)


class ProductionOuterGateTests(IsolatedAsyncioTestCase):
    async def test_failed_chat_attempt_does_not_read_config_dataset_gpu_or_database(self):
        with tempfile.TemporaryDirectory() as raw_tmp:
            root = Path(raw_tmp)
            preflight = root / "failed-chat"
            preflight.mkdir()
            (preflight / "00_manifest.json").write_text(
                json.dumps(
                    {
                        "model": "gpt-5.4-mini",
                        "provider_name": "synthetic",
                        "endpoint": "https://relay.example.test/chat/completions",
                        "effective_wire_api": "chat",
                    }
                ),
                encoding="utf-8",
            )
            (preflight / "02_transport.json").write_text(
                json.dumps({"http_status": 403, "attempt_count": 1}),
                encoding="utf-8",
            )
            (preflight / "04_summary.json").write_text(
                json.dumps(
                    {
                        "status": "failed",
                        "returned_model": None,
                        "attempt_count": 1,
                    }
                ),
                encoding="utf-8",
            )
            config = production.ProductionRunConfig(
                attempt_id="production-gate-403",
                preflight_attempt_dir=preflight,
                artifact_root=root / "live",
            )
            forbidden = AssertionError("live dependency crossed the failed Chat gate")
            with (
                patch.object(production, "load_relay_config", side_effect=forbidden),
                patch.object(production, "_default_episode_loader", side_effect=forbidden),
                patch.object(production, "_default_embedding_factory", side_effect=forbidden),
                patch.object(production, "_default_neo4j_factory", side_effect=forbidden),
                patch.object(production, "_build_graphiti", side_effect=forbidden),
            ):
                result = await production.execute_production_run(config)

        self.assertEqual(result["status"], "blocked_preflight")
        self.assertEqual(result["http_status"], 403)
        self.assertFalse(result["live_dependency_construction_started"])


@dataclass(frozen=True)
class SourceEpisode:
    question_id: str = "07741c45"
    source_sequence: int = 0
    source_hash: str = FROZEN_SHA
    name: str = "07741c45::episode::0000"
    body: str = "private source body"
    reference_time: str = "2026-08-11T00:00:00Z"


class FrozenProductionEpisodeTests(TestCase):
    def test_loader_converts_only_the_frozen_episode_for_graphiti(self):
        source = SourceEpisode()
        records = [{"question_id": "07741c45"}]

        episode = production.load_production_episode(
            dataset_path=Path("/synthetic/data.json"),
            records_loader=lambda _path: records,
            episode_builder=lambda record: [source],
            reference_time_parser=lambda value: f"parsed:{value}",
            episode_source="message-enum",
        )

        self.assertEqual(episode.question_id, "07741c45")
        self.assertEqual(episode.source_sequence, 0)
        self.assertEqual(episode.source_hash, FROZEN_SHA)
        self.assertEqual(episode.episode_body, source.body)
        self.assertEqual(episode.reference_time, f"parsed:{source.reference_time}")
        self.assertEqual(episode.source, "message-enum")


class LocalNeo4jAndCleanupTests(IsolatedAsyncioTestCase):
    def test_neo4j_uri_must_resolve_to_the_local_host(self):
        self.assertEqual(
            production.validate_local_neo4j_uri("bolt://localhost:7687"),
            "bolt://localhost:7687",
        )
        for remote in (
            "bolt://10.87.5.247:7687",
            "neo4j://db.example.test:7687",
            "https://localhost:7474",
        ):
            with self.assertRaises(ValueError):
                production.validate_local_neo4j_uri(remote)

    async def test_cleanup_is_predicated_on_the_exact_attempt_group(self):
        calls: list[tuple[str, dict[str, object]]] = []

        class Driver:
            async def execute_query(self, query, **kwargs):
                calls.append((query, kwargs))

        await production.cleanup_attempt_group(
            driver=Driver(),
            group_id="tmp-api-char-aabbccdd",
            expected_group_id="tmp-api-char-aabbccdd",
        )

        self.assertEqual(len(calls), 1)
        query, kwargs = calls[0]
        self.assertIn("WHERE node.group_id = $group_id", query)
        self.assertEqual(kwargs["params"], {"group_id": "tmp-api-char-aabbccdd"})
        with self.assertRaises(ValueError):
            await production.cleanup_attempt_group(
                driver=Driver(),
                group_id="wrong-group",
                expected_group_id="tmp-api-char-aabbccdd",
            )

    async def test_freshness_gate_queries_only_the_exact_attempt_group(self):
        calls: list[tuple[str, dict[str, object]]] = []

        class Driver:
            async def execute_query(self, query, **kwargs):
                calls.append((query, kwargs))
                return SimpleNamespace(records=[{"node_count": 0}])

        await production.assert_attempt_group_empty(
            driver=Driver(),
            group_id="tmp-api-char-aabbccdd",
            expected_group_id="tmp-api-char-aabbccdd",
        )

        self.assertEqual(len(calls), 1)
        query, kwargs = calls[0]
        self.assertIn("WHERE node.group_id = $group_id", query)
        self.assertIn("RETURN count(node) AS node_count", query)
        self.assertEqual(kwargs["params"], {"group_id": "tmp-api-char-aabbccdd"})

    async def test_freshness_gate_fails_closed_on_residue_or_malformed_result(self):
        class Driver:
            def __init__(self, result):
                self.result = result

            async def execute_query(self, _query, **_kwargs):
                return self.result

        with self.assertRaises(production.AttemptNamespaceNotEmptyError):
            await production.assert_attempt_group_empty(
                driver=Driver(SimpleNamespace(records=[{"node_count": 1}])),
                group_id="tmp-api-char-aabbccdd",
                expected_group_id="tmp-api-char-aabbccdd",
            )
        for malformed in (
            SimpleNamespace(records=[]),
            SimpleNamespace(records=[{}]),
            SimpleNamespace(records=[{"node_count": True}]),
        ):
            with self.assertRaises(RuntimeError):
                await production.assert_attempt_group_empty(
                    driver=Driver(malformed),
                    group_id="tmp-api-char-aabbccdd",
                    expected_group_id="tmp-api-char-aabbccdd",
                )
        with self.assertRaises(ValueError):
            await production.assert_attempt_group_empty(
                driver=Driver(SimpleNamespace(records=[{"node_count": 0}])),
                group_id="wrong-group",
                expected_group_id="tmp-api-char-aabbccdd",
            )

    async def test_production_runner_checks_freshness_before_bounded_add(self):
        events: list[str] = []

        class Driver:
            async def execute_query(self, query, **kwargs):
                events.append("namespace_query")
                return SimpleNamespace(records=[{"node_count": 0}])

        relay = RelayConfig(
            base_url="https://relay.example.test/v1",
            api_key="synthetic-runtime-secret",
            model="gpt-5.4-mini",
            wire_api="chat",
            config_declared_wire_api="responses",
            provider_name="synthetic",
            timeout_s=10,
        )
        context = production.ProductionContext(
            relay=relay,
            episode=production.ProductionEpisode(
                question_id="07741c45",
                source_sequence=0,
                source_hash=FROZEN_SHA,
                name="07741c45::episode::0000",
                episode_body="private source body",
                source_description="synthetic",
                reference_time="synthetic-time",
                source="message-enum",
            ),
            neo4j=production.Neo4jCredentials(
                uri="bolt://localhost:7687",
                user="neo4j",
                password="synthetic-password",
            ),
        )

        async def fake_live_experiment(**kwargs):
            return await kwargs["experiment_runner"](
                graphiti=SimpleNamespace(driver=Driver()),
                dataset=context,
                run_dir=Path("/unused"),
                resource_handoff=SimpleNamespace(name="synthetic-handoff"),
            )

        async def fake_run_bounded(**kwargs):
            self.assertEqual(events, ["namespace_query"])
            self.assertEqual(
                kwargs["resource_handoff"].name,
                "synthetic-handoff",
            )
            events.append("bounded_add")
            return {"status": "success"}

        config = production.ProductionRunConfig(
            attempt_id="freshness-order",
            preflight_attempt_dir=Path("/unused/preflight"),
            artifact_root=Path("/unused/artifacts"),
        )
        with (
            patch.object(production, "run_live_experiment", new=fake_live_experiment),
            patch.object(production, "run_bounded", new=fake_run_bounded),
        ):
            result = await production.execute_production_run(config)

        self.assertEqual(result, {"status": "success"})
        self.assertEqual(events, ["namespace_query", "bounded_add"])


class GraphitiConstructionPrivacyTests(IsolatedAsyncioTestCase):
    async def test_graphiti_disables_raw_episode_content_storage(self):
        captured: dict[str, object] = {}

        class FakeGraphiti:
            def __init__(self, **kwargs):
                captured.update(kwargs)

            async def close(self):
                await captured["graph_driver"].close()

        relay = RelayConfig(
            base_url="https://relay.example.test/v1",
            api_key="synthetic-runtime-secret",
            model="gpt-5.4-mini",
            wire_api="chat",
            config_declared_wire_api="responses",
            provider_name="synthetic",
            timeout_s=10,
        )
        context = production.ProductionContext(
            relay=relay,
            episode=SimpleNamespace(),
            neo4j=production.Neo4jCredentials(
                uri="bolt://localhost:7687",
                user="neo4j",
                password="synthetic-password",
            ),
        )
        transport = SimpleNamespace(close=AsyncMock())
        neo4j = SimpleNamespace(close=AsyncMock())
        with (
            patch("graphiti_core.Graphiti", new=FakeGraphiti),
            patch.object(
                production,
                "AsyncOpenAIChatTransport",
                return_value=transport,
            ),
        ):
            graphiti = await production._build_graphiti(
                context=context,
                embedding=object(),
                neo4j=neo4j,
                config=production.ProductionRunConfig(
                    attempt_id="privacy-contract",
                    preflight_attempt_dir=Path("/unused/preflight"),
                ),
            )
            await graphiti.close()

        self.assertIs(captured["store_raw_episode_content"], False)
        neo4j.close.assert_awaited_once()
        transport.close.assert_awaited_once()

    async def test_failed_graphiti_construction_leaves_neo4j_for_outer_owner(self):
        neo4j = SimpleNamespace(close=AsyncMock())
        transport = SimpleNamespace(close=AsyncMock())
        relay = RelayConfig(
            base_url="https://relay.example.test/v1",
            api_key="synthetic-runtime-secret",
            model="gpt-5.4-mini",
            wire_api="chat",
            config_declared_wire_api="responses",
            provider_name="synthetic",
            timeout_s=10,
        )
        context = production.ProductionContext(
            relay=relay,
            episode=SimpleNamespace(),
            neo4j=production.Neo4jCredentials(
                uri="bolt://localhost:7687",
                user="neo4j",
                password="synthetic-password",
            ),
        )

        with (
            patch(
                "graphiti_core.Graphiti",
                side_effect=RuntimeError("synthetic Graphiti constructor failure"),
            ),
            patch.object(
                production,
                "AsyncOpenAIChatTransport",
                return_value=transport,
            ),
        ):
            with self.assertRaisesRegex(RuntimeError, "Graphiti constructor"):
                await production._build_graphiti(
                    context=context,
                    embedding=object(),
                    neo4j=neo4j,
                    config=production.ProductionRunConfig(
                        attempt_id="construction-owner-contract",
                        preflight_attempt_dir=Path("/unused/preflight"),
                    ),
                )

        transport.close.assert_awaited_once()
        neo4j.close.assert_not_awaited()


class CrossEncoderFenceTests(IsolatedAsyncioTestCase):
    async def test_cross_encoder_cannot_inject_an_unplanned_prompt(self):
        fence = production.ForbiddenConstructionCrossEncoder()
        with self.assertRaises(production.UnplannedCrossEncoderCall):
            await fence.rank("query", ["passage"])
        self.assertEqual(fence.rank_call_count, 1)
