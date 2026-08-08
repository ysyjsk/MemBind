import json
import sys
import tempfile
from pathlib import Path
from unittest import IsolatedAsyncioTestCase


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from experiment_runner import run_experiment  # noqa: E402
from model_oracle_audit import (  # noqa: E402
    CrossEncoderAuditWrapper,
    model_oracle_audit_payload,
)
from tests.test_experiment_runner import Graphiti, instance, spec  # noqa: E402


class FakeCrossEncoder:
    def __init__(self, *, fail=False):
        self.fail = fail
        self.calls = []

    async def rank(self, query, passages):
        self.calls.append((query, list(passages)))
        if self.fail:
            raise RuntimeError("rank failed")
        return [(passage, float(index)) for index, passage in enumerate(passages)]


class ModelOracleAuditTests(IsolatedAsyncioTestCase):
    async def test_wrapper_preserves_result_and_records_only_safe_hashes(self):
        inner = FakeCrossEncoder()
        wrapper = CrossEncoderAuditWrapper(inner)

        result = await wrapper.rank("secret query", ["secret a", "secret b"])

        self.assertEqual(result, [("secret a", 0.0), ("secret b", 1.0)])
        self.assertEqual(wrapper.rank_call_count, 1)
        self.assertEqual(inner.calls, [("secret query", ["secret a", "secret b"])])
        encoded = json.dumps(wrapper.rank_events, sort_keys=True)
        self.assertNotIn("secret", encoded)
        self.assertEqual(wrapper.rank_events[0]["passage_count"], 2)
        self.assertEqual(len(wrapper.rank_events[0]["query_sha256"]), 64)

    async def test_failed_rank_is_still_counted_before_delegate(self):
        wrapper = CrossEncoderAuditWrapper(FakeCrossEncoder(fail=True))

        with self.assertRaisesRegex(RuntimeError, "rank failed"):
            await wrapper.rank("query", ["passage"])

        self.assertEqual(wrapper.rank_call_count, 1)
        self.assertEqual(wrapper.rank_events[0]["outcome"], "raised")

    async def test_zero_and_nonzero_payloads_use_measured_gate(self):
        zero = model_oracle_audit_payload(
            CrossEncoderAuditWrapper(FakeCrossEncoder()),
            run_id="zero",
        )
        self.assertEqual(zero["rank_call_count"], 0)
        self.assertEqual(zero["cross_encoder_status"], "not_invoked")
        self.assertFalse(zero["blocks_v2"])

        wrapper = CrossEncoderAuditWrapper(FakeCrossEncoder())
        await wrapper.rank("q", ["p"])
        nonzero = model_oracle_audit_payload(wrapper, run_id="one")
        self.assertEqual(nonzero["rank_call_count"], 1)
        self.assertEqual(
            nonzero["cross_encoder_status"],
            "invoked_requires_capture_replay",
        )
        self.assertTrue(nonzero["blocks_v2"])

    async def test_uninstrumented_cross_encoder_cannot_masquerade_as_zero_calls(self):
        with self.assertRaisesRegex(ValueError, "not instrumented"):
            model_oracle_audit_payload(
                FakeCrossEncoder(),
                run_id="uninstrumented",
            )

    async def test_runner_writes_audit_after_final_retrieval(self):
        with tempfile.TemporaryDirectory() as tmp:
            artifacts = Path(tmp)
            output = artifacts / "diagnostics" / "model_oracle_audit.json"
            graphiti = Graphiti()
            graphiti.cross_encoder = CrossEncoderAuditWrapper(FakeCrossEncoder())

            async def runner(*_args):
                return None

            async def retriever(runtime, *_args):
                await runtime.cross_encoder.rank(
                    "final secret query",
                    ["final secret passage"],
                )
                return {
                    "retrieved_episode_ids": [],
                    "metrics": {"evidence_recall_at_10": 0.0},
                }

            async def exporter(*_args):
                return {
                    "entities": [],
                    "edges": [],
                    "episodes": [],
                    "canonical_graph_hash": "hash",
                }

            result = await run_experiment(
                spec("live"),
                instance(),
                arrival_interval_ms=100,
                artifacts=artifacts,
                graphiti_factory=lambda prompt_cache=None, embedding_cache=None: graphiti,
                method_runners={"M0": runner},
                service_checker=lambda: _async_none(),
                graph_exporter=exporter,
                retrieval_evaluator=retriever,
                model_oracle_audit_path=output,
            )

            payload = json.loads(output.read_text(encoding="utf-8"))
            self.assertEqual(payload["rank_call_count"], 1)
            self.assertEqual(payload["phase_call_counts"], {"final_retrieval": 1})
            self.assertTrue(payload["blocks_v2"])
            self.assertNotIn("secret", output.read_text(encoding="utf-8"))
            self.assertEqual(result["model_oracle_audit_path"], str(output))
            self.assertEqual(result["rank_call_count"], 1)

    async def test_runner_persists_audit_when_final_retrieval_raises(self):
        with tempfile.TemporaryDirectory() as tmp:
            artifacts = Path(tmp)
            output = artifacts / "diagnostics" / "model_oracle_audit.json"
            graphiti = Graphiti()
            graphiti.cross_encoder = CrossEncoderAuditWrapper(FakeCrossEncoder())

            async def runner(*_args):
                return None

            async def retriever(runtime, *_args):
                await runtime.cross_encoder.rank("query", ["passage"])
                raise RuntimeError("retrieval failed after audit event")

            async def exporter(*_args):
                return {"entities": [], "edges": [], "episodes": [], "canonical_graph_hash": "hash"}

            with self.assertRaises(Exception):
                await run_experiment(
                    spec("live"),
                    instance(),
                    arrival_interval_ms=100,
                    artifacts=artifacts,
                    graphiti_factory=lambda prompt_cache=None, embedding_cache=None: graphiti,
                    method_runners={"M0": runner},
                    service_checker=lambda: _async_none(),
                    graph_exporter=exporter,
                    retrieval_evaluator=retriever,
                    model_oracle_audit_path=output,
                )

            payload = json.loads(output.read_text(encoding="utf-8"))
            self.assertEqual(payload["rank_call_count"], 1)
            self.assertEqual(payload["events"][0]["outcome"], "completed")


async def _async_none():
    return None
