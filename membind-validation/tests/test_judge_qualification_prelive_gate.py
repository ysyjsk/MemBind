"""Intentional RED contracts for the formal Judge pre-live evidence gate.

This module specifies the last offline gate before the one permitted live run.
It performs no real network I/O and creates no real authorization.  Every HTTP
client is backed by ``httpx.MockTransport`` and a socket guard stays active.

Expected production API
-----------------------
``evaluation.judge_qualification_live`` exports::

    build_judge_prelive_evidence_manifest(...)
    validate_judge_prelive_evidence_manifest(value, validation_root)

The builder returns a canonical, sealed
``membind.judge-prelive-evidence-manifest.v1`` object.  The formal runner gains
one keyword argument::

    prelive_evidence_binding={"path": <validation-root-relative path>,
                              "sha256": <manifest file SHA256>}

Authorization schema ``membind.judge-live-authorization.v1`` remains v1 for
backward artifact readability, but the formal live lane requires two new exact
fields: ``prelive_evidence_manifest_file_sha256`` and
``prelive_evidence_manifest_payload_sha256``.  Existing direct bindings to the
strict freeze, live source, and deployment evidence stay required.  Before an
authorization receipt is created, the runner must load and deeply validate the
pre-live manifest and every file/payload binding it contains.
"""

from __future__ import annotations

import hashlib
import json
import socket
import sys
import tempfile
from copy import deepcopy
from pathlib import Path
from unittest import IsolatedAsyncioTestCase, TestCase, mock

import httpx


ROOT = Path(__file__).resolve().parents[1]
REPOSITORY_ROOT = ROOT.parent
sys.path.insert(0, str(ROOT / "src"))

from evaluation.judge_qualification import (  # noqa: E402
    build_strict_judge_qualification_freeze,
    canonical_json_bytes,
    verify_judge_qualification_artifacts,
)
import evaluation.judge_qualification_live as live_module  # noqa: E402
from evaluation.judge_qualification_live import (  # noqa: E402
    JudgeQualificationLiveError,
    load_verified_judge_deployment_evidence,
    run_formal_judge_qualification,
)
from tests.judge_test_evidence_fixture import (  # noqa: E402
    write_test_evidence_reports,
)


WORKPLAN = REPOSITORY_ROOT / "MemBind_JUDGE_QUALIFICATION_WORKPLAN_v1.0.md"
WORKPLAN_SHA256 = "a2a2d59c538131dae8cb412fed8a4e40ce339a6db321af7e554cf1c2f66f93d8"
FIXTURE = ROOT / "fixtures/judge_qualification_14_v1.json"
OFFLINE_MANIFEST = ROOT / "artifacts/protocol/judge_upstream_manifest_20260812.json"
CORE_SOURCE = ROOT / "src/evaluation/judge_qualification.py"
LIVE_SOURCE = ROOT / "src/evaluation/judge_qualification_live.py"
Q3_SOURCE = ROOT / "src/evaluation/judge_qualification_q3.py"
DEPLOYMENT_EVIDENCE = (
    ROOT / "artifacts/environment/judge_deployment_evidence_20260813.json"
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


def _json_payload_sha256(path: Path) -> str:
    value = json.loads(path.read_text("ascii"))
    observed = value.get("payload_sha256")
    if isinstance(observed, str):
        candidate = deepcopy(value)
        candidate.pop("payload_sha256")
        assert observed == _sha_bytes(canonical_json_bytes(candidate))
        return observed
    return _sha_bytes(canonical_json_bytes(value))


def _binding(path: Path, *, payload: bool = False) -> dict[str, str]:
    result = {
        "path": path.relative_to(ROOT).as_posix(),
        "sha256": _sha_file(path),
    }
    if payload:
        result["payload_sha256"] = _json_payload_sha256(path)
    return result


def _write_canonical(path: Path, value: dict[str, object]) -> None:
    path.write_bytes(canonical_json_bytes(value) + b"\n")


class PreliveFixture:
    """Build only disposable offline evidence under a temporary root."""

    def __init__(self, directory: Path, run_id: str) -> None:
        self.directory = directory
        self.run_id = run_id
        self.freeze = build_strict_judge_qualification_freeze(
            validation_root=ROOT,
            fixture_path=FIXTURE.relative_to(ROOT),
            offline_manifest_path=OFFLINE_MANIFEST.relative_to(ROOT),
            qualification_source_path=CORE_SOURCE.relative_to(ROOT),
            qualification_live_source_path=LIVE_SOURCE.relative_to(ROOT),
        )
        self.freeze_path = directory / "strict-freeze.json"
        _write_canonical(self.freeze_path, self.freeze)

        self.reports = write_test_evidence_reports(directory, ROOT)

        self.judge_tests = sorted((ROOT / "tests").glob("test_judge*.py"))
        test_bindings = [_binding(path) for path in self.judge_tests]
        self.manifest = _seal(
            {
                "schema_version": "membind.judge-prelive-evidence-manifest.v1",
                "protocol_id": "judge-qualification-v1.0",
                "scientific_surface": "JUDGE_QUALIFICATION_ONLY",
                "authorized_run_id": run_id,
                "live_run_limit": 1,
                "workplan_sha256": WORKPLAN_SHA256,
                "judge_tests_aggregate_sha256": _sha_bytes(
                    canonical_json_bytes(test_bindings)
                ),
                "bindings": {
                    "qualification_source": _binding(CORE_SOURCE),
                    "qualification_live_source": _binding(LIVE_SOURCE),
                    "qualification_q3_source": _binding(Q3_SOURCE),
                    "judge_tests": test_bindings,
                    "qualification_fixture": _binding(FIXTURE, payload=True),
                    "offline_manifest": _binding(OFFLINE_MANIFEST, payload=True),
                    "deployment_evidence": _binding(
                        DEPLOYMENT_EVIDENCE, payload=True
                    ),
                    "final_focused_report": _binding(
                        self.reports["focused"], payload=True
                    ),
                    "final_impact_report": _binding(
                        self.reports["impact"], payload=True
                    ),
                    "final_q3_dry_run_report": _binding(
                        self.reports["q3"], payload=True
                    ),
                    "strict_freeze": _binding(self.freeze_path, payload=True),
                },
            }
        )
        self.manifest_path = directory / "judge-prelive-evidence-manifest.json"
        _write_canonical(self.manifest_path, self.manifest)
        self.manifest_binding = _binding(self.manifest_path)

        deployment = load_verified_judge_deployment_evidence(
            ROOT,
            DEPLOYMENT_EVIDENCE.relative_to(ROOT),
            _sha_file(DEPLOYMENT_EVIDENCE),
        )
        self.authorization_path = directory / "judge-live-authorization.json"
        self.authorization = _seal(
            {
                "schema_version": "membind.judge-live-authorization.v1",
                "protocol_id": "judge-qualification-v1.0",
                "scientific_surface": "JUDGE_QUALIFICATION_ONLY",
                "authorization_id": "jqa-prelive-red-only",
                "authorized_run_id": run_id,
                "authorization_path": self.authorization_path.relative_to(
                    ROOT
                ).as_posix(),
                "live_run_limit": 1,
                "freeze_payload_sha256": self.freeze["payload_sha256"],
                "qualification_live_source_sha256": _sha_file(LIVE_SOURCE),
                "deployment_evidence_payload_sha256": deployment[
                    "evidence_payload_sha256"
                ],
                "prelive_evidence_manifest_file_sha256": _sha_file(
                    self.manifest_path
                ),
                "prelive_evidence_manifest_payload_sha256": self.manifest[
                    "payload_sha256"
                ],
            }
        )
        _write_canonical(self.authorization_path, self.authorization)
        self.authorization_binding = _binding(self.authorization_path)

    @property
    def consumption_path(self) -> Path:
        return self.authorization_path.with_name(
            self.authorization_path.name + ".consumed.json"
        )

    def rewrite_manifest(
        self, mutate: object
    ) -> tuple[dict[str, str], dict[str, str]]:
        value = deepcopy(self.manifest)
        value.pop("payload_sha256")
        mutate(value)
        self.manifest = _seal(value)
        _write_canonical(self.manifest_path, self.manifest)

        authorization = deepcopy(self.authorization)
        authorization.pop("payload_sha256")
        authorization["prelive_evidence_manifest_file_sha256"] = _sha_file(
            self.manifest_path
        )
        authorization["prelive_evidence_manifest_payload_sha256"] = self.manifest[
            "payload_sha256"
        ]
        self.authorization = _seal(authorization)
        _write_canonical(self.authorization_path, self.authorization)
        return _binding(self.manifest_path), _binding(self.authorization_path)

    def rebind_authorization_to_manifest(self) -> dict[str, str]:
        """Keep authorization hashes current so outer-manifest gates are isolated."""

        authorization = deepcopy(self.authorization)
        authorization.pop("payload_sha256")
        authorization["prelive_evidence_manifest_file_sha256"] = _sha_file(
            self.manifest_path
        )
        authorization["prelive_evidence_manifest_payload_sha256"] = self.manifest[
            "payload_sha256"
        ]
        self.authorization = _seal(authorization)
        _write_canonical(self.authorization_path, self.authorization)
        self.authorization_binding = _binding(self.authorization_path)
        return self.authorization_binding


class JudgePreliveManifestRedTests(TestCase):
    def test_builder_and_validator_produce_exact_complete_manifest(self) -> None:
        builder = getattr(live_module, "build_judge_prelive_evidence_manifest")
        validator = getattr(live_module, "validate_judge_prelive_evidence_manifest")
        run_id = "jq-1010101010101010"
        with tempfile.TemporaryDirectory(dir=ROOT / "artifacts") as temporary:
            fixture = PreliveFixture(Path(temporary), run_id)
            built = builder(
                validation_root=ROOT,
                authorized_run_id=run_id,
                workplan_path=WORKPLAN,
                qualification_source_path=CORE_SOURCE.relative_to(ROOT),
                qualification_live_source_path=LIVE_SOURCE.relative_to(ROOT),
                qualification_q3_source_path=Q3_SOURCE.relative_to(ROOT),
                judge_test_paths=[path.relative_to(ROOT) for path in fixture.judge_tests],
                qualification_fixture_path=FIXTURE.relative_to(ROOT),
                offline_manifest_path=OFFLINE_MANIFEST.relative_to(ROOT),
                deployment_evidence_path=DEPLOYMENT_EVIDENCE.relative_to(ROOT),
                final_focused_report_path=fixture.reports["focused"].relative_to(ROOT),
                final_impact_report_path=fixture.reports["impact"].relative_to(ROOT),
                final_q3_dry_run_report_path=fixture.reports["q3"].relative_to(ROOT),
                strict_freeze_path=fixture.freeze_path.relative_to(ROOT),
                live_run_limit=1,
            )

            self.assertEqual(built, fixture.manifest)
            self.assertEqual(validator(built, ROOT), built)
            self.assertEqual(
                set(built["bindings"]),
                {
                    "qualification_source",
                    "qualification_live_source",
                    "qualification_q3_source",
                    "judge_tests",
                    "qualification_fixture",
                    "offline_manifest",
                    "deployment_evidence",
                    "final_focused_report",
                    "final_impact_report",
                    "final_q3_dry_run_report",
                    "strict_freeze",
                },
            )
            self.assertEqual(
                [entry["path"] for entry in built["bindings"]["judge_tests"]],
                sorted(
                    path.relative_to(ROOT).as_posix() for path in fixture.judge_tests
                ),
            )

    def test_builder_rejects_incomplete_nonunique_or_reordered_test_inventory(
        self,
    ) -> None:
        builder = getattr(live_module, "build_judge_prelive_evidence_manifest")
        run_id = "jq-1111111111111111"
        with tempfile.TemporaryDirectory(dir=ROOT / "artifacts") as temporary:
            fixture = PreliveFixture(Path(temporary), run_id)
            expected = [path.relative_to(ROOT) for path in fixture.judge_tests]
            extra = Path("tests/test_qwen3_judge_backend.py")
            cases = {
                "omitted": expected[:-1],
                "added": expected + [extra],
                "duplicate": expected + [expected[-1]],
                "reordered": list(reversed(expected)),
            }
            for label, supplied in cases.items():
                with self.subTest(label=label), self.assertRaisesRegex(
                    JudgeQualificationLiveError, "test set|Judge test|pre-live"
                ):
                    builder(
                        validation_root=ROOT,
                        authorized_run_id=run_id,
                        workplan_path=WORKPLAN,
                        qualification_source_path=CORE_SOURCE.relative_to(ROOT),
                        qualification_live_source_path=LIVE_SOURCE.relative_to(ROOT),
                        qualification_q3_source_path=Q3_SOURCE.relative_to(ROOT),
                        judge_test_paths=supplied,
                        qualification_fixture_path=FIXTURE.relative_to(ROOT),
                        offline_manifest_path=OFFLINE_MANIFEST.relative_to(ROOT),
                        deployment_evidence_path=DEPLOYMENT_EVIDENCE.relative_to(ROOT),
                        final_focused_report_path=fixture.reports["focused"].relative_to(ROOT),
                        final_impact_report_path=fixture.reports["impact"].relative_to(ROOT),
                        final_q3_dry_run_report_path=fixture.reports["q3"].relative_to(ROOT),
                        strict_freeze_path=fixture.freeze_path.relative_to(ROOT),
                        live_run_limit=1,
                    )

    def test_validator_rejects_resealed_test_inventory_or_q3_source_drift(self) -> None:
        validator = getattr(live_module, "validate_judge_prelive_evidence_manifest")
        mutations = {
            "omitted": lambda value: value["bindings"]["judge_tests"].pop(),
            "added": lambda value: value["bindings"]["judge_tests"].append(
                _binding(ROOT / "tests/test_qwen3_judge_backend.py")
            ),
            "duplicate": lambda value: value["bindings"]["judge_tests"].append(
                deepcopy(value["bindings"]["judge_tests"][-1])
            ),
            "reordered": lambda value: value["bindings"].__setitem__(
                "judge_tests", list(reversed(value["bindings"]["judge_tests"]))
            ),
            "q3_source": lambda value: value["bindings"][
                "qualification_q3_source"
            ].__setitem__("sha256", "f" * 64),
        }
        for label, mutation in mutations.items():
            with self.subTest(label=label), tempfile.TemporaryDirectory(
                dir=ROOT / "artifacts"
            ) as temporary:
                fixture = PreliveFixture(Path(temporary), "jq-1212121212121212")
                changed = deepcopy(fixture.manifest)
                changed.pop("payload_sha256")
                mutation(changed)
                changed["judge_tests_aggregate_sha256"] = _sha_bytes(
                    canonical_json_bytes(changed["bindings"]["judge_tests"])
                )
                changed = _seal(changed)
                with self.assertRaisesRegex(
                    JudgeQualificationLiveError, "test set|Judge test|Q3|pre-live"
                ):
                    validator(changed, ROOT)


class JudgePreliveAuthorizationRedTests(IsolatedAsyncioTestCase):
    def _transports(
        self, fixture: PreliveFixture, calls: list[str]
    ) -> tuple[httpx.MockTransport, httpx.MockTransport]:
        labels = iter(
            "YES" if item["human_label"] else "NO" for item in fixture.freeze["items"]
        )

        def assert_consumed_before_request() -> None:
            self.assertTrue(
                fixture.consumption_path.is_file(),
                "live authorization must be consumed before the first HTTP request",
            )
            receipt = json.loads(fixture.consumption_path.read_text("ascii"))
            self.assertEqual(receipt["status"], "consumed_before_first_request")
            self.assertEqual(
                receipt["prelive_evidence_manifest_payload_sha256"],
                fixture.manifest["payload_sha256"],
            )

        def models_handler(_request: httpx.Request) -> httpx.Response:
            assert_consumed_before_request()
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
            assert_consumed_before_request()
            calls.append("chat")
            return httpx.Response(
                200,
                json={
                    "id": "mock-prelive-red",
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

    async def _run(
        self,
        fixture: PreliveFixture,
        calls: list[str],
        *,
        prelive_binding: dict[str, str] | None,
        authorization_binding: dict[str, str] | None = None,
    ) -> dict[str, object]:
        models_transport, chat_transport = self._transports(fixture, calls)
        kwargs: dict[str, object] = {
            "validation_root": ROOT,
            "runs_root": fixture.directory / "runs",
            "run_id": fixture.run_id,
            "freeze": fixture.freeze,
            "config_mapping": {
                "base_url": "http://judge.private.invalid/v1",
                "api_key": "PRIVATE-JUDGE-CREDENTIAL",
            },
            "deployment_evidence_binding": _binding(DEPLOYMENT_EVIDENCE),
            "authorization_binding": (
                authorization_binding or fixture.authorization_binding
            ),
            "models_transport": models_transport,
            "chat_transport": chat_transport,
        }
        if prelive_binding is not None:
            kwargs["prelive_evidence_binding"] = prelive_binding
        return await run_formal_judge_qualification(**kwargs)

    async def test_complete_prelive_closure_is_consumed_before_mock_requests(self) -> None:
        run_id = "jq-2020202020202020"
        with tempfile.TemporaryDirectory(dir=ROOT / "artifacts") as temporary, mock.patch.object(
            socket.socket,
            "connect",
            side_effect=AssertionError("real network forbidden in pre-live gate test"),
        ):
            fixture = PreliveFixture(Path(temporary), run_id)
            calls: list[str] = []
            result = await self._run(
                fixture,
                calls,
                prelive_binding=fixture.manifest_binding,
            )
            self.assertEqual(result["qualification_status"], "PASS")
            self.assertTrue(fixture.consumption_path.is_file())
            receipt = json.loads(fixture.consumption_path.read_text("ascii"))
            self.assertEqual(
                receipt["prelive_evidence_manifest_file_sha256"],
                _sha_file(fixture.manifest_path),
            )
            self.assertEqual(
                receipt["prelive_evidence_manifest_payload_sha256"],
                fixture.manifest["payload_sha256"],
            )
            self.assertEqual(len(calls), 29)

            run_dir = fixture.directory / "runs" / run_id
            copied = run_dir / "prelive_evidence_manifest.json"
            self.assertEqual(copied.read_bytes(), fixture.manifest_path.read_bytes())
            expected_binding = {
                "manifest_file_sha256": _sha_file(fixture.manifest_path),
                "manifest_payload_sha256": fixture.manifest["payload_sha256"],
            }
            run_manifest = json.loads(run_dir.joinpath("manifest.json").read_text("ascii"))
            verification = verify_judge_qualification_artifacts(
                run_dir, fixture.freeze
            )
            self.assertEqual(
                run_manifest["prelive_evidence_binding"], expected_binding
            )
            self.assertEqual(
                verification["prelive_evidence_binding"], expected_binding
            )

    async def test_modern_authorization_without_manifest_binding_is_rejected_pre_http(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory(dir=ROOT / "artifacts") as temporary:
            fixture = PreliveFixture(Path(temporary), "jq-2121212121212121")
            calls: list[str] = []
            with self.assertRaisesRegex(
                JudgeQualificationLiveError, "pre-live|prelive|binding"
            ):
                await self._run(fixture, calls, prelive_binding=None)
            self.assertEqual(calls, [])
            self.assertFalse(fixture.consumption_path.exists())

    async def test_three_field_legacy_authorization_is_not_live_sufficient(self) -> None:
        run_id = "jq-3030303030303030"
        with tempfile.TemporaryDirectory(dir=ROOT / "artifacts") as temporary:
            fixture = PreliveFixture(Path(temporary), run_id)
            legacy = deepcopy(fixture.authorization)
            legacy.pop("payload_sha256")
            legacy.pop("prelive_evidence_manifest_file_sha256")
            legacy.pop("prelive_evidence_manifest_payload_sha256")
            legacy = _seal(legacy)
            _write_canonical(fixture.authorization_path, legacy)
            calls: list[str] = []
            with self.assertRaisesRegex(
                JudgeQualificationLiveError, "pre-live|prelive|authorization"
            ):
                await self._run(
                    fixture,
                    calls,
                    prelive_binding=None,
                    authorization_binding=_binding(fixture.authorization_path),
                )
            self.assertEqual(calls, [])
            self.assertFalse(fixture.consumption_path.exists())

    async def test_outer_manifest_boundary_fails_before_consumption_or_http(self) -> None:
        run_id = "jq-3131313131313131"
        cases = (
            "bad_sha",
            "noncanonical",
            "resealed_wrong_schema",
            "symlink",
            "path_escape",
        )
        for label in cases:
            with self.subTest(label=label), tempfile.TemporaryDirectory(
                dir=ROOT / "artifacts"
            ) as temporary:
                fixture = PreliveFixture(Path(temporary), run_id)
                manifest_binding = deepcopy(fixture.manifest_binding)

                if label == "bad_sha":
                    manifest_binding["sha256"] = "f" * 64
                elif label == "noncanonical":
                    fixture.manifest_path.write_text(
                        json.dumps(fixture.manifest, indent=2) + "\n",
                        encoding="ascii",
                    )
                    manifest_binding = _binding(fixture.manifest_path)
                    fixture.rebind_authorization_to_manifest()
                elif label == "resealed_wrong_schema":
                    changed = deepcopy(fixture.manifest)
                    changed.pop("payload_sha256")
                    changed["schema_version"] = "membind.judge-prelive-evil.v1"
                    fixture.manifest = _seal(changed)
                    _write_canonical(fixture.manifest_path, fixture.manifest)
                    manifest_binding = _binding(fixture.manifest_path)
                    fixture.rebind_authorization_to_manifest()
                elif label == "symlink":
                    real = fixture.manifest_path.with_name("prelive-real.json")
                    fixture.manifest_path.rename(real)
                    fixture.manifest_path.symlink_to(real.name)
                    manifest_binding = {
                        "path": fixture.manifest_path.relative_to(ROOT).as_posix(),
                        "sha256": _sha_file(real),
                    }
                else:
                    manifest_binding = {
                        "path": "../MemBind_JUDGE_QUALIFICATION_WORKPLAN_v1.0.md",
                        "sha256": _sha_file(WORKPLAN),
                    }

                calls: list[str] = []
                with self.assertRaisesRegex(
                    JudgeQualificationLiveError, "pre-live|prelive|path|binding"
                ):
                    await self._run(
                        fixture,
                        calls,
                        prelive_binding=manifest_binding,
                        authorization_binding=fixture.authorization_binding,
                    )
                self.assertEqual(calls, [])
                self.assertFalse(fixture.consumption_path.exists())

    async def test_every_prelive_binding_drift_fails_before_consumption_or_http(self) -> None:
        run_id = "jq-4040404040404040"

        def alter_binding(name: str, field: str = "sha256"):
            def mutate(value: dict[str, object]) -> None:
                value["bindings"][name][field] = "f" * 64

            return mutate

        mutations = {
            "workplan": lambda value: value.__setitem__(
                "workplan_sha256", "f" * 64
            ),
            "core_source": alter_binding("qualification_source"),
            "live_source": alter_binding("qualification_live_source"),
            "q3_source": alter_binding("qualification_q3_source"),
            "judge_test_file": lambda value: value["bindings"]["judge_tests"][
                0
            ].__setitem__("sha256", "f" * 64),
            "judge_tests_aggregate": lambda value: value.__setitem__(
                "judge_tests_aggregate_sha256", "f" * 64
            ),
            "fixture_file": alter_binding("qualification_fixture"),
            "fixture_payload": alter_binding(
                "qualification_fixture", "payload_sha256"
            ),
            "offline_file": alter_binding("offline_manifest"),
            "offline_payload": alter_binding("offline_manifest", "payload_sha256"),
            "deployment_file": alter_binding("deployment_evidence"),
            "deployment_payload": alter_binding(
                "deployment_evidence", "payload_sha256"
            ),
            "focused_report": alter_binding("final_focused_report"),
            "impact_report": alter_binding("final_impact_report"),
            "q3_report": alter_binding("final_q3_dry_run_report"),
            "strict_freeze_file": alter_binding("strict_freeze"),
            "strict_freeze_payload": alter_binding(
                "strict_freeze", "payload_sha256"
            ),
            "run_id": lambda value: value.__setitem__(
                "authorized_run_id", "jq-ffffffffffffffff"
            ),
            "run_limit": lambda value: value.__setitem__("live_run_limit", 2),
        }
        for label, mutation in mutations.items():
            with self.subTest(label=label), tempfile.TemporaryDirectory(
                dir=ROOT / "artifacts"
            ) as temporary:
                fixture = PreliveFixture(Path(temporary), run_id)
                manifest_binding, authorization_binding = fixture.rewrite_manifest(
                    mutation
                )
                calls: list[str] = []
                with self.assertRaisesRegex(
                    JudgeQualificationLiveError, "pre-live|prelive|authorization"
                ):
                    await self._run(
                        fixture,
                        calls,
                        prelive_binding=manifest_binding,
                        authorization_binding=authorization_binding,
                    )
                self.assertEqual(calls, [])
                self.assertFalse(fixture.consumption_path.exists())


if __name__ == "__main__":
    import unittest

    unittest.main()
