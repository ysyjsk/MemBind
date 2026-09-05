"""One-episode reproduction of the documented Graphiti/Ollama shape."""

from __future__ import annotations

import asyncio
import json
import time

from membind.native import GraphitiEpisode, GraphitiNative


async def run(model: str = "qwen2.5:14b", max_tokens: int = 2048, structured_output_mode: str = "json_schema") -> None:
    from graphiti_core import Graphiti
    from graphiti_core.cross_encoder.openai_reranker_client import OpenAIRerankerClient
    from graphiti_core.embedder.openai import OpenAIEmbedder, OpenAIEmbedderConfig
    from graphiti_core.llm_client.config import LLMConfig
    from graphiti_core.llm_client.openai_generic_client import OpenAIGenericClient

    base_url = "http://127.0.0.1:11434/v1"
    config = LLMConfig(
        api_key="ollama",
        model=model,
        small_model=model,
        base_url=base_url,
        temperature=0,
    )
    llm = OpenAIGenericClient(config=config, max_tokens=max_tokens, structured_output_mode=structured_output_mode)
    embedder = OpenAIEmbedder(
        config=OpenAIEmbedderConfig(
            api_key="ollama",
            embedding_model="nomic-embed-text",
            embedding_dim=768,
            base_url=base_url,
        )
    )
    reranker = OpenAIRerankerClient(client=llm, config=config)
    graphiti = Graphiti(
        "bolt://127.0.0.1:7687",
        "neo4j",
        "password",
        llm_client=llm,
        embedder=embedder,
        cross_encoder=reranker,
        max_coroutines=2,
    )
    await graphiti.build_indices_and_constraints(delete_existing=False)
    group_id = "clean-repro-" + time.strftime("%Y%m%dT%H%M%SZ")
    result = await GraphitiNative(graphiti).add_episode(
        GraphitiEpisode(
            name=group_id,
            body="[USER]\nI moved to Berlin in 2024.\n[ASSISTANT]\nUnderstood.",
            source_description="clean_membind external reproduction",
            reference_time="2025-01-01T00:00:00Z",
            group_id=group_id,
        )
    )
    print(json.dumps({"status": "PASS", "group_id": group_id, "result_type": type(result).__name__}, sort_keys=True))
    await graphiti.close()


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--model", default="qwen2.5:14b")
    parser.add_argument("--max-tokens", type=int, default=2048)
    parser.add_argument("--structured-output-mode", choices=("json_schema", "json_object"), default="json_schema")
    args = parser.parse_args()
    asyncio.run(run(args.model, args.max_tokens, args.structured_output_mode))
