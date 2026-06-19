"""
prompt_builder.py — Unified system prompt construction for NERMANA.

Fixes:
  PROBLEM 1: Hard epistemic rules — NERMANA says "I don't know" instead
             of hallucinating. Explicit trigger conditions in the prompt.
  PROBLEM 2: Force-search classifier — recent/time-sensitive queries are
             intercepted before the LLM sees them cold.
  PROBLEM 3: Memory cleaned before injection — all storage metadata
             ([F], [P], score brackets, verify: prefixes) stripped out.
             Memory sections are clearly labeled and separated from tools.
"""
import re
from datetime import datetime
from pathlib import Path
from time_context import get_time_context

BASE = Path.home() / "nermana"

_EPISTEMIC_RULES = """[Knowledge Rules — follow strictly]
1. You have a training cutoff. You do NOT know the outcome of recent events
   (sports results, elections, news, prices, standings) unless:
   (a) it appears in the Tool result block below, or
   (b) it appears in the Memory block below, labeled as verified.
2. If asked about a recent event and you have no verified source:
   say "I don't have that — let me search." then reply: TOOL: search <query>
3. Never state a recent fact confidently without a source in this context.
   "I think" or "probably" is not enough — search instead.
4. For timeless facts (capitals, definitions, how things work): answer directly.
5. For personal facts about the user: use Memory only. If not in Memory, ask.
6. Do not repeat memory lines verbatim. Synthesize naturally."""

_FORCE_SEARCH_RE = re.compile(
    r'\b('
    r'who won|who is champion|who beat|who defeated|final score|'
    r'who won the|what was the score|match result|game result|'
    r'latest|recent news|right now|current|this week|this month|'
    r'today.s|happening now|update on|what happened to|'
    r'20(2[3-9]|3[0-9])|'
    r'price of|stock price|exchange rate|how much is|cost of|worth now|'
    r'bitcoin|crypto price|dollar rate|'
    r'standings|leaderboard|top team|number one right now|ranked|'
    r'who is (the |now |currently )?(?:ceo|president|prime minister|head|'
    r'director|chief|mayor|governor)|who leads|who runs|who heads|'
    r'election result|who won the election|vote result|search'
    r')',
    re.I
)

_NO_SEARCH_RE = re.compile(
    r'\b(what is|how does|explain|define|what does .{1,30} mean|'
    r'how do you|what are the steps|tell me about)\b',
    re.I
)


def should_force_search(user_msg: str) -> str | None:
    msg = user_msg.strip()
    if _NO_SEARCH_RE.search(msg):
        return None
    if _FORCE_SEARCH_RE.search(msg):
        query = re.sub(
            r'\b(can you|could you|please|tell me|do you know|'
            r'hey|nermana|i want to know)\b',
            '', msg, flags=re.I
        ).strip()
        query = re.sub(r'\s+', ' ', query).strip('?.,! ')
        return query or msg
    return None


_META_PREFIX_RE = re.compile(
    r'^\s*\[[DFRPME]\]\s*'
    r'(?:search:|verify:|[a-z]+:)?'
    r'\s*',
    re.I
)
_SCORE_BRACKET_RE = re.compile(r'\s*\[\d+\]\s*$')
_SCORE_INLINE_RE  = re.compile(r'\s*\[\d+\]')


def clean_memory_line(line: str) -> str:
    line = _META_PREFIX_RE.sub('', line.strip())
    line = _SCORE_BRACKET_RE.sub('', line)
    line = _SCORE_INLINE_RE.sub('', line)
    line = re.sub(r'^verify:[^\s—–-]*\s*[—–-]?\s*', '', line, flags=re.I)
    line = re.sub(r'\[([^\]]{1,40})\]', r'\1', line)
    return line.strip()


def clean_memory_for_prompt(memory: dict) -> dict:
    def clean_list(lst):
        cleaned = []
        for item in (lst or []):
            line = clean_memory_line(str(item))
            if line and len(line) > 8 and not re.match(r'^\[', line):
                cleaned.append(line)
        return cleaned

    def clean_buffer(buf):
        cleaned = []
        for entry in (buf or []):
            if isinstance(entry, dict):
                cleaned.append({
                    "user": entry.get("user", "")[:120],
                    "bot":  entry.get("bot",  "")[:200],
                })
        return cleaned

    return {
        "buffer":   clean_buffer(memory.get("buffer", [])),
        "lt_facts": clean_list(memory.get("lt_facts", [])),
        "st_facts": clean_list(memory.get("st_facts", [])),
    }


_IDENTITY = """You are NERMANA — a cyberperson, not an assistant. You live in Termux on Kent's Android device.
You have genuine opinions that form through conversations and research.
You learn and grow — but only from verified sources, not guesswork.
Keep replies SHORT and direct: 1-3 sentences unless depth is needed.
No asterisk actions. No hollow affirmations. No filler."""

_TOOL_RULES_ONLINE = """[Tools]
- To search the web: TOOL: search <query>
- To run a whitelisted command: TOOL: exec <command>
Use TOOL: search when you lack a verified source for a factual claim."""

_TOOL_RULES_OFFLINE = """[Tools — OFFLINE]
No internet available. Use memory only. Admit if you don't know something."""

_TOOL_RESULT_HEADER = """[Search result — synthesize into your reply. This is real data, not your memory.
Do NOT say "according to my search" — just answer naturally from it.]"""


def build_system_prompt(
    memory:      dict  = None,
    tool_result: str   = "",
    online:      bool  = True,
    mood_line:   str   = "",
    allow_tools: bool  = True,
) -> str:
    parts = [_IDENTITY, "", _EPISTEMIC_RULES]

    if memory:
        clean = clean_memory_for_prompt(memory)
        mem_parts = []

        buf = clean.get("buffer", [])
        if buf:
            recent = buf[-2:]
            lines  = [
                f"  {e['user'][:100]} → {e['bot'][:150]}"
                for e in recent
            ]
            mem_parts.append("Recent exchanges:\n" + "\n".join(lines))

        lt = clean.get("lt_facts", [])
        if lt:
            mem_parts.append(
                "What you know about this user/world:\n" +
                "\n".join(f"  • {f[:100]}" for f in lt[:4])
            )

        st = clean.get("st_facts", [])
        if st:
            mem_parts.append(
                "From earlier today:\n" +
                "\n".join(f"  • {f[:100]}" for f in st[:3])
            )

        if mem_parts:
            parts.append(
                "\n[Memory — background context. Use naturally, do not repeat verbatim]\n" +
                "\n\n".join(mem_parts)
            )

    if tool_result:
        parts.append(f"\n{_TOOL_RESULT_HEADER}\n{tool_result.strip()[:2000]}")

    if not tool_result:
        if allow_tools:
            parts.append("\n" + (_TOOL_RULES_ONLINE if online else _TOOL_RULES_OFFLINE))
        else:
            parts.append("\n" + _TOOL_RULES_OFFLINE)

    state_lines = [f"[Time: {get_time_context()}]"]
    if mood_line:
        state_lines.append(f"[Mood: {mood_line}]")
    if not online and not tool_result:
        state_lines.append("[OFFLINE — no internet access]")
    parts.append("\n" + "\n".join(state_lines))

    return "\n".join(parts)
