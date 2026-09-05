from membind.backends import Mistral32BackendConfig


def test_mistral32_backend_identity_is_explicit_and_deterministic():
    config = Mistral32BackendConfig()
    assert config.model == "mistral-small3.2:24b-instruct-2506-q4_K_M"
    assert config.graphiti_commit == "021d3a57d511f21b10adaf7fa923bd5c1fce5e9d"
    assert config.identity_sha256 == Mistral32BackendConfig().identity_sha256
