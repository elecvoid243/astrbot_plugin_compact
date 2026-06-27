"""`/compact` 命令参数解析的单元测试。"""

from __future__ import annotations

from main import parse_compact_args


def test_parse_no_args_returns_defaults() -> None:
    args = parse_compact_args("/compact")
    assert args.focus == ""
    assert args.keep_recent_ratio is None
    assert args.provider_id is None


def test_parse_focus_topic_only() -> None:
    args = parse_compact_args("/compact 重构鉴权逻辑")
    assert args.focus == "重构鉴权逻辑"
    assert args.keep_recent_ratio is None
    assert args.provider_id is None


def test_parse_keep_flag() -> None:
    args = parse_compact_args("/compact --keep 0.20")
    assert args.focus == ""
    assert args.keep_recent_ratio == 0.20
    assert args.provider_id is None


def test_parse_provider_flag() -> None:
    args = parse_compact_args("/compact --provider deepseek-r1")
    assert args.focus == ""
    assert args.provider_id == "deepseek-r1"


def test_parse_combined() -> None:
    args = parse_compact_args(
        "/compact 重构鉴权逻辑 --keep 0.20 --provider deepseek-r1"
    )
    assert args.focus == "重构鉴权逻辑"
    assert args.keep_recent_ratio == 0.20
    assert args.provider_id == "deepseek-r1"


def test_parse_combined_flag_before_focus() -> None:
    args = parse_compact_args("/compact --provider x 重构鉴权逻辑 --keep 0.1")
    assert args.focus == "重构鉴权逻辑"
    assert args.keep_recent_ratio == 0.1
    assert args.provider_id == "x"


def test_parse_clamps_keep_ratio() -> None:
    # Out-of-range values are clamped silently
    args_high = parse_compact_args("/compact --keep 2.0")
    assert args_high.keep_recent_ratio == 0.3
    args_low = parse_compact_args("/compact --keep -1.0")
    assert args_low.keep_recent_ratio == 0.0


def test_parse_invalid_keep_falls_back_to_none() -> None:
    args = parse_compact_args("/compact --keep abc")
    assert args.keep_recent_ratio is None
