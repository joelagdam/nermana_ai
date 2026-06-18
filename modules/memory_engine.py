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
SUM  = BASE / "memory/summaries"  # NEW: Summaries directory

for d in [LT, ST, JUNK, BUF, KNW, SUM]:
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
SUMMARY_FACTS_PER_TOPIC = int(CFG.get("SUMMARY_FACTS_PER_TOPIC", 5))  # NEW: How many facts to keep in summary per topic

_buf_lock = threading.Lock()
_sum_lock = threading.Lock()  # NEW: Lock for summary updates

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


def _extract_topic_from_fact(fact: str) -> str:
    """Extract a topic key from a fact for summarization.
    Example: '[F] NERMANA uses llama.cpp for LLM inference [8]' -> 'llama.cpp inference'
    Mobile-optimized: simple keyword extraction, no NLP needed."""
    # Remove category tag and score
    clean = re.sub(r'^\[[DFRPME]\]\s*|\[\d+\]\s*$', '', fact.strip())
    if not clean:
        return "general"

    # Extract meaningful words (3+ chars, not stopwords)
    STOP = {"the","and","for","you","this","that","with","from","are","was",
            "have","not","its","can","did","how","who","what","when","where","why",
            "is","of","in","on","at","to","as","by","an","be","or","if","so","up","out"}
    words = [w.lower() for w in re.findall(r'\b[a-zA-Z0-9_]{3,}\b', clean)
             if w.lower() not in STOP and len(w) >= 3]

    if not words:
        return "general"

    # Take first 2-3 significant words as topic
    return " ".join(words[:3]) if len(words) > 3 else " ".join(words)


def _update_summary(topic: str, new_fact: str, score: int):
    """Update the summary file for a topic with a new fact.
    Maintains top N facts by score (with recency tiebreaker).
    Mobile-optimized: bounded storage, minimal computation."""
    with _sum_lock:
        summary_file = SUM / f"{topic.replace(' ', '_')}.txt"

        # Load existing summary facts
        facts = []
        if summary_file.exists():
            try:
                content = summary_file.read_text(encoding="utf-8").strip()
                if content:
                    for line in content.split(';;'):  # Using ;; as separator (rare in facts)
                        if line.strip():
                            parts = line.strip().split('||', 1)  # fact||score
                            if len(parts) == 2:
                                facts.append((parts[0], int(parts[1]), time.time()))  # fact, score, timestamp
            except Exception as e:
                log.debug(f"Could not load summary for {topic}: {e}")

        # Add new fact
        facts.append((new_fact, score, time.time()))

        # Sort by score (desc), then by timestamp (desc for recency), keep top N
        facts.sort(key=lambda x: (x[1], x[2]), reverse=True)
        facts = facts[:SUMMARY_FACTS_PER_TOPIC]

        # Write back summary
        try:
            lines = [f"{fact}||{score}" for fact, score, _ in facts]
            summary_file.write_text(";;".join(lines) + "\n", encoding="utf-8")
            log.debug(f"Updated summary for '{topic}': {len(facts)} facts")
        except Exception as e:
            log.warning(f"Failed to write summary for {topic}: {e}")


def store_memory(line: str, score: int, user_ctx: str = ""):
    today = datetime.now().strftime("%Y-%m-%d")
    if score >= LT_SCORE_MIN:
        with open(LT / "daily.txt", "a", encoding="utf-8") as f:
            f.write(line + "\n")
        fact_id = f"{int(time.time())}_{abs(hash(line)) % 999999}"
        store_embedding(fact_id, line)

        # NEW: Also update summary for this fact
        topic = _extract_topic_from_fact(line)
        _update_summary(topic, line, score)

    elif score >= 4:
        with open(ST / f"{today}_ctx.txt", "a", encoding="utf-8") as f:
            f.write(line + "\n")

        # NEW: Also update summary for medium-score facts (optional)
        # topic = _extract_topic_from_fact(line)
        # _update_summary(topic, line, score)
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
        "summaries": [],  # NEW: Add summaries to results
    }
    words = _extract_query_words(user_input)
    result["keywords"] = list(words)

    # NEW: Check summaries first for ultra-fast retrieval (mobile optimization)
    if words:
        try:
            for summary_file in SUM.glob("*.txt"):
                if len(result["summaries"]) >= 3:  # Limit summaries returned
                    break
                try:
                    topic = summary_file.stem.replace('_', ' ')
                    # Simple topic matching: check if any query word is in topic
                    if any(word in topic.lower() for word in words):
                        content = summary_file.read_text(encoding="utf-8").strip()
                        if content:
                            # Parse summary facts
                            summary_lines = []
                            for part in content.split(';;'):
                                if part.strip():
                                    fact_score = part.strip().split('||', 1)
                                    if len(fact_score) == 2:
                                        summary_lines.append(fact_score[0])  # Just the fact
                            if summary_lines:
                                result["summaries"].append({
                                    "topic": topic,
                                    "facts": summary_lines
                                })
                except Exception as e:
                    log.debug(f"Could not read summary {summary_file}: {e}")
        except Exception as e:
            log.debug(f"Summary search failed: {e}")

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

    # NEW: Count summary files and facts
    summary_count = 0
    summary_facts = 0
    if SUM.exists():
        for f in SUM.glob("*.txt"):
            summary_count += 1
            try:
                content = f.read_text(encoding="utf-8").strip()
                if content:
                    summary_facts += len([p for p in content.split(';;') if p.strip()])
            except Exception:
                pass

    return {
        "buffer":     len(_buffer),
        "long_term":  count(LT),
        "short_term": count(ST),
        "junk":       count(JUNK),
        "summaries":  summary_count,  # NEW
        "summary_facts": summary_facts,  # NEW
    }


def clear_all():
    for d in [LT, ST, JUNK, BUF, KNW, SUM]:  # NEW: Include summaries
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