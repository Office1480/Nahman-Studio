"""Recherche par le sens : vecteurs multilingues calculés localement, sans clé ni coût."""
import numpy as np

from . import db

MODEL = "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2"
_model = None
_index: tuple[int, np.ndarray, np.ndarray] | None = None


def available() -> bool:
    try:
        import fastembed  # noqa: F401
        return True
    except ImportError:
        return False


def _embed(texts: list[str], cache_dir: str) -> np.ndarray:
    global _model
    if _model is None:
        from fastembed import TextEmbedding
        _model = TextEmbedding(MODEL, cache_dir=cache_dir)
    vectors = np.array(list(_model.embed(texts, batch_size=32)), dtype=np.float32)
    return vectors / np.linalg.norm(vectors, axis=1, keepdims=True)


def index_missing(cache_dir: str, limit: int = 2000) -> int:
    """Calcule les vecteurs des passages qui n'en ont pas encore."""
    with db.connect() as c:
        rows = c.execute("SELECT p.id, p.text FROM passages p LEFT JOIN vectors v ON v.passage_id=p.id "
                         "WHERE v.passage_id IS NULL LIMIT ?", (limit,)).fetchall()
    if not rows:
        return 0
    vectors = _embed([r["text"] for r in rows], cache_dir).astype(np.float16)
    with db.connect() as c:
        c.executemany("INSERT OR REPLACE INTO vectors(passage_id, vec) VALUES (?,?)",
                      [(r["id"], v.tobytes()) for r, v in zip(rows, vectors)])
    return len(rows)


def _load() -> tuple[np.ndarray, np.ndarray] | None:
    global _index
    with db.connect() as c:
        count = c.execute("SELECT COUNT(*) FROM vectors").fetchone()[0]
        if not count:
            return None
        if _index is None or _index[0] != count:
            rows = c.execute("SELECT passage_id, vec FROM vectors").fetchall()
            ids = np.array([r["passage_id"] for r in rows])
            matrix = np.frombuffer(b"".join(r["vec"] for r in rows), dtype=np.float16).reshape(len(rows), -1)
            _index = (count, ids, matrix)
    return _index[1], _index[2]


def search(query: str, cache_dir: str, k: int = 80, floor: float = 0.42) -> list[tuple[int, float]]:
    """Passages les plus proches du sens de la requête : [(id, score)]."""
    if not available():
        return []
    loaded = _load()
    if loaded is None:
        return []
    ids, matrix = loaded
    scores = matrix.astype(np.float32) @ _embed([query], cache_dir)[0]
    best = np.argsort(-scores)[:k]
    return [(int(ids[i]), float(scores[i])) for i in best if scores[i] >= floor]
