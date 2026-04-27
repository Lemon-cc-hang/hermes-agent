"""FileMemoryStore — pure-file persistent memory layer.

Stores agent memories as plain Markdown files under ~/.hermes/memory/
with YAML frontmatter, making them human-readable, editable, and grep-able.
"""

import os
import re
import yaml
import logging
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional

logger = logging.getLogger(__name__)

# Supported memory categories.
_MEMORY_CATEGORIES = frozenset({"user", "project", "task", "error", "learning", "context"})

# YAML frontmatter template for each memory file.
_FRONTMATTER_TEMPLATE = """---
id: {id}
category: {category}
created: {created}
updated: {updated}
tags: {tags}
---

{content}
"""


class FileMemoryStore:
    """Persistent memory storage using plain Markdown files.

    Directory layout::

        ~/.hermes/memory/
        ├── MEMORY.md          # auto-generated index
        ├── user/
        ├── project/
        ├── task/
        ├── error/
        ├── learning/
        └── context/
    """

    def __init__(self, memory_dir: Optional[str] = None):
        self.memory_dir = Path(memory_dir or Path.home() / ".hermes" / "memory")
        self.memory_dir.mkdir(parents=True, exist_ok=True)
        for cat in _MEMORY_CATEGORIES:
            (self.memory_dir / cat).mkdir(exist_ok=True)
        self._index_path = self.memory_dir / "MEMORY.md"
        self._rebuild_index()

    # ── CRUD ─────────────────────────────────────────

    def save(
        self,
        entry_id: str,
        content: str,
        category: str = "context",
        tags: Optional[List[str]] = None,
    ) -> Path:
        """Save or overwrite a memory entry."""
        if category not in _MEMORY_CATEGORIES:
            raise ValueError(f"Invalid category {category!r}; must be one of {_MEMORY_CATEGORIES}")

        now = datetime.now().isoformat()
        safe_id = re.sub(r"[^\w\-\.]", "_", entry_id)
        file_path = self.memory_dir / category / f"{safe_id}.md"

        frontmatter = _FRONTMATTER_TEMPLATE.format(
            id=safe_id,
            category=category,
            created=now,
            updated=now,
            tags=yaml.dump(tags or [], default_flow_style=True).strip(),
            content=content.strip(),
        )
        file_path.write_text(frontmatter, encoding="utf-8")
        self._rebuild_index()
        logger.debug("Saved memory %s/%s", category, safe_id)
        return file_path

    def load(self, entry_id: str, category: str = "context") -> str:
        """Load a memory entry’s raw content."""
        safe_id = re.sub(r"[^\w\-\.]", "_", entry_id)
        file_path = self.memory_dir / category / f"{safe_id}.md"
        if not file_path.exists():
            raise FileNotFoundError(f"Memory entry not found: {category}/{safe_id}")
        text = file_path.read_text(encoding="utf-8")
        # Strip YAML frontmatter and return body.
        if text.startswith("---"):
            _, _, body = text.split("---", 2)
            return body.strip()
        return text.strip()

    def delete(self, entry_id: str, category: str = "context") -> bool:
        """Delete a memory entry. Returns True if it existed."""
        safe_id = re.sub(r"[^\w\-\.]", "_", entry_id)
        file_path = self.memory_dir / category / f"{safe_id}.md"
        existed = file_path.exists()
        if existed:
            file_path.unlink()
            self._rebuild_index()
            logger.debug("Deleted memory %s/%s", category, safe_id)
        return existed

    def list_entries(self, category: Optional[str] = None) -> Dict[str, List[str]]:
        """Return a mapping {category: [entry_id, ...]}."""
        result: Dict[str, List[str]] = {}
        cats = [category] if category else list(_MEMORY_CATEGORIES)
        for cat in cats:
            cat_dir = self.memory_dir / cat
            if not cat_dir.exists():
                continue
            result[cat] = [
                p.stem for p in sorted(cat_dir.glob("*.md"))
            ]
        return result

    def search(self, keyword: str, category: Optional[str] = None) -> List[Dict[str, str]]:
        """Full-text search across memory files."""
        matches = []
        cats = [category] if category else list(_MEMORY_CATEGORIES)
        for cat in cats:
            cat_dir = self.memory_dir / cat
            if not cat_dir.exists():
                continue
            for md_file in cat_dir.glob("*.md"):
                text = md_file.read_text(encoding="utf-8")
                if keyword.lower() in text.lower():
                    # Extract body without frontmatter.
                    body = text
                    if body.startswith("---"):
                        _, _, body = body.split("---", 2)
                    matches.append({
                        "id": md_file.stem,
                        "category": cat,
                        "preview": body.strip()[:200],
                    })
        return matches

    def clear_category(self, category: str) -> int:
        """Remove every entry in a category. Returns count deleted."""
        if category not in _MEMORY_CATEGORIES:
            raise ValueError(f"Invalid category {category!r}")
        cat_dir = self.memory_dir / category
        count = 0
        for md_file in list(cat_dir.glob("*.md")):
            md_file.unlink()
            count += 1
        self._rebuild_index()
        return count

    # ── Index ────────────────────────────────────────────────

    def _rebuild_index(self) -> None:
        """Regenerate MEMORY.md from the current on-disk state."""
        lines = [
            "# Hermes Memory Index",
            "",
            f"_Auto-generated at {datetime.now().isoformat()}_",
            "",
        ]
        for cat in sorted(_MEMORY_CATEGORIES):
            cat_dir = self.memory_dir / cat
            entries = sorted(cat_dir.glob("*.md")) if cat_dir.exists() else []
            lines.append(f"## {cat} ({len(entries)} entries)")
            for entry in entries:
                lines.append(f"- `{entry.stem}`")
            lines.append("")
        self._index_path.write_text("\n".join(lines), encoding="utf-8")
