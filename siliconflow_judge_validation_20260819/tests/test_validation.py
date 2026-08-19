import hashlib
import json
import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

import run_validation as rv


HISTORY_IDS = rv.EXPECTED_HISTORY_IDS


def write_bundle(
    root: Path,
    method: str,
    history_id: str,
    *,
    question: str | None = None,
    reference: str | None = None,
    prediction: str | None = None,
    original_label: bool = True,
) -> None:
    artifact = {
        "question": question or f"question-{history_id}",
        "question_type": "knowledge-update",
        "reference_answer": reference or f"reference-{history_id}",
        "predicted_answer": prediction or f"prediction-{method}-{history_id}",
        "judge_result": {
            "status": "SUCCESS",
            "label": original_label,
        },
    }
    path = root / "units" / method / history_id / "attempt-001" / "private_bundle.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"private_artifact": artifact}), encoding="utf-8")


class StrictParsingTests(unittest.TestCase):
    def test_accepts_only_bare_yes_or_no_with_optional_period(self) -> None:
        accepted = {
            "yes": ("YES", True),
            " YES.\n": ("YES", True),
            "no": ("NO", False),
            "No.": ("NO", False),
        }
        for raw, expected in accepted.items():
            with self.subTest(raw=raw):
                self.assertEqual(rv.parse_judge_output(raw), expected)

        for raw in ("yes because", "answer: no", "<think>x</think>yes", "", "maybe"):
            with self.subTest(raw=raw):
                self.assertEqual(rv.parse_judge_output(raw), ("INVALID", None))


class FrozenInputTests(unittest.TestCase):
    def test_loads_exactly_paired_successful_bundles(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            for method in ("u0", "pc2"):
                for history_id in HISTORY_IDS:
                    write_bundle(root, method, history_id)

            records = rv.load_frozen_records(root, expected_history_ids=set(HISTORY_IDS))

        self.assertEqual(len(records), 8)
        self.assertEqual({record.method for record in records}, {"U0", "P(C=2)"})
        self.assertEqual({record.history_id for record in records}, set(HISTORY_IDS))

    def test_rejects_question_mismatch_within_pair(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            for method in ("u0", "pc2"):
                for history_id in HISTORY_IDS:
                    write_bundle(root, method, history_id)
            write_bundle(root, "pc2", HISTORY_IDS[0], question="mismatched-question")

            with self.assertRaisesRegex(rv.InputContractError, "paired question mismatch"):
                rv.load_frozen_records(root, expected_history_ids=set(HISTORY_IDS))

    def test_rejects_missing_history(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            for method in ("u0", "pc2"):
                for history_id in HISTORY_IDS:
                    if method == "pc2" and history_id == HISTORY_IDS[-1]:
                        continue
                    write_bundle(root, method, history_id)

            with self.assertRaisesRegex(rv.InputContractError, "history set"):
                rv.load_frozen_records(root, expected_history_ids=set(HISTORY_IDS))

    def test_prompt_is_loaded_from_pinned_vendor(self) -> None:
        repo_root = Path(__file__).resolve().parents[2]
        prompt = rv.build_official_prompt(
            repo_root,
            task="knowledge-update",
            question="Where?",
            reference="There.",
            prediction="Here.",
        )
        self.assertIn("some previous information along with an updated answer", prompt)
        self.assertTrue(prompt.endswith("Answer yes or no only."))

    def test_all_real_frozen_prompts_match_original_prompt_hashes(self) -> None:
        repo_root = Path(__file__).resolve().parents[2]
        source = (
            repo_root
            / "paper-eval-v3/artifacts/paper_eval/quality_evaluation_v1/runs"
            / "qev1-dev-20260817-001"
        )
        records = rv.load_frozen_records(source)
        for record in records:
            with self.subTest(method=record.method, history_id=record.history_id):
                prompt = rv.build_official_prompt(
                    repo_root,
                    task=record.task,
                    question=record.question,
                    reference=record.reference,
                    prediction=record.prediction,
                )
                source_bundle = json.loads(Path(record.source_path).read_text())
                frozen_hash = source_bundle["private_artifact"]["judge_result"][
                    "prompt_sha256"
                ]
                self.assertEqual(hashlib.sha256(prompt.encode()).hexdigest(), frozen_hash)


class ModelSelectionTests(unittest.TestCase):
    def test_explicit_model_must_be_available(self) -> None:
        models = ["Qwen/Qwen3-8B", "deepseek-ai/DeepSeek-V3"]
        self.assertEqual(rv.select_model(models, "Qwen/Qwen3-8B"), "Qwen/Qwen3-8B")
        with self.assertRaisesRegex(rv.ModelSelectionError, "not present"):
            rv.select_model(models, "missing/model")

    def test_automatic_selection_is_deterministic(self) -> None:
        models = ["misc/model", "Qwen/Qwen3-8B", "Qwen/Qwen3-32B"]
        self.assertEqual(rv.select_model(models, None), "Qwen/Qwen3-32B")


class AggregationTests(unittest.TestCase):
    def test_invalid_is_excluded_from_accuracy_denominator(self) -> None:
        items = [
            {"method": "U0", "parse_status": "YES", "label": True, "original_label": True},
            {"method": "U0", "parse_status": "NO", "label": False, "original_label": False},
            {"method": "U0", "parse_status": "INVALID", "label": None, "original_label": True},
            {"method": "P(C=2)", "parse_status": "YES", "label": True, "original_label": True},
        ]
        summary = rv.aggregate(items)

        self.assertEqual(summary["U0"]["valid_count"], 2)
        self.assertEqual(summary["U0"]["invalid_count"], 1)
        self.assertEqual(summary["U0"]["correct_count"], 1)
        self.assertEqual(summary["U0"]["accuracy"], 0.5)
        self.assertEqual(summary["U0"]["agreement_with_original_count"], 2)
        self.assertEqual(summary["U0"]["agreement_with_original_rate"], 1.0)


class PrivacyAndPayloadTests(unittest.TestCase):
    def test_result_item_contains_hash_not_raw_output(self) -> None:
        raw = "yes"
        item = rv.make_result_item(
            method="U0",
            history_id="h",
            model="m",
            prompt="p",
            raw_output=raw,
            original_label=True,
            usage={"prompt_tokens": 10, "completion_tokens": 1, "total_tokens": 11},
            finish_reason="stop",
        )
        self.assertNotIn("raw_output", item)
        self.assertEqual(item["response_sha256"], hashlib.sha256(raw.encode()).hexdigest())
        self.assertEqual(item["label"], True)

    def test_chat_payload_is_deterministic_and_non_thinking(self) -> None:
        payload = rv.build_chat_payload("Qwen/Qwen3-32B", "judge prompt")
        self.assertEqual(payload["temperature"], 0)
        self.assertEqual(payload["max_tokens"], 16)
        self.assertEqual(payload["messages"], [{"role": "user", "content": "judge prompt"}])
        self.assertEqual(payload["enable_thinking"], False)


class EndToEndArtifactTests(unittest.TestCase):
    def test_mocked_success_path_writes_two_half_accuracy_summaries_without_secret(self) -> None:
        class FakeClient:
            def __init__(self, api_key: str, **_: object) -> None:
                self.outputs = iter(("yes", "yes.", "no", "NO.") * 2)

            def list_models(self) -> list[str]:
                return ["Qwen/Qwen3-32B"]

            def judge(self, model: str, prompt: str):
                return next(self.outputs), {"total_tokens": 11}, "stop"

        with tempfile.TemporaryDirectory() as tmp:
            temp_root = Path(tmp)
            source = temp_root / "source"
            output = temp_root / "output"
            for method in ("u0", "pc2"):
                for history_id in HISTORY_IDS:
                    write_bundle(
                        source,
                        method,
                        history_id,
                        original_label=history_id in HISTORY_IDS[:2],
                    )
            args = SimpleNamespace(
                source_run=source,
                output_dir=output,
                base_url=rv.DEFAULT_BASE_URL,
                model=None,
                timeout=1.0,
                proxy_mode="direct",
                dry_run=False,
            )
            fake_secret = "unit-test-secret-must-not-be-serialized"
            with mock.patch.object(rv, "SiliconFlowClient", FakeClient), mock.patch.dict(
                os.environ, {"SILICONFLOW_API_KEY": fake_secret}
            ):
                exit_code = rv.run(args)

            result = json.loads((output / "RESULTS.json").read_text(encoding="utf-8"))
            serialized = "\n".join(path.read_text() for path in output.iterdir())

        self.assertEqual(exit_code, 0)
        self.assertEqual(result["judge_request_count"], 8)
        self.assertEqual(result["summary"]["U0"]["accuracy"], 0.5)
        self.assertEqual(result["summary"]["P(C=2)"]["accuracy"], 0.5)
        self.assertNotIn("raw_output", serialized)
        self.assertNotIn(fake_secret, serialized)


if __name__ == "__main__":
    unittest.main()
