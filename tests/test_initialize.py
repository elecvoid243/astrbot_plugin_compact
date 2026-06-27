"""`CompactPlugin.initialize` 配置加载与默认值 fallback 测试。"""

from __future__ import annotations

import asyncio
from unittest.mock import MagicMock

from main import CompactPlugin


def _build_context(config: dict | None = None) -> MagicMock:
    """Build a MagicMock context whose `get_config` returns the given config.

    If config is None, get_config returns {} (the call should still succeed).
    """
    ctx = MagicMock()
    ctx.get_config.return_value = config if config is not None else {}
    return ctx


def test_initialize_loads_full_config() -> None:
    plugin = CompactPlugin(context=_build_context())
    # Reset to defaults first to prove initialize is the thing that loads them.
    plugin.compress_provider_id = ""
    plugin.keep_recent_ratio = 0.15
    plugin.instruction_text = ""
    plugin.min_messages = 4
    plugin.show_summary = True
    plugin.summary_max_chars = 800

    cfg = {
        "compress_provider_id": "deepseek-v3",
        "keep_recent_ratio": 0.25,
        "instruction_text": "Custom instruction",
        "min_messages": 6,
        "show_summary": False,
        "summary_max_chars": 400,
    }
    plugin.context.get_config = MagicMock(return_value=cfg)

    asyncio.run(plugin.initialize())

    assert plugin.compress_provider_id == "deepseek-v3"
    assert plugin.keep_recent_ratio == 0.25
    assert plugin.instruction_text == "Custom instruction"
    assert plugin.min_messages == 6
    assert plugin.show_summary is False
    assert plugin.summary_max_chars == 400


def test_initialize_clamps_keep_recent_ratio() -> None:
    plugin = CompactPlugin(context=_build_context({"keep_recent_ratio": 5.0}))
    asyncio.run(plugin.initialize())
    assert plugin.keep_recent_ratio == 0.3

    plugin2 = CompactPlugin(context=_build_context({"keep_recent_ratio": -1.0}))
    asyncio.run(plugin2.initialize())
    assert plugin2.keep_recent_ratio == 0.0


def test_initialize_falls_back_on_bad_value() -> None:
    """If config parsing blows up (e.g. string for int), keep defaults."""
    plugin = CompactPlugin(context=_build_context({"min_messages": "not-an-int"}))
    asyncio.run(plugin.initialize())
    # The bad string triggers ValueError, caught and we keep the default 4.
    assert plugin.min_messages == 4


def test_initialize_handles_missing_config() -> None:
    """`get_config()` returning None must not crash initialize."""
    ctx = MagicMock()
    ctx.get_config.return_value = None
    plugin = CompactPlugin(context=ctx)
    asyncio.run(plugin.initialize())
    # All defaults preserved.
    assert plugin.compress_provider_id == ""
    assert plugin.keep_recent_ratio == 0.15
    assert plugin.min_messages == 4


def test_initialize_handles_get_config_raising() -> None:
    """If get_config itself raises, we should not propagate."""
    ctx = MagicMock()
    ctx.get_config.side_effect = RuntimeError("config store down")
    plugin = CompactPlugin(context=ctx)
    asyncio.run(plugin.initialize())
    assert plugin.compress_provider_id == ""
    assert plugin.keep_recent_ratio == 0.15
