"""Explicit project credential parsing for the gated Protocol v1.3 H0 path.

The loader deliberately does not call ``dotenv`` and never writes values into
``os.environ``.  Its caller must pass the machine-state gate before invoking it.
Only the returned in-memory mapping contains secrets.
"""

from __future__ import annotations

from h0_bootstrap import disable_implicit_dotenv

disable_implicit_dotenv()

from pathlib import Path
from typing import Any, Mapping

from h0_runtime import H0ManifestError


H0_EMBEDDING_BASE_URL = "http://10.87.5.247:8001/v1/"
H0_NEO4J_URI = "bolt://localhost:7687"
H0_NEO4J_USER = "neo4j"
H0_NEO4J_DATABASE = "neo4j"


def parse_h0_project_env(path: str | Path) -> dict[str, str]:
    """Parse one explicit project file without exporting any value."""

    path = Path(path)
    if path.is_symlink() or not path.is_file():
        raise H0ManifestError("project credential file is missing or invalid")
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeError):
        raise H0ManifestError("project credential file is unreadable") from None
    loaded: dict[str, str] = {}
    for raw_line in lines:
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if "=" not in line:
            raise H0ManifestError("project credential file has an invalid line")
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip()
        if not key:
            raise H0ManifestError("project credential file has an invalid key")
        if key in loaded:
            raise H0ManifestError("project credential file has a duplicate key")
        if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
            value = value[1:-1]
        loaded[key] = value
    return loaded


class H0ProjectCredentialLoader:
    """Load all H0 service credentials and enforce their public bindings."""

    def __init__(self, *, root: str | Path, definition: Any) -> None:
        self.root = Path(root).resolve()
        self.definition = definition

    def __call__(self) -> dict[str, dict[str, str]]:
        loaded = parse_h0_project_env(self.root / ".env")
        identity = getattr(self.definition, "identity", None)
        namespace = getattr(self.definition, "embedding_namespace", None)
        if not isinstance(identity, Mapping) or not isinstance(namespace, Mapping):
            raise H0ManifestError("authorized runtime definition is incomplete")

        shared_key = loaded.get("VLLM_API_KEY")
        construction_key = loaded.get("CONSTRUCTION_LLM_API_KEY") or shared_key
        embedding_key = loaded.get("EMBEDDING_API_KEY") or shared_key
        construction_base = loaded.get("CONSTRUCTION_LLM_BASE_URL")
        construction_model = loaded.get("CONSTRUCTION_LLM_MODEL")
        embedding_base = str(loaded.get("EMBEDDING_BASE_URL") or "").rstrip("/") + "/"
        embedding_model = loaded.get("EMBEDDING_MODEL")
        neo4j_uri = loaded.get("NEO4J_URI")
        neo4j_user = loaded.get("NEO4J_USER")
        neo4j_password = loaded.get("NEO4J_PASSWORD")
        neo4j_database = loaded.get("NEO4J_DATABASE", H0_NEO4J_DATABASE)

        exact_public_bindings = (
            construction_base == identity.get("base_url")
            and construction_model == identity.get("served_model_id")
            and embedding_base == H0_EMBEDDING_BASE_URL
            and embedding_model == namespace.get("served_model_id")
            and neo4j_uri == H0_NEO4J_URI
            and neo4j_user == H0_NEO4J_USER
            and neo4j_database == H0_NEO4J_DATABASE
        )
        if not exact_public_bindings:
            raise H0ManifestError("project service configuration differs from bindings")
        if not all(
            isinstance(value, str) and bool(value)
            for value in (construction_key, embedding_key, neo4j_password)
        ):
            raise H0ManifestError("project credential file lacks required credentials")

        return {
            "construction": {
                "base_url": construction_base,
                "api_key": construction_key,
            },
            "embedding": {
                "base_url": embedding_base,
                "model": embedding_model,
                "api_key": embedding_key,
            },
            "neo4j": {
                "uri": neo4j_uri,
                "user": neo4j_user,
                "password": neo4j_password,
                "database": neo4j_database,
            },
        }
