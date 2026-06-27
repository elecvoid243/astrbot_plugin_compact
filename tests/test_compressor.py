"""Compactor 包装的单元测试。"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from compressor import Compactor


@pytest.fixture
def mock_provider() -> MagicMock:
    """A mock provider whose text_chat returns a fixed summary string."""
    provider = MagicMock()
    provider.provider_config = {"modalities": ["text"]}
    response = MagicMock()
    response.completion_text = "This is a test summary."
    provider.text_chat = AsyncMock(return_value=response)
    return provider


@pytest.mark.asyncio
async def test_compactor_returns_summary_in_messages(mock_provider: MagicMock) -> None:
    """Compactor should return the inner compressor's result containing the mock summary verbatim."""
    from astrbot.core.agent.message import Message

    messages = [
        Message(role="user", content="Hello"),
        Message(role="assistant", content="Hi there"),
        Message(role="user", content="How are you?"),
        Message(role="assistant", content="Doing well"),
    ]

    compactor = Compactor(provider=mock_provider, keep_recent_ratio=0.5)
    result = await compactor(messages)

    assert isinstance(result, list)
    assert len(result) > 0
    # Structural assertion: at least one user-role message with non-empty string content.
    # The inner compressor is the source of truth for exact phrasing, so we don't
    # match literal template strings here.
    assert any(
        m.role == "user" and isinstance(m.content, str) and len(m.content) > 0
        for m in result
    )
    # The mocked LLM summary text must appear verbatim somewhere in the result.
    assert any(
        isinstance(m.content, str) and "This is a test summary." in m.content
        for m in result
    )


@pytest.mark.asyncio
async def test_compactor_returns_original_on_llm_failure(
    mock_provider: MagicMock,
) -> None:
    """Compactor must follow the spec §6 contract: LLM failure -> return original messages.

    Note: `LLMSummaryCompressor.__call__` (compressor.py:246-253) catches and
    logs the LLM error, then returns the original list. Compactor does not
    re-raise. The plugin handler relies on this contract to keep history
    untouched on failure.
    """
    from astrbot.core.agent.message import Message

    mock_provider.text_chat.side_effect = RuntimeError("LLM down")

    messages = [
        Message(role="user", content="a"),
        Message(role="assistant", content="b"),
        Message(role="user", content="c"),
        Message(role="assistant", content="d"),
    ]

    compactor = Compactor(provider=mock_provider)
    result = await compactor(messages)

    assert result == messages  # inner swallowed the error, returned input as-is


def test_compactor_clamps_keep_recent_ratio() -> None:
    """Compactor should clamp keep_recent_ratio to [0, 0.3].

    Note: this couples the test to the wrapper's private `_compressor` attribute.
    This is intentional — Compactor is a thin pass-through and the clamp is the
    single behavioral contract we want to assert. If the inner is renamed, update
    both the wrapper and this test in the same commit.
    """
    compactor = Compactor(provider=MagicMock(), keep_recent_ratio=2.0)
    # Inner compressor is LLMSummaryCompressor; check that its ratio was clamped
    assert compactor._compressor.keep_recent_ratio == 0.3
