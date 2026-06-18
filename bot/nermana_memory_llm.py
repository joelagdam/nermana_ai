"""
nermana_memory_llm.py v4.7.2

BUG-13: Old prompt included format examples inline ([D] description [7]).
SmolLM2 pattern-matched those examples and reproduced them verbatim
instead of analyzing the actual conversation. Replaced with a pure
imperative ruleset -- no examples in the prompt body at all.

Everything else (multi-fact, MAX_TOKENS=320, score regex) from v4.7.1.
"""
import logging, re, sys, threading, time
from pathlib import Path

sys.path.insert(0, str(Path.home() / "nermana" / "modules"))
from llm_client import call, CFG
from memory_engine import store_memory
from pipeline_log import log_memory_eval

log = logging.getLogger("memory_llm")

MAX_TOKENS = int(CFG.get("MEMORY_MAX_TOKENS", "320"))

PROMPT = """\
Analyze the conversation and extract every distinct fact worth remembering.
Output one line per fact. Nothing else. No preamble.

Each line must follow this structure:
- Start with a category tag in square brackets.
- Then a short description of the fact (under 65 characters).
- End with a relevance score in square brackets (0 through 10).

Category tags to use:
  D for a decision, plan, or commitment made.
  F for factual information that was learned or confirmed.
  R for a reflection, realization, or change of view.
  P for a stated preference, opinion, or value.
  M for a mistake, correction, or identified bug.
  E for an emotional state or mood expressed.

Scoring rules:
  Score 8 or 9 for facts that are specific, named, or directly actionable.
  Score 7 for facts that are clearly memorable but not uniquely specific.
  Score 5 or 6 for context that is useful today but may not matter tomorrow.
  Score 3 or 4 for generic or mildly useful information.
  Score 1 or 2 for noise or barely relevant content.
  Score 0 only if there is absolutely nothing worth storing.

If there is nothing to store, output exactly one line with score 0.
Do not repeat the conversation. Do not explain your choices. Lines only.\
"""

_ACK_RE = re.compile(
    r'^(ok|okay|k|thx|thanks|ty|np|sure|yes|no|yep|nope|lol|haha|'
    r'|cool|nice|got it|understood|noted|right|great|got it)\\.?$',
    re.I
)

_HEDGE_RE = re.compile(
    r'\b(i think|i believe|i\'m not sure|probably|might be|could be|'
    r'i\'m guessing|not certain|not sure|i guess|perhaps|roughly|'
    r'approximately|if i recall)\b', re.I
)
_FACTUAL_TOPIC_RE = re.compile(
    r'\b(what is|who is|when did|how many|where is|define|explain|'
    r'according to|the fact|research|study|data|statistic)\b', re.I
)

_LINE_RE  = re.compile(r'^\s*\[[DFRPMEdfrtpme]\]\s*.+\[\d{1,2}\]\s*$')
_SCORE_RE = re.compile(r'\[(\d{1,2})\]\s*$')


def _is_trivial(user, bot):
    if len(user.strip()) < 8 or len(bot.strip()) < 8:
        return True
    return bool(_ACK_RE.match(user.strip()) or _ACK_RE.match(bot.strip()))


def _parse_score(line: str) -> int:
    m = _SCORE_RE.search(line)
    return max(0, min(10, int(m.group(1)))) if m else 4


def heuristic_eval(user, bot):
    combined = (user + " " + bot).lower()
    results = []
    if any(w in combined for w in ["decided","chosen","going to","will use","switching to"]):
        results.append(("[D]", 7))
    if any(w in combined for w in ["always","never","prefer","like","hate","favorite","love"]):
        results.append(("[P]", 6))
    if any(w in combined for w in ["error","mistake","wrong","failed","bug","crash","broken"]):
        results.append(("[M]", 5))
    if any(w in combined for w in ["learned","discovered","found out","realized","turns out"]):
        results.append(("[F]", 7))
    if not results and len(bot.strip()) > 80:
        results.append(("[F]", 4))
    output = []
    for tag, score in results[:3]:
        line = f"{tag} {user[:60]} [{score}]"
        output.append((line, score))
    return output


def _background_verify(topic_hint):
    try:
        import tools
        results = tools.web_search(topic_hint, n=1)
        if not results:
            return
        snippet = results[0].get("snippet", "")[:200]
        if snippet:
            store_memory(f"[F] verify:{topic_hint[:35]} -- {snippet[:65]} [5]", 5)
    except Exception as e:
        log.debug(f"Background verify: {e}")


def run(user_message: str, nermana_response: str) -> dict:
    if _is_trivial(user_message, nermana_response):
        log.debug("Skipping eval: trivial exchange")
        return {"stored": [], "skipped": True}

    exchange = f"Human: {user_message}\nNERMANA: {nermana_response}"

    raw = call(
        messages=[{"role": "user", "content": f"Conversation:\n{exchange}"}],
        system=PROMPT,
        max_tokens=MAX_TOKENS,
        temperature=0.15,
        _bg=True
    )

    raw_lines = (raw or "").strip().splitlines()
    valid = [l.strip() for l in raw_lines if _LINE_RE.match(l.strip())]

    if not valid:
        log.debug(f"Evaluator output unusable, using heuristic. raw={repr(raw[:80])}")
        pairs = heuristic_eval(user_message, nermana_response)
    else:
        pairs = [(l, _parse_score(l)) for l in valid[:4]]

    stored_facts = []
    for line, score in pairs:
        if score < 1:
            continue
        tier = "long_term" if score >= 7 else "short_term" if score >= 4 else "junk"
        store_memory(line, score, user_ctx=user_message)
        log_memory_eval(exchange, raw or "", line, score, tier)
        stored_facts.append({"line": line, "score": score, "tier": tier})
        log.debug(f"Stored [{tier}] s={score}: {line[:55]}")

    if _HEDGE_RE.search(nermana_response) and _FACTUAL_TOPIC_RE.search(user_message):
        threading.Thread(
            target=_background_verify, args=(user_message[:80],), daemon=True
        ).start()

    return {"stored": stored_facts}


_LAST_CONSOL = Path.home() / "nermana" / "state" / "last_consolidation.txt"


def _consolidate():
    try:
        lt_file = Path.home() / "nermana" / "memory" / "long_term" / "daily.txt"
        if not lt_file.exists():
            return
        lines = [l.strip() for l in lt_file.read_text().splitlines() if l.strip()]
        if len(lines) < 10:
            return
        def words(s):
            return set(re.findall(r'[a-zA-Z0-9]{3,}',
                re.sub(r'^\[[DFRPME]\]\s*|\[\d+\]\s*$', '', s).lower()))
        kept, removed = [], 0
        for i, a in enumerate(lines):
            wa = words(a)
            dominated = any(
                words(b) and wa and len(wa & words(b)) / min(len(wa), len(words(b))) > 0.65
                and len(b) >= len(a)
                for b in lines[i+1:]
            )
            if dominated:
                removed += 1
            else:
                kept.append(a)
        if removed > 0:
            lt_file.write_text("\n".join(kept) + "\n")
            log.info(f"Consolidation: removed {removed} near-duplicates")
        _LAST_CONSOL.write_text(str(time.time()))
    except Exception as e:
        log.warning(f"Consolidation error: {e}")


def start_consolidation_loop():
    def _loop():
        while True:
            try:
                last = float(_LAST_CONSOL.read_text().strip()) if _LAST_CONSOL.exists() else 0
                if time.time() - last > 86400:
                    _consolidate()
            except Exception as e:
                log.debug(f"Consolidation loop: {e}")
            time.sleep(3600)
    threading.Thread(target=_loop, daemon=True, name="nermana-consolidation").start()
