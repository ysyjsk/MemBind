import json
import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace
from unittest import IsolatedAsyncioTestCase, TestCase


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from experiment_runner import (  # noqa: E402
    ExperimentRunFailed,
    RunArtifactExists,
    cache_for_spec,
    embedding_cache_for_spec,
    run_experiment,
)
from embedding_cache import CachingCountingEmbedder, EmbeddingCache  # noqa: E402
from response_cache import PromptCache, PromptParts, UnexpectedPromptError  # noqa: E402


class GraphOps:
    async def clear_data(self, driver):
        driver.events.append("clear")
        driver.node_count = 0


class Driver:
    def __init__(self, events):
        self.events = events
        self.node_count = 2
        self.graph_ops = GraphOps()

    async def execute_query(self, query):
        self.events.append("count")
        return SimpleNamespace(records=[{"node_count": self.node_count}])


class Graphiti:
    def __init__(self):
        self.events = []
        self.driver = Driver(self.events)
        self.llm_client = SimpleNamespace(
            call_count=0,
            parse_failure_count=0,
            usage_totals={},
            failure_events=[],
        )
        self.embedder = SimpleNamespace(api_call_count=0, text_count=0, cache_hit_count=0)

    async def build_indices_and_constraints(self):
        self.events.append("indexes")

    async def add_episode(self, **_kwargs):
        self.events.append("warmup")
        self.driver.node_count = 1

    async def close(self):
        self.events.append("close")


def spec(mode="live"):
    return {
        "run_id": f"run-{mode}",
        "lane": "correctness" if mode != "live" else "performance",
        "mode": mode,
        "method": "M0",
        "question_id": "q",
        "repeat": 0,
    }


def instance():
    return {
        "question_id": "q",
        "question": "What changed?",
        "answer_session_ids": ["s0"],
        "haystack_sessions": [[{"role": "user", "content": "hello"}]],
        "haystack_dates": ["2026-01-01"],
        "haystack_session_ids": ["s0"],
    }


class CacheModeTests(TestCase):
    def test_capture_then_replay_share_file_and_live_disables_cache(self):
        with tempfile.TemporaryDirectory() as tmp:
            artifacts = Path(tmp)
            capture = cache_for_spec(spec("capture"), artifacts)
            capture.put(PromptParts("rev", {}, {}, "s", "u"), "{}", {}, {})
            replay = cache_for_spec(spec("replay"), artifacts)

            self.assertFalse(capture.read_only)
            self.assertTrue(replay.read_only)
            self.assertIsNotNone(replay.get(PromptParts("rev", {}, {}, "s", "u")))
            self.assertIsNone(cache_for_spec(spec("live"), artifacts))

    def test_capture_refuses_to_overwrite_an_existing_cache(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "prompt_cache" / "q.jsonl"
            path.parent.mkdir(parents=True)
            path.write_text("already exists\n", encoding="utf-8")

            with self.assertRaises(RunArtifactExists):
                cache_for_spec(spec("capture"), Path(tmp))

    def test_embedding_capture_then_replay_share_file_and_live_has_no_artifact(self):
        with tempfile.TemporaryDirectory() as tmp:
            artifacts = Path(tmp)
            capture = embedding_cache_for_spec(spec("capture"), artifacts)
            capture.put("same", [1.0, 2.0])
            replay = embedding_cache_for_spec(spec("replay"), artifacts)

            self.assertFalse(capture.read_only)
            self.assertTrue(replay.read_only)
            self.assertEqual(replay.get("same"), [1.0, 2.0])
            self.assertIsNone(embedding_cache_for_spec(spec("live"), artifacts))
            self.assertEqual(
                sorted((artifacts / "embedding_cache").glob("*.jsonl")),
                [artifacts / "embedding_cache" / "q.jsonl"],
            )

    def test_embedding_capture_refuses_to_overwrite_an_existing_cache(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "embedding_cache" / "q.jsonl"
            path.parent.mkdir(parents=True)
            path.write_text("already exists\n", encoding="utf-8")

            with self.assertRaises(RunArtifactExists):
                embedding_cache_for_spec(spec("capture"), Path(tmp))

    def test_embedding_replay_requires_capture_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaisesRegex(FileNotFoundError, "embedding capture cache"):
                embedding_cache_for_spec(spec("replay"), Path(tmp))


class RunExperimentTests(IsolatedAsyncioTestCase):
    async def test_run_uses_full_isolation_lifecycle_and_persists_outputs(self):
        with tempfile.TemporaryDirectory() as tmp:
            artifacts = Path(tmp)
            graphiti = Graphiti()

            async def service_check():
                graphiti.events.append("services")

            async def runner(runtime, episodes, *_args):
                runtime.events.append("run")
                runtime.driver.node_count = len(episodes) + 2

            async def exporter(runtime, episodes, group_id):
                runtime.events.append("export")
                return {"entities": [], "edges": [], "episodes": [], "canonical_graph_hash": "hash"}

            async def retriever(runtime, data, episodes):
                runtime.events.append("retrieve")
                return {"retrieved_episode_ids": [], "metrics": {"evidence_recall_at_10": 0.0}}

            result = await run_experiment(
                spec(),
                instance(),
                arrival_interval_ms=100,
                artifacts=artifacts,
                graphiti_factory=lambda prompt_cache=None, embedding_cache=None: graphiti,
                method_runners={"M0": runner},
                service_checker=service_check,
                graph_exporter=exporter,
                retrieval_evaluator=retriever,
            )

            self.assertEqual(result["status"], "success")
            self.assertEqual(
                graphiti.events,
                [
                    "services",
                    "clear",
                    "indexes",
                    "count",
                    "warmup",
                    "clear",
                    "count",
                    "run",
                    "export",
                    "retrieve",
                    "clear",
                    "count",
                    "close",
                ],
            )
            self.assertTrue((artifacts / "runs" / "run-live.json").exists())
            self.assertTrue((artifacts / "graphs" / "run-live.canonical.json").exists())
            self.assertTrue((artifacts / "retrieval" / "run-live.json").exists())

    async def test_failure_is_recorded_and_database_is_still_cleared_and_closed(self):
        with tempfile.TemporaryDirectory() as tmp:
            artifacts = Path(tmp)
            graphiti = Graphiti()
            graphiti.llm_client.failure_events = [
                {
                    "episode_key": ("run-live", 0),
                    "max_tokens": 4096,
                    "finish_reason": "length",
                    "raw_response": '{"broken":',
                    "error": "JSONDecodeError",
                }
            ]

            async def runner(runtime, *_args):
                runtime.events.append("run")
                runtime.driver.node_count = 9
                raise RuntimeError("construction failed")

            with self.assertRaises(ExperimentRunFailed):
                await run_experiment(
                    spec(),
                    instance(),
                    arrival_interval_ms=100,
                    artifacts=artifacts,
                    graphiti_factory=lambda prompt_cache=None, embedding_cache=None: graphiti,
                    method_runners={"M0": runner},
                    service_checker=lambda: _async_none(),
                )

            status = json.loads((artifacts / "runs" / "run-live.json").read_text())
            self.assertEqual(status["status"], "failed")
            self.assertIn("construction failed", status["error"])
            self.assertEqual(graphiti.driver.node_count, 0)
            self.assertEqual(graphiti.events[-3:], ["clear", "count", "close"])
            failure_path = artifacts / "llm_failures" / "run-live.json"
            self.assertTrue(failure_path.exists())
            failures = json.loads(failure_path.read_text())
            self.assertEqual(failures[0]["max_tokens"], 4096)

    async def test_replay_miss_diagnostics_are_persisted_even_when_run_fails(self):
        with tempfile.TemporaryDirectory() as tmp:
            artifacts = Path(tmp)
            cache_path = artifacts / "prompt_cache" / "q.jsonl"
            PromptCache(cache_path, read_only=False).put(
                PromptParts(
                    "rev",
                    {"prompt_name": "dedupe_edges.resolve_edge"},
                    {"title": "EdgeDuplicate"},
                    "system",
                    "cached user prompt",
                ),
                raw_response="{}",
                parsed_response={},
                token_usage={},
            )
            graphiti = Graphiti()

            def factory(prompt_cache=None, embedding_cache=None):
                graphiti.llm_client = SimpleNamespace(
                    inner=graphiti.llm_client,
                    cache=prompt_cache,
                )
                return graphiti

            async def runner(runtime, *_args):
                requested = PromptParts(
                    "rev",
                    {"prompt_name": "dedupe_edges.resolve_edge"},
                    {"title": "EdgeDuplicate"},
                    "system",
                    "unexpected user prompt",
                )
                diagnostic = runtime.llm_client.cache.record_unexpected(requested)
                raise UnexpectedPromptError(diagnostic["prompt_hash"], diagnostic)

            with self.assertRaises(ExperimentRunFailed):
                await run_experiment(
                    spec("replay"),
                    instance(),
                    arrival_interval_ms=100,
                    artifacts=artifacts,
                    graphiti_factory=factory,
                    method_runners={"M0": runner},
                    service_checker=lambda: _async_none(),
                )

            diagnostic_path = artifacts / "unexpected_prompts" / "run-replay.json"
            self.assertTrue(diagnostic_path.exists())
            payload = json.loads(diagnostic_path.read_text(encoding="utf-8"))
            self.assertEqual(payload["run_id"], "run-replay")
            self.assertEqual(payload["diagnostics"][0]["prompt_name"], "dedupe_edges.resolve_edge")
            status = json.loads(
                (artifacts / "runs" / "run-replay.json").read_text(encoding="utf-8")
            )
            self.assertEqual(status["unexpected_prompt_diagnostics_path"], str(diagnostic_path))

    async def test_search_forensics_are_persisted_before_failed_run_cleanup(self):
        with tempfile.TemporaryDirectory() as tmp:
            artifacts = Path(tmp)
            graphiti = Graphiti()
            graphiti.driver.search_forensic_events = [
                {
                    "episode_key": ["run-live", 8],
                    "kind": "node_cosine_search",
                    "parameters": {"search_vector_sha256": "vector-hash"},
                    "backend_candidates": [{"name": "SDG"}],
                }
            ]
            graphiti.driver.source_state_events = [
                {
                    "run_id": "run-live",
                    "source_sequence": 8,
                    "logical_graph_hash": "graph-hash",
                }
            ]

            async def runner(runtime, *_args):
                runtime.driver.node_count = 9
                raise RuntimeError("diagnostic failure")

            with self.assertRaises(ExperimentRunFailed):
                await run_experiment(
                    spec(),
                    instance(),
                    arrival_interval_ms=100,
                    artifacts=artifacts,
                    graphiti_factory=lambda prompt_cache=None, embedding_cache=None: graphiti,
                    method_runners={"M0": runner},
                    service_checker=lambda: _async_none(),
                )

            diagnostic_path = artifacts / "search_forensics" / "run-live.json"
            self.assertTrue(diagnostic_path.exists())
            payload = json.loads(diagnostic_path.read_text(encoding="utf-8"))
            self.assertEqual(payload["query_events"][0]["backend_candidates"], [{"name": "SDG"}])
            self.assertEqual(payload["source_states"][0]["logical_graph_hash"], "graph-hash")
            status = json.loads(
                (artifacts / "runs" / "run-live.json").read_text(encoding="utf-8")
            )
            self.assertEqual(status["search_forensics_path"], str(diagnostic_path))

    async def test_embedding_replay_miss_diagnostic_is_persisted_without_live_call(self):
        class Inner:
            def __init__(self):
                self.calls = 0

            async def create(self, _value):
                self.calls += 1
                return [999.0]

            async def create_batch(self, _values):
                self.calls += 1
                return [[999.0]]

        with tempfile.TemporaryDirectory() as tmp:
            artifacts = Path(tmp)
            prompt_path = artifacts / "prompt_cache" / "q.jsonl"
            PromptCache(prompt_path, read_only=False).put(
                PromptParts("rev", {}, {}, "s", "u"), "{}", {}, {}
            )
            embedding_path = artifacts / "embedding_cache" / "q.jsonl"
            EmbeddingCache(embedding_path, read_only=False).put("known", [1.0])
            graphiti = Graphiti()
            inner = Inner()

            def factory(prompt_cache=None, embedding_cache=None):
                graphiti.embedder = CachingCountingEmbedder(
                    inner,
                    persistent_cache=embedding_cache,
                )
                return graphiti

            async def runner(runtime, *_args):
                await runtime.embedder.create(["missing secret text"])

            with self.assertRaises(ExperimentRunFailed):
                await run_experiment(
                    spec("replay"),
                    instance(),
                    arrival_interval_ms=100,
                    artifacts=artifacts,
                    graphiti_factory=factory,
                    method_runners={"M0": runner},
                    service_checker=lambda: _async_none(),
                )

            self.assertEqual(inner.calls, 0)
            diagnostic_path = artifacts / "unexpected_embeddings" / "run-replay.json"
            self.assertTrue(diagnostic_path.exists())
            payload = json.loads(diagnostic_path.read_text(encoding="utf-8"))
            self.assertEqual(payload["run_id"], "run-replay")
            self.assertEqual(payload["diagnostics"][0]["text_length"], 19)
            self.assertNotIn("missing secret text", diagnostic_path.read_text(encoding="utf-8"))
            status = json.loads(
                (artifacts / "runs" / "run-replay.json").read_text(encoding="utf-8")
            )
            self.assertEqual(
                status["unexpected_embedding_diagnostics_path"],
                str(diagnostic_path),
            )


async def _async_none():
    return None


if __name__ == "__main__":
    import unittest

    unittest.main()
