import re, json, time, logging, threading, shutil
from pathlib import Path
from collections import deque
from datetime import datetime, timedelta

log = logging.getLogger("memory_engine")

BASE = Path.home() / "nermana"
LT   = BASE / "memory/long_term"
ST   = BASE / "memory/short_term"
JUNK = BASE / "memory/junk"
BUF  = BASE / "memory/buffer"
KNW  = BASE / "knowledge"

for d in [LT, ST, JUNK, BUF, KNW]:
    d.mkdir(parents=True, exist_ok=True)

def _load_cfg():
    cfg = {}
    cfg_file = BASE / ".config"
    if cfg_file.exists():
        for line in cfg_file.read_text().splitlines():
            if "=" in line and not line.startswith("#"):
                k, v = line.split("=", 1)
                cfg[k.strip()] = v.strip().strip('"').strip("'")
    return cfg

CFG           = _load_cfg()
BUFFER_WINDOW = int(CFG.get("BUFFER_WINDOW", 20))
LT_SCORE_MIN  = int(CFG.get("LONG_TERM_SCORE_MIN", 7))

_buf_lock = threading.Lock()

_buffer = deque(maxlen=BUFFER_WINDOW)
_buf_file = BUF / "current.jsonl"
if _buf_file.exists():
    try:
        for _line in _buf_file.read_text(encoding="utf-8").splitlines():
            try:
                _buffer.append(json.loads(_line))
            except Exception:
                pass
        log.info(f"Buffer reloaded from disk: {len(_buffer)} entries")
    except Exception as _e:
        log.warning(f"Buffer reload failed: {_e}")

try:
    from semantic_memory import semantic_search, store_embedding, init_semantic_memory, is_available
    SEMANTIC_OK = True
except Exception:
    SEMANTIC_OK = False
    def semantic_search(*args, **kwargs): return []
    def store_embedding(*args, **kwargs): pass
    def is_available(): return False


def add_to_buffer(user: str, bot: str):
    entry = {"user": user[:200], "bot": bot[:400], "ts": time.time()}
    with _buf_lock:
        _buffer.append(entry)
        try:
            with open(_buf_file, "w", encoding="utf-8") as f:
                for e in _buffer:
                    f.write(json.dumps(e) + "\n")
        except Exception as e:
            log.warning(f"Buffer write failed: {e}")


def get_buffer():
    return list(_buffer)


def store_memory(line: str, score: int, user_ctx: str = ""):
    today = datetime.now().strftime("%Y-%m-%d")
    if score >= LT_SCORE_MIN:
        with open(LT / "daily.txt", "a", encoding="utf-8") as f:
            f.write(line + "\n")
        fact_id = f"{int(time.time())}_{abs(hash(line)) % 999999}"
        store_embedding(fact_id, line)
    elif score >= 4:
        with open(ST / f"{today}_ctx.txt", "a", encoding="utf-8") as f:
            f.write(line + "\n")
    else:
        with open(JUNK / f"{today}_disc.txt", "a", encoding="utf-8") as f:
            f.write(line + "\n")


def _keyword_filter(text: str, words: set) -> bool:
    return any(w in text.lower() for w in words)


def _extract_query_words(user_input: str) -> set:
    STOP = {
        "the","and","for","you","this","that","with","from","are","was",
        "have","not","its","can","did","how","who","what","when","where","why"
    }
    words = set(re.findall(r'\b[a-zA-Z0-9_]{3,}\b', user_input.lower()))
    return words - STOP


def get_relevant_memory(user_input: str, max_facts: int = 5) -> dict:
    result = {
        "buffer":   list(_buffer)[-4:],
        "lt_facts": [],
        "st_facts": [],
        "keywords": [],
    }
    words = _extract_query_words(user_input)
    result["keywords"] = list(words)

    if is_available():
        try:
            sem = semantic_search(user_input, top_k=max_facts)
            result["lt_facts"] = [r["text"] for r in sem]
        except Exception as e:
            log.warning(f"Semantic search failed: {e}")

    if not result["lt_facts"] and words:
        for lt_file in LT.glob("*.txt"):
            if len(result["lt_facts"]) >= max_facts:
                break
            try:
                lines = lt_file.read_text(encoding="utf-8").splitlines()
                for line in lines:
                    line = line.strip()
                    if line and _keyword_filter(line, words):
                        result["lt_facts"].append(line)
                        if len(result["lt_facts"]) >= max_facts:
                            break
            except Exception:
                pass

    today     = datetime.now().date()
    yesterday = today - timedelta(days=1)
    for day in [today, yesterday]:
        st_file = ST / f"{day.strftime('%Y-%m-%d')}_ctx.txt"
        if st_file.exists():
            try:
                lines = st_file.read_text(encoding="utf-8").splitlines()
                for line in reversed(lines):
                    line = line.strip()
                    if not line:
                        continue
                    if not words or _keyword_filter(line, words):
                        result["st_facts"].append(line)
                    if len(result["st_facts"]) >= 3:
                        break
            except Exception:
                pass
        if len(result["st_facts"]) >= 3:
            break

    _cleanup_old_st()

    return result


_last_st_cleanup = 0.0

def _cleanup_old_st():
    global _last_st_cleanup
    now = time.time()
    if now - _last_st_cleanup < 3600:
        return
    _last_st_cleanup = now
    cutoff = datetime.now().date() - timedelta(days=7)
    try:
        for f in ST.glob("*_ctx.txt"):
            try:
                day_str = f.name.split("_")[0]
                from datetime import date
                if date.fromisoformat(day_str) < cutoff:
                    f.unlink()
                    log.debug(f"Cleaned old ST file: {f.name}")
            except Exception:
                pass
    except Exception as e:
        log.debug(f"ST cleanup error: {e}")


def get_stats() -> dict:
    def count(p):
        if not p.exists():
            return 0
        total = 0
        for f in p.glob("*.txt"):
            try:
                total += sum(1 for _ in open(f, encoding="utf-8"))
            except Exception:
                pass
        return total
    return {
        "buffer":     len(_buffer),
        "long_term":  count(LT),
        "short_term": count(ST),
        "junk":       count(JUNK),
    }


def clear_all():
    for d in [LT, ST, JUNK, BUF, KNW]:
        if not d.exists():
            continue
        for item in d.glob("*"):
            try:
                if item.is_file():
                    item.unlink(missing_ok=True)
                elif item.is_dir():
                    shutil.rmtree(item, ignore_errors=True)
            except Exception as e:
                log.warning(f"clear_all: could not remove {item}: {e}")
    with _buf_lock:
        _buffer.clear()
    try:
        import sqlite3
        db = BASE / "memory" / "embeddings" / "vectors.db"
        if db.exists():
            conn = sqlite3.connect(str(db))
            conn.execute("DELETE FROM vectors")
            conn.commit()
            conn.close()
    except Exception as e:
        log.warning(f"clear_all: vector DB clear failed: {e}")
