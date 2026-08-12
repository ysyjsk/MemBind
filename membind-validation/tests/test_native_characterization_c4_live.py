"""Offline boundary tests for the production C4/E3 adapter and CLI."""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import io
import json
import sys
import tempfile
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from unittest import IsolatedAsyncioTestCase, TestCase, mock


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

import current_state_gate as state_gate  # noqa: E402
import dataset  # noqa: E402
import native_characterization_c4_artifacts as c4a  # noqa: E402
import native_characterization_c4_live as live  # noqa: E402
import native_characterization_c4_runner as runner  # noqa: E402


HISTORY_ID = "07741c45"
RUN_ID = "c4-0123456789abcdef"
SHA_A = "a" * 64
SHA_B = "b" * 64


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(value, ensure_ascii=True, sort_keys=True, separators=(",", ":"))
        + "\n",
        encoding="ascii",
    )


def _seal(value: dict[str, object]) -> dict[str, object]:
    return c4a.seal_payload(value)


def _raw_record() -> dict[str, object]:
    return {
        "question_id": HISTORY_ID,
        "group_id": HISTORY_ID,
        "haystack_session_ids": [f"session-{index}" for index in range(49)],
        "haystack_dates": [f"2026-01-{index % 28 + 1:02d}T00:00:00Z" for index in range(49)],
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


class FakeDriver:
    def __init__(
        self,
        events: list[tuple[object, ...]],
        namespace: str,
        *,
        nodes: int = 0,
        relationships: int = 0,
    ) -> None:
        self.events = events
        self.namespace = namespace
        self.nodes = nodes
        self.relationships = relationships

    async def build_indices_and_constraints(self) -> None:
        self.events.append(("ready", self.namespace))

    async def execute_query(self, query: str, *, params: dict[str, str]) -> object:
        self.events.append(("counts", params["group_id"], query))
        return SimpleNamespace(
            records=[
                {
                    "node_count": self.nodes,
                    "relationship_count": self.relationships,
                }
            ]
        )


class FakeGraphiti:
    def __init__(
        self,
        events: list[tuple[object, ...]],
        namespace: str,
        *,
        nodes: int = 0,
        relationships: int = 0,
        fail_service: bool = False,
        fail_close: bool = False,
    ) -> None:
        self.events = events
        self.driver = FakeDriver(
            events, namespace, nodes=nodes, relationships=relationships
        )
        self.fail_service = fail_service
        self.fail_close = fail_close
        self.add_calls: list[dict[str, object]] = []
        self.close_count = 0

    async def add_episode(self, **kwargs: object) -> None:
        self.events.append(("add_episode", kwargs.get("group_id")))
        self.add_calls.append(dict(kwargs))
        if self.fail_service:
            raise RuntimeError(
                "secret remote response api-key=forbidden episode body must not escape"
            )

    async def close(self) -> None:
        self.close_count += 1
        self.events.append(("close", self.driver.namespace))
        if self.fail_close:
            raise RuntimeError("secret close failure")


class Fixture:
    def __init__(self, owner: IsolatedAsyncioTestCase) -> None:
        self.owner = owner
        temporary = tempfile.TemporaryDirectory()
        owner.addCleanup(temporary.cleanup)
        self.repo = Path(temporary.name)
        self.validation = self.repo / "membind-validation"
        self.validation.mkdir()
        self.state_path = self.validation / "CURRENT_STATE.json"
        self.schedule_path = (
            self.validation
            / "artifacts/diagnostics/native_characterization_c4_schedule_dry_run_20260812.json"
        )
        self.freeze_path = (
            self.validation
            / "artifacts/native_characterization/freeze_reference_aligned_64k.json"
        )
        self.split_path = (
            self.validation / "artifacts/dataset/frozen_split_v1_3.json"
        )
        self.raw_path = self.repo / "raw/longmemeval_s_cleaned.json"
        self.raw_path.parent.mkdir(parents=True)
        _write_json(self.raw_path, [_raw_record()])
        self.episodes = dataset.build_episodes(_raw_record())

        self.split: dict[str, object] = {
            "protocol_version": "current-validation-v1.3",
            "calibration_question_ids": [HISTORY_ID, "b", "c", "d"],
            "compatibility_development_question_ids": ["e"],
            "evaluation_question_ids": ["f"],
            "source_path": str(self.raw_path),
            "source_sha256": _sha(self.raw_path),
        }
        _write_json(self.split_path, self.split)

        self.freeze: dict[str, object] = _seal(
            {
                "schema_version": "membind.native-characterization-freeze.v1",
                "run_id": "native-characterization-freeze-reference-aligned-64k",
                "runtime_identities": {
                    "construction": {
                        "vllm_version": "0.26.0",
                        "served_model_id": "qwen3-32b-fp8",
                        "max_model_len": 65536,
                        "rope_type": "yarn",
                        "yarn_factor": 2.0,
                        "original_max_position_embeddings": 32768,
                        "rope_theta": 1000000,
                    }
                },
                "state_transition": {
                    "execution_envelope_updated": True,
                    "live_authorized": False,
                },
                "input_hashes": {"split_sha256": _sha(self.split_path)},
                "dataset": {
                    "source_sha256": _sha(self.raw_path),
                    "split_sha256": _sha(self.split_path),
                    "calibration_histories": [
                        {
                            "history_id": HISTORY_ID,
                            "episode_count": 49,
                            "episodes": [
                                {
                                    "source_sequence": episode.source_sequence,
                                    "episode_source_sha256": episode.source_hash,
                                }
                                for episode in self.episodes
                            ],
                        }
                    ],
                },
            }
        )
        _write_json(self.freeze_path, self.freeze)

        methods = ["Native-Sync"] * 5 + ["Native-Async-Serial"] * 5
        loads = [0.5, 0.8, 1.0, 1.2, 1.5] * 2
        self.schedule: dict[str, object] = _seal(
            {
                "schema_version": c4a.SCHEDULE_SCHEMA,
                "status": "dry_run",
                "stage": "C4/E3_OFFLINE_SCHEDULE",
                "run_id": "c2-17cdaabd562e9673",
                "history_id": HISTORY_ID,
                "schedule_semantics": (
                    "controlled_deterministic_absolute_open_loop_replay"
                ),
                "episode_ids": [f"{HISTORY_ID}:{index}" for index in range(49)],
                "block_schedules": [
                    {
                        "block_index": index,
                        "method": method,
                        "normalized_offered_load": load,
                        "graph_namespace": f"nc-e3-{index:016x}",
                        "interarrival_ns": 100 - index,
                        "absolute_arrival_offsets_ns": [
                            source_sequence * (100 - index)
                            for source_sequence in range(49)
                        ],
                    }
                    for index, (method, load) in enumerate(zip(methods, loads))
                ],
            }
        )
        _write_json(self.schedule_path, self.schedule)

        c2 = {
            "status": "verified",
            "run_id": "c2-17cdaabd562e9673",
            "verification_path": "artifacts/diagnostics/c2-verification.json",
            "verification_sha256": "1" * 64,
            "verification_payload_sha256": "2" * 64,
            "manifest_sha256": "3" * 64,
            "checkpoint_sha256": "4" * 64,
            "e1_breakdown_sha256": "5" * 64,
            "top_level_e1_breakdown_sha256": "5" * 64,
        }
        c3 = {
            "schema_version": "membind.native-characterization-c3-completion.v1",
            "status": "complete",
            "run_id": "c2-17cdaabd562e9673",
            "dependency_map_path": "artifacts/native_characterization/dependency_map.json",
            "dependency_map_sha256": "6" * 64,
            "dependency_map_payload_sha256": "7" * 64,
            "e2_path": "artifacts/native_characterization/e2.json",
            "e2_sha256": "8" * 64,
            "e2_payload_sha256": "9" * 64,
            "analyzer_source_sha256": "a" * 64,
            "focused_log_sha256": "b" * 64,
            "focused_test_count": 12,
        }
        self.authorization: dict[str, object] = {
            "schema_version": "membind.native-characterization-c4-authorization.v1",
            "source_state_sha256": "0" * 64,
            "schedule_path": str(self.schedule_path.relative_to(self.validation)),
            "schedule_sha256": _sha(self.schedule_path),
            "schedule_payload_sha256": self.schedule["payload_sha256"],
            "freeze_path": str(self.freeze_path.relative_to(self.validation)),
            "freeze_sha256": _sha(self.freeze_path),
            "freeze_payload_sha256": self.freeze["payload_sha256"],
            "c4_source_path": "src/native_characterization_c4.py",
            "c4_source_sha256": SHA_A,
            "c4_test_path": "tests/test_native_characterization_c4.py",
            "c4_test_sha256": SHA_A,
            "c4_green_log_path": "artifacts/tdd/c4-green.log",
            "c4_green_log_sha256": SHA_A,
            "c4_focused_test_count": 20,
            "c2_evidence": c2,
            "c3_evidence": c3,
            "operator_authorization_input": True,
            "live_authorized": True,
        }
        self.state: dict[str, object] = {
            "protocol_version": "current-validation-v1.3",
            "current_stage": "NATIVE_CHARACTERIZATION",
            "status": "native_characterization_c4_live_only",
            "current_action_scope": "native_characterization_c4_live_only",
            "current_blocker": None,
            "next_allowed_action": "run_native_characterization_c4",
            "authorized_live_actions": ["native_characterization_c4"],
            "native_characterization_live_authorized": True,
            "live_h0_candidate_authorized": False,
            "authorized_h0_candidate_id": None,
            "service_admin_authorized": False,
            "v3_smoke_003_authorized": False,
            "native_characterization_c4_authorization": self.authorization,
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

    def dependencies(
        self,
        events: list[tuple[object, ...]],
        *,
        run_c4: object | None = None,
        episode_builder: object = dataset.build_episodes,
        runtime_builder: object | None = None,
    ) -> live.C4LiveDependencies:
        def gate(action: object, *, state_path: object) -> state_gate.GateDecision:
            events.append(("gate", action, Path(state_path)))
            return state_gate.GateDecision(True, "authorized", "native_characterization_c4")

        def state_loader(path: Path) -> dict[str, object]:
            events.append(("state", path))
            return json.loads(path.read_text(encoding="ascii"))

        def raw_loader(path: Path) -> list[dict[str, object]]:
            events.append(("dataset", path))
            return dataset.load_json_records(path)

        self.graphitis: list[FakeGraphiti] = []

        def default_runtime_builder(**kwargs: object) -> object:
            index = len(self.graphitis)
            namespace = f"nc-e3-{index:016x}"
            events.append(
                (
                    "runtime",
                    kwargs["live_action"],
                    kwargs["structured_output_mode"],
                )
            )
            decision = kwargs["authorization_checker"](
                state_gate.LiveAction.NATIVE_CHARACTERIZATION_C4
            )
            self.owner.assertTrue(decision.allowed)
            graphiti = FakeGraphiti(events, namespace)
            self.graphitis.append(graphiti)
            return SimpleNamespace(graphiti=graphiti)

        async def default_runner(**kwargs: object) -> dict[str, object]:
            events.append(("runner",))
            self.owner.assertTrue(
                kwargs["gate_checker"](
                    state_gate.LiveAction.NATIVE_CHARACTERIZATION_C4,
                    state_path=self.state_path,
                ).allowed
            )
            blocks = kwargs["schedule"]["block_schedules"]
            for supplied in blocks:
                block = runner.C4Block(
                    block_index=supplied["block_index"],
                    method=supplied["method"],
                    normalized_offered_load=supplied["normalized_offered_load"],
                    graph_namespace=supplied["graph_namespace"],
                    interarrival_ns=supplied["interarrival_ns"],
                    absolute_arrival_offsets_ns=tuple(
                        supplied["absolute_arrival_offsets_ns"]
                    ),
                )
                runtime = await kwargs["runtime_factory"](block)
                counts = await runtime.namespace_counts()
                self.owner.assertEqual(counts, runner.NamespaceCounts(0, 0))
                await runtime.close()
            kwargs["progress_sink"](
                {"event": "terminal_success", "completed_episode_count": 490}
            )
            return {"status": "complete", "completed_episode_count": 490}

        return live.C4LiveDependencies(
            gate_checker=gate,
            state_loader=state_loader,
            raw_dataset_loader=raw_loader,
            episode_builder=episode_builder,
            runtime_builder=runtime_builder or default_runtime_builder,
            run_c4=run_c4 or default_runner,
        )

    def constants(self):
        return mock.patch.multiple(
            live,
            VALIDATION_ROOT=self.validation.resolve(),
            DEFAULT_STATE_PATH=self.state_path.resolve(),
        )


class NativeCharacterizationC4LiveTests(IsolatedAsyncioTestCase):
    maxDiff = None

    async def test_gate_is_first_then_exact_inputs_and_ten_fresh_u0_lifecycles(self) -> None:
        fixture = Fixture(self)
        events: list[tuple[object, ...]] = []
        output = FlushStream()
        with fixture.constants():
            result = await live.execute_c4_live(
                validation_root=fixture.validation,
                state_path=fixture.state_path,
                dependencies=fixture.dependencies(events),
                progress_stream=output,
            )

        self.assertEqual(result["status"], "complete")
        self.assertEqual(events[0][0], "gate")
        self.assertEqual(events[1][0], "state")
        self.assertLess(
            next(index for index, item in enumerate(events) if item[0] == "dataset"),
            next(index for index, item in enumerate(events) if item[0] == "runtime"),
        )
        self.assertEqual(sum(item[0] == "runtime" for item in events), 10)
        self.assertEqual(len({id(item) for item in fixture.graphitis}), 10)
        self.assertTrue(all(item.close_count == 1 for item in fixture.graphitis))
        for index in range(10):
            namespace = f"nc-e3-{index:016x}"
            self.assertLess(events.index(("ready", namespace)), next(
                position
                for position, event in enumerate(events)
                if event[0:2] == ("counts", namespace)
            ))
        rendered = [json.loads(line) for line in output.getvalue().splitlines()]
        self.assertEqual(rendered[-1]["event"], "terminal_success")
        self.assertEqual(output.flush_count, len(rendered))

    async def test_gate_denial_has_zero_downstream_calls_and_zero_output(self) -> None:
        fixture = Fixture(self)
        events: list[tuple[object, ...]] = []
        output = FlushStream()
        dependencies = fixture.dependencies(events)

        def deny(action: object, *, state_path: object) -> object:
            events.append(("gate-denied", action, state_path))
            raise state_gate.LiveActionDenied("action_not_authorized", action=str(action))

        dependencies = replace(dependencies, gate_checker=deny)
        with fixture.constants(), self.assertRaises(state_gate.LiveActionDenied):
            await live.execute_c4_live(
                validation_root=fixture.validation,
                state_path=fixture.state_path,
                dependencies=dependencies,
                progress_stream=output,
            )
        self.assertEqual([item[0] for item in events], ["gate-denied"])
        self.assertEqual(output.getvalue(), "")
        self.assertEqual(list((fixture.validation / "artifacts/native_characterization/runs").glob("c4-*")), [])

    async def test_missing_or_wrong_authorization_metadata_fails_closed(self) -> None:
        for mutation in (
            lambda state: state.pop("native_characterization_c4_authorization"),
            lambda state: state.update({"status": "native_characterization_c4_offline_only"}),
            lambda state: state["native_characterization_c4_authorization"].update(
                {"live_authorized": False}
            ),
            lambda state: state.update({"authorized_live_actions": ["native_characterization_c2"]}),
        ):
            with self.subTest(mutation=mutation):
                fixture = Fixture(self)
                mutation(fixture.state)
                fixture.write_state()
                events: list[tuple[object, ...]] = []
                with fixture.constants(), self.assertRaises(live.C4LiveAdapterError):
                    await live.execute_c4_live(
                        validation_root=fixture.validation,
                        state_path=fixture.state_path,
                        dependencies=fixture.dependencies(events),
                    )
                self.assertEqual(events[:2], [events[0], events[1]])
                self.assertEqual([item[0] for item in events], ["gate", "state"])

    async def test_schedule_path_file_hash_and_payload_seal_drift_fail_closed(self) -> None:
        for kind in ("path", "file", "payload"):
            with self.subTest(kind=kind):
                fixture = Fixture(self)
                if kind == "path":
                    fixture.authorization["schedule_path"] = "artifacts/diagnostics/other.json"
                    fixture.write_state()
                elif kind == "file":
                    fixture.schedule_path.write_bytes(fixture.schedule_path.read_bytes() + b" ")
                else:
                    broken = dict(fixture.schedule)
                    broken["payload_sha256"] = SHA_B
                    _write_json(fixture.schedule_path, broken)
                    fixture.authorization["schedule_sha256"] = _sha(fixture.schedule_path)
                    fixture.authorization["schedule_payload_sha256"] = SHA_B
                    fixture.write_state()
                events: list[tuple[object, ...]] = []
                with fixture.constants(), self.assertRaises(live.C4LiveAdapterError):
                    await live.execute_c4_live(
                        validation_root=fixture.validation,
                        state_path=fixture.state_path,
                        dependencies=fixture.dependencies(events),
                    )
                self.assertNotIn("dataset", [item[0] for item in events])
                self.assertNotIn("runtime", [item[0] for item in events])

    async def test_freeze_path_hash_payload_and_runtime_identity_drift_fail_closed(self) -> None:
        for kind in ("path", "file", "payload", "runtime"):
            with self.subTest(kind=kind):
                fixture = Fixture(self)
                if kind == "path":
                    fixture.authorization["freeze_path"] = "artifacts/native_characterization/other.json"
                    fixture.write_state()
                elif kind == "file":
                    fixture.freeze_path.write_bytes(fixture.freeze_path.read_bytes() + b" ")
                elif kind == "payload":
                    broken = dict(fixture.freeze)
                    broken["payload_sha256"] = SHA_B
                    _write_json(fixture.freeze_path, broken)
                    fixture.authorization["freeze_sha256"] = _sha(fixture.freeze_path)
                    fixture.authorization["freeze_payload_sha256"] = SHA_B
                    fixture.write_state()
                else:
                    fixture.freeze["runtime_identities"]["construction"]["max_model_len"] = 40960
                    fixture.rebind_freeze()
                events: list[tuple[object, ...]] = []
                with fixture.constants(), self.assertRaises(live.C4LiveAdapterError):
                    await live.execute_c4_live(
                        validation_root=fixture.validation,
                        state_path=fixture.state_path,
                        dependencies=fixture.dependencies(events),
                    )
                self.assertNotIn("dataset", [item[0] for item in events])
                self.assertNotIn("runtime", [item[0] for item in events])

    async def test_split_and_raw_dataset_source_hash_drift_fail_closed(self) -> None:
        for kind in ("split", "source"):
            with self.subTest(kind=kind):
                fixture = Fixture(self)
                target = fixture.split_path if kind == "split" else fixture.raw_path
                target.write_bytes(target.read_bytes() + b" ")
                events: list[tuple[object, ...]] = []
                with fixture.constants(), self.assertRaises(live.C4LiveAdapterError):
                    await live.execute_c4_live(
                        validation_root=fixture.validation,
                        state_path=fixture.state_path,
                        dependencies=fixture.dependencies(events),
                    )
                self.assertNotIn("runtime", [item[0] for item in events])

    async def test_wrong_history_count_order_or_episode_hash_fails_closed(self) -> None:
        def wrong_history(record: dict[str, object]) -> list[dataset.Episode]:
            return [replace(item, question_id="wrong") for item in dataset.build_episodes(record)]

        def wrong_count(record: dict[str, object]) -> list[dataset.Episode]:
            return dataset.build_episodes(record)[:-1]

        def wrong_order(record: dict[str, object]) -> list[dataset.Episode]:
            return list(reversed(dataset.build_episodes(record)))

        def wrong_hash(record: dict[str, object]) -> list[dataset.Episode]:
            values = dataset.build_episodes(record)
            values[12] = replace(values[12], source_hash=SHA_B)
            return values

        for builder in (wrong_history, wrong_count, wrong_order, wrong_hash):
            with self.subTest(builder=builder.__name__):
                fixture = Fixture(self)
                events: list[tuple[object, ...]] = []
                with fixture.constants(), self.assertRaises(live.C4LiveAdapterError):
                    await live.execute_c4_live(
                        validation_root=fixture.validation,
                        state_path=fixture.state_path,
                        dependencies=fixture.dependencies(
                            events, episode_builder=builder
                        ),
                    )
                self.assertNotIn("runtime", [item[0] for item in events])

    async def test_exact_history_and_provenance_are_passed_to_runner(self) -> None:
        fixture = Fixture(self)
        events: list[tuple[object, ...]] = []
        captured: dict[str, object] = {}

        async def inspect(**kwargs: object) -> dict[str, object]:
            captured.update(kwargs)
            return {"status": "complete"}

        with fixture.constants():
            await live.execute_c4_live(
                validation_root=fixture.validation,
                state_path=fixture.state_path,
                dependencies=fixture.dependencies(events, run_c4=inspect),
            )
        episodes = captured["episodes"]
        self.assertEqual(len(episodes), 49)
        self.assertEqual(
            [item.source_sequence for item in episodes], list(range(49))
        )
        self.assertTrue(all(item.payload.question_id == HISTORY_ID for item in episodes))
        self.assertEqual(
            set(captured["provenance_hashes"]), set(c4a.REQUIRED_PROVENANCE_HASHES)
        )
        self.assertIsNone(captured["resume_run_id"])

    async def test_resume_run_id_is_passed_only_to_runner(self) -> None:
        fixture = Fixture(self)
        events: list[tuple[object, ...]] = []
        captured: dict[str, object] = {}

        async def inspect(**kwargs: object) -> dict[str, object]:
            captured.update(kwargs)
            return {"status": "complete", "run_id": RUN_ID}

        with fixture.constants():
            await live.execute_c4_live(
                validation_root=fixture.validation,
                state_path=fixture.state_path,
                resume_run_id=RUN_ID,
                dependencies=fixture.dependencies(events, run_c4=inspect),
            )

        self.assertEqual(captured["resume_run_id"], RUN_ID)
        self.assertEqual(
            captured["creation_command"][-2:],
            ["--resume-run-id", RUN_ID],
        )
        self.assertNotIn("runtime", [item[0] for item in events])

    async def test_terminal_failure_recovery_flag_is_passed_only_to_runner(self) -> None:
        fixture = Fixture(self)
        events: list[tuple[object, ...]] = []
        captured: dict[str, object] = {}

        async def inspect(**kwargs: object) -> dict[str, object]:
            captured.update(kwargs)
            return {"status": "complete", "run_id": RUN_ID}

        with fixture.constants():
            await live.execute_c4_live(
                validation_root=fixture.validation,
                state_path=fixture.state_path,
                resume_run_id=RUN_ID,
                recover_terminal_failure=True,
                dependencies=fixture.dependencies(events, run_c4=inspect),
            )

        self.assertEqual(captured["resume_run_id"], RUN_ID)
        self.assertEqual(captured["recover_terminal_failure"], True)
        self.assertEqual(
            captured["creation_command"][-3:],
            ["--resume-run-id", RUN_ID, "--recover-terminal-failure"],
        )
        self.assertNotIn("runtime", [item[0] for item in events])

    async def test_service_replaces_only_group_and_calls_graphiti_once(self) -> None:
        fixture = Fixture(self)
        events: list[tuple[object, ...]] = []
        captured_runtime: object | None = None

        async def inspect(**kwargs: object) -> dict[str, object]:
            nonlocal captured_runtime
            supplied = kwargs["schedule"]["block_schedules"][0]
            block = runner.C4Block(
                block_index=0,
                method=supplied["method"],
                normalized_offered_load=supplied["normalized_offered_load"],
                graph_namespace=supplied["graph_namespace"],
                interarrival_ns=supplied["interarrival_ns"],
                absolute_arrival_offsets_ns=tuple(supplied["absolute_arrival_offsets_ns"]),
            )
            captured_runtime = await kwargs["runtime_factory"](block)
            await captured_runtime.namespace_counts()
            await captured_runtime.service(kwargs["episodes"][0], 123)
            await captured_runtime.close()
            return {"status": "complete"}

        def kwargs_builder(episode: dataset.Episode) -> dict[str, object]:
            return {
                "name": episode.name,
                "group_id": episode.group_id,
                "episode_body": episode.body,
            }

        with fixture.constants(), mock.patch.object(
            live, "graphiti_episode_kwargs", side_effect=kwargs_builder
        ) as kwargs_mock:
            await live.execute_c4_live(
                validation_root=fixture.validation,
                state_path=fixture.state_path,
                dependencies=fixture.dependencies(events, run_c4=inspect),
            )
        graphiti = fixture.graphitis[0]
        self.assertEqual(kwargs_mock.call_count, 1)
        supplied_episode = kwargs_mock.call_args.args[0]
        self.assertEqual(supplied_episode.group_id, "nc-e3-0000000000000000")
        self.assertEqual(supplied_episode.body, fixture.episodes[0].body)
        self.assertEqual(len(graphiti.add_calls), 1)
        self.assertEqual(
            graphiti.add_calls[0]["group_id"], "nc-e3-0000000000000000"
        )

    async def test_resume_cleanup_hook_uses_exact_block_namespace(self) -> None:
        fixture = Fixture(self)
        events: list[tuple[object, ...]] = []
        clear_calls: list[tuple[object, list[str]]] = []

        async def inspect(**kwargs: object) -> dict[str, object]:
            supplied = kwargs["schedule"]["block_schedules"][3]
            block = runner.C4Block(
                block_index=3,
                method=supplied["method"],
                normalized_offered_load=supplied["normalized_offered_load"],
                graph_namespace=supplied["graph_namespace"],
                interarrival_ns=supplied["interarrival_ns"],
                absolute_arrival_offsets_ns=tuple(supplied["absolute_arrival_offsets_ns"]),
            )
            runtime = await kwargs["runtime_factory"](block)
            await runtime.clear_namespace()
            await runtime.close()
            return {"status": "complete"}

        async def clear_spy(driver: object, *, group_ids: list[str]) -> None:
            clear_calls.append((driver, list(group_ids)))

        with fixture.constants(), mock.patch.object(live, "clear_data", side_effect=clear_spy):
            await live.execute_c4_live(
                validation_root=fixture.validation,
                state_path=fixture.state_path,
                dependencies=fixture.dependencies(events, run_c4=inspect),
            )

        self.assertEqual(len(clear_calls), 1)
        self.assertIs(clear_calls[0][0], fixture.graphitis[0].driver)
        self.assertEqual(clear_calls[0][1], ["nc-e3-0000000000000003"])

    async def test_nonempty_namespace_and_service_failure_are_sanitized_and_closed(self) -> None:
        for kind in ("namespace", "service"):
            with self.subTest(kind=kind):
                fixture = Fixture(self)
                events: list[tuple[object, ...]] = []

                def runtime_builder(**kwargs: object) -> object:
                    namespace = "nc-e3-0000000000000000"
                    graphiti = FakeGraphiti(
                        events,
                        namespace,
                        nodes=1 if kind == "namespace" else 0,
                        fail_service=kind == "service",
                    )
                    fixture.graphitis.append(graphiti)
                    return SimpleNamespace(graphiti=graphiti)

                async def inspect(**kwargs: object) -> dict[str, object]:
                    supplied = kwargs["schedule"]["block_schedules"][0]
                    block = runner.C4Block(
                        block_index=0,
                        method=supplied["method"],
                        normalized_offered_load=supplied["normalized_offered_load"],
                        graph_namespace=supplied["graph_namespace"],
                        interarrival_ns=supplied["interarrival_ns"],
                        absolute_arrival_offsets_ns=tuple(supplied["absolute_arrival_offsets_ns"]),
                    )
                    runtime = await kwargs["runtime_factory"](block)
                    try:
                        await runtime.namespace_counts()
                        await runtime.service(kwargs["episodes"][0], 0)
                    finally:
                        await runtime.close()
                    return {"status": "unexpected"}

                with fixture.constants(), mock.patch.object(
                    live,
                    "graphiti_episode_kwargs",
                    side_effect=lambda episode: {"group_id": episode.group_id},
                ), self.assertRaises(live.C4LiveAdapterError) as raised:
                    await live.execute_c4_live(
                        validation_root=fixture.validation,
                        state_path=fixture.state_path,
                        dependencies=fixture.dependencies(
                            events,
                            run_c4=inspect,
                            runtime_builder=runtime_builder,
                        ),
                    )
                self.assertNotIn("secret", str(raised.exception).casefold())
                self.assertEqual(
                    raised.exception.token_envelope,
                    {
                        "prompt_tokens": None,
                        "output_tokens": None,
                        "requested_max_tokens": 16384,
                    },
                )
                self.assertEqual(fixture.graphitis[0].close_count, 1)

    async def test_bad_namespace_is_rejected_before_runtime_builder(self) -> None:
        fixture = Fixture(self)
        fixture.schedule["block_schedules"][0]["graph_namespace"] = "not-c4"
        fixture.schedule = _seal(
            {key: value for key, value in fixture.schedule.items() if key != "payload_sha256"}
        )
        _write_json(fixture.schedule_path, fixture.schedule)
        fixture.authorization["schedule_sha256"] = _sha(fixture.schedule_path)
        fixture.authorization["schedule_payload_sha256"] = fixture.schedule["payload_sha256"]
        fixture.write_state()
        events: list[tuple[object, ...]] = []
        with fixture.constants(), self.assertRaises(live.C4LiveAdapterError):
            await live.execute_c4_live(
                validation_root=fixture.validation,
                state_path=fixture.state_path,
                dependencies=fixture.dependencies(events),
            )
        self.assertNotIn("runtime", [item[0] for item in events])

    async def test_progress_rejects_sensitive_payload_and_flushes_each_json_event(self) -> None:
        fixture = Fixture(self)
        events: list[tuple[object, ...]] = []
        output = FlushStream()

        async def emit(**kwargs: object) -> dict[str, object]:
            sink = kwargs["progress_sink"]
            sink({"event": "block_start", "block_index": 0})
            sink({"event": "bad", "secret": "api-key=must-not-escape"})
            sink({"event": "terminal_success", "completed_episode_count": 490})
            return {"status": "complete"}

        with fixture.constants():
            await live.execute_c4_live(
                validation_root=fixture.validation,
                state_path=fixture.state_path,
                dependencies=fixture.dependencies(events, run_c4=emit),
                progress_stream=output,
            )
        lines = output.getvalue().splitlines()
        self.assertEqual(len(lines), 2)
        self.assertEqual(output.flush_count, 2)
        self.assertNotIn("api-key", output.getvalue().casefold())
        self.assertNotIn("episode body", output.getvalue().casefold())

    async def test_paths_outside_exact_repository_locations_fail_before_gate(self) -> None:
        fixture = Fixture(self)
        for validation, state in (
            (fixture.repo, fixture.state_path),
            (fixture.validation, fixture.validation / "other-state.json"),
        ):
            with self.subTest(validation=validation, state=state):
                events: list[tuple[object, ...]] = []
                with fixture.constants(), self.assertRaises(live.C4LiveAdapterError):
                    await live.execute_c4_live(
                        validation_root=validation,
                        state_path=state,
                        dependencies=fixture.dependencies(events),
                    )
                self.assertEqual(events, [])


class NativeCharacterizationC4LiveCliTests(TestCase):
    def test_cli_exposes_only_validation_and_state_paths(self) -> None:
        parser = live.build_parser()
        option_strings = {
            option
            for action in parser._actions
            for option in action.option_strings
        }
        self.assertEqual(
            option_strings,
            {
                "-h",
                "--help",
                "--validation-root",
                "--state",
                "--resume-run-id",
                "--recover-terminal-failure",
            },
        )
        for forbidden in (
            "--model",
            "--schedule",
            "--namespace",
            "--history",
            "--url",
            "--service-admin",
        ):
            with self.subTest(forbidden=forbidden), self.assertRaises(SystemExit):
                parser.parse_args([forbidden, "value"])

    def test_cli_defaults_are_exact_repository_paths(self) -> None:
        args = live.build_parser().parse_args([])
        self.assertEqual(args.validation_root, live.VALIDATION_ROOT)
        self.assertEqual(args.state, live.DEFAULT_STATE_PATH)


if __name__ == "__main__":
    import unittest

    unittest.main()
