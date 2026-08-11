"""Offline TDD contracts for the one-shot, block-0 C2 cleanup helper."""

from __future__ import annotations

import asyncio
import json
import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace
from unittest import TestCase


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from native_characterization_c2_cleanup import (  # noqa: E402
    FAILED_C2_ATTEMPT_ID,
    POLLUTED_C2_GROUP_ID,
    ScopedC2CleanupError,
    cleanup_scoped_c2_namespace,
)


OTHER_FROZEN_GROUPS = (
    "nc-e1e2-1deef863d4241064",
    "nc-e1e2-51f7e5e87820835a",
    "nc-e1e2-5c183f97e44578e7",
)


def _write_freeze(root: Path, *, block_zero: str = POLLUTED_C2_GROUP_ID) -> Path:
    namespaces = (block_zero, *OTHER_FROZEN_GROUPS)
    histories = ("h-alpha", "h-beta", "h-gamma", "h-delta")
    freeze = {
        "screening": {
            "e1_e2": {
                "shared_native_trace": True,
                "block_order": [
                    {
                        "block_index": index,
                        "history_id": history_id,
                        "graph_namespace": namespaces[index],
                    }
                    for index, history_id in enumerate(histories)
                ],
            }
        },
        "dataset": {
            "calibration_histories": [
                {
                    "history_id": history_id,
                    "episode_count": 1,
                    "episodes": [
                        {
                            "source_sequence": 0,
                            "episode_source_sha256": f"{index + 1:x}" * 64,
                            "prefix_sha256": f"{index + 5:x}" * 64,
                        }
                    ],
                }
                for index, history_id in enumerate(histories)
            ]
        },
    }
    path = root / "freeze.json"
    path.write_text(json.dumps(freeze, sort_keys=True), encoding="ascii")
    return path


class _FakeDriver:
    def __init__(self, counts: list[tuple[str, int]], events: list[tuple]) -> None:
        self.counts = list(counts)
        self.events = events

    async def execute_query(self, query: str, **kwargs):
        params = kwargs.get("params")
        if params != {"group_id": POLLUTED_C2_GROUP_ID}:
            raise AssertionError(f"unexpected params: {params!r}")
        if "MATCH (n)" in query:
            kind = "node"
            key = "node_count"
        elif "MATCH ()-[r]->()" in query:
            kind = "relationship"
            key = "relationship_count"
        else:
            raise AssertionError("count query was not group-scoped")
        if "group_id = $group_id" not in query:
            raise AssertionError("count query omitted the exact group predicate")
        expected_kind, value = self.counts.pop(0)
        if kind != expected_kind:
            raise AssertionError(f"expected {expected_kind}, observed {kind}")
        self.events.append(("count", kind, value, dict(params)))
        return SimpleNamespace(records=[{key: value}])


class _ResultDriver:
    def __init__(self, results: list[object], events: list[tuple]) -> None:
        self.results = list(results)
        self.events = events

    async def execute_query(self, query: str, **kwargs):
        params = kwargs.get("params")
        if params != {"group_id": POLLUTED_C2_GROUP_ID}:
            raise AssertionError(f"unexpected params: {params!r}")
        self.events.append(("count_result", query, dict(params)))
        return self.results.pop(0)


def _clear_spy(events: list[tuple], *, fail: bool = False):
    async def clear(driver, *, group_ids):
        events.append(("clear", driver, list(group_ids)))
        if fail:
            raise RuntimeError("raw-upstream-detail-must-not-escape")

    return clear


class NativeCharacterizationC2CleanupTests(TestCase):
    def test_cleanup_evidence_is_bound_to_latest_polluting_attempt(self) -> None:
        self.assertEqual(FAILED_C2_ATTEMPT_ID, "c2-723261287e32e182")

    def test_exact_block_zero_cleanup_counts_before_and_after(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            freeze = _write_freeze(Path(tmp))
            events: list[tuple] = []
            driver = _FakeDriver(
                [("node", 7), ("relationship", 11), ("node", 0), ("relationship", 0)],
                events,
            )
            evidence = asyncio.run(
                cleanup_scoped_c2_namespace(
                    driver=driver,
                    freeze_path=freeze,
                    target_group=POLLUTED_C2_GROUP_ID,
                    operator_authorized=True,
                    clear_data_impl=_clear_spy(events),
                )
            )

        self.assertEqual(
            [event[0:2] for event in events],
            [
                ("count", "node"),
                ("count", "relationship"),
                ("clear", driver),
                ("count", "node"),
                ("count", "relationship"),
            ],
        )
        self.assertEqual(events[2][2], [POLLUTED_C2_GROUP_ID])
        self.assertEqual(evidence["status"], "verified_empty")
        self.assertEqual(evidence["target_group_id"], POLLUTED_C2_GROUP_ID)
        self.assertEqual(evidence["failed_attempt_id"], FAILED_C2_ATTEMPT_ID)
        self.assertFalse(evidence["failed_attempt_valid"])
        self.assertFalse(evidence["failed_attempt_mergeable"])
        self.assertFalse(evidence["replacement_resume_allowed"])
        self.assertEqual(
            evidence["cleanup_primitive"],
            "graphiti.clear_data(driver,group_ids=[target_group])",
        )
        self.assertEqual(evidence["pre_cleanup"], {"node_count": 7, "relationship_count": 11})
        self.assertEqual(evidence["post_cleanup"], {"node_count": 0, "relationship_count": 0})
        self.assertRegex(evidence["freeze_sha256"], r"^[0-9a-f]{64}$")
        self.assertRegex(evidence["payload_sha256"], r"^[0-9a-f]{64}$")

    def test_operator_authorization_must_be_literal_true_before_any_io(self) -> None:
        for authorization in (False, None, 0, 1, "true"):
            with self.subTest(authorization=authorization), tempfile.TemporaryDirectory() as tmp:
                freeze = _write_freeze(Path(tmp))
                events: list[tuple] = []
                with self.assertRaisesRegex(ScopedC2CleanupError, "operator_authorization_required"):
                    asyncio.run(
                        cleanup_scoped_c2_namespace(
                            driver=_FakeDriver([], events),
                            freeze_path=freeze,
                            target_group=POLLUTED_C2_GROUP_ID,
                            operator_authorized=authorization,
                            clear_data_impl=_clear_spy(events),
                        )
                    )
                self.assertEqual(events, [])

    def test_rejects_none_empty_multiple_other_frozen_and_non_frozen_targets(self) -> None:
        invalid_targets = (
            None,
            "",
            "   ",
            [POLLUTED_C2_GROUP_ID],
            (POLLUTED_C2_GROUP_ID,),
            [POLLUTED_C2_GROUP_ID, OTHER_FROZEN_GROUPS[0]],
            *OTHER_FROZEN_GROUPS,
            "nc-e1e2-0000000000000000",
        )
        for target in invalid_targets:
            with self.subTest(target=target), tempfile.TemporaryDirectory() as tmp:
                freeze = _write_freeze(Path(tmp))
                events: list[tuple] = []
                with self.assertRaises(ScopedC2CleanupError):
                    asyncio.run(
                        cleanup_scoped_c2_namespace(
                            driver=_FakeDriver([], events),
                            freeze_path=freeze,
                            target_group=target,
                            operator_authorized=True,
                            clear_data_impl=_clear_spy(events),
                        )
                    )
                self.assertEqual(events, [])

    def test_rejects_freeze_block_zero_drift_before_driver_io(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            freeze = _write_freeze(
                Path(tmp), block_zero="nc-e1e2-aaaaaaaaaaaaaaaa"
            )
            events: list[tuple] = []
            with self.assertRaisesRegex(ScopedC2CleanupError, "freeze_block_zero_binding_mismatch"):
                asyncio.run(
                    cleanup_scoped_c2_namespace(
                        driver=_FakeDriver([], events),
                        freeze_path=freeze,
                        target_group=POLLUTED_C2_GROUP_ID,
                        operator_authorized=True,
                        clear_data_impl=_clear_spy(events),
                    )
                )
            self.assertEqual(events, [])

    def test_rejects_malformed_or_unreadable_freeze_before_driver_io(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            malformed = root / "malformed.json"
            malformed.write_text("{", encoding="ascii")
            for freeze in (malformed, root / "missing.json"):
                with self.subTest(freeze=freeze.name):
                    events: list[tuple] = []
                    with self.assertRaisesRegex(ScopedC2CleanupError, "freeze_invalid"):
                        asyncio.run(
                            cleanup_scoped_c2_namespace(
                                driver=_FakeDriver([], events),
                                freeze_path=freeze,
                                target_group=POLLUTED_C2_GROUP_ID,
                                operator_authorized=True,
                                clear_data_impl=_clear_spy(events),
                            )
                        )
                    self.assertEqual(events, [])

    def test_missing_or_wrong_pre_count_records_prevent_clear(self) -> None:
        malformed_results = (
            SimpleNamespace(),
            SimpleNamespace(records=None),
            SimpleNamespace(records={"node_count": 1}),
            SimpleNamespace(records=[]),
            SimpleNamespace(records=[{}]),
            SimpleNamespace(records=[{"node_count": 1}, {"node_count": 2}]),
        )
        for malformed in malformed_results:
            with self.subTest(malformed=malformed), tempfile.TemporaryDirectory() as tmp:
                freeze = _write_freeze(Path(tmp))
                events: list[tuple] = []
                with self.assertRaises(ScopedC2CleanupError):
                    asyncio.run(
                        cleanup_scoped_c2_namespace(
                            driver=_ResultDriver([malformed], events),
                            freeze_path=freeze,
                            target_group=POLLUTED_C2_GROUP_ID,
                            operator_authorized=True,
                            clear_data_impl=_clear_spy(events),
                        )
                    )
                self.assertEqual(sum(event[0] == "clear" for event in events), 0)

    def test_invalid_post_count_has_sanitized_verification_evidence(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            freeze = _write_freeze(Path(tmp))
            events: list[tuple] = []
            driver = _ResultDriver(
                [
                    SimpleNamespace(records=[{"node_count": 3}]),
                    SimpleNamespace(records=[{"relationship_count": 4}]),
                    SimpleNamespace(records=[{}]),
                ],
                events,
            )
            with self.assertRaisesRegex(
                ScopedC2CleanupError, "post_node_count_invalid"
            ) as raised:
                asyncio.run(
                    cleanup_scoped_c2_namespace(
                        driver=driver,
                        freeze_path=freeze,
                        target_group=POLLUTED_C2_GROUP_ID,
                        operator_authorized=True,
                        clear_data_impl=_clear_spy(events),
                    )
                )

        self.assertEqual(sum(event[0] == "clear" for event in events), 1)
        evidence = raised.exception.evidence
        self.assertEqual(evidence["status"], "post_cleanup_verification_failed")
        self.assertEqual(evidence["pre_cleanup"], {"node_count": 3, "relationship_count": 4})
        self.assertEqual(evidence["post_cleanup"], {"node_count": None, "relationship_count": None})
        serialized = json.dumps(evidence, sort_keys=True).casefold()
        for forbidden in ("api_key", "authorization", "cypher", "prompt", "response"):
            self.assertNotIn(forbidden, serialized)

    def test_post_cleanup_residuals_fail_closed_with_sanitized_evidence(self) -> None:
        for post_nodes, post_relationships in ((1, 0), (0, 2)):
            with self.subTest(
                post_nodes=post_nodes, post_relationships=post_relationships
            ), tempfile.TemporaryDirectory() as tmp:
                freeze = _write_freeze(Path(tmp))
                events: list[tuple] = []
                driver = _FakeDriver(
                    [
                        ("node", 3),
                        ("relationship", 4),
                        ("node", post_nodes),
                        ("relationship", post_relationships),
                    ],
                    events,
                )
                with self.assertRaisesRegex(
                    ScopedC2CleanupError, "post_cleanup_residual_detected"
                ) as raised:
                    asyncio.run(
                        cleanup_scoped_c2_namespace(
                            driver=driver,
                            freeze_path=freeze,
                            target_group=POLLUTED_C2_GROUP_ID,
                            operator_authorized=True,
                            clear_data_impl=_clear_spy(events),
                        )
                    )
                evidence = raised.exception.evidence
                self.assertEqual(evidence["status"], "residual_detected")
                self.assertEqual(
                    evidence["post_cleanup"],
                    {
                        "node_count": post_nodes,
                        "relationship_count": post_relationships,
                    },
                )
                serialized = json.dumps(evidence, sort_keys=True).casefold()
                for forbidden in ("api_key", "authorization", "cypher", "prompt", "response"):
                    self.assertNotIn(forbidden, serialized)

    def test_invalid_pre_cleanup_count_prevents_clear(self) -> None:
        for invalid in (True, -1, 1.5, "1"):
            with self.subTest(invalid=invalid), tempfile.TemporaryDirectory() as tmp:
                freeze = _write_freeze(Path(tmp))
                events: list[tuple] = []
                driver = _FakeDriver([("node", invalid)], events)
                with self.assertRaisesRegex(ScopedC2CleanupError, "pre_node_count_invalid"):
                    asyncio.run(
                        cleanup_scoped_c2_namespace(
                            driver=driver,
                            freeze_path=freeze,
                            target_group=POLLUTED_C2_GROUP_ID,
                            operator_authorized=True,
                            clear_data_impl=_clear_spy(events),
                        )
                    )
                self.assertFalse(any(event[0] == "clear" for event in events))

    def test_upstream_failure_is_sanitized_and_does_not_run_post_counts(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            freeze = _write_freeze(Path(tmp))
            events: list[tuple] = []
            driver = _FakeDriver([("node", 2), ("relationship", 3)], events)
            with self.assertRaisesRegex(ScopedC2CleanupError, "upstream_clear_failed") as raised:
                asyncio.run(
                    cleanup_scoped_c2_namespace(
                        driver=driver,
                        freeze_path=freeze,
                        target_group=POLLUTED_C2_GROUP_ID,
                        operator_authorized=True,
                        clear_data_impl=_clear_spy(events, fail=True),
                    )
                )

        self.assertEqual([event[0] for event in events], ["count", "count", "clear"])
        self.assertNotIn("raw-upstream-detail", str(raised.exception))
        self.assertEqual(raised.exception.evidence["status"], "upstream_clear_failed")

    def test_already_empty_target_remains_exact_and_idempotent(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            freeze = _write_freeze(Path(tmp))
            events: list[tuple] = []
            driver = _FakeDriver(
                [("node", 0), ("relationship", 0), ("node", 0), ("relationship", 0)],
                events,
            )
            evidence = asyncio.run(
                cleanup_scoped_c2_namespace(
                    driver=driver,
                    freeze_path=freeze,
                    target_group=POLLUTED_C2_GROUP_ID,
                    operator_authorized=True,
                    clear_data_impl=_clear_spy(events),
                )
            )

        self.assertTrue(evidence["preexisting_empty"])
        self.assertEqual(sum(event[0] == "clear" for event in events), 1)
