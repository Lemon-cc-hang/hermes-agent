"""
Skill Tier Management — Frequency-Driven Skill Organization

Provides:
  • Tier-based directory layout (pinned / high / low / archived)
  • SQLite-backed daily usage statistics
  • Automatic promotion / demotion / archival
  • Jaccard similarity guard against duplicate skills

All state lives in hermes_state.py (SessionDB).  This module is stateless
and safe to import anywhere.
"""

from __future__ import annotations

import logging
import os
import shutil
import sqlite3
from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict, List, Optional, Set, Tuple

from hermes_constants import get_hermes_home

logger = logging.getLogger(__name__)

# ── Tier layout ────────────────────────────────────────────────────────────

HERMES_HOME = get_hermes_home()
SKILLS_DIR = HERMES_HOME / "skills"

TIER_DIRS = {
    "pinned": SKILLS_DIR / "pinned",
    "high": SKILLS_DIR / "high",
    "low": SKILLS_DIR / "low",
    "archived": SKILLS_DIR / "archived",
}

TIER_ORDER = ["pinned", "high", "low"]  # archived is never auto-injected

# Thresholds (tune via config.yaml skills.tier_threshold if needed)
HIGH_VIEWS_THRESHOLD = 3   # 7-day views to promote low → high
ARCHIVE_VIEWS_THRESHOLD = 0  # 7-day views to demote low → archived
SESSION_EVAL_THRESHOLD = 15  # sessions between auto-evaluations


# ── Lazy DB import (avoid circular deps) ───────────────────────────────────

def _db():
    from hermes_state import SessionDB, DEFAULT_DB_PATH
    return SessionDB(DEFAULT_DB_PATH)


# ── Core helpers ───────────────────────────────────────────────────────────

def _ensure_tier_dirs() -> None:
    for p in TIER_DIRS.values():
        p.mkdir(parents=True, exist_ok=True)


def _jaccard(a: Set[str], b: Set[str]) -> float:
    if not a and not b:
        return 1.0
    union = a | b
    if not union:
        return 0.0
    return len(a & b) / len(union)


def _tokenize(text: str) -> Set[str]:
    return set(text.lower().split())


def _today() -> str:
    return datetime.utcnow().strftime("%Y-%m-%d")


# ── Public API ─────────────────────────────────────────────────────────────

def get_skill_tier(name: str) -> Optional[str]:
    """Return the tier directory name a skill currently lives in, or None."""
    for tier, path in TIER_DIRS.items():
        # Support nested category layout: tier/category/name/SKILL.md
        for skill_md in path.rglob("SKILL.md"):
            if skill_md.parent.name == name:
                return tier
    # Legacy: flat layout under skills/
    if (SKILLS_DIR / name / "SKILL.md").exists():
        # Auto-migrate on discovery
        move_skill_to_tier(name, "low")
        return "low"
    return None


def get_all_skills_by_tier() -> Dict[str, List[str]]:
    """Return {tier: [skill_name, ...]} for all tiers except archived."""
    _ensure_tier_dirs()
    result: Dict[str, List[str]] = {t: [] for t in TIER_ORDER}
    for tier in TIER_ORDER:
        path = TIER_DIRS[tier]
        if not path.exists():
            continue
        # Support nested category layout: scan recursively for SKILL.md
        seen = set()
        for skill_md in sorted(path.rglob("SKILL.md")):
            skill_name = skill_md.parent.name
            if skill_name not in seen:
                seen.add(skill_name)
                result[tier].append(skill_name)
    return result


def record_skill_view(name: str) -> None:
    """Increment today's view count for a skill in SQLite."""
    try:
        db = _db()
        db._execute_write(lambda conn: conn.execute(
            """INSERT INTO skill_daily_stats (name, date, view_count)
               VALUES (?, ?, 1)
               ON CONFLICT(name, date) DO UPDATE SET
               view_count = view_count + 1""",
            (name, _today()),
        ))
    except Exception as e:
        logger.debug("record_skill_view failed for %s: %s", name, e)


def get_7day_views(name: str) -> int:
    """Total views for a skill in the last 7 days."""
    try:
        db = _db()
        cur = db._conn.execute(
            """SELECT COALESCE(SUM(view_count), 0)
               FROM skill_daily_stats
               WHERE name = ? AND date >= date('now', '-7 days')""",
            (name,),
        )
        row = cur.fetchone()
        return int(row[0]) if row else 0
    except Exception as e:
        logger.debug("get_7day_views failed for %s: %s", name, e)
        return 0


def get_session_eval_state() -> Tuple[int, Optional[str]]:
    """Return (session_count, last_evaluated)."""
    try:
        db = _db()
        cur = db._conn.execute(
            "SELECT session_count, last_evaluated FROM skill_evaluation_log WHERE id = 1"
        )
        row = cur.fetchone()
        if row:
            return int(row[0]), row[1]
    except Exception as e:
        logger.debug("get_session_eval_state failed: %s", e)
    return 0, None


def increment_session_count() -> bool:
    """Bump session counter.  Returns True if evaluation threshold reached."""
    try:
        db = _db()
        db._execute_write(lambda conn: conn.execute(
            """INSERT INTO skill_evaluation_log (id, session_count, last_evaluated)
               VALUES (1, 1, datetime('now'))
               ON CONFLICT(id) DO UPDATE SET
               session_count = session_count + 1,
               last_evaluated = COALESCE(last_evaluated, datetime('now'))""",
        ))
        count, _ = get_session_eval_state()
        if count >= SESSION_EVAL_THRESHOLD:
            # Reset and trigger
            db._execute_write(lambda conn: conn.execute(
                "UPDATE skill_evaluation_log SET session_count = 0 WHERE id = 1"
            ))
            return True
    except Exception as e:
        logger.debug("increment_session_count failed: %s", e)
    return False


def _cleanup_old_stats(days: int = 10) -> None:
    """Delete stats older than *days* days."""
    try:
        db = _db()
        db._execute_write(lambda conn: conn.execute(
            "DELETE FROM skill_daily_stats WHERE date < date('now', ?)",
            (f"-{days} days",),
        ))
    except Exception as e:
        logger.debug("_cleanup_old_stats failed: %s", e)


def evaluate_and_migrate(threshold: int = HIGH_VIEWS_THRESHOLD) -> List[Dict]:
    """
    Evaluate all skills and migrate between tiers based on 7-day usage.

    Rules:
      low  → high    : 7-day views > threshold
      high → low     : 7-day views <= threshold (and > 0)
      low  → archived: 7-day views == 0  (and skill exists > 30 days)

    Returns list of migration records for logging.
    """
    _cleanup_old_stats()
    _ensure_tier_dirs()
    migrated: List[Dict] = []

    # low → high
    for name in get_all_skills_by_tier().get("low", []):
        views = get_7day_views(name)
        if views > threshold:
            move_skill_to_tier(name, "high")
            migrated.append({"skill": name, "from": "low", "to": "high", "views": views})

    # high → low
    for name in get_all_skills_by_tier().get("high", []):
        views = get_7day_views(name)
        if 0 < views <= threshold:
            move_skill_to_tier(name, "low")
            migrated.append({"skill": name, "from": "high", "to": "low", "views": views})

    # low → archived (only if zero views AND older than 30 days)
    cutoff = datetime.utcnow() - timedelta(days=30)
    for name in get_all_skills_by_tier().get("low", []):
        views = get_7day_views(name)
        if views == 0:
            found = _find_skill_path(name)
            if found:
                skill_path, _ = found
                try:
                    mtime = datetime.utcfromtimestamp(skill_path.stat().st_mtime)
                    if mtime < cutoff:
                        move_skill_to_tier(name, "archived")
                        migrated.append({"skill": name, "from": "low", "to": "archived", "views": 0})
                except Exception:
                    pass

    if migrated:
        logger.info("Skill tier migrations: %s", migrated)
    return migrated


def _find_skill_path(name: str) -> Optional[Tuple[Path, str]]:
    """Find a skill directory and its current tier. Returns (path, tier) or None."""
    for tier, path in TIER_DIRS.items():
        for skill_md in path.rglob("SKILL.md"):
            if skill_md.parent.name == name:
                return skill_md.parent, tier
    # Legacy flat layout
    flat = SKILLS_DIR / name
    if flat.exists() and (flat / "SKILL.md").exists():
        return flat, "legacy"
    return None


def move_skill_to_tier(name: str, target_tier: str) -> None:
    """Physically move a skill directory to another tier, preserving category sub-path."""
    if target_tier not in TIER_DIRS:
        raise ValueError(f"Unknown tier '{target_tier}'. Use: {list(TIER_DIRS)}")

    found = _find_skill_path(name)
    if found is None:
        raise FileNotFoundError(f"Skill '{name}' not found in any tier.")

    src, _ = found
    # Preserve relative category path under the tier
    # src structure: tier_dir/category_dir/skill_dir
    # We want to move to: target_tier_dir/category_dir/skill_dir
    try:
        # src.parent = tier/category/skill → tier/category
        # src.parent.parent = tier/category → tier
        rel = src.relative_to(src.parent.parent)  # category/skill
        if len(rel.parts) >= 2:
            # Has category sub-path: keep category, move skill dir
            dst = TIER_DIRS[target_tier] / rel
        else:
            dst = TIER_DIRS[target_tier] / name
    except ValueError:
        dst = TIER_DIRS[target_tier] / name

    dst.parent.mkdir(parents=True, exist_ok=True)

    if dst.exists():
        shutil.rmtree(dst, ignore_errors=True)

    shutil.move(str(src), str(dst))
    logger.info("Moved skill '%s' → %s/", name, target_tier)


def check_similar_skill(name: str, description: str, threshold: float = 0.5) -> Optional[str]:
    """
    Compare a proposed skill against all existing skills using Jaccard similarity
    on {name words} ∪ {description words}.

    Returns the name of the most similar existing skill if similarity >= threshold,
    else None.
    """
    _ensure_tier_dirs()
    proposed_tokens = _tokenize(name) | _tokenize(description)
    if not proposed_tokens:
        return None

    best_match: Optional[str] = None
    best_score = 0.0

    for tier in TIER_ORDER + ["archived"]:
        path = TIER_DIRS[tier]
        if not path.exists():
            continue
        for skill_md in path.rglob("SKILL.md"):
            try:
                from agent.skill_utils import parse_frontmatter
                raw = skill_md.read_text(encoding="utf-8")
                fm, _ = parse_frontmatter(raw)
                existing_tokens = _tokenize(skill_md.parent.name) | _tokenize(str(fm.get("description", "")))
                score = _jaccard(proposed_tokens, existing_tokens)
                if score > best_score:
                    best_score = score
                    best_match = skill_md.parent.name
            except Exception:
                continue

    # Also scan legacy flat layout
    if SKILLS_DIR.exists():
        for subdir in SKILLS_DIR.iterdir():
            if not subdir.is_dir() or subdir.name in TIER_DIRS:
                continue
            skill_md = subdir / "SKILL.md"
            if not skill_md.exists():
                continue
            try:
                from agent.skill_utils import parse_frontmatter
                raw = skill_md.read_text(encoding="utf-8")
                fm, _ = parse_frontmatter(raw)
                existing_tokens = _tokenize(subdir.name) | _tokenize(str(fm.get("description", "")))
                score = _jaccard(proposed_tokens, existing_tokens)
                if score > best_score:
                    best_score = score
                    best_match = subdir.name
            except Exception:
                continue

    if best_score >= threshold:
        logger.info("Similar skill detected: %s (score %.2f)", best_match, best_score)
        return best_match
    return None


# ── Tier-aware skill scanning (used by prompt_builder) ─────────────────────

def iter_tier_skills(tier: str):
    """Yield (skill_name, skill_md_path) for every skill in *tier*."""
    path = TIER_DIRS.get(tier)
    if not path or not path.exists():
        return
    seen = set()
    for skill_md in sorted(path.rglob("SKILL.md")):
        name = skill_md.parent.name
        if name not in seen:
            seen.add(name)
            yield name, skill_md
