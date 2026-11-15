"""Optional local semantic ranker (Swedish/English) via fastembed + ONNX. No network use after the
one-time model download; profile text never leaves the machine.

Ads are chunked (the model sees ~128 tokens), each chunk is compared with each profile evidence/project
snippet, and a job's score is the mean of its best three chunk similarities.
"""
from __future__ import annotations

import re
import time
from pathlib import Path

MODEL = "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2"   # Apache-2.0, ~0.22 GB, 384-d
CACHE = Path(__file__).resolve().parents[2] / "data" / "models"
MAX_CHUNKS, CHUNK_WORDS = 12, 80

_model = None


def available() -> bool:
    try:
        import fastembed  # noqa: F401
        import numpy  # noqa: F401
        return True
    except ImportError:
        return False


def _get(model_name: str = MODEL):
    global _model
    if _model is None:
        from fastembed import TextEmbedding
        CACHE.mkdir(parents=True, exist_ok=True)
        _model = TextEmbedding(model_name=model_name, cache_dir=str(CACHE))
    return _model


def chunk(title: str, text: str) -> list[str]:
    words = re.sub(r"\s+", " ", text or "").split(" ")
    chunks = [(title or "").strip()] if title else []
    for i in range(0, len(words), CHUNK_WORDS):
        c = " ".join(words[i:i + CHUNK_WORDS]).strip()
        if c:
            chunks.append(c)
    return chunks[:MAX_CHUNKS] or [""]


def profile_snippets(profile) -> list[str]:
    d = profile.data
    out = [f"{e['label']}. {e.get('where', '')}" for e in d.get("evidence", [])]
    out += [f"{p['title']}. {p.get('summary', '')}" for p in d.get("projects", [])]
    return out


def embed_texts(texts: list[str], model_name: str = MODEL):
    import numpy as np
    arr = np.array(list(_get(model_name).embed(texts)), dtype="float32")
    return arr / (np.linalg.norm(arr, axis=1, keepdims=True) + 1e-9)


def ensure_job_embeddings(conn, rows: list[dict], model_name: str = MODEL, progress=None) -> dict:
    """Compute and cache per-job chunk vectors. Returns {'computed': n, 'cached': n, 'seconds': s, 'chunks': n}."""
    import numpy as np
    stats = {"computed": 0, "cached": 0, "chunks": 0, "seconds": 0.0}
    have = {(r["source_id"], r["content_hash"]) for r in conn.execute("SELECT source_id, content_hash FROM embeddings WHERE model=?", (model_name,))}
    t0 = time.perf_counter()
    for i, r in enumerate(rows):
        j = r["job"]
        if (j["source_id"], j["content_hash"]) in have:
            stats["cached"] += 1
            continue
        ch = chunk(j["title"], j["description_text"])
        v = embed_texts(ch, model_name)
        conn.execute("INSERT OR REPLACE INTO embeddings VALUES (?,?,?,?,?,?)",
                     (j["source_id"], j["content_hash"], model_name, len(ch), v.tobytes(), v.shape[1]))
        stats["computed"] += 1
        stats["chunks"] += len(ch)
        if i % 50 == 0:
            conn.commit()
            if progress:
                progress(i, len(rows))
    conn.commit()
    stats["seconds"] = round(time.perf_counter() - t0, 1)
    return stats


def cache_complete(conn, rows: list[dict], model_name: str = MODEL) -> bool:
    have = {(r["source_id"], r["content_hash"]) for r in conn.execute("SELECT source_id, content_hash FROM embeddings WHERE model=?", (model_name,))}
    return all((r["job"]["source_id"], r["job"]["content_hash"]) in have for r in rows)


def embedding_scores(conn, rows: list[dict], profile, model_name: str = MODEL, top_n: int = 3) -> dict[str, float]:
    import numpy as np
    snippets = embed_texts(profile_snippets(profile), model_name)
    blobs = {(r["source_id"], r["content_hash"]): r for r in conn.execute("SELECT * FROM embeddings WHERE model=?", (model_name,))}
    out = {}
    for r in rows:
        j = r["job"]
        b = blobs.get((j["source_id"], j["content_hash"]))
        if b is None:
            continue
        v = np.frombuffer(b["vectors"], dtype="float32").reshape(b["n_chunks"], b["dim"])
        best = (v @ snippets.T).max(axis=1)                 # best profile match per chunk
        out[j["source_id"]] = float(np.sort(best)[::-1][:top_n].mean())
    return out
