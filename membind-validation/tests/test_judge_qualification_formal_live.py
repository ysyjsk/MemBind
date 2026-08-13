"""RED contracts for the one durable formal Judge qualification entry point."""

from __future__ import annotations

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

from evaluation.judge_qualification import (  # noqa: E402
    build_strict_judge_qualification_freeze,
    verify_judge_qualification_artifacts,
)
from evaluation.judge_qualification_live import run_formal_judge_qualification  # noqa: E402


FIXTURE = ROOT / "fixtures/judge_qualification_14_v1.json"
OFFLINE_MANIFEST = ROOT / "artifacts/protocol/judge_upstream_manifest_20260812.json"
CORE_SOURCE = ROOT / "src/evaluation/judge_qualification.py"
LIVE_SOURCE = ROOT / "src/evaluation/judge_qualification_live.py"
DEPLOYMENT_EVIDENCE = (
    ROOT / "artifacts/environment/judge_deployment_evidence_20260813.json"
)


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _models_response() -> httpx.Response:
    return httpx.Response(
        200,
        json={
            "object": "list",
            "data": [{
                "id": "qwen3-32b-fp8",
                "object": "model",
                "owned_by": "vllm",
                "root": "qwen3-32b-fp8",
                "max_model_len": 65536,
            }],
        },
    )


def _completion(label: str) -> httpx.Response:
    return httpx.Response(
        200,
        json={
            "id": "mock-formal",
            "object": "chat.completion",
            "created": 0,
            "model": "qwen3-32b-fp8",
            "choices": [{
                "index": 0,
                "message": {"role": "assistant", "content": label},
                "finish_reason": "stop",
            }],
        },
    )


class FormalJudgeQualificationTests(IsolatedAsyncioTestCase):
    async def test_formal_mock_run_uses_strict_freeze_and_durable_28_event_store(self) -> None:
        freeze = build_strict_judge_qualification_freeze(
            validation_root=ROOT,
            fixture_path=FIXTURE.relative_to(ROOT),
            offline_manifest_path=OFFLINE_MANIFEST.relative_to(ROOT),
            qualification_source_path=CORE_SOURCE.relative_to(ROOT),
            qualification_live_source_path=LIVE_SOURCE.relative_to(ROOT),
        )
        labels = iter("YES" if item["human_label"] else "NO" for item in freeze["items"])
        models_calls = 0
        chat_bodies: list[dict[str, object]] = []

        def models_handler(request: httpx.Request) -> httpx.Response:
            nonlocal models_calls
            models_calls += 1
            self.assertEqual(request.method, "GET")
            return _models_response()

        def chat_handler(request: httpx.Request) -> httpx.Response:
            self.assertEqual(request.method, "POST")
            body = json.loads(request.content)
            chat_bodies.append(body)
            return _completion(next(labels))

        deployment_binding = {
            "path": DEPLOYMENT_EVIDENCE.relative_to(ROOT).as_posix(),
            "sha256": _sha(DEPLOYMENT_EVIDENCE),
        }
        with tempfile.TemporaryDirectory() as temporary, mock.patch.object(
            socket.socket,
            "connect",
            side_effect=AssertionError("real network forbidden in formal dry-run"),
        ):
            result = await run_formal_judge_qualification(
                validation_root=ROOT,
                runs_root=Path(temporary),
                run_id="jq-fedcba9876543210",
                freeze=freeze,
                config_mapping={
                    "base_url": "http://judge.private.invalid/v1",
                    "api_key": "PRIVATE-JUDGE-CREDENTIAL",
                },
                deployment_evidence_binding=deployment_binding,
                models_transport=httpx.MockTransport(models_handler),
                chat_transport=httpx.MockTransport(chat_handler),
            )
            run_dir = Path(temporary) / "jq-fedcba9876543210"
            verification = verify_judge_qualification_artifacts(run_dir, freeze)
            events = run_dir.joinpath("events.jsonl").read_text(encoding="ascii").splitlines()

        self.assertEqual(result["qualification_status"], "PASS")
        self.assertEqual(models_calls, 15)
        self.assertEqual(len(chat_bodies), 14)
        self.assertEqual(verification["attempt_status"], "complete")
        self.assertEqual(len(events), 28)
        for body in chat_bodies:
            self.assertEqual(body["model"], "qwen3-32b-fp8")
            self.assertEqual(body["temperature"], 0)
            self.assertEqual(body["max_tokens"], 10)
            self.assertEqual(body["n"], 1)
            self.assertEqual(body["chat_template_kwargs"], {"enable_thinking": False})
            self.assertEqual([m["role"] for m in body["messages"]], ["user"])


if __name__ == "__main__":
    import unittest

    unittest.main()
