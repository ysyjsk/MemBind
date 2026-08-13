"""Offline RED contracts for the production C5 live boundary adapter.

These tests deliberately mock every service boundary.  The adapter must prove
the frozen C5/Judge provenance and all-namespace empty preflight before it may
construct a live run or delegate to the already-tested C5 live core.
"""

from __future__ import annotations

import asyncio
import hashlib
import io
import json
import sys
import tempfile
from dataclasses import replace
from pathlib import Path
from unittest import IsolatedAsyncioTestCase, TestCase, mock


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

import current_state_gate as state_gate  # noqa: E402
import dataset  # noqa: E402
import native_characterization_c5 as c5  # noqa: E402
import native_characterization_c5_live as live  # noqa: E402
import native_characterization_c5_live_core as core  # noqa: E402


HISTORY_ID = "07741c45"
RUN_ID = "c5-0123456789abcdef"
RESUME_RUN_ID = "c5-fedcba9876543210"
SHA_A = "a" * 64
C4_RUN_ID = "c4-8e76fba0288047f9"
JUDGE_RUN_ID = "jq-b00a9689796c1e67"
C4_BOUNDED_USE = (
    "c4_summary_sufficient_for_c5_progression_without_reclassifying_"
    "c4_attempt_mergeable"
)
TCB_PATHS = {
    "c5_core_source": "src/native_characterization_c5.py",
    "c5_authorization_source": "src/native_characterization_c5_authorization.py",
    "c5_live_source": "src/native_characterization_c5_live.py",
    "c5_live_artifacts_source": "src/native_characterization_c5_live_artifacts.py",
    "c5_live_core_source": "src/native_characterization_c5_live_core.py",
    "c5_qa_source": "src/native_characterization_c5_qa.py",
    "c5_core_tests": "tests/test_native_characterization_c5.py",
    "c5_authorization_tests": "tests/test_native_characterization_c5_authorization.py",
    "c5_live_tests": "tests/test_native_characterization_c5_live.py",
    "c5_live_artifacts_tests": "tests/test_native_characterization_c5_live_artifacts.py",
    "c5_live_core_tests": "tests/test_native_characterization_c5_live_core.py",
    "c5_live_integration_tests": "tests/test_native_characterization_c5_live_integration.py",
    "c5_qa_tests": "tests/test_native_characterization_c5_qa.py",
}
GREEN_RECEIPT_PATHS = {
    "c5_focused_regression": "artifacts/tdd/native_characterization_c5_focused_green_20260813.log",
    "c5_stale_state_regression": (
        "artifacts/tdd/native_characterization_c5_stale_state_fixture_"
        "focused_green_20260813.log"
    ),
    "c5_full_offline_regression": (
        "artifacts/tdd/native_characterization_c5_full_offline_regression_"
        "20260813.log"
    ),
}
NAMESPACES = (
    "nc-e4-1434fcb947df5c3d",
    "nc-e4-b352061ffa0d4b21",
    "nc-e4-c15538d1fe2801cb",
    "nc-e4-2a427029b1a8b2ac",
)


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(value, ensure_ascii=True, sort_keys=True, separators=(",", ":"))
        + "\n",
        encoding="ascii",
    )


def _write_events(path: Path, values: list[dict[str, object]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "".join(
            json.dumps(value, ensure_ascii=True, sort_keys=True, separators=(",", ":"))
            + "\n"
            for value in values
        ),
        encoding="ascii",
    )


def _seal(value: dict[str, object]) -> dict[str, object]:
    return c5.seal_payload(value)


def _raw_record() -> dict[str, object]:
    return {
        "question_id": HISTORY_ID,
        "group_id": HISTORY_ID,
        "question_type": "knowledge-update",
        "question": "Where does the subject work now?",
        "answer": "At the current employer.",
        "answer_session_ids": ["session-48"],
        "haystack_session_ids": [f"session-{index}" for index in range(49)],
        "haystack_dates": [
            f"2026-01-{index % 28 + 1:02d}T00:00:00Z" for index in range(49)
        ],
        "haystack_sessions": [
            [{"role": "user", "content": f"episode body {index}"}]
            for index in range(49)
        ],
    }


class FlushStream(io.StringIO):
    def __init__(self) -> None:
        super().__init__()
        self.flush_count = 0

    def flush(self) -> None:
        self.flush_count += 1
        super().flush()


class ClosableQA:
    def __init__(self) -> None:
        self.closed = False

    async def __call__(
        self, _runtime: object, _block: core.C5Block
    ) -> dict[str, object]:
        return {"status": "SUCCESS", "correct": True}

    async def aclose(self) -> None:
        self.closed = True


class Fixture:
    def __init__(self, owner: IsolatedAsyncioTestCase) -> None:
        temporary = tempfile.TemporaryDirectory()
        owner.addCleanup(temporary.cleanup)
        self.repo = Path(temporary.name)
        self.validation = self.repo / "membind-validation"
        self.validation.mkdir()
        self.state_path = self.validation / "CURRENT_STATE.json"
        self.runs_root = self.validation / "artifacts/native_characterization/runs"
        self.freeze_path = (
            self.validation
            / "artifacts/native_characterization/freeze_reference_aligned_64k.json"
        )
        self.c4_path = self.runs_root / "c4-8e76fba0288047f9/e3_sync_async.json"
        self.c4_checkpoint_path = self.c4_path.parent / "checkpoint.json"
        self.c4_events_path = self.c4_path.parent / "events.jsonl"
        self.judge_summary_path = (
            self.validation
            / "artifacts/judge_qualification/runs/jq-b00a9689796c1e67/qualification_summary.json"
        )
        self.judge_runtime_path = self.judge_summary_path.parent / "runtime_identity.json"
        self.judge_manifest_path = self.judge_summary_path.parent / "manifest.json"
        self.judge_freeze_path = self.judge_summary_path.parent / "fixture_freeze.json"
        self.judge_checkpoint_path = self.judge_summary_path.parent / "checkpoint.json"
        self.judge_events_path = self.judge_summary_path.parent / "events.jsonl"
        self.raw_path = self.repo / "raw/longmemeval_s_cleaned.json"
        _write_json(self.raw_path, [_raw_record()])
        self.episodes = dataset.build_episodes(_raw_record())

        for name, relative in TCB_PATHS.items():
            target = self.validation / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(f"frozen {name}\n", encoding="ascii")
        for name, relative in GREEN_RECEIPT_PATHS.items():
            target = self.validation / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(f"Ran {80 if name != 'c5_full_offline_regression' else 814} tests in 1.0s\n\nOK\n", encoding="ascii")

        frozen_episodes = [
            {
                "source_sequence": episode.source_sequence,
                "episode_source_sha256": episode.source_hash,
                "prefix_sha256": hashlib.sha256(
                    f"prefix-{episode.source_sequence}".encode("ascii")
                ).hexdigest(),
            }
            for episode in self.episodes
        ]
        self.freeze: dict[str, object] = _seal(
            {
                "schema_version": "membind.native-characterization-freeze.v1",
                "artifact_id": "native-characterization-freeze-reference-aligned-64k",
                "protocol": {
                    "id": "native-characterization-v1.1",
                    "freeze_marker": True,
                    "workplan_path": "MemBind_NATIVE_GRAPHITI_CHARACTERIZATION_WORKPLAN_v1.1.md",
                    "workplan_sha256": SHA_A,
                },
                "runtime_identities": {
                    "construction": {
                        "vllm_version": "0.26.0",
                        "served_model_id": "qwen3-32b-fp8",
                        "max_model_len": 65536,
                        "rope_type": "yarn",
                        "yarn_factor": 2.0,
                        "original_max_position_embeddings": 32768,
                        "rope_theta": 1000000,
                        "enable_thinking": False,
                    },
                    "embedding": {
                        "served_model_id": "qwen3-embedding-0.6b",
                        "dimension": 1024,
                    },
                },
                "dataset": {
                    "source_sha256": _sha(self.raw_path),
                    "calibration_histories": [
                        {
                            "history_id": HISTORY_ID,
                            "episode_count": 49,
                            "episodes": frozen_episodes,
                        }
                    ],
                },
                "screening": {
                    "e4": {
                        "history_id": HISTORY_ID,
                        "concurrency_order": [1, 2, 4, 8],
                        "block_order": [
                            {
                                "block_index": index,
                                "concurrency": concurrency,
                                "graph_namespace": namespace,
                            }
                            for index, (concurrency, namespace) in enumerate(
                                zip((1, 2, 4, 8), NAMESPACES)
                            )
                        ],
                    }
                },
                "state_transition": {
                    "execution_envelope_updated": True,
                    "live_authorized": False,
                },
            }
        )
        _write_json(self.freeze_path, self.freeze)

        self.c4: dict[str, object] = _seal(
            {
                "schema_version": "membind.native-characterization-e3-sync-async.v1",
                "status": "complete",
                "run_id": "c4-8e76fba0288047f9",
                "block_count": 10,
                "episode_count": 490,
                "mergeable": False,
            }
        )
        _write_json(self.c4_path, self.c4)
        self.c4_checkpoint: dict[str, object] = _seal(
            {
                "schema_version": "membind.native-characterization-c4-checkpoint.v1",
                "run_id": C4_RUN_ID,
                "stage": "C4/E3",
                "checkpoint_level": "root",
                "status": "incomplete_invalid_non_mergeable",
                "progress": {
                    "completed_block_indices": list(range(10)),
                    "completed_episode_count": 490,
                    "failure_stage": "verification",
                },
                "failure": {
                    "error_class": "builtins.TypeError",
                    "token_envelope": {
                        "output_tokens": None,
                        "prompt_tokens": None,
                        "requested_max_tokens": None,
                    },
                },
            }
        )
        _write_json(self.c4_checkpoint_path, self.c4_checkpoint)
        self.c4_events = []
        for sequence in range(735):
            self.c4_events.append(
                _seal(
                    {
                        "schema_version": "membind.native-characterization-c4-event.v1",
                        "run_id": C4_RUN_ID,
                        "event_sequence": sequence,
                        "event_type": (
                            "enqueue" if sequence < 245 else "publication"
                        ),
                    }
                )
            )
        self.c4_events.append(
            _seal(
                {
                    "schema_version": "membind.native-characterization-c4-event.v1",
                    "run_id": C4_RUN_ID,
                    "event_sequence": 735,
                    "event_type": "failure",
                    "block_index": None,
                    "source_sequence": None,
                    "failure_scope": "stage",
                    "failure_stage": "verification",
                    "status": "incomplete_invalid_non_mergeable",
                    "error_class": "builtins.TypeError",
                    "completed_block_count": 10,
                    "completed_episode_count": 490,
                }
            )
        )
        _write_events(self.c4_events_path, self.c4_events)

        self.judge_runtime: dict[str, object] = _seal(
            {
                "schema_version": "membind.judge-runtime-identity.v1",
                "run_id": "jq-b00a9689796c1e67",
                "identity": {
                    "served_model_name": "qwen3-32b-fp8",
                    "vllm_version": "0.26.0",
                    "max_model_len": 65536,
                    "effective_enable_thinking": False,
                    "backend_public_config": {
                        "backend": "openai_compatible_chat_completions",
                        "served_model_name": "qwen3-32b-fp8",
                        "temperature": 0,
                        "max_tokens": 10,
                        "n": 1,
                        "max_attempts": 1,
                        "sdk_hidden_retries": 0,
                        "effective_enable_thinking": False,
                    },
                },
            }
        )
        _write_json(self.judge_runtime_path, self.judge_runtime)
        self.judge_freeze: dict[str, object] = _seal(
            {
                "schema_version": "membind.judge-qualification-freeze.v1",
                "protocol_id": "judge-qualification-v1.0",
                "scientific_surface": "JUDGE_QUALIFICATION_ONLY",
                "items": [{"item_index": index} for index in range(14)],
                "strict_pass_gate": {"planned_item_count": 14},
            }
        )
        _write_json(self.judge_freeze_path, self.judge_freeze)
        self.judge_manifest: dict[str, object] = _seal(
            {
                "schema_version": "membind.judge-qualification-run.v1",
                "protocol_id": "judge-qualification-v1.0",
                "scientific_surface": "JUDGE_QUALIFICATION_ONLY",
                "run_id": JUDGE_RUN_ID,
                "freeze_file_sha256": _sha(self.judge_freeze_path),
                "freeze_payload_sha256": self.judge_freeze["payload_sha256"],
                "runtime_identity_file_sha256": _sha(self.judge_runtime_path),
                "runtime_identity_payload_sha256": self.judge_runtime[
                    "payload_sha256"
                ],
            }
        )
        _write_json(self.judge_manifest_path, self.judge_manifest)
        previous: str | None = None
        self.judge_events: list[dict[str, object]] = []
        for sequence in range(28):
            event = _seal(
                {
                    "schema_version": "membind.judge-qualification-event.v1",
                    "run_id": JUDGE_RUN_ID,
                    "event_sequence": sequence,
                    "previous_event_sha256": previous,
                    "event_type": (
                        "dispatch_intent_durable"
                        if sequence % 2 == 0
                        else "terminal_success"
                    ),
                    "item_index": sequence // 2,
                }
            )
            self.judge_events.append(event)
            previous = str(event["payload_sha256"])
        _write_events(self.judge_events_path, self.judge_events)
        self.judge_checkpoint: dict[str, object] = _seal(
            {
                "schema_version": "membind.judge-qualification-checkpoint.v1",
                "run_id": JUDGE_RUN_ID,
                "status": "complete",
                "phase": "finalized",
                "failure_class": None,
                "failed_item_id": None,
                "terminal_item_count": 14,
                "next_item_index": 14,
                "event_count": 28,
                "last_event_payload_sha256": self.judge_events[-1][
                    "payload_sha256"
                ],
                "freeze_payload_sha256": self.judge_freeze["payload_sha256"],
                "runtime_identity_payload_sha256": self.judge_runtime[
                    "payload_sha256"
                ],
            }
        )
        _write_json(self.judge_checkpoint_path, self.judge_checkpoint)
        self.judge_summary: dict[str, object] = _seal(
            {
                "schema_version": "membind.judge-qualification-summary.v1",
                "protocol_id": "judge-qualification-v1.0",
                "scientific_surface": "JUDGE_QUALIFICATION_ONLY",
                "run_id": "jq-b00a9689796c1e67",
                "attempt_status": "complete",
                "qualification_status": "PASS",
                "mergeable": True,
                "planned_item_count": 14,
                "terminal_item_count": 14,
                "eligible_item_count": 14,
                "agreement_count": 14,
                "observed_agreement": 1.0,
                "cohens_kappa": 1.0,
                "invalid_output_count": 0,
                "service_error_count": 0,
                "retry_count_total": 0,
                "confusion_matrix": {
                    "true_positive": 7,
                    "true_negative": 7,
                    "false_positive": 0,
                    "false_negative": 0,
                },
                "runtime_identity_payload_sha256": self.judge_runtime[
                    "payload_sha256"
                ],
                "freeze_payload_sha256": self.judge_freeze["payload_sha256"],
            }
        )
        _write_json(self.judge_summary_path, self.judge_summary)

        self.authorization: dict[str, object] = {
            "schema_version": "membind.native-characterization-c5-authorization.v1",
            "history_id": HISTORY_ID,
            "episode_count": 49,
            "episode_source_hashes": [episode.source_hash for episode in self.episodes],
            "concurrency_grid": [1, 2, 4, 8],
            "graph_namespaces": list(NAMESPACES),
            "screening_pass_count": 1,
            "workplan_sha256": SHA_A,
            "freeze_path": self.freeze_path.relative_to(self.validation).as_posix(),
            "freeze_sha256": _sha(self.freeze_path),
            "freeze_payload_sha256": self.freeze["payload_sha256"],
            "c4_summary_path": self.c4_path.relative_to(self.validation).as_posix(),
            "c4_summary_sha256": _sha(self.c4_path),
            "c4_summary_payload_sha256": self.c4["payload_sha256"],
            "c4_disposition": {
                "run_id": C4_RUN_ID,
                "summary_path": self.c4_path.relative_to(self.validation).as_posix(),
                "summary_sha256": _sha(self.c4_path),
                "summary_payload_sha256": self.c4["payload_sha256"],
                "summary_status": "complete",
                "summary_mergeable": False,
                "checkpoint_path": self.c4_checkpoint_path.relative_to(
                    self.validation
                ).as_posix(),
                "checkpoint_sha256": _sha(self.c4_checkpoint_path),
                "checkpoint_payload_sha256": self.c4_checkpoint["payload_sha256"],
                "events_path": self.c4_events_path.relative_to(
                    self.validation
                ).as_posix(),
                "events_sha256": _sha(self.c4_events_path),
                "event_count": 736,
                "failure_event_count": 1,
                "last_failure_event_sequence": 735,
                "last_failure_event_payload_sha256": self.c4_events[-1][
                    "payload_sha256"
                ],
                "retained_failure": {
                    "attempt_status": "incomplete_invalid_non_mergeable",
                    "failure_stage": "verification",
                    "error_class": "builtins.TypeError",
                },
                "bounded_use": C4_BOUNDED_USE,
            },
            "judge_qualification_summary_path": self.judge_summary_path.relative_to(
                self.validation
            ).as_posix(),
            "judge_qualification_summary_sha256": _sha(self.judge_summary_path),
            "judge_qualification_summary_payload_sha256": self.judge_summary[
                "payload_sha256"
            ],
            "judge_runtime_identity_path": self.judge_runtime_path.relative_to(
                self.validation
            ).as_posix(),
            "judge_runtime_identity_sha256": _sha(self.judge_runtime_path),
            "judge_runtime_identity_payload_sha256": self.judge_runtime[
                "payload_sha256"
            ],
            "judge_closure": {
                "manifest_path": self.judge_manifest_path.relative_to(
                    self.validation
                ).as_posix(),
                "manifest_sha256": _sha(self.judge_manifest_path),
                "manifest_payload_sha256": self.judge_manifest["payload_sha256"],
                "fixture_freeze_path": self.judge_freeze_path.relative_to(
                    self.validation
                ).as_posix(),
                "fixture_freeze_sha256": _sha(self.judge_freeze_path),
                "fixture_freeze_payload_sha256": self.judge_freeze[
                    "payload_sha256"
                ],
                "checkpoint_path": self.judge_checkpoint_path.relative_to(
                    self.validation
                ).as_posix(),
                "checkpoint_sha256": _sha(self.judge_checkpoint_path),
                "checkpoint_payload_sha256": self.judge_checkpoint[
                    "payload_sha256"
                ],
                "events_path": self.judge_events_path.relative_to(
                    self.validation
                ).as_posix(),
                "events_sha256": _sha(self.judge_events_path),
                "event_count": 28,
                "last_event_payload_sha256": self.judge_events[-1][
                    "payload_sha256"
                ],
            },
            "c5_live_tcb_paths": dict(TCB_PATHS),
            "c5_live_tcb_sha256": {
                name: _sha(self.validation / relative)
                for name, relative in TCB_PATHS.items()
            },
            "c5_focused_regression": {
                "status": "green",
                "test_count": 80,
                "path": GREEN_RECEIPT_PATHS["c5_focused_regression"],
                "sha256": _sha(
                    self.validation / GREEN_RECEIPT_PATHS["c5_focused_regression"]
                ),
            },
            "c5_full_offline_regression": {
                "status": "green",
                "test_count": 814,
                "path": GREEN_RECEIPT_PATHS["c5_full_offline_regression"],
                "sha256": _sha(
                    self.validation
                    / GREEN_RECEIPT_PATHS["c5_full_offline_regression"]
                ),
            },
            "c5_stale_state_regression": {
                "status": "green",
                "test_count": 80,
                "path": GREEN_RECEIPT_PATHS["c5_stale_state_regression"],
                "sha256": _sha(
                    self.validation
                    / GREEN_RECEIPT_PATHS["c5_stale_state_regression"]
                ),
            },
            "live_authorized": True,
        }
        self.state: dict[str, object] = {
            "protocol_version": "current-validation-v1.3",
            "current_stage": "NATIVE_CHARACTERIZATION",
            "status": "native_characterization_c5_live_only",
            "current_action_scope": "native_characterization_c5_live_only",
            "current_blocker": None,
            "next_allowed_action": "run_native_characterization_c5",
            "authorized_live_actions": ["native_characterization_c5"],
            "native_characterization_live_authorized": True,
            "live_h0_candidate_authorized": False,
            "authorized_h0_candidate_id": None,
            "service_admin_authorized": False,
            "v3_smoke_003_authorized": False,
            "stage_progress": {
                "native_characterization": live.TARGET_PROGRESS,
            },
            "native_characterization_c5_authorization": self.authorization,
        }
        self.write_state()

    def write_state(self) -> None:
        _write_json(self.state_path, self.state)

    def rebind_freeze(self) -> None:
        candidate = dict(self.freeze)
        candidate.pop("payload_sha256", None)
        self.freeze = _seal(candidate)
        _write_json(self.freeze_path, self.freeze)
        self.authorization["freeze_sha256"] = _sha(self.freeze_path)
        self.authorization["freeze_payload_sha256"] = self.freeze["payload_sha256"]
        self.write_state()

    def rebind_c4(self) -> None:
        candidate = dict(self.c4)
        candidate.pop("payload_sha256", None)
        self.c4 = _seal(candidate)
        _write_json(self.c4_path, self.c4)
        self.authorization["c4_summary_sha256"] = _sha(self.c4_path)
        self.authorization["c4_summary_payload_sha256"] = self.c4["payload_sha256"]
        self.write_state()

    def rebind_judge_runtime(self) -> None:
        candidate = dict(self.judge_runtime)
        candidate.pop("payload_sha256", None)
        self.judge_runtime = _seal(candidate)
        _write_json(self.judge_runtime_path, self.judge_runtime)
        self.authorization["judge_runtime_identity_sha256"] = _sha(
            self.judge_runtime_path
        )
        self.authorization["judge_runtime_identity_payload_sha256"] = (
            self.judge_runtime["payload_sha256"]
        )
        self.judge_summary["runtime_identity_payload_sha256"] = self.judge_runtime[
            "payload_sha256"
        ]
        self.rebind_judge_summary()

    def rebind_judge_summary(self) -> None:
        candidate = dict(self.judge_summary)
        candidate.pop("payload_sha256", None)
        self.judge_summary = _seal(candidate)
        _write_json(self.judge_summary_path, self.judge_summary)
        self.authorization["judge_qualification_summary_sha256"] = _sha(
            self.judge_summary_path
        )
        self.authorization["judge_qualification_summary_payload_sha256"] = (
            self.judge_summary["payload_sha256"]
        )
        self.write_state()

    def dependencies(
        self,
        events: list[tuple[object, ...]],
        *,
        namespace_counts: dict[str, core.NamespaceCounts] | None = None,
        run_c5: object | None = None,
        episode_builder: object = dataset.build_episodes,
        qa_evaluator: object | None = None,
    ) -> live.C5LiveDependencies:
        def gate(action: object, *, state_path: object) -> state_gate.GateDecision:
            events.append(("gate", action, Path(state_path)))
            return state_gate.GateDecision(True, "authorized", "native_characterization_c5")

        def state_loader(path: Path) -> dict[str, object]:
            events.append(("state", path))
            return json.loads(path.read_text(encoding="ascii"))

        def raw_loader(path: Path) -> list[dict[str, object]]:
            events.append(("dataset", path))
            return dataset.load_json_records(path)

        async def preflight(namespaces: tuple[str, ...]) -> dict[str, core.NamespaceCounts]:
            events.append(("preflight", namespaces))
            return namespace_counts or {
                namespace: core.NamespaceCounts(0, 0) for namespace in namespaces
            }

        def store_factory(**kwargs: object) -> object:
            events.append(("store", kwargs["run_id"]))
            return object()

        async def default_runner(**kwargs: object) -> dict[str, object]:
            events.append(("runner",))
            return {"status": "complete", "completed_block_indices": [0, 1, 2, 3]}

        async def default_qa(_runtime: object, _block: core.C5Block) -> dict[str, object]:
            return {"status": "SUCCESS", "correct": True}

        def runtime_factory_builder(**kwargs: object) -> object:
            events.append(("runtime_factory", tuple(kwargs["graph_namespaces"])))

            async def factory(_block: core.C5Block) -> object:
                raise AssertionError("the mocked core must not open a live runtime")

            return factory

        return live.C5LiveDependencies(
            gate_checker=gate,
            state_loader=state_loader,
            raw_dataset_loader=raw_loader,
            episode_builder=episode_builder,
            namespace_preflight=preflight,
            runtime_factory_builder=runtime_factory_builder,
            store_factory=store_factory,
            run_c5=run_c5 or default_runner,
            qa_evaluator=qa_evaluator or default_qa,
        )

    def constants(self):
        return mock.patch.multiple(
            live,
            VALIDATION_ROOT=self.validation.resolve(),
            DEFAULT_STATE_PATH=self.state_path.resolve(),
            FROZEN_DATASET_PATH=self.raw_path.resolve(),
        )


class NativeCharacterizationC5LiveTests(IsolatedAsyncioTestCase):
    maxDiff = None

    async def test_fresh_controller_lease_precedes_preflight_and_store_mutation(self) -> None:
        fixture = Fixture(self)
        first_events: list[tuple[object, ...]] = []
        second_events: list[tuple[object, ...]] = []
        runner_started = asyncio.Event()
        release_runner = asyncio.Event()
        runner_calls = 0

        async def blocking_first_runner(**_kwargs: object) -> dict[str, object]:
            nonlocal runner_calls
            runner_calls += 1
            if runner_calls == 1:
                runner_started.set()
                await release_runner.wait()
            return {
                "status": "complete",
                "completed_block_indices": [0, 1, 2, 3],
            }

        first = asyncio.create_task(
            live.execute_c5_live(
                validation_root=fixture.validation,
                state_path=fixture.state_path,
                run_id=RUN_ID,
                dependencies=fixture.dependencies(
                    first_events, run_c5=blocking_first_runner
                ),
                progress_stream=None,
            )
        )
        with fixture.constants():
            await runner_started.wait()
            with self.assertRaisesRegex(
                live.C5LiveAdapterError, "c5_run_lease_locked"
            ):
                await live.execute_c5_live(
                    validation_root=fixture.validation,
                    state_path=fixture.state_path,
                    run_id=RUN_ID,
                    dependencies=fixture.dependencies(
                        second_events, run_c5=blocking_first_runner
                    ),
                    progress_stream=None,
                )
            self.assertEqual([event[0] for event in second_events], ["gate"])
            self.assertEqual(runner_calls, 1)
            release_runner.set()
            await first

            lease = live._C5RunLease.acquire(fixture.runs_root, RUN_ID)
            lease.close()

    async def test_resume_controller_lease_precedes_recovery_mutation(self) -> None:
        fixture = Fixture(self)
        first_events: list[tuple[object, ...]] = []
        second_events: list[tuple[object, ...]] = []
        runner_started = asyncio.Event()
        release_runner = asyncio.Event()
        runner_calls = 0
        recovery_calls = 0

        async def blocking_first_runner(**_kwargs: object) -> dict[str, object]:
            nonlocal runner_calls
            runner_calls += 1
            if runner_calls == 1:
                runner_started.set()
                await release_runner.wait()
            return {
                "status": "complete",
                "completed_block_indices": [0, 1, 2, 3],
            }

        def prepare(**_kwargs: object) -> dict[str, object]:
            nonlocal recovery_calls
            recovery_calls += 1
            return {"status": "running"}

        inspection = live.artifacts.C5ResumeInspection(
            completed_block_indices=(),
            partial_block_index=0,
        )
        store = object()
        patches = (
            mock.patch.object(
                live.artifacts,
                "verify_c5_live_artifacts",
                return_value={
                    "attempt_status": live.artifacts.INCOMPLETE_NON_MERGEABLE,
                    "failure_event_count": 0,
                },
            ),
            mock.patch.object(
                live.artifacts,
                "prepare_c5_running_resume_prefix",
                side_effect=prepare,
            ),
            mock.patch.object(
                live.artifacts,
                "inspect_c5_resume_prefix",
                return_value=inspection,
            ),
            mock.patch.object(
                live.artifacts.C5LiveArtifactStore,
                "open_existing",
                return_value=store,
            ),
        )
        with fixture.constants(), patches[0], patches[1], patches[2], patches[3]:
            first = asyncio.create_task(
                live.execute_c5_live(
                    validation_root=fixture.validation,
                    state_path=fixture.state_path,
                    resume_run_id=RESUME_RUN_ID,
                    dependencies=fixture.dependencies(
                        first_events, run_c5=blocking_first_runner
                    ),
                    progress_stream=None,
                )
            )
            await runner_started.wait()
            self.assertEqual(recovery_calls, 1)
            with self.assertRaisesRegex(
                live.C5LiveAdapterError, "c5_run_lease_locked"
            ):
                await live.execute_c5_live(
                    validation_root=fixture.validation,
                    state_path=fixture.state_path,
                    resume_run_id=RESUME_RUN_ID,
                    dependencies=fixture.dependencies(
                        second_events, run_c5=blocking_first_runner
                    ),
                    progress_stream=None,
                )
            self.assertEqual([event[0] for event in second_events], ["gate"])
            self.assertEqual(recovery_calls, 1)
            self.assertEqual(runner_calls, 1)
            release_runner.set()
            await first

            lease = live._C5RunLease.acquire(fixture.runs_root, RESUME_RUN_ID)
            lease.close()

    async def test_controller_lease_is_held_until_qa_close_completes(self) -> None:
        fixture = Fixture(self)
        events: list[tuple[object, ...]] = []
        close_started = asyncio.Event()
        release_close = asyncio.Event()

        class BlockingCloseQA(ClosableQA):
            async def aclose(self) -> None:
                close_started.set()
                await release_close.wait()
                await super().aclose()

        class LockCheckingStore:
            def __init__(self) -> None:
                self.close_observed_locked = False

            def close(self) -> None:
                try:
                    competing = live._C5RunLease.acquire(
                        fixture.runs_root, RUN_ID
                    )
                except live.C5LiveAdapterError as error:
                    self.close_observed_locked = (
                        error.code == "c5_run_lease_locked"
                    )
                else:
                    competing.close()

        qa = BlockingCloseQA()
        store = LockCheckingStore()
        dependencies = fixture.dependencies(events, qa_evaluator=qa)
        dependencies = replace(
            dependencies, store_factory=lambda **_kwargs: store
        )
        with fixture.constants():
            controller = asyncio.create_task(
                live.execute_c5_live(
                    validation_root=fixture.validation,
                    state_path=fixture.state_path,
                    run_id=RUN_ID,
                    dependencies=dependencies,
                    progress_stream=None,
                )
            )
            await close_started.wait()
            with self.assertRaisesRegex(
                live.C5LiveAdapterError, "c5_run_lease_locked"
            ):
                live._C5RunLease.acquire(fixture.runs_root, RUN_ID)
            release_close.set()
            await controller

        self.assertTrue(qa.closed)
        self.assertTrue(store.close_observed_locked)
        lease = live._C5RunLease.acquire(fixture.runs_root, RUN_ID)
        lease.close()

    async def test_process_exit_releases_controller_lease(self) -> None:
        fixture = Fixture(self)
        script = """
import sys
from pathlib import Path
sys.path.insert(0, sys.argv[1])
from native_characterization_c5_live import _C5RunLease
lease = _C5RunLease.acquire(Path(sys.argv[2]), sys.argv[3])
print('locked', flush=True)
sys.stdin.buffer.read(1)
"""
        process = await asyncio.create_subprocess_exec(
            sys.executable,
            "-c",
            script,
            str(ROOT / "src"),
            str(fixture.runs_root),
            RUN_ID,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        self.assertIsNotNone(process.stdout)
        self.assertEqual(await process.stdout.readline(), b"locked\n")
        with self.assertRaisesRegex(
            live.C5LiveAdapterError, "c5_run_lease_locked"
        ):
            live._C5RunLease.acquire(fixture.runs_root, RUN_ID)
        self.assertIsNotNone(process.stdin)
        process.stdin.write(b"x")
        await process.stdin.drain()
        self.assertEqual(await process.wait(), 0)

        lease = live._C5RunLease.acquire(fixture.runs_root, RUN_ID)
        lease.close()

    async def test_qa_close_failure_still_closes_store_before_lease_release(self) -> None:
        fixture = Fixture(self)
        events: list[tuple[object, ...]] = []

        class FailingCloseQA(ClosableQA):
            async def aclose(self) -> None:
                raise RuntimeError("sanitized close failure")

        class LockCheckingStore:
            def __init__(self) -> None:
                self.closed_while_locked = False

            def close(self) -> None:
                try:
                    competing = live._C5RunLease.acquire(
                        fixture.runs_root, RUN_ID
                    )
                except live.C5LiveAdapterError as error:
                    self.closed_while_locked = error.code == "c5_run_lease_locked"
                else:
                    competing.close()

        store = LockCheckingStore()
        dependencies = fixture.dependencies(events, qa_evaluator=FailingCloseQA())
        dependencies = replace(
            dependencies, store_factory=lambda **_kwargs: store
        )
        with fixture.constants(), self.assertRaisesRegex(
            RuntimeError, "sanitized close failure"
        ):
            await live.execute_c5_live(
                validation_root=fixture.validation,
                state_path=fixture.state_path,
                run_id=RUN_ID,
                dependencies=dependencies,
                progress_stream=None,
            )

        self.assertTrue(store.closed_while_locked)
        lease = live._C5RunLease.acquire(fixture.runs_root, RUN_ID)
        lease.close()

    async def test_exact_c5_only_gate_is_first_and_denial_has_no_side_effects(self) -> None:
        fixture = Fixture(self)
        events: list[tuple[object, ...]] = []
        output = FlushStream()
        dependencies = fixture.dependencies(events)

        def deny(action: object, *, state_path: object) -> object:
            events.append(("gate-denied", action, Path(state_path)))
            raise state_gate.LiveActionDenied("action_not_authorized", action=str(action))

        dependencies = replace(dependencies, gate_checker=deny)
        with fixture.constants(), self.assertRaises(state_gate.LiveActionDenied):
            await live.execute_c5_live(
                validation_root=fixture.validation,
                state_path=fixture.state_path,
                run_id=RUN_ID,
                dependencies=dependencies,
                progress_stream=output,
            )
        self.assertEqual([event[0] for event in events], ["gate-denied"])
        self.assertEqual(events[0][1], state_gate.LiveAction.NATIVE_CHARACTERIZATION_C5)
        self.assertEqual(output.getvalue(), "")
        self.assertFalse((fixture.runs_root / RUN_ID).exists())

        for mutation in (
            lambda state: state.update({"status": "native_characterization_c4_live_only"}),
            lambda state: state.update(
                {"authorized_live_actions": ["native_characterization_c4"]}
            ),
            lambda state: state.update({"service_admin_authorized": True}),
            lambda state: state.pop("native_characterization_c5_authorization"),
        ):
            with self.subTest(mutation=mutation):
                candidate = Fixture(self)
                mutation(candidate.state)
                candidate.write_state()
                candidate_events: list[tuple[object, ...]] = []
                with candidate.constants(), self.assertRaises(live.C5LiveAdapterError):
                    await live.execute_c5_live(
                        validation_root=candidate.validation,
                        state_path=candidate.state_path,
                        run_id=RUN_ID,
                        dependencies=candidate.dependencies(candidate_events),
                        progress_stream=None,
                    )
                self.assertEqual(
                    [event[0] for event in candidate_events], ["gate", "state"]
                )

    async def test_freeze_history_c4_and_qualified_judge_are_hash_bound(self) -> None:
        def old_envelope(fixture: Fixture) -> None:
            fixture.freeze["runtime_identities"]["construction"]["max_model_len"] = 40960
            fixture.rebind_freeze()

        def wrong_history_hash(fixture: Fixture) -> None:
            fixture.freeze["dataset"]["calibration_histories"][0]["episodes"][12][
                "episode_source_sha256"
            ] = SHA_A
            fixture.rebind_freeze()

        def incomplete_c4(fixture: Fixture) -> None:
            fixture.c4["status"] = "incomplete_invalid_non_mergeable"
            fixture.rebind_c4()

        def failed_judge(fixture: Fixture) -> None:
            fixture.judge_summary["qualification_status"] = "FAIL"
            fixture.rebind_judge_summary()

        def judge_runtime_drift(fixture: Fixture) -> None:
            fixture.judge_runtime["identity"]["backend_public_config"]["max_tokens"] = 11
            fixture.rebind_judge_runtime()

        for mutation in (
            old_envelope,
            wrong_history_hash,
            incomplete_c4,
            failed_judge,
            judge_runtime_drift,
        ):
            with self.subTest(mutation=mutation.__name__):
                fixture = Fixture(self)
                mutation(fixture)
                events: list[tuple[object, ...]] = []
                with fixture.constants(), self.assertRaises(live.C5LiveAdapterError):
                    await live.execute_c5_live(
                        validation_root=fixture.validation,
                        state_path=fixture.state_path,
                        run_id=RUN_ID,
                        dependencies=fixture.dependencies(events),
                        progress_stream=None,
                    )
                self.assertNotIn("preflight", [event[0] for event in events])
                self.assertNotIn("store", [event[0] for event in events])
                self.assertNotIn("runner", [event[0] for event in events])

    async def test_exact_state_and_complete_receipt_are_required_before_preflight(self) -> None:
        def current_blocker(fixture: Fixture) -> None:
            fixture.state["current_blocker"] = "stale blocker"

        def live_h0(fixture: Fixture) -> None:
            fixture.state["live_h0_candidate_authorized"] = True

        def h0_candidate(fixture: Fixture) -> None:
            fixture.state["authorized_h0_candidate_id"] = "h0-stale"

        def missing_c4_closure(fixture: Fixture) -> None:
            fixture.authorization.pop("c4_disposition")

        def missing_judge_closure(fixture: Fixture) -> None:
            fixture.authorization.pop("judge_closure")

        def missing_tcb(fixture: Fixture) -> None:
            fixture.authorization.pop("c5_live_tcb_sha256")

        def missing_focused_green(fixture: Fixture) -> None:
            fixture.authorization.pop("c5_focused_regression")

        def missing_full_green(fixture: Fixture) -> None:
            fixture.authorization.pop("c5_full_offline_regression")

        for mutation in (
            current_blocker,
            live_h0,
            h0_candidate,
            missing_c4_closure,
            missing_judge_closure,
            missing_tcb,
            missing_focused_green,
            missing_full_green,
        ):
            with self.subTest(mutation=mutation.__name__):
                fixture = Fixture(self)
                mutation(fixture)
                fixture.write_state()
                events: list[tuple[object, ...]] = []
                with fixture.constants(), self.assertRaises(live.C5LiveAdapterError):
                    await live.execute_c5_live(
                        validation_root=fixture.validation,
                        state_path=fixture.state_path,
                        run_id=RUN_ID,
                        dependencies=fixture.dependencies(events),
                        progress_stream=None,
                    )
                self.assertEqual([event[0] for event in events], ["gate", "state"])

    async def test_tcb_file_drift_fails_before_preflight(self) -> None:
        fixture = Fixture(self)
        target = fixture.validation / TCB_PATHS["c5_live_core_source"]
        target.write_text("drifted after authorization\n", encoding="ascii")
        events: list[tuple[object, ...]] = []

        with fixture.constants(), self.assertRaisesRegex(
            live.C5LiveAdapterError, "c5_live_tcb"
        ):
            await live.execute_c5_live(
                validation_root=fixture.validation,
                state_path=fixture.state_path,
                run_id=RUN_ID,
                dependencies=fixture.dependencies(events),
                progress_stream=None,
            )
        self.assertNotIn("preflight", [event[0] for event in events])

    async def test_c4_closure_seal_shape_and_final_failure_are_revalidated(self) -> None:
        def checkpoint_shape(fixture: Fixture) -> None:
            fixture.c4_checkpoint["status"] = "complete"
            candidate = dict(fixture.c4_checkpoint)
            candidate.pop("payload_sha256", None)
            fixture.c4_checkpoint = _seal(candidate)
            _write_json(fixture.c4_checkpoint_path, fixture.c4_checkpoint)
            closure = fixture.authorization["c4_disposition"]
            closure["checkpoint_sha256"] = _sha(fixture.c4_checkpoint_path)
            closure["checkpoint_payload_sha256"] = fixture.c4_checkpoint[
                "payload_sha256"
            ]

        def final_failure(fixture: Fixture) -> None:
            fixture.c4_events[-1]["failure_stage"] = "finalization"
            candidate = dict(fixture.c4_events[-1])
            candidate.pop("payload_sha256", None)
            fixture.c4_events[-1] = _seal(candidate)
            _write_events(fixture.c4_events_path, fixture.c4_events)
            closure = fixture.authorization["c4_disposition"]
            closure["events_sha256"] = _sha(fixture.c4_events_path)
            closure["last_failure_event_payload_sha256"] = fixture.c4_events[-1][
                "payload_sha256"
            ]

        def truncated_events(fixture: Fixture) -> None:
            fixture.c4_events.pop(20)
            _write_events(fixture.c4_events_path, fixture.c4_events)
            fixture.authorization["c4_disposition"]["events_sha256"] = _sha(
                fixture.c4_events_path
            )

        for mutation in (checkpoint_shape, final_failure, truncated_events):
            with self.subTest(mutation=mutation.__name__):
                fixture = Fixture(self)
                mutation(fixture)
                fixture.write_state()
                events: list[tuple[object, ...]] = []
                with fixture.constants(), self.assertRaisesRegex(
                    live.C5LiveAdapterError, "c4_closure"
                ):
                    await live.execute_c5_live(
                        validation_root=fixture.validation,
                        state_path=fixture.state_path,
                        run_id=RUN_ID,
                        dependencies=fixture.dependencies(events),
                        progress_stream=None,
                    )
                self.assertNotIn("preflight", [event[0] for event in events])

    async def test_judge_closure_shapes_and_full_event_chain_are_revalidated(self) -> None:
        def manifest_binding(fixture: Fixture) -> None:
            fixture.judge_manifest["freeze_payload_sha256"] = SHA_A
            candidate = dict(fixture.judge_manifest)
            candidate.pop("payload_sha256", None)
            fixture.judge_manifest = _seal(candidate)
            _write_json(fixture.judge_manifest_path, fixture.judge_manifest)
            closure = fixture.authorization["judge_closure"]
            closure["manifest_sha256"] = _sha(fixture.judge_manifest_path)
            closure["manifest_payload_sha256"] = fixture.judge_manifest[
                "payload_sha256"
            ]

        def checkpoint_shape(fixture: Fixture) -> None:
            fixture.judge_checkpoint["terminal_item_count"] = 13
            candidate = dict(fixture.judge_checkpoint)
            candidate.pop("payload_sha256", None)
            fixture.judge_checkpoint = _seal(candidate)
            _write_json(fixture.judge_checkpoint_path, fixture.judge_checkpoint)
            closure = fixture.authorization["judge_closure"]
            closure["checkpoint_sha256"] = _sha(fixture.judge_checkpoint_path)
            closure["checkpoint_payload_sha256"] = fixture.judge_checkpoint[
                "payload_sha256"
            ]

        def event_chain(fixture: Fixture) -> None:
            fixture.judge_events[12]["previous_event_sha256"] = SHA_A
            candidate = dict(fixture.judge_events[12])
            candidate.pop("payload_sha256", None)
            fixture.judge_events[12] = _seal(candidate)
            _write_events(fixture.judge_events_path, fixture.judge_events)
            fixture.authorization["judge_closure"]["events_sha256"] = _sha(
                fixture.judge_events_path
            )

        for mutation in (manifest_binding, checkpoint_shape, event_chain):
            with self.subTest(mutation=mutation.__name__):
                fixture = Fixture(self)
                mutation(fixture)
                fixture.write_state()
                events: list[tuple[object, ...]] = []
                with fixture.constants(), self.assertRaisesRegex(
                    live.C5LiveAdapterError, "judge_closure"
                ):
                    await live.execute_c5_live(
                        validation_root=fixture.validation,
                        state_path=fixture.state_path,
                        run_id=RUN_ID,
                        dependencies=fixture.dependencies(events),
                        progress_stream=None,
                    )
                self.assertNotIn("preflight", [event[0] for event in events])

    async def test_all_frozen_namespaces_are_preflighted_before_store_or_core(self) -> None:
        fixture = Fixture(self)
        events: list[tuple[object, ...]] = []
        with fixture.constants():
            result = await live.execute_c5_live(
                validation_root=fixture.validation,
                state_path=fixture.state_path,
                run_id=RUN_ID,
                dependencies=fixture.dependencies(events),
                progress_stream=None,
            )
        self.assertEqual(result["status"], "complete")
        self.assertEqual(events[0][0], "gate")
        self.assertEqual(events[1][0], "state")
        self.assertIn(("preflight", NAMESPACES), events)
        self.assertLess(
            next(index for index, event in enumerate(events) if event[0] == "preflight"),
            next(index for index, event in enumerate(events) if event[0] == "store"),
        )
        self.assertLess(
            next(index for index, event in enumerate(events) if event[0] == "preflight"),
            next(index for index, event in enumerate(events) if event[0] == "runner"),
        )

    async def test_nonempty_preflight_fails_closed_without_cleanup_or_core(self) -> None:
        fixture = Fixture(self)
        events: list[tuple[object, ...]] = []
        counts = {namespace: core.NamespaceCounts(0, 0) for namespace in NAMESPACES}
        counts[NAMESPACES[2]] = core.NamespaceCounts(1, 2)

        with fixture.constants(), self.assertRaises(live.C5LiveAdapterError) as raised:
            await live.execute_c5_live(
                validation_root=fixture.validation,
                state_path=fixture.state_path,
                run_id=RUN_ID,
                dependencies=fixture.dependencies(events, namespace_counts=counts),
                progress_stream=None,
            )
        self.assertIn("namespace", raised.exception.code)
        self.assertEqual(sum(event[0] == "preflight" for event in events), 1)
        self.assertNotIn("clear", [event[0] for event in events])
        self.assertNotIn("store", [event[0] for event in events])
        self.assertNotIn("runner", [event[0] for event in events])

    async def test_success_delegates_once_with_exact_schedule_provenance_and_same_qa(self) -> None:
        fixture = Fixture(self)
        events: list[tuple[object, ...]] = []
        captured: dict[str, object] = {}
        call_count = 0

        async def inspect(**kwargs: object) -> dict[str, object]:
            nonlocal call_count
            call_count += 1
            captured.update(kwargs)
            return {"status": "complete", "completed_block_indices": [0, 1, 2, 3]}

        async def qa(runtime: object, block: core.C5Block) -> dict[str, object]:
            return {"status": "SUCCESS", "correct": runtime is not None and block is not None}

        with fixture.constants():
            await live.execute_c5_live(
                validation_root=fixture.validation,
                state_path=fixture.state_path,
                run_id=RUN_ID,
                dependencies=fixture.dependencies(events, run_c5=inspect, qa_evaluator=qa),
                progress_stream=None,
            )

        self.assertEqual(call_count, 1)
        self.assertIs(captured["qa_evaluator"], qa)
        self.assertEqual(captured["schedule"]["history_id"], HISTORY_ID)
        self.assertEqual(captured["schedule"]["concurrency_grid"], [1, 2, 4, 8])
        self.assertEqual(
            [block["graph_namespace"] for block in captured["schedule"]["block_schedules"]],
            list(NAMESPACES),
        )
        self.assertEqual(len(captured["episodes"]), 49)
        self.assertEqual(
            [episode.payload.source_hash for episode in captured["episodes"]],
            fixture.authorization["episode_source_hashes"],
        )
        self.assertEqual(
            set(captured["provenance_hashes"]),
            {
                "freeze_sha256",
                "freeze_payload_sha256",
                "dataset_source_sha256",
                "c4_summary_sha256",
                "c4_summary_payload_sha256",
                "judge_qualification_summary_sha256",
                "judge_qualification_summary_payload_sha256",
                "judge_runtime_identity_sha256",
                "judge_runtime_identity_payload_sha256",
                "c4_checkpoint_sha256",
                "c4_checkpoint_payload_sha256",
                "c4_events_sha256",
                "judge_manifest_sha256",
                "judge_manifest_payload_sha256",
                "judge_fixture_freeze_sha256",
                "judge_fixture_freeze_payload_sha256",
                "judge_checkpoint_sha256",
                "judge_checkpoint_payload_sha256",
                "judge_events_sha256",
            },
        )

    async def test_controller_closes_owned_qa_transport_after_core_returns(self) -> None:
        fixture = Fixture(self)
        events: list[tuple[object, ...]] = []
        qa = ClosableQA()

        with fixture.constants():
            await live.execute_c5_live(
                validation_root=fixture.validation,
                state_path=fixture.state_path,
                run_id=RUN_ID,
                dependencies=fixture.dependencies(events, qa_evaluator=qa),
                progress_stream=None,
            )

        self.assertTrue(qa.closed)

    async def test_wrong_dataset_episode_hash_fails_before_preflight(self) -> None:
        fixture = Fixture(self)
        events: list[tuple[object, ...]] = []

        def wrong_episode(record: dict[str, object]) -> list[dataset.Episode]:
            episodes = dataset.build_episodes(record)
            episodes[8] = replace(episodes[8], source_hash=SHA_A)
            return episodes

        with fixture.constants(), self.assertRaises(live.C5LiveAdapterError):
            await live.execute_c5_live(
                validation_root=fixture.validation,
                state_path=fixture.state_path,
                run_id=RUN_ID,
                dependencies=fixture.dependencies(events, episode_builder=wrong_episode),
                progress_stream=None,
            )
        self.assertNotIn("preflight", [event[0] for event in events])

    async def test_progress_sink_flushes_safe_json_and_drops_unsafe_payloads(self) -> None:
        output = FlushStream()
        sink = live._progress_sink(output)
        sink({"event": "block_checkpoint", "block_index": 1, "episode_count": 49})
        sink({"event": "unsafe", "prompt": "episode body secret"})
        sink({"event": "unsafe", "detail": "api-key=forbidden"})
        sink({"event": "unsafe", "response": {"status": 200}})

        rendered = [json.loads(line) for line in output.getvalue().splitlines()]
        self.assertEqual(
            rendered,
            [{"block_index": 1, "episode_count": 49, "event": "block_checkpoint"}],
        )
        self.assertEqual(output.flush_count, 1)


class NativeCharacterizationC5LiveCliTests(TestCase):
    def test_cli_requires_exactly_one_fresh_or_resume_run_id(self) -> None:
        parser = live.build_parser()
        with self.assertRaises(SystemExit):
            parser.parse_args([])
        with self.assertRaises(SystemExit):
            parser.parse_args(["--run-id", RUN_ID, "--resume-run-id", RESUME_RUN_ID])
        self.assertEqual(parser.parse_args(["--run-id", RUN_ID]).run_id, RUN_ID)
        self.assertEqual(
            parser.parse_args(["--resume-run-id", RESUME_RUN_ID]).resume_run_id,
            RESUME_RUN_ID,
        )

    def test_cli_exposes_no_authority_service_or_protocol_overrides(self) -> None:
        parser = live.build_parser()
        for forbidden in (
            "--namespace",
            "--concurrency",
            "--history-id",
            "--model",
            "--base-url",
            "--api-key",
            "--clear",
            "--skip-judge",
        ):
            with self.subTest(forbidden=forbidden), self.assertRaises(SystemExit):
                parser.parse_args(["--run-id", RUN_ID, forbidden, "value"])


if __name__ == "__main__":
    import unittest

    unittest.main()
