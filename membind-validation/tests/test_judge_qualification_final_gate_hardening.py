"""Intentional RED contracts for the final offline Judge gate.

These tests exercise only disposable evidence and ``httpx.MockTransport``.
They specify the remaining fail-closed boundaries before any formal live
authorization may be created: strict authorization scalar/private-material
validation, independent post-run authorization identity verification, and
strict numeric types in the core structured-report verifier.
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
from unittest import IsolatedAsyncioTestCase, TestCase, mock

import httpx


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from evaluation.judge_qualification import (  # noqa: E402
    _validate_test_evidence_report,
    canonical_json_bytes,
    verify_judge_qualification_artifacts,
)
from evaluation.judge_qualification_live import (  # noqa: E402
    JudgeQualificationLiveError,
    run_formal_judge_qualification,
)
from tests.judge_test_evidence_fixture import (  # noqa: E402
    write_test_evidence_reports,
)
from tests.test_judge_qualification_prelive_gate import (  # noqa: E402
    DEPLOYMENT_EVIDENCE,
    PreliveFixture,
    _binding,
)


def _sha_bytes(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _sha_file(path: Path) -> str:
    return _sha_bytes(path.read_bytes())


def _seal(value: dict[str, object]) -> dict[str, object]:
    sealed = deepcopy(value)
    sealed.pop("payload_sha256", None)
    sealed["payload_sha256"] = _sha_bytes(canonical_json_bytes(sealed))
    return sealed


def _read(path: Path) -> dict[str, object]:
    value = json.loads(path.read_text("ascii"))
    assert isinstance(value, dict)
    return value


def _write_canonical(path: Path, value: dict[str, object]) -> bytes:
    raw = canonical_json_bytes(value) + b"\n"
    path.write_bytes(raw)
    return raw


def _mock_transports(
    fixture: PreliveFixture, calls: list[str]
) -> tuple[httpx.MockTransport, httpx.MockTransport]:
    labels = iter(
        "YES" if item["human_label"] else "NO" for item in fixture.freeze["items"]
    )

    def models_handler(_request: httpx.Request) -> httpx.Response:
        calls.append("models")
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
        calls.append("chat")
        return httpx.Response(
            200,
            json={
                "id": "mock-final-gate-red",
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

    return httpx.MockTransport(models_handler), httpx.MockTransport(chat_handler)


async def _run_mock_formal(
    fixture: PreliveFixture,
    calls: list[str],
    *,
    runs_root: Path,
) -> dict[str, object]:
    models_transport, chat_transport = _mock_transports(fixture, calls)
    return await run_formal_judge_qualification(
        validation_root=ROOT,
        runs_root=runs_root,
        run_id=fixture.run_id,
        freeze=fixture.freeze,
        config_mapping={
            "base_url": "http://judge.private.invalid/v1",
            "api_key": "DISPOSABLE-MOCK-CREDENTIAL",
        },
        deployment_evidence_binding=_binding(DEPLOYMENT_EVIDENCE),
        authorization_binding=_binding(fixture.authorization_path),
        prelive_evidence_binding=_binding(fixture.manifest_path),
        models_transport=models_transport,
        chat_transport=chat_transport,
    )


class JudgeFinalAuthorizationAdmissionRedTests(IsolatedAsyncioTestCase):
    async def test_authorization_id_and_nested_private_material_fail_pre_http(
        self,
    ) -> None:
        mutations: tuple[tuple[str, object], ...] = (
            ("empty_authorization_id", ""),
            (
                "nested_private_material",
                {"api_key": "MUST-NOT-BE-PERSISTED"},
            ),
        )
        for index, (label, authorization_id) in enumerate(mutations, start=1):
            with self.subTest(label=label), tempfile.TemporaryDirectory(
                dir=ROOT / "artifacts"
            ) as temporary, mock.patch.object(
                socket.socket,
                "connect",
                side_effect=AssertionError("real network forbidden in final gate RED"),
            ):
                directory = Path(temporary)
                fixture = PreliveFixture(directory, f"jq-d{index:015x}")
                authorization = _read(fixture.authorization_path)
                authorization["authorization_id"] = authorization_id
                _write_canonical(fixture.authorization_path, _seal(authorization))

                calls: list[str] = []
                error: BaseException | None = None
                try:
                    await _run_mock_formal(
                        fixture,
                        calls,
                        runs_root=directory / "runs",
                    )
                except BaseException as observed:
                    error = observed

                self.assertIsInstance(error, JudgeQualificationLiveError)
                self.assertEqual(calls, [])
                self.assertFalse(
                    fixture.consumption_path.exists(),
                    "invalid authorization must not consume its singleton grant",
                )


class JudgeFinalRunVerifierRedTests(IsolatedAsyncioTestCase):
    @staticmethod
    def _rebind_resealed_authorization(run_dir: Path, field: str) -> None:
        authorization_path = run_dir / "live_authorization.json"
        consumption_path = run_dir / "live_authorization_consumption.json"
        manifest_path = run_dir / "manifest.json"

        authorization = _read(authorization_path)
        authorization[field] = "f" * 64
        authorization = _seal(authorization)
        authorization_raw = _write_canonical(authorization_path, authorization)

        consumption = _read(consumption_path)
        consumption["authorization_file_sha256"] = _sha_bytes(authorization_raw)
        consumption["authorization_payload_sha256"] = authorization["payload_sha256"]
        consumption = _seal(consumption)
        consumption_raw = _write_canonical(consumption_path, consumption)

        manifest = _read(manifest_path)
        manifest["live_authorization_binding"] = {
            "authorization_file_sha256": _sha_bytes(authorization_raw),
            "authorization_payload_sha256": authorization["payload_sha256"],
            "consumption_file_sha256": _sha_bytes(consumption_raw),
            "consumption_payload_sha256": consumption["payload_sha256"],
        }
        _write_canonical(manifest_path, _seal(manifest))

    async def test_verifier_rejects_resealed_authorization_identity_hashes(
        self,
    ) -> None:
        run_id = "jq-e1e1e1e1e1e1e1e1"
        with tempfile.TemporaryDirectory(
            dir=ROOT / "artifacts"
        ) as temporary, mock.patch.object(
            socket.socket,
            "connect",
            side_effect=AssertionError("real network forbidden in final gate RED"),
        ):
            directory = Path(temporary)
            fixture = PreliveFixture(directory, run_id)
            calls: list[str] = []
            result = await _run_mock_formal(
                fixture,
                calls,
                runs_root=directory / "runs",
            )
            self.assertEqual(result["qualification_status"], "PASS")
            self.assertEqual(len(calls), 29)

            baseline = directory / "runs" / run_id
            for field in (
                "qualification_live_source_sha256",
                "deployment_evidence_payload_sha256",
            ):
                with self.subTest(field=field):
                    case_dir = directory / "tampered" / field / run_id
                    case_dir.parent.mkdir(parents=True)
                    shutil.copytree(baseline, case_dir)
                    self._rebind_resealed_authorization(case_dir, field)

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


class JudgeFinalCoreReportTypesRedTests(TestCase):
    def test_core_report_verifier_rejects_numeric_type_impersonation(self) -> None:
        with tempfile.TemporaryDirectory(dir=ROOT / "artifacts") as temporary:
            reports = write_test_evidence_reports(Path(temporary), ROOT)
            focused = _read(reports["focused"])
            q3 = _read(reports["q3"])
            mutations: tuple[tuple[str, str, dict[str, object], object], ...] = (
                ("float_exit_code", "focused", focused, 0.0),
                (
                    "float_test_count",
                    "focused",
                    focused,
                    float(focused["test_count"]),
                ),
                ("bool_external_request_count", "q3", q3, False),
                ("integer_authorization_flag", "q3", q3, 0),
            )
            for label, suite_id, baseline, impostor in mutations:
                with self.subTest(label=label):
                    changed = deepcopy(baseline)
                    if label == "float_exit_code":
                        changed["exit_code"] = impostor
                    elif label == "float_test_count":
                        changed["test_count"] = impostor
                    elif label == "bool_external_request_count":
                        changed["q3_summary"]["real_external_requests"] = impostor
                    else:
                        changed["q3_summary"]["live_authorization_created"] = impostor
                    changed = _seal(changed)

                    with self.assertRaisesRegex(
                        Exception,
                        "test evidence|semantics|invalid|Q3|type",
                    ):
                        _validate_test_evidence_report(ROOT, changed, suite_id)


if __name__ == "__main__":
    import unittest

    unittest.main()
