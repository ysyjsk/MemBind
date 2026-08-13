"""RED contracts for the formal runner's authorization/transport boundary.

An authorization-free invocation is an offline dry-run only when *both* HTTP
transports are explicit ``httpx.MockTransport`` instances.  Every other
transport combination must fail before runtime-identity capture, before a
handler is called, and before a run directory is created.

All positive-path traffic terminates in ``MockTransport``.  Invalid cases patch
runtime-identity capture as a final network tripwire, so this module cannot
contact the live Judge service while the production guard is still RED.
"""

from __future__ import annotations

import hashlib
import json
import sys
import tempfile
from pathlib import Path
from unittest import IsolatedAsyncioTestCase, mock

import httpx


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from evaluation.judge_qualification import (  # noqa: E402
    build_strict_judge_qualification_freeze,
)
from evaluation.judge_qualification_live import (  # noqa: E402
    JudgeQualificationLiveError,
    run_formal_judge_qualification,
)


FIXTURE = ROOT / "fixtures/judge_qualification_14_v1.json"
OFFLINE_MANIFEST = ROOT / "artifacts/protocol/judge_upstream_manifest_20260812.json"
CORE_SOURCE = ROOT / "src/evaluation/judge_qualification.py"
LIVE_SOURCE = ROOT / "src/evaluation/judge_qualification_live.py"
DEPLOYMENT_EVIDENCE = (
    ROOT / "artifacts/environment/judge_deployment_evidence_20260813.json"
)


def _sha_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _models_response() -> httpx.Response:
    return httpx.Response(
        200,
        json={
            "object": "list",
            "data": [
                {
                    "id": "qwen3-32b-fp8",
                    "object": "model",
                    "owned_by": "vllm",
                    "root": "qwen3-32b-fp8",
                    "max_model_len": 65536,
                }
            ],
        },
    )


def _completion(label: str) -> httpx.Response:
    return httpx.Response(
        200,
        json={
            "id": "mock-transport-authorization-boundary",
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


class _OfflineNonMockTransport(httpx.AsyncBaseTransport):
    """A non-MockTransport that records accidental dispatch without sockets."""

    def __init__(self, response_factory: object) -> None:
        self.calls = 0
        self._response_factory = response_factory

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        self.calls += 1
        factory = self._response_factory
        if not callable(factory):
            raise AssertionError("invalid offline response factory")
        return factory(request)


class FormalTransportAuthorizationRedTests(IsolatedAsyncioTestCase):
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
            "api_key": "OFFLINE-TEST-CREDENTIAL",
        }

    def _mock_transports(
        self,
        calls: list[str],
    ) -> tuple[httpx.MockTransport, httpx.MockTransport]:
        labels = iter(
            "YES" if item["human_label"] else "NO" for item in self.freeze["items"]
        )

        def models_handler(request: httpx.Request) -> httpx.Response:
            calls.append("models")
            self.assertEqual((request.method, request.url.path), ("GET", "/v1/models"))
            return _models_response()

        def chat_handler(request: httpx.Request) -> httpx.Response:
            calls.append("chat")
            self.assertEqual(
                (request.method, request.url.path),
                ("POST", "/v1/chat/completions"),
            )
            return _completion(next(labels))

        return httpx.MockTransport(models_handler), httpx.MockTransport(chat_handler)

    async def test_no_authorization_allows_explicit_double_mock_dry_run(self) -> None:
        calls: list[str] = []
        models_transport, chat_transport = self._mock_transports(calls)

        with tempfile.TemporaryDirectory() as temporary:
            runs_root = Path(temporary)
            result = await run_formal_judge_qualification(
                validation_root=ROOT,
                runs_root=runs_root,
                run_id="jq-1010101010101010",
                freeze=self.freeze,
                config_mapping=self.config,
                deployment_evidence_binding=self.deployment_binding,
                authorization_binding=None,
                models_transport=models_transport,
                chat_transport=chat_transport,
            )

            self.assertEqual(result["qualification_status"], "PASS")
            self.assertTrue(runs_root.joinpath("jq-1010101010101010").is_dir())

        self.assertEqual(calls.count("chat"), 14)
        self.assertGreaterEqual(calls.count("models"), 1)

    async def test_no_authorization_rejects_any_non_double_mock_combination(self) -> None:
        cases = (
            "models_none",
            "chat_none",
            "models_non_mock",
            "chat_non_mock",
            "both_none",
            "both_non_mock",
            "models_none_chat_non_mock",
            "models_non_mock_chat_none",
        )

        for index, case in enumerate(cases, start=1):
            with self.subTest(case=case), tempfile.TemporaryDirectory() as temporary:
                calls: list[str] = []
                mock_models, mock_chat = self._mock_transports(calls)
                non_mock_models = _OfflineNonMockTransport(
                    lambda _request: _models_response()
                )
                labels = iter(
                    "YES" if item["human_label"] else "NO"
                    for item in self.freeze["items"]
                )
                non_mock_chat = _OfflineNonMockTransport(
                    lambda _request: _completion(next(labels))
                )
                models_transport: httpx.AsyncBaseTransport | None = mock_models
                chat_transport: httpx.AsyncBaseTransport | None = mock_chat
                if case == "models_none":
                    models_transport = None
                elif case == "chat_none":
                    chat_transport = None
                elif case == "models_non_mock":
                    models_transport = non_mock_models
                elif case == "chat_non_mock":
                    chat_transport = non_mock_chat
                elif case == "both_none":
                    models_transport = None
                    chat_transport = None
                elif case == "both_non_mock":
                    models_transport = non_mock_models
                    chat_transport = non_mock_chat
                elif case == "models_none_chat_non_mock":
                    models_transport = None
                    chat_transport = non_mock_chat
                elif case == "models_non_mock_chat_none":
                    models_transport = non_mock_models
                    chat_transport = None

                runs_root = Path(temporary)
                run_id = f"jq-{index:016x}"
                with mock.patch(
                    "evaluation.judge_qualification_live.capture_judge_runtime_identity",
                    side_effect=AssertionError(
                        "authorization guard failed before runtime identity request"
                    ),
                ) as identity_capture:
                    with self.assertRaisesRegex(
                        JudgeQualificationLiveError,
                        "authorization|MockTransport|dry-run",
                    ):
                        await run_formal_judge_qualification(
                            validation_root=ROOT,
                            runs_root=runs_root,
                            run_id=run_id,
                            freeze=self.freeze,
                            config_mapping=self.config,
                            deployment_evidence_binding=self.deployment_binding,
                            authorization_binding=None,
                            models_transport=models_transport,
                            chat_transport=chat_transport,
                        )

                self.assertEqual(calls, [])
                self.assertEqual(non_mock_models.calls, 0)
                self.assertEqual(non_mock_chat.calls, 0)
                identity_capture.assert_not_called()
                self.assertFalse(runs_root.joinpath(run_id).exists())


if __name__ == "__main__":
    import unittest

    unittest.main()
