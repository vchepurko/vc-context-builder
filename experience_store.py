"""Local Phase 5 experience store.

Experiences are repo-local memories: decisions, mistakes, dead ends, and
patterns that should survive a chat restart without becoming shared repo
state. Storage lives under ``~/.vc-context/<repo-hash>/learned/`` and is
never committed.
"""

from __future__ import annotations

import json
import math
import os
import sqlite3
import time
import uuid
from typing import Any, Dict, List, Optional, Sequence

from paths import ensure_local_state_dir
from semantic_store import LocalHashEmbeddingProvider, _tokens

DB_FILENAME = "experience.sqlite"
SCHEMA_VERSION = 1

_VALID_TYPES = {"decision", "mistake", "dead_end", "pattern"}
_VALID_SOURCES = {"user", "agent", "auto"}


def db_path(project_root: str) -> str:
    return os.path.join(ensure_local_state_dir(project_root, "learned"), DB_FILENAME)


def _connect(project_root: str) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path(project_root))
    conn.row_factory = sqlite3.Row
    return conn


def _init_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS meta (
          key TEXT PRIMARY KEY,
          value TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS experiences (
          id TEXT PRIMARY KEY,
          type TEXT NOT NULL,
          context_text TEXT NOT NULL,
          content TEXT NOT NULL,
          source TEXT NOT NULL,
          source_file TEXT,
          confidence REAL NOT NULL,
          created_at INTEGER NOT NULL,
          updated_at INTEGER NOT NULL,
          last_verified INTEGER,
          vector_json TEXT NOT NULL,
          tokens_json TEXT NOT NULL
        );
        """
    )
    conn.execute(
        "INSERT INTO meta(key, value) VALUES('schema_version', ?) "
        "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
        (str(SCHEMA_VERSION),),
    )


def _embed_text(context_text: str, content: str, source_file: Optional[str]) -> str:
    parts = [context_text, content]
    if source_file:
        parts.append(source_file)
    return "\n".join(p for p in parts if p)


def _pack_vector(vec: Sequence[float]) -> str:
    return json.dumps([round(v, 8) for v in vec], separators=(",", ":"))


def _unpack_vector(raw: str) -> List[float]:
    data = json.loads(raw)
    return [float(v) for v in data]


def _cosine(a: Sequence[float], b: Sequence[float]) -> float:
    if not a or not b or len(a) != len(b):
        return 0.0
    return sum(x * y for x, y in zip(a, b))


def _effective_confidence(confidence: float, last_verified: Optional[int], now: int) -> float:
    if not last_verified:
        return confidence
    age_days = max(0.0, (now - last_verified) / 86400.0)
    decay_steps = math.floor(age_days / 30.0)
    return max(0.0, confidence - (0.05 * decay_steps))


def remember_experience(
    project_root: str,
    *,
    context_text: str,
    content: str,
    type: str = "decision",
    source: str = "user",
    source_file: Optional[str] = None,
    confidence: Optional[float] = None,
) -> Dict[str, Any]:
    """Persist one repo-local experience and return its compact record."""
    context_text = context_text.strip()
    content = content.strip()
    if not context_text or not content:
        raise ValueError("context_text and content are required")
    exp_type = type.strip() if type else "decision"
    if exp_type not in _VALID_TYPES:
        raise ValueError(f"type must be one of {sorted(_VALID_TYPES)}")
    exp_source = source.strip() if source else "user"
    if exp_source not in _VALID_SOURCES:
        raise ValueError(f"source must be one of {sorted(_VALID_SOURCES)}")
    conf = confidence
    if conf is None:
        conf = 0.95 if exp_source == "user" else 0.75 if exp_source == "agent" else 0.55
    conf = max(0.0, min(1.0, float(conf)))
    provider = LocalHashEmbeddingProvider()
    text = _embed_text(context_text, content, source_file)
    tokens = sorted(set(_tokens(text)))
    now = int(time.time())
    exp_id = str(uuid.uuid4())
    with _connect(project_root) as conn:
        _init_schema(conn)
        conn.execute(
            """
            INSERT INTO experiences(
              id, type, context_text, content, source, source_file, confidence,
              created_at, updated_at, last_verified, vector_json, tokens_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                exp_id,
                exp_type,
                context_text,
                content,
                exp_source,
                source_file,
                conf,
                now,
                now,
                now,
                _pack_vector(provider.embed(text)),
                json.dumps(tokens, separators=(",", ":")),
            ),
        )
    return {
        "id": exp_id,
        "type": exp_type,
        "source": exp_source,
        "confidence": round(conf, 3),
        "source_file": source_file,
    }


def recall_experience(
    project_root: str,
    context: str,
    *,
    top_k: int = 3,
    type: Optional[str] = None,
    min_score: float = 0.05,
) -> List[Dict[str, Any]]:
    """Return relevant repo-local experiences for ``context``."""
    context = context.strip()
    if not context:
        return []
    provider = LocalHashEmbeddingProvider()
    qvec = provider.embed(context)
    qtokens = set(_tokens(context))
    exp_type = type.strip() if type else None
    if exp_type and exp_type not in _VALID_TYPES:
        return []
    now = int(time.time())
    try:
        with _connect(project_root) as conn:
            _init_schema(conn)
            rows = conn.execute(
                """
                SELECT id, type, context_text, content, source, source_file,
                       confidence, created_at, updated_at, last_verified,
                       vector_json, tokens_json
                FROM experiences
                """
            ).fetchall()
    except sqlite3.Error:
        return []

    hits: List[Dict[str, Any]] = []
    verify_updates: List[str] = []
    for row in rows:
        if exp_type and row["type"] != exp_type:
            continue
        try:
            vec = _unpack_vector(row["vector_json"])
            tokens = set(json.loads(row["tokens_json"]))
        except (TypeError, ValueError, json.JSONDecodeError):
            continue
        overlap = len(qtokens & tokens) / max(1, len(qtokens))
        score = (0.78 * _cosine(qvec, vec)) + (0.22 * overlap)
        if score < min_score:
            continue
        source_file = row["source_file"]
        stale = False
        if source_file:
            stale = not os.path.exists(os.path.join(project_root, source_file))
            if not stale:
                verify_updates.append(row["id"])
        effective_conf = _effective_confidence(float(row["confidence"]), row["last_verified"], now)
        hit = {
            "id": row["id"],
            "type": row["type"],
            "score": round(score, 4),
            "confidence": round(effective_conf, 3),
            "source": row["source"],
            "context_text": row["context_text"],
            "content": row["content"],
            "stale": stale,
        }
        if source_file:
            hit["source_file"] = source_file
        hits.append(hit)

    if verify_updates:
        try:
            with _connect(project_root) as conn:
                _init_schema(conn)
                conn.executemany(
                    "UPDATE experiences SET last_verified = ? WHERE id = ?",
                    [(now, exp_id) for exp_id in verify_updates],
                )
        except sqlite3.Error:
            pass

    hits.sort(key=lambda h: (-float(h["score"]), -float(h["confidence"]), str(h["id"])))
    return hits[: max(1, min(20, top_k))]
