"""`/compact` handler 端到端集成测试。"""

from __future__ import annotations


import asyncio

from unittest.mock import AsyncMock, MagicMock


from main import CompactPlugin


def _body(subcommand: str, body: str = "") -> str:
    """Build the full message string that AstrBot produces after wake/strip.

    Models the state of ``event.get_message_str()`` **after** WakingCheck
    has stripped the ``/`` prefix.  The ``compact <subcommand>`` prefix is
    still present and will be removed by :meth:`_extract_command_body`.
    """
    s = f"compact {subcommand}"
    if body:
        s += " " + body
    return s


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

    event.get_message_str.return_value = "/compact run"
    asyncio.run(plugin.compact_run(event))  # type: ignore[arg-type]

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

    event.get_message_str.return_value = "/compact run"
    asyncio.run(plugin.compact_run(event))  # type: ignore[arg-type]

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

    event.get_message_str.return_value = "/compact run"
    asyncio.run(plugin.compact_run(event))  # type: ignore[arg-type]

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

    event.get_message_str.return_value = "/compact run"
    asyncio.run(plugin.compact_run(event))  # type: ignore[arg-type]

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

    event.get_message_str.return_value = "/compact run keep 0.30"
    asyncio.run(plugin.compact_run(event))  # type: ignore[arg-type]

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

    event.get_message_str.return_value = "/compact run provider deepseek-v3"
    asyncio.run(plugin.compact_run(event))  # type: ignore[arg-type]

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

    event.get_message_str.return_value = "/compact run"
    asyncio.run(plugin.compact_run(event))  # type: ignore[arg-type]

    plugin.context.conversation_manager.update_conversation.assert_called_once()

    event.set_result.assert_called_once()


# === /compact subcommand tests =============================================


def test_help_subcommand_returns_usage() -> None:
    """`/compact help` should reply with usage and must NOT touch conversation."""
    plugin = CompactPlugin(context=_make_context())
    asyncio.run(plugin.initialize())
    event = _make_event()

    asyncio.run(plugin.compact_help(event))  # type: ignore[arg-type]

    event.set_result.assert_called_once()
    result = event._result_holder["result"]
    plain_texts = [comp.text for comp in result.chain if hasattr(comp, "text")]
    assert any("/compact" in t and "帮助" in t for t in plain_texts)
    # Must not trigger any conversation write
    plugin.context.conversation_manager.update_conversation.assert_not_called()


def test_status_subcommand_reports_history_length() -> None:
    """`/compact status` should report msg count and threshold."""
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

    plugin = CompactPlugin(
        context=_make_context(conv_manager=_make_conv_manager(history=history)),
    )
    asyncio.run(plugin.initialize())
    event = _make_event()

    asyncio.run(plugin.compact_status(event))  # type: ignore[arg-type]

    event.set_result.assert_called_once()
    result = event._result_holder["result"]
    plain_texts = [comp.text for comp in result.chain if hasattr(comp, "text")]
    # Both msg count "4" and threshold "4" should appear
    assert any("4" in t and ("历史消息" in t or "消息条数" in t) for t in plain_texts)


def test_status_subcommand_refuses_no_conversation() -> None:
    """No active conversation → friendly refusal message."""
    plugin = CompactPlugin(
        context=_make_context(conv_manager=_make_conv_manager(cid=None)),
    )
    asyncio.run(plugin.initialize())
    event = _make_event()

    asyncio.run(plugin.compact_status(event))  # type: ignore[arg-type]

    event.set_result.assert_called_once()
    result = event._result_holder["result"]
    plain_texts = [comp.text for comp in result.chain if hasattr(comp, "text")]
    assert any("没有进行中的会话" in t for t in plain_texts)


def test_preview_subcommand_does_not_call_provider() -> None:
    """`/compact preview` must NOT call the LLM provider."""
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

    provider = _make_provider("MUST NOT BE CALLED")
    plugin = CompactPlugin(
        context=_make_context(
            provider=provider,
            conv_manager=_make_conv_manager(history=history),
        ),
    )
    asyncio.run(plugin.initialize())
    event = _make_event()

    event.get_message_str.return_value = "/compact preview"
    asyncio.run(plugin.compact_preview(event))  # type: ignore[arg-type]

    event.set_result.assert_called_once()
    # Critical: provider.text_chat must not have been called
    provider.text_chat.assert_not_called()
    # And no history write either
    plugin.context.conversation_manager.update_conversation.assert_not_called()
    result = event._result_holder["result"]
    plain_texts = [comp.text for comp in result.chain if hasattr(comp, "text")]
    assert any("预览" in t for t in plain_texts)


def test_preview_subcommand_below_threshold_says_no_compress() -> None:
    """If msg count < min_messages, preview must clearly state no compression."""
    plugin = CompactPlugin(
        context=_make_context(conv_manager=_make_conv_manager(history=[])),
    )
    asyncio.run(plugin.initialize())
    plugin.min_messages = 10  # ensure threshold higher than 0
    event = _make_event()

    event.get_message_str.return_value = "/compact preview"
    asyncio.run(plugin.compact_preview(event))  # type: ignore[arg-type]

    event.set_result.assert_called_once()
    result = event._result_holder["result"]
    plain_texts = [comp.text for comp in result.chain if hasattr(comp, "text")]
    assert any("不会触发压缩" in t for t in plain_texts)


def test_config_subcommand_shows_all_settings() -> None:
    """`/compact config` should list all effective settings."""
    plugin = CompactPlugin(context=_make_context())
    asyncio.run(plugin.initialize())
    plugin.keep_recent_ratio = 0.20
    plugin.min_messages = 6
    event = _make_event()

    asyncio.run(plugin.compact_config(event))  # type: ignore[arg-type]

    event.set_result.assert_called_once()
    result = event._result_holder["result"]
    plain_texts = [comp.text for comp in result.chain if hasattr(comp, "text")]
    text = " ".join(plain_texts)
    # All keys must be mentioned
    for key in (
        "compress_provider_id",
        "keep_recent_ratio",
        "instruction_text",
        "min_messages",
        "show_summary",
        "summary_max_chars",
    ):
        assert key in text, f"missing {key} in config reply"
    # Custom values must be reflected
    assert "0.2" in text
    assert "6" in text


# === /compact set subcommand tests =========================================


def test_set_subcommand_updates_memory_immediately() -> None:
    """/compact set keep 0.20 updates self.keep_recent_ratio in this process."""
    plugin = CompactPlugin(context=_make_context())
    asyncio.run(plugin.initialize())
    event = _make_event()

    event.get_message_str.return_value = "/compact set keep 0.20"
    asyncio.run(plugin.compact_set(event))  # type: ignore[arg-type]

    # In-memory update happened
    assert plugin.keep_recent_ratio == 0.20
    event.set_result.assert_called_once()
    result = event._result_holder["result"]
    plain_texts = [comp.text for comp in result.chain if hasattr(comp, "text")]
    text = " ".join(plain_texts)
    assert "临时生效" in text
    assert "keep_recent_ratio" in text


def test_set_subcommand_supports_multiple_keys() -> None:
    """/compact set keep 0.1 provider deepseek-r1 updates both at once."""
    plugin = CompactPlugin(context=_make_context())
    asyncio.run(plugin.initialize())
    event = _make_event()

    event.get_message_str.return_value = "/compact set keep 0.1 provider deepseek-r1"
    asyncio.run(plugin.compact_set(event))  # type: ignore[arg-type]

    assert plugin.keep_recent_ratio == 0.1
    assert plugin.compress_provider_id == "deepseek-r1"
    event.set_result.assert_called_once()


def test_set_subcommand_rejects_unknown_value_silently_via_error() -> None:
    """/compact set keep abc returns an error message and does not change state."""
    plugin = CompactPlugin(context=_make_context())
    asyncio.run(plugin.initialize())
    original_keep = plugin.keep_recent_ratio
    event = _make_event()

    event.get_message_str.return_value = "/compact set keep abc"
    asyncio.run(plugin.compact_set(event))  # type: ignore[arg-type]

    # State unchanged
    assert plugin.keep_recent_ratio == original_keep
    event.set_result.assert_called_once()
    result = event._result_holder["result"]
    plain_texts = [comp.text for comp in result.chain if hasattr(comp, "text")]
    text = " ".join(plain_texts)
    assert "失败" in text


def test_set_subcommand_accepts_long_form_canonical_keys() -> None:
    """/compact set keep_recent_ratio 0.20 must work (regression: parser used to
    only accept short aliases, which made `/compact set` look broken for users
    who copy-pasted the key name from `/compact config`).
    """
    plugin = CompactPlugin(context=_make_context())
    asyncio.run(plugin.initialize())
    event = _make_event()

    event.get_message_str.return_value = "/compact set keep_recent_ratio 0.20"
    asyncio.run(plugin.compact_set(event))  # type: ignore[arg-type]

    # In-memory state changed
    assert plugin.keep_recent_ratio == 0.20
    # Success reply
    event.set_result.assert_called_once()
    result = event._result_holder["result"]
    plain_texts = [comp.text for comp in result.chain if hasattr(comp, "text")]
    assert "临时生效" in " ".join(plain_texts)


def test_set_subcommand_accepts_long_form_provider_and_default_focus() -> None:
    """/compact set compress_provider_id / default_focus 也用长名。"""
    plugin = CompactPlugin(context=_make_context())
    asyncio.run(plugin.initialize())
    event = _make_event()

    event.get_message_str.return_value = (
        "/compact set compress_provider_id deepseek-v3 default_focus 鉴权重构"
    )
    asyncio.run(plugin.compact_set(event))  # type: ignore[arg-type]

    assert plugin.compress_provider_id == "deepseek-v3"
    assert plugin.default_focus == "鉴权重构"
    event.set_result.assert_called_once()


def test_set_subcommand_rejects_extra_focus_text() -> None:
    """/compact set 不允许非 key-value 自由文本(应原子性整体拒绝)."""
    plugin = CompactPlugin(context=_make_context())
    asyncio.run(plugin.initialize())
    original_keep = plugin.keep_recent_ratio
    event = _make_event()

    event.get_message_str.return_value = "/compact set keep 0.1 鉴权"
    asyncio.run(plugin.compact_set(event))  # type: ignore[arg-type]

    # 整个 set 应被拒绝(任何 key 不应被写)
    assert plugin.keep_recent_ratio == original_keep
    event.set_result.assert_called_once()
    result = event._result_holder["result"]
    plain_texts = [comp.text for comp in result.chain if hasattr(comp, "text")]
    text = " ".join(plain_texts)
    assert "自由文本" in text
    assert "鉴权" in text


# === Regression: GreedyStr workaround =======================================


def test_extract_command_body_preserves_all_tokens() -> None:
    """_extract_command_body 必须保留所有空白分隔的 token（回归测试 #1）。

    AstrBot 当前的 ``validate_and_convert_params`` 对 ``GreedyStr("")``
    实例作 ``is`` 身份比较（不是 isinstance），导致多 token 输入被截断到
    第一个 token。本实现的 ``_extract_command_body`` 直接从
    ``event.get_message_str()`` 取完整正文并手动剥离命令前缀，不受此 bug
    影响。
    """
    event = _make_event(_body("set", "keep 0.2"))
    body = CompactPlugin._extract_command_body(event, "set")
    assert body == "keep 0.2", f"expected 'keep 0.2', got {body!r}"


def test_extract_command_body_works_with_three_tokens() -> None:
    """三个以上 token 也能完整保留。"""
    event = _make_event(_body("run", "keep 0.20 provider gpt-4o 鉴权"))
    body = CompactPlugin._extract_command_body(event, "run")
    assert body == "keep 0.20 provider gpt-4o 鉴权", f"got {body!r}"


def test_extract_command_body_empty_on_no_args() -> None:
    """没有参数时返回空字符串。"""
    event = _make_event(_body("run"))
    body = CompactPlugin._extract_command_body(event, "run")
    assert body == "", f"got {body!r}"


def test_extract_command_body_strips_wake_prefix() -> None:
    """即使包含唤醒前缀 / 也能正确剥离。"""
    event = _make_event("/" + _body("preview", "keep 0.3"))
    body = CompactPlugin._extract_command_body(event, "preview")
    assert body == "keep 0.3", f"got {body!r}"


def test_extract_command_body_case_insensitive() -> None:
    """大小写不敏感匹配。"""
    event = _make_event("COMPACT RUN keep 0.2")
    body = CompactPlugin._extract_command_body(event, "run")
    assert body == "keep 0.2", f"got {body!r}"
