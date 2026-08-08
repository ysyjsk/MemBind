"""Local BGE-M3 embedding adapter for the temporary GPT-5.5 lane.

The adapter intentionally lives under gpt55_temporary/ so the short-lived GPT
diagnostic can use a local SentenceTransformer without changing the frozen
mainline vLLM/embedding implementation in src/.
"""

from __future__ import annotations

import argparse
import asyncio
import importlib
import importlib.metadata
import json
import math
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

from graphiti_core.embedder.client import EmbedderClient


BGE_M3_MODEL = "BAAI/bge-m3"
BGE_M3_REVISION = "5617a9f61b028005a4858fdac845db406aefb181"
BGE_M3_DIMENSION = 1024
DEFAULT_HF_HUB_CACHE = Path("/data/predator/ly/Mem/cache/huggingface/hub")


@dataclass(frozen=True)
class LocalBgeM3Config:
    """Small config shim matching the fields Graphiti/cache wrappers inspect."""

    embedding_model: str = BGE_M3_MODEL
    embedding_dim: int = BGE_M3_DIMENSION
    revision: str = BGE_M3_REVISION
    cache_folder: str = str(DEFAULT_HF_HUB_CACHE)
    normalize_embeddings: bool = True
    local_files_only: bool = True
    trust_remote_code: bool = False
    use_safetensors: bool = False


def _import_torch() -> Any:
    return importlib.import_module("torch")


def _import_sentence_transformer_cls() -> Any:
    module = importlib.import_module("sentence_transformers")
    return module.SentenceTransformer


def _cuda_available(torch_module: Any) -> bool:
    cuda = getattr(torch_module, "cuda", None)
    available = getattr(cuda, "is_available", None)
    return bool(callable(available) and available())


def _vector_as_list(vector: Any) -> list[float]:
    if hasattr(vector, "tolist"):
        vector = vector.tolist()
    return [float(value) for value in vector]


def _normalize(vector: list[float]) -> list[float]:
    norm = math.sqrt(sum(value * value for value in vector))
    if norm <= 0:
        raise ValueError("embedding model returned a zero vector")
    return [value / norm for value in vector]


def _validate_vectors(vectors: Iterable[Any], *, dimension: int) -> list[list[float]]:
    validated: list[list[float]] = []
    for vector in vectors:
        values = _normalize(_vector_as_list(vector))
        if len(values) != dimension:
            raise ValueError(
                f"local BGE-M3 embedding dimension mismatch: expected {dimension}, got {len(values)}"
            )
        validated.append(values)
    return validated


def _texts_for_create(input_data: Any) -> list[str]:
    if isinstance(input_data, str):
        return [input_data]
    if isinstance(input_data, list) and input_data and all(
        isinstance(item, str) for item in input_data
    ):
        return list(input_data)
    raise TypeError("local BGE-M3 create() expects a string or non-empty list[str]")


class LocalBgeM3Embedder(EmbedderClient):
    """Graphiti EmbedderClient backed by an offline local BGE-M3 model."""

    def __init__(
        self,
        *,
        model: str = BGE_M3_MODEL,
        revision: str = BGE_M3_REVISION,
        cache_folder: str | Path = DEFAULT_HF_HUB_CACHE,
        dimension: int = BGE_M3_DIMENSION,
        batch_size: int = 32,
        sentence_transformer_cls: Any | None = None,
        torch_module: Any | None = None,
    ) -> None:
        self.model = str(model)
        self.revision = str(revision)
        self.cache_folder = Path(cache_folder)
        self.dimension = int(dimension)
        self.batch_size = int(batch_size)
        self._sentence_transformer_cls = sentence_transformer_cls
        self._torch = torch_module
        self._encoder: Any | None = None
        self.config = LocalBgeM3Config(
            embedding_model=self.model,
            embedding_dim=self.dimension,
            revision=self.revision,
            cache_folder=str(self.cache_folder),
        )

    def _load_encoder(self) -> Any:
        if self._encoder is not None:
            return self._encoder
        torch_module = self._torch if self._torch is not None else _import_torch()
        sentence_transformer_cls = (
            self._sentence_transformer_cls
            if self._sentence_transformer_cls is not None
            else _import_sentence_transformer_cls()
        )
        device = "cuda" if _cuda_available(torch_module) else "cpu"
        model_kwargs: dict[str, Any] = {"use_safetensors": False}
        if device == "cuda":
            model_kwargs["torch_dtype"] = torch_module.float16

        self._encoder = sentence_transformer_cls(
            self.model,
            cache_folder=str(self.cache_folder.resolve()),
            revision=self.revision,
            device=device,
            trust_remote_code=False,
            local_files_only=True,
            model_kwargs=model_kwargs,
            tokenizer_kwargs={"local_files_only": True},
            config_kwargs={"local_files_only": True},
        )
        return self._encoder

    def _encode_sync(self, texts: list[str]) -> list[list[float]]:
        encoder = self._load_encoder()
        vectors = encoder.encode(
            texts,
            batch_size=self.batch_size,
            normalize_embeddings=True,
            convert_to_numpy=True,
            show_progress_bar=False,
        )
        validated = _validate_vectors(vectors, dimension=self.dimension)
        if len(validated) != len(texts):
            raise RuntimeError("local BGE-M3 encode() returned the wrong number of vectors")
        return validated

    async def create(self, input_data: str | list[str] | Iterable[int] | Iterable[Iterable[int]]) -> list[float]:
        texts = _texts_for_create(input_data)
        vectors = await asyncio.to_thread(self._encode_sync, texts)
        return vectors[0]

    def create_batch_sync(self, input_data_list: list[str]) -> list[list[float]]:
        if not input_data_list:
            return []
        if any(not isinstance(value, str) for value in input_data_list):
            raise TypeError("local BGE-M3 create_batch_sync() expects list[str]")
        return self._encode_sync(list(input_data_list))

    async def create_batch(self, input_data_list: list[str]) -> list[list[float]]:
        if not input_data_list:
            return []
        if any(not isinstance(value, str) for value in input_data_list):
            raise TypeError("local BGE-M3 create_batch() expects list[str]")
        return await asyncio.to_thread(self._encode_sync, list(input_data_list))


def probe_local_embedding(
    *,
    output: str | Path | None = None,
    model: str = BGE_M3_MODEL,
    revision: str = BGE_M3_REVISION,
    cache_folder: str | Path = DEFAULT_HF_HUB_CACHE,
    dimension: int = BGE_M3_DIMENSION,
    batch_size: int = 2,
    require_cuda: bool = True,
    sentence_transformer_cls: Any | None = None,
    torch_module: Any | None = None,
) -> dict[str, Any]:
    """Run a small local embedding preflight and optionally persist the result."""

    artifact: dict[str, Any] = {
        "schema_version": "membind.gpt55_temporary.local_embedding_preflight.v1",
        "created_at_unix": time.time(),
        "ok": False,
        "status": "blocked",
        "provider": "local_bge_m3",
        "model": model,
        "revision": revision,
        "cache_folder": str(Path(cache_folder).resolve()),
        "dimension": int(dimension),
        "normalize_embeddings": True,
        "local_files_only": True,
        "trust_remote_code": False,
        "use_safetensors": False,
        "device": None,
        "torch_version": None,
        "sentence_transformers_version": None,
        "device_name": None,
        "model_parameter_dtype": None,
        "pooling_policy": "model_defined",
        "normalization_policy": "encode(normalize_embeddings=True)+adapter_defensive_l2",
        "precision_policy": "fp16_on_cuda_else_model_default",
        "vector_norms": [],
        "latency_seconds": None,
        "reason": None,
    }
    try:
        torch_mod = torch_module if torch_module is not None else _import_torch()
        artifact["torch_version"] = str(getattr(torch_mod, "__version__", "unknown"))
        cuda_available = _cuda_available(torch_mod)
        artifact["device"] = "cuda" if cuda_available else "cpu"
        if cuda_available:
            get_device_name = getattr(getattr(torch_mod, "cuda", None), "get_device_name", None)
            if callable(get_device_name):
                artifact["device_name"] = str(get_device_name(0))
        if require_cuda and not cuda_available:
            artifact["reason"] = "CUDA is required for the local BGE-M3 temporary lane"
            return _persist_probe(output, artifact)
        st_cls = (
            sentence_transformer_cls
            if sentence_transformer_cls is not None
            else _import_sentence_transformer_cls()
        )
        artifact["sentence_transformers_version"] = str(
            getattr(st_cls, "package_version", None)
            or importlib.metadata.version("sentence-transformers")
        )
        started = time.monotonic()
        embedder = LocalBgeM3Embedder(
            model=model,
            revision=revision,
            cache_folder=cache_folder,
            dimension=dimension,
            batch_size=batch_size,
            sentence_transformer_cls=st_cls,
            torch_module=torch_mod,
        )
        vectors = embedder.create_batch_sync(["alpha project", "beta recipe"])
        try:
            first_parameter = next(iter(embedder._load_encoder().parameters()))
            artifact["model_parameter_dtype"] = str(getattr(first_parameter, "dtype", None))
        except (AttributeError, StopIteration, TypeError):
            artifact["model_parameter_dtype"] = None
        artifact["latency_seconds"] = time.monotonic() - started
        artifact["vector_norms"] = [
            math.sqrt(sum(value * value for value in vector)) for vector in vectors
        ]
        artifact["ok"] = True
        artifact["status"] = "passed"
        return _persist_probe(output, artifact)
    except Exception as exc:
        artifact["reason"] = f"{type(exc).__name__}: {exc}"
        return _persist_probe(output, artifact)


def _persist_probe(output: str | Path | None, artifact: dict[str, Any]) -> dict[str, Any]:
    if output is not None:
        path = Path(output)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(artifact, ensure_ascii=True, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        artifact["artifact"] = str(path)
    return artifact


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", default="gpt55_temporary/artifacts/diagnostics/local_embedding_preflight.json")
    parser.add_argument("--model", default=BGE_M3_MODEL)
    parser.add_argument("--revision", default=BGE_M3_REVISION)
    parser.add_argument("--cache-folder", default=str(DEFAULT_HF_HUB_CACHE))
    parser.add_argument("--dimension", type=int, default=BGE_M3_DIMENSION)
    parser.add_argument("--batch-size", type=int, default=2)
    parser.add_argument("--allow-cpu", action="store_true")
    return parser


def main() -> None:
    args = _parser().parse_args()
    result = probe_local_embedding(
        output=args.output,
        model=args.model,
        revision=args.revision,
        cache_folder=args.cache_folder,
        dimension=args.dimension,
        batch_size=args.batch_size,
        require_cuda=not args.allow_cpu,
    )
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    if not result.get("ok"):
        raise SystemExit(1)


if __name__ == "__main__":
    main()
