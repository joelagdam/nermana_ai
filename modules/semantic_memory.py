import json, logging, sqlite3, threading, time
from pathlib import Path
from typing import List, Dict

import numpy as np

log = logging.getLogger("semantic_memory")

BASE    = Path.home() / "nermana"
DB_PATH = BASE / "memory" / "embeddings" / "vectors.db"

_vec_cache: list = []
_cache_dirty       = True
_cache_lock        = threading.Lock()

_embed_healthy      = None
_last_health_check  = 0.0
_HEALTH_TTL         = 30.0


def _load_cfg():
    cfg = {}
    cfg_file = BASE / ".config"
    if cfg_file.exists():
        for line in cfg_file.read_text().splitlines():
            if "=" in line and not line.startswith("#"):
                k, v = line.split("=", 1)
                cfg[k.strip()] = v.strip().strip('"').strip("'")
    return cfg

CFG              = _load_cfg()
SEMANTIC_ENABLED = CFG.get("SEMANTIC_MEMORY_ENABLED", "true").lower() == "true"


def _init_db():
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(DB_PATH))
    conn.execute(
        "CREATE TABLE IF NOT EXISTS vectors "
        "(id TEXT PRIMARY KEY, text TEXT, vec BLOB)"
    )
    conn.commit()
    conn.close()

_init_db()


def _probe_embed_server() -> bool:
    global _embed_healthy, _last_health_check
    now = time.time()
    if now - _last_health_check < _HEALTH_TTL and _embed_healthy is not None:
        return _embed_healthy
    try:
        from llm_client import embed
        result = embed("health check", timeout=3)
        _embed_healthy    = bool(result)
        _last_health_check = now
        return _embed_healthy
    except Exception:
        _embed_healthy    = False
        _last_health_check = now
        return False


def is_available() -> bool:
    if not SEMANTIC_ENABLED:
        return False
    return _probe_embed_server()


def _rebuild_cache():
    global _vec_cache, _cache_dirty
    try:
        conn = sqlite3.connect(str(DB_PATH))
        rows = conn.execute("SELECT text, vec FROM vectors").fetchall()
        conn.close()
        built = []
        for text, vec_blob in rows:
            try:
                vec = np.frombuffer(vec_blob, dtype=np.float32).copy()
                built.append((text, vec))
            except Exception:
                pass
        with _cache_lock:
            _vec_cache   = built
            _cache_dirty = False
        log.debug(f"Vector cache rebuilt: {len(built)} entries")
    except Exception as e:
        log.warning(f"Vector cache rebuild failed: {e}")


def store_embedding(fact_id: str, text: str):
    if not SEMANTIC_ENABLED:
        return
    try:
        from llm_client import embed
        vec = embed(text, timeout=10)
        if not vec:
            return
        vec_blob = np.array(vec, dtype=np.float32).tobytes()
        conn = sqlite3.connect(str(DB_PATH))
        conn.execute(
            "INSERT OR REPLACE INTO vectors (id, text, vec) VALUES (?, ?, ?)",
            (fact_id, text, vec_blob)
        )
        conn.commit()
        conn.close()
        global _cache_dirty
        _cache_dirty = True
    except Exception as e:
        log.warning(f"store_embedding failed: {e}")


def semantic_search(query: str, top_k: int = 5) -> List[Dict]:
    if not SEMANTIC_ENABLED:
        return []
    try:
        from llm_client import embed
        query_vec = embed(query, timeout=8)
        if not query_vec:
            return []
    except Exception as e:
        log.warning(f"Query embed failed: {e}")
        return []

    if _cache_dirty:
        _rebuild_cache()

    with _cache_lock:
        cache_snapshot = list(_vec_cache)

    if not cache_snapshot:
        return []

    query_np = np.array(query_vec, dtype=np.float32)
    q_norm   = np.linalg.norm(query_np)
    if q_norm == 0:
        return []

    results = []
    for text, vec in cache_snapshot:
        if len(vec) != len(query_np):
            continue
        v_norm = np.linalg.norm(vec)
        if v_norm == 0:
            continue
        sim = float(np.dot(query_np, vec) / (q_norm * v_norm))
        if sim > 0.2:
            results.append((sim, text))

    results.sort(key=lambda x: x[0], reverse=True)
    return [{"text": text, "score": sim} for sim, text in results[:top_k]]


def init_semantic_memory():
    log.info("Neural semantic memory initializing — preloading vector cache")
    _rebuild_cache()
