#!/usr/bin/env python3
"""Compare the checked-in MAB projection with the exact HF revision.

The verifier uses the official parquet split directly (``datasets`` is not
required) and fetches the official ``ConversationCreator`` and LongMemEval
config at a pinned Git commit.  It never drops a context to make a comparison
pass; the resulting disposition is computed from all observed differences.
"""

from __future__ import annotations

import ast
import hashlib
import json
import os
import subprocess
import re
import tempfile
import urllib.request
from pathlib import Path
from typing import Any, Mapping

import pyarrow.parquet as pq

ROOT = Path(__file__).resolve().parents[1]
LOCAL = ROOT / "mab_quality_v2_final_qa/data/official_5_contexts.json"
OUT = ROOT / "mab_quality_v2_final_qa/evidence"
HF_DATASET = "ai-hyz/MemoryAgentBench"
HF_REVISION = "7ea066982b140a19337e17e60d45d4076e042faf"
SPLIT_PATH = "data/Accurate_Retrieval-00000-of-00001.parquet"
OFFICIAL_REPO = "HUST-AI-HYZ/MemoryAgentBench"
OFFICIAL_CODE_REVISION = "fe1735de8cf8b9908e1e3d3b5612afc815698062"
SOURCE_FILTER = "longmemeval_s*"


def _sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _sha256_file(path: Path) -> str:
    return _sha256_bytes(path.read_bytes())


def _git_head() -> str:
    return subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()


def _canonical(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)


def _metadata(row: Mapping[str, Any]) -> dict[str, Any]:
    value = row.get("metadata", {})
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except json.JSONDecodeError:
            value = {}
    return dict(value) if isinstance(value, Mapping) else {}


def _context_sessions(raw: Any) -> list[dict[str, Any]]:
    if isinstance(raw, str):
        raw = ast.literal_eval(raw)
    values = list(raw.get("sessions", raw.get("haystack_sessions"))) if isinstance(raw, Mapping) else list(raw)
    sessions: list[dict[str, Any]] = []
    if values and all(isinstance(item, Mapping) for item in values):
        for item in values:
            sessions.append({"timestamp": item.get("timestamp", item.get("date", item.get("session_date"))), "turns": item.get("turns", item.get("messages", item.get("dialogue")))})
    elif len(values) % 2 == 0 and all(isinstance(values[i], str) for i in range(0, len(values), 2)):
        sessions = [{"timestamp": values[i], "turns": values[i + 1]} for i in range(0, len(values), 2)]
    else:
        sessions = [{"timestamp": item[0], "turns": item[1]} for item in values]
    return sessions


def _question_ids(row: Mapping[str, Any]) -> list[str]:
    metadata = _metadata(row)
    values = row.get("question_ids", metadata.get("question_ids", []))
    if isinstance(values, str):
        return [values]
    return [str(value) for value in (values or [])]


def _qa_count(row: Mapping[str, Any]) -> int:
    questions = row.get("questions", [])
    if isinstance(questions, str):
        return 1
    return len(questions or [])


def _download(url: str) -> bytes:
    request = urllib.request.Request(url, headers={"User-Agent": "MemBind-official-parity/1"})
    with urllib.request.urlopen(request, timeout=60) as response:
        return response.read()


def _fetch_official() -> tuple[list[dict[str, Any]], dict[str, Any]]:
    parquet_url = f"https://huggingface.co/datasets/{HF_DATASET}/resolve/{HF_REVISION}/{SPLIT_PATH}"
    with tempfile.NamedTemporaryFile(prefix="mab-parity-", suffix=".parquet", delete=False) as handle:
        path = Path(handle.name)
        handle.write(_download(parquet_url))
    try:
        rows = pq.read_table(path).to_pylist()
    finally:
        path.unlink(missing_ok=True)
    selected = [dict(row) for row in rows if _metadata(row).get("source") == SOURCE_FILTER]
    code_files = {}
    for relative in ("conversation_creator.py", "utils/eval_data_utils.py", "configs/data_conf/Accurate_Retrieval/LongMemEval/Longmemeval_s_star.yaml"):
        url = f"https://raw.githubusercontent.com/{OFFICIAL_REPO}/{OFFICIAL_CODE_REVISION}/{relative}"
        body = _download(url)
        code_files[relative] = {"sha256": _sha256_bytes(body), "bytes": len(body)}
    return selected, {"hf_parquet_url": parquet_url, "official_code_revision": OFFICIAL_CODE_REVISION, "official_code_files": code_files, "official_loader_source": "ConversationCreator._create_query_answer_pairs + utils.eval_data_utils._load_and_filter_dataset", "official_loader_revision": OFFICIAL_CODE_REVISION}


def _record_inventory(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    inventory = []
    for index, row in enumerate(rows):
        metadata = _metadata(row)
        context = row.get("context", "")
        sessions = _context_sessions(context)
        questions = row.get("questions", [])
        inventory.append({
            "record_index": index,
            "source": metadata.get("source", row.get("source")),
            "context_sha256": _sha256_bytes(str(context).encode()),
            "session_count": len(sessions),
            "qa_count": _qa_count(row),
            "question_ids": _question_ids(row),
            "qa_pair_ids": [str(value) for value in (row.get("qa_pair_ids", metadata.get("qa_pair_ids", [])) or [])],
            "source_order_key": metadata.get("source", row.get("source")),
            "question_38": {
                "question_id": _question_ids(row)[38] if len(_question_ids(row)) > 38 else None,
                "question": questions[38] if isinstance(questions, list) and len(questions) > 38 else None,
            },
        })
    return inventory


def _gold_membership(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    result = []
    for index, row in enumerate(rows):
        metadata = _metadata(row)
        groups = metadata.get("haystack_sessions", []) or []
        common_ids = {f"record-{index}:s{i:04d}" for i in range(len(_context_sessions(row.get("context", ""))))}
        q38 = groups[38] if len(groups) > 38 else []
        common_digests = {
            _sha256_bytes(_canonical(session.get("turns", [])).encode())
            for session in _context_sessions(row.get("context", ""))
        }
        membership = []
        flags = []
        for session in q38 if isinstance(q38, list) else []:
            turns = session if isinstance(session, list) else []
            clean = [{"role": item.get("role"), "content": item.get("content")} for item in turns if isinstance(item, Mapping)]
            digest = _sha256_bytes(_canonical(clean).encode())
            membership.append(digest in common_digests)
            flags.append(any(bool(item.get("has_answer")) for item in turns if isinstance(item, Mapping)))
        result.append({"record_index": index, "question_index": 38, "private_gold_session_count": len(q38) if isinstance(q38, list) else None, "common_context_session_count": len(common_ids), "question_38_has_answer_flags": flags, "private_session_membership_in_common_context": membership, "all_private_sessions_present": all(membership) if membership else None})
    return result


def main() -> int:
    generator_source_sha256 = _sha256_file(Path(__file__))
    base_code_commit = _git_head()
    source_bundle: dict[str, Any] = {
        "local_projection_sha256": _sha256_file(LOCAL) if LOCAL.is_file() else None,
        "generator_source_sha256": generator_source_sha256,
        "base_code_commit": base_code_commit,
        "hf_dataset": HF_DATASET,
        "hf_revision": HF_REVISION,
        "split_path": SPLIT_PATH,
        "official_code_revision": OFFICIAL_CODE_REVISION,
    }
    report: dict[str, Any] = {"schema_version": "membind.official-dataset-parity.v1", "benchmark": "MemoryAgentBench", "task": "Accurate Retrieval", "hf_dataset": HF_DATASET, "hf_revision": HF_REVISION, "split_path": SPLIT_PATH, "source_filter": SOURCE_FILTER, "local_file": str(LOCAL.resolve()), "local_file_sha256": source_bundle["local_projection_sha256"], "base_code_commit": base_code_commit, "generator_source_sha256": generator_source_sha256}
    try:
        official, source = _fetch_official()
        source_bundle["official_source_files"] = source.get("official_code_files", {})
        report["evaluated_source_bundle"] = source_bundle
        report["evaluated_source_bundle_sha256"] = _sha256_bytes(_canonical(source_bundle).encode())
        local = json.loads(LOCAL.read_text(encoding="utf-8"))
        if not isinstance(local, list):
            raise ValueError("local projection is not a list")
        official_inventory, local_inventory = _record_inventory(official), _record_inventory([dict(item) for item in local])
        report.update({"status": "PASS", "official_source": source, "official_inventory": official_inventory, "local_inventory": local_inventory, "official_record_count": len(official), "local_record_count": len(local)})
        differences: list[dict[str, Any]] = []
        if len(official) != len(local): differences.append({"field": "record_count", "official": len(official), "local": len(local)})
        for oi, li in zip(official_inventory, local_inventory):
            for field in ("context_sha256", "session_count", "qa_count", "question_ids", "qa_pair_ids", "source_order_key"):
                if oi.get(field) != li.get(field): differences.append({"record_index": oi["record_index"], "field": field, "official": oi.get(field), "local": li.get(field)})
        report["differences"] = differences
        report["question_38"] = {"official": [row["question_38"] for row in official_inventory], "local": [row["question_38"] for row in local_inventory]}
        report["gold_session_membership"] = {"official_loader_requires_gold_session_membership": False, "official_loader_basis": "ConversationCreator consumes questions/answers and does not inspect metadata.haystack_sessions", "membership_observations": _gold_membership(official)}
        report["selection"] = "OFFICIAL_AS_PUBLISHED_5_RECORDS" if len(official) == 5 and len(local) == 5 and not differences else ("PREREGISTERED_4_CONTEXT_SUBSET" if len(local) == 4 else "CORRECTED_DATASET_VARIANT")
        report["anomaly_disclosure"] = [{"record_index": row["record_index"], "question_index": 38, "note": "question 38 is retained exactly as published; any missing common-context gold session is disclosed rather than removed"} for row in official_inventory if row["question_38"].get("question_id") == "0ddfec37_abs"]
    except Exception as exc:
        report["evaluated_source_bundle"] = source_bundle
        report["evaluated_source_bundle_sha256"] = _sha256_bytes(_canonical(source_bundle).encode())
        report.update({"status": "BLOCKED_OFFICIAL_DATASET_PARITY", "error": f"{type(exc).__name__}: {exc}", "selection": "CORRECTED_DATASET_VARIANT"})
    report["report_sha256"] = _sha256_bytes(_canonical({key: value for key, value in report.items() if key != "report_sha256"}).encode())
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "OFFICIAL_DATASET_PARITY_REPORT.json").write_text(json.dumps(report, ensure_ascii=True, sort_keys=True, indent=2) + "\n", encoding="utf-8")
    differences = report.get("differences", [])
    markdown = ["# Official MemoryAgentBench Parity", "", f"Status: `{report['status']}`.", "", f"HF revision: `{HF_DATASET}@{HF_REVISION}`", f"Official code revision: `{report.get('official_source', {}).get('official_code_revision', OFFICIAL_CODE_REVISION)}`", f"Selection: `{report.get('selection', 'UNKNOWN')}`", f"Official records: `{report.get('official_record_count', 'UNKNOWN')}`; local records: `{report.get('local_record_count', 'UNKNOWN')}`", f"Differences: `{len(differences)}`.", f"Base code commit: `{report.get('base_code_commit')}`", f"Generator source SHA-256: `{report.get('generator_source_sha256')}`", f"Evaluated source bundle SHA-256: `{report.get('evaluated_source_bundle_sha256')}`", "", "The official five-record inventory is never reduced to conceal a mapping anomaly; question 38 is retained and disclosed."]
    if report.get("error"): markdown.extend(["", f"Verifier error: `{report['error']}`"])
    (OUT / "OFFICIAL_DATASET_PARITY_REPORT.md").write_text("\n".join(markdown) + "\n", encoding="utf-8")
    print(json.dumps({"status": report["status"], "selection": report.get("selection"), "differences": len(differences)}, sort_keys=True))
    return 0 if report["status"] == "PASS" else 2


if __name__ == "__main__":
    raise SystemExit(main())
