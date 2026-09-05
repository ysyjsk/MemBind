"""Run a real frozen MAB construction prefix with the clean Native boundary.

The legacy dataset loader is used only as a source-format reader here; the
construction call and episode mapping are clean-mainline code.  The loader is
not imported by ``membind.core`` or by the formal runner.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import time
from pathlib import Path

from membind.native import GraphitiEpisode, GraphitiNative


ROOT = Path(__file__).resolve().parents[2]
DATASET = ROOT / "mab_quality_v2_final_qa" / "data" / "official_5_contexts.json"


def _episode(chunk: object, group_id: str) -> GraphitiEpisode:
    return GraphitiEpisode(
        name=f"{group_id}::episode::{int(chunk.global_sequence):04d}",
        body=str(chunk.body),
        source_description="MemoryAgentBench LongMemEval MAB_ROLE_AWARE_LOSSLESS_8192_V1",
        reference_time=str(chunk.reference_time),
        uuid=str(chunk.chunk_id),
        group_id=group_id,
    )


async def run(context_index: int, max_source_sequence: int | None, max_global_sequence: int | None, smoke_count: int | None, max_tokens: int, output: Path) -> None:
    from mab_quality_v2_final_qa.mab_main_dataset import load_main_contexts
    from mab_quality_v2_final_qa.mab8192_adapter import MAB8192Manifest
    from graphiti_core import Graphiti
    from graphiti_core.cross_encoder.openai_reranker_client import OpenAIRerankerClient
    from graphiti_core.embedder.openai import OpenAIEmbedder, OpenAIEmbedderConfig
    from graphiti_core.llm_client.config import LLMConfig
    from graphiti_core.llm_client.openai_generic_client import OpenAIGenericClient

    context = load_main_contexts(DATASET)[context_index]
    manifest = MAB8192Manifest.from_context(context, dataset_revision="hf:ai-hyz/MemoryAgentBench@7ea066982b140a19337e17e60d45d4076e042faf")
    chunks = list(manifest.chunks)
    if max_source_sequence is not None:
        chunks = [chunk for chunk in chunks if chunk.source_sequence <= max_source_sequence]
    if max_global_sequence is not None:
        chunks = [chunk for chunk in chunks if chunk.global_sequence <= max_global_sequence]
    if smoke_count is not None:
        chunks = chunks[:smoke_count]
    if not chunks:
        raise ValueError("selected workload is empty")

    base_url = "http://127.0.0.1:11434/v1"
    config = LLMConfig(api_key="ollama", model="qwen2.5:14b", small_model="qwen2.5:14b", base_url=base_url, temperature=0)
    llm = OpenAIGenericClient(config=config, max_tokens=max_tokens, structured_output_mode="json_schema")
    embedder = OpenAIEmbedder(config=OpenAIEmbedderConfig(api_key="ollama", embedding_model="nomic-embed-text", embedding_dim=768, base_url=base_url))
    reranker = OpenAIRerankerClient(client=llm, config=config)
    graphiti = Graphiti("bolt://127.0.0.1:7687", "neo4j", "password", llm_client=llm, embedder=embedder, cross_encoder=reranker, max_coroutines=2)
    await graphiti.build_indices_and_constraints(delete_existing=False)
    group_id = f"clean-mab-c{context_index}-{time.strftime('%Y%m%dT%H%M%SZ')}"
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("w", encoding="utf-8") as stream:
        stream.write(json.dumps({"event": "START", "context_id": context.context_id, "context_index": context_index, "group_id": group_id, "chunk_count": len(chunks), "adapter": "MAB_ROLE_AWARE_LOSSLESS_8192_V1", "workload_sha256": hashlib.sha256(manifest.jsonl().encode()).hexdigest()}, sort_keys=True) + "\n")
        stream.flush()
        try:
            for chunk in chunks:
                started = time.time_ns()
                await GraphitiNative(graphiti).add_episode(_episode(chunk, group_id))
                stream.write(json.dumps({"event": "PUBLICATION_DURABLE", "source_sequence": chunk.source_sequence, "global_sequence": chunk.global_sequence, "chunk_id": chunk.chunk_id, "elapsed_ns": time.time_ns() - started}, sort_keys=True) + "\n")
                stream.flush()
        except BaseException as exc:
            stream.write(json.dumps({"event": "FAILURE", "error_type": type(exc).__name__, "error": str(exc)}, sort_keys=True) + "\n")
            stream.flush()
            raise
        else:
            stream.write(json.dumps({"event": "COMPLETE", "source_sequence_max": max(chunk.source_sequence for chunk in chunks), "chunk_count": len(chunks)}, sort_keys=True) + "\n")
            stream.flush()
    await graphiti.close()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--context-index", type=int, default=0)
    parser.add_argument("--max-source-sequence", type=int)
    parser.add_argument("--max-global-sequence", type=int)
    parser.add_argument("--smoke-count", type=int)
    parser.add_argument("--max-tokens", type=int, default=2048)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    asyncio.run(run(args.context_index, args.max_source_sequence, args.max_global_sequence, args.smoke_count, args.max_tokens, args.output))


if __name__ == "__main__":
    main()
