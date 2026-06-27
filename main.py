"""AstrBot /compact 插件入口。"""

from __future__ import annotations

import json
import shlex
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from astrbot import logger
from astrbot.api import star
from astrbot.api.all import MessageEventResult
from astrbot.api.event import AstrMessageEvent, MessageChain, filter
from astrbot.core.agent.message import (
    Message,
    bind_checkpoint_messages,
    dump_messages_with_checkpoints,
)
from astrbot.core.star.filter.command import GreedyStr

try:
    from .compressor import Compactor  # package context (AstrBot runtime)
except ImportError:  # pragma: no cover - direct script / pytest
    from compressor import Compactor

if TYPE_CHECKING:
    from astrbot.core.provider.provider import Provider

# 持久化 namespace, 来自 metadata.yaml 的 name 字段
CONFIG_NAMESPACE = "astrbot_plugin_compact"


def _parse_bool(s: str) -> bool | None:
    """Parse 'on'/'off'/'true'/'false'/1/0/yes/no into bool. None for invalid."""
    v = s.strip().lower()
    if v in {"on", "true", "1", "yes", "y", "enable", "enabled"}:
        return True
    if v in {"off", "false", "0", "no", "n", "disable", "disabled"}:
        return False
    return None


# canonical key -> plugin instance attribute name(供 set 同步到 self.*)
_CONFIG_ATTR_BY_KEY: dict[str, str] = {
    "compress_provider_id": "compress_provider_id",
    "keep_recent_ratio": "keep_recent_ratio",
    "min_messages": "min_messages",
    "show_summary": "show_summary",
    "summary_max_chars": "summary_max_chars",
    "default_focus": "default_focus",
}

# 短名 -> 配置项实际 key 的映射(用户友好)
_KEY_ALIASES: dict[str, str] = {
    "keep": "keep_recent_ratio",
    "provider": "compress_provider_id",
    "min": "min_messages",
    "focus": "default_focus",
    "summary": "show_summary",
    "chars": "summary_max_chars",
}

# 短名 -> (解析器, 是否数值)
_KEY_PARSERS: dict[str, tuple[callable, bool]] = {
    # key      # parse_fn            # is_numeric (clamp/min semantics)
    "keep": (float, True),
    "min": (int, True),
    "chars": (int, True),
    "provider": (str, False),
    "focus": (str, False),
    "summary": (_parse_bool, False),
}


_KEEP_MAX = 0.3
_KEEP_MIN = 0.0


@dataclass
class CompactArgs:
    """Parsed `/compact` subcommand arguments.

    Attributes:
        focus: Free-text focus topic. Empty when the user did not provide one.
        keep_recent_ratio: Override for `keep_recent_ratio`. None means use
            the plugin config default.
        provider_id: Override for `compress_provider_id`. None means use the
            plugin config default.
        min_messages: Override for `min_messages`. None means use config.
        show_summary: Override for `show_summary`. None means use config.
        summary_max_chars: Override for `summary_max_chars`. None means use config.
        default_focus: Override for `default_focus` (session-persistent).
            None means use config.
        config_provider_id: Filled by the caller with the plugin config value
            for the `compress_provider_id` (used as L2 in resolve_provider).
    """

    focus: str = ""
    keep_recent_ratio: float | None = None
    provider_id: str | None = None
    min_messages: int | None = None
    show_summary: bool | None = None
    summary_max_chars: int | None = None
    default_focus: str | None = None
    config_provider_id: str = ""


@dataclass
class ParseResult:
    """Result of parsing position-token subcommand args (run/preview/set).

    Carries raw string→typed overrides (with type errors surfaced) and
    a free-text focus topic.
    """

    overrides: dict[str, object] = field(default_factory=dict)
    focus: str = ""
    errors: list[str] = field(default_factory=list)


def parse_compact_args(text: str) -> CompactArgs:
    """Parse the raw command text after the `/compact` prefix (legacy form).

    Kept for backward compatibility with internal callers; the public surface
    is now ``parse_compact_overrides`` for positional-key-value forms.

    This function is a thin adapter: it accepts both the old ``--keep`` /
    ``--provider`` style and the new positional style, normalizing both into
    a :class:`CompactArgs`. New code should call ``parse_compact_overrides``
    directly.

    Args:
        text: The full message string (e.g. ``/compact 重构鉴权``).

    Returns:
        A :class:`CompactArgs` populated from the recognized tokens.
        Unknown tokens are appended to ``focus``.
    """
    body = text.strip()
    if body.lower().startswith("/compact"):
        body = body[len("/compact") :].strip()

    parsed = parse_compact_overrides(body)
    return CompactArgs(
        focus=parsed.focus,
        keep_recent_ratio=_as_float_override(parsed.overrides.get("keep_recent_ratio")),
        provider_id=_as_str_override(parsed.overrides.get("compress_provider_id")),
        min_messages=_as_int_override(parsed.overrides.get("min_messages")),
        show_summary=_as_bool_override(parsed.overrides.get("show_summary")),
        summary_max_chars=_as_int_override(parsed.overrides.get("summary_max_chars")),
        default_focus=_as_str_override(parsed.overrides.get("default_focus")),
    )


def _as_float_override(v: object) -> float | None:
    if v is None:
        return None
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return max(_KEEP_MIN, min(_KEEP_MAX, f))


def _as_int_override(v: object) -> int | None:
    if v is None:
        return None
    try:
        return int(v)
    except (TypeError, ValueError):
        return None


def _as_str_override(v: object) -> str | None:
    if v is None:
        return None
    s = str(v).strip()
    return s or None


def _as_bool_override(v: object) -> bool | None:
    if isinstance(v, bool):
        return v
    if isinstance(v, str):
        return _parse_bool(v)
    return None


def parse_compact_overrides(body: str) -> ParseResult:
    """Parse position-token overrides from a subcommand body.

    Recognized key aliases (any of):
    - ``keep <0~0.3>``  → ``keep_recent_ratio`` (clamped)
    - ``provider <id>``  → ``compress_provider_id``
    - ``min <int>``      → ``min_messages``
    - ``summary on/off`` → ``show_summary``
    - ``chars <int>``    → ``summary_max_chars``
    - ``focus <text>``   → ``default_focus``

    Tokens that don't form a key-value pair (or are unknown keys) are joined
    into ``focus`` with single spaces.

    Args:
        body: The text after ``/compact run`` (or ``preview``/``set``).

    Returns:
        A :class:`ParseResult` with typed overrides, the leftover focus text,
        and a list of per-token error messages (empty on success).
    """
    result = ParseResult()
    if not body or not body.strip():
        return result

    # shlex 处理引号转义,但保留原本中文标点的完整性
    try:
        tokens = shlex.split(body, posix=True)
    except ValueError:
        # shlex 失败时退回简单空白切分
        tokens = body.split()

    focus_tokens: list[str] = []
    i = 0
    while i < len(tokens):
        tok = tokens[i]
        if tok in _KEY_ALIASES:
            canonical = _KEY_ALIASES[tok]
            parser, is_numeric = _KEY_PARSERS[tok]
            if i + 1 >= len(tokens):
                result.errors.append(f"`{tok}` 后缺少值")
                i += 1
                continue
            raw_value = tokens[i + 1]
            try:
                parsed_value = parser(raw_value)
            except (TypeError, ValueError) as e:
                result.errors.append(f"`{tok} {raw_value}` 解析失败: {e}")
                i += 2
                continue
            if parsed_value is None:
                result.errors.append(
                    f"`{tok} {raw_value}` 解析失败: 期望 {tok} 的合法值",
                )
                i += 2
                continue
            # 数值类 key 额外校验正性
            if is_numeric:
                if canonical == "keep_recent_ratio":
                    parsed_value = max(_KEEP_MIN, min(_KEEP_MAX, float(parsed_value)))
                elif isinstance(parsed_value, (int, float)) and parsed_value < 0:
                    result.errors.append(f"`{tok} {raw_value}` 不可为负")
                    i += 2
                    continue
            result.overrides[canonical] = parsed_value
            i += 2
        else:
            focus_tokens.append(tok)
            i += 1

    result.focus = " ".join(focus_tokens)
    return result


def known_override_keys() -> set[str]:
    """Return the set of short-key aliases recognized by the parser.

    Used by ``/compact set`` to validate input and by ``/compact config``
    to enumerate the user-facing keys.
    """
    return set(_KEY_ALIASES.keys())


def resolve_provider(
    context: star.Context,
    umo: str,
    args: CompactArgs,
) -> "Provider | None":
    """Resolve which provider to use for compression.

    Priority (high → low, per spec §5.3):
    1. Command-line `--provider <id>` (via `args.provider_id`)
    2. Plugin config `compress_provider_id` (via `args.config_provider_id`)
    3. Core config `provider_settings.llm_compress_provider_id`
       (read from `context.get_config(umo=umo)`)
    4. Current chat provider for the session (`context.get_using_provider`)

    Each level falls through to the next on lookup failure (returns None
    or empty string).

    Args:
        context: The AstrBot plugin context.
        umo: Unified message origin (session id).
        args: The parsed command arguments. May carry an explicit provider id
            and a config-resolved provider id from the plugin's load step.

    Returns:
        The resolved `Provider` instance, or `None` if no provider is
        available (caller should refuse to compress).
    """
    # Level 1: command-line --provider
    if args.provider_id:
        explicit = context.get_provider_by_id(args.provider_id)
        if explicit is not None:
            return explicit

    # Level 2: plugin config `compress_provider_id`
    if args.config_provider_id:
        plug = context.get_provider_by_id(args.config_provider_id)
        if plug is not None:
            return plug

    # Level 3: core config `provider_settings.llm_compress_provider_id`
    core_provider_id = ""
    try:
        cfg = context.get_config(umo=umo)
        if isinstance(cfg, dict):
            provider_settings = cfg.get("provider_settings") or {}
            if isinstance(provider_settings, dict):
                core_provider_id = str(
                    provider_settings.get("llm_compress_provider_id", "")
                )
    except Exception:
        core_provider_id = ""
    if core_provider_id:
        core = context.get_provider_by_id(core_provider_id)
        if core is not None:
            return core

    # Level 4: current chat provider
    return context.get_using_provider(umo=umo)


class CompactPlugin(star.Star):
    """为 AstrBot 添加 `/compact` slash command。

    See spec: docs/superpowers/specs/2026-06-26-compact-plugin-design.md
    """

    def __init__(self, context: star.Context) -> None:
        super().__init__(context)
        self.context = context
        # 配置项默认值
        self.compress_provider_id: str = ""
        self.keep_recent_ratio: float = 0.15
        self.instruction_text: str = ""
        self.min_messages: int = 4
        self.show_summary: bool = True
        self.summary_max_chars: int = 800
        self.default_focus: str = ""  # 每次压缩默认附加的 focus 话题

    async def initialize(self) -> None:
        """AstrBot 在插件加载完成后回调此方法。

        读取 WebUI 写入的配置（如果有）。失败时使用默认值并打 warning。
        """
        try:
            cfg = self.context.get_config() or {}
            self.compress_provider_id = str(cfg.get("compress_provider_id", ""))
            ratio = float(cfg.get("keep_recent_ratio", 0.15))
            self.keep_recent_ratio = max(0.0, min(0.3, ratio))
            self.instruction_text = str(cfg.get("instruction_text", ""))
            self.min_messages = int(cfg.get("min_messages", 4))
            self.show_summary = bool(cfg.get("show_summary", True))
            self.summary_max_chars = int(cfg.get("summary_max_chars", 800))
            self.default_focus = str(cfg.get("default_focus", ""))
        except Exception as e:
            logger.warning(f"[compact] failed to load config: {e}; using defaults")

    # ===== /compact command group ============================================
    # 命令组本身是空壳,真正的逻辑分散在以下 6 个子命令方法中:
    # - compact_run: 实际执行压缩
    # - compact_help: 显示帮助信息
    # - compact_status: 显示当前会话的历史条数与可用 provider
    # - compact_preview: 预览压缩后条数(不真正调用 LLM)
    # - compact_config: 显示当前生效的压缩配置项
    # - compact_set: 持久化修改配置项(写到 AstrBot 插件配置磁盘)
    # =========================================================================

    @filter.command_group("compact")
    def compact(self):
        """对话历史压缩指令组。"""
        pass

    @compact.command("run")
    async def compact_run(
        self,
        event: AstrMessageEvent,
        text: GreedyStr = GreedyStr(""),  # type: ignore[valid-type]
    ) -> None:
        """`/compact run [key value ...] [focus]` — 实际执行 LLM 摘要式压缩。

        Args:
            event: The current AstrMessageEvent.
            text: GreedyStr capturing all text after `/compact run`.
                支持位置 key-value 覆盖,例如 ``keep 0.2 provider x 鉴权``。
        """
        parsed = parse_compact_overrides(str(text))
        if parsed.errors:
            # 不静默忽略 — 直接告诉用户
            event.set_result(
                MessageEventResult().message(
                    "⚠️ 参数解析存在问题:\n"
                    + "\n".join(f"- {e}" for e in parsed.errors)
                    + "\n\n已忽略错误项,使用其余参数继续。"
                ),
            )

        args = self._build_compact_args(parsed)
        await self._execute_compress(event, args)

    @compact.command("help")
    async def compact_help(self, event: AstrMessageEvent) -> None:
        """`/compact help` — 显示使用帮助。"""
        event.set_result(
            MessageEventResult().message(
                "📖 **/compact — 对话历史压缩帮助**\n\n"
                "压缩当前对话历史,由 LLM 生成摘要以减少 token 占用。\n\n"
                "**子命令:**\n"
                "  /compact run [选项] [聚焦话题]    执行压缩(核心功能)\n"
                "  /compact help                     显示本帮助\n"
                "  /compact status                   查看当前会话状态\n"
                "  /compact preview [选项] [聚焦]     预览压缩效果(不实际压缩)\n"
                "  /compact config                   查看生效的压缩配置\n"
                "  /compact set key value [...]       持久化修改配置项\n\n"
                "**run / preview 子命令的选项(位置参数,无横线):**\n"
                "  keep <0~0.3>       保留最近消息比例(超出范围自动钳制)\n"
                "  provider <id>      显式指定用于压缩的 LLM provider\n"
                "  min <int>          临时覆盖最小压缩阈值\n"
                "  summary on/off     临时覆盖是否显示摘要\n"
                "  chars <int>        临时覆盖摘要预览最大字符数\n"
                "  focus <text>       设置 default_focus\n"
                "  其余自由文本        临时聚焦话题,LLM 摘要将重点关注\n\n"
                "**示例:**\n"
                "  /compact run 鉴权重构\n"
                "  /compact run keep 0.2 provider deepseek-r1 鉴权\n"
                "  /compact preview keep 0.3\n"
                "  /compact set keep 0.20\n"
                "  /compact set provider deepseek-r1\n\n"
                "压缩完成后会显示「✅ 压缩完成」及摘要预览。"
            ),
        )

    @compact.command("status")
    async def compact_status(self, event: AstrMessageEvent) -> None:
        """`/compact status` — 显示当前会话的历史条数与最小压缩阈值。"""
        umo = event.unified_msg_origin
        cid = await self.context.conversation_manager.get_curr_conversation_id(umo)
        if not cid:
            event.set_result(
                MessageEventResult().message(
                    "当前没有进行中的会话，请先发送一条消息。"
                ),
            )
            return

        conv = await self.context.conversation_manager.get_conversation(umo, cid)
        history = self._load_history_dicts(conv)
        msg_count = len(history)
        provider = self.context.get_using_provider(umo=umo)
        provider_name = getattr(provider, "provider_id", None) or "未绑定"
        text = (
            f"📊 **当前会话状态**\n\n"
            f"- 历史消息条数: `{msg_count}`\n"
            f"- 最小压缩阈值: `{self.min_messages}` 条\n"
            f"- 当前 chat provider: `{provider_name}`\n"
            f"- 是否达到压缩门槛: "
            f"{'是 ✅' if msg_count >= self.min_messages else '否 ⏸'}\n"
        )
        event.set_result(MessageEventResult().message(text))

    @compact.command("preview")
    async def compact_preview(
        self,
        event: AstrMessageEvent,
        text: GreedyStr = GreedyStr(""),  # type: ignore[valid-type]
    ) -> None:
        """`/compact preview [key value ...] [focus]` — 预览压缩效果。

        不实际调用 LLM,只估算压缩后条数。支持位置 key-value 临时覆盖。

        Args:
            event: The current AstrMessageEvent.
            text: GreedyStr capturing all text after `/compact preview`.
        """
        parsed = parse_compact_overrides(str(text))
        if parsed.errors:
            event.set_result(
                MessageEventResult().message(
                    "⚠️ 参数解析存在问题:\n"
                    + "\n".join(f"- {e}" for e in parsed.errors)
                    + "\n\n已忽略错误项,使用其余参数继续。"
                ),
            )

        umo = event.unified_msg_origin
        args = self._build_compact_args(parsed)

        cid = await self.context.conversation_manager.get_curr_conversation_id(umo)
        if not cid:
            event.set_result(
                MessageEventResult().message(
                    "当前没有进行中的会话，请先发送一条消息。"
                ),
            )
            return

        conv = await self.context.conversation_manager.get_conversation(umo, cid)
        history = self._load_history_dicts(conv)
        msg_count = len(history)
        ratio = (
            args.keep_recent_ratio
            if args.keep_recent_ratio is not None
            else self.keep_recent_ratio
        )
        provider = resolve_provider(self.context, umo, args)
        provider_id = (
            getattr(provider, "provider_id", None) or "(无)"
            if provider is not None
            else "(无可用 provider)"
        )

        if msg_count < self.min_messages:
            text_msg = (
                f"🔍 **压缩预览(不实际执行)**\n\n"
                f"- 当前消息数: `{msg_count}` < 阈值 `{self.min_messages}`\n"
                f"- 预览结果: 不会触发压缩\n"
                f"- 选用 provider: `{provider_id}`\n"
            )
        else:
            # Estimate: 保留 ratio 比例的最近轮次 + 1 条 system + 1 条 summary
            keep = max(2, int(msg_count * ratio))
            est_after = keep + 2
            text_msg = (
                f"🔍 **压缩预览(不实际执行)**\n\n"
                f"- 当前消息数: `{msg_count}`\n"
                f"- 选用 keep ratio: `{ratio:.2f}`\n"
                f"- 选用 provider: `{provider_id}`\n"
                f"- 预计保留最近约: `{keep}` 条\n"
                f"- 预计压缩后总条数: `~{est_after}` 条 (含 system + summary)\n"
                f"- 预计节省: `~{max(0, msg_count - est_after)}` 条\n\n"
                f"使用 `/compact run ...` 真正执行压缩。"
            )
        event.set_result(MessageEventResult().message(text_msg))

    @compact.command("config")
    async def compact_config(self, event: AstrMessageEvent) -> None:
        """`/compact config` — 显示当前生效的压缩配置项。"""
        text = (
            "⚙️ **当前生效的压缩配置**\n\n"
            f"- `compress_provider_id`: "
            f"`{self.compress_provider_id or '(空,回退到会话 provider)'}`\n"
            f"- `keep_recent_ratio`: `{self.keep_recent_ratio}` "
            f"(合法范围 0~0.3)\n"
            f"- `instruction_text`: "
            f"`{self.instruction_text or '(空,使用 LLM 默认指令)'}`\n"
            f"- `min_messages`: `{self.min_messages}` (低于此数量不压缩)\n"
            f"- `show_summary`: `{self.show_summary}` "
            f"(是否在回复中展示摘要)\n"
            f"- `summary_max_chars`: `{self.summary_max_chars}` "
            f"(摘要预览最大字符数)\n"
            f"- `default_focus`: "
            f"`{self.default_focus or '(空)'}` "
            f"(每次压缩默认聚焦的话题)\n\n"
            f"用 `/compact set <key> <value>` 持久化修改。"
        )
        event.set_result(MessageEventResult().message(text))

    @compact.command("set")
    async def compact_set(
        self,
        event: AstrMessageEvent,
        text: GreedyStr = GreedyStr(""),  # type: ignore[valid-type]
    ) -> None:
        """`/compact set key value [key value ...]` — 持久化修改配置项。

        支持一次修改多项(全部生效,任意一项失败则全部回滚写盘动作)。
        立即在本次进程内生效(更新 self.*),同时调用 AstrBot 的
        ``update_config`` API 写入磁盘。

        Args:
            event: The current AstrMessageEvent.
            text: GreedyStr capturing all text after `/compact set`.
        """
        parsed = parse_compact_overrides(str(text))
        if parsed.errors:
            event.set_result(
                MessageEventResult().message(
                    "❌ /compact set 参数解析失败:\n"
                    + "\n".join(f"- {e}" for e in parsed.errors)
                ),
            )
            return
        if not parsed.overrides:
            event.set_result(
                MessageEventResult().message(
                    "ℹ️ /compact set 需要至少一个 key value 对。\n"
                    "可用 key: "
                    + ", ".join(sorted(known_override_keys()))
                    + "\n示例: `/compact set keep 0.20`"
                ),
            )
            return
        if parsed.focus:
            # set 不接受 focus(否则会与"设置 default_focus"混淆)
            event.set_result(
                MessageEventResult().message(
                    f"⚠️ /compact set 不接受额外自由文本:"
                    f" `{parsed.focus}`\n"
                    "如需设置 default_focus,使用: "
                    "`/compact set focus <text>`"
                ),
            )
            return

        # 0. set 不接受自由文本(set 阶段不应有 focus 残留)
        if parsed.focus:
            event.set_result(
                MessageEventResult().message(
                    f"⚠️ /compact set 不接受额外自由文本:"
                    f" `{parsed.focus}`\n"
                    "如需设置 default_focus,使用: "
                    "`/compact set focus <text>`"
                ),
            )
            return

        # 1. 本进程内立即生效
        applied: list[str] = []
        for canonical_key, value in parsed.overrides.items():
            attr = _CONFIG_ATTR_BY_KEY.get(canonical_key)
            if attr is None:
                continue
            setattr(self, attr, value)
            applied.append(f"{canonical_key} = {value!r}")

        # 2. 写盘
        persist_errors: list[str] = []
        for canonical_key, value in parsed.overrides.items():
            try:
                from astrbot.core.star.config import update_config

                update_config(CONFIG_NAMESPACE, canonical_key, value)
            except Exception as e:
                persist_errors.append(f"{canonical_key}: {e}")

        if persist_errors:
            event.set_result(
                MessageEventResult().message(
                    "⚠️ 已更新本次进程内的配置,但写盘失败:\n"
                    + "\n".join(f"- {e}" for e in persist_errors)
                    + "\n\n重启插件后这些改动会丢失。"
                ),
            )
            return

        event.set_result(
            MessageEventResult().message(
                "✅ 配置已持久化保存:\n" + "\n".join(f"- {line}" for line in applied)
            ),
        )

    # ===== helpers ==========================================================

    def _build_compact_args(self, parsed: ParseResult) -> CompactArgs:
        """Build a :class:`CompactArgs` from a parsed subcommand result.

        Merges session-level overrides (focus text + key-value overrides)
        with the plugin-level ``compress_provider_id`` (read at init time).
        """
        return CompactArgs(
            focus=parsed.focus,
            keep_recent_ratio=_as_float_override(
                parsed.overrides.get("keep_recent_ratio")
            ),
            provider_id=_as_str_override(parsed.overrides.get("compress_provider_id")),
            min_messages=_as_int_override(parsed.overrides.get("min_messages")),
            show_summary=_as_bool_override(parsed.overrides.get("show_summary")),
            summary_max_chars=_as_int_override(
                parsed.overrides.get("summary_max_chars")
            ),
            default_focus=_as_str_override(parsed.overrides.get("default_focus")),
            config_provider_id=self.compress_provider_id,
        )

    # ===== shared core =======================================================

    async def _execute_compress(
        self,
        event: AstrMessageEvent,
        args: CompactArgs,
    ) -> None:
        """Execute the LLM-summary compression flow.

        由 ``compact_run`` 调用,内部使用 ``args`` 中的所有临时覆盖
        (focus / keep_recent_ratio / provider_id / min_messages /
        show_summary / summary_max_chars / default_focus)。

        Args:
            event: The current AstrMessageEvent.
            args: Parsed command arguments. Must have ``config_provider_id`` set
                by the caller.
        """
        umo = event.unified_msg_origin

        # 应用覆盖:每个字段优先用 args 中的临时值,缺省回退到 self.*
        effective_min = (
            args.min_messages if args.min_messages is not None else self.min_messages
        )
        effective_show_summary = (
            args.show_summary if args.show_summary is not None else self.show_summary
        )
        effective_chars = (
            args.summary_max_chars
            if args.summary_max_chars is not None
            else self.summary_max_chars
        )
        # default_focus 优先用 args 临时值(本次) -> self.default_focus(持久)
        effective_default_focus = (
            args.default_focus if args.default_focus is not None else self.default_focus
        )

        # 1. Resolve provider (four-level fallback).
        provider = resolve_provider(self.context, umo, args)
        if provider is None:
            event.set_result(
                MessageEventResult().message(
                    "未找到可用的 LLM provider。请检查：\n"
                    "1. 当前会话是否绑定了 provider；\n"
                    "2. 或在插件配置中填写 `compress_provider_id`；\n"
                    "3. 或在 `/compact run provider <id>` 中显式指定。",
                ),
            )
            return

        # 2. Get current conversation.
        cid = await self.context.conversation_manager.get_curr_conversation_id(umo)
        if not cid:
            event.set_result(
                MessageEventResult().message(
                    "当前没有进行中的会话，请先发送一条消息。",
                ),
            )
            return

        conv = await self.context.conversation_manager.get_conversation(umo, cid)
        history_dicts = self._load_history_dicts(conv)
        messages = bind_checkpoint_messages(history_dicts)

        # 3. Below threshold → refuse.
        if len(messages) < effective_min:
            event.set_result(
                MessageEventResult().message(
                    f"消息数量过少(当前 {len(messages)} 条,"
                    f"少于最小阈值 {effective_min} 条),无需压缩。",
                ),
            )
            return

        # 4. Build Compactor with overridden settings.
        ratio = (
            args.keep_recent_ratio
            if args.keep_recent_ratio is not None
            else self.keep_recent_ratio
        )
        # focus 优先级:args.focus(本次) > effective_default_focus(持久默认)
        effective_focus = args.focus or effective_default_focus
        instruction = self._build_instruction(effective_focus)
        compactor = Compactor(
            provider=provider,
            keep_recent_ratio=ratio,
            instruction_text=instruction,
        )

        # 5. Send a "processing" message so the user knows compression is
        #    running (LLM calls may take a while).
        try:
            await event.send(
                MessageChain().message("⏳ 正在压缩对话历史，请稍候..."),
            )
        except Exception:
            logger.warning(
                "[compact] failed to send processing message, continuing anyway"
            )

        # 6. Run compression. The inner LLMSummaryCompressor swallows
        #    LLM errors and returns the original list unchanged (per spec §6).
        try:
            new_messages = await compactor(messages)
        except Exception as e:
            logger.error(f"[compact] unexpected error during compression: {e}")
            event.set_result(
                MessageEventResult().message(f"压缩失败:{e}"),
            )
            return

        # 7. Save the new history.
        new_history = dump_messages_with_checkpoints(new_messages)
        await self.context.conversation_manager.update_conversation(
            umo,
            cid,
            new_history,
        )

        # 8. Reply with a status message (optionally including a summary excerpt).
        compressed = new_messages != messages
        if compressed:
            head = f"✅ 压缩完成:{len(messages)} → {len(new_messages)} 条消息。"
        else:
            head = "ℹ️ 压缩未生效(消息量低于触发阈值或 LLM 未返回有效摘要)。"

        if effective_show_summary:
            summary_excerpt = self._extract_summary(new_messages)
            truncated = self._truncate(summary_excerpt, effective_chars)
            reply_text = f"{head}\n\n📝 摘要预览:\n{truncated}"
        else:
            reply_text = head

        event.set_result(MessageEventResult().message(reply_text))

    # ----- helpers ----------------------------------------------------------

    @staticmethod
    def _load_history_dicts(conv) -> list:
        """Load raw history dicts from a Conversation-like object.

        ``conv.history`` is stored as a JSON-encoded string by AstrBot's
        ``ConversationManager._convert_conv_from_v2_to_v1``. Parsing it here
        keeps downstream code (e.g. ``bind_checkpoint_messages``) from
        iterating over individual characters when the storage is a string.

        Args:
            conv: A conversation object (or None).

        Returns:
            A list of message dicts. Empty when the conversation is missing
            or the history is malformed.
        """
        raw = conv.history if conv is not None else None
        if isinstance(raw, str):
            try:
                return json.loads(raw)
            except (TypeError, ValueError):
                return []
        if isinstance(raw, list):
            return raw
        return []

    def _build_instruction(self, focus: str) -> str | None:
        """Combine user-configured instruction with the focus topic.

        Args:
            focus: The optional focus topic from the command line.

        Returns:
            A combined instruction string, or None if neither is set so
            the inner compressor falls back to its built-in default.
        """
        if not focus:
            return self.instruction_text or None
        if self.instruction_text:
            return f"{self.instruction_text}\n\n重点关注：{focus}"
        return f"重点关注：{focus}"

    @staticmethod
    def _extract_summary(messages: list[Message]) -> str:
        """Pull the summary text out of the compressed message list.

        Looks for a user-role message whose content is a non-empty string.
        The inner LLMSummaryCompressor emits a user-role "compressed summary"
        message; we scan for the first such message and return its body.

        Args:
            messages: The compressed message list.

        Returns:
            The summary text, or an empty string if none is found.
        """
        for m in messages:
            if m.role == "user" and isinstance(m.content, str) and m.content.strip():
                return m.content
        return ""

    def _truncate(self, text: str, max_chars: int | None = None) -> str:
        """Truncate summary text to ``max_chars`` (or ``self.summary_max_chars``).

        Args:
            text: The original (possibly long) summary string.
            max_chars: Override for the truncation length. ``None`` falls back
                to ``self.summary_max_chars``.

        Returns:
            The truncated string with an ellipsis suffix if it was too long.
        """
        if not text:
            return "(LLM 未返回摘要正文)"
        limit = max_chars if max_chars is not None else self.summary_max_chars
        if limit <= 0:
            return text
        if len(text) <= limit:
            return text
        return text[:limit] + "..."


__all__ = [
    "CompactPlugin",
    "parse_compact_args",
    "parse_compact_overrides",
    "known_override_keys",
    "resolve_provider",
    "CompactArgs",
    "ParseResult",
]
