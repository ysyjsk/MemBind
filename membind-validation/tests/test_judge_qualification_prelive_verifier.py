"""Intentional RED contract for independent pre-live artifact verification.

The formal runner validates the pre-live evidence closure before dispatch.  A
successful run must remain independently auditable afterwards: resealing the
run-local copy and updating every shallow hash binding must not make semantic
or closure tampering acceptable to ``verify_judge_qualification_artifacts``.

This test performs no real network I/O and creates only disposable mock live
authorizations under a temporary artifact directory.
"""

from __future__ import annotations

import hashlib
import json
import shutil
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
    canonical_json_bytes,
    verify_judge_qualification_artifacts,
)
from evaluation.judge_qualification_live import (  # noqa: E402
    run_formal_judge_qualification,
)
from tests.test_judge_qualification_prelive_gate import (  # noqa: E402
    DEPLOYMENT_EVIDENCE,
    PreliveFixture,
    _binding,
)


def _sha_bytes(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _seal(value: dict[str, object]) -> dict[str, object]:
    sealed = deepcopy(value)
    sealed.pop("payload_sha256", None)
    sealed["payload_sha256"] = _sha_bytes(canonical_json_bytes(sealed))
    return sealed


def _write_canonical(path: Path, value: dict[str, object]) -> bytes:
    raw = canonical_json_bytes(value) + b"\n"
    path.write_bytes(raw)
    return raw


def _read(path: Path) -> dict[str, object]:
    value = json.loads(path.read_text("ascii"))
    assert isinstance(value, dict)
    return value


class JudgePreliveVerifierIntentionalRedTests(IsolatedAsyncioTestCase):
    @staticmethod
    def _transports(
        fixture: PreliveFixture,
    ) -> tuple[httpx.MockTransport, httpx.MockTransport]:
        labels = iter(
            "YES" if item["human_label"] else "NO"
            for item in fixture.freeze["items"]
        )

        def models_handler(_request: httpx.Request) -> httpx.Response:
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

        def chat_handler(_request: httpx.Request) -> httpx.Response:
            return httpx.Response(
                200,
                json={
                    "id": "mock-prelive-verifier-red",
                    "object": "chat.completion",
                    "created": 0,
                    "model": "qwen3-32b-fp8",
                    "choices": [
                        {
                            "index": 0,
                            "message": {
                                "role": "assistant",
                                "content": next(labels),
                            },
                            "finish_reason": "stop",
                        }
                    ],
                },
            )

        return httpx.MockTransport(models_handler), httpx.MockTransport(
            chat_handler
        )

    @staticmethod
    def _tamper_and_rebind(
        run_dir: Path, mutate: object
    ) -> None:
        """Change pre-live semantics while making all shallow hashes agree."""

        prelive_path = run_dir / "prelive_evidence_manifest.json"
        prelive = _read(prelive_path)
        prelive.pop("payload_sha256")
        mutate(prelive)
        prelive = _seal(prelive)
        prelive_raw = _write_canonical(prelive_path, prelive)
        prelive_binding = {
            "manifest_file_sha256": _sha_bytes(prelive_raw),
            "manifest_payload_sha256": prelive["payload_sha256"],
        }

        authorization_path = run_dir / "live_authorization.json"
        authorization = _read(authorization_path)
        authorization.pop("payload_sha256")
        authorization["prelive_evidence_manifest_file_sha256"] = (
            prelive_binding["manifest_file_sha256"]
        )
        authorization["prelive_evidence_manifest_payload_sha256"] = (
            prelive_binding["manifest_payload_sha256"]
        )
        authorization = _seal(authorization)
        authorization_raw = _write_canonical(authorization_path, authorization)

        consumption_path = run_dir / "live_authorization_consumption.json"
        consumption = _read(consumption_path)
        consumption.pop("payload_sha256")
        consumption["authorization_file_sha256"] = _sha_bytes(authorization_raw)
        consumption["authorization_payload_sha256"] = authorization[
            "payload_sha256"
        ]
        consumption["prelive_evidence_manifest_file_sha256"] = (
            prelive_binding["manifest_file_sha256"]
        )
        consumption["prelive_evidence_manifest_payload_sha256"] = (
            prelive_binding["manifest_payload_sha256"]
        )
        consumption = _seal(consumption)
        consumption_raw = _write_canonical(consumption_path, consumption)

        manifest_path = run_dir / "manifest.json"
        manifest = _read(manifest_path)
        manifest.pop("payload_sha256")
        manifest["prelive_evidence_binding"] = prelive_binding
        manifest["live_authorization_binding"] = {
            "authorization_file_sha256": _sha_bytes(authorization_raw),
            "authorization_payload_sha256": authorization["payload_sha256"],
            "consumption_file_sha256": _sha_bytes(consumption_raw),
            "consumption_payload_sha256": consumption["payload_sha256"],
        }
        _write_canonical(manifest_path, _seal(manifest))

    async def test_verifier_deeply_rejects_resealed_prelive_semantic_tampering(
        self,
    ) -> None:
        run_id = "jq-5656565656565656"
        with tempfile.TemporaryDirectory(
            dir=ROOT / "artifacts"
        ) as temporary, mock.patch.object(
            socket.socket,
            "connect",
            side_effect=AssertionError("real network forbidden in verifier RED"),
        ):
            fixture = PreliveFixture(Path(temporary), run_id)
            models_transport, chat_transport = self._transports(fixture)
            await run_formal_judge_qualification(
                validation_root=ROOT,
                runs_root=fixture.directory / "runs",
                run_id=run_id,
                freeze=fixture.freeze,
                config_mapping={
                    "base_url": "http://judge.private.invalid/v1",
                    "api_key": "PRIVATE-JUDGE-CREDENTIAL",
                },
                deployment_evidence_binding=_binding(DEPLOYMENT_EVIDENCE),
                authorization_binding=fixture.authorization_binding,
                prelive_evidence_binding=fixture.manifest_binding,
                models_transport=models_transport,
                chat_transport=chat_transport,
            )

            baseline = fixture.directory / "runs" / run_id
            baseline_verification = verify_judge_qualification_artifacts(
                baseline, fixture.freeze
            )
            self.assertEqual(baseline_verification["attempt_status"], "complete")
            self.assertEqual(baseline_verification["qualification_status"], "PASS")

            mutations = {
                "schema_version": lambda value: value.__setitem__(
                    "schema_version", "membind.judge-prelive-evidence-manifest.v999"
                ),
                "authorized_run_id": lambda value: value.__setitem__(
                    "authorized_run_id", "jq-ffffffffffffffff"
                ),
                "live_run_limit": lambda value: value.__setitem__(
                    "live_run_limit", 2
                ),
                "closure_binding": lambda value: value["bindings"][
                    "qualification_source"
                ].__setitem__("sha256", "f" * 64),
                "unknown_field": lambda value: value.__setitem__(
                    "unrecognized_evidence", "must-fail-closed"
                ),
            }
            for label, mutation in mutations.items():
                with self.subTest(label=label):
                    case_dir = fixture.directory / "tampered" / label / run_id
                    case_dir.parent.mkdir(parents=True)
                    shutil.copytree(baseline, case_dir)
                    self._tamper_and_rebind(case_dir, mutation)

                    verification = verify_judge_qualification_artifacts(
                        case_dir, fixture.freeze
                    )
                    self.assertEqual(
                        verification["attempt_status"],
                        "incomplete_invalid_non_mergeable",
                    )
                    self.assertEqual(
                        verification["failure_class"],
                        "artifact_verification_error",
                    )


if __name__ == "__main__":
    import unittest

    unittest.main()
