"""
nermana_self_monitor.py -- Per-Exchange Quality Scorer & Correction Detector
v4.6.0
"""
import json, logging, re, threading, time
from pathlib import Path

from modules import pipeline_log
log = logging.getLogger("self_monitor")

BASE          = Path.home() / "nermana"
STATE         = BASE / "state"
QUALITY_JSONL = STATE / "quality_scores.jsonl"
CURIOSITY_F   = STATE / "curiosity_queue.json"
STATE.mkdir(parents=True, exist_ok=True)

QUALITY_RETAIN_DAYS = 7

_CORRECTION_PHRASES = [
    "that is wrong", "thats wrong", "not correct", "not right",
    "actually,", "actually.", "not quite", "you are wrong",
    "youre wrong", "incorrect", "nope,", "nope.", "wrong,", "wrong!",
    "no, it", "no, that", "no! that", "no! it",
    "that is false", "that is not right", "that is not correct",
    "you are mistaken", "youre mistaken",
]

def _is_correction(text):
    low = text.lower().strip()
    return any(phrase in low for phrase in _CORRECTION_PHRASES)

_TOPIC_RE = re.compile(r"\b(?:about|regarding|on|the|for)\s+([A-Z][a-zA-Z0-9\s]{2,30})")

_QUALITY_PROMPT = (
    "Rate the quality of this AI reply on a scale of 1-5.\n\n"
    "1 = Hallucination, clearly wrong, or no useful content\n"
    "2 = Vague, evasive, or partially wrong\n"
    "3 = Acceptable but could be better\n"
    "4 = Good, accurate, direct\n"
    "5 = Excellent, accurate, concise, insightful\n\n"
    "Human message: {user}\n"
    "AI reply: {bot}\n\n"
    "Reply with ONLY a single digit 1-5. Nothing else."
)

_lock = threading.Lock()

def _score_reply(user, bot):
    try:
        from llm_client import call
        raw = call(
            messages=[{"role": "user", "content": _QUALITY_PROMPT.format(
                user=user[:200], bot=bot[:300])}],
            max_tokens=5, temperature=0.1, _bg=True
        )
        # Log the quality score LLM call for pipeline visibility
        pipeline_log.log_llm_call("quality_score", _QUALITY_PROMPT.format(
            user=user[:200], bot=bot[:300]), raw or "")
        m = re.search(r"[1-5]", raw or "")
        return int(m.group()) if m else 0
    except Exception as e:
        log.debug(f"Quality score failed: {e}")
        return 0

def _extract_topic(user_msg):
    m = _TOPIC_RE.search(user_msg)
    if m:
        return m.group(1).strip()[:60]
    words = re.findall(r"\b[A-Z][a-zA-Z]{2,}\b", user_msg)
    if words:
        return " ".join(words[:3])
    return user_msg.strip()[:40]

def _store_correction(user_msg, bot_reply, topic):
    try:
        from memory_engine import store_memory
        store_memory(f"[M] NERMANA was corrected on: {topic[:60]} [6]", 6)
        log.info(f"Correction stored: {topic}")
    except Exception as e:
        log.debug(f"Correction store failed: {e}")
    _add_to_curiosity(topic)

def _add_to_curiosity(topic):
    with _lock:
        try:
            queue = json.loads(CURIOSITY_F.read_text()) if CURIOSITY_F.exists() else []
            if topic not in queue:
                queue.append(topic)
            CURIOSITY_F.write_text(json.dumps(queue[-20:], indent=2))
        except Exception as e:
            log.debug(f"Curiosity queue: {e}")

def _write_quality_record(user, bot, score, correction, topic):
    record = {
        "ts": time.time(), "ts_human": time.strftime("%Y-%m-%d %H:%M:%S"),
        "user_msg": user[:80], "bot_reply": bot[:200],
        "score": score, "correction": correction, "topic": topic,
    }
    try:
        with open(QUALITY_JSONL, "a", encoding="utf-8") as f:
            f.write(json.dumps(record) + "\n")
        _prune_quality_log()
    except Exception as e:
        log.debug(f"Quality record: {e}")

def _prune_quality_log():
    if not QUALITY_JSONL.exists():
        return
    cutoff = time.time() - QUALITY_RETAIN_DAYS * 86400
    try:
        lines = QUALITY_JSONL.read_text(encoding="utf-8").splitlines()
        kept = []
        for l in lines:
            try:
                if json.loads(l).get("ts", 0) >= cutoff:
                    kept.append(l)
            except Exception:
                pass
        QUALITY_JSONL.write_text("\n".join(kept) + "\n", encoding="utf-8")
    except Exception:
        pass

def _trigger_reflection(score):
    if score > 0 and score < 3:
        log.info(f"Low quality ({score}/5) -- scheduling reflection")
        try:
            from reflection_engine import schedule_reflection
            schedule_reflection(reason=f"low_quality_{score}")
        except Exception as e:
            log.debug(f"Schedule reflection: {e}")

def score_reply(user_msg, bot_reply):
    if len(user_msg.strip()) < 5 or len(bot_reply.strip()) < 5:
        return
    topic      = _extract_topic(user_msg)
    correction = _is_correction(user_msg)
    score      = _score_reply(user_msg, bot_reply)
    _write_quality_record(user_msg, bot_reply, score, correction, topic)
    if correction:
        log.info(f"Correction on: {topic}")
        _store_correction(user_msg, bot_reply, topic)
    _trigger_reflection(score)

def get_curiosity_queue():
    try:
        if CURIOSITY_F.exists():
            return json.loads(CURIOSITY_F.read_text())
    except Exception:
        pass
    return []

def clear_curiosity_queue():
    with _lock:
        try:
            CURIOSITY_F.write_text("[]")
        except Exception:
            pass

def get_recent_quality(n=20):
    if not QUALITY_JSONL.exists():
        return []
    try:
        lines = QUALITY_JSONL.read_text(encoding="utf-8").splitlines()
        result = []
        for line in lines[-n:]:
            try:
                result.append(json.loads(line))
            except Exception:
                pass
        return result
    except Exception:
        return []
