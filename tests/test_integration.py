"""端到端集成测试：完整的 conv → provider → compressor → history pipeline。



只 mock LLM provider，验证 summary 真的被写入持久化的 history。

"""

from __future__ import annotations


import asyncio

from unittest.mock import AsyncMock, MagicMock


from main import CompactPlugin


def _make_provider(summary_text: str) -> MagicMock:
    """Mock provider that returns a fixed summary."""

    provider = MagicMock()

    provider.provider_config = {"modalities": ["text"]}

    response = MagicMock()

    response.completion_text = summary_text

    provider.text_chat = AsyncMock(return_value=response)

    return provider


def _make_event(umo: str = "umo:integration") -> MagicMock:

    event = MagicMock()

    event.unified_msg_origin = umo

    result_holder: dict = {}

    def _capture(result):

        result_holder["result"] = result

    event.set_result.side_effect = _capture

    event._result_holder = result_holder

    return event


def _make_conv_manager(
    *,
    cid: str = "conv-int",
    history: list[dict] | None = None,
) -> MagicMock:

    mgr = MagicMock()

    mgr.get_curr_conversation_id = AsyncMock(return_value=cid)

    conv = MagicMock()

    conv.history = history if history is not None else []

    mgr.get_conversation = AsyncMock(return_value=conv)

    captured: dict = {}

    async def _update(umo, cid, history=None, **_kw):

        captured["umo"] = umo

        captured["cid"] = cid

        captured["history"] = history or []

    mgr.update_conversation = AsyncMock(side_effect=_update)

    mgr._captured = captured

    return mgr


def _make_context(
    provider: MagicMock | None = None,
    conv_manager: MagicMock | None = None,
    get_provider_by_id: dict[str, object] | None = None,
) -> MagicMock:

    ctx = MagicMock()

    ctx.get_using_provider.return_value = provider

    ctx.get_provider_by_id.side_effect = lambda pid: (get_provider_by_id or {}).get(pid)

    ctx.get_config.return_value = {}

    ctx.conversation_manager = conv_manager or _make_conv_manager()

    return ctx


def test_e2e_realistic_summary_persists_to_history() -> None:
    """Pipeline: load history → compress via mocked LLM → save new history."""

    from astrbot.core.agent.message import (
        AssistantMessageSegment,
        UserMessageSegment,
        bind_checkpoint_messages,
        dump_messages_with_checkpoints,
    )

    # 8 messages (2 rounds + buffer)

    raw_history = dump_messages_with_checkpoints(
        [
            UserMessageSegment(content="我们要重构鉴权"),
            AssistantMessageSegment(content="好的，我先看一下目前的代码"),
            UserMessageSegment(content="使用了 jwt"),
            AssistantMessageSegment(content="jwt 方案记录在 README.md"),
            UserMessageSegment(content="希望加入 RBAC"),
            AssistantMessageSegment(content="RBAC 需要重新设计"),
            UserMessageSegment(content="是否考虑 ABAC？"),
            AssistantMessageSegment(content="ABAC 太复杂，先用 RBAC"),
        ],
    )

    provider = _make_provider("主要讨论了鉴权重构，结论：使用 JWT + RBAC 组合。")

    conv_mgr = _make_conv_manager(history=raw_history)

    ctx = _make_context(provider=provider, conv_manager=conv_mgr)

    plugin = CompactPlugin(context=ctx)

    asyncio.run(plugin.initialize())

    event = _make_event()

    event.get_message_str.return_value = "/compact run 重构鉴权"
    asyncio.run(plugin.compact_run(event))  # type: ignore[arg-type]

    # LLM was called exactly once (single summary call).

    provider.text_chat.assert_called_once()

    # History was updated.

    conv_mgr.update_conversation.assert_called_once()

    saved = conv_mgr._captured["history"]

    assert saved != raw_history, "compressed history must differ from original"

    assert len(saved) < len(raw_history), "compressed history must be shorter"

    # Reload the saved history and verify the summary is embedded verbatim.

    reloaded = bind_checkpoint_messages(saved)

    found = False

    for m in reloaded:
        if (
            m.role == "user"
            and isinstance(m.content, str)
            and "JWT + RBAC" in m.content
        ):
            found = True

            break

    assert found, f"summary text not found in saved history; got: {saved}"

    # Reply contains the summary excerpt.

    event.set_result.assert_called_once()

    result = event._result_holder["result"]

    plain_texts = [comp.text for comp in result.chain if hasattr(comp, "text")]

    assert any("JWT + RBAC" in t for t in plain_texts), (
        f"reply should embed summary excerpt; got: {plain_texts}"
    )


def test_e2e_no_compression_below_threshold_preserves_history() -> None:
    """Below `min_messages` the handler must not write anything to history."""

    from astrbot.core.agent.message import (
        AssistantMessageSegment,
        UserMessageSegment,
        dump_messages_with_checkpoints,
    )

    history = dump_messages_with_checkpoints(
        [
            UserMessageSegment(content="hi"),
            AssistantMessageSegment(content="hello"),
        ],
    )

    provider = _make_provider("should not be called")

    conv_mgr = _make_conv_manager(history=history)

    ctx = _make_context(provider=provider, conv_manager=conv_mgr)

    plugin = CompactPlugin(context=ctx)

    plugin.min_messages = 4

    asyncio.run(plugin.initialize())

    event = _make_event()

    event.get_message_str.return_value = "/compact run"
    asyncio.run(plugin.compact_run(event))  # type: ignore[arg-type]

    provider.text_chat.assert_not_called()

    conv_mgr.update_conversation.assert_not_called()

    event.set_result.assert_called_once()


def test_e2e_focus_instruction_combined_with_config() -> None:
    """`/compact <focus>` + plugin config instruction → both reach the LLM call."""

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
            UserMessageSegment(content="u4"),
            AssistantMessageSegment(content="a4"),
        ],
    )

    provider = _make_provider("focused summary")

    conv_mgr = _make_conv_manager(history=history)

    # Pass the config through the mock so initialize() picks it up.

    ctx = _make_context(provider=provider, conv_manager=conv_mgr)

    ctx.get_config.return_value = {"instruction_text": "Base config instruction"}

    plugin = CompactPlugin(context=ctx)

    asyncio.run(plugin.initialize())

    assert plugin.instruction_text == "Base config instruction"

    event = _make_event()

    event.get_message_str.return_value = "/compact run 重构鉴权"
    asyncio.run(plugin.compact_run(event))  # type: ignore[arg-type]

    provider.text_chat.assert_called_once()

    # The inner compressor calls `text_chat(contexts=[...])` where each

    # context is a plain dict (sanitized via `sanitize_contexts_by_modalities`).

    call_args = provider.text_chat.call_args

    contexts = call_args.kwargs.get("contexts") or call_args.kwargs.get("prompt")

    assert contexts is not None, (
        f"text_chat call has no contexts/prompt kwarg; "
        f"kwargs={call_args.kwargs}, args={call_args.args}"
    )

    # The last user-role entry in contexts embeds the combined instruction.

    last_user = next((c for c in reversed(contexts) if c.get("role") == "user"), None)

    assert last_user is not None, f"no user message in contexts: {contexts}"

    content = last_user["content"]

    if isinstance(content, list):
        text = next((p["text"] for p in content if p.get("type") == "text"), "")

    else:
        text = content

    assert "Base config instruction" in text, (
        f"config instruction missing from prompt: {text!r}"
    )

    assert "重构鉴权" in text, f"focus topic missing from prompt: {text!r}"


def test_e2e_summary_excerpt_truncation() -> None:
    """Long summary should be truncated to `summary_max_chars` with ellipsis."""

    from astrbot.core.agent.message import (
        AssistantMessageSegment,
        UserMessageSegment,
        dump_messages_with_checkpoints,
    )

    long_summary = "x" * 5000  # way longer than default summary_max_chars=800

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

    provider = _make_provider(long_summary)

    conv_mgr = _make_conv_manager(history=history)

    ctx = _make_context(provider=provider, conv_manager=conv_mgr)

    plugin = CompactPlugin(context=ctx)

    asyncio.run(plugin.initialize())

    # Set after initialize() so the default 800 doesn't overwrite our 200.

    plugin.summary_max_chars = 200

    event = _make_event()

    event.get_message_str.return_value = "/compact run"
    asyncio.run(plugin.compact_run(event))  # type: ignore[arg-type]

    result = event._result_holder["result"]

    plain_texts = [comp.text for comp in result.chain if hasattr(comp, "text")]

    full_text = "\n".join(plain_texts)

    # The summary preview section is everything after the "📝 摘要预览：" marker.

    marker = "📝 摘要预览:\n"

    marker_idx = full_text.find(marker)

    assert marker_idx >= 0, f"no summary marker in reply: {plain_texts!r}"

    excerpt = full_text[marker_idx + len(marker) :]

    assert excerpt.endswith("..."), f"missing ellipsis: {excerpt!r}"

    # Truncated excerpt is exactly summary_max_chars + "...".

    assert len(excerpt) == 200 + 3, (
        f"unexpected excerpt length: len={len(excerpt)}, expected 203"
    )
