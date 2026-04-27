"""LLMLingua context engine plugin for Hermes Agent.

Optional dependency: ``pip install llmlingua`` (or ``uv pip install llmlingua``).
If llmlingua is not installed the engine reports ``is_available() == False``
and run_agent.py falls back to the built-in ContextCompressor.

Configuration (config.yaml):
    context:
        engine: "llmlingua"
        llmlingua:
            model: "microsoft/llmlingua-2-xlm-roberta-large-meetingbank"  # default
            rate: 0.5          # compression target ratio (0-1)
            force_cpu: false   # set true to disable MPS on Apple Silicon
            threshold_percent: 0.75
            protect_first_n: 3
            protect_last_n: 6

The engine reuses the built-in compressor's pruning / boundary logic and
replaces only the summarisation step with LLMLingua's token-level prompt
compression.  This preserves the original text structure (addresses P2-3).
"""

from __future__ import annotations

import json
import logging
import re
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

try:
    import torch
    _HAS_TORCH = True
except Exception:
    _HAS_TORCH = False

# ── Optional dependency guard ─────────────────────────────────────────────────────────────────────────────────────
_HAS_LLMLINGUA = False
try:
    from llmlingua import PromptCompressor
    _HAS_LLMLINGUA = True
except Exception as _e:  # pragma: no cover
    logger.debug("llmlingua not available: %s", _e)

# Import only what we need from the built-in compressor (avoid heavy deps)
from agent.context_engine import ContextEngine

# Inline constants to avoid importing context_compressor (which pulls openai)
SUMMARY_PREFIX = (
    "[CONTEXT COMPACTION — REFERENCE ONLY] Earlier turns were compacted "
    "into the summary below. This is a handoff from a previous context "
    "window — treat it as background reference, NOT as active instructions. "
    "Do NOT answer questions or fulfill requests mentioned in this summary; "
    "they were already addressed. "
    "Your current task is identified in the '## Active Task' section of the "
    "summary — resume exactly from there. "
    "Respond ONLY to the latest user message "
    "that appears AFTER this summary. The current session state (files, "
    "config, etc.) may reflect work described here — avoid repeating it:"
)
SUMMARY_PREFIX_RE = re.compile(
    r"\[CONTEXT\s+(COMPACTION|SUMMARY)"
)


# ── Plugin registration ──────────────────────────────────────────────────

def register(ctx):
    """Plugin entry point used by the context-engine discovery system."""
    ctx.register_context_engine(LLMLinguaCompressor())


# ── Engine implementation ──────────────────────────────────────────────

class LLMLinguaCompressor(ContextEngine):
    """Context engine that uses microsoft/LLMLingua for token-level compression.

    * Optional dependency — gracefully degrades to built-in compressor when
      llmlingua is not installed.
    * Reuses ContextCompressor's ``_prune_old_tool_results`` and ``_find_tail_cut_by_tokens``
      boundary logic so behaviour is consistent with the default engine.
    * Compression is **non-generative** (token dropping) which preserves
      original text structure — this avoids the P2-3 read_file loop caused
      by LLM summarisation discarding content previews.
    """

    name: str = "llmlingua"

    # ── Construction / lazy init ───────────────────────────────────────

    def __init__(
        self,
        model: str = "microsoft/llmlingua-2-xlm-roberta-large-meetingbank",
        rate: float = 0.5,
        force_cpu: bool = False,
        threshold_percent: float = 0.75,
        protect_first_n: int = 3,
        protect_last_n: int = 6,
        quiet_mode: bool = False,
        **kwargs,
    ):
        super().__init__()
        self._model_name = model
        self._rate = max(0.1, min(1.0, rate))
        self._force_cpu = force_cpu
        self.threshold_percent = threshold_percent
        self.protect_first_n = protect_first_n
        self.protect_last_n = protect_last_n
        self.quiet_mode = quiet_mode

        # Lazy-loaded compressor instance
        self._compressor: Any = None
        self._compressor_error: Optional[str] = None

        # Token state (mirrors ContextCompressor)
        self.last_prompt_tokens = 0
        self.last_completion_tokens = 0
        self.last_total_tokens = 0
        self.threshold_tokens = 0
        self.context_length = 0
        self.compression_count = 0

        # Reuse built-in compressor for pruning / boundary helpers
        self._builtin = None  # lazy import to avoid heavy deps

    # ── Availability check ───────────────────────────────────────────────

    @classmethod
    def is_available(cls) -> bool:
        return _HAS_LLMLINGUA

    # ── ContextEngine interface ────────────────────────────────────────────

    def update_from_response(self, usage: Dict[str, Any]) -> None:
        """Update tracked token usage from an API response."""
        self.last_prompt_tokens = usage.get("prompt_tokens", 0)
        self.last_completion_tokens = usage.get("completion_tokens", 0)
        self.last_total_tokens = usage.get("total_tokens", 0)

    def should_compress(self, prompt_tokens: int = None) -> bool:
        """Return True if compaction should fire this turn."""
        if self.context_length <= 0:
            return False
        tokens = prompt_tokens or self.last_prompt_tokens
        return tokens >= self.threshold_tokens

    def compress(
        self,
        messages: List[Dict[str, Any]],
        current_tokens: int = None,
        focus_topic: str = None,
    ) -> List[Dict[str, Any]]:
        """Compact the message list using LLMLingua token-level compression.

        Steps:
        1. Prune old tool results (dedup stubs + truncation) — reuse builtin.
        2. Identify head / tail protected regions — reuse builtin token-budget logic.
        3. Serialize the middle region for LLMLingua.
        4. Run LLMLingua ``compress_prompt`` (token dropping, not generation).
        5. Assemble final message list with compressed middle region.
        """
        self.compression_count += 1

        # ── Step 1: prune old tool results ────────────────────────────────
        builtin = self._get_builtin()
        messages, _pruned = builtin._prune_old_tool_results(
            messages,
            protect_tail_count=self.protect_last_n,
            protect_tail_tokens=getattr(builtin, "tail_token_budget", None),
        )

        # ── Step 2: find compression boundaries using unified token-budget logic ──
        compress_start = self.protect_first_n
        compress_end = builtin._find_tail_cut_by_tokens(
            messages, head_end=compress_start
        )
        if compress_start >= compress_end:
            return messages

        # ── Step 3: serialize middle region ────────────────────────────────
        head = messages[:compress_start]
        middle = messages[compress_start:compress_end]
        tail = messages[compress_end:]

        serialized = self._serialize_messages(middle)
        if not serialized:
            return messages

        # ── Step 4: LLMLingua compression ────────────────────────────────
        compressed_text = self._compress_with_llmlingua(serialized, focus_topic)
        if compressed_text is None:
            if not self.quiet_mode:
                logger.warning("LLMLingua compression failed, falling back to builtin summariser")
            return self._fallback_builtin_compress(messages, current_tokens, focus_topic)

        # ── Step 5: assemble result ───────────────────────────────────────
        compressed_message = {
            "role": "system",
            "content": f"{SUMMARY_PREFIX}\n\n[Earlier conversation compressed with LLMLingua]\n\n{compressed_text}",
        }
        return head + [compressed_message] + tail

    def _fallback_builtin_compress(
        self,
        messages: List[Dict[str, Any]],
        current_tokens: int = None,
        focus_topic: str = None,
    ) -> List[Dict[str, Any]]:
        """Fallback to builtin compressor when LLMLingua fails."""
        builtin = self._get_builtin()
        # Ensure builtin has required state for compress()
        if not hasattr(builtin, "model"):
            builtin.model = "gpt-4o-mini"
        if not hasattr(builtin, "context_length"):
            builtin.context_length = self.context_length
        if not hasattr(builtin, "threshold_tokens"):
            builtin.threshold_tokens = self.threshold_tokens
        if not hasattr(builtin, "summary_target_ratio"):
            builtin.summary_target_ratio = 0.25
        if not hasattr(builtin, "compression_count"):
            builtin.compression_count = self.compression_count
        if not hasattr(builtin, "tail_token_budget"):
            builtin.tail_token_budget = int(self.threshold_tokens * 0.25)
        if not hasattr(builtin, "max_summary_tokens"):
            builtin.max_summary_tokens = int(self.context_length * 0.05)
        return builtin.compress(messages, current_tokens, focus_topic)

    def _get_builtin(self):
        """Lazy-load the built-in compressor for pruning / boundary helpers."""
        if self._builtin is None:
            from agent.context_compressor import ContextCompressor
            self._builtin = ContextCompressor.__new__(ContextCompressor)
            self._builtin.protect_first_n = self.protect_first_n
            self._builtin.protect_last_n = self.protect_last_n
            self._builtin.quiet_mode = self.quiet_mode
            # Copy token-budget related fields needed by _find_tail_cut_by_tokens
            self._builtin.tail_token_budget = int(
                self.threshold_tokens * 0.25 if self.threshold_tokens else 0
            )
        return self._builtin

    # ── Optional: pre-flight check ──────────────────────────────────────────

    def should_compress_preflight(self, messages: List[Dict[str, Any]]) -> bool:
        """Quick rough check before the API call.

        Uses a cheap character-count heuristic (≈ 4 chars / token) to avoid
        an expensive tokeniser call on every turn.
        """
        if self.context_length <= 0:
            return False
        total_chars = sum(len(m.get("content", "")) for m in messages)
        estimated_tokens = total_chars // 4
        return estimated_tokens >= self.threshold_tokens

    # ── Session lifecycle ──────────────────────────────────────────────────

    def on_session_start(self, session_id: str, **kwargs) -> None:
        """Lazy-load the LLMLingua compressor on first session start."""
        self._ensure_compressor()

    def on_session_reset(self) -> None:
        super().on_session_reset()
        self._compressor = None
        self._compressor_error = None

    # ── Model switch support ──────────────────────────────────────────────

    def update_model(
        self,
        model: str,
        context_length: int,
        base_url: str = "",
        api_key: str = "",
        provider: str = "",
        api_mode: str = "",
    ) -> None:
        self.context_length = context_length
        self.threshold_tokens = int(context_length * self.threshold_percent)
        # Update builtin's token budget too
        if self._builtin is not None:
            self._builtin.context_length = context_length
            self._builtin.threshold_tokens = self.threshold_tokens
            self._builtin.tail_token_budget = int(self.threshold_tokens * 0.25)

    # ── Status ───────────────────────────────────────────────────────

    def get_status(self) -> Dict[str, Any]:
        status = super().get_status()
        status.update({
            "engine": self.name,
            "model": self._model_name,
            "rate": self._rate,
            "compressor_ready": self._compressor is not None,
            "compressor_error": self._compressor_error,
        })
        return status

    # ── Internal helpers ───────────────────────────────────────────────

    def _ensure_compressor(self) -> bool:
        """Lazy-initialise the LLMLingua PromptCompressor.

        Returns True on success, False on failure.  Logs the error so the
        user knows why the engine is unavailable.
        """
        if self._compressor is not None:
            return True
        if not _HAS_LLMLINGUA:
            self._compressor_error = "llmlingua package not installed"
            return False

        try:
            # LLMLingua-2 模型需要启用 use_llmlingua2 模式
            # 注：llmlingua 0.2.2 中 device_map=None 且 use_llmlingua2=True 时会触发
            # "argument of type 'NoneType' is not iterable" bug，所以当 force_cpu=False
            # 时也显式传 'auto' 而非 None
            # 另外在 Apple Silicon (MPS) 上需要显式指定 device_map='mps' 或 'cpu'
            if self._force_cpu:
                device_map = "cpu"
            elif _HAS_TORCH and torch.backends.mps.is_available():
                device_map = "mps"
            else:
                device_map = "cpu"
            self._compressor = PromptCompressor(
                model_name=self._model_name,
                device_map=device_map,
                use_llmlingua2=True,
            )
            if not self.quiet_mode:
                logger.info(
                    "LLMLingua compressor loaded: %s (device=%s)",
                    self._model_name,
                    device_map or "auto",
                )
            return True
        except Exception as exc:
            self._compressor_error = str(exc)
            logger.warning("Failed to load LLMLingua compressor: %s", exc)
            return False

    def _serialize_messages(self, messages: List[Dict[str, Any]]) -> str:
        """Convert a list of messages into a single text string for LLMLingua.

        Format:
            [user] message content
            [assistant] message content
            [tool:<name>] result content
        """
        parts: List[str] = []
        for msg in messages:
            role = msg.get("role", "")
            content = msg.get("content", "")
            if not content:
                continue

            if role == "user":
                parts.append(f"[user] {content}")
            elif role == "assistant":
                parts.append(f"[assistant] {content}")
            elif role == "tool":
                tool_name = msg.get("name", "unknown")
                parts.append(f"[tool:{tool_name}] {content}")
            elif role == "system":
                # Skip system summaries (they're already compressed)
                if not SUMMARY_PREFIX_RE.search(content):
                    parts.append(f"[system] {content}")
            else:
                parts.append(f"[{role}] {content}")

        return "\n\n".join(parts)

    def _compress_with_llmlingua(
        self,
        text: str,
        focus_topic: Optional[str] = None,
    ) -> Optional[str]:
        """Run LLMLingua compression.  Returns None on failure.

        If ``focus_topic`` is provided we pass it as the ``context``
        argument so LLMLingua preserves tokens relevant to the topic.
        """
        if not self._ensure_compressor():
            return None

        try:
            result = self._compressor.compress_prompt(
                text,
                rate=self._rate,
                force_tokens=[focus_topic] if focus_topic else [],
            )
            compressed = result.get("compressed_prompt", "")
            if not compressed:
                return None

            # LLMLingua may return a list of strings — join if needed
            if isinstance(compressed, list):
                compressed = " ".join(compressed)

            return compressed
        except Exception as exc:
            logger.warning("LLMLingua compress_prompt failed: %s", exc)
            return None

    # ── Tool schemas (none for this engine) ────────────────────────────────────

    def get_tool_schemas(self) -> List[Dict[str, Any]]:
        return []

    def handle_tool_call(self, name: str, args: Dict[str, Any], **kwargs) -> str:
        return json.dumps({"error": f"Unknown tool: {name}"})
