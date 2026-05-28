"""Local semantic symbol index for Phase 5.

The storage contract is deliberately production-friendly:

* per-repo state lives under ``~/.vc-context/<repo-hash>/embeddings/``;
* SQLite is stdlib and durable;
* the provider interface is explicit, so a real embedding model or
  sqlite-vec backend can replace the default without changing MCP shape.

The default provider is a deterministic hashed vectorizer. It is not as
smart as a neural model, but it is offline, fast, and good enough to make
the semantic-search path useful immediately while preserving the final
architecture.
"""

from __future__ import annotations

import hashlib
import json
import math
import os
import re
import sqlite3
import time
from dataclasses import dataclass
from typing import Any, ClassVar, Dict, List, Optional, Sequence

from _test_filter import is_test_path
from paths import ensure_local_state_dir

SCHEMA_VERSION = 1
DB_FILENAME = "symbols.sqlite"
DEFAULT_DIM = 256


_TOKEN_RE = re.compile(r"[A-Za-z0-9]+")


def _split_camel(text: str) -> str:
    text = re.sub(r"([a-z0-9])([A-Z])", r"\1 \2", text)
    text = re.sub(r"([A-Z]+)([A-Z][a-z])", r"\1 \2", text)
    return text


def _tokens(text: str) -> List[str]:
    text = _split_camel(text.replace("_", " ").replace("-", " ").replace("/", " "))
    return [t.lower() for t in _TOKEN_RE.findall(text) if len(t) > 1]


def _symbol_text(name: str, rec: Dict[str, Any]) -> str:
    parts = [
        name,
        str(rec.get("kind") or ""),
        str(rec.get("role") or ""),
        str(rec.get("params") or ""),
        str(rec.get("file") or ""),
        str(rec.get("doc") or ""),
    ]
    for key in ("decorators", "callees", "raises"):
        value = rec.get(key)
        if isinstance(value, list):
            parts.extend(str(v) for v in value)
    return "\n".join(p for p in parts if p)


def _symbols_hash(symbols: Dict[str, Dict[str, Any]]) -> str:
    payload = json.dumps(symbols, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _pack_vector(vec: Sequence[float]) -> str:
    return json.dumps([round(v, 8) for v in vec], separators=(",", ":"))


def _unpack_vector(raw: str) -> List[float]:
    data = json.loads(raw)
    return [float(v) for v in data]


def _cosine(a: Sequence[float], b: Sequence[float]) -> float:
    if not a or not b or len(a) != len(b):
        return 0.0
    return sum(x * y for x, y in zip(a, b))


class EmbeddingProvider:
    """Provider interface for semantic vectors."""

    name = "base"
    dim = DEFAULT_DIM

    def embed(self, text: str) -> List[float]:
        raise NotImplementedError


class LocalHashEmbeddingProvider(EmbeddingProvider):
    """Deterministic bag-of-token hashing provider.

    Uses signed feature hashing plus L2 normalisation. This keeps runtime
    stdlib-only while giving us the same vector-search control flow as a
    future sqlite-vec / neural embedding provider.
    """

    name = "local_hash"

    def __init__(self, dim: int = DEFAULT_DIM) -> None:
        self.dim = dim

    def embed(self, text: str) -> List[float]:
        vec = [0.0] * self.dim
        for tok in _tokens(text):
            digest = hashlib.blake2b(tok.encode("utf-8"), digest_size=8).digest()
            n = int.from_bytes(digest, "big")
            idx = n % self.dim
            sign = 1.0 if (n >> 8) & 1 else -1.0
            # Mild length dampening keeps long file paths/docstrings from
            # swamping concise symbol names.
            vec[idx] += sign / math.sqrt(max(1, len(tok)))
        norm = math.sqrt(sum(v * v for v in vec))
        if norm == 0:
            return vec
        return [v / norm for v in vec]


def _hf_model_is_cached(model_name: str) -> bool:
    """Return True when the HuggingFace model snapshot already exists locally.

    HF Hub stores models under ~/.cache/huggingface/hub/models--<org>--<name>/snapshots/.
    When at least one snapshot directory is present, we can load offline.
    """
    cache_root = os.environ.get(
        "HF_HUB_CACHE",
        os.path.join(os.path.expanduser("~"), ".cache", "huggingface", "hub"),
    )
    folder = "models--" + model_name.replace("/", "--")
    snapshots_dir = os.path.join(cache_root, folder, "snapshots")
    if not os.path.isdir(snapshots_dir):
        return False
    try:
        return any(True for _ in os.scandir(snapshots_dir))
    except OSError:
        return False


class SentenceTransformersEmbeddingProvider(EmbeddingProvider):
    """Local neural embeddings via sentence-transformers.

    Downloads the model to ~/.cache/huggingface/ on first use (~25 MB for
    all-MiniLM-L6-v2). Subsequent runs use the cached model — no network.
    Requires: ``pip install sentence-transformers`` (dev/install-time only).
    """

    name = "sentence_transformers"
    dim = 384  # all-MiniLM-L6-v2 output dimension

    DEFAULT_MODEL = "sentence-transformers/all-MiniLM-L6-v2"

    def __init__(self, model_name: str = DEFAULT_MODEL) -> None:
        self.model_name = model_name
        self._model = None  # lazy-load on first embed()

    def _load(self) -> None:
        if self._model is not None:
            return
        try:
            from sentence_transformers import SentenceTransformer  # type: ignore
        except ImportError as exc:
            raise RuntimeError(
                "sentence-transformers is not installed. "
                "Run: pip install sentence-transformers"
            ) from exc
        # Skip HuggingFace Hub version-check HEAD request when model is cached.
        # local_files_only=True tells the Hub library not to reach out to the
        # network at all, which eliminates the ~200 ms stall on every rebuild.
        local_only = _hf_model_is_cached(self.model_name)
        self._model = SentenceTransformer(self.model_name, local_files_only=local_only)

    def embed(self, text: str) -> List[float]:
        self._load()
        vec = self._model.encode(text, normalize_embeddings=True)  # type: ignore[union-attr,attr-defined]
        return [float(v) for v in vec]


class OpenAIEmbeddingProvider(EmbeddingProvider):
    """OpenAI text-embedding-3-small via the openai SDK.

    Cost: ~$0.002 per full project rebuild (2000 symbols × 50 tokens).
    Requires: ``pip install openai`` and OPENAI_API_KEY env var.
    """

    name = "openai"
    dim = 1536

    DEFAULT_MODEL = "text-embedding-3-small"

    def __init__(self, model: str = DEFAULT_MODEL, api_key: Optional[str] = None) -> None:
        self.model = model
        self._api_key = api_key or os.environ.get("OPENAI_API_KEY")

    def embed(self, text: str) -> List[float]:
        try:
            from openai import OpenAI  # type: ignore
        except ImportError as exc:
            raise RuntimeError(
                "openai package is not installed. Run: pip install openai"
            ) from exc
        if not self._api_key:
            raise RuntimeError(
                "OPENAI_API_KEY environment variable is not set."
            )
        client = OpenAI(api_key=self._api_key)
        response = client.embeddings.create(input=text, model=self.model)
        return [float(v) for v in response.data[0].embedding]


class OllamaEmbeddingProvider(EmbeddingProvider):
    """Local embeddings via Ollama REST API (stdlib only — no extra deps).

    Requires: ``ollama serve`` running locally.
    Pull the model once: ``ollama pull nomic-embed-text``

    Default model ``nomic-embed-text`` (768 dim) is trained on code + docs
    and significantly outperforms ``all-MiniLM-L6-v2`` for symbol search.
    ``mxbai-embed-large`` (1024 dim) gives the best local quality.

    The dim is inferred from the first embed() call if unknown, then cached
    on the instance so subsequent calls skip the inference.
    """

    name = "ollama"
    dim = 768  # nomic-embed-text default; updated after first embed if needed

    DEFAULT_MODEL = "nomic-embed-text"
    DEFAULT_HOST = "http://localhost:11434"

    # Known output dimensions for common Ollama embedding models.
    _KNOWN_DIMS: ClassVar[Dict[str, int]] = {
        "nomic-embed-text": 768,
        "mxbai-embed-large": 1024,
        "all-minilm": 384,
        "bge-m3": 1024,
        "snowflake-arctic-embed": 1024,
    }

    def __init__(self, model: str = DEFAULT_MODEL, host: str = DEFAULT_HOST) -> None:
        self.model = model
        self.host = host.rstrip("/")
        # Pre-set dim from known-dims table; updated dynamically on first call.
        for key, d in self._KNOWN_DIMS.items():
            if key in model.lower():
                self.dim = d
                break

    def embed(self, text: str) -> List[float]:
        import urllib.request as _req

        payload = json.dumps({"model": self.model, "prompt": text}).encode("utf-8")
        request = _req.Request(
            f"{self.host}/api/embeddings",
            data=payload,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with _req.urlopen(request, timeout=30) as resp:
                data = json.loads(resp.read())
        except Exception as exc:
            raise RuntimeError(
                f"Ollama embedding failed — is `ollama serve` running "
                f"and model '{self.model}' pulled? ({exc})"
            ) from exc
        vec = [float(v) for v in data["embedding"]]
        # Sync dim to actual output on first call (handles unlisted models).
        if len(vec) != self.dim:
            self.dim = len(vec)
        return vec


def provider_from_conventions(project_root: str) -> EmbeddingProvider:
    """Read .vc-context/conventions.json and return the configured provider.

    Supported values for ``embedding_provider``:
      - ``"local_hash"`` (default, no deps)
      - ``"sentence_transformers"`` (local neural, needs sentence-transformers)
      - ``"openai"`` (API, needs openai + OPENAI_API_KEY)
      - ``"ollama"`` (local REST API, needs ``ollama serve``)

    Dict form allows model/host overrides:
      ``{"name": "ollama", "model": "mxbai-embed-large", "host": "http://localhost:11434"}``

    Falls back to LocalHashEmbeddingProvider when the key is absent or unknown.
    """
    conv_path = os.path.join(project_root, ".vc-context", "conventions.json")
    provider_name = "local_hash"
    model_override: Optional[str] = None
    host_override: Optional[str] = None

    if os.path.exists(conv_path):
        try:
            with open(conv_path, encoding="utf-8") as fh:
                conv = json.load(fh)
            embedding_cfg = conv.get("embedding_provider")
            if isinstance(embedding_cfg, str):
                provider_name = embedding_cfg
            elif isinstance(embedding_cfg, dict):
                provider_name = str(embedding_cfg.get("name", "local_hash"))
                model_override = embedding_cfg.get("model")
                host_override = embedding_cfg.get("host")
        except (OSError, json.JSONDecodeError):
            pass

    if provider_name == "sentence_transformers":
        kwargs: Dict[str, Any] = {}
        if model_override:
            kwargs["model_name"] = model_override
        return SentenceTransformersEmbeddingProvider(**kwargs)
    if provider_name == "openai":
        kwargs = {}
        if model_override:
            kwargs["model"] = model_override
        return OpenAIEmbeddingProvider(**kwargs)
    if provider_name == "ollama":
        kwargs = {}
        if model_override:
            kwargs["model"] = model_override
        if host_override:
            kwargs["host"] = host_override
        return OllamaEmbeddingProvider(**kwargs)
    return LocalHashEmbeddingProvider()


@dataclass(frozen=True)
class SearchHit:
    name: str
    score: float
    file: str
    line: Optional[int]
    kind: Optional[str]
    role: Optional[str]
    doc: Optional[str]
    provider: str
    why: List[str]

    def as_dict(self) -> Dict[str, Any]:
        out: Dict[str, Any] = {
            "name": self.name,
            "score": round(self.score, 4),
            "file": self.file,
            "provider": self.provider,
            "why": self.why,
        }
        for key in ("line", "kind", "role", "doc"):
            value = getattr(self, key)
            if value is not None:
                out[key] = value
        return out


def db_path(project_root: str) -> str:
    return os.path.join(ensure_local_state_dir(project_root, "embeddings"), DB_FILENAME)


def _connect(project_root: str) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path(project_root), timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    return conn


def _init_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS meta (
          key TEXT PRIMARY KEY,
          value TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS symbols (
          name TEXT PRIMARY KEY,
          file TEXT NOT NULL,
          line INTEGER,
          kind TEXT,
          role TEXT,
          doc TEXT,
          search_text TEXT NOT NULL,
          tokens_json TEXT NOT NULL,
          vector_json TEXT NOT NULL
        );
        """
    )


def _meta(conn: sqlite3.Connection, key: str) -> Optional[str]:
    row = conn.execute("SELECT value FROM meta WHERE key = ?", (key,)).fetchone()
    return str(row["value"]) if row else None


def _set_meta(conn: sqlite3.Connection, key: str, value: str) -> None:
    conn.execute(
        "INSERT INTO meta(key, value) VALUES(?, ?) "
        "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
        (key, value),
    )


def build_symbol_store(
    project_root: str,
    symbols: Dict[str, Dict[str, Any]],
    *,
    provider: Optional[EmbeddingProvider] = None,
) -> Dict[str, Any]:
    """Rebuild the local semantic symbol store from ``agent_symbols``."""
    provider = provider or LocalHashEmbeddingProvider()
    source_hash = _symbols_hash(symbols)
    path = db_path(project_root)
    started = time.monotonic()
    with _connect(project_root) as conn:
        _init_schema(conn)
        conn.execute("DELETE FROM symbols")
        rows = []
        for name, rec in sorted(symbols.items()):
            if not isinstance(rec, dict):
                continue
            text = _symbol_text(name, rec)
            toks = sorted(set(_tokens(text)))
            doc = rec.get("doc")
            doc_first = str(doc).splitlines()[0] if doc else None
            line = rec.get("line")
            rows.append(
                (
                    name,
                    str(rec.get("file") or ""),
                    int(line) if isinstance(line, int) else None,
                    str(rec.get("kind")) if rec.get("kind") else None,
                    str(rec.get("role")) if rec.get("role") else None,
                    doc_first,
                    text,
                    json.dumps(toks, separators=(",", ":")),
                    _pack_vector(provider.embed(text)),
                )
            )
        conn.executemany(
            """
            INSERT INTO symbols(
              name, file, line, kind, role, doc, search_text, tokens_json, vector_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            rows,
        )
        _set_meta(conn, "schema_version", str(SCHEMA_VERSION))
        _set_meta(conn, "provider", provider.name)
        _set_meta(conn, "dim", str(provider.dim))
        _set_meta(conn, "source_hash", source_hash)
        _set_meta(conn, "symbol_count", str(len(rows)))
        _set_meta(conn, "built_at", str(int(time.time())))
    return {
        "path": path,
        "symbols": len(rows),
        "provider": provider.name,
        "dim": provider.dim,
        "duration_ms": int((time.monotonic() - started) * 1000),
    }


def ensure_symbol_store(
    project_root: str,
    symbols: Dict[str, Dict[str, Any]],
    *,
    provider: Optional[EmbeddingProvider] = None,
) -> Dict[str, Any]:
    """Build the store only when missing, stale, or provider-incompatible."""
    provider = provider or LocalHashEmbeddingProvider()
    source_hash = _symbols_hash(symbols)
    try:
        with _connect(project_root) as conn:
            _init_schema(conn)
            if (
                _meta(conn, "schema_version") == str(SCHEMA_VERSION)
                and _meta(conn, "provider") == provider.name
                and _meta(conn, "dim") == str(provider.dim)
                and _meta(conn, "source_hash") == source_hash
            ):
                return {
                    "path": db_path(project_root),
                    "symbols": int(_meta(conn, "symbol_count") or 0),
                    "provider": provider.name,
                    "dim": provider.dim,
                    "rebuilt": False,
                }
    except sqlite3.Error:
        pass
    result = build_symbol_store(project_root, symbols, provider=provider)
    result["rebuilt"] = True
    return result


def _why(query_tokens: set[str], row_tokens: set[str], name: str, file: str, doc: str) -> List[str]:
    why: List[str] = []
    name_tokens = set(_tokens(name))
    file_tokens = set(_tokens(file))
    doc_tokens = set(_tokens(doc))
    if query_tokens & name_tokens:
        why.append("name")
    if query_tokens & file_tokens:
        why.append("file")
    if query_tokens & doc_tokens:
        why.append("doc")
    if query_tokens & row_tokens and not why:
        why.append("metadata")
    return why


def semantic_search(
    project_root: str,
    symbols: Dict[str, Dict[str, Any]],
    query: str,
    *,
    top_k: int = 5,
    kind: Optional[str] = None,
    role: Optional[str] = None,
    include_tests: bool = False,
    provider: Optional[EmbeddingProvider] = None,
) -> List[Dict[str, Any]]:
    """Search symbols by meaning-ish text, not exact symbol name."""
    query = query.strip()
    if not query:
        return []
    provider = provider or LocalHashEmbeddingProvider()
    ensure_symbol_store(project_root, symbols, provider=provider)
    qvec = provider.embed(query)
    qtokens = set(_tokens(query))
    hits: List[SearchHit] = []
    with _connect(project_root) as conn:
        _init_schema(conn)
        rows = conn.execute(
            "SELECT name, file, line, kind, role, doc, tokens_json, vector_json FROM symbols"
        ).fetchall()
    kind_l = kind.lower() if kind else None
    role_l = role.lower() if role else None
    for row in rows:
        file = str(row["file"] or "")
        if not include_tests and is_test_path(file):
            continue
        row_kind = str(row["kind"] or "")
        row_role = str(row["role"] or "")
        if kind_l and row_kind.lower() != kind_l:
            continue
        if role_l and row_role.lower() != role_l:
            continue
        try:
            row_tokens = set(json.loads(row["tokens_json"]))
            vec = _unpack_vector(row["vector_json"])
        except (TypeError, ValueError, json.JSONDecodeError):
            continue
        cosine = _cosine(qvec, vec)
        overlap = len(qtokens & row_tokens) / max(1, len(qtokens))
        name_overlap = len(qtokens & set(_tokens(str(row["name"])))) / max(1, len(qtokens))
        score = (0.72 * cosine) + (0.2 * overlap) + (0.08 * name_overlap)
        if score <= 0:
            continue
        hits.append(
            SearchHit(
                name=str(row["name"]),
                score=score,
                file=file,
                line=row["line"] if isinstance(row["line"], int) else None,
                kind=row_kind or None,
                role=row_role or None,
                doc=str(row["doc"]) if row["doc"] else None,
                provider=provider.name,
                why=_why(qtokens, row_tokens, str(row["name"]), file, str(row["doc"] or "")),
            )
        )
    hits.sort(key=lambda h: (-h.score, h.name))
    return [h.as_dict() for h in hits[: max(1, min(50, top_k))]]
