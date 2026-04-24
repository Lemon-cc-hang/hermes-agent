"""Project Knowledge Base — ChromaDB-backed RAG for code/projects.

NOT memory. Does NOT inject into system prompt. Retrieved on-demand only.
Stored in ~/.hermes/project_kb/ (SQLite + HNSW vector index).
"""

import json
import hashlib
from pathlib import Path
from typing import Optional

from hermes_constants import get_hermes_home
from tools.registry import registry

logger = __import__("logging").getLogger(__name__)

try:
    import chromadb
    from chromadb.utils.embedding_functions import SentenceTransformerEmbeddingFunction
    _AVAILABLE = True
except ImportError:
    _AVAILABLE = False

# ── Paths ──
_KB_DIR = get_hermes_home() / "project_kb"
_COLLECTION_NAME = "projects"


def _get_client():
    if not _AVAILABLE:
        raise RuntimeError(
            "ChromaDB not installed. Run: source ~/.hermes/hermes-agent/venv/bin/activate && pip install chromadb sentence-transformers"
        )
    _KB_DIR.mkdir(parents=True, exist_ok=True)
    return chromadb.PersistentClient(path=str(_KB_DIR))


def _get_collection():
    client = _get_client()
    ef = SentenceTransformerEmbeddingFunction(model_name="all-MiniLM-L6-v2")
    return client.get_or_create_collection(
        name=_COLLECTION_NAME,
        embedding_function=ef,
        metadata={"hnsw:space": "cosine"}
    )


# ── Tool: Add ──
def project_kb_add(project: str, content: str, source: str = "") -> str:
    """Add parsed project content to the knowledge base."""
    if not content or len(content.strip()) < 10:
        return json.dumps({"success": False, "error": "Content too short (min 10 chars)"})

    col = _get_collection()
    doc_id = hashlib.sha256(f"{project}:{source}:{content[:100]}".encode()).hexdigest()[:16]

    col.upsert(
        documents=[content],
        metadatas=[{"project": project, "source": source or "unknown"}],
        ids=[doc_id]
    )
    return json.dumps({
        "success": True,
        "id": doc_id,
        "project": project,
        "chars": len(content),
        "note": "Stored in vector DB. NOT in memory/system prompt."
    }, ensure_ascii=False)


# ── Tool: Search ──
def project_kb_search(query: str, project: str = "", top_k: int = 5) -> str:
    """Semantic search across projects. Returns relevant snippets."""
    if not query:
        return json.dumps({"error": "Query required"}, ensure_ascii=False)

    col = _get_collection()
    where_filter = {"project": project} if project else None

    results = col.query(
        query_texts=[query],
        n_results=min(top_k, 20),
        where=where_filter,
        include=["documents", "metadatas", "distances"]
    )

    items = []
    docs = results["documents"][0] if results["documents"] else []
    metas = results["metadatas"][0] if results["metadatas"] else []
    dists = results["distances"][0] if results["distances"] else []

    for i, (doc, meta, dist) in enumerate(zip(docs, metas, dists)):
        items.append({
            "rank": i + 1,
            "project": meta.get("project", "unknown"),
            "source": meta.get("source", ""),
            "distance": round(float(dist), 4),
            "snippet": doc[:1200] + ("..." if len(doc) > 1200 else "")
        })

    return json.dumps({
        "query": query,
        "results": items,
        "count": len(items),
        "note": "Injected into THIS TURN ONLY. Not persisted in system prompt."
    }, ensure_ascii=False)


# ── Tool: List Projects ──
def project_kb_list() -> str:
    """List all projects in the knowledge base."""
    try:
        col = _get_collection()
        data = col.get(include=["metadatas"])
        projects = sorted(set(m["project"] for m in data["metadatas"] if "project" in m))
        return json.dumps({"projects": projects, "total_chunks": len(data["ids"])}, ensure_ascii=False)
    except Exception:
        return json.dumps({"projects": [], "total_chunks": 0}, ensure_ascii=False)


# ── Tool: Delete Project ──
def project_kb_delete(project: str) -> str:
    """Delete all chunks for a project from the knowledge base."""
    if not project:
        return json.dumps({"success": False, "error": "project name required"}, ensure_ascii=False)

    col = _get_collection()
    try:
        col.delete(where={"project": project})
        return json.dumps({"success": True, "deleted_project": project}, ensure_ascii=False)
    except Exception as e:
        return json.dumps({"success": False, "error": str(e)}, ensure_ascii=False)


# ── Tool: Count Project ──
def project_kb_count(project: str = "") -> str:
    """Count chunks for a project or total."""
    col = _get_collection()
    if project:
        data = col.get(where={"project": project}, include=[])
        return json.dumps({"project": project, "chunks": len(data["ids"])}, ensure_ascii=False)
    data = col.get(include=[])
    return json.dumps({"total_chunks": len(data["ids"])}, ensure_ascii=False)


# ── Registry helpers ──
def check_kb_requirements() -> bool:
    return _AVAILABLE


# ── Registry ──
registry.register(
    name="project_kb_add",
    toolset="project_kb",
    schema={
        "name": "project_kb_add",
        "description": (
            "Add parsed project code/docs to the vector knowledge base. "
            "Use AFTER analyzing a project. NEVER use for user preferences or temporary facts."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "project": {
                    "type": "string",
                    "description": "Project name (e.g. 'order-service'). Use consistent names."
                },
                "content": {
                    "type": "string",
                    "description": "Parsed content: module structure, key classes, APIs, dependencies, design decisions"
                },
                "source": {
                    "type": "string",
                    "description": "Source file path or module name"
                }
            },
            "required": ["project", "content"]
        }
    },
    handler=lambda args, **kw: project_kb_add(
        project=args.get("project", ""),
        content=args.get("content", ""),
        source=args.get("source", "")
    ),
    check_fn=check_kb_requirements,
)

registry.register(
    name="project_kb_search",
    toolset="project_kb",
    schema={
        "name": "project_kb_search",
        "description": (
            "Search project knowledge base by semantic meaning. "
            "Use when user asks about code, architecture, dependencies, or specific projects. "
            "Returns snippets injected into THIS TURN ONLY."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "What to find (natural language or keywords)"
                },
                "project": {
                    "type": "string",
                    "description": "Filter to specific project (optional). Leave blank to search all."
                },
                "top_k": {
                    "type": "integer",
                    "description": "Max results (default: 5, max: 20)"
                }
            },
            "required": ["query"]
        }
    },
    handler=lambda args, **kw: project_kb_search(
        query=args.get("query", ""),
        project=args.get("project", ""),
        top_k=args.get("top_k", 5)
    ),
    check_fn=check_kb_requirements,
)

registry.register(
    name="project_kb_list",
    toolset="project_kb",
    schema={
        "name": "project_kb_list",
        "description": "List all projects stored in the knowledge base.",
        "parameters": {"type": "object", "properties": {}, "required": []}
    },
    handler=lambda args, **kw: project_kb_list(),
    check_fn=check_kb_requirements,
)

registry.register(
    name="project_kb_delete",
    toolset="project_kb",
    schema={
        "name": "project_kb_delete",
        "description": "Delete ALL data for a project from the knowledge base. Irreversible.",
        "parameters": {
            "type": "object",
            "properties": {
                "project": {
                    "type": "string",
                    "description": "Project name to delete completely"
                }
            },
            "required": ["project"]
        }
    },
    handler=lambda args, **kw: project_kb_delete(project=args.get("project", "")),
    check_fn=check_kb_requirements,
)

registry.register(
    name="project_kb_count",
    toolset="project_kb",
    schema={
        "name": "project_kb_count",
        "description": "Count chunks for a project or total KB size.",
        "parameters": {
            "type": "object",
            "properties": {
                "project": {
                    "type": "string",
                    "description": "Project name (blank for total)"
                }
            },
            "required": []
        }
    },
    handler=lambda args, **kw: project_kb_count(project=args.get("project", "")),
    check_fn=check_kb_requirements,
)
