"""Offline contracts for Native Graphiti characterization instrumentation."""

from __future__ import annotations

import asyncio
import json
import sys
import tempfile
from concurrent.futures import ThreadPoolExecutor
from contextlib import asynccontextmanager
from pathlib import Path
from types import SimpleNamespace
from unittest import IsolatedAsyncioTestCase, TestCase
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from native_characterization_instrumentation import (  # noqa: E402
    install_graphiti_phase_instrumentation,
    install_native_characterization_instrumentation,
    instrument_driver,
    instrument_embedding_client,
    instrument_llm_client,
    patch_phase_alias,
)
from native_characterization_tracing import (  # noqa: E402
    DurableJsonlEnvelopeWriter,
    SpanRecord,
    TraceRecorder,
    critical_path_ns,
    exclusive_duration_ns,
    interval_union_ns,
)


class _Clock:
    def __init__(self, *values: int) -> None:
        self._values = iter(values)

    def __call__(self) -> int:
        return next(self._values)


def _span(
    span_id: str,
    parent_span_id: str | None,
    start_ns: int,
    end_ns: int,
) -> SpanRecord:
    return SpanRecord(
        sequence=int(span_id.removeprefix("s")),
        span_id=span_id,
        parent_span_id=parent_span_id,
        run_id="run",
        episode_id="episode",
        source_sequence=0,
        phase="phase",
        operation_class=None,
        start_ns=start_ns,
        end_ns=end_ns,
        status="ok",
        error_code=None,
        metadata={},
    )


class TraceRecorderTests(TestCase):
    def test_nested_spans_have_parents_and_restore_context(self) -> None:
        recorder = TraceRecorder(clock=_Clock(0, 2, 6, 10))

        with recorder.episode_scope("run", "episode", 7):
            with recorder.span("outer"):
                with recorder.span("inner"):
                    pass
            self.assertIsNone(recorder.current_span_id())

        records = sorted(recorder.records, key=lambda item: item.start_ns)
        self.assertEqual([item.phase for item in records], ["outer", "inner"])
        self.assertIsNone(records[0].parent_span_id)
        self.assertEqual(records[1].parent_span_id, records[0].span_id)
        self.assertEqual((records[0].start_ns, records[0].end_ns), (0, 10))
        self.assertEqual((records[1].start_ns, records[1].end_ns), (2, 6))
        self.assertIsNone(recorder.current_episode())

    def test_nested_episode_scope_restores_outer_context(self) -> None:
        recorder = TraceRecorder()

        with recorder.episode_scope("outer", "e0", 0):
            with recorder.episode_scope("inner", "e1", 1):
                self.assertEqual(recorder.current_episode().run_id, "inner")
            self.assertEqual(recorder.current_episode().run_id, "outer")

        self.assertIsNone(recorder.current_episode())

    def test_exception_closes_span_and_reraises_same_object_without_message(self) -> None:
        recorder = TraceRecorder(clock=_Clock(10, 20))
        failure = RuntimeError("PRIVATE-EXCEPTION-BODY")

        with self.assertRaises(RuntimeError) as raised:
            with recorder.episode_scope("run", "episode", 0):
                with recorder.span("failing"):
                    raise failure

        self.assertIs(raised.exception, failure)
        record = recorder.records[0]
        self.assertEqual(record.status, "error")
        self.assertEqual(record.error_code, "builtins.RuntimeError")
        self.assertNotIn("PRIVATE-EXCEPTION-BODY", json.dumps(record.to_dict()))
        self.assertIsNone(recorder.current_span_id())

    def test_metadata_rejects_content_bearing_fields(self) -> None:
        recorder = TraceRecorder()

        with recorder.episode_scope("run", "episode", 0):
            with recorder.span("phase") as span:
                with self.assertRaisesRegex(ValueError, "content-bearing"):
                    span.add_metadata("raw_response", "private")


class ConcurrentContextTests(IsolatedAsyncioTestCase):
    async def test_interleaved_episode_tasks_never_cross_attribute_spans(self) -> None:
        recorder = TraceRecorder()
        a_entered = asyncio.Event()
        b_entered = asyncio.Event()

        async def task_a() -> None:
            with recorder.episode_scope("run-a", "episode-a", 1):
                with recorder.span("a-outer"):
                    a_entered.set()
                    await b_entered.wait()
                    with recorder.span("a-inner"):
                        await asyncio.sleep(0)

        async def task_b() -> None:
            await a_entered.wait()
            with recorder.episode_scope("run-b", "episode-b", 2):
                with recorder.span("b-outer"):
                    b_entered.set()
                    await asyncio.sleep(0)

        await asyncio.gather(task_a(), task_b())

        by_phase = {record.phase: record for record in recorder.records}
        self.assertEqual(by_phase["a-inner"].run_id, "run-a")
        self.assertEqual(by_phase["a-inner"].parent_span_id, by_phase["a-outer"].span_id)
        self.assertEqual(by_phase["b-outer"].run_id, "run-b")
        self.assertIsNone(by_phase["b-outer"].parent_span_id)
        self.assertIsNone(recorder.current_episode())

    async def test_child_task_inherits_episode_and_parent_span(self) -> None:
        recorder = TraceRecorder()

        async def child() -> None:
            with recorder.span("child"):
                await asyncio.sleep(0)

        with recorder.episode_scope("run", "episode", 3):
            with recorder.span("parent"):
                await asyncio.create_task(child())

        records = {record.phase: record for record in recorder.records}
        self.assertEqual(records["child"].parent_span_id, records["parent"].span_id)
        self.assertEqual(records["child"].source_sequence, 3)


class IntervalAccountingTests(TestCase):
    def test_interval_union_handles_nested_overlap_adjacent_and_disjoint(self) -> None:
        self.assertEqual(
            interval_union_ns([(0, 10), (2, 6), (8, 12), (12, 12), (15, 20)]),
            17,
        )

    def test_interval_union_rejects_negative_intervals(self) -> None:
        with self.assertRaisesRegex(ValueError, "end before start"):
            interval_union_ns([(5, 4)])

    def test_exclusive_duration_clips_and_unions_children(self) -> None:
        self.assertEqual(
            exclusive_duration_ns((0, 20), [(-5, 4), (2, 10), (5, 12), (14, 18), (19, 25)]),
            3,
        )

    def test_critical_path_distinguishes_sequential_and_fork_join_children(self) -> None:
        sequential = [
            _span("s0", None, 0, 20),
            _span("s1", "s0", 2, 6),
            _span("s2", "s0", 8, 12),
        ]
        fork_join = [
            _span("s0", None, 0, 20),
            _span("s1", "s0", 2, 10),
            _span("s2", "s0", 5, 12),
        ]

        self.assertEqual(critical_path_ns("s0", sequential), 20)
        self.assertEqual(critical_path_ns("s0", fork_join), 18)

    def test_critical_path_accepts_siblings_with_identical_intervals(self) -> None:
        records = [
            _span("s0", None, 0, 20),
            _span("s1", "s0", 2, 10),
            _span("s2", "s0", 2, 10),
        ]

        self.assertEqual(critical_path_ns("s0", records), 20)


class DurableWriterTests(TestCase):
    def test_writer_rejects_content_bearing_payloads(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            writer = DurableJsonlEnvelopeWriter(Path(temp_dir) / "trace.jsonl")
            with self.assertRaisesRegex(ValueError, "content-bearing"):
                writer.write({"raw_response": "PRIVATE"})

    def test_writer_fsyncs_before_return(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            writer = DurableJsonlEnvelopeWriter(Path(temp_dir) / "trace.jsonl")
            with patch("native_characterization_tracing.os.fsync") as fsync:
                writer.write({"schema_version": "test", "spans": []})
            fsync.assert_called_once()

    def test_concurrent_writes_produce_complete_parseable_lines(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "trace.jsonl"
            writer = DurableJsonlEnvelopeWriter(path)

            def write_one(index: int) -> None:
                writer.write(
                    {
                        "schema_version": "test",
                        "run_id": f"run-{index}",
                        "padding_length": 20_000,
                        "spans": [],
                    }
                )

            with ThreadPoolExecutor(max_workers=8) as executor:
                list(executor.map(write_one, range(24)))

            records = [json.loads(line) for line in path.read_text().splitlines()]
            self.assertEqual(len(records), 24)
            self.assertEqual({record["run_id"] for record in records}, {f"run-{i}" for i in range(24)})


class PhaseAliasTests(IsolatedAsyncioTestCase):
    async def test_phase_alias_is_transparent_reversible_and_scope_bounded(self) -> None:
        recorder = TraceRecorder()
        calls: list[tuple[tuple[object, ...], dict[str, object]]] = []
        result = object()

        async def operation(*args: object, **kwargs: object) -> object:
            calls.append((args, kwargs))
            return result

        owner = SimpleNamespace(operation=operation)
        original = owner.operation
        installed = patch_phase_alias(owner, "operation", "node-extraction", recorder)
        arg = object()
        kwarg = object()

        self.assertIs(await owner.operation(arg, named=kwarg), result)
        self.assertEqual(recorder.records, [])
        with recorder.episode_scope("run", "episode", 0):
            self.assertIs(await owner.operation(arg, named=kwarg), result)

        self.assertEqual(calls, [((arg,), {"named": kwarg}), ((arg,), {"named": kwarg})])
        self.assertEqual([record.phase for record in recorder.records], ["node-extraction"])
        installed.restore()
        self.assertIs(owner.operation, original)

    async def test_phase_alias_reraises_same_exception(self) -> None:
        recorder = TraceRecorder()
        failure = LookupError("PRIVATE")

        async def operation() -> None:
            raise failure

        owner = SimpleNamespace(operation=operation)
        installed = patch_phase_alias(owner, "operation", "edge-resolution", recorder)
        try:
            with self.assertRaises(LookupError) as raised:
                with recorder.episode_scope("run", "episode", 0):
                    await owner.operation()
        finally:
            installed.restore()

        self.assertIs(raised.exception, failure)
        self.assertEqual(recorder.records[0].status, "error")

    async def test_graphiti_phase_installation_targets_bound_aliases_and_is_idempotent(self) -> None:
        recorder = TraceRecorder()
        calls: list[str] = []

        async def alias(*args: object, **kwargs: object) -> str:
            calls.append("alias")
            return "alias-result"

        class FakeGraphiti:
            async def retrieve_episodes(self, *args: object, **kwargs: object) -> str:
                calls.append("retrieve")
                return "retrieve-result"

            async def _process_episode_data(self, *args: object, **kwargs: object) -> str:
                calls.append("publish")
                return "publish-result"

        phase_owner = SimpleNamespace(
            extract_nodes=alias,
            resolve_extracted_nodes=alias,
            extract_edges=alias,
            resolve_extracted_edges=alias,
            extract_attributes_from_nodes=alias,
        )
        graphiti = FakeGraphiti()
        originals = {
            name: getattr(phase_owner, name)
            for name in (
                "extract_nodes",
                "resolve_extracted_nodes",
                "extract_edges",
                "resolve_extracted_edges",
                "extract_attributes_from_nodes",
            )
        }
        retrieve_original = graphiti.retrieve_episodes
        publish_original = graphiti._process_episode_data

        first = install_graphiti_phase_instrumentation(
            graphiti,
            recorder,
            phase_module=phase_owner,
        )
        second = install_graphiti_phase_instrumentation(
            graphiti,
            recorder,
            phase_module=phase_owner,
        )
        self.assertIs(first, second)

        with recorder.episode_scope("run", "episode", 0):
            self.assertEqual(await phase_owner.extract_nodes(), "alias-result")
            self.assertEqual(await graphiti.retrieve_episodes(), "retrieve-result")
            self.assertEqual(await graphiti._process_episode_data(), "publish-result")

        phases = [record.phase for record in recorder.records]
        self.assertEqual(
            phases,
            ["node-extraction", "previous-context", "publication"],
        )
        first.restore()
        for name, original in originals.items():
            self.assertIs(getattr(phase_owner, name), original)
        self.assertIs(graphiti.retrieve_episodes.__func__, retrieve_original.__func__)
        self.assertIs(
            graphiti._process_episode_data.__func__, publish_original.__func__
        )


class LLMAndEmbeddingWrapperTests(IsolatedAsyncioTestCase):
    async def test_llm_wrapper_records_attempts_tokens_and_no_content(self) -> None:
        recorder = TraceRecorder()
        response = SimpleNamespace(
            usage=SimpleNamespace(prompt_tokens=11, completion_tokens=4, total_tokens=15)
        )
        transport_calls: list[dict[str, object]] = []

        async def create(**kwargs: object) -> object:
            transport_calls.append(kwargs)
            return response

        returned = object()

        class Client:
            def __init__(self) -> None:
                self.client = SimpleNamespace(
                    chat=SimpleNamespace(completions=SimpleNamespace(create=create))
                )

            async def generate_response(self, messages: object, **kwargs: object) -> object:
                await self.client.chat.completions.create(messages=messages, api_key="PRIVATE-KEY")
                await self.client.chat.completions.create(messages=messages, api_key="PRIVATE-KEY")
                return returned

        client = Client()
        installed = instrument_llm_client(client, recorder)
        messages = [{"role": "user", "content": "PRIVATE-PROMPT"}]
        try:
            with recorder.episode_scope("run", "episode", 0):
                observed = await client.generate_response(
                    messages,
                    prompt_name="extract_nodes.extract_message",
                )
        finally:
            installed.restore()

        self.assertIs(observed, returned)
        self.assertIs(transport_calls[0]["messages"], messages)
        logical = next(record for record in recorder.records if record.phase == "llm")
        attempts = [record for record in recorder.records if record.phase == "llm-transport"]
        self.assertEqual(logical.metadata["retry_count"], 1)
        self.assertEqual(logical.metadata["input_tokens"], 22)
        self.assertEqual(logical.metadata["output_tokens"], 8)
        self.assertEqual(len(attempts), 2)
        persisted = json.dumps(recorder.episode_envelope("run", "episode", 0))
        self.assertNotIn("PRIVATE-PROMPT", persisted)
        self.assertNotIn("PRIVATE-KEY", persisted)

    async def test_embedding_wrapper_preserves_results_and_records_shape(self) -> None:
        recorder = TraceRecorder()
        single = [1.0, 2.0, 3.0]
        batch = [[1.0, 2.0], [3.0, 4.0]]

        class Embedder:
            async def create(self, input_data: object) -> object:
                return single

            async def create_batch(self, input_data_list: object) -> object:
                return batch

        embedder = Embedder()
        installed = instrument_embedding_client(embedder, recorder)
        try:
            with recorder.episode_scope("run", "episode", 0):
                observed_single = await embedder.create("PRIVATE-SINGLE")
                observed_batch = await embedder.create_batch(["PRIVATE-A", "PRIVATE-B"])
        finally:
            installed.restore()

        self.assertIs(observed_single, single)
        self.assertIs(observed_batch, batch)
        records = [record for record in recorder.records if record.phase == "embedding"]
        self.assertEqual(
            [(record.metadata["text_count"], record.metadata["dimension"]) for record in records],
            [(1, 3), (2, 2)],
        )
        persisted = json.dumps(recorder.episode_envelope("run", "episode", 0))
        self.assertNotIn("PRIVATE", persisted)

    async def test_embedding_wrapper_preserves_original_keyword_names(self) -> None:
        recorder = TraceRecorder()

        class Embedder:
            async def create(self, *, input_data: object) -> list[float]:
                return [1.0]

            async def create_batch(self, *, input_data_list: object) -> list[list[float]]:
                return [[1.0], [2.0]]

        embedder = Embedder()
        installed = instrument_embedding_client(embedder, recorder)
        try:
            with recorder.episode_scope("run", "episode", 0):
                self.assertEqual(await embedder.create(input_data="PRIVATE"), [1.0])
                self.assertEqual(
                    await embedder.create_batch(input_data_list=["PRIVATE", "PRIVATE"]),
                    [[1.0], [2.0]],
                )
        finally:
            installed.restore()

        self.assertEqual(
            [record.metadata["text_count"] for record in recorder.records],
            [1, 2],
        )


class DriverWrapperTests(IsolatedAsyncioTestCase):
    async def test_execute_query_is_transparent_and_sanitized(self) -> None:
        recorder = TraceRecorder()
        result = object()
        calls: list[tuple[object, tuple[object, ...], dict[str, object]]] = []

        class Driver:
            async def execute_query(self, query: object, *args: object, **kwargs: object) -> object:
                calls.append((query, args, kwargs))
                return result

        driver = Driver()
        installed = instrument_driver(driver, recorder)
        positional = object()
        try:
            with recorder.episode_scope("run", "episode", 0):
                observed = await driver.execute_query(
                    "MATCH (n) RETURN n // PRIVATE-QUERY",
                    positional,
                    params={"private": "PRIVATE-PARAM"},
                    routing_="r",
                )
        finally:
            installed.restore()

        self.assertIs(observed, result)
        self.assertEqual(calls[0][1], (positional,))
        self.assertEqual(calls[0][2]["params"], {"private": "PRIVATE-PARAM"})
        record = next(record for record in recorder.records if record.phase == "database")
        self.assertEqual(record.operation_class, "query")
        persisted = json.dumps(recorder.episode_envelope("run", "episode", 0))
        self.assertNotIn("PRIVATE-QUERY", persisted)
        self.assertNotIn("PRIVATE-PARAM", persisted)

    async def test_execute_query_preserves_original_keyword_name(self) -> None:
        recorder = TraceRecorder()
        observed: list[tuple[tuple[object, ...], dict[str, object]]] = []

        class Driver:
            async def execute_query(self, *args: object, **kwargs: object) -> str:
                observed.append((args, kwargs))
                return "ok"

        driver = Driver()
        installed = instrument_driver(driver, recorder)
        try:
            with recorder.episode_scope("run", "episode", 0):
                result = await driver.execute_query(
                    cypher_query_="MATCH (n) RETURN n",
                    routing_="r",
                )
        finally:
            installed.restore()

        self.assertEqual(result, "ok")
        self.assertEqual(
            observed,
            [((), {"cypher_query_": "MATCH (n) RETURN n", "routing_": "r"})],
        )

    async def test_query_classifier_ignores_write_words_inside_literals_and_comments(self) -> None:
        recorder = TraceRecorder()

        class Driver:
            async def execute_query(self, *args: object, **kwargs: object) -> None:
                return None

        driver = Driver()
        installed = instrument_driver(driver, recorder)
        try:
            with recorder.episode_scope("run", "episode", 0):
                await driver.execute_query(
                    "MATCH (n) RETURN 'CREATE SET DELETE' AS text // MERGE"
                )
        finally:
            installed.restore()

        self.assertEqual(recorder.records[0].operation_class, "query")

    async def test_execute_write_records_each_transaction_internal_run_without_double_count(self) -> None:
        recorder = TraceRecorder()
        tx_results = [object() for _ in range(4)]

        class Transaction:
            def __init__(self) -> None:
                self.calls: list[tuple[object, dict[str, object]]] = []

            async def run(self, query: object, **kwargs: object) -> object:
                self.calls.append((query, kwargs))
                return tx_results[len(self.calls) - 1]

        transaction = Transaction()

        class Session:
            async def execute_write(self, callback: object, *args: object, **kwargs: object) -> object:
                return await callback(transaction, *args, **kwargs)

            async def close(self) -> None:
                return None

        class Driver:
            async def execute_query(self, query: object, **kwargs: object) -> None:
                return None

            def session(self, **kwargs: object) -> object:
                return Session()

        driver = Driver()
        installed = instrument_driver(driver, recorder)
        callback_result = object()
        marker = object()
        callback_calls: list[tuple[object, object, object]] = []

        async def callback(tx: object, observed_marker: object, *, driver_arg: object) -> object:
            callback_calls.append((tx, observed_marker, driver_arg))
            for ordinal in range(4):
                self.assertIs(
                    await tx.run(f"CREATE PRIVATE-{ordinal}", value=f"PRIVATE-{ordinal}"),
                    tx_results[ordinal],
                )
            return callback_result

        try:
            with recorder.episode_scope("run", "episode", 0):
                session = driver.session(database="neo4j")
                observed = await session.execute_write(callback, marker, driver_arg=driver)
        finally:
            installed.restore()

        self.assertIs(observed, callback_result)
        self.assertIs(callback_calls[0][1], marker)
        self.assertIs(callback_calls[0][2], driver)
        db_operations = [record for record in recorder.records if record.phase == "database"]
        self.assertEqual(len(db_operations), 4)
        self.assertTrue(all(record.operation_class == "write" for record in db_operations))
        transaction_ids = {record.metadata["transaction_id"] for record in db_operations}
        self.assertEqual(len(transaction_ids), 1)
        boundaries = [record for record in recorder.records if record.phase == "database-transaction"]
        self.assertEqual(len(boundaries), 1)
        persisted = json.dumps(recorder.episode_envelope("run", "episode", 0))
        self.assertNotIn("PRIVATE", persisted)

    async def test_transaction_failure_closes_spans_and_preserves_exception(self) -> None:
        recorder = TraceRecorder()
        failure = ArithmeticError("PRIVATE")

        class Transaction:
            async def run(self, query: object, **kwargs: object) -> None:
                raise failure

        class Session:
            async def execute_write(self, callback: object, *args: object, **kwargs: object) -> object:
                return await callback(Transaction(), *args, **kwargs)

            async def close(self) -> None:
                return None

        class Driver:
            async def execute_query(self, query: object, **kwargs: object) -> None:
                return None

            def session(self, **kwargs: object) -> object:
                return Session()

        driver = Driver()
        installed = instrument_driver(driver, recorder)

        async def callback(tx: object) -> None:
            await tx.run("CREATE PRIVATE")

        try:
            with self.assertRaises(ArithmeticError) as raised:
                with recorder.episode_scope("run", "episode", 0):
                    await driver.session().execute_write(callback)
        finally:
            installed.restore()

        self.assertIs(raised.exception, failure)
        self.assertEqual(
            {record.status for record in recorder.records},
            {"error"},
        )

    async def test_execute_write_preserves_named_callback_argument(self) -> None:
        recorder = TraceRecorder()

        class Transaction:
            async def run(self, query: object, **kwargs: object) -> None:
                return None

        class Session:
            async def execute_write(self, *, func: object, marker: object) -> object:
                return await func(Transaction(), marker)

            async def close(self) -> None:
                return None

        class Driver:
            async def execute_query(self, *args: object, **kwargs: object) -> None:
                return None

            def session(self) -> Session:
                return Session()

        marker = object()
        result = object()

        async def callback(tx: object, observed_marker: object) -> object:
            self.assertIs(observed_marker, marker)
            await tx.run("CREATE (n)")
            return result

        driver = Driver()
        installed = instrument_driver(driver, recorder)
        try:
            with recorder.episode_scope("run", "episode", 0):
                observed = await driver.session().execute_write(func=callback, marker=marker)
        finally:
            installed.restore()

        self.assertIs(observed, result)

    async def test_session_context_exits_original_manager_when_enter_returns_delegate(self) -> None:
        recorder = TraceRecorder()
        result = object()

        class Delegate:
            async def run(self, query: object, **kwargs: object) -> object:
                return result

        class SessionContext:
            def __init__(self) -> None:
                self.exited = False

            async def __aenter__(self) -> Delegate:
                return Delegate()

            async def __aexit__(self, *args: object) -> None:
                self.exited = True

        context = SessionContext()

        class Driver:
            async def execute_query(self, *args: object, **kwargs: object) -> None:
                return None

            def session(self) -> SessionContext:
                return context

        driver = Driver()
        installed = instrument_driver(driver, recorder)
        try:
            with recorder.episode_scope("run", "episode", 0):
                async with driver.session() as session:
                    self.assertIs(await session.run("MATCH (n) RETURN n"), result)
        finally:
            installed.restore()

        self.assertTrue(context.exited)

    async def test_direct_driver_transaction_yields_instrumented_tx_and_preserves_exit(self) -> None:
        recorder = TraceRecorder()
        result = object()

        class Transaction:
            async def run(self, query: object, **kwargs: object) -> object:
                return result

        class Driver:
            async def execute_query(self, query: object, **kwargs: object) -> None:
                return None

            @asynccontextmanager
            async def transaction(self):
                yield Transaction()

        driver = Driver()
        installed = instrument_driver(driver, recorder)
        try:
            with recorder.episode_scope("run", "episode", 0):
                async with driver.transaction() as tx:
                    observed = await tx.run("SET PRIVATE", value="PRIVATE")
        finally:
            installed.restore()

        self.assertIs(observed, result)
        db_operations = [record for record in recorder.records if record.phase == "database"]
        self.assertEqual(len(db_operations), 1)
        self.assertEqual(db_operations[0].operation_class, "write")

    async def test_clone_is_instrumented_and_restore_propagates(self) -> None:
        recorder = TraceRecorder()

        class Clone:
            def __init__(self) -> None:
                self.calls = 0

            async def execute_query(self, query: object, **kwargs: object) -> str:
                self.calls += 1
                return "clone-result"

        clone = Clone()

        class Driver:
            async def execute_query(self, query: object, **kwargs: object) -> None:
                return None

            def clone(self, database: str) -> object:
                return clone

        driver = Driver()
        original_clone_execute = clone.execute_query.__func__
        installed = instrument_driver(driver, recorder)
        try:
            observed_clone = driver.clone("other")
            with recorder.episode_scope("run", "episode", 0):
                self.assertEqual(await observed_clone.execute_query("MATCH PRIVATE", routing_="r"), "clone-result")
        finally:
            installed.restore()

        self.assertEqual(clone.calls, 1)
        self.assertEqual(len([record for record in recorder.records if record.phase == "database"]), 1)
        self.assertIs(clone.execute_query.__func__, original_clone_execute)


class _DeterministicParityFixture:
    def __init__(self, mode: str = "success") -> None:
        self.mode = mode
        self.events: list[tuple[object, ...]] = []
        self.graph_state: list[tuple[object, ...]] = []
        self.entered = asyncio.Event()
        self.release = asyncio.Event()
        self.failure = RuntimeError("PRIVATE-FIXTURE-FAILURE")
        self.result = object()
        self.phase_results = {
            name: object()
            for name in (
                "extract_nodes",
                "resolve_extracted_nodes",
                "extract_edges",
                "resolve_extracted_edges",
                "extract_attributes_from_nodes",
            )
        }
        self.llm_result = object()
        self.embedding_result = [1.0, 2.0]
        self.db_result = object()

        def phase(name: str):
            async def call(*args: object, **kwargs: object) -> object:
                self.events.append((name, args, kwargs))
                if name == "extract_nodes" and self.mode == "cancel":
                    self.entered.set()
                    try:
                        await self.release.wait()
                    finally:
                        self.events.append(("extract_nodes-cleanup",))
                if name == "resolve_extracted_nodes" and self.mode == "error":
                    raise self.failure
                return self.phase_results[name]

            return call

        self.phase_module = SimpleNamespace(
            **{name: phase(name) for name in self.phase_results}
        )

        async def transport_create(*args: object, **kwargs: object) -> object:
            self.events.append(("llm-transport", args, kwargs))
            return SimpleNamespace(
                usage=SimpleNamespace(
                    prompt_tokens=3,
                    completion_tokens=2,
                    total_tokens=5,
                )
            )

        async def generate_response(*args: object, **kwargs: object) -> object:
            self.events.append(("llm", args, kwargs))
            await llm.client.chat.completions.create(
                messages=args[0],
                api_key="PRIVATE-FIXTURE-KEY",
            )
            return self.llm_result

        llm = SimpleNamespace(
            client=SimpleNamespace(
                chat=SimpleNamespace(
                    completions=SimpleNamespace(create=transport_create)
                )
            ),
            generate_response=generate_response,
        )

        async def create_embedding(*args: object, **kwargs: object) -> object:
            self.events.append(("embedding", args, kwargs))
            return self.embedding_result

        embedder = SimpleNamespace(create=create_embedding)

        async def execute_query(*args: object, **kwargs: object) -> object:
            self.events.append(("database", args, kwargs))
            return self.db_result

        driver = SimpleNamespace(execute_query=execute_query)

        async def retrieve_episodes(*args: object, **kwargs: object) -> tuple[str]:
            self.events.append(("retrieve", args, kwargs))
            return ("previous",)

        async def publish(*args: object, **kwargs: object) -> str:
            self.events.append(("publish", args, kwargs))
            return "published"

        async def add_episode(payload: object, *, option: object) -> object:
            self.events.append(("add-episode", payload, option))
            previous = await graphiti.retrieve_episodes(payload)
            nodes = await self.phase_module.extract_nodes(payload, previous=previous)
            resolved_nodes = await self.phase_module.resolve_extracted_nodes(nodes)
            edges = await self.phase_module.extract_edges(payload, resolved_nodes)
            resolved_edges = await self.phase_module.resolve_extracted_edges(edges)
            attributes = await self.phase_module.extract_attributes_from_nodes(
                resolved_nodes,
                edges=resolved_edges,
            )
            llm_value = await graphiti.llm_client.generate_response(
                [{"role": "user", "content": "PRIVATE-PROMPT"}],
                prompt_name="extract_nodes.extract_message",
            )
            embedding_value = await graphiti.embedder.create(
                input_data="PRIVATE-EMBEDDING"
            )
            db_value = await graphiti.driver.execute_query(
                "MATCH (n) RETURN n // PRIVATE-QUERY",
                routing_="r",
            )
            publication = await graphiti._process_episode_data(attributes)
            state = (
                payload,
                option,
                previous,
                nodes,
                resolved_nodes,
                edges,
                resolved_edges,
                attributes,
                llm_value,
                embedding_value,
                db_value,
                publication,
            )
            self.graph_state.append(state)
            return self.result

        graphiti = SimpleNamespace(
            llm_client=llm,
            embedder=embedder,
            driver=driver,
            retrieve_episodes=retrieve_episodes,
            _process_episode_data=publish,
            add_episode=add_episode,
        )
        self.graphiti = graphiti

    def reset(self) -> None:
        self.events.clear()
        self.graph_state.clear()
        self.entered = asyncio.Event()
        self.release = asyncio.Event()


class InstrumentationLifecycleParityTests(IsolatedAsyncioTestCase):
    async def test_high_level_install_is_idempotent_reversible_and_reinstallable(self) -> None:
        fixture = _DeterministicParityFixture()
        recorder = TraceRecorder()
        originals = {
            "add_episode": fixture.graphiti.add_episode,
            "llm": fixture.graphiti.llm_client.generate_response,
            "embedding": fixture.graphiti.embedder.create,
            "database": fixture.graphiti.driver.execute_query,
            "extract_nodes": fixture.phase_module.extract_nodes,
        }

        first = install_native_characterization_instrumentation(
            fixture.graphiti,
            recorder,
            phase_module=fixture.phase_module,
        )
        second = install_native_characterization_instrumentation(
            fixture.graphiti,
            recorder,
            phase_module=fixture.phase_module,
        )
        self.assertIs(first, second)
        with self.assertRaisesRegex(RuntimeError, "another recorder"):
            install_native_characterization_instrumentation(
                fixture.graphiti,
                TraceRecorder(),
                phase_module=fixture.phase_module,
            )

        payload = object()
        option = object()
        with recorder.episode_scope("run", "episode", 0):
            self.assertIs(
                await fixture.graphiti.add_episode(payload, option=option),
                fixture.result,
            )
        self.assertEqual(
            [record.phase for record in recorder.records].count("add-episode"),
            1,
        )

        first.restore()
        first.restore()
        self.assertIs(fixture.graphiti.add_episode, originals["add_episode"])
        self.assertIs(fixture.graphiti.llm_client.generate_response, originals["llm"])
        self.assertIs(fixture.graphiti.embedder.create, originals["embedding"])
        self.assertIs(fixture.graphiti.driver.execute_query, originals["database"])
        self.assertIs(fixture.phase_module.extract_nodes, originals["extract_nodes"])

        third = install_native_characterization_instrumentation(
            fixture.graphiti,
            recorder,
            phase_module=fixture.phase_module,
        )
        self.assertIsNot(third, first)
        third.restore()

    async def test_partial_install_failure_rolls_back_every_wrapper(self) -> None:
        fixture = _DeterministicParityFixture()
        recorder = TraceRecorder()
        original_phase = fixture.phase_module.extract_nodes
        original_llm = fixture.graphiti.llm_client.generate_response
        failure = RuntimeError("synthetic install failure")

        with patch(
            "native_characterization_instrumentation.instrument_embedding_client",
            side_effect=failure,
        ):
            with self.assertRaises(RuntimeError) as raised:
                install_native_characterization_instrumentation(
                    fixture.graphiti,
                    recorder,
                    phase_module=fixture.phase_module,
                )

        self.assertIs(raised.exception, failure)
        self.assertIs(fixture.phase_module.extract_nodes, original_phase)
        self.assertIs(fixture.graphiti.llm_client.generate_response, original_llm)
        installed = install_native_characterization_instrumentation(
            fixture.graphiti,
            recorder,
            phase_module=fixture.phase_module,
        )
        installed.restore()

    async def test_trace_off_on_success_has_identical_semantics(self) -> None:
        fixture = _DeterministicParityFixture()
        payload = object()
        option = object()

        off_result = await fixture.graphiti.add_episode(payload, option=option)
        off_events = list(fixture.events)
        off_state = list(fixture.graph_state)
        fixture.reset()

        recorder = TraceRecorder()
        installed = install_native_characterization_instrumentation(
            fixture.graphiti,
            recorder,
            phase_module=fixture.phase_module,
        )
        try:
            with recorder.episode_scope("run", "episode", 0):
                on_result = await fixture.graphiti.add_episode(payload, option=option)
        finally:
            installed.restore()

        self.assertIs(on_result, off_result)
        self.assertEqual(fixture.events, off_events)
        self.assertEqual(fixture.graph_state, off_state)
        self.assertIs(fixture.events[0][1], payload)
        self.assertIs(fixture.events[0][2], option)

    async def test_trace_off_on_preserves_exception_object_and_call_order(self) -> None:
        fixture = _DeterministicParityFixture(mode="error")
        payload = object()
        option = object()

        with self.assertRaises(RuntimeError) as off_raised:
            await fixture.graphiti.add_episode(payload, option=option)
        off_events = list(fixture.events)
        fixture.reset()

        recorder = TraceRecorder()
        installed = install_native_characterization_instrumentation(
            fixture.graphiti,
            recorder,
            phase_module=fixture.phase_module,
        )
        try:
            with self.assertRaises(RuntimeError) as on_raised:
                with recorder.episode_scope("run", "episode", 0):
                    await fixture.graphiti.add_episode(payload, option=option)
        finally:
            installed.restore()

        self.assertIs(off_raised.exception, fixture.failure)
        self.assertIs(on_raised.exception, fixture.failure)
        self.assertEqual(fixture.events, off_events)
        self.assertEqual(fixture.graph_state, [])
        self.assertEqual(
            {record.status for record in recorder.records},
            {"error", "ok"},
        )

    async def test_trace_off_on_preserves_cancellation_and_cleanup(self) -> None:
        fixture = _DeterministicParityFixture(mode="cancel")
        payload = object()
        option = object()

        async def cancelled_run() -> tuple[bool, list[tuple[object, ...]]]:
            task = asyncio.create_task(
                fixture.graphiti.add_episode(payload, option=option)
            )
            await fixture.entered.wait()
            task.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await task
            return task.cancelled(), list(fixture.events)

        off_cancelled, off_events = await cancelled_run()
        fixture.reset()
        recorder = TraceRecorder()
        installed = install_native_characterization_instrumentation(
            fixture.graphiti,
            recorder,
            phase_module=fixture.phase_module,
        )
        try:
            with recorder.episode_scope("run", "episode", 0):
                on_cancelled, on_events = await cancelled_run()
        finally:
            installed.restore()

        self.assertTrue(off_cancelled)
        self.assertTrue(on_cancelled)
        self.assertEqual(on_events, off_events)
        self.assertEqual(fixture.graph_state, [])
        cancelled_phases = {
            record.phase for record in recorder.records if record.status == "cancelled"
        }
        self.assertEqual(cancelled_phases, {"add-episode", "node-extraction"})


if __name__ == "__main__":
    import unittest

    unittest.main()
