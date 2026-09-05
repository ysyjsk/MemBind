import pytest

from membind.backends import Qwen35BackendConfig, ReasoningDisabledOpenAIClient


def test_qwen35_backend_identity_is_explicit_and_deterministic():
    config = Qwen35BackendConfig()
    assert config.model == "qwen3.5:latest"
    assert config.reasoning_effort == "none"
    assert config.identity_sha256 == Qwen35BackendConfig().identity_sha256


class _FakeCompletions:
    def __init__(self):
        self.kwargs = None

    async def create(self, **kwargs):
        self.kwargs = kwargs
        return {"ok": True}


class _FakeClient:
    def __init__(self, completions):
        self.chat = type("Chat", (), {"completions": completions})()


@pytest.mark.asyncio
async def test_transport_injects_only_reasoning_effort():
    completions = _FakeCompletions()
    client = ReasoningDisabledOpenAIClient(_FakeClient(completions))
    result = await client.chat.completions.create(model="m", messages=[], extra_body={"other": 1})
    assert result == {"ok": True}
    assert completions.kwargs["extra_body"] == {"other": 1, "reasoning_effort": "none"}
