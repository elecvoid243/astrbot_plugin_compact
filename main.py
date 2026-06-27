"""AstrBot /compact 插件入口。"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
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


@dataclass
class CompactArgs:
    """Parsed `/compact` command arguments."""

    focus: str = ""
    keep_recent_ratio: float | None = None
    provider_id: str | None = None
    config_provider_id: str = ""  # 来自插件配置 compress_provider_id


_KEEP_RE = re.compile(r"--keep\s+(\S+)")
_PROVIDER_RE = re.compile(r"--provider\s+(\S+)")
_KEEP_MAX = 0.3
_KEEP_MIN = 0.0


def parse_compact_args(text: str) -> CompactArgs:
    """Parse the raw command text after the `/compact` prefix.

    Supports three forms:
    - `/compact` (no args)
    - `/compact <focus topic>` (free text)
    - `/compact --keep <0~0.3> [--provider <id>] [<focus>]`

    Args:
        text: The full message string (e.g. `/compact 重构鉴权 --keep 0.2`).

    Returns:
        A CompactArgs dataclass. `keep_recent_ratio` is clamped to [0, 0.3];
        values outside the range are clamped silently. Non-numeric `--keep`
        values yield `None` so the caller falls back to plugin config.
    """
    body = text.strip()
    if body.lower().startswith("/compact"):
        body = body[len("/compact") :].strip()

    keep_match = _KEEP_RE.search(body)

    keep_value: float | None = None
    if keep_match:
        raw = keep_match.group(1)
        try:
            keep_value = float(raw)
        except ValueError:
            keep_value = None
        else:
            keep_value = max(_KEEP_MIN, min(_KEEP_MAX, keep_value))
        # Strip the flag from the body so the remainder is the focus topic
        body = (body[: keep_match.start()] + body[keep_match.end() :]).strip()

    # Re-search provider on the (possibly shortened) body so indices line up.
    provider_match = _PROVIDER_RE.search(body)

    provider_id: str | None = None
    if provider_match:
        provider_id = provider_match.group(1)
        body = (body[: provider_match.start()] + body[provider_match.end() :]).strip()

    return CompactArgs(
        focus=body,
        keep_recent_ratio=keep_value,
        provider_id=provider_id,
    )


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
        except Exception as e:
            logger.warning(f"[compact] failed to load config: {e}; using defaults")

    @filter.command("compact")
    async def compact(
        self,
        event: AstrMessageEvent,
        text: GreedyStr = GreedyStr(""),  # type: ignore[valid-type]
    ) -> None:
        """手动触发 LLM 摘要式上下文压缩。复用 AstrBot 内置 LLMSummaryCompressor。

        Args:
            event: The current AstrMessageEvent.
            text: GreedyStr capturing all text after `/compact`. Empty when
                the user issued `/compact` with no arguments.
        """
        umo = event.unified_msg_origin

        # 0. Help subcommand.
        raw = text.strip() if isinstance(text, str) else str(text)
        if raw.lower() == "help":
            event.set_result(
                MessageEventResult().message(
                    "📖 **/compact — 对话历史压缩帮助**\n\n"
                    "压缩当前对话历史，由 LLM 生成摘要以减少 token 占用。\n\n"
                    "**用法：**\n"
                    "  /compact                             全部默认\n"
                    "  /compact <聚焦话题>                   指定摘要关注点\n"
                    "  /compact --keep <0~0.3>              指定保留最近轮次比例\n"
                    "  /compact --provider <id>             指定 LLM provider\n"
                    "  /compact --provider <id> --keep <0~0.3> <聚焦话题>\n\n"
                    "**选项：**\n"
                    "  --keep <0~0.3>    保留最近消息比例（默认 0.15），超范围自动钳制\n"
                    "  --provider <id>   显式指定用于压缩的 LLM provider\n"
                    "  自由文本           聚焦话题，LLM 摘要将重点关注此内容\n\n"
                    "**示例：**\n"
                    "  /compact 重构鉴权\n"
                    "  /compact --provider deepseek-r1 --keep 0.20 重构鉴权\n\n"
                    "压缩完成后会显示「✅ 压缩完成」及摘要预览。",
                ),
            )
            return

        # 1. Parse command arguments.
        args = parse_compact_args("/compact " + text)
        args.config_provider_id = self.compress_provider_id

        # 2. Resolve provider (four-level fallback).
        provider = resolve_provider(self.context, umo, args)
        if provider is None:
            event.set_result(
                MessageEventResult().message(
                    "未找到可用的 LLM provider。请检查：\n"
                    "1. 当前会话是否绑定了 provider；\n"
                    "2. 或在插件配置中填写 `compress_provider_id`；\n"
                    "3. 或在 `/compact --provider <id>` 中显式指定。",
                ),
            )
            return

        # 3. Get current conversation.
        cid = await self.context.conversation_manager.get_curr_conversation_id(umo)
        if not cid:
            event.set_result(
                MessageEventResult().message(
                    "当前没有进行中的会话，请先发送一条消息。",
                ),
            )
            return

        conv = await self.context.conversation_manager.get_conversation(umo, cid)
        raw_history = conv.history if conv is not None else None
        # `conv.history` is stored as a JSON-encoded string (see
        # `ConversationManager._convert_conv_from_v2_to_v1`). Parsing it here
        # keeps `bind_checkpoint_messages` from iterating over individual chars.
        if isinstance(raw_history, str):
            try:
                history_dicts = json.loads(raw_history)
            except (TypeError, ValueError):
                history_dicts = []
        elif isinstance(raw_history, list):
            history_dicts = raw_history
        else:
            history_dicts = []
        messages = bind_checkpoint_messages(history_dicts)

        # 4. Below threshold → refuse.
        if len(messages) < self.min_messages:
            event.set_result(
                MessageEventResult().message(
                    f"消息数量过少（当前 {len(messages)} 条，"
                    f"少于最小阈值 {self.min_messages} 条），无需压缩。",
                ),
            )
            return

        # 5. Build Compactor with overridden settings.
        ratio = (
            args.keep_recent_ratio
            if args.keep_recent_ratio is not None
            else self.keep_recent_ratio
        )
        instruction = self._build_instruction(args.focus)
        compactor = Compactor(
            provider=provider,
            keep_recent_ratio=ratio,
            instruction_text=instruction,
        )

        # 5b. Send a "processing" message so the user knows compression is
        #     running (LLM calls may take a while).
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
                MessageEventResult().message(f"压缩失败：{e}"),
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
            head = f"✅ 压缩完成：{len(messages)} → {len(new_messages)} 条消息。"
        else:
            head = "ℹ️ 压缩未生效（消息量低于触发阈值或 LLM 未返回有效摘要）。"

        if self.show_summary:
            summary_excerpt = self._extract_summary(new_messages)
            truncated = self._truncate(summary_excerpt)
            reply_text = f"{head}\n\n📝 摘要预览：\n{truncated}"
        else:
            reply_text = head

        event.set_result(MessageEventResult().message(reply_text))

    # ----- helpers ----------------------------------------------------------

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

    def _truncate(self, text: str) -> str:
        """Truncate summary text to `summary_max_chars`.

        Args:
            text: The original (possibly long) summary string.

        Returns:
            The truncated string with an ellipsis suffix if it was too long.
        """
        if not text:
            return "（LLM 未返回摘要正文）"
        if len(text) <= self.summary_max_chars:
            return text
        return text[: self.summary_max_chars] + "..."


__all__ = [
    "CompactPlugin",
    "parse_compact_args",
    "resolve_provider",
    "CompactArgs",
]
