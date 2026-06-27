"""`resolve_provider` 四级回退链的单元测试。"""

from __future__ import annotations

from unittest.mock import MagicMock

from main import CompactArgs, resolve_provider


def _mk_ctx(
    *,
    provider_for_id: dict[str, object] | None = None,
    core_provider: object | None = "core-provider",
    chat_provider: object | None = "chat-provider",
    core_cfg: dict | None = None,
) -> MagicMock:
    """Build a MagicMock context matching resolve_provider's call surface."""
    ctx = MagicMock()
    ctx.get_provider_by_id.side_effect = lambda pid: (provider_for_id or {}).get(pid)
    ctx.get_using_provider.return_value = chat_provider
    ctx.get_config.return_value = core_cfg if core_cfg is not None else {}
    ctx._core_provider = core_provider
    return ctx


def test_level1_explicit_provider() -> None:
    ctx = _mk_ctx(
        provider_for_id={"cmd-prov": "cmd-provider"},
        core_provider="core-provider",
        chat_provider="chat-provider",
    )
    args = CompactArgs(provider_id="cmd-prov", config_provider_id="")
    assert resolve_provider(ctx, "umo:x", args) == "cmd-provider"
    ctx.get_provider_by_id.assert_called_once_with("cmd-prov")


def test_falls_through_to_level2_plugin_config() -> None:
    """When level-1 lookup misses (returns None), try plugin config."""
    ctx = _mk_ctx(
        provider_for_id={"plug-prov": "plug-provider"},
        chat_provider="chat-provider",
    )
    args = CompactArgs(provider_id="missing", config_provider_id="plug-prov")
    assert resolve_provider(ctx, "umo:x", args) == "plug-provider"


def test_falls_through_to_level3_core_config() -> None:
    ctx = _mk_ctx(
        provider_for_id={"core-prov": "core-provider"},
        chat_provider="chat-provider",
        core_cfg={"provider_settings": {"llm_compress_provider_id": "core-prov"}},
    )
    args = CompactArgs(config_provider_id="")
    assert resolve_provider(ctx, "umo:x", args) == "core-provider"
    ctx.get_config.assert_called_with(umo="umo:x")


def test_falls_through_to_level4_chat_provider() -> None:
    """If levels 1-3 all fail/miss, fall back to the current chat provider."""
    ctx = _mk_ctx(
        provider_for_id={},
        core_provider=None,
        chat_provider="chat-provider",
        core_cfg={},
    )
    args = CompactArgs()
    assert resolve_provider(ctx, "umo:y", args) == "chat-provider"
    ctx.get_using_provider.assert_called_with(umo="umo:y")


def test_returns_none_when_all_levels_empty() -> None:
    """Caller (handler) uses None to refuse compression with a clear message."""
    ctx = _mk_ctx(
        provider_for_id={},
        core_provider=None,
        chat_provider=None,
        core_cfg={},
    )
    args = CompactArgs()
    assert resolve_provider(ctx, "umo:z", args) is None


def test_level1_overrides_lower_levels_even_when_chat_available() -> None:
    ctx = _mk_ctx(
        provider_for_id={"cmd": "cmd-p"},
        chat_provider="chat-p",
    )
    args = CompactArgs(provider_id="cmd", config_provider_id="missing")
    assert resolve_provider(ctx, "umo:a", args) == "cmd-p"
