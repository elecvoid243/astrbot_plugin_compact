"""API 隔离层：所有对 astrbot.core.agent.context.* 的引用都集中在这里。

当 AstrBot 内部类改名/移位时，维护者只需修改本文件。
"""

from __future__ import annotations

from astrbot.core.agent.context.compressor import LLMSummaryCompressor
from astrbot.core.agent.context.token_counter import EstimateTokenCounter
from astrbot.core.agent.message import Message
from astrbot.core.provider.provider import Provider


class Compactor:
    """Thin wrapper around `LLMSummaryCompressor`.

    The single insulation layer between this plugin and AstrBot's
    internal context-compression API. See spec §3.3.
    """

    def __init__(
        self,
        provider: Provider,
        keep_recent_ratio: float = 0.15,
        instruction_text: str | None = None,
    ) -> None:
        """Initialize the wrapper.

        Args:
            provider: The LLM provider used to generate the summary.
            keep_recent_ratio: Ratio of current context tokens to keep as
                exact recent context. Clamped to [0, 0.3] by the inner compressor.
            instruction_text: Optional custom summary instruction. Falls back
                to the default built into `LLMSummaryCompressor`.
        """
        self._compressor = LLMSummaryCompressor(
            provider=provider,
            keep_recent_ratio=keep_recent_ratio,
            instruction_text=instruction_text,
            token_counter=EstimateTokenCounter(),
        )

    async def __call__(self, messages: list[Message]) -> list[Message]:
        """Run LLM-based compression on the given message list.

        Args:
            messages: The original conversation history as Pydantic Message
                objects. The full list is processed; the inner compressor
                decides what becomes "summary" and what stays verbatim.

        Returns:
            The compressed message list (system messages + summary pair +
            recent rounds). The exact shape is owned by `LLMSummaryCompressor`.

        Failure contract (per spec §6):
            On LLM failure, the inner `LLMSummaryCompressor` catches and logs
            the error and returns the original `messages` unchanged. Compactor
            is a pure pass-through and does not re-raise. Callers must compare
            the returned list to the input to detect a no-op compression.
        """
        return await self._compressor(messages)
