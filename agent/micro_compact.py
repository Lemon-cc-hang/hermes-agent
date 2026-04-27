"""MicroCompact — zero-API-call lightweight context compression.

Inspired by Claude Code's MicroCompact layer.  Clears old tool output
content without calling an auxiliary LLM, preserving tool call records
so the model knows the tool was executed.

Strategy:
1. Identify compactable tool results (terminal, read_file, search_files, etc.)
2. Apply time-decay: oldest outputs are cleared first
3. Replace content with a placeholder message
4. Zero API calls, zero cost, zero latency

This sits BEFORE the API-based ContextCompressor in the compression
pipeline.  It fires when token usage is 50-75%, while the full
summarizer fires at >= 75%.
"""

import logging
from typing import Any, Dict, List, Optional, Set

logger = logging.getLogger(__name__)

# Tools whose output can be safely cleared (the model only needs to know
# the tool was called, not the full output).
_COMPACTABLE_TOOLS: Set[str] = {
    "terminal",
    "read_file",
    "search_files",
    "web_search",
    "web_extract",
    "vision_analyze",
    "browser_navigate",
    "browser_snapshot",
    "browser_click",
    "browser_get_images",
    "browser_vision",
    "browser_console",
}

# Placeholder message — must be short but informative.
_PRUNED_TOOL_PLACEHOLDER = "[Old tool output cleared by MicroCompact to save context space]"

# Minimum messages to keep after MicroCompact.
_MIN_MESSAGES_TO_KEEP = 6


class MicroCompact:
    """Zero-API-call context compressor for tool outputs."""

    def __init__(self, compactable_tools: Optional[Set[str]] = None) -> None:
        self.compactable_tools = compactable_tools or _COMPACTABLE_TOOLS
        self.pruned_count = 0
        self.total_cleared_chars = 0

    def compact(
        self,
        messages: List[Dict[str, Any]],
        target_token_reduction: float = 0.15,
    ) -> List[Dict[str, Any]]:
        """Clear old tool outputs to reduce token usage.

        Args:
            messages: Full conversation message list.
            target_token_reduction: Fraction of total content to aim to clear.

        Returns:
            Messages list with old tool outputs replaced by placeholders.
        """
        if not messages:
            return messages

        # Identify tool-result messages that are compactable.
        # We scan from oldest to newest, but skip the most recent N turns.
        protect_last_n = 4  # Keep last 4 turns intact
        protect_first_n = 2  # Keep system + first user message

        prunable_indices = []
        for i, msg in enumerate(messages):
            # Protect head and tail
            if i < protect_first_n or i >= len(messages) - protect_last_n:
                continue

            if msg.get("role") != "tool":
                continue

            # Check if this tool result is from a compactable tool
            tool_name = msg.get("tool_name", "")
            if not tool_name:
                # Try to infer from content patterns
                tool_name = self._infer_tool_name(msg)

            if tool_name in self.compactable_tools:
                content = msg.get("content", "")
                content_len = len(content) if isinstance(content, str) else 0
                prunable_indices.append((i, content_len))

        if not prunable_indices:
            logger.debug("MicroCompact: no prunable tool outputs found")
            return messages

        # Sort by index (oldest first) — time-decay: clear oldest first
        prunable_indices.sort(key=lambda x: x[0])

        # Calculate how many to clear to reach target reduction.
        total_chars = sum(len(str(m.get("content", ""))) for m in messages)
        target_chars_to_clear = int(total_chars * target_token_reduction)

        cleared_chars = 0
        cleared_count = 0
        result = list(messages)  # Shallow copy

        for idx, content_len in prunable_indices:
            if cleared_chars >= target_chars_to_clear:
                break

            msg = result[idx]
            old_content = msg.get("content", "")
            old_content_len = len(old_content) if isinstance(old_content, str) else 0

            # Replace with placeholder
            msg["content"] = _PRUNED_TOOL_PLACEHOLDER
            # Keep metadata about what was cleared
            msg["_microcompact_cleared"] = True
            msg["_microcompact_original_length"] = old_content_len

            cleared_chars += old_content_len
            cleared_count += 1

        self.pruned_count = cleared_count
        self.total_cleared_chars = cleared_chars

        if cleared_count > 0:
            logger.info(
                "MicroCompact: cleared %d tool outputs (%d chars, ~%d tokens), "
                "preserving %d messages",
                cleared_count,
                cleared_chars,
                cleared_chars // 4,  # Rough estimate
                len(result),
            )

        return result

    def _infer_tool_name(self, msg: Dict[str, Any]) -> str:
        """Infer tool name from a tool result message when tool_name is missing."""
        content = str(msg.get("content", ""))

        # Pattern matching for common tool outputs
        if content.startswith("{" ) and "\"matches\"" in content[:200]:
            return "search_files"
        if "LINE_NUM|CONTENT" in content[:100]:
            return "read_file"
        if content.startswith("{" ) and "\"output\"" in content[:200]:
            return "terminal"
        if content.startswith("http") and ("image" in content.lower() or "url" in content.lower()):
            return "vision_analyze"

        return ""

    def get_stats(self) -> Dict[str, Any]:
        """Return compaction statistics."""
        return {
            "pruned_count": self.pruned_count,
            "total_cleared_chars": self.total_cleared_chars,
            "estimated_tokens_saved": self.total_cleared_chars // 4,
        }
