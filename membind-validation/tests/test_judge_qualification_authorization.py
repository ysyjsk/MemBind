"""RED contracts for the singleton formal-Judge live authorization.

Assumed production API
----------------------
``run_formal_judge_qualification`` accepts one additional keyword argument::

    authorization_binding={"path": <validation-root-relative path>,
                           "sha256": <authorization file SHA256>}

The authorization is an immutable, canonically sealed JSON document.  Before
the first model-identity or chat request, the runner atomically creates the
sibling ``<authorization filename>.consumed.json`` receipt.  Exactly one
caller may create that receipt; a consumed authorization is never reusable,
including when the winning caller later fails.

A successful run copies the authorization and consumption receipt into
``live_authorization.json`` and ``live_authorization_consumption.json``.  Its
manifest and public verifier result expose the same ``live_authorization_binding``
containing the file and payload SHA256 values for both documents.

Every HTTP request in this module terminates in ``httpx.MockTransport``.  These
tests do not authorize a real Judge request or any C4/C5/state mutation.
"""

from __future__ import annotations

import asyncio
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
            "id": "mock-authorized-formal",
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


class FormalJudgeAuthorizationRedTests(IsolatedAsyncioTestCase):
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
            "api_key": "PRIVATE-JUDGE-CREDENTIAL",
        }

    def _authorization(
        self,
        run_id: str,
        prelive_manifest_path: Path,
        prelive_manifest: dict[str, object],
        **changes: object,
    ) -> dict[str, object]:
        value: dict[str, object] = {
            "schema_version": "membind.judge-live-authorization.v1",
            "protocol_id": "judge-qualification-v1.0",
            "scientific_surface": "JUDGE_QUALIFICATION_ONLY",
            "authorization_id": "jqa-0123456789abcdef",
            "authorized_run_id": run_id,
            "authorization_path": (
                prelive_manifest_path.with_name("judge-live-authorization.json")
                .relative_to(ROOT)
                .as_posix()
            ),
            "live_run_limit": 1,
            "freeze_payload_sha256": self.freeze["payload_sha256"],
            "qualification_live_source_sha256": _sha_file(LIVE_SOURCE),
            "deployment_evidence_payload_sha256": self.deployment[
                "evidence_payload_sha256"
            ],
            "prelive_evidence_manifest_file_sha256": _sha_file(
                prelive_manifest_path
            ),
            "prelive_evidence_manifest_payload_sha256": prelive_manifest[
                "payload_sha256"
            ],
        }
        value.update(changes)
        return _seal(value)

    def _write_authorization(
        self,
        directory: Path,
        run_id: str,
        **changes: object,
    ) -> tuple[
        Path,
        dict[str, object],
        dict[str, str],
        dict[str, str],
    ]:
        freeze_path = directory / "strict-freeze.json"
        freeze_path.write_bytes(canonical_json_bytes(self.freeze) + b"\n")
        reports = write_test_evidence_reports(directory, ROOT)
        prelive_manifest = build_judge_prelive_evidence_manifest(
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
        prelive_manifest_path = directory / "judge-prelive-evidence-manifest.json"
        prelive_manifest_path.write_bytes(
            canonical_json_bytes(prelive_manifest) + b"\n"
        )
        prelive_binding = {
            "path": prelive_manifest_path.relative_to(ROOT).as_posix(),
            "sha256": _sha_file(prelive_manifest_path),
        }
        authorization = self._authorization(
            run_id,
            prelive_manifest_path,
            prelive_manifest,
            **changes,
        )
        path = directory / "judge-live-authorization.json"
        path.write_bytes(canonical_json_bytes(authorization) + b"\n")
        binding = {
            "path": path.relative_to(ROOT).as_posix(),
            "sha256": _sha_file(path),
        }
        return path, authorization, binding, prelive_binding

    @staticmethod
    def _consumption_path(authorization_path: Path) -> Path:
        return authorization_path.with_name(authorization_path.name + ".consumed.json")

    def _transports(
        self,
        calls: list[str],
        *,
        models_status: int = 200,
    ) -> tuple[httpx.MockTransport, httpx.MockTransport]:
        labels = iter(
            "YES" if item["human_label"] else "NO" for item in self.freeze["items"]
        )

        def models_handler(_request: httpx.Request) -> httpx.Response:
            calls.append("models")
            if models_status != 200:
                return httpx.Response(models_status, json={"error": "mock failure"})
            return _models_response()

        def chat_handler(_request: httpx.Request) -> httpx.Response:
            calls.append("chat")
            return _completion(next(labels))

        return httpx.MockTransport(models_handler), httpx.MockTransport(chat_handler)

    async def _run(
        self,
        *,
        runs_root: Path,
        run_id: str,
        binding: dict[str, str],
        prelive_binding: dict[str, str],
        calls: list[str],
        models_status: int = 200,
    ) -> dict[str, object]:
        models_transport, chat_transport = self._transports(
            calls, models_status=models_status
        )
        return await run_formal_judge_qualification(
            validation_root=ROOT,
            runs_root=runs_root,
            run_id=run_id,
            freeze=self.freeze,
            config_mapping=self.config,
            deployment_evidence_binding=self.deployment_binding,
            authorization_binding=binding,
            prelive_evidence_binding=prelive_binding,
            models_transport=models_transport,
            chat_transport=chat_transport,
        )

    async def test_concurrent_consume_is_atomic_and_loser_sends_zero_requests(self) -> None:
        run_id = "jq-1111111111111111"
        with tempfile.TemporaryDirectory(dir=ROOT / "artifacts") as temporary:
            directory = Path(temporary)
            authorization_path, _authorization, binding, prelive_binding = self._write_authorization(
                directory, run_id
            )
            calls = [[], []]
            outcomes = await asyncio.gather(
                self._run(
                    runs_root=directory / "runs",
                    run_id=run_id,
                    binding=binding,
                    prelive_binding=prelive_binding,
                    calls=calls[0],
                ),
                self._run(
                    runs_root=directory / "runs",
                    run_id=run_id,
                    binding=binding,
                    prelive_binding=prelive_binding,
                    calls=calls[1],
                ),
                return_exceptions=True,
            )

            successes = [value for value in outcomes if isinstance(value, dict)]
            failures = [value for value in outcomes if isinstance(value, BaseException)]
            self.assertEqual(len(successes), 1)
            self.assertEqual(len(failures), 1)
            self.assertIsInstance(failures[0], JudgeQualificationLiveError)
            self.assertEqual(sorted(map(len, calls)), [0, 29])
            self.assertTrue(self._consumption_path(authorization_path).is_file())

    async def test_wrong_run_limit_or_hash_drift_fails_before_first_request(self) -> None:
        run_id = "jq-2222222222222222"
        mutations = {
            "wrong_run": {"authorized_run_id": "jq-ffffffffffffffff"},
            "wrong_live_run_limit": {"live_run_limit": 2},
            "freeze_hash_drift": {"freeze_payload_sha256": "a" * 64},
            "live_source_hash_drift": {
                "qualification_live_source_sha256": "b" * 64
            },
            "deployment_hash_drift": {
                "deployment_evidence_payload_sha256": "c" * 64
            },
        }
        for label, changes in mutations.items():
            with self.subTest(label=label), tempfile.TemporaryDirectory(
                dir=ROOT / "artifacts"
            ) as temporary:
                directory = Path(temporary)
                authorization_path, _authorization, binding, prelive_binding = self._write_authorization(
                    directory, run_id, **changes
                )
                calls: list[str] = []
                with self.assertRaises(JudgeQualificationLiveError):
                    await self._run(
                        runs_root=directory / "runs",
                        run_id=run_id,
                        binding=binding,
                        prelive_binding=prelive_binding,
                        calls=calls,
                    )
                self.assertEqual(calls, [])
                self.assertFalse(self._consumption_path(authorization_path).exists())

        with tempfile.TemporaryDirectory(dir=ROOT / "artifacts") as temporary:
            directory = Path(temporary)
            authorization_path, _authorization, binding, prelive_binding = self._write_authorization(
                directory, run_id
            )
            binding["sha256"] = "e" * 64
            calls = []
            with self.assertRaises(JudgeQualificationLiveError):
                await self._run(
                    runs_root=directory / "runs",
                    run_id=run_id,
                    binding=binding,
                    prelive_binding=prelive_binding,
                    calls=calls,
                )
            self.assertEqual(calls, [])
            self.assertFalse(self._consumption_path(authorization_path).exists())

    async def test_authorization_symlink_is_rejected_before_first_request(self) -> None:
        run_id = "jq-3333333333333333"
        with tempfile.TemporaryDirectory(dir=ROOT / "artifacts") as temporary:
            directory = Path(temporary)
            real_path, _authorization, _binding, prelive_binding = self._write_authorization(
                directory, run_id
            )
            symlink = directory / "authorization-symlink.json"
            symlink.symlink_to(real_path.name)
            binding = {
                "path": symlink.relative_to(ROOT).as_posix(),
                "sha256": _sha_file(real_path),
            }
            calls: list[str] = []
            with self.assertRaises(JudgeQualificationLiveError):
                await self._run(
                    runs_root=directory / "runs",
                    run_id=run_id,
                    binding=binding,
                    prelive_binding=prelive_binding,
                    calls=calls,
                )
            self.assertEqual(calls, [])
            self.assertFalse(self._consumption_path(symlink).exists())

    async def test_consumed_receipt_survives_downstream_failure_and_blocks_reuse(self) -> None:
        run_id = "jq-4444444444444444"
        with tempfile.TemporaryDirectory(dir=ROOT / "artifacts") as temporary:
            directory = Path(temporary)
            authorization_path, _authorization, binding, prelive_binding = self._write_authorization(
                directory, run_id
            )
            first_calls: list[str] = []
            with self.assertRaises(JudgeQualificationLiveError):
                await self._run(
                    runs_root=directory / "runs",
                    run_id=run_id,
                    binding=binding,
                    prelive_binding=prelive_binding,
                    calls=first_calls,
                    models_status=503,
                )
            consumption_path = self._consumption_path(authorization_path)
            self.assertTrue(consumption_path.is_file())
            consumption_raw = consumption_path.read_bytes()
            self.assertTrue(consumption_raw)

            second_calls: list[str] = []
            with self.assertRaises(JudgeQualificationLiveError):
                await self._run(
                    runs_root=directory / "second-runs",
                    run_id=run_id,
                    binding=binding,
                    prelive_binding=prelive_binding,
                    calls=second_calls,
                )
            self.assertEqual(first_calls, ["models"])
            self.assertEqual(second_calls, [])
            self.assertEqual(consumption_path.read_bytes(), consumption_raw)

    async def test_manifest_and_verifier_bind_authorization_and_consumption_hashes(self) -> None:
        run_id = "jq-5555555555555555"
        with tempfile.TemporaryDirectory(dir=ROOT / "artifacts") as temporary, mock.patch.object(
            socket.socket,
            "connect",
            side_effect=AssertionError("real network forbidden in authorization test"),
        ):
            directory = Path(temporary)
            authorization_path, authorization, binding, prelive_binding = self._write_authorization(
                directory, run_id
            )
            calls: list[str] = []
            result = await self._run(
                runs_root=directory / "runs",
                run_id=run_id,
                binding=binding,
                prelive_binding=prelive_binding,
                calls=calls,
            )
            run_dir = directory / "runs" / run_id
            consumption_path = self._consumption_path(authorization_path)
            manifest = json.loads(run_dir.joinpath("manifest.json").read_text("ascii"))
            verification = verify_judge_qualification_artifacts(run_dir, self.freeze)

            expected_binding = {
                "authorization_file_sha256": _sha_file(authorization_path),
                "authorization_payload_sha256": authorization["payload_sha256"],
                "consumption_file_sha256": _sha_file(consumption_path),
                "consumption_payload_sha256": json.loads(
                    consumption_path.read_text("ascii")
                )["payload_sha256"],
            }
            self.assertEqual(result["qualification_status"], "PASS")
            self.assertEqual(
                calls,
                ["models"]
                + [value for _ in range(14) for value in ("models", "chat")],
            )
            self.assertEqual(manifest["live_authorization_binding"], expected_binding)
            self.assertEqual(
                verification["live_authorization_binding"], expected_binding
            )
            self.assertEqual(
                _sha_file(run_dir / "live_authorization.json"),
                expected_binding["authorization_file_sha256"],
            )
            self.assertEqual(
                _sha_file(run_dir / "live_authorization_consumption.json"),
                expected_binding["consumption_file_sha256"],
            )


if __name__ == "__main__":
    import unittest

    unittest.main()
