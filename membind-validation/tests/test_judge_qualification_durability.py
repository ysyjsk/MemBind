"""Q0 RED contracts for Judge qualification crash consistency and isolation."""

from __future__ import annotations

import asyncio
import json
import os
import sys
import tempfile
from dataclasses import replace
from pathlib import Path
from unittest import IsolatedAsyncioTestCase, TestCase, mock


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from evaluation.judge_qualification import (  # noqa: E402
    JudgeQualificationArtifactError,
    JudgeQualificationArtifactStore,
    canonical_json_bytes,
    run_judge_qualification,
    verify_judge_qualification_artifacts,
)
from evaluation.schemas import EvaluationResult  # noqa: E402
from tests.test_judge_qualification import (  # noqa: E402
    QualificationHarness,
    _SequenceBackend,
    _evaluation_item,
    _invalid_result,
    _official_prompt_hash,
    _service_result,
    _sha256,
    _success_result,
)


CANONICAL_INCOMPLETE = "incomplete_invalid_non_mergeable"


def _read_events(path: Path) -> list[dict[str, object]]:
    return [json.loads(line) for line in path.read_text(encoding="ascii").splitlines()]


def _payload_hash(value: dict[str, object]) -> str:
    candidate = dict(value)
    candidate.pop("payload_sha256", None)
    return _sha256(canonical_json_bytes(candidate))


def _mutate_json(path: Path, field: str, value: object) -> None:
    payload = json.loads(path.read_text(encoding="ascii"))
    payload[field] = value
    path.write_bytes(canonical_json_bytes(payload) + b"\n")


class _IntentCheckingEvaluator:
    def __init__(self, store: JudgeQualificationArtifactStore, records: list[dict[str, object]], fsync_spy: mock.Mock) -> None:
        self.store = store
        self.records = {str(record["item_id"]): record for record in records}
        self.fsync_spy = fsync_spy
        self.calls: list[str] = []
        self.previous_fsync_count = fsync_spy.call_count

    async def evaluate(self, item: object) -> EvaluationResult:
        item_id = str(getattr(item, "item_id"))
        events = _read_events(self.store.events_path)
        self.assert_dispatch_is_last(events, item_id)
        if self.fsync_spy.call_count <= self.previous_fsync_count:
            raise AssertionError("dispatch intent was not fsynced before evaluator entry")
        self.previous_fsync_count = self.fsync_spy.call_count
        self.calls.append(item_id)
        return _success_result(self.records[item_id])

    @staticmethod
    def assert_dispatch_is_last(events: list[dict[str, object]], item_id: str) -> None:
        if not events:
            raise AssertionError("evaluator entered before a durable dispatch event")
        event = events[-1]
        if event.get("event_type") != "dispatch_intent_durable" or event.get("item_id") != item_id:
            raise AssertionError("last durable event is not this item's dispatch intent")


class JudgeQualificationDurabilityTests(IsolatedAsyncioTestCase):
    async def _complete_run(self, harness: QualificationHarness) -> JudgeQualificationArtifactStore:
        store = harness.create_store()
        backend = _SequenceBackend(
            ["YES" if record["human_label"] else "NO" for record in harness.items]
        )
        from evaluation.benchmarks.longmemeval import LongMemEvalAdapter

        summary = await run_judge_qualification(
            freeze=harness.freeze,
            items=[_evaluation_item(record) for record in harness.items],
            evaluator=LongMemEvalAdapter(backend),
            store=store,
        )
        self.assertEqual(summary["qualification_status"], "PASS")
        return store

    async def test_dispatch_intent_precedes_evaluator_and_events_form_one_hash_chain(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            harness = QualificationHarness(Path(temporary))
            store = harness.create_store()
            with mock.patch(
                "evaluation.judge_qualification.os.fsync", wraps=os.fsync
            ) as fsync_spy:
                evaluator = _IntentCheckingEvaluator(store, harness.items, fsync_spy)
                summary = await run_judge_qualification(
                    freeze=harness.freeze,
                    items=[_evaluation_item(record) for record in harness.items],
                    evaluator=evaluator,
                    store=store,
                )

            self.assertEqual(summary["qualification_status"], "PASS")
            self.assertEqual(evaluator.calls, [str(record["item_id"]) for record in harness.items])
            events = _read_events(store.events_path)
            self.assertEqual(len(events), 28)
            self.assertEqual([event["event_sequence"] for event in events], list(range(28)))
            self.assertEqual(
                [event["event_type"] for event in events],
                [value for _ in harness.items for value in ("dispatch_intent_durable", "terminal_success")],
            )
            previous_hash: str | None = None
            for event in events:
                self.assertEqual(event["previous_event_sha256"], previous_hash)
                self.assertEqual(event["payload_sha256"], _payload_hash(event))
                previous_hash = str(event["payload_sha256"])

    async def test_terminal_checkpoint_requires_matching_durable_intent(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            harness = QualificationHarness(Path(temporary))
            store = harness.create_store()
            record = harness.items[0]
            with self.assertRaises(JudgeQualificationArtifactError):
                store.write_terminal_result(
                    item=_evaluation_item(record),
                    candidate_answer_id=str(record["candidate_answer_id"]),
                    human_label=bool(record["human_label"]),
                    result=_success_result(record),
                    dispatch_intent_payload_sha256="0" * 64,
                )
            self.assertEqual(_read_events(store.events_path), [])

    async def test_same_run_concurrent_executor_is_rejected_by_nonblocking_lock(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            harness = QualificationHarness(Path(temporary))
            primary_store = harness.create_store()
            contender_store = JudgeQualificationArtifactStore.resume(
                run_dir=primary_store.run_dir,
                freeze=harness.freeze,
            )
            records = {str(record["item_id"]): record for record in harness.items}
            entered = asyncio.Event()
            release = asyncio.Event()

            class BlockingEvaluator:
                def __init__(self) -> None:
                    self.calls: list[str] = []

                async def evaluate(self, item: object) -> EvaluationResult:
                    item_id = str(getattr(item, "item_id"))
                    self.calls.append(item_id)
                    if len(self.calls) == 1:
                        entered.set()
                        await release.wait()
                    return _success_result(records[item_id])

            class ContenderEvaluator:
                def __init__(self) -> None:
                    self.calls: list[str] = []

                async def evaluate(self, item: object) -> EvaluationResult:
                    self.calls.append(str(getattr(item, "item_id")))
                    raise AssertionError("concurrent executor reached the evaluator")

            primary_evaluator = BlockingEvaluator()
            contender_evaluator = ContenderEvaluator()
            items = [_evaluation_item(record) for record in harness.items]
            primary_task = asyncio.create_task(
                run_judge_qualification(
                    freeze=harness.freeze,
                    items=items,
                    evaluator=primary_evaluator,
                    store=primary_store,
                )
            )
            await asyncio.wait_for(entered.wait(), timeout=1.0)
            started = asyncio.get_running_loop().time()
            contender_error: BaseException | None = None
            try:
                await asyncio.wait_for(
                    run_judge_qualification(
                        freeze=harness.freeze,
                        items=items,
                        evaluator=contender_evaluator,
                        store=contender_store,
                    ),
                    timeout=0.25,
                )
            except BaseException as error:
                contender_error = error
            elapsed = asyncio.get_running_loop().time() - started
            release.set()
            primary_summary = await asyncio.wait_for(primary_task, timeout=5.0)

            self.assertIsInstance(contender_error, JudgeQualificationArtifactError)
            self.assertRegex(str(contender_error), r"(?i)lock|execut")
            self.assertLess(elapsed, 0.25, "concurrent acquisition blocked instead of failing fast")
            self.assertEqual(contender_evaluator.calls, [])
            self.assertEqual(len(primary_evaluator.calls), 14)
            self.assertEqual(primary_summary["qualification_status"], "PASS")

    async def test_all_14_item_fields_are_validated_before_first_evaluator_call(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            harness = QualificationHarness(Path(temporary))
            store = harness.create_store()
            items = [_evaluation_item(record) for record in harness.items]
            items[-1] = replace(items[-1], hypothesis="late-item-drift")
            backend = _SequenceBackend(
                ["YES" if record["human_label"] else "NO" for record in harness.items]
            )
            from evaluation.benchmarks.longmemeval import LongMemEvalAdapter

            with self.assertRaisesRegex(
                JudgeQualificationArtifactError,
                "item|frozen|freeze|field",
            ):
                await run_judge_qualification(
                    freeze=harness.freeze,
                    items=items,
                    evaluator=LongMemEvalAdapter(backend),
                    store=store,
                )

            self.assertEqual(backend.prompts, [], "late item drift was detected after dispatch began")
            self.assertEqual(_read_events(store.events_path), [])

    async def test_symlinked_critical_artifacts_fail_closed_without_touching_targets(self) -> None:
        critical_artifacts = (
            "manifest.json",
            "runtime_identity.json",
            "events.jsonl",
            "checkpoint.json",
        )
        for name in critical_artifacts:
            with self.subTest(artifact=name), tempfile.TemporaryDirectory() as temporary:
                harness = QualificationHarness(Path(temporary))
                store = harness.create_store()
                artifact = store.run_dir / name
                target = harness.root / f"outside-{name}"
                target.write_bytes(artifact.read_bytes())
                target_before = target.read_bytes()
                artifact.unlink()
                artifact.symlink_to(target)

                verification = verify_judge_qualification_artifacts(
                    store.run_dir,
                    harness.freeze,
                )
                resume_error: BaseException | None = None
                try:
                    JudgeQualificationArtifactStore.resume(
                        run_dir=store.run_dir,
                        freeze=harness.freeze,
                    )
                except BaseException as error:
                    resume_error = error

                self.assertEqual(target.read_bytes(), target_before)
                with self.subTest(artifact=name, operation="verify"):
                    self.assertEqual(verification["attempt_status"], CANONICAL_INCOMPLETE)
                    self.assertEqual(
                        verification["failure_class"],
                        "artifact_verification_error",
                    )
                with self.subTest(artifact=name, operation="resume"):
                    self.assertIsInstance(resume_error, JudgeQualificationArtifactError)

    async def test_false_audit_metadata_cannot_turn_an_opposite_success_label_into_pass(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            harness = QualificationHarness(Path(temporary))
            store = harness.create_store()
            record = harness.items[0]
            self.assertIs(record["human_label"], True)
            fabricated = replace(
                _success_result(record, predicted=False),
                raw_output="NO",
                normalized_output="NO",
                parse_status="NO",
                metadata={"audit_label": True},
            )

            with self.assertRaisesRegex(
                JudgeQualificationArtifactError,
                "label|audit|parser|terminal result",
            ):
                store.write_item_result(
                    item=_evaluation_item(record),
                    candidate_answer_id=str(record["candidate_answer_id"]),
                    human_label=bool(record["human_label"]),
                    result=fabricated,
                )

            self.assertFalse(
                (store.run_dir / "items/000/checkpoint.json").exists(),
                "an internally contradictory SUCCESS result became durable evidence",
            )

    async def test_terminal_failure_evidence_blocks_resume_even_if_root_is_still_in_progress(self) -> None:
        terminal_results = (
            ("invalid", _invalid_result),
            ("service", _service_result),
        )
        for name, result_factory in terminal_results:
            with self.subTest(terminal=name), tempfile.TemporaryDirectory() as temporary:
                harness = QualificationHarness(Path(temporary))
                store = harness.create_store()
                record = harness.items[0]
                store.write_item_result(
                    item=_evaluation_item(record),
                    candidate_answer_id=str(record["candidate_answer_id"]),
                    human_label=bool(record["human_label"]),
                    result=result_factory(record),
                )
                root_checkpoint = json.loads(store.checkpoint_path.read_text(encoding="ascii"))
                self.assertEqual(root_checkpoint["status"], "in_progress")

                with self.assertRaisesRegex(
                    JudgeQualificationArtifactError,
                    "non-mergeable|terminal|invalid|service",
                ):
                    JudgeQualificationArtifactStore.resume(
                        run_dir=store.run_dir,
                        freeze=harness.freeze,
                    )

    async def test_complete_success_evidence_with_one_disagreement_persists_nonmergeable_fail(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            harness = QualificationHarness(Path(temporary))
            outcomes = ["YES" if record["human_label"] else "NO" for record in harness.items]
            outcomes[0] = "NO"
            backend = _SequenceBackend(outcomes)
            store = harness.create_store()
            from evaluation.benchmarks.longmemeval import LongMemEvalAdapter

            summary = await run_judge_qualification(
                freeze=harness.freeze,
                items=[_evaluation_item(record) for record in harness.items],
                evaluator=LongMemEvalAdapter(backend),
                store=store,
            )

            self.assertEqual(len(backend.prompts), 14)
            self.assertEqual(summary["qualification_status"], "FAIL")
            self.assertEqual(summary["attempt_status"], "complete")
            self.assertIs(summary["mergeable"], False)
            self.assertEqual(summary["terminal_item_count"], 14)
            self.assertEqual(summary["eligible_item_count"], 14)
            self.assertEqual(summary["agreement_count"], 13)
            self.assertTrue(store.summary_path.is_file())
            persisted = json.loads(store.summary_path.read_text(encoding="ascii"))
            self.assertEqual(persisted, summary)
            self.assertEqual(persisted["payload_sha256"], _payload_hash(persisted))
            verification = verify_judge_qualification_artifacts(store.run_dir, harness.freeze)
            self.assertEqual(verification["attempt_status"], "complete")
            self.assertEqual(verification["qualification_status"], "FAIL")
            self.assertIs(verification["mergeable"], False)

    async def test_ambiguous_inflight_resume_never_dispatches_and_is_nonmergeable(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            harness = QualificationHarness(Path(temporary))
            store = harness.create_store()
            record = harness.items[0]
            store.write_dispatch_intent(
                item=_evaluation_item(record),
                candidate_answer_id=str(record["candidate_answer_id"]),
                human_label=bool(record["human_label"]),
                prompt_hash=_official_prompt_hash(record),
                config_hash="c" * 64,
            )
            backend = _SequenceBackend(["YES"])
            with self.assertRaises(JudgeQualificationArtifactError):
                resumed = JudgeQualificationArtifactStore.resume(
                    run_dir=store.run_dir,
                    freeze=harness.freeze,
                )
                from evaluation.benchmarks.longmemeval import LongMemEvalAdapter

                await run_judge_qualification(
                    freeze=harness.freeze,
                    items=[_evaluation_item(item) for item in harness.items],
                    evaluator=LongMemEvalAdapter(backend),
                    store=resumed,
                )

            self.assertEqual(backend.prompts, [])
            verification = verify_judge_qualification_artifacts(store.run_dir, harness.freeze)
            self.assertEqual(verification["attempt_status"], CANONICAL_INCOMPLETE)
            self.assertEqual(verification["failure_class"], "ambiguous_dispatch_intent")
            self.assertFalse(store.summary_path.exists())

    async def test_invalid_output_is_durable_and_stops_before_suffix(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            harness = QualificationHarness(Path(temporary))
            store = harness.create_store()
            backend = _SequenceBackend(["YES", "maybe", "NO"])
            from evaluation.benchmarks.longmemeval import LongMemEvalAdapter

            result = await run_judge_qualification(
                freeze=harness.freeze,
                items=[_evaluation_item(record) for record in harness.items],
                evaluator=LongMemEvalAdapter(backend),
                store=store,
            )

            self.assertEqual(len(backend.prompts), 2)
            self.assertEqual(result["attempt_status"], CANONICAL_INCOMPLETE)
            self.assertEqual(result["failure_class"], "invalid_output")
            verification = verify_judge_qualification_artifacts(store.run_dir, harness.freeze)
            self.assertEqual(verification["attempt_status"], CANONICAL_INCOMPLETE)
            self.assertEqual(verification["invalid_output_count"], 1)
            events = _read_events(store.events_path)
            self.assertEqual(events[-1]["event_type"], "terminal_invalid")
            self.assertEqual(events[-1]["item_id"], harness.items[1]["item_id"])
            self.assertFalse(store.summary_path.exists())

    async def test_event_item_checkpoint_and_root_checkpoint_tamper_fail_closed(self) -> None:
        mutations = (
            ("event", lambda store: store.events_path, "event_sequence", 99),
            (
                "item_checkpoint",
                lambda store: store.run_dir / "items/000/checkpoint.json",
                "human_label",
                False,
            ),
            ("root_checkpoint", lambda store: store.run_dir / "checkpoint.json", "status", "running"),
        )
        for name, select_path, field, value in mutations:
            with self.subTest(name=name), tempfile.TemporaryDirectory() as temporary:
                harness = QualificationHarness(Path(temporary))
                store = await self._complete_run(harness)
                path = select_path(store)
                if name == "event":
                    lines = path.read_text(encoding="ascii").splitlines()
                    first = json.loads(lines[0])
                    first[field] = value
                    lines[0] = canonical_json_bytes(first).decode("ascii")
                    path.write_text("\n".join(lines) + "\n", encoding="ascii")
                else:
                    _mutate_json(path, field, value)
                verification = verify_judge_qualification_artifacts(store.run_dir, harness.freeze)
                self.assertEqual(verification["attempt_status"], CANONICAL_INCOMPLETE)
                with self.assertRaises(JudgeQualificationArtifactError):
                    JudgeQualificationArtifactStore.resume(
                        run_dir=store.run_dir,
                        freeze=harness.freeze,
                    )

    async def test_runtime_identity_drift_stops_before_next_dispatch(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            harness = QualificationHarness(Path(temporary))
            store = harness.create_store()
            observed = 0

            def identity_reader() -> dict[str, object]:
                nonlocal observed
                observed += 1
                if observed == 1:
                    return dict(harness.runtime_identity)
                return dict(harness.runtime_identity) | {"model_fingerprint": "9" * 64}

            backend = _SequenceBackend(["YES", "NO"])
            from evaluation.benchmarks.longmemeval import LongMemEvalAdapter

            result = await run_judge_qualification(
                freeze=harness.freeze,
                items=[_evaluation_item(record) for record in harness.items],
                evaluator=LongMemEvalAdapter(backend),
                store=store,
                runtime_identity_reader=identity_reader,
            )

            self.assertEqual(len(backend.prompts), 1)
            self.assertEqual(result["attempt_status"], CANONICAL_INCOMPLETE)
            self.assertEqual(result["failure_class"], "runtime_identity_drift")
            self.assertEqual(result["failed_item_id"], harness.items[1]["item_id"])
            self.assertFalse(store.summary_path.exists())

    async def test_current_state_is_never_written_and_failure_tree_contains_no_secrets(self) -> None:
        secrets = (
            "PRIVATE-JUDGE-CREDENTIAL",
            "Bearer PRIVATE-AUTHORIZATION",
            "http://user:PRIVATE@judge.private.invalid/v1",
            "DOTENV_PRIVATE_TOKEN",
            "PRIVATE-RESPONSE-BODY",
        )
        with tempfile.TemporaryDirectory() as temporary:
            harness = QualificationHarness(Path(temporary))
            current_state = harness.root / "CURRENT_STATE.json"
            sentinel = b'{"sentinel":"byte-identical"}\n'
            current_state.write_bytes(sentinel)
            store = harness.create_store()
            backend = _SequenceBackend(
                ["YES", ConnectionError(" | ".join(secrets))]
            )
            from evaluation.benchmarks.longmemeval import LongMemEvalAdapter

            result = await run_judge_qualification(
                freeze=harness.freeze,
                items=[_evaluation_item(record) for record in harness.items],
                evaluator=LongMemEvalAdapter(backend),
                store=store,
            )

            self.assertEqual(result["attempt_status"], CANONICAL_INCOMPLETE)
            self.assertEqual(current_state.read_bytes(), sentinel)
            persisted = b"".join(
                path.read_bytes()
                for path in sorted(store.run_dir.rglob("*"))
                if path.is_file()
            ).decode("ascii")
            for secret in secrets:
                self.assertNotIn(secret, persisted)
            self.assertNotIn("authorization", persisted.casefold())
            self.assertNotIn("api_key", persisted.casefold())


class JudgeQualificationWriterIsolationTests(TestCase):
    def test_public_store_api_has_no_current_state_writer_surface(self) -> None:
        import inspect
        import evaluation.judge_qualification as qualification

        source = inspect.getsource(qualification)
        self.assertNotIn("CURRENT_STATE.json", source)
        self.assertNotIn("current_state_gate", source)
        for callable_object in (
            JudgeQualificationArtifactStore.create,
            JudgeQualificationArtifactStore.resume,
            run_judge_qualification,
        ):
            parameters = inspect.signature(callable_object).parameters
            self.assertFalse(
                {"state", "state_path", "current_state", "current_state_path"}
                & set(parameters)
            )


if __name__ == "__main__":
    import unittest

    unittest.main()
