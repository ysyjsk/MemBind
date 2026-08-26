"""Content-free diagnostics for temporary-provider schema failures."""

from __future__ import annotations

import inspect
from typing import Any, Callable, Mapping

from .engineering_observer_runtime import CompositeEngineeringError


_PYDANTIC_FAILURE = "Graphiti response failed Pydantic validation"
_SCOPE_FIELDS = (
    "phase",
    "source_sequence",
    "state_version",
    "request_ordinal",
    "prompt_name",
)


class DevelopmentSchemaOutputError(RuntimeError):
    """A provider response failed its declared model; only safe metadata remains."""

    def __init__(self, diagnostic: Mapping[str, Any]) -> None:
        super().__init__("temporary provider response failed declared schema")
        self.diagnostic = dict(diagnostic)


def _response_model_identity(value: Any) -> str | None:
    if value is None:
        return None
    module = getattr(value, "__module__", None)
    qualname = getattr(value, "__qualname__", None)
    if isinstance(module, str) and isinstance(qualname, str):
        return f"{module}.{qualname}"
    return type(value).__module__ + "." + type(value).__qualname__


def _safe_validation_errors(error: BaseException | None) -> list[dict[str, Any]]:
    errors = getattr(error, "errors", None)
    if not callable(errors):
        return []
    try:
        rows = errors(
            include_url=False,
            include_context=False,
            include_input=False,
        )
    except TypeError:
        rows = errors()
    result: list[dict[str, Any]] = []
    for row in rows if isinstance(rows, list) else ():
        if not isinstance(row, Mapping):
            continue
        location = row.get("loc")
        safe_location = [
            item if isinstance(item, int) and not isinstance(item, bool) else str(item)
            for item in (location if isinstance(location, tuple | list) else ())
        ]
        error_type = row.get("type")
        result.append(
            {
                "location": safe_location,
                "type": str(error_type) if error_type is not None else "unknown",
            }
        )
    return result


def install_development_schema_diagnostics(
    client: Any,
    *,
    scope_reader: Callable[[], Mapping[str, Any] | None],
) -> None:
    """Wrap one validated Graphiti client without changing its decision."""

    original = getattr(client, "generate_response", None)
    if not callable(original) or not callable(scope_reader):
        raise ValueError("development schema diagnostic target is invalid")
    if getattr(client, "_membind_v7_schema_diagnostics_installed", False):
        raise ValueError("development schema diagnostics already installed")

    async def generate_response(*args: Any, **kwargs: Any) -> Any:
        try:
            result = original(*args, **kwargs)
            return await result if inspect.isawaitable(result) else result
        except CompositeEngineeringError as error:
            if str(error) != _PYDANTIC_FAILURE:
                raise
            response_model = kwargs.get("response_model")
            if response_model is None and len(args) >= 2:
                response_model = args[1]
            scope = scope_reader()
            safe_scope = {
                field: scope.get(field)
                for field in _SCOPE_FIELDS
                if isinstance(scope, Mapping) and field in scope
            }
            errors = _safe_validation_errors(error.__cause__)
            raise DevelopmentSchemaOutputError(
                {
                    "schema_version": "membind.v7.development-schema-diagnostic.v1",
                    **safe_scope,
                    "response_model": _response_model_identity(response_model),
                    "validation_error_count": len(errors),
                    "validation_errors": errors,
                    "validation_input_persisted": False,
                    "raw_request_persisted": False,
                    "raw_response_persisted": False,
                    "raw_embedding_persisted": False,
                }
            ) from error

    client.generate_response = generate_response
    client._membind_v7_schema_diagnostics_installed = True


def augment_development_failure(
    failure: Mapping[str, Any], error: BaseException
) -> dict[str, Any]:
    """Add schema identity to an already sanitized failed-attempt artifact."""

    result = dict(failure)
    if not isinstance(error, DevelopmentSchemaOutputError):
        return result
    result.update(
        {
            "failure_class": "INFRASTRUCTURE_PROVIDER_STRUCTURED_OUTPUT_INVALID",
            "provider_schema_diagnostic": dict(error.diagnostic),
            "raw_request_persisted": False,
            "raw_response_persisted": False,
            "raw_embedding_persisted": False,
            "credentials_recorded": False,
        }
    )
    return result


__all__ = [
    "DevelopmentSchemaOutputError",
    "augment_development_failure",
    "install_development_schema_diagnostics",
]
