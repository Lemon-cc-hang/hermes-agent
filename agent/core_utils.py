"""Core utilities extracted from run_agent.py for modularization.

This module contains small, self-contained helpers that don't depend on
AIAgent state but are used throughout the conversation pipeline.
"""

import os
import sys
import threading
import logging
from typing import Optional

logger = logging.getLogger(__name__)


class IterationBudget:
    """Thread-safe iteration counter for an agent.

    Each agent (parent or subagent) gets its own ``IterationBudget``.
    The parent's budget is capped at ``max_iterations`` (default 90).
    Each subagent gets an independent budget capped at
    ``delegation.max_iterations`` (default 50) — this means total
    iterations across parent + subagents can exceed the parent's cap.
    Users control the per-subagent limit via ``delegation.max_iterations``
    in config.yaml.

    ``execute_code`` (programmatic tool calling) iterations are refunded via
    :meth:`refund` so they don't eat into the budget.
    """

    def __init__(self, max_total: int):
        self.max_total = max_total
        self._used = 0
        self._lock = threading.Lock()

    def consume(self) -> bool:
        """Try to consume one iteration.  Returns True if allowed."""
        with self._lock:
            if self._used >= self.max_total:
                return False
            self._used += 1
            return True

    def refund(self) -> None:
        """Give back one iteration (e.g. for execute_code turns)."""
        with self._lock:
            if self._used > 0:
                self._used -= 1

    @property
    def used(self) -> int:
        return self._used

    @property
    def remaining(self) -> int:
        with self._lock:
            return max(0, self.max_total - self._used)


# ── Safe stdio wrapper (prevents OSError on broken pipes) ─────────────

class _SafeWriter:
    """Wrap a stream so that write()/flush() never raise.

    Broken-pipe errors are common in gateway mode (e.g. systemd's
    ``systemctl status`` piping the journal) or when stdout is redirected
    to a closed consumer.  Swallowing them keeps the agent from crashing
    mid-turn.
    """

    def __init__(self, stream) -> None:
        self._stream = stream

    def write(self, data: str | bytes) -> int:
        try:
            return self._stream.write(data)
        except (OSError, ValueError):
            return len(data) if isinstance(data, str) else len(data)

    def flush(self) -> None:
        try:
            self._stream.flush()
        except (OSError, ValueError):
            pass

    def isatty(self) -> bool:
        try:
            return self._stream.isatty()
        except (OSError, ValueError):
            return False

    def __getattr__(self, name: str):
        return getattr(self._stream, name)


def install_safe_stdio() -> None:
    """Wrap ``sys.stdout`` and ``sys.stderr`` with :class:`_SafeWriter`."""
    for stream_name in ("stdout", "stderr"):
        stream = getattr(sys, stream_name)
        if not isinstance(stream, _SafeWriter):
            setattr(sys, stream_name, _SafeWriter(stream))
