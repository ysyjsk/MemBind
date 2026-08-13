"""RED contracts for the bounded 14-item Qwen Judge qualification lane.

This module intentionally imports an implementation that does not exist yet.
The qualification is independent of C4/C5 and uses only a fake Judge backend.
"""

from __future__ import annotations

import hashlib
import json
import socket
import sys
import tempfile
from pathlib import Path
from unittest import IsolatedAsyncioTestCase, TestCase, mock


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from evaluation.backends.base import JudgeBackendResult  # noqa: E402
from evaluation.benchmarks.longmemeval import LongMemEvalAdapter  # noqa: E402
from evaluation.schemas import EvaluationItem, EvaluationResult, EvaluationStatus  # noqa: E402
from evaluation.vendor.longmemeval_evaluate_qa import get_anscheck_prompt  # noqa: E402
from evaluation.judge_qualification import (  # noqa: E402
    JUDGE_QUALIFICATION_ONLY,
    STRICT_PASS_GATE,
    JudgeQualificationArtifactError,
    JudgeQualificationArtifactStore,
    analyze_judge_qualification,
    build_judge_qualification_freeze,
    canonical_json_bytes,
    run_judge_qualification,
    validate_judge_qualification_freeze,
    verify_judge_qualification_artifacts,
)


ROUTES = (
    ("single-session-user", False, "single-session-user"),
    ("single-session-assistant", False, "single-session-assistant"),
    ("multi-session", False, "multi-session"),
    ("temporal-reasoning", False, "temporal-reasoning"),
    ("knowledge-update", False, "knowledge-update"),
    ("single-session-preference", False, "single-session-preference"),
    ("single-session-user", True, "abstention"),
)


def _canonical(value: object) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("ascii")


def _sha256(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _reseal(value: dict[str, object]) -> dict[str, object]:
    candidate = json.loads(json.dumps(value))
    candidate.pop("payload_sha256", None)
    candidate["payload_sha256"] = _sha256(_canonical(candidate))
    return candidate


def _official_prompt_hash(record: dict[str, object]) -> str:
    prompt = get_anscheck_prompt(
        str(record["question_type"]),
        str(record["question"]),
        str(record["reference_answer"]),
        str(record["hypothesis"]),
        bool(record["abstention"]),
    )
    return _sha256(prompt.encode("utf-8"))


def qualification_fixture() -> dict[str, object]:
    """Return the human-labeled fixture frozen before any Judge output."""

    items: list[dict[str, object]] = []
    for question_type, abstention, route_id in ROUTES:
        for human_label, suffix in ((True, "yes"), (False, "no")):
            item_id = f"qualification-{route_id}-{suffix}"
            items.append(
                {
                    "item_id": item_id,
                    "benchmark": "longmemeval",
                    "question_id": f"question-{route_id}-{suffix}",
                    "candidate_answer_id": f"candidate-{route_id}-{suffix}",
                    "question_type": question_type,
                    "question": f"Frozen question for {route_id} ({suffix})?",
                    "reference_answer": "frozen-reference",
                    "hypothesis": f"frozen-candidate-{suffix}",
                    "abstention": abstention,
                    "route_id": route_id,
                    "human_label": human_label,
                }
            )
    return {
        "schema_version": "membind.judge-qualification-fixture.v1",
        "scientific_surface": JUDGE_QUALIFICATION_ONLY,
        "items": items,
    }


def _evaluation_item(record: dict[str, object]) -> EvaluationItem:
    return EvaluationItem(
        item_id=str(record["item_id"]),
        benchmark=str(record["benchmark"]),
        question_id=str(record["question_id"]),
        question_type=str(record["question_type"]),
        question=str(record["question"]),
        reference_answer=str(record["reference_answer"]),
        hypothesis=str(record["hypothesis"]),
        abstention=bool(record["abstention"]),
    )


def _success_result(record: dict[str, object], predicted: bool | None = None) -> EvaluationResult:
    label = bool(record["human_label"]) if predicted is None else predicted
    normalized = "YES" if label else "NO"
    return EvaluationResult(
        item_id=str(record["item_id"]),
        benchmark="longmemeval",
        scorer="longmemeval_official_get_anscheck_prompt",
        judge_model="qwen3-32b-fp8",
        label=label,
        status=EvaluationStatus.SUCCESS,
        raw_output=normalized,
        normalized_output=normalized,
        parse_status=normalized,
        retry_count=0,
        error_class=None,
        prompt_hash=_official_prompt_hash(record),
        config_hash="c" * 64,
        metadata={"audit_label": label},
    )


def _invalid_result(record: dict[str, object]) -> EvaluationResult:
    return EvaluationResult(
        item_id=str(record["item_id"]),
        benchmark="longmemeval",
        scorer="longmemeval_official_get_anscheck_prompt",
        judge_model="qwen3-32b-fp8",
        # Official-compatible label is deliberately False. The analyzer must
        # exclude it instead of accidentally counting a true negative.
        label=False,
        status=EvaluationStatus.INVALID_OUTPUT,
        raw_output="maybe",
        normalized_output="maybe",
        parse_status="INVALID",
        retry_count=0,
        error_class=None,
        prompt_hash=_official_prompt_hash(record),
        config_hash="c" * 64,
        metadata={"audit_label": None},
    )


def _service_result(record: dict[str, object]) -> EvaluationResult:
    return EvaluationResult(
        item_id=str(record["item_id"]),
        benchmark="longmemeval",
        scorer="longmemeval_official_get_anscheck_prompt",
        judge_model="qwen3-32b-fp8",
        label=None,
        status=EvaluationStatus.SERVICE_ERROR,
        raw_output="",
        normalized_output="",
        parse_status="NOT_RUN",
        retry_count=0,
        error_class="openai.APIConnectionError",
        prompt_hash=_official_prompt_hash(record),
        config_hash="c" * 64,
        metadata={},
    )


class _SequenceBackend:
    model = "qwen3-32b-fp8"
    config_hash = "c" * 64

    def __init__(self, outcomes: list[str | BaseException]) -> None:
        self.outcomes = list(outcomes)
        self.prompts: list[str] = []

    async def judge(self, prompt: str) -> JudgeBackendResult:
        self.prompts.append(prompt)
        outcome = self.outcomes.pop(0)
        if isinstance(outcome, BaseException):
            return JudgeBackendResult.service_error(
                retry_count=0,
                error_class=f"{type(outcome).__module__}.{type(outcome).__name__}",
            )
        return JudgeBackendResult.success(raw_output=outcome, retry_count=0)


class QualificationHarness:
    """Build isolated, content-bound freeze and artifact fixtures."""

    def __init__(self, root: Path) -> None:
        self.root = root
        self.fixture = qualification_fixture()
        self.fixture_path = root / "fixtures/judge_qualification_14.json"
        self.offline_manifest_path = root / "artifacts/protocol/judge_upstream_manifest.json"
        self.source_path = root / "src/evaluation/judge_qualification.py"
        for path in (self.fixture_path, self.offline_manifest_path, self.source_path):
            path.parent.mkdir(parents=True, exist_ok=True)
        self.fixture_path.write_bytes(_canonical(self.fixture) + b"\n")
        self.offline_manifest_path.write_bytes(
            _canonical(
                {
                    "schema_version": "membind.judge-upstream-manifest.v1",
                    "status": "offline_implementation",
                    "payload_sha256": "a" * 64,
                }
            )
            + b"\n"
        )
        self.source_path.write_bytes(b'"""qualification implementation fixture"""\n')
        self.freeze = build_judge_qualification_freeze(
            validation_root=root,
            fixture_path=self.fixture_path.relative_to(root),
            fixture_sha256=_sha256(self.fixture_path.read_bytes()),
            offline_manifest_path=self.offline_manifest_path.relative_to(root),
            offline_manifest_sha256=_sha256(self.offline_manifest_path.read_bytes()),
            qualification_source_path=self.source_path.relative_to(root),
            qualification_source_sha256=_sha256(self.source_path.read_bytes()),
        )

    @property
    def items(self) -> list[dict[str, object]]:
        return list(self.fixture["items"])  # type: ignore[arg-type]

    @property
    def runtime_identity(self) -> dict[str, object]:
        return {
            "served_model_name": "qwen3-32b-fp8",
            "vllm_version": "0.26.0",
            "model_fingerprint": "f" * 64,
            "dtype": "bfloat16",
            "quantization": "fp8",
            "max_model_len": 65536,
            "rope_parameters": {
                "rope_type": "yarn",
                "factor": 2.0,
                "original_max_position_embeddings": 32768,
                "rope_theta": 1000000,
            },
            "chat_template_sha256": "d" * 64,
            "endpoint_identity_sha256": "e" * 64,
            "runtime_backend_config_hash": "c" * 64,
            "effective_enable_thinking": False,
            "temperature": 0,
            "max_tokens": 10,
            "n": 1,
            "python_version": "3.12",
            "openai_sdk_version": "2.53.0",
            "httpx_version": "0.28.1",
            "offline_manifest_file_sha256": _sha256(
                self.offline_manifest_path.read_bytes()
            ),
            "offline_manifest_payload_sha256": "a" * 64,
        }

    def create_store(self, run_id: str = "jq-0123456789abcdef") -> JudgeQualificationArtifactStore:
        return JudgeQualificationArtifactStore.create(
            runs_root=self.root / "artifacts/judge_qualification/runs",
            run_id=run_id,
            freeze=self.freeze,
            runtime_identity=self.runtime_identity,
            command_argv=["judge-qualification", "--frozen-14"],
        )


class JudgeQualificationFreezeTests(TestCase):
    maxDiff = None

    def test_freeze_binds_exact_14_balanced_routes_human_labels_sources_and_gate(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            harness = QualificationHarness(Path(temporary))
            freeze = harness.freeze

            self.assertEqual(freeze["scientific_surface"], JUDGE_QUALIFICATION_ONLY)
            self.assertEqual(len(freeze["items"]), 14)
            self.assertEqual(
                [(item["route_id"], item["human_label"]) for item in freeze["items"]],
                [(route_id, label) for _, _, route_id in ROUTES for label in (True, False)],
            )
            self.assertEqual(freeze["strict_pass_gate"], STRICT_PASS_GATE)
            self.assertEqual(
                freeze["strict_pass_gate"],
                {
                    "planned_item_count": 14,
                    "terminal_item_count": 14,
                    "eligible_item_count": 14,
                    "agreement_count": 14,
                    "invalid_output_count": 0,
                    "service_error_count": 0,
                    "retry_count_total": 0,
                    "confusion_matrix": {
                        "true_positive": 7,
                        "true_negative": 7,
                        "false_positive": 0,
                        "false_negative": 0,
                    },
                    "observed_agreement": 1.0,
                    "cohens_kappa": 1.0,
                },
            )
            self.assertEqual(
                set(freeze["bindings"]),
                {"offline_manifest", "qualification_fixture", "qualification_source"},
            )
            self.assertEqual(validate_judge_qualification_freeze(freeze, harness.root), freeze)

            for binding in freeze["bindings"].values():
                self.assertRegex(binding["sha256"], r"^[0-9a-f]{64}$")
            self.assertEqual(freeze["payload_sha256"], _sha256(canonical_json_bytes(
                {key: value for key, value in freeze.items() if key != "payload_sha256"}
            )))

    def test_freeze_rejects_route_label_gate_or_bound_file_drift(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            harness = QualificationHarness(Path(temporary))
            mutated = json.loads(json.dumps(harness.freeze))
            mutated["items"][0]["human_label"] = False
            mutated = _reseal(mutated)
            with self.assertRaises((JudgeQualificationArtifactError, ValueError)):
                validate_judge_qualification_freeze(mutated, harness.root)

            mutated = json.loads(json.dumps(harness.freeze))
            mutated["strict_pass_gate"]["agreement_count"] = 13
            mutated = _reseal(mutated)
            with self.assertRaises((JudgeQualificationArtifactError, ValueError)):
                validate_judge_qualification_freeze(mutated, harness.root)

        for binding_name in ("offline_manifest_path", "fixture_path", "source_path"):
            with self.subTest(binding_name=binding_name), tempfile.TemporaryDirectory() as temporary:
                harness = QualificationHarness(Path(temporary))
                bound_path = getattr(harness, binding_name)
                bound_path.write_bytes(bound_path.read_bytes() + b"\n")
                with self.assertRaises((JudgeQualificationArtifactError, ValueError)):
                    validate_judge_qualification_freeze(harness.freeze, harness.root)


class JudgeQualificationAnalysisTests(TestCase):
    def test_perfect_balanced_results_pass_with_exact_confusion_and_kappa(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            harness = QualificationHarness(Path(temporary))
            results = [_success_result(record) for record in harness.items]
            summary = analyze_judge_qualification(harness.freeze, results)

            self.assertEqual(summary["qualification_status"], "PASS")
            self.assertEqual(summary["eligible_item_count"], 14)
            self.assertEqual(summary["agreement_count"], 14)
            self.assertEqual(summary["invalid_output_count"], 0)
            self.assertEqual(summary["service_error_count"], 0)
            self.assertEqual(
                summary["confusion_matrix"],
                {"true_positive": 7, "true_negative": 7, "false_positive": 0, "false_negative": 0},
            )
            self.assertEqual(summary["cohens_kappa"], 1.0)
            self.assertEqual(summary["failed_gate_fields"], [])

    def test_invalid_and_service_outputs_are_excluded_not_coerced_to_false(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            harness = QualificationHarness(Path(temporary))
            false_items = [item for item in harness.items if item["human_label"] is False]
            invalid_id = false_items[0]["item_id"]
            service_id = false_items[1]["item_id"]
            results = []
            for record in harness.items:
                if record["item_id"] == invalid_id:
                    results.append(_invalid_result(record))
                elif record["item_id"] == service_id:
                    results.append(_service_result(record))
                else:
                    results.append(_success_result(record))
            summary = analyze_judge_qualification(harness.freeze, results)

            self.assertEqual(summary["qualification_status"], "FAIL")
            self.assertEqual(summary["eligible_item_count"], 12)
            self.assertEqual(summary["agreement_count"], 12)
            self.assertEqual(summary["invalid_output_count"], 1)
            self.assertEqual(summary["service_error_count"], 1)
            # The two ineligible human-NO items must not inflate true negatives.
            self.assertEqual(summary["confusion_matrix"]["true_negative"], 5)
            self.assertEqual(sum(summary["confusion_matrix"].values()), 12)
            self.assertIn("eligible_item_count", summary["failed_gate_fields"])
            self.assertIn("invalid_output_count", summary["failed_gate_fields"])
            self.assertIn("service_error_count", summary["failed_gate_fields"])

    def test_confusion_matrix_and_cohens_kappa_are_computed_from_eligible_items(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            harness = QualificationHarness(Path(temporary))
            true_flipped = False
            false_flipped = False
            results = []
            for record in harness.items:
                human = bool(record["human_label"])
                if human and not true_flipped:
                    predicted = False
                    true_flipped = True
                elif not human and not false_flipped:
                    predicted = True
                    false_flipped = True
                else:
                    predicted = human
                results.append(_success_result(record, predicted))
            summary = analyze_judge_qualification(harness.freeze, results)

            self.assertEqual(summary["qualification_status"], "FAIL")
            self.assertEqual(
                summary["confusion_matrix"],
                {"true_positive": 6, "true_negative": 6, "false_positive": 1, "false_negative": 1},
            )
            self.assertEqual(summary["agreement_count"], 12)
            self.assertAlmostEqual(summary["cohens_kappa"], 5 / 7)


class JudgeQualificationArtifactTests(TestCase):
    def test_store_is_canonical_exclusive_secret_safe_and_item_exactly_once(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            harness = QualificationHarness(Path(temporary))
            store = harness.create_store()
            manifest_raw = store.manifest_path.read_bytes()
            manifest = json.loads(manifest_raw.decode("ascii"))
            self.assertEqual(manifest_raw, canonical_json_bytes(manifest) + b"\n")
            self.assertEqual(manifest["runtime_identity"], harness.runtime_identity)
            rendered = manifest_raw.decode("ascii").casefold()
            for forbidden in ("api_key", "authorization", "bearer ", "base_url", "password"):
                self.assertNotIn(forbidden, rendered)

            with self.assertRaises((FileExistsError, JudgeQualificationArtifactError)):
                harness.create_store()

            first = harness.items[0]
            result = _success_result(first)
            path = store.write_item_result(
                item=_evaluation_item(first),
                candidate_answer_id=str(first["candidate_answer_id"]),
                human_label=bool(first["human_label"]),
                result=result,
            )
            before = path.read_bytes()
            self.assertEqual(before, canonical_json_bytes(json.loads(before.decode("ascii"))) + b"\n")
            with self.assertRaises((FileExistsError, JudgeQualificationArtifactError)):
                store.write_item_result(
                    item=_evaluation_item(first),
                    candidate_answer_id=str(first["candidate_answer_id"]),
                    human_label=bool(first["human_label"]),
                    result=result,
                )
            self.assertEqual(path.read_bytes(), before)

            unsafe_root = harness.root / "artifacts/judge_qualification/unsafe"
            with self.assertRaises((JudgeQualificationArtifactError, ValueError)):
                JudgeQualificationArtifactStore.create(
                    runs_root=unsafe_root,
                    run_id="judge-qualification-unsafe",
                    freeze=harness.freeze,
                    runtime_identity=harness.runtime_identity | {"api_key": "PRIVATE-SECRET"},
                    command_argv=["judge-qualification"],
                )
            self.assertFalse(unsafe_root.exists())

    def test_resume_runs_only_missing_items_and_requires_all_14_before_finalize(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            harness = QualificationHarness(Path(temporary))
            store = harness.create_store()
            for record in harness.items[:3]:
                store.write_item_result(
                    item=_evaluation_item(record),
                    candidate_answer_id=str(record["candidate_answer_id"]),
                    human_label=bool(record["human_label"]),
                    result=_success_result(record),
                )
            with self.assertRaises(JudgeQualificationArtifactError):
                store.finalize()

            resumed = JudgeQualificationArtifactStore.resume(
                run_dir=store.run_dir,
                freeze=harness.freeze,
            )
            self.assertEqual(resumed.completed_item_ids, tuple(item["item_id"] for item in harness.items[:3]))
            self.assertEqual(resumed.pending_item_ids, tuple(item["item_id"] for item in harness.items[3:]))
            for record in harness.items[3:]:
                resumed.write_item_result(
                    item=_evaluation_item(record),
                    candidate_answer_id=str(record["candidate_answer_id"]),
                    human_label=bool(record["human_label"]),
                    result=_success_result(record),
                )
            summary = resumed.finalize()
            self.assertEqual(summary["qualification_status"], "PASS")
            verification = verify_judge_qualification_artifacts(store.run_dir, harness.freeze)
            self.assertEqual(verification["attempt_status"], "complete")
            self.assertEqual(verification["completed_item_count"], 14)
            self.assertEqual(verification["duplicate_item_count"], 0)


class JudgeQualificationRunnerTests(IsolatedAsyncioTestCase):
    async def test_fake_backend_runs_all_14_without_network_and_persists_pass(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            harness = QualificationHarness(Path(temporary))
            outcomes = ["YES" if item["human_label"] else "NO" for item in harness.items]
            backend = _SequenceBackend(outcomes)
            store = harness.create_store()
            with mock.patch.object(
                socket.socket,
                "connect",
                side_effect=AssertionError("real network forbidden in Judge qualification tests"),
            ):
                summary = await run_judge_qualification(
                    freeze=harness.freeze,
                    items=[_evaluation_item(item) for item in harness.items],
                    evaluator=LongMemEvalAdapter(backend),
                    store=store,
                )

            self.assertEqual(len(backend.prompts), 14)
            self.assertEqual(summary["qualification_status"], "PASS")
            self.assertEqual(summary["scientific_surface"], JUDGE_QUALIFICATION_ONLY)
            self.assertEqual(
                verify_judge_qualification_artifacts(store.run_dir, harness.freeze)["attempt_status"],
                "complete",
            )

    async def test_service_error_is_durable_incomplete_and_stops_without_following_calls(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            harness = QualificationHarness(Path(temporary))
            backend = _SequenceBackend(["YES", "NO", ConnectionError("PRIVATE-SECRET"), "YES"])
            store = harness.create_store()
            result = await run_judge_qualification(
                freeze=harness.freeze,
                items=[_evaluation_item(item) for item in harness.items],
                evaluator=LongMemEvalAdapter(backend),
                store=store,
            )

            self.assertEqual(result["attempt_status"], "incomplete_invalid_non_mergeable")
            self.assertEqual(result["failure_class"], "service_error")
            self.assertEqual(result["failed_item_id"], harness.items[2]["item_id"])
            self.assertEqual(len(backend.prompts), 3)
            self.assertEqual(len(backend.outcomes), 1)
            verification = verify_judge_qualification_artifacts(store.run_dir, harness.freeze)
            self.assertEqual(verification["attempt_status"], "incomplete_invalid_non_mergeable")
            self.assertEqual(verification["failure_class"], "service_error")
            self.assertEqual(verification["completed_item_count"], 3)
            self.assertEqual(verification["service_error_count"], 1)
            self.assertFalse(store.summary_path.exists())
            persisted = b"".join(path.read_bytes() for path in sorted(store.run_dir.rglob("*")) if path.is_file())
            self.assertNotIn(b"PRIVATE-SECRET", persisted)


if __name__ == "__main__":
    import unittest

    unittest.main()
