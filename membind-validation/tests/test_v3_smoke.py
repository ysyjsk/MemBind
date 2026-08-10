import json
import sys
import tempfile
from argparse import Namespace
from pathlib import Path
from unittest import TestCase
from unittest.mock import AsyncMock, patch


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

import replay_driver  # noqa: E402


class V3SmokeTests(TestCase):
    def test_v3_smoke_runs_only_m0_capture_and_m2_replay(self):
        with tempfile.TemporaryDirectory() as tmp:
            artifacts = Path(tmp) / "artifacts"
            (artifacts / "dataset").mkdir(parents=True)
            (artifacts / "graphs").mkdir()
            (artifacts / "retrieval").mkdir()
            (artifacts / "runs").mkdir()
            (artifacts / "traces").mkdir()
            (artifacts / "dataset" / "frozen_split.json").write_text(
                json.dumps({"evaluation_question_ids": ["q0"]}),
                encoding="utf-8",
            )

            async def fake_run_one(
                method,
                instance,
                run_id,
                repeat,
                arrival_interval_ms,
                *,
                lane,
                mode,
                cache_id,
            ):
                (artifacts / "graphs" / f"{run_id}.canonical.json").write_text(
                    json.dumps({"nodes": [], "edges": [], "episodes": []}),
                    encoding="utf-8",
                )
                (artifacts / "retrieval" / f"{run_id}.json").write_text(
                    json.dumps({"queries": []}),
                    encoding="utf-8",
                )
                (artifacts / "runs" / f"{run_id}.json").write_text(
                    json.dumps(
                        {
                            "run_id": run_id,
                            "status": "success",
                            "llm_metrics": {"unexpected_prompt": False},
                        }
                    ),
                    encoding="utf-8",
                )
                (artifacts / "traces" / f"{run_id}.jsonl").write_text(
                    "\n".join(
                        [
                            json.dumps({"source_sequence": 0, "publish_time": 0}),
                            json.dumps({"source_sequence": 1, "publish_time": 1}),
                        ]
                    )
                    + "\n",
                    encoding="utf-8",
                )

            run_one = AsyncMock(side_effect=fake_run_one)
            args = Namespace(
                data="unused.json",
                question_id=None,
                attempt="v3test",
                reference_attempt=None,
                arrival_interval_ms=25,
                authorization_checker=lambda *_args, **_kwargs: None,
            )

            with (
                patch.object(replay_driver, "ARTIFACTS", artifacts),
                patch.object(replay_driver, "load_instance", return_value={"question_id": "q0"}),
                patch.object(replay_driver, "build_episodes", return_value=[object(), object()]),
                patch.object(
                    replay_driver,
                    "compare_canonical_graphs",
                    return_value={"canonical_graph_parity": True},
                ),
                patch.object(
                    replay_driver,
                    "_retrieval_with_reference",
                    return_value={"exact_match": True},
                ),
                patch.object(replay_driver, "run_one", new=run_one),
            ):
                replay_driver.cmd_v3_smoke(args)

            observed = [
                (call.args[0], call.kwargs["mode"], call.kwargs["lane"], call.kwargs["cache_id"])
                for call in run_one.await_args_list
            ]
            self.assertEqual(
                observed,
                [
                    (
                        replay_driver.M0_NATIVE_SERIAL,
                        "capture",
                        "v3_smoke",
                        "v3_smoke_v3test_q0",
                    ),
                    (
                        replay_driver.M2_MEMBIND_GO_C8,
                        "replay",
                        "v3_smoke",
                        "v3_smoke_v3test_q0",
                    ),
                ],
            )
            self.assertNotIn(
                replay_driver.M1_WHOLE_PARALLEL_C8,
                [call.args[0] for call in run_one.await_args_list],
            )
            status = json.loads((artifacts / "smoke" / "v3test.json").read_text(encoding="utf-8"))
            self.assertTrue(status["ok"])
            self.assertEqual(set(status["run_ids"]), {"M0", "M2"})
            self.assertNotIn("m1_vs_m0", status)
            self.assertNotIn("M1_vs_M0", status["retrieval"])
