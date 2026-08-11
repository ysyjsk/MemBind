"""RED/GREEN contract for durable C2 evidence when one episode raises."""

from __future__ import annotations

import asyncio
import json
import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace
from unittest import TestCase
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

import native_characterization_c2 as c2  # noqa: E402
from tests import test_native_characterization_c2 as fixtures  # noqa: E402


class SyntheticEpisodeFailure(RuntimeError):
    """Stable exception type whose message must never enter C2 artifacts."""


class NativeCharacterizationC2ErrorDurabilityTests(TestCase):
    def test_structured_decode_failure_persists_all_attempts_and_logical_error(self):
        with tempfile.TemporaryDirectory() as temporary:
            validation_root = Path(temporary)
            freeze_path = fixtures._write_freeze(validation_root)
            run_id = "c2-offline-structured-retry-evidence"

            class Transport:
                async def create(self, *_args, **_kwargs):
                    return SimpleNamespace(
                        usage=SimpleNamespace(prompt_tokens=7, completion_tokens=3)
                    )

            class Client:
                def __init__(self):
                    self.client = SimpleNamespace(
                        chat=SimpleNamespace(
                            completions=SimpleNamespace(create=Transport().create)
                        )
                    )

                async def generate_response(self, *_args, **_kwargs):
                    for _ in range(4):
                        await self.client.chat.completions.create()
                    raise json.JSONDecodeError(
                        "PRIVATE_DECODE_MESSAGE",
                        "PRIVATE_RAW_RESPONSE",
                        0,
                    )

            def runtime_factory():
                runtime = fixtures._fake_runtime_factory()
                runtime.graphiti.llm_client = Client()
                return runtime

            with self.assertRaises(json.JSONDecodeError):
                asyncio.run(
                    c2.execute_c2(
                        validation_root=validation_root,
                        freeze_path=freeze_path.relative_to(
                            validation_root
                        ).as_posix(),
                        run_id=run_id,
                        authorization_checker=lambda _action: None,
                        runtime_factory=runtime_factory,
                        measurement_installer=fixtures._complete_measurement_installer,
                        graph_prefix_collector=fixtures._graph_prefix_collector,
                    )
                )

            run_root = (
                validation_root
                / "artifacts"
                / "native_characterization"
                / "runs"
                / run_id
            )
            envelope = json.loads(
                (
                    run_root / "blocks" / "000_h-alpha" / "trace.jsonl"
                ).read_text("ascii").splitlines()[0]
            )
            logical = [span for span in envelope["spans"] if span["phase"] == "llm"]
            transports = [
                span
                for span in envelope["spans"]
                if span["phase"] == "llm-transport"
            ]
            self.assertEqual(len(logical), 1)
            self.assertEqual(logical[0]["status"], "error")
            self.assertEqual(logical[0]["metadata"]["retry_count"], 3)
            self.assertEqual(logical[0]["metadata"]["input_tokens"], 28)
            self.assertEqual(logical[0]["metadata"]["output_tokens"], 12)
            self.assertEqual(len(transports), 4)
            self.assertTrue(all(span["status"] == "ok" for span in transports))
            error_views = [
                json.loads(line)
                for line in (run_root / "errors.jsonl").read_text("ascii").splitlines()
            ]
            self.assertEqual(len(error_views), 1)
            self.assertEqual(
                {span["phase"] for span in error_views[0]["spans"]},
                {"add-episode", "llm", "node-extraction"},
            )
            persisted = "".join(
                path.read_text("ascii")
                for path in run_root.rglob("*.jsonl")
            )
            self.assertNotIn("PRIVATE_DECODE_MESSAGE", persisted)
            self.assertNotIn("PRIVATE_RAW_RESPONSE", persisted)

    def test_failed_episode_envelope_is_durable_before_root_error_checkpoint(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            validation_root = Path(temporary)
            freeze_path = fixtures._write_freeze(validation_root)
            run_id = "c2-offline-error-durability"
            run_root = (
                validation_root
                / "artifacts"
                / "native_characterization"
                / "runs"
                / run_id
            )
            failure = SyntheticEpisodeFailure("SECRET_EPISODE_BODY_MUST_NOT_PERSIST")
            events: list[tuple[str, str]] = []

            def runtime_factory():
                runtime = fixtures._fake_runtime_factory()
                original_add_episode = runtime.graphiti.add_episode

                async def add_episode(episode):
                    if episode["episode_id"] == "h-alpha:1":
                        raise failure
                    return await original_add_episode(episode)

                runtime.graphiti.add_episode = add_episode
                return runtime

            original_writer_type = c2.DurableJsonlEnvelopeWriter

            class ObservedDurableWriter:
                def __init__(self, path):
                    self._inner = original_writer_type(path)
                    self._path = Path(path)

                def write(self, envelope):
                    self._inner.write(envelope)
                    if self._path.name != "trace.jsonl":
                        return
                    root = next(
                        span
                        for span in envelope["spans"]
                        if span["phase"] == "add-episode"
                    )
                    events.append(("trace_durable", root["status"]))

            original_atomic_json = c2._atomic_json

            def observed_atomic_json(path, value):
                path = Path(path)
                if path == run_root / "checkpoint.json" and value.get("status") == "error":
                    events.append(("root_error_checkpoint", str(value["status"])))
                return original_atomic_json(path, value)

            with (
                patch.object(c2, "DurableJsonlEnvelopeWriter", ObservedDurableWriter),
                patch.object(c2, "_atomic_json", side_effect=observed_atomic_json),
            ):
                with self.assertRaises(SyntheticEpisodeFailure) as raised:
                    asyncio.run(
                        c2.execute_c2(
                            validation_root=validation_root,
                            freeze_path=freeze_path.relative_to(
                                validation_root
                            ).as_posix(),
                            run_id=run_id,
                            authorization_checker=lambda _action: None,
                            runtime_factory=runtime_factory,
                            measurement_installer=(
                                fixtures._complete_measurement_installer
                            ),
                            graph_prefix_collector=(
                                fixtures._graph_prefix_collector
                            ),
                        )
                    )

            self.assertIs(raised.exception, failure)
            error_trace_index = events.index(("trace_durable", "error"))
            checkpoint_index = events.index(("root_error_checkpoint", "error"))
            self.assertLess(error_trace_index, checkpoint_index)
            self.assertEqual(events.count(("trace_durable", "error")), 1)

            trace_path = run_root / "blocks" / "000_h-alpha" / "trace.jsonl"
            envelopes = [json.loads(line) for line in trace_path.read_text("ascii").splitlines()]
            self.assertEqual([item["episode_id"] for item in envelopes], ["h-alpha:0", "h-alpha:1"])
            failed_envelope = envelopes[-1]
            failed_roots = [
                span
                for span in failed_envelope["spans"]
                if span["phase"] == "add-episode"
            ]
            self.assertEqual(len(failed_roots), 1)
            self.assertEqual(failed_roots[0]["status"], "error")
            self.assertEqual(
                failed_roots[0]["error_code"],
                f"{SyntheticEpisodeFailure.__module__}.{SyntheticEpisodeFailure.__qualname__}",
            )

            checkpoint = json.loads((run_root / "checkpoint.json").read_text("ascii"))
            self.assertEqual(checkpoint["status"], "error")
            self.assertEqual(checkpoint["completed_episode_ids"], ["h-alpha:0"])
            self.assertNotIn("h-alpha:1", checkpoint["completed_episode_ids"])
            self.assertFalse(any("resume" in key.casefold() for key in checkpoint))
            self.assertFalse(any("merge" in key.casefold() for key in checkpoint))

            persisted = (trace_path.read_text("ascii") + (run_root / "checkpoint.json").read_text("ascii"))
            self.assertNotIn("SECRET_EPISODE_BODY_MUST_NOT_PERSIST", persisted)


if __name__ == "__main__":
    import unittest

    unittest.main()
