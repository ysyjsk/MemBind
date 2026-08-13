"""Intentional RED contracts for machine-verifiable Judge test evidence.

The pre-live gate must authorize structured, sealed evidence reports rather
than trusting the mere existence of unittest text logs.  This module is fully
offline: it creates only disposable files below ``artifacts`` and never calls
the live runner or any HTTP client.
"""

from __future__ import annotations

import hashlib
import json
import sys
import tempfile
import unittest
from copy import deepcopy
from pathlib import Path
from unittest import TestCase


ROOT = Path(__file__).resolve().parents[1]
REPOSITORY_ROOT = ROOT.parent
sys.path.insert(0, str(ROOT / "src"))

from evaluation.judge_qualification import (  # noqa: E402
    build_strict_judge_qualification_freeze,
    canonical_json_bytes,
)
import evaluation.judge_qualification_live as live_module  # noqa: E402
from evaluation.judge_qualification_live import (  # noqa: E402
    JudgeQualificationLiveError,
)


WORKPLAN = REPOSITORY_ROOT / "MemBind_JUDGE_QUALIFICATION_WORKPLAN_v1.0.md"
FIXTURE = ROOT / "fixtures/judge_qualification_14_v1.json"
OFFLINE_MANIFEST = ROOT / "artifacts/protocol/judge_upstream_manifest_20260812.json"
CORE_SOURCE = ROOT / "src/evaluation/judge_qualification.py"
LIVE_SOURCE = ROOT / "src/evaluation/judge_qualification_live.py"
Q3_SOURCE = ROOT / "src/evaluation/judge_qualification_q3.py"
Q3_TEST = ROOT / "tests/test_judge_qualification_q3_dry_run.py"
DEPLOYMENT_EVIDENCE = (
    ROOT / "artifacts/environment/judge_deployment_evidence_20260813.json"
)
IMPACT_TESTS = (
    ROOT / "tests/test_evaluator_registry.py",
    ROOT / "tests/test_longmemeval_adapter.py",
    ROOT / "tests/test_qwen3_judge_backend.py",
)
Q3_SCENARIOS = (
    "full_pass",
    "invalid_stop",
    "service_error_stop",
    "tamper",
    "ambiguous_inflight",
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


def _write_canonical(path: Path, value: dict[str, object]) -> None:
    path.write_bytes(canonical_json_bytes(value) + b"\n")


def _binding(path: Path, *, payload: bool = False) -> dict[str, str]:
    result = {
        "path": path.relative_to(ROOT).as_posix(),
        "sha256": _sha_file(path),
    }
    if payload:
        value = json.loads(path.read_text("ascii"))
        result["payload_sha256"] = value["payload_sha256"]
    return result


def _module_name(path: Path) -> str:
    return ".".join(path.relative_to(ROOT).with_suffix("").parts)


def _test_count(paths: tuple[Path, ...] | list[Path]) -> int:
    loader = unittest.TestLoader()
    suite = loader.loadTestsFromNames([_module_name(path) for path in paths])
    failures = [
        test
        for group in suite
        for test in (group if isinstance(group, unittest.TestSuite) else (group,))
        if isinstance(test, unittest.loader._FailedTest)
    ]
    if failures:
        raise AssertionError(f"test inventory does not load: {failures!r}")
    return suite.countTestCases()


def _focused_tests() -> tuple[Path, ...]:
    return tuple(
        sorted(
            path
            for path in (ROOT / "tests").glob("test_judge*.py")
            if path.is_file() and not path.is_symlink()
        )
    )


def _raw_unittest_log(test_count: int) -> str:
    return (
        "machine-evidence suite completed\n"
        "----------------------------------------------------------------------\n"
        f"Ran {test_count} tests in 0.001s\n\n"
        "OK\n"
    )


def _q3_summary() -> dict[str, object]:
    return {
        "schema_version": "membind.judge-q3-dry-run-summary.v1",
        "status": "GREEN",
        "scenarios": list(Q3_SCENARIOS),
        "real_external_requests": 0,
        "live_authorization_created": False,
    }


class EvidenceFixture:
    """Build disposable candidate inputs without implementing production APIs."""

    def __init__(self, directory: Path) -> None:
        self.directory = directory
        self.focused_tests = _focused_tests()
        self.impact_tests = IMPACT_TESTS
        self.q3_sources = (Q3_SOURCE, Q3_TEST)
        self.counts = {
            "focused": _test_count(self.focused_tests),
            "impact": _test_count(list(self.impact_tests)),
            "q3": _test_count([Q3_TEST]),
        }
        self.logs: dict[str, Path] = {}
        for suite_id, count in self.counts.items():
            path = directory / f"{suite_id}.log"
            path.write_text(_raw_unittest_log(count), encoding="ascii")
            self.logs[suite_id] = path

    def build_report(self, suite_id: str) -> dict[str, object]:
        builder = getattr(live_module, "build_judge_test_evidence_report")
        sources = {
            "focused": self.focused_tests,
            "impact": self.impact_tests,
            "q3": self.q3_sources,
        }[suite_id]
        return builder(
            validation_root=ROOT,
            suite_id=suite_id,
            status="GREEN",
            exit_code=0,
            test_count=self.counts[suite_id],
            source_paths=[path.relative_to(ROOT) for path in sources],
            raw_log_path=self.logs[suite_id].relative_to(ROOT),
            q3_summary=_q3_summary() if suite_id == "q3" else None,
        )


class JudgeTestEvidenceReportRedTests(TestCase):
    def test_reports_are_sealed_and_bind_dynamic_final_inventories(self) -> None:
        validator = getattr(live_module, "validate_judge_test_evidence_report")
        with tempfile.TemporaryDirectory(dir=ROOT / "artifacts") as temporary:
            fixture = EvidenceFixture(Path(temporary))
            reports = {
                suite_id: fixture.build_report(suite_id)
                for suite_id in ("focused", "impact", "q3")
            }

            for suite_id, report in reports.items():
                with self.subTest(suite_id=suite_id):
                    self.assertEqual(validator(report, ROOT), report)
                    self.assertEqual(
                        report["schema_version"],
                        "membind.judge-test-evidence.v1",
                    )
                    self.assertEqual(report["scientific_surface"], "JUDGE_QUALIFICATION_ONLY")
                    self.assertEqual(report["suite_id"], suite_id)
                    self.assertEqual(report["status"], "GREEN")
                    self.assertEqual(report["exit_code"], 0)
                    self.assertEqual(report["test_count"], fixture.counts[suite_id])
                    self.assertEqual(
                        report["raw_log"],
                        _binding(fixture.logs[suite_id]),
                    )
                    self.assertEqual(
                        report["payload_sha256"],
                        _seal({key: value for key, value in report.items() if key != "payload_sha256"})[
                            "payload_sha256"
                        ],
                    )

            self.assertEqual(
                [entry["path"] for entry in reports["focused"]["source_inventory"]],
                [path.relative_to(ROOT).as_posix() for path in fixture.focused_tests],
            )
            self.assertEqual(
                [entry["path"] for entry in reports["impact"]["source_inventory"]],
                [path.relative_to(ROOT).as_posix() for path in IMPACT_TESTS],
            )
            q3 = reports["q3"]
            self.assertEqual(q3["q3_summary"], _q3_summary())
            self.assertEqual(
                [entry["path"] for entry in q3["source_inventory"]],
                [Q3_SOURCE.relative_to(ROOT).as_posix(), Q3_TEST.relative_to(ROOT).as_posix()],
            )

    def test_builder_rejects_false_green_counts_inventories_and_q3_claims(self) -> None:
        builder = getattr(live_module, "build_judge_test_evidence_report")
        with tempfile.TemporaryDirectory(dir=ROOT / "artifacts") as temporary:
            fixture = EvidenceFixture(Path(temporary))
            focused = [path.relative_to(ROOT) for path in fixture.focused_tests]
            base = {
                "validation_root": ROOT,
                "suite_id": "focused",
                "status": "GREEN",
                "exit_code": 0,
                "test_count": fixture.counts["focused"],
                "source_paths": focused,
                "raw_log_path": fixture.logs["focused"].relative_to(ROOT),
                "q3_summary": None,
            }
            cases = {
                "red_status": {"status": "RED"},
                "nonzero_exit": {"exit_code": 1},
                "wrong_count": {"test_count": fixture.counts["focused"] - 1},
                "omitted_source": {"source_paths": focused[:-1]},
                "reordered_source": {"source_paths": list(reversed(focused))},
                "q3_claim_on_focused": {"q3_summary": _q3_summary()},
            }
            for label, changes in cases.items():
                with self.subTest(label=label), self.assertRaisesRegex(
                    JudgeQualificationLiveError,
                    "test evidence|test count|inventory|GREEN|exit|Q3|unittest",
                ):
                    builder(**(base | changes))

            q3_base = base | {
                "suite_id": "q3",
                "test_count": fixture.counts["q3"],
                "source_paths": [
                    Q3_SOURCE.relative_to(ROOT),
                    Q3_TEST.relative_to(ROOT),
                ],
                "raw_log_path": fixture.logs["q3"].relative_to(ROOT),
                "q3_summary": _q3_summary(),
            }
            for field, bad in {
                "scenarios": list(Q3_SCENARIOS[:-1]),
                "real_external_requests": 1,
                "live_authorization_created": True,
            }.items():
                changed = deepcopy(q3_base)
                changed["q3_summary"][field] = bad
                with self.subTest(q3_field=field), self.assertRaisesRegex(
                    JudgeQualificationLiveError, "Q3|test evidence"
                ):
                    builder(**changed)

    def test_validator_deeply_rejects_resealed_report_or_raw_log_drift(self) -> None:
        validator = getattr(live_module, "validate_judge_test_evidence_report")
        with tempfile.TemporaryDirectory(dir=ROOT / "artifacts") as temporary:
            fixture = EvidenceFixture(Path(temporary))
            baseline = fixture.build_report("focused")
            mutations = {
                "status": lambda value: value.__setitem__("status", "GREENISH"),
                "exit": lambda value: value.__setitem__("exit_code", 0.0),
                "count": lambda value: value.__setitem__(
                    "test_count", fixture.counts["focused"] - 1
                ),
                "inventory": lambda value: value["source_inventory"].pop(),
                "raw_hash": lambda value: value["raw_log"].__setitem__(
                    "sha256", "f" * 64
                ),
            }
            for label, mutation in mutations.items():
                changed = deepcopy(baseline)
                changed.pop("payload_sha256")
                mutation(changed)
                changed = _seal(changed)
                with self.subTest(label=label), self.assertRaisesRegex(
                    JudgeQualificationLiveError,
                    "test evidence|test count|inventory|GREEN|exit|log|unittest",
                ):
                    validator(changed, ROOT)

            fixture.logs["focused"].write_text(
                _raw_unittest_log(fixture.counts["focused"] - 1), encoding="ascii"
            )
            with self.assertRaisesRegex(
                JudgeQualificationLiveError, "test evidence|test count|log|unittest"
            ):
                validator(baseline, ROOT)


class JudgePreliveStructuredEvidenceRedTests(TestCase):
    def test_prelive_manifest_binds_and_deeply_validates_three_reports(self) -> None:
        prelive_builder = getattr(live_module, "build_judge_prelive_evidence_manifest")
        prelive_validator = getattr(live_module, "validate_judge_prelive_evidence_manifest")
        with tempfile.TemporaryDirectory(dir=ROOT / "artifacts") as temporary:
            directory = Path(temporary)
            fixture = EvidenceFixture(directory)
            reports: dict[str, tuple[Path, dict[str, object]]] = {}
            for suite_id in ("focused", "impact", "q3"):
                value = fixture.build_report(suite_id)
                path = directory / f"{suite_id}-evidence.json"
                _write_canonical(path, value)
                reports[suite_id] = (path, value)

            freeze = build_strict_judge_qualification_freeze(
                validation_root=ROOT,
                fixture_path=FIXTURE.relative_to(ROOT),
                offline_manifest_path=OFFLINE_MANIFEST.relative_to(ROOT),
                qualification_source_path=CORE_SOURCE.relative_to(ROOT),
                qualification_live_source_path=LIVE_SOURCE.relative_to(ROOT),
            )
            freeze_path = directory / "strict-freeze.json"
            _write_canonical(freeze_path, freeze)
            run_id = "jq-7171717171717171"
            manifest = prelive_builder(
                validation_root=ROOT,
                authorized_run_id=run_id,
                workplan_path=WORKPLAN,
                qualification_source_path=CORE_SOURCE.relative_to(ROOT),
                qualification_live_source_path=LIVE_SOURCE.relative_to(ROOT),
                qualification_q3_source_path=Q3_SOURCE.relative_to(ROOT),
                judge_test_paths=[path.relative_to(ROOT) for path in fixture.focused_tests],
                qualification_fixture_path=FIXTURE.relative_to(ROOT),
                offline_manifest_path=OFFLINE_MANIFEST.relative_to(ROOT),
                deployment_evidence_path=DEPLOYMENT_EVIDENCE.relative_to(ROOT),
                final_focused_report_path=reports["focused"][0].relative_to(ROOT),
                final_impact_report_path=reports["impact"][0].relative_to(ROOT),
                final_q3_dry_run_report_path=reports["q3"][0].relative_to(ROOT),
                strict_freeze_path=freeze_path.relative_to(ROOT),
                live_run_limit=1,
            )
            self.assertEqual(prelive_validator(manifest, ROOT), manifest)
            self.assertNotIn("final_focused_log", manifest["bindings"])
            for suite_id, binding_name in {
                "focused": "final_focused_report",
                "impact": "final_impact_report",
                "q3": "final_q3_dry_run_report",
            }.items():
                path, value = reports[suite_id]
                self.assertEqual(
                    manifest["bindings"][binding_name],
                    {
                        "path": path.relative_to(ROOT).as_posix(),
                        "sha256": _sha_file(path),
                        "payload_sha256": value["payload_sha256"],
                    },
                )

            focused_path, focused_report = reports["focused"]
            changed = deepcopy(focused_report)
            changed.pop("payload_sha256")
            changed["test_count"] -= 1
            _write_canonical(focused_path, _seal(changed))
            changed_manifest = deepcopy(manifest)
            changed_manifest.pop("payload_sha256")
            changed_manifest["bindings"]["final_focused_report"] = _binding(
                focused_path, payload=True
            )
            changed_manifest = _seal(changed_manifest)
            with self.assertRaisesRegex(
                JudgeQualificationLiveError,
                "pre-live|test evidence|test count|focused",
            ):
                prelive_validator(changed_manifest, ROOT)


if __name__ == "__main__":
    unittest.main()
