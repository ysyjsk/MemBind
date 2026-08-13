"""RED contracts for sealed deployment evidence at the formal Judge boundary.

The formal runner must accept only a validation-root-relative evidence binding
and derive the runtime identity through ``load_verified_judge_deployment_evidence``.
Caller-authored identity dictionaries are deliberately outside this boundary.

Every HTTP request in this module terminates in ``httpx.MockTransport``.  Socket
access is additionally blocked so these tests cannot qualify or contact a live
Judge deployment.
"""

from __future__ import annotations

import hashlib
import inspect
import json
import socket
import sys
import tempfile
from copy import deepcopy
from pathlib import Path
from unittest import IsolatedAsyncioTestCase, mock

import httpx


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from evaluation.judge_qualification import (  # noqa: E402
    build_strict_judge_qualification_freeze,
    canonical_json_bytes,
    verify_judge_qualification_artifacts,
)
from evaluation.judge_qualification_live import (  # noqa: E402
    JudgeQualificationLiveError,
    load_verified_judge_deployment_evidence,
    run_formal_judge_qualification,
)


FIXTURE = ROOT / "fixtures/judge_qualification_14_v1.json"
OFFLINE_MANIFEST = ROOT / "artifacts/protocol/judge_upstream_manifest_20260812.json"
CORE_SOURCE = ROOT / "src/evaluation/judge_qualification.py"
LIVE_SOURCE = ROOT / "src/evaluation/judge_qualification_live.py"
DEPLOYMENT_EVIDENCE = (
    ROOT / "artifacts/environment/judge_deployment_evidence_20260813.json"
)
ACTUAL_REPOSITORY_REVISION = "aa55da1ecc13d006e8b8e4f54579b1ea8c3db2df"


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
            "id": "mock-sealed-deployment-evidence",
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


class FormalDeploymentEvidenceRedTests(IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.freeze = build_strict_judge_qualification_freeze(
            validation_root=ROOT,
            fixture_path=FIXTURE.relative_to(ROOT),
            offline_manifest_path=OFFLINE_MANIFEST.relative_to(ROOT),
            qualification_source_path=CORE_SOURCE.relative_to(ROOT),
            qualification_live_source_path=LIVE_SOURCE.relative_to(ROOT),
        )
        self.binding = {
            "path": DEPLOYMENT_EVIDENCE.relative_to(ROOT).as_posix(),
            "sha256": _sha_file(DEPLOYMENT_EVIDENCE),
        }
        self.loaded_evidence = load_verified_judge_deployment_evidence(
            ROOT,
            Path(self.binding["path"]),
            self.binding["sha256"],
        )
        self.config = {
            "base_url": "http://judge.private.invalid/v1",
            "api_key": "PRIVATE-JUDGE-CREDENTIAL",
        }

    def _transports(
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

    async def _run_with_binding(
        self,
        *,
        runs_root: Path,
        run_id: str,
        binding: dict[str, str],
        calls: list[str],
    ) -> dict[str, object]:
        models_transport, chat_transport = self._transports(calls)
        with mock.patch.object(
            socket.socket,
            "connect",
            side_effect=AssertionError("real network forbidden in sealed-evidence test"),
        ):
            return await run_formal_judge_qualification(
                validation_root=ROOT,
                runs_root=runs_root,
                run_id=run_id,
                freeze=self.freeze,
                config_mapping=self.config,
                deployment_evidence_binding=binding,
                models_transport=models_transport,
                chat_transport=chat_transport,
            )

    async def test_formal_runner_derives_revision_identity_from_sealed_evidence(
        self,
    ) -> None:
        calls: list[str] = []
        with tempfile.TemporaryDirectory() as temporary:
            runs_root = Path(temporary)
            result = await self._run_with_binding(
                runs_root=runs_root,
                run_id="jq-dededededededede",
                binding=self.binding,
                calls=calls,
            )
            run_dir = runs_root / "jq-dededededededede"
            verification = verify_judge_qualification_artifacts(run_dir, self.freeze)
            runtime_document = json.loads(
                run_dir.joinpath("runtime_identity.json").read_text(encoding="ascii")
            )

        identity = runtime_document["identity"]
        self.assertEqual(result["qualification_status"], "PASS")
        self.assertEqual(verification["attempt_status"], "complete")
        self.assertEqual(calls.count("chat"), 14)
        self.assertGreaterEqual(calls.count("models"), 1)
        self.assertEqual(
            identity["repository_revision"], ACTUAL_REPOSITORY_REVISION
        )
        self.assertNotIn("model_fingerprint", identity)
        self.assertEqual(
            identity["deployment_evidence_binding"],
            {
                **self.binding,
                "payload_sha256": self.loaded_evidence["evidence_payload_sha256"],
            },
        )

    async def test_caller_assertion_dictionary_is_not_a_formal_input(self) -> None:
        parameters = inspect.signature(run_formal_judge_qualification).parameters
        self.assertIn("deployment_evidence_binding", parameters)
        self.assertNotIn("deployment_evidence", parameters)

        calls: list[str] = []
        models_transport, chat_transport = self._transports(calls)
        with tempfile.TemporaryDirectory() as temporary, self.assertRaises(TypeError):
            await run_formal_judge_qualification(
                validation_root=ROOT,
                runs_root=Path(temporary),
                run_id="jq-cacacacacacacaca",
                freeze=self.freeze,
                config_mapping=self.config,
                deployment_evidence=dict(self.loaded_evidence),
                models_transport=models_transport,
                chat_transport=chat_transport,
            )

        self.assertEqual(calls, [])

    async def test_outer_file_hash_drift_fails_before_any_request(self) -> None:
        calls: list[str] = []
        with tempfile.TemporaryDirectory() as temporary:
            with self.assertRaises(JudgeQualificationLiveError):
                await self._run_with_binding(
                    runs_root=Path(temporary),
                    run_id="jq-abababababababab",
                    binding={**self.binding, "sha256": "0" * 64},
                    calls=calls,
                )

        self.assertEqual(calls, [])

    async def test_outer_content_drift_fails_before_any_request(self) -> None:
        calls: list[str] = []
        with tempfile.TemporaryDirectory(dir=ROOT / "artifacts") as temporary:
            drifted_path = Path(temporary) / "judge-deployment-evidence-drifted.json"
            drifted = deepcopy(json.loads(DEPLOYMENT_EVIDENCE.read_text("ascii")))
            drifted["runtime"]["repository_revision"] = "b" * 40
            # Keep the original payload seal: the binding authenticates these
            # bytes, while the loader must independently reject their content.
            drifted_path.write_bytes(canonical_json_bytes(drifted) + b"\n")
            drifted_binding = {
                "path": drifted_path.relative_to(ROOT).as_posix(),
                "sha256": _sha_file(drifted_path),
            }
            with self.assertRaises(JudgeQualificationLiveError):
                await self._run_with_binding(
                    runs_root=Path(temporary) / "runs",
                    run_id="jq-bcbcbcbcbcbcbcbc",
                    binding=drifted_binding,
                    calls=calls,
                )

        self.assertEqual(calls, [])


if __name__ == "__main__":
    import unittest

    unittest.main()
