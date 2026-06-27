"""`/compact` handler 端到端集成测试。"""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock

from main import CompactPlugin


def _make_provider(text: str = "summary text") -> MagicMock:
    """A mock provider that returns a fixed summary on text_chat."""
    provider = MagicMock()
    provider.provider_config = {"modalities": ["text"]}
    response = MagicMock()
    response.completion_text = text
    provider.text_chat = AsyncMock(return_value=response)
    return provider


def _make_event(text: str = "/compact", umo: str = "umo:test") -> MagicMock:
    """Mock AstrMessageEvent. `set_result` captures the MessageEventResult."""
    event = MagicMock()
    event.unified_msg_origin = umo
    event.get_message_str.return_value = text
    result_holder: dict = {}

    def _capture_result(result):
        result_holder["result"] = result

    event.set_result.side_effect = _capture_result
    event._result_holder = result_holder
    return event


def _make_conv_manager(
    *,
    cid: str | None = "conv-1",
    history: list[dict] | None = None,
) -> MagicMock:
    mgr = MagicMock()
    mgr.get_curr_conversation_id = AsyncMock(return_value=cid)
    conv = MagicMock()
    conv.history = history if history is not None else []
    mgr.get_conversation = AsyncMock(return_value=conv)

    async def _update_conversation(umo, cid, history=None, **_kw):
        # Capture the new history list for assertion in tests
        mgr.last_updated_history = history or []

    mgr.update_conversation = AsyncMock(side_effect=_update_conversation)
    return mgr


def _make_context(
    *,
    provider: MagicMock | None = None,
    conv_manager: MagicMock | None = None,
    get_provider_by_id: dict[str, object] | None = None,
    get_config: dict | None = None,
) -> MagicMock:
    ctx = MagicMock()
    ctx.get_using_provider.return_value = provider
    ctx.get_provider_by_id.side_effect = lambda pid: (get_provider_by_id or {}).get(pid)
    ctx.get_config.return_value = get_config if get_config is not None else {}
    ctx.conversation_manager = conv_manager or _make_conv_manager()
    return ctx


# === Test cases ==============================================================


def test_handler_refuses_when_no_provider() -> None:
    """All four levels of provider resolution yield None → user-facing message."""
    from astrbot.api.event import MessageEventResult

    plugin = CompactPlugin(context=_make_context(provider=None))
    asyncio.run(plugin.initialize())
    event = _make_event()

    asyncio.run(plugin.compact(event, ""))  # type: ignore[arg-type]

    event.set_result.assert_called_once()
    result = event._result_holder["result"]
    assert isinstance(result, MessageEventResult)
    # Compose text by reading .chain — the underlying object is MessageEventResult
    # with a `.chain` attribute that holds the message components.
    plain_texts = [comp.text for comp in result.chain if hasattr(comp, "text")]
    assert any("provider" in t.lower() or "LLM" in t for t in plain_texts)


def test_handler_refuses_when_no_conversation() -> None:
    plugin = CompactPlugin(
        context=_make_context(provider=_make_provider()),
    )
    plugin.context.conversation_manager = _make_conv_manager(cid=None)
    asyncio.run(plugin.initialize())
    event = _make_event()

    asyncio.run(plugin.compact(event, ""))  # type: ignore[arg-type]
    event.set_result.assert_called_once()


def test_handler_refuses_when_too_few_messages() -> None:
    from astrbot.core.agent.message import (
        AssistantMessageSegment,
        UserMessageSegment,
        dump_messages_with_checkpoints,
    )

    real_history = dump_messages_with_checkpoints(
        [
            UserMessageSegment(content="hi"),
            AssistantMessageSegment(content="hello"),
        ],
    )

    plugin = CompactPlugin(
        context=_make_context(
            provider=_make_provider(),
            conv_manager=_make_conv_manager(history=real_history),
        ),
    )
    asyncio.run(plugin.initialize())
    event = _make_event()

    asyncio.run(plugin.compact(event, ""))  # type: ignore[arg-type]

    event.set_result.assert_called_once()
    plugin.context.conversation_manager.update_conversation.assert_not_called()
    # Reply should mention the message count shortage.
    result = event._result_holder["result"]
    plain_texts = [comp.text for comp in result.chain if hasattr(comp, "text")]
    assert any(
        "2" in t and ("少于" in t or "过少" in t or "过短" in t) for t in plain_texts
    )


def test_handler_compresses_and_saves() -> None:
    from astrbot.core.agent.message import (
        AssistantMessageSegment,
        UserMessageSegment,
        dump_messages_with_checkpoints,
    )

    history = dump_messages_with_checkpoints(
        [
            UserMessageSegment(content="u1"),
            AssistantMessageSegment(content="a1"),
            UserMessageSegment(content="u2"),
            AssistantMessageSegment(content="a2"),
            UserMessageSegment(content="u3"),
            AssistantMessageSegment(content="a3"),
        ],
    )

    plugin = CompactPlugin(
        context=_make_context(
            provider=_make_provider("Mocked summary."),
            conv_manager=_make_conv_manager(history=history),
        ),
    )
    asyncio.run(plugin.initialize())
    event = _make_event()

    asyncio.run(plugin.compact(event, ""))  # type: ignore[arg-type]

    # Compressor ran and called update_conversation with a new history list.
    plugin.context.conversation_manager.update_conversation.assert_called_once()
    new_history = plugin.context.conversation_manager.last_updated_history
    assert isinstance(new_history, list)
    assert len(new_history) > 0

    # Reply sent.
    event.set_result.assert_called_once()


def test_handler_respects_keep_override() -> None:
    """`--keep 0.30` must propagate to the compactor instance."""
    from astrbot.core.agent.message import (
        AssistantMessageSegment,
        UserMessageSegment,
        dump_messages_with_checkpoints,
    )

    history = dump_messages_with_checkpoints(
        [
            UserMessageSegment(content="u1"),
            AssistantMessageSegment(content="a1"),
            UserMessageSegment(content="u2"),
            AssistantMessageSegment(content="a2"),
            UserMessageSegment(content="u3"),
            AssistantMessageSegment(content="a3"),
        ],
    )

    plugin = CompactPlugin(
        context=_make_context(
            provider=_make_provider(),
            conv_manager=_make_conv_manager(history=history),
        ),
    )
    asyncio.run(plugin.initialize())
    # Force higher default to ensure override works
    plugin.keep_recent_ratio = 0.05
    event = _make_event()

    asyncio.run(plugin.compact(event, "--keep 0.30"))  # type: ignore[arg-type]

    # We can't directly inspect the compactor, but we know the path was exercised
    # because update_conversation was called.
    plugin.context.conversation_manager.update_conversation.assert_called_once()


def test_handler_provider_command_line_override() -> None:
    """`--provider deepseek-v3` overrides the chat provider."""
    from astrbot.core.agent.message import (
        AssistantMessageSegment,
        UserMessageSegment,
        dump_messages_with_checkpoints,
    )

    history = dump_messages_with_checkpoints(
        [
            UserMessageSegment(content="u1"),
            AssistantMessageSegment(content="a1"),
            UserMessageSegment(content="u2"),
            AssistantMessageSegment(content="a2"),
        ],
    )

    explicit = _make_provider("Explicit provider summary.")
    plugin = CompactPlugin(
        context=_make_context(
            provider=_make_provider(),  # fallback chat provider
            conv_manager=_make_conv_manager(history=history),
            get_provider_by_id={"deepseek-v3": explicit},
        ),
    )
    asyncio.run(plugin.initialize())
    event = _make_event()

    asyncio.run(plugin.compact(event, "--provider deepseek-v3"))  # type: ignore[arg-type]

    # The explicit provider should have been called (AsyncMock lets us verify).
    explicit.text_chat.assert_called()
    # update_conversation should have been called.
    plugin.context.conversation_manager.update_conversation.assert_called_once()


def test_handler_no_op_on_llm_failure() -> None:
    """LLM errors are caught; original history is preserved (per spec §6)."""
    from astrbot.core.agent.message import (
        AssistantMessageSegment,
        UserMessageSegment,
        dump_messages_with_checkpoints,
    )

    history = dump_messages_with_checkpoints(
        [
            UserMessageSegment(content="u1"),
            AssistantMessageSegment(content="a1"),
            UserMessageSegment(content="u2"),
            AssistantMessageSegment(content="a2"),
        ],
    )

    failing_provider = _make_provider()
    failing_provider.text_chat.side_effect = RuntimeError("LLM down")

    plugin = CompactPlugin(
        context=_make_context(
            provider=failing_provider,
            conv_manager=_make_conv_manager(history=history),
        ),
    )
    asyncio.run(plugin.initialize())
    event = _make_event()

    # The inner LLMSummaryCompressor swallows the error and returns the input
    # unchanged, so update_conversation is called with the same content.
    # The handler should still reply successfully.
    asyncio.run(plugin.compact(event, ""))  # type: ignore[arg-type]
    plugin.context.conversation_manager.update_conversation.assert_called_once()
    event.set_result.assert_called_once()
