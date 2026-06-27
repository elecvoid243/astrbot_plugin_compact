"""`/compact` 子命令参数解析的单元测试。

测试位置 key-value 解析(`/compact run keep 0.2 鉴权` 形式)。
旧式 `--keep` / `--provider` 标志已被移除,完全使用自然语言位置参数。
"""

from __future__ import annotations

from main import (
    ParseResult,
    known_override_keys,
    parse_compact_args,
    parse_compact_overrides,
)


# === parse_compact_overrides(新接口) =====================================


def test_overrides_empty_body_returns_empty_result() -> None:
    result = parse_compact_overrides("")
    assert isinstance(result, ParseResult)
    assert result.overrides == {}
    assert result.focus == ""
    assert result.errors == []


def test_overrides_only_focus() -> None:
    result = parse_compact_overrides("重构鉴权逻辑")
    assert result.overrides == {}
    assert result.focus == "重构鉴权逻辑"
    assert result.errors == []


def test_overrides_keep_value() -> None:
    result = parse_compact_overrides("keep 0.20 鉴权")
    assert result.overrides == {"keep_recent_ratio": 0.20}
    assert result.focus == "鉴权"


def test_overrides_provider_value() -> None:
    result = parse_compact_overrides("provider deepseek-r1")
    assert result.overrides == {"compress_provider_id": "deepseek-r1"}
    assert result.focus == ""


def test_overrides_combined_keep_and_provider() -> None:
    result = parse_compact_overrides("keep 0.20 provider deepseek-r1 鉴权")
    assert result.overrides == {
        "keep_recent_ratio": 0.20,
        "compress_provider_id": "deepseek-r1",
    }
    assert result.focus == "鉴权"


def test_overrides_focus_can_be_chinese() -> None:
    result = parse_compact_overrides("重构 鉴权 逻辑")
    assert result.focus == "重构 鉴权 逻辑"
    assert result.overrides == {}


def test_overrides_clamps_keep_ratio_high() -> None:
    result = parse_compact_overrides("keep 2.0")
    # 0.3 is the documented upper bound
    assert result.overrides["keep_recent_ratio"] == 0.3


def test_overrides_clamps_keep_ratio_low() -> None:
    result = parse_compact_overrides("keep -1.0")
    assert result.overrides["keep_recent_ratio"] == 0.0


def test_overrides_invalid_keep_records_error() -> None:
    result = parse_compact_overrides("keep abc")
    assert "keep_recent_ratio" not in result.overrides
    assert any("keep" in e for e in result.errors)


def test_overrides_min_messages() -> None:
    result = parse_compact_overrides("min 6")
    assert result.overrides == {"min_messages": 6}


def test_overrides_summary_on_off() -> None:
    r1 = parse_compact_overrides("summary on")
    assert r1.overrides == {"show_summary": True}
    r2 = parse_compact_overrides("summary off")
    assert r2.overrides == {"show_summary": False}
    r3 = parse_compact_overrides("summary true")
    assert r3.overrides == {"show_summary": True}


def test_overrides_invalid_summary_records_error() -> None:
    result = parse_compact_overrides("summary maybe")
    assert "show_summary" not in result.overrides
    assert result.errors


def test_overrides_chars_value() -> None:
    result = parse_compact_overrides("chars 500")
    assert result.overrides == {"summary_max_chars": 500}


def test_overrides_focus_key() -> None:
    """`focus <text>` 设置 default_focus(只取第一个 token 作值,其余追加到 focus)."""
    result = parse_compact_overrides("focus 鉴权重构 补充说明")
    assert result.overrides["default_focus"] == "鉴权重构"
    assert result.focus == "补充说明"


def test_overrides_missing_value_records_error() -> None:
    result = parse_compact_overrides("keep")
    assert "keep_recent_ratio" not in result.overrides
    assert any("缺少值" in e for e in result.errors)


def test_overrides_unknown_key_goes_to_focus() -> None:
    """未识别的 key 被视作普通 token,追加到 focus."""
    result = parse_compact_overrides("--keep 0.2 鉴权")
    # 旧式 -- 标记被当普通 token(已无特殊处理)
    assert result.focus == "--keep 0.2 鉴权"
    assert result.overrides == {}


def test_overrides_key_without_value_keeps_as_focus() -> None:
    """`keep` 单独出现时(后面没有数字) → 走 error path,前 token 不被吞掉."""
    result = parse_compact_overrides("鉴权 keep")
    # keep 后面没有 value → error,但 tokens 之前已经全被消费
    assert any("keep" in e for e in result.errors)


def test_overrides_handles_quoted_strings() -> None:
    """shlex 解析:带引号的字符串作为一个 token."""
    result = parse_compact_overrides('focus "鉴权重构 补充"')
    assert result.overrides["default_focus"] == "鉴权重构 补充"


def test_known_override_keys_covers_all_keys() -> None:
    keys = known_override_keys()
    assert {"keep", "provider", "min", "summary", "chars", "focus"} <= keys


# === parse_compact_args(legacy shim) =====================================


def test_args_legacy_no_args_returns_defaults() -> None:
    args = parse_compact_args("/compact")
    assert args.focus == ""
    assert args.keep_recent_ratio is None
    assert args.provider_id is None
    assert args.min_messages is None


def test_args_legacy_focus_only() -> None:
    args = parse_compact_args("/compact 重构鉴权")
    assert args.focus == "重构鉴权"
    assert args.keep_recent_ratio is None


def test_args_legacy_keep_positional() -> None:
    """老式 --keep 不再特殊处理,但位置 keep 仍生效."""
    args = parse_compact_args("/compact keep 0.20")
    assert args.keep_recent_ratio == 0.20


def test_args_legacy_clamps_keep() -> None:
    args = parse_compact_args("/compact keep 2.0")
    assert args.keep_recent_ratio == 0.3
