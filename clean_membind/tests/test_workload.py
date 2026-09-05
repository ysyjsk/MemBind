from membind.workload import MAB8192Manifest, MABContext, MABSession, canonical_episode_body, split_lossless_body


def test_mab_adapter_is_lossless_and_character_bounded():
    session = MABSession("s0", 0, "2025-01-01T00:00:00Z", ({"role": "user", "content": "x" * 20},))
    context = MABContext("ctx", (session,))
    manifest = MAB8192Manifest.from_context(context, dataset_revision="test", chunk_size=8)
    assert all(len(chunk.body) <= 8 for chunk in manifest.chunks)
    assert manifest.reconstruct_session("s0") == canonical_episode_body(session)
    assert "answer" not in canonical_episode_body(session).lower()


def test_split_preserves_unicode_and_exact_text():
    body = "你好" * 20
    chunks = split_lossless_body(body, chunk_size=3)
    assert "".join(chunks) == body
    assert max(map(len, chunks)) <= 3
