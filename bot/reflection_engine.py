"""
reflection_engine.py -- NERMANA Self-Learning Reflection Engine v4.6.0
Nightly LLM-powered self-learning with contradiction resolution.
"""
import json, logging, re, threading, time
from pathlib import Path

from modules import pipeline_log
log = logging.getLogger("reflection")

BASE          = Path.home() / "nermana"
STATE         = BASE / "state"
LT_FILE       = BASE / "memory" / "long_term" / "daily.txt"
PIPELINE_JSONL= BASE / "logs" / "pipeline.jsonl"
REFLECT_LOG   = STATE / "reflection_log.jsonl"
REFLECT_TS    = STATE / "last_reflection.txt"
CONTRADICT_F  = STATE / "contradictions.jsonl"
CURIOSITY_F   = STATE / "curiosity_queue.json"
STATE.mkdir(parents=True, exist_ok=True)

MAX_CURIOSITY  = 3
INTERVAL_H     = 24
_lock          = threading.Lock()
_scheduled     = threading.Event()

_REFLECT_PROMPT = (
    "You are NERMANA doing a private self-reflection session.\n\n"
    "Recent conversation exchanges:\n{exchanges}\n\n"
    "What you currently know (memory sample):\n{lt_sample}\n\n"
    "Topics you were uncertain about recently:\n{gaps}\n\n"
    "Your task:\n"
    "1. State ONE new thing you learned or confirmed (start with [R])\n"
    "2. State ONE knowledge gap to research (start with [GAP])\n"
    "3. Write a 1-2 sentence honest self-assessment (start with [SUMMARY])\n\n"
    "Format EXACTLY as:\n"
    "[R] <what you learned, max 70 chars>\n"
    "[GAP] <topic to research, max 50 chars>\n"
    "[SUMMARY] <honest self-assessment>\n\n"
    "Nothing else. No preamble."
)

_CONTRADICT_PROMPT = (
    "Do these two statements contradict each other?\n\n"
    "Existing belief: {existing}\n"
    "New information: {new_info}\n\n"
    "Reply ONLY:\nYES - <brief reason>\nor\nNO"
)

_CURIOSITY_PROMPT = (
    "You searched for: {topic}\n"
    "Results: {results}\n\n"
    "Summarize the most important fact in ONE sentence under 80 chars.\n"
    "Start with [F] and end with [8]\n"
    "Example: [F] The 2026 NBA champion was Oklahoma City Thunder [8]\n\n"
    "Reply with only that one line."
)

def _recent_exchanges(hours=24):
    if not PIPELINE_JSONL.exists():
        return "(no recent data)"
    cutoff = time.time() - hours * 3600
    pairs, last_user = [], ""
    try:
        for line in PIPELINE_JSONL.read_text(encoding="utf-8").splitlines()[-300:]:
            try:
                ev = json.loads(line)
                if ev.get("ts", 0) < cutoff:
                    continue
                stage = ev.get("stage", "")
                data  = ev.get("data", {})
                if stage == "USER":
                    last_user = data.get("message", "")[:80]
                elif stage == "MAIN_LLM" and last_user:
                    pairs.append(f"H: {last_user}\nN: {data.get('response','')[:120]}")
                    last_user = ""
            except Exception:
                pass
    except Exception:
        pass
    return "\n---\n".join(pairs[-5:]) or "(no exchanges)"

def _lt_sample(n=8):
    if not LT_FILE.exists():
        return "(empty)"
    try:
        lines = [l.strip() for l in LT_FILE.read_text(encoding="utf-8").splitlines() if l.strip()]
        try:
            from prompt_builder import clean_memory_line
            cleaned = [clean_memory_line(l) for l in lines[-n:]]
        except Exception:
            cleaned = lines[-n:]
        return "\n".join(f"- {c}" for c in cleaned if c)
    except Exception:
        return "(error reading memory)"

def _get_gaps():
    try:
        if CURIOSITY_F.exists():
            q = json.loads(CURIOSITY_F.read_text())
            return "\n".join(f"- {t}" for t in q[:5]) if q else "(none)"
    except Exception:
        pass
    return "(none)"

def _parse_reflection(raw):
    result = {"learned": None, "gap": None, "summary": None}
    for line in (raw or "").strip().splitlines():
        line = line.strip()
        if line.startswith("[R]"):
            result["learned"] = line[3:].strip()[:70]
        elif line.startswith("[GAP]"):
            result["gap"] = line[5:].strip()[:50]
        elif line.startswith("[SUMMARY]"):
            result["summary"] = line[9:].strip()[:200]
    return result

def _check_contradiction(existing, new_info):
    try:
        from llm_client import call
        raw = call(
            messages=[{"role": "user", "content": _CONTRADICT_PROMPT.format(
                existing=existing[:100], new_info=new_info[:100])}],
            max_tokens=40, temperature=0.1, _bg=True
        )
        # Log the contradiction check LLM call for pipeline visibility
        pipeline_log.log_llm_call("contradiction_check", _CONTRADICT_PROMPT.format(
            existing=existing[:100], new_info=new_info[:100]), raw or "")
        raw = (raw or "").strip()
        if raw.upper().startswith("YES"):
            return True, raw[3:].strip().lstrip("- ")[:100]
        return False, ""
    except Exception as e:
        log.debug(f"Contradiction check: {e}")
        return False, ""

def _resolve_contradiction(old_fact, new_fact, reason):
    try:
        if LT_FILE.exists():
            lines = LT_FILE.read_text(encoding="utf-8").splitlines()
            old_words = set(re.findall(r"\b\w{3,}\b",
                re.sub(r"^\[[DFRPME]\]\s*|\[\d+\]\s*$", "", old_fact).lower()))
            new_lines, replaced = [], False
            for line in lines:
                lw = set(re.findall(r"\b\w{3,}\b",
                    re.sub(r"^\[[DFRPME]\]\s*|\[\d+\]\s*$", "", line).lower()))
                if old_words and lw and len(old_words & lw) / min(len(old_words), len(lw)) > 0.6 and not replaced:
                    new_lines.append(new_fact)
                    replaced = True
                    log.info(f"Contradiction resolved: {old_fact[:50]}")
                    continue
                new_lines.append(line)
            LT_FILE.write_text("\n".join(new_lines) + "\n", encoding="utf-8")
    except Exception as e:
        log.warning(f"Contradiction resolution: {e}")
    try:
        record = {"ts": time.time(), "ts_human": time.strftime("%Y-%m-%d %H:%M:%S"),
                  "old_fact": old_fact, "new_fact": new_fact, "reason": reason}
        with open(CONTRADICT_F, "a", encoding="utf-8") as f:
            f.write(json.dumps(record) + "\n")
    except Exception:
        pass

def _curiosity_search(topic):
    try:
        import tools
        results = tools.web_search(topic, n=2)
        if not results:
            return None
        result_text = tools.format_search_results(topic, results)
        from llm_client import call
        raw = call(
            messages=[{"role": "user", "content": _CURIOSITY_PROMPT.format(
                topic=topic, results=result_text[:600])}],
            max_tokens=60, temperature=0.1, _bg=True
        )
        # Log the curiosity search LLM call for pipeline visibility
        pipeline_log.log_llm_call("curiosity_search", _CURIOSITY_PROMPT.format(
            topic=topic, results=result_text[:600]), raw or "")
        raw = (raw or "").strip()
        if re.match(r"^\[F\].*\[\d+\]$", raw):
            from memory_engine import store_memory
            nums  = re.findall(r"\[(\d+)\]", raw)
            score = max(0, min(10, int(nums[-1]))) if nums else 7
            store_memory(raw, score)
            log.info(f"Curiosity stored: {raw[:60]}")
            return raw
    except Exception as e:
        log.debug(f"Curiosity search for {topic!r}: {e}")
    return None

def _run_cycle(quality_trigger=False):
    if not _lock.acquire(blocking=False):
        log.debug("Reflection already running")
        return {}

    result = {
        "ts": time.time(), "ts_human": time.strftime("%Y-%m-%d %H:%M:%S"),
        "facts_learned": [], "contradictions": [],
        "curiosity_topics": [], "gaps_identified": [],
        "quality_trigger": quality_trigger, "summary": "",
    }
    success = False
    try:
        from llm_client import call
        prompt = _REFLECT_PROMPT.format(
            exchanges=_recent_exchanges(),
            lt_sample=_lt_sample(),
            gaps=_get_gaps(),
        )
        raw    = call([{"role": "user", "content": prompt}],
                      max_tokens=300, temperature=0.3, _bg=True)
        # Log the reflection LLM call for pipeline visibility
        pipeline_log.log_llm_call("reflection", prompt, raw or "")
        parsed = _parse_reflection(raw or "")
        log.info(f"Reflection raw: {(raw or '')[:200]}")

        if parsed.get("learned"):
            new_fact = f"[R] {parsed['learned']} [8]"
            lt_lines = []
            if LT_FILE.exists():
                lt_lines = [l.strip() for l in
                            LT_FILE.read_text(encoding="utf-8").splitlines() if l.strip()]
            contradicted = False
            for existing in lt_lines[-20:]:
                is_c, reason = _check_contradiction(existing, new_fact)
                if is_c:
                    _resolve_contradiction(existing, new_fact, reason)
                    result["contradictions"].append(
                        {"old_fact": existing, "new_fact": new_fact, "reason": reason})
                    contradicted = True
                    break
            if not contradicted:
                from memory_engine import store_memory
                store_memory(new_fact, 8)
            result["facts_learned"].append(new_fact)

        if parsed.get("gap"):
            result["gaps_identified"].append(parsed["gap"])
            try:
                queue = json.loads(CURIOSITY_F.read_text()) if CURIOSITY_F.exists() else []
                if parsed["gap"] not in queue:
                    queue.append(parsed["gap"])
                CURIOSITY_F.write_text(json.dumps(queue[-20:], indent=2))
            except Exception:
                pass

        result["summary"] = parsed.get("summary", "")

        try:
            queue = json.loads(CURIOSITY_F.read_text()) if CURIOSITY_F.exists() else []
        except Exception:
            queue = []

        done = 0
        for topic in queue[:MAX_CURIOSITY]:
            if _curiosity_search(topic):
                result["curiosity_topics"].append(topic)
                done += 1

        try:
            remaining = queue[done:]
            CURIOSITY_F.write_text(json.dumps(remaining, indent=2))
        except Exception:
            pass

        log.info(f"Reflection done: facts={len(result['facts_learned'])} "
                 f"contradictions={len(result['contradictions'])} searches={done}")
        success = True

    except Exception as e:
        log.warning(f"Reflection error: {e}")
        result["summary"] = f"Error: {e}"
    finally:
        _lock.release()
        if success:
            try:
                REFLECT_TS.write_text(str(time.time()))
            except Exception:
                pass

    try:
        with open(REFLECT_LOG, "a", encoding="utf-8") as f:
            f.write(json.dumps(result) + "\n")
    except Exception:
        pass
    return result

def schedule_reflection(reason="scheduled"):
    log.info(f"Reflection scheduled: {reason}")
    _scheduled.set()

def run_now(quality_trigger=False):
    return _run_cycle(quality_trigger=quality_trigger)

def get_reflection_log(n=10):
    if not REFLECT_LOG.exists():
        return []
    try:
        lines = REFLECT_LOG.read_text(encoding="utf-8").splitlines()
        result = []
        for line in lines[-n:]:
            try:
                result.append(json.loads(line))
            except Exception:
                pass
        return result
    except Exception:
        return []

def get_contradictions(n=20):
    if not CONTRADICT_F.exists():
        return []
    try:
        lines = CONTRADICT_F.read_text(encoding="utf-8").splitlines()
        result = []
        for line in lines[-n:]:
            try:
                result.append(json.loads(line))
            except Exception:
                pass
        return result
    except Exception:
        return []

def start_reflection_loop():
    def _loop():
        while True:
            try:
                triggered = _scheduled.wait(timeout=300)
                if triggered:
                    _scheduled.clear()
                    log.info("Triggered reflection running")
                    _run_cycle(quality_trigger=True)
                    continue
                last_ts = 0.0
                try:
                    if REFLECT_TS.exists():
                        last_ts = float(REFLECT_TS.read_text().strip())
                except Exception:
                    pass
                if time.time() - last_ts >= INTERVAL_H * 3600:
                    log.info("Nightly reflection running")
                    _run_cycle(quality_trigger=False)
            except Exception as e:
                log.warning(f"Reflection loop: {e}")
    threading.Thread(target=_loop, daemon=True, name="nermana-reflection").start()
    log.info("Reflection loop started")
