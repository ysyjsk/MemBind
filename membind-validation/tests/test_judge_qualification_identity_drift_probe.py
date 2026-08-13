"""RED contracts for the formal Judge per-item deployment-identity probe.

All HTTP traffic terminates in ``httpx.MockTransport``.  The formal lane must
capture one initial identity and then re-read ``/v1/models`` immediately before
each pending chat dispatch.  Drift or an unavailable identity endpoint stops
before that item's durable dispatch intent; resume probes only the pending
suffix and never repeats a terminal item.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import socket
import sys
import tempfile
from pathlib import Path
from unittest import IsolatedAsyncioTestCase, mock

import httpx


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from evaluation.benchmarks.longmemeval import LongMemEvalAdapter  # noqa: E402
from evaluation.judge_qualification import (  # noqa: E402
    JudgeQualificationArtifactStore,
    build_strict_judge_qualification_freeze,
    run_judge_qualification,
    verify_judge_qualification_artifacts,
)
from evaluation.judge_qualification_live import (  # noqa: E402
    run_formal_judge_qualification,
)
from tests.test_judge_qualification import (  # noqa: E402
    QualificationHarness,
    _SequenceBackend,
    _evaluation_item,
    _success_result,
)


FIXTURE = ROOT / "fixtures/judge_qualification_14_v1.json"
OFFLINE_MANIFEST = ROOT / "artifacts/protocol/judge_upstream_manifest_20260812.json"
CORE_SOURCE = ROOT / "src/evaluation/judge_qualification.py"
LIVE_SOURCE = ROOT / "src/evaluation/judge_qualification_live.py"
DEPLOYMENT_EVIDENCE = (
    ROOT / "artifacts/environment/judge_deployment_evidence_20260813.json"
)
CANONICAL_INCOMPLETE = "incomplete_invalid_non_mergeable"


def _sha_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _models_response(*, root: str = "qwen3-32b-fp8") -> httpx.Response:
    return httpx.Response(
        200,
        json={
            "object": "list",
            "data": [
                {
                    "id": "qwen3-32b-fp8",
                    "object": "model",
                    "owned_by": "vllm",
                    "root": root,
                    "max_model_len": 65536,
                }
            ],
        },
    )


def _completion(label: str) -> httpx.Response:
    return httpx.Response(
        200,
        json={
            "id": "mock-per-item-identity-probe",
            "object": "chat.completion",
            "created": 0,
            "model": "qwen3-32b-fp8",
            "choices": [
                {
                    "index": 0,
                    "message": {"role": "assistant", "content": label},
                    "finish_reason": "stop",
                }
            ],
        },
    )


class JudgePerItemIdentityProbeRedTests(IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.freeze = build_strict_judge_qualification_freeze(
            validation_root=ROOT,
            fixture_path=FIXTURE.relative_to(ROOT),
            offline_manifest_path=OFFLINE_MANIFEST.relative_to(ROOT),
            qualification_source_path=CORE_SOURCE.relative_to(ROOT),
            qualification_live_source_path=LIVE_SOURCE.relative_to(ROOT),
        )
        self.deployment_binding = {
            "path": DEPLOYMENT_EVIDENCE.relative_to(ROOT).as_posix(),
            "sha256": _sha_file(DEPLOYMENT_EVIDENCE),
        }
        self.config = {
            "base_url": "http://judge.private.invalid/v1",
            "api_key": "PRIVATE-JUDGE-CREDENTIAL",
        }

    async def _formal_run(
        self,
        *,
        temporary: Path,
        run_id: str,
        models_handler: object,
        chat_handler: object,
    ) -> dict[str, object]:
        with mock.patch.object(
            socket.socket,
            "connect",
            side_effect=AssertionError("real network forbidden in identity-probe test"),
        ):
            return await run_formal_judge_qualification(
                validation_root=ROOT,
                runs_root=temporary,
                run_id=run_id,
                freeze=self.freeze,
                config_mapping=self.config,
                deployment_evidence_binding=self.deployment_binding,
                models_transport=httpx.MockTransport(models_handler),
                chat_transport=httpx.MockTransport(chat_handler),
            )

    async def test_fresh_14_item_run_awaits_15_models_gets_before_14_chat_posts(
        self,
    ) -> None:
        calls: list[str] = []
        labels = iter(
            "YES" if item["human_label"] else "NO" for item in self.freeze["items"]
        )

        async def models_handler(request: httpx.Request) -> httpx.Response:
            self.assertEqual((request.method, request.url.path), ("GET", "/v1/models"))
            await asyncio.sleep(0)
            calls.append("models")
            return _models_response()

        async def chat_handler(request: httpx.Request) -> httpx.Response:
            self.assertEqual(
                (request.method, request.url.path),
                ("POST", "/v1/chat/completions"),
            )
            await asyncio.sleep(0)
            calls.append("chat")
            return _completion(next(labels))

        with tempfile.TemporaryDirectory() as temporary:
            result = await self._formal_run(
                temporary=Path(temporary),
                run_id="jq-1010101010101010",
                models_handler=models_handler,
                chat_handler=chat_handler,
            )

        self.assertEqual(result["qualification_status"], "PASS")
        self.assertEqual(calls.count("models"), 15)
        self.assertEqual(calls.count("chat"), 14)
        self.assertEqual(calls[0:2], ["models", "models"])
        self.assertEqual(calls[2:], [value for _ in range(13) for value in ("chat", "models")] + ["chat"])

    async def test_drift_before_item_k_durably_stops_without_dispatching_item_k(
        self,
    ) -> None:
        failed_index = 4
        calls: list[str] = []
        labels = iter(
            "YES" if item["human_label"] else "NO" for item in self.freeze["items"]
        )
        models_call_count = 0

        async def models_handler(_request: httpx.Request) -> httpx.Response:
            nonlocal models_call_count
            models_call_count += 1
            calls.append("models")
            # Call 1 is the initial capture. Calls 2.. are per-item probes.
            if models_call_count == failed_index + 2:
                return _models_response(root="different-model-root")
            return _models_response()

        async def chat_handler(_request: httpx.Request) -> httpx.Response:
            calls.append("chat")
            return _completion(next(labels))

        with tempfile.TemporaryDirectory() as temporary:
            run_root = Path(temporary)
            result = await self._formal_run(
                temporary=run_root,
                run_id="jq-2020202020202020",
                models_handler=models_handler,
                chat_handler=chat_handler,
            )
            run_dir = run_root / "jq-2020202020202020"
            verification = verify_judge_qualification_artifacts(run_dir, self.freeze)
            checkpoint = json.loads(run_dir.joinpath("checkpoint.json").read_text("ascii"))
            events = [
                json.loads(line)
                for line in run_dir.joinpath("events.jsonl").read_text("ascii").splitlines()
            ]

        failed_item_id = self.freeze["items"][failed_index]["item_id"]
        self.assertEqual(calls.count("models"), failed_index + 2)
        self.assertEqual(calls.count("chat"), failed_index)
        self.assertEqual(result["attempt_status"], CANONICAL_INCOMPLETE)
        self.assertEqual(result["failure_class"], "runtime_identity_drift")
        self.assertEqual(result["failed_item_id"], failed_item_id)
        self.assertEqual(verification["attempt_status"], CANONICAL_INCOMPLETE)
        self.assertEqual(checkpoint["failure_class"], "runtime_identity_drift")
        self.assertEqual(checkpoint["terminal_item_count"], failed_index)
        self.assertNotIn(failed_item_id, {event["item_id"] for event in events})

    async def test_unavailable_probe_before_item_k_is_durable_and_sends_no_chat(
        self,
    ) -> None:
        failed_index = 0
        calls: list[str] = []
        models_call_count = 0

        async def models_handler(_request: httpx.Request) -> httpx.Response:
            nonlocal models_call_count
            models_call_count += 1
            calls.append("models")
            if models_call_count == 2:
                return httpx.Response(503, json={"error": "mock unavailable"})
            return _models_response()

        async def chat_handler(_request: httpx.Request) -> httpx.Response:
            calls.append("chat")
            return _completion("YES")

        with tempfile.TemporaryDirectory() as temporary:
            run_root = Path(temporary)
            result = await self._formal_run(
                temporary=run_root,
                run_id="jq-3030303030303030",
                models_handler=models_handler,
                chat_handler=chat_handler,
            )
            run_dir = run_root / "jq-3030303030303030"
            checkpoint = json.loads(run_dir.joinpath("checkpoint.json").read_text("ascii"))
            events = run_dir.joinpath("events.jsonl").read_text("ascii").splitlines()

        self.assertEqual(calls, ["models", "models"])
        self.assertEqual(result["attempt_status"], CANONICAL_INCOMPLETE)
        self.assertEqual(result["failure_class"], "runtime_identity_unavailable")
        self.assertEqual(
            result["failed_item_id"], self.freeze["items"][failed_index]["item_id"]
        )
        self.assertEqual(checkpoint["failure_class"], "runtime_identity_unavailable")
        self.assertEqual(checkpoint["terminal_item_count"], 0)
        self.assertEqual(events, [])

    async def test_resume_awaits_identity_only_for_pending_suffix(self) -> None:
        completed_prefix = 3
        with tempfile.TemporaryDirectory() as temporary:
            harness = QualificationHarness(Path(temporary))
            store = harness.create_store()
            for record in harness.items[:completed_prefix]:
                store.write_item_result(
                    item=_evaluation_item(record),
                    candidate_answer_id=str(record["candidate_answer_id"]),
                    human_label=bool(record["human_label"]),
                    result=_success_result(record),
                )
            resumed = JudgeQualificationArtifactStore.resume(
                run_dir=store.run_dir,
                freeze=harness.freeze,
            )
            backend = _SequenceBackend(
                [
                    "YES" if record["human_label"] else "NO"
                    for record in harness.items[completed_prefix:]
                ]
            )
            probe_count = 0

            async def identity_reader() -> dict[str, object]:
                nonlocal probe_count
                await asyncio.sleep(0)
                probe_count += 1
                return dict(harness.runtime_identity)

            result = await run_judge_qualification(
                freeze=harness.freeze,
                items=[_evaluation_item(record) for record in harness.items],
                evaluator=LongMemEvalAdapter(backend),
                store=resumed,
                runtime_identity_reader=identity_reader,
            )

        pending_count = 14 - completed_prefix
        self.assertEqual(result["qualification_status"], "PASS")
        self.assertEqual(probe_count, pending_count)
        self.assertEqual(len(backend.prompts), pending_count)


if __name__ == "__main__":
    import unittest

    unittest.main()
