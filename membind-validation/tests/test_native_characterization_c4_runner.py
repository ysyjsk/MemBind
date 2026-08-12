"""Focused fake-boundary tests for the thin C4/E3 live runner."""

from __future__ import annotations

import asyncio
import hashlib
import json
import sys
import tempfile
import threading
from pathlib import Path
from unittest import IsolatedAsyncioTestCase


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

import current_state_gate as state_gate  # noqa: E402
import native_characterization_c4 as c4  # noqa: E402
import native_characterization_c4_artifacts as c4a  # noqa: E402
import native_characterization_c4_runner as runner  # noqa: E402


RUN_ID = "c4-0123456789abcdef"


def schedule(*, block_count: int = 10, episode_count: int = 49) -> dict[str, object]:
    methods = [c4.NATIVE_SYNC] * 5 + [c4.NATIVE_ASYNC_SERIAL] * 5
    loads = [0.5, 0.8, 1.0, 1.2, 1.5] * 2
    blocks = []
    for index, (method, load) in enumerate(zip(methods[:block_count], loads[:block_count])):
        interval = 100 - index
        blocks.append(
            {
                "block_index": index,
                "method": method,
                "normalized_offered_load": load,
                "graph_namespace": f"nc-e3-{index:016x}",
                "interarrival_ns": interval,
                "absolute_arrival_offsets_ns": [
                    source_sequence * interval for source_sequence in range(episode_count)
                ],
            }
        )
    return c4a.seal_payload(
        {
            "schema_version": c4a.SCHEDULE_SCHEMA,
            "status": "dry_run",
            "stage": "C4/E3_OFFLINE_SCHEDULE",
            "run_id": "c2-17cdaabd562e9673",
            "history_id": "07741c45",
            "episode_ids": [
                f"07741c45:{source_sequence}"
                for source_sequence in range(episode_count)
            ],
            "block_schedules": blocks,
        }
    )


def episodes() -> list[c4.Episode]:
    return [
        c4.Episode(source_sequence=index, payload={"synthetic": index})
        for index in range(49)
    ]


def source_hashes() -> list[str]:
    return [hashlib.sha256(f"episode-{index}".encode("ascii")).hexdigest() for index in range(49)]


def provenance() -> dict[str, str]:
    return {
        name: hashlib.sha256(name.encode("ascii")).hexdigest()
        for name in c4a.REQUIRED_PROVENANCE_HASHES
    }


class FastForwardClock:
    def __init__(self) -> None:
        self.current_ns = 1_000

    def now_ns(self) -> int:
        return self.current_ns

    async def sleep_until_ns(self, timestamp_ns: int) -> None:
        if timestamp_ns < self.current_ns:
            raise AssertionError("clock moved backwards")
        self.current_ns = timestamp_ns


class YieldingFastForwardClock(FastForwardClock):
    def __init__(self) -> None:
        super().__init__()
        self.sleep_calls: list[int] = []

    async def sleep_until_ns(self, timestamp_ns: int) -> None:
        if timestamp_ns < self.current_ns:
            raise AssertionError("clock moved backwards")
        self.current_ns = timestamp_ns
        self.sleep_calls.append(timestamp_ns)
        await asyncio.sleep(0)


class AdvancingReadClock(FastForwardClock):
    """Models the nanoseconds elapsed between runner and replay clock reads."""

    def now_ns(self) -> int:
        self.current_ns += 1
        return self.current_ns


class FakeStore:
    def __init__(
        self,
        calls: list[tuple[object, ...]],
        *,
        finalize_error: BaseException | None = None,
    ) -> None:
        self.calls = calls
        self.enqueues: list[dict[str, object]] = []
        self.publications: list[dict[str, object]] = []
        self.episode_checkpoints: list[dict[str, object]] = []
        self.block_checkpoints: list[dict[str, object]] = []
        self.root_checkpoints: list[dict[str, object]] = []
        self.failures: list[dict[str, object]] = []
        self.finalizations: list[dict[str, object]] = []
        self.stage_failures: list[dict[str, object]] = []
        self.finalize_error = finalize_error

    def append_enqueue_event(self, value: dict[str, object]) -> dict[str, object]:
        self.enqueues.append(dict(value))
        return {**value, "payload_sha256": "e" * 64}

    def append_publication_event(self, value: dict[str, object]) -> dict[str, object]:
        self.publications.append(dict(value))
        return {**value, "payload_sha256": "p" * 64}

    def write_episode_checkpoint(self, **value: object) -> None:
        self.episode_checkpoints.append(dict(value))

    def write_block_checkpoint(self, **value: object) -> None:
        self.block_checkpoints.append(dict(value))

    def write_root_checkpoint(self, **value: object) -> None:
        self.root_checkpoints.append(dict(value))

    def record_failure(self, **value: object) -> None:
        retained = dict(value)
        error = retained.pop("error")
        retained["error_class"] = f"{type(error).__module__}.{type(error).__qualname__}" if isinstance(error, BaseException) else error
        self.failures.append(retained)

    def finalize_success(self, block_results: object) -> None:
        self.finalizations.append({"block_results": block_results})
        if self.finalize_error is not None:
            raise self.finalize_error

    def record_stage_failure(self, **value: object) -> None:
        retained = dict(value)
        error = retained.pop("error")
        retained["error_class"] = f"{type(error).__module__}.{type(error).__qualname__}" if isinstance(error, BaseException) else error
        self.stage_failures.append(retained)


class OneShotBlockingStore(FakeStore):
    """Blocks the first publication on a worker thread until the test releases it."""

    def __init__(self, calls: list[tuple[object, ...]]) -> None:
        super().__init__(calls)
        self.entered = threading.Event()
        self.release = threading.Event()
        self.timed_out = False

    def append_publication_event(self, value: dict[str, object]) -> dict[str, object]:
        if not self.entered.is_set():
            self.entered.set()
            if not self.release.wait(timeout=0.2):
                self.timed_out = True
        return super().append_publication_event(value)


class FakeRuntime:
    def __init__(
        self,
        block_index: int,
        namespace: str,
        calls: list[tuple[object, ...]],
        *,
        counts: runner.NamespaceCounts = runner.NamespaceCounts(0, 0),
        fail_at: tuple[int, int] | None = None,
    ) -> None:
        self.block_index = block_index
        self.namespace = namespace
        self.calls = calls
        self.counts = counts
        self.fail_at = fail_at
        self.service_calls: list[int] = []
        self.clear_calls = 0
        self.closed = False

    async def namespace_counts(self) -> runner.NamespaceCounts:
        self.calls.append(("preflight", self.block_index, self.namespace))
        return self.counts

    async def clear_namespace(self) -> None:
        self.clear_calls += 1
        self.calls.append(("clear", self.block_index, self.namespace))
        self.counts = runner.NamespaceCounts(0, 0)

    async def service(self, episode: c4.Episode, service_start_ns: int) -> None:
        self.service_calls.append(episode.source_sequence)
        if self.fail_at == (self.block_index, episode.source_sequence):
            error = RuntimeError("secret remote response must never be persisted")
            error.token_envelope = {  # type: ignore[attr-defined]
                "prompt_tokens": 25_001,
                "output_tokens": 7,
                "requested_max_tokens": 16_384,
            }
            raise error

    async def close(self) -> None:
        self.closed = True
        self.calls.append(("close", self.block_index, self.namespace))


class NativeCharacterizationC4RunnerTests(IsolatedAsyncioTestCase):
    maxDiff = None

    def fixture(
        self,
        *,
        bad_counts_at: int | None = None,
        fail_at: tuple[int, int] | None = None,
    ) -> tuple[
        dict[str, object],
        FakeStore,
        list[FakeRuntime],
        list[tuple[object, ...]],
    ]:
        calls: list[tuple[object, ...]] = []
        store = FakeStore(calls)
        runtimes: list[FakeRuntime] = []
        progress: list[dict[str, object]] = []

        def gate(action: object, *, state_path: object) -> state_gate.GateDecision:
            calls.append(("gate", action, state_path))
            return state_gate.GateDecision(True, "authorized", str(action.value))

        def store_factory(*args: object, **kwargs: object) -> FakeStore:
            calls.append(("manifest", kwargs["run_id"]))
            return store

        async def runtime_factory(block: runner.C4Block) -> FakeRuntime:
            calls.append(("runtime", block.block_index, block.graph_namespace))
            counts = (
                runner.NamespaceCounts(1, 0)
                if block.block_index == bad_counts_at
                else runner.NamespaceCounts(0, 0)
            )
            runtime = FakeRuntime(
                block.block_index,
                block.graph_namespace,
                calls,
                counts=counts,
                fail_at=fail_at,
            )
            runtimes.append(runtime)
            return runtime

        kwargs: dict[str, object] = {
            "runs_root": Path("unused-by-fake-store"),
            "schedule": schedule(),
            "provenance_hashes": provenance(),
            "episodes": episodes(),
            "episode_source_hashes": source_hashes(),
            "clock": FastForwardClock(),
            "runtime_factory": runtime_factory,
            "state_path": Path("CURRENT_STATE.fake.json"),
            "creation_command": ["c4-runner", "--live"],
            "gate_checker": gate,
            "store_factory": store_factory,
            "run_id_factory": lambda: RUN_ID,
            "progress_sink": lambda event: progress.append(dict(event)),
        }
        kwargs["progress_events"] = progress
        return kwargs, store, runtimes, calls

    async def test_exact_grid_new_lifecycle_empty_preflight_and_durable_progress(self) -> None:
        kwargs, store, runtimes, calls = self.fixture()
        progress = kwargs.pop("progress_events")
        result = await runner.run_c4_live(**kwargs)

        self.assertEqual(result["status"], "complete", result)
        self.assertEqual(result["run_id"], RUN_ID)
        self.assertEqual(result["completed_block_count"], 10)
        self.assertEqual(result["completed_episode_count"], 490)
        self.assertEqual(calls[0][0], "gate")
        self.assertIs(calls[0][1], state_gate.LiveAction.NATIVE_CHARACTERIZATION_C4)
        self.assertEqual(calls[1], ("manifest", RUN_ID))
        self.assertEqual(len(runtimes), 10)
        self.assertEqual(len({id(runtime) for runtime in runtimes}), 10)
        self.assertEqual(
            [runtime.namespace for runtime in runtimes],
            [f"nc-e3-{index:016x}" for index in range(10)],
        )
        self.assertTrue(all(runtime.closed for runtime in runtimes))
        self.assertTrue(all(runtime.service_calls == list(range(49)) for runtime in runtimes))
        self.assertEqual(len(store.enqueues), 5 * 49)
        self.assertEqual(len(store.publications), 10 * 49)
        first_block = [
            item for item in store.publications if item["block_index"] == 0
        ]
        self.assertEqual(
            [item["planned_arrival_timestamp_ns"] for item in first_block],
            [
                1_000 + runner.START_LEAD_NS + source_sequence * 100
                for source_sequence in range(49)
            ],
        )
        self.assertEqual(len(store.episode_checkpoints), 10 * 49)
        self.assertEqual(len(store.block_checkpoints), 10)
        self.assertEqual(len(store.root_checkpoints), 10)
        self.assertEqual(
            store.root_checkpoints[-1]["progress"]["completed_episode_count"], 10 * 49
        )
        self.assertEqual(len(store.finalizations), 1)
        finalized = store.finalizations[0]["block_results"]
        self.assertEqual(len(finalized), 10)
        self.assertTrue(
            all(
                set(item)
                == {
                    "block_index",
                    "graph_namespace",
                    "history_id",
                    "method",
                    "normalized_offered_load",
                }
                for item in finalized
            )
        )
        event_names = [item["event"] for item in progress]
        self.assertEqual(event_names[0], "manifest_planned")
        self.assertEqual(event_names[-1], "terminal_success")
        self.assertEqual(event_names.count("block_start"), 10)
        self.assertEqual(event_names.count("namespace_preflight"), 10)
        self.assertEqual(event_names.count("episode_published"), 490)
        self.assertEqual(event_names.count("block_complete"), 10)
        self.assertNotIn("payload", repr(progress).casefold())

    async def test_nonempty_namespace_is_checkpointed_and_stops_before_service(self) -> None:
        kwargs, store, runtimes, _ = self.fixture(bad_counts_at=2)
        progress = kwargs.pop("progress_events")
        result = await runner.run_c4_live(**kwargs)

        self.assertEqual(result["status"], c4a.FAILURE_STATUS)
        self.assertEqual(len(runtimes), 3)
        self.assertEqual(runtimes[2].service_calls, [])
        self.assertTrue(runtimes[2].closed)
        self.assertEqual(len(store.failures), 1)
        failure = store.failures[0]
        self.assertEqual(failure["block_index"], 2)
        self.assertEqual(failure["source_sequence"], 0)
        self.assertEqual(failure["completed_block_indices"], [0, 1])
        self.assertEqual(failure["completed_episode_count"], 98)
        self.assertEqual(failure["error_class"], f"{runner.NamespacePreflightError.__module__}.{runner.NamespacePreflightError.__qualname__}")
        self.assertEqual(progress[-1]["event"], "terminal_failure")

    async def test_service_failure_is_sanitized_checkpointed_and_no_later_block_starts(self) -> None:
        kwargs, store, runtimes, _ = self.fixture(fail_at=(2, 3))
        progress = kwargs.pop("progress_events")
        result = await runner.run_c4_live(**kwargs)

        self.assertEqual(result["status"], c4a.FAILURE_STATUS)
        self.assertEqual(len(runtimes), 3)
        self.assertEqual(runtimes[2].service_calls, [0, 1, 2, 3])
        self.assertTrue(runtimes[2].closed)
        self.assertEqual(len(store.failures), 1)
        failure = store.failures[0]
        self.assertEqual(failure["block_index"], 2)
        self.assertEqual(failure["source_sequence"], 3)
        self.assertEqual(failure["completed_source_sequences"], [0, 1, 2])
        self.assertEqual(failure["completed_block_indices"], [0, 1])
        self.assertEqual(failure["completed_episode_count"], 101)
        self.assertEqual(
            failure["token_envelope"],
            {
                "prompt_tokens": 25_001,
                "output_tokens": 7,
                "requested_max_tokens": 16_384,
            },
        )
        self.assertNotIn("secret remote response", repr(store.failures))
        self.assertNotIn("secret remote response", repr(progress))

    async def test_gate_denial_precedes_manifest_and_every_live_factory(self) -> None:
        kwargs, store, runtimes, calls = self.fixture()
        kwargs.pop("progress_events")

        def deny(action: object, *, state_path: object) -> state_gate.GateDecision:
            calls.append(("gate-denied", action, state_path))
            raise state_gate.LiveActionDenied("action_not_authorized", action=str(action))

        kwargs["gate_checker"] = deny
        with self.assertRaises(state_gate.LiveActionDenied):
            await runner.run_c4_live(**kwargs)

        self.assertEqual([item[0] for item in calls], ["gate-denied"])
        self.assertEqual(store.publications, [])
        self.assertEqual(runtimes, [])

    async def test_wrong_grid_and_non_c4_run_id_fail_before_runtime(self) -> None:
        for invalid_schedule in (schedule(block_count=9), schedule(episode_count=48)):
            with self.subTest(kind="schedule"):
                kwargs, _, runtimes, calls = self.fixture()
                kwargs.pop("progress_events")
                kwargs["schedule"] = invalid_schedule
                with self.assertRaises(runner.NativeCharacterizationC4RunnerError):
                    await runner.run_c4_live(**kwargs)
                self.assertEqual(calls, [])
                self.assertEqual(runtimes, [])

        kwargs, _, runtimes, calls = self.fixture()
        kwargs.pop("progress_events")
        kwargs["run_id_factory"] = lambda: "c2-0123456789abcdef"
        with self.assertRaises(runner.NativeCharacterizationC4RunnerError):
            await runner.run_c4_live(**kwargs)
        self.assertEqual([item[0] for item in calls], ["gate"])
        self.assertEqual(runtimes, [])

    async def test_blocking_artifact_io_does_not_block_arrival_producer(self) -> None:
        kwargs, _, _, calls = self.fixture()
        kwargs.pop("progress_events")
        store = OneShotBlockingStore(calls)
        clock = YieldingFastForwardClock()
        kwargs["clock"] = clock
        kwargs["store_factory"] = lambda *args, **values: store
        replay = asyncio.create_task(runner.run_c4_live(**kwargs))

        for _ in range(1_000):
            if store.entered.is_set():
                break
            await asyncio.sleep(0)
        self.assertTrue(store.entered.is_set())
        # The first publication is still blocked, but the independent producer
        # must already have reached subsequent absolute arrivals.
        for _ in range(100):
            if len(clock.sleep_calls) > 1:
                break
            await asyncio.sleep(0)
        self.assertFalse(store.timed_out)
        self.assertGreater(len(clock.sleep_calls), 1)
        store.release.set()
        result = await replay
        self.assertEqual(result["status"], "complete")

    async def test_start_lead_survives_advancing_monotonic_clock_reads(self) -> None:
        kwargs, _, _, _ = self.fixture()
        kwargs.pop("progress_events")
        kwargs["clock"] = AdvancingReadClock()
        kwargs["start_lead_ns"] = 10
        result = await runner.run_c4_live(**kwargs)
        self.assertEqual(result["status"], "complete")

    async def test_real_artifact_store_finalizes_and_verifies_exact_490_prefix(self) -> None:
        kwargs, _, _, _ = self.fixture()
        kwargs.pop("progress_events")
        kwargs.pop("store_factory")
        kwargs.pop("progress_sink")
        with tempfile.TemporaryDirectory() as temporary:
            runs_root = Path(temporary) / "runs"
            kwargs["runs_root"] = runs_root
            kwargs["post_finalize_verifier"] = lambda store: c4a.verify_c4_artifacts(
                store.run_root
            )
            result = await runner.run_c4_live(**kwargs)
            verification = await asyncio.to_thread(
                c4a.verify_c4_artifacts, runs_root / RUN_ID
            )

        self.assertEqual(result["status"], "complete")
        self.assertEqual(verification["attempt_status"], "complete")
        self.assertEqual(verification["event_counts"]["publication"], 490)
        self.assertEqual(verification["event_counts"]["enqueue"], 245)
        self.assertEqual(verification["event_counts"]["failure"], 0)
        self.assertRegex(verification["success_summary_sha256"], r"^[0-9a-f]{64}$")
        self.assertIn("e3_sync_async.json", verification["hash_inventory"])

    async def test_finalization_failure_is_root_checkpointed_as_stage_failure(self) -> None:
        kwargs, _, _, calls = self.fixture()
        progress = kwargs.pop("progress_events")
        store = FakeStore(calls, finalize_error=RuntimeError("never persist this detail"))
        kwargs["store_factory"] = lambda *args, **values: store
        result = await runner.run_c4_live(**kwargs)

        self.assertEqual(result["status"], c4a.FAILURE_STATUS)
        self.assertEqual(result["failure_stage"], "finalization")
        self.assertEqual(result["completed_block_count"], 10)
        self.assertEqual(result["completed_episode_count"], 490)
        self.assertEqual(len(store.root_checkpoints), 10)
        self.assertEqual(len(store.stage_failures), 1)
        self.assertEqual(
            store.stage_failures[0],
            {
                "failure_stage": "finalization",
                "completed_block_indices": list(range(10)),
                "completed_episode_count": 490,
                "token_envelope": {
                    "prompt_tokens": None,
                    "output_tokens": None,
                    "requested_max_tokens": None,
                },
                "error_class": "builtins.RuntimeError",
            },
        )
        self.assertEqual(progress[-1]["event"], "terminal_failure")
        self.assertEqual(progress[-1]["failure_stage"], "finalization")
        self.assertNotIn("never persist this detail", repr(store.stage_failures))

    async def test_post_finalize_verifier_failure_uses_stage_failure_api(self) -> None:
        kwargs, store, _, _ = self.fixture()
        progress = kwargs.pop("progress_events")
        kwargs["post_finalize_verifier"] = lambda _store: {
            "status": "verified",
            "attempt_status": "running",
        }
        result = await runner.run_c4_live(**kwargs)

        self.assertEqual(result["status"], c4a.FAILURE_STATUS)
        self.assertEqual(result["failure_stage"], "verification")
        self.assertEqual(result["completed_block_count"], 10)
        self.assertEqual(result["completed_episode_count"], 490)
        self.assertEqual(len(store.finalizations), 1)
        self.assertEqual(len(store.stage_failures), 1)
        self.assertEqual(store.stage_failures[0]["failure_stage"], "verification")
        self.assertEqual(store.stage_failures[0]["completed_block_indices"], list(range(10)))
        self.assertEqual(store.stage_failures[0]["completed_episode_count"], 490)
        self.assertEqual(progress[-1]["event"], "terminal_failure")
        self.assertEqual(progress[-1]["failure_stage"], "verification")

    async def test_resume_run_id_appends_to_same_run_and_skips_completed_prefix(self) -> None:
        kwargs, store, runtimes, calls = self.fixture()
        progress = kwargs.pop("progress_events")
        with tempfile.TemporaryDirectory() as temporary:
            runs_root = Path(temporary) / "runs"
            prefix = FakeStore(calls)
            kwargs["runs_root"] = runs_root
            kwargs["resume_run_id"] = RUN_ID
            kwargs["store_factory"] = lambda *args, **values: self.fail("fresh store must not be created")

            def resume_store_factory(**values: object) -> FakeStore:
                self.assertEqual(values["runs_root"], runs_root)
                self.assertEqual(values["run_id"], RUN_ID)
                calls.append(("resume-store", values["run_id"]))
                return prefix

            kwargs["resume_store_factory"] = resume_store_factory
            kwargs["resume_prefix_loader"] = lambda **values: runner.C4ResumePrefix(
                run_id=RUN_ID,
                completed_block_indices=(0, 1, 2),
                next_block_index=3,
                completed_episode_count=147,
            )

            async def runtime_factory(block: runner.C4Block) -> FakeRuntime:
                calls.append(("runtime", block.block_index, block.graph_namespace))
                runtime = FakeRuntime(
                    block.block_index,
                    block.graph_namespace,
                    calls,
                    counts=(
                        runner.NamespaceCounts(5, 7)
                        if block.block_index == 3
                        else runner.NamespaceCounts(0, 0)
                    ),
                )
                runtimes.append(runtime)
                return runtime

            kwargs["runtime_factory"] = runtime_factory
            result = await runner.run_c4_live(**kwargs)

        self.assertEqual(result["status"], "complete")
        self.assertEqual(result["run_id"], RUN_ID)
        self.assertEqual(result["resumed"], True)
        self.assertEqual(result["completed_block_count"], 10)
        self.assertEqual(result["completed_episode_count"], 490)
        self.assertEqual([runtime.block_index for runtime in runtimes], list(range(3, 10)))
        self.assertEqual(runtimes[0].clear_calls, 1)
        self.assertEqual([runtime.clear_calls for runtime in runtimes[1:]], [0] * 6)
        self.assertLess(
            calls.index(("clear", 3, "nc-e3-0000000000000003")),
            calls.index(("preflight", 3, "nc-e3-0000000000000003")),
        )
        self.assertEqual(runtimes[0].service_calls, list(range(49)))
        self.assertEqual([runtime.service_calls for runtime in runtimes[1:]], [list(range(49))] * 6)
        self.assertEqual(len(prefix.publications), 7 * 49)
        self.assertEqual(prefix.publications[0]["block_index"], 3)
        self.assertEqual(prefix.publications[0]["source_sequence"], 0)
        self.assertEqual(prefix.root_checkpoints[0]["progress"]["completed_block_indices"], [0, 1, 2])
        self.assertEqual(prefix.root_checkpoints[0]["progress"]["completed_episode_count"], 147)
        self.assertEqual(prefix.root_checkpoints[-1]["progress"]["completed_episode_count"], 490)
        self.assertEqual(calls[1], ("resume-store", RUN_ID))
        self.assertEqual(progress[0]["event"], "resume_prefix_verified")
        self.assertEqual(progress[0]["completed_block_indices"], [0, 1, 2])
        self.assertEqual(progress[1]["event"], "manifest_planned")

    async def test_recover_terminal_failure_uses_recovery_loader_before_resume(self) -> None:
        kwargs, _store, runtimes, calls = self.fixture()
        progress = kwargs.pop("progress_events")
        with tempfile.TemporaryDirectory() as temporary:
            runs_root = Path(temporary) / "runs"
            prefix = FakeStore(calls)
            kwargs["runs_root"] = runs_root
            kwargs["resume_run_id"] = RUN_ID
            kwargs["recover_terminal_failure"] = True
            kwargs["store_factory"] = lambda *args, **values: self.fail("fresh store must not be created")
            kwargs["resume_prefix_loader"] = lambda **values: self.fail("running-prefix loader must not be used")
            kwargs["resume_store_factory"] = lambda **values: prefix

            def terminal_recovery_loader(**values: object) -> runner.C4ResumePrefix:
                self.assertEqual(values["runs_root"], runs_root)
                self.assertEqual(values["run_id"], RUN_ID)
                calls.append(("terminal-recovery", values["run_id"]))
                return runner.C4ResumePrefix(
                    run_id=RUN_ID,
                    completed_block_indices=(0, 1, 2),
                    next_block_index=3,
                    completed_episode_count=147,
                )

            kwargs["terminal_recovery_loader"] = terminal_recovery_loader
            result = await runner.run_c4_live(**kwargs)

        self.assertEqual(result["status"], "complete")
        self.assertEqual(result["resumed"], True)
        self.assertEqual(result["recovered_terminal_failure"], True)
        self.assertEqual([runtime.block_index for runtime in runtimes], list(range(3, 10)))
        self.assertEqual(calls[1], ("terminal-recovery", RUN_ID))
        self.assertEqual(progress[0]["event"], "resume_prefix_verified")
        self.assertEqual(progress[0]["recovered_terminal_failure"], True)

    async def test_resume_run_id_rejects_failed_complete_or_mismatched_prefix_before_runtime(self) -> None:
        for kind in ("failed", "complete", "schedule"):
            with self.subTest(kind=kind), tempfile.TemporaryDirectory() as temporary:
                kwargs, _store, runtimes, calls = self.fixture()
                kwargs.pop("progress_events")
                runs_root = Path(temporary) / "runs"
                prefix = c4a.C4ArtifactStore.create(
                    runs_root,
                    RUN_ID,
                    schedule(),
                    provenance(),
                    ["c4-runner", "--live"],
                )
                if kind == "failed":
                    prefix.record_failure(
                        block_index=0,
                        source_sequence=0,
                        error=RuntimeError("ignored"),
                        completed_source_sequences=[],
                        completed_block_indices=[],
                        completed_episode_count=0,
                    )
                elif kind == "complete":
                    prefix.write_root_checkpoint(
                        status="completed",
                        progress={
                            "completed_block_indices": list(range(10)),
                            "completed_episode_count": 490,
                        },
                    )
                else:
                    manifest_path = prefix.manifest_path
                    manifest = json.loads(manifest_path.read_text("ascii"))
                    manifest["schedule_payload_sha256"] = "f" * 64
                    manifest = c4a.seal_payload(manifest)
                    manifest_path.write_bytes(c4a.canonical_json_bytes(manifest) + b"\n")
                    prefix.write_root_checkpoint(
                        status="running",
                        progress={
                            "completed_block_indices": [0],
                            "completed_episode_count": 49,
                        },
                    )
                kwargs["runs_root"] = runs_root
                kwargs["resume_run_id"] = RUN_ID
                with self.assertRaises(runner.NativeCharacterizationC4RunnerError):
                    await runner.run_c4_live(**kwargs)
                self.assertEqual(runtimes, [])
                self.assertEqual([item[0] for item in calls], ["gate"])


if __name__ == "__main__":
    import unittest

    unittest.main()
