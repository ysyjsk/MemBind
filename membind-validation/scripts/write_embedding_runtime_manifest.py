#!/usr/bin/env python3
"""Persist the operator fingerprint and non-secret embedding identity evidence."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from embedding_identity import (  # noqa: E402
    build_operator_fingerprint_manifest,
    write_embedding_model_manifest,
)


FINGERPRINT = "5f5a8400eeaa2f07d167d8b5b7e63d615945a8f54f506e02342840cd4e3fe626"
MODEL = "qwen3-embedding-0.6b"
HF_REVISION = "97b0c614be4d77ee51c0cef4e5f07c00f9eb65b3"


def _read_endpoint_observation(artifacts: Path) -> dict[str, object]:
    probe_path = artifacts / "environment" / "embedding_identity_probe.json"
    if not probe_path.is_file():
        raise FileNotFoundError(f"missing endpoint identity probe: {probe_path}")
    payload = json.loads(probe_path.read_text(encoding="utf-8"))
    return {
        "served_model_id": payload.get("served_model_id"),
        "reported_revision": payload.get("endpoint_reported_revision"),
        "reported_model_root": payload.get("reported_model_root"),
        "reported_max_model_len": payload.get("reported_max_model_len"),
        "vllm_version": payload.get("vllm_version"),
        "probe_artifact": "artifacts/environment/embedding_identity_probe.json",
    }


def build_manifest(artifacts: Path, *, dtype: str | None = None) -> dict[str, object]:
    endpoint = _read_endpoint_observation(artifacts)
    dtype_value = dtype or "unresolved"
    dtype_status = "deployment_config_verified" if dtype else "unresolved"
    runtime_evidence = "artifacts/environment/embedding_runtime_dtype_evidence_20260808.json"
    namespace = {
        "schema_version": "membind.embedding_oracle.v1",
        "served_model_id": MODEL,
        "identity_kind": "deployment_fingerprint",
        "identity_value": FINGERPRINT,
        "dimension": 1024,
        "dtype": dtype_value,
        "pooling": "last_token",
        "normalization": "l2",
        "instruction_policy": "none",
        "input_transform": "utf8_exact_v1",
        "tokenizer_fingerprint": None,
        "model_fingerprint": FINGERPRINT,
    }
    field_evidence = {
        "served_model_id": {
            "value": MODEL,
            "status": "endpoint_observed",
            "source": "artifacts/environment/embedding_identity_probe.json",
        },
        "identity_value": {
            "value": FINGERPRINT,
            "status": "operator_asserted",
            "source": "operator-supplied deployment-directory SHA256",
        },
        "dimension": {
            "value": 1024,
            "status": "runtime_contract_observed",
            "source": "artifacts/environment/manifest.json and retained vectors",
        },
        "dtype": {
            "value": dtype if dtype else None,
            "status": dtype_status,
            "source": (
                runtime_evidence
                if dtype
                else "missing remote argv, startup log, or deployed config evidence"
            ),
            **({} if dtype else {"candidate_values": ["float16", "bfloat16"]}),
        },
        "pooling": {
            "value": "last_token",
            "status": "deployment_config_verified" if dtype else "external_checkpoint_reference",
            "source": (
                runtime_evidence
                if dtype
                else "Qwen/Qwen3-Embedding-0.6B checkpoint reference at "
                + HF_REVISION
                + ": 1_Pooling/config.json"
            ),
        },
        "normalization": {
            "value": "l2",
            "status": "deployment_config_verified" if dtype else "retained_behavior_and_external_reference",
            "source": (
                runtime_evidence
                if dtype
                else "retained unit-norm vectors plus Qwen checkpoint modules.json "
                "Normalize reference at " + HF_REVISION
            ),
        },
        "instruction_policy": {
            "value": "none",
            "status": "deployment_config_and_client_code_verified" if dtype else "client_code_verified",
            "source": (
                runtime_evidence
                + "; src/graphiti_native.py and installed Graphiti OpenAIEmbedder.create"
                if dtype
                else "src/graphiti_native.py and installed Graphiti OpenAIEmbedder.create"
            ),
        },
        "input_transform": {
            "value": "utf8_exact_v1",
            "status": "client_code_verified",
            "source": "src/embedding_cache.py exact UTF-8 item key",
        },
    }
    return build_operator_fingerprint_manifest(
        operator_fingerprint=FINGERPRINT,
        namespace=namespace,
        field_evidence=field_evidence,
        endpoint_observation=endpoint,
    )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--artifacts",
        type=Path,
        default=ROOT / "artifacts",
    )
    parser.add_argument(
        "--dtype",
        help="only pass this after verifying the actual remote launch/config evidence",
    )
    args = parser.parse_args()
    manifest = build_manifest(args.artifacts, dtype=args.dtype)
    output = args.artifacts / "environment" / "embedding_model_fingerprint.json"
    written = write_embedding_model_manifest(manifest, output)
    print(
        json.dumps(
            {
                "path": str(output),
                "gate_status": written["gate_status"],
                "unresolved_fields": written["unresolved_fields"],
                "namespace_sha256": written["namespace_sha256"],
            },
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
