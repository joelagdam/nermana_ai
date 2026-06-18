"""
nermana_primer.py v4.7.2
Builds the [Memory:] block injected into the LLM system prompt.

Key rules:
  - Only inject LT facts that share >=2 content words with the current query
    (BUG-14: prevents unrelated facts causing hallucination / topic confusion)
  - Never fall back to the raw KNW/facts.txt dump regardless of query topic
    (BUG-14: that dump injects everything -- terrible for focused replies)
  - Primer is ONLY called at depth=0. depth>0 (tool pass) reuses the briefing
    already built at depth=0, so the tool result in the system prompt is not
    corrupted by a stale or wrong memory injection (BUG-15b)
  - Cache TTL: 90s (was 60s); prune at 20 entries (was 30)
"""
import re, sys, time as _time
from pathlib import Path

sys.path.insert(0, str(Path.home() / "nermana" / "modules"))
from memory_engine import get_relevant_memory
from pipeline_log import log_memory_query, log_primer_call

_cache = {}
_CACHE_TTL = 90

_STOP = {
    "the","and","for","you","this","that","with","from","are","was","have",
    "not","its","what","how","can","will","does","did","but","just","like",
    "your","mine","then","than","when","where","who","why","there","been",
    "about","more","also","some","their"
}

def _content_words(text: str) -> set:
    return {w for w in re.findall(r'\b[a-zA-Z0-9_]{3,}\b', text.lower())
            if w not in _STOP}

def _is_relevant(fact: str, query_words: set, threshold: int = 2) -> bool:
    if not query_words:
        return True
    fact_words = _content_words(fact)
    return len(fact_words & query_words) >= threshold


def run(user_message: str, conversation_history=None, use_cache: bool = True):
    cache_key = user_message.strip()[:60]
    if use_cache and cache_key in _cache:
        briefing, memory, ts = _cache[cache_key]
        if _time.time() - ts < _CACHE_TTL:
            log_primer_call("cached", user_message, briefing)
            return briefing, memory

    memory = get_relevant_memory(user_message)
    log_memory_query(user_message, memory)

    query_words = _content_words(user_message)
    parts = []

    lt_raw = memory.get("lt_facts") or []
    lt_relevant = [f for f in lt_raw if _is_relevant(f, query_words, threshold=2)]
    if lt_relevant:
        fact_lines = [f"  - {f[:140]}" for f in lt_relevant[:6]]
        parts.append("Known facts:\n" + "\n".join(fact_lines))

    st_raw = memory.get("st_facts") or []
    st_relevant = [f for f in st_raw if _is_relevant(f, query_words, threshold=1)]
    if st_relevant:
        parts.append("Today's context:\n" + "\n".join(
            f"  - {f[:120]}" for f in st_relevant[-4:]))

    briefing = "\n\n".join(parts)

    log_primer_call("deterministic", user_message, briefing or "(empty)")

    if use_cache:
        _cache[cache_key] = (briefing, memory, _time.time())
        if len(_cache) > 20:
            oldest = min(_cache, key=lambda k: _cache[k][2])
            del _cache[oldest]

    return briefing, memory
