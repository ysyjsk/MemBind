"""Intentional RED contracts for formal Judge authorization hardening.

These tests are entirely offline.  Every HTTP client uses ``MockTransport``
and a socket guard remains active.  The contracts require a singleton grant to
bind its exact validation-root-relative path, reject extra/private fields before
consumption, and make the artifact verifier enforce the same exact schemas.
"""

from __future__ import annotations

import hashlib
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
    build_judge_prelive_evidence_manifest,
    load_verified_judge_deployment_evidence,
    run_formal_judge_qualification,
)
from tests.judge_test_evidence_fixture import (  # noqa: E402
    write_test_evidence_reports,
)


FIXTURE = ROOT / "fixtures/judge_qualification_14_v1.json"
OFFLINE_MANIFEST = ROOT / "artifacts/protocol/judge_upstream_manifest_20260812.json"
CORE_SOURCE = ROOT / "src/evaluation/judge_qualification.py"
LIVE_SOURCE = ROOT / "src/evaluation/judge_qualification_live.py"
Q3_SOURCE = ROOT / "src/evaluation/judge_qualification_q3.py"
DEPLOYMENT_EVIDENCE = (
    ROOT / "artifacts/environment/judge_deployment_evidence_20260813.json"
)
WORKPLAN = ROOT.parent / "MemBind_JUDGE_QUALIFICATION_WORKPLAN_v1.0.md"


def _sha_bytes(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _sha_file(path: Path) -> str:
    return _sha_bytes(path.read_bytes())


def _seal(value: dict[str, object]) -> dict[str, object]:
    sealed = deepcopy(value)
    sealed.pop("payload_sha256", None)
    sealed["payload_sha256"] = _sha_bytes(canonical_json_bytes(sealed))
    return sealed


def _write_canonical(path: Path, value: dict[str, object]) -> None:
    path.write_bytes(canonical_json_bytes(value) + b"\n")


def _read_json(path: Path) -> dict[str, object]:
    value = json.loads(path.read_text("ascii"))
    assert isinstance(value, dict)
    return value


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
            "id": "mock-authorization-hardening",
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


class FormalJudgeAuthorizationHardeningRedTests(IsolatedAsyncioTestCase):
    """Security contracts that intentionally fail against the current code."""

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
        self.deployment = load_verified_judge_deployment_evidence(
            ROOT,
            Path(self.deployment_binding["path"]),
            self.deployment_binding["sha256"],
        )
        self.config = {
            "base_url": "http://judge.private.invalid/v1",
            "api_key": "OFFLINE-PRIVATE-CREDENTIAL",
        }

    def _prepare(self, directory: Path, run_id: str) -> dict[str, object]:
        freeze_path = directory / "strict-freeze.json"
        _write_canonical(freeze_path, self.freeze)
        reports = write_test_evidence_reports(directory, ROOT)
        prelive = build_judge_prelive_evidence_manifest(
            validation_root=ROOT,
            authorized_run_id=run_id,
            workplan_path=WORKPLAN,
            qualification_source_path=CORE_SOURCE.relative_to(ROOT),
            qualification_live_source_path=LIVE_SOURCE.relative_to(ROOT),
            qualification_q3_source_path=Q3_SOURCE.relative_to(ROOT),
            judge_test_paths=sorted(
                path.relative_to(ROOT)
                for path in ROOT.joinpath("tests").glob("test_judge*.py")
            ),
            qualification_fixture_path=FIXTURE.relative_to(ROOT),
            offline_manifest_path=OFFLINE_MANIFEST.relative_to(ROOT),
            deployment_evidence_path=DEPLOYMENT_EVIDENCE.relative_to(ROOT),
            final_focused_report_path=reports["focused"].relative_to(ROOT),
            final_impact_report_path=reports["impact"].relative_to(ROOT),
            final_q3_dry_run_report_path=reports["q3"].relative_to(ROOT),
            strict_freeze_path=freeze_path.relative_to(ROOT),
            live_run_limit=1,
        )
        prelive_path = directory / "judge-prelive-evidence-manifest.json"
        _write_canonical(prelive_path, prelive)
        authorization_path = directory / "judge-live-authorization.json"
        authorization = _seal(
            {
                "schema_version": "membind.judge-live-authorization.v1",
                "protocol_id": "judge-qualification-v1.0",
                "scientific_surface": "JUDGE_QUALIFICATION_ONLY",
                "authorization_id": "jqa-hardening-red-only",
                "authorized_run_id": run_id,
                "authorization_path": authorization_path.relative_to(ROOT).as_posix(),
                "live_run_limit": 1,
                "freeze_payload_sha256": self.freeze["payload_sha256"],
                "qualification_live_source_sha256": _sha_file(LIVE_SOURCE),
                "deployment_evidence_payload_sha256": self.deployment[
                    "evidence_payload_sha256"
                ],
                "prelive_evidence_manifest_file_sha256": _sha_file(prelive_path),
                "prelive_evidence_manifest_payload_sha256": prelive["payload_sha256"],
            }
        )
        _write_canonical(authorization_path, authorization)
        return {
            "run_id": run_id,
            "prelive_path": prelive_path,
            "authorization_path": authorization_path,
        }

    def _transports(
        self, calls: list[str]
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

    async def _run(
        self,
        fixture: dict[str, object],
        *,
        runs_root: Path,
        authorization_path: Path,
        calls: list[str],
    ) -> dict[str, object]:
        models_transport, chat_transport = self._transports(calls)
        prelive_path = fixture["prelive_path"]
        assert isinstance(prelive_path, Path)
        return await run_formal_judge_qualification(
            validation_root=ROOT,
            runs_root=runs_root,
            run_id=str(fixture["run_id"]),
            freeze=self.freeze,
            config_mapping=self.config,
            deployment_evidence_binding=self.deployment_binding,
            authorization_binding={
                "path": authorization_path.relative_to(ROOT).as_posix(),
                "sha256": _sha_file(authorization_path),
            },
            prelive_evidence_binding={
                "path": prelive_path.relative_to(ROOT).as_posix(),
                "sha256": _sha_file(prelive_path),
            },
            models_transport=models_transport,
            chat_transport=chat_transport,
        )

    async def test_copied_canonical_authorization_cannot_create_second_receipt(self) -> None:
        with tempfile.TemporaryDirectory(dir=ROOT / "artifacts") as temporary, mock.patch.object(
            socket.socket,
            "connect",
            side_effect=AssertionError("real network forbidden in hardening RED"),
        ):
            directory = Path(temporary)
            fixture = self._prepare(directory, "jq-a1a1a1a1a1a1a1a1")
            original = fixture["authorization_path"]
            assert isinstance(original, Path)
            first_calls: list[str] = []
            first = await self._run(
                fixture,
                runs_root=directory / "first-runs",
                authorization_path=original,
                calls=first_calls,
            )
            self.assertEqual(first["qualification_status"], "PASS")
            self.assertEqual(len(first_calls), 29)

            copied = directory / "copied-live-authorization.json"
            copied.write_bytes(original.read_bytes())
            second_calls: list[str] = []
            second_error: BaseException | None = None
            try:
                await self._run(
                    fixture,
                    runs_root=directory / "second-runs",
                    authorization_path=copied,
                    calls=second_calls,
                )
            except BaseException as error:
                second_error = error

            self.assertIsInstance(second_error, JudgeQualificationLiveError)
            self.assertEqual(second_calls, [])
            self.assertFalse(
                copied.with_name(copied.name + ".consumed.json").exists()
            )

    async def test_authorization_extra_private_or_unknown_field_fails_pre_http(self) -> None:
        mutations = {
            "api_key": "MUST-NOT-BE-PERSISTED",
            "base_url": "http://private.invalid/v1",
            "unexpected_grant_field": "not-in-the-exact-schema",
        }
        for index, (field, value) in enumerate(mutations.items(), start=1):
            with self.subTest(field=field), tempfile.TemporaryDirectory(
                dir=ROOT / "artifacts"
            ) as temporary, mock.patch.object(
                socket.socket,
                "connect",
                side_effect=AssertionError("real network forbidden in hardening RED"),
            ):
                directory = Path(temporary)
                fixture = self._prepare(
                    directory, f"jq-b{index:015x}"
                )
                authorization_path = fixture["authorization_path"]
                assert isinstance(authorization_path, Path)
                authorization = _read_json(authorization_path)
                authorization[field] = value
                _write_canonical(authorization_path, _seal(authorization))
                calls: list[str] = []
                error: BaseException | None = None
                try:
                    await self._run(
                        fixture,
                        runs_root=directory / "runs",
                        authorization_path=authorization_path,
                        calls=calls,
                    )
                except BaseException as observed:
                    error = observed

                self.assertIsInstance(error, JudgeQualificationLiveError)
                self.assertEqual(calls, [])
                self.assertFalse(
                    authorization_path.with_name(
                        authorization_path.name + ".consumed.json"
                    ).exists()
                )

    def _rebind_run_manifest(self, run_dir: Path) -> None:
        authorization_path = run_dir / "live_authorization.json"
        consumption_path = run_dir / "live_authorization_consumption.json"
        prelive_path = run_dir / "prelive_evidence_manifest.json"
        authorization = _read_json(authorization_path)
        consumption = _read_json(consumption_path)
        prelive = _read_json(prelive_path)
        manifest_path = run_dir / "manifest.json"
        manifest = _read_json(manifest_path)
        manifest["live_authorization_binding"] = {
            "authorization_file_sha256": _sha_file(authorization_path),
            "authorization_payload_sha256": authorization["payload_sha256"],
            "consumption_file_sha256": _sha_file(consumption_path),
            "consumption_payload_sha256": consumption["payload_sha256"],
        }
        manifest["prelive_evidence_binding"] = {
            "manifest_file_sha256": _sha_file(prelive_path),
            "manifest_payload_sha256": prelive["payload_sha256"],
        }
        _write_canonical(manifest_path, _seal(manifest))

    def _mutate_run_evidence(self, run_dir: Path, target: str) -> None:
        authorization_path = run_dir / "live_authorization.json"
        consumption_path = run_dir / "live_authorization_consumption.json"
        prelive_path = run_dir / "prelive_evidence_manifest.json"
        authorization = _read_json(authorization_path)
        consumption = _read_json(consumption_path)
        prelive = _read_json(prelive_path)

        if target == "authorization_secret":
            authorization["api_key"] = "MUST-NOT-BE-PERSISTED"
            authorization = _seal(authorization)
            _write_canonical(authorization_path, authorization)
            consumption["authorization_file_sha256"] = _sha_file(authorization_path)
            consumption["authorization_payload_sha256"] = authorization["payload_sha256"]
            _write_canonical(consumption_path, _seal(consumption))
        elif target == "receipt_unknown":
            consumption["unexpected_receipt_field"] = "not-in-the-exact-schema"
            _write_canonical(consumption_path, _seal(consumption))
        elif target == "prelive_secret":
            prelive["base_url"] = "http://private.invalid/v1"
            prelive = _seal(prelive)
            _write_canonical(prelive_path, prelive)
            authorization["prelive_evidence_manifest_file_sha256"] = _sha_file(
                prelive_path
            )
            authorization["prelive_evidence_manifest_payload_sha256"] = prelive[
                "payload_sha256"
            ]
            authorization = _seal(authorization)
            _write_canonical(authorization_path, authorization)
            consumption["authorization_file_sha256"] = _sha_file(authorization_path)
            consumption["authorization_payload_sha256"] = authorization["payload_sha256"]
            consumption["prelive_evidence_manifest_file_sha256"] = _sha_file(
                prelive_path
            )
            consumption["prelive_evidence_manifest_payload_sha256"] = prelive[
                "payload_sha256"
            ]
            _write_canonical(consumption_path, _seal(consumption))
        else:  # pragma: no cover - test helper contract
            raise AssertionError(f"unknown mutation target: {target}")
        self._rebind_run_manifest(run_dir)

    async def test_verifier_rejects_resealed_unknown_or_secret_evidence_fields(self) -> None:
        targets = ("authorization_secret", "receipt_unknown", "prelive_secret")
        for index, target in enumerate(targets, start=1):
            with self.subTest(target=target), tempfile.TemporaryDirectory(
                dir=ROOT / "artifacts"
            ) as temporary, mock.patch.object(
                socket.socket,
                "connect",
                side_effect=AssertionError("real network forbidden in hardening RED"),
            ):
                directory = Path(temporary)
                fixture = self._prepare(directory, f"jq-c{index:015x}")
                authorization_path = fixture["authorization_path"]
                assert isinstance(authorization_path, Path)
                calls: list[str] = []
                result = await self._run(
                    fixture,
                    runs_root=directory / "runs",
                    authorization_path=authorization_path,
                    calls=calls,
                )
                self.assertEqual(result["qualification_status"], "PASS")
                self.assertEqual(len(calls), 29)
                run_dir = directory / "runs" / str(fixture["run_id"])
                self._mutate_run_evidence(run_dir, target)

                verification = verify_judge_qualification_artifacts(
                    run_dir, self.freeze
                )
                self.assertEqual(
                    verification["failure_class"], "artifact_verification_error"
                )
                self.assertEqual(
                    verification["attempt_status"],
                    "incomplete_invalid_non_mergeable",
                )


if __name__ == "__main__":
    import unittest

    unittest.main()
