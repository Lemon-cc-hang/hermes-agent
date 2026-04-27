"""MicroCompact — lightweight context compressor (zero API calls).

Cleans up stale tool outputs in the message history before expensive
context compression (ContextCompressor) is triggered.
"""

import json
from typing import List, Dict, Any, Optional

# Tools whose output can be safely pruned when context grows large.
_COMPACTABLE_TOOLS = frozenset({
    "terminal",
    "execute_code",
    "search_files",
    "web_search",
    "web_extract",
    "read_file",
    "browser_navigate",
    "browser_snapshot",
    "vision_analyze",
})


class MicroCompact:
    """Zero-API-call context compressor for tool-output cleanup.

    Triggered when token usage is in the 50–75% range, *before* the
    expensive ContextCompressor (which fires at >=75%).
    """

    def __init__(self, threshold_tokens: int = 15000, protect_last_n: int = 4):
        self.threshold_tokens = threshold_tokens
        self.protect_last_n = protect_last_n
        self.compactable_tools = _COMPACTABLE_TOOLS
        self.pruned_count = 0
        self.total_cleared_chars = 0

    def compact(self, messages: list) -> list:
        """Return a copy of *messages* with old tool outputs truncated."""
        if not messages:
            return messages

        # Identify tool-result messages that can be pruned.
        prunable_indices = []
        for i, msg in enumerate(messages):
            if msg.get("role") != "tool":
                continue
            tool_name = self._infer_tool_name(msg)
            if tool_name and tool_name in self.compactable_tools:
                prunable_indices.append(i)

        if not prunable_indices:
            return messages

        # Always protect the most recent N tool outputs.
        protected = set(prunable_indices[-self.protect_last_n:])

        cleared_count = 0
        cleared_chars = 0
        for i in prunable_indices:
            if i in protected:
                continue
            msg = messages[i]
            old_content = msg.get("content", "")
            if isinstance(old_content, str) and len(old_content) > 200:
                cleared_chars += len(old_content)
                msg["content"] = f"[预约缩] 工具输出已清理 ({len(old_content)} chars -> summary)"
                cleared_count += 1

        self.pruned_count = cleared_count
        self.total_cleared_chars += cleared_chars
        return messages

    def _infer_tool_name(self, msg: dict) -> Optional[str]:
        """Infer the tool name from a tool-result message."""
        # OpenAI format: tool_call_id maps back to assistant message.
        tool_call_id = msg.get("tool_call_id")
        if not tool_call_id:
            return msg.get("name")
        return msg.get("name")

    def should_compact(self, approx_tokens: int, max_tokens: int) -> bool:
        """Return True when tokens are in the 50–75% window."""
        ratio = approx_tokens / max(max_tokens, 1)
        return 0.50 <= ratio < 0.75

    def get_stats(self) -> dict:
        return {
            "pruned_count": self.pruned_count,
            "total_cleared_chars": self.total_cleared_chars,
            "estimated_tokens_saved": self.total_cleared_chars // 4,
        }
