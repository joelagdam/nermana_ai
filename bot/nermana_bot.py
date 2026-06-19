#!/usr/bin/env python3
import sys, asyncio, re, time, json, threading, queue, logging, socket
from pathlib import Path
from collections import deque

sys.path.insert(0, str(Path.home() / "nermana" / "modules"))
sys.path.insert(0, str(Path.home() / "nermana" / "bot"))

from llm_client import call, call_stream, CFG
from memory_engine import add_to_buffer, get_stats
from pipeline_log import log_user_message, log_error, get_recent, log_tool_use, log_llm_call
from time_context import get_time_context, get_time_short
from prompt_builder import build_system_prompt, should_force_search
import mood, tools, confirm_state as cstate, scheduler, idle_sleep
import nermana_primer as primer
import nermana_memory_llm as memory_llm
import nermana_self_monitor as self_monitor
import reflection_engine
from diagnostics import start_diagnostic_loop

try:
    from semantic_memory import init_semantic_memory
    init_semantic_memory()
except Exception:
    pass

from telegram import Update
from telegram.ext import ApplicationBuilder, MessageHandler, CommandHandler, TypeHandler, filters
from telegram.constants import ChatAction

TOKEN        = CFG.get("TELEGRAM_TOKEN", "")
TEMPERATURE  = float(CFG.get("TEMPERATURE", "0.7"))
MAX_TOKENS   = int(CFG.get("MAIN_MAX_TOKENS", "400"))
REP_PENALTY  = float(CFG.get("REPETITION_PENALTY", "1.15"))
VERSION      = "4.7.2-20260618"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
    handlers=[
        logging.FileHandler(Path.home() / "nermana" / "logs" / "bot.log"),
        logging.StreamHandler(),
    ],
)
log = logging.getLogger("nermana")

if not TOKEN or TOKEN in ("YOUR_TOKEN_HERE", ""):
    log.error("TELEGRAM_TOKEN missing in ~/.nermana/.config")
    raise SystemExit("No valid TELEGRAM_TOKEN")

conversation_history = deque(maxlen=20)

_awake_cache = {"ok": False, "ts": 0.0}
_AWAKE_TTL = 30.0

def _is_server_awake_cached() -> bool:
    global _awake_cache
    if _awake_cache["ok"] and (time.time() - _awake_cache["ts"]) < _AWAKE_TTL:
        return True
    result = idle_sleep.is_server_awake()
    _awake_cache["ok"] = result
    _awake_cache["ts"] = time.time()
    return result

def is_online():
    try:
        socket.create_connection(("8.8.8.8", 53), timeout=1)
        return True
    except Exception:
        return False

def _clean(text: str) -> str:
    if not text:
        return ""
    text = re.sub(r'\*[^*]+\*', '', text)
    text = re.sub(r'^\s*(NERMANA:|Assistant:|AI:)\s*', '', text, flags=re.I)
    return ' '.join(text.split())[:500]


def _heuristic_reply(query: str) -> str | None:
    """Return a direct answer for simple factual queries without LLM if confident."""
    # Only for short, question-like queries
    q = query.strip().lower()
    if not q or len(q) > 60:
        return None
    # Simple interrogative patterns
    if not (q.startswith("what is ") or q.startswith("who is ") or q.startswith("where is ") or
            q.startswith("when is ") or q.startswith("how many ") or q.startswith("define ") or
            q.startswith("explain ")):
        return None
    # Try to get a concise answer from web search
    try:
        if not is_online():
            return None
        results = tools.web_search(query, n=2)
        if not results:
            return None
        # Score each result by presence of query terms in snippet+title
        query_words = set(re.findall(r'\b[a-zA-Z0-9_]{3,}\b', q))
        STOP = {"the","and","for","you","this","that","with","from","are","was",
                "have","not","its","can","will","does","did","but","just","like",
                "what","who","when","where","why","how","is","of","in","on","at","to"}
        query_words = query_words - STOP
        best_snippet = ""
        best_score = -1
        for r in results:
            if not isinstance(r, dict):
                continue
            text = (r.get('snippet', '') + ' ' + r.get('title', '')).lower()
            words_in = set(re.findall(r'\b[a-zA-Z0-9_]{3,}\b', text))
            score = len(query_words & words_in)
            if score > best_score:
                best_score = score
                best_snippet = r.get('snippet', '').strip()
                if not best_snippet:
                    best_snippet = r.get('title', '').strip()
        if not best_snippet or best_score < 1:
            return None
        # Heuristic: if snippet contains a pattern like "X is Y" where X is the query topic
        # Extract a short answer: first sentence up to 200 chars
        # Take first sentence
        import re
        sentences = re.split(r'(?<=[.!?])\s+', best_snippet)
        answer = sentences[0] if sentences else best_snippet
        if len(answer) > 200:
            answer = answer[:200] + '...'
        # Prepend a natural phrase
        return f"According to search: {answer}"
    except Exception:
        return None

async def stream_response(msg, ctx, chat_id, user_text, system_prompt, history):
    full       = ""
    last_sent  = ""
    last_edit  = 0.0
    EDIT_INT   = 0.4
    state      = "buffering"
    _TOOL_RE   = re.compile(r'^\s*TOOL:\s*(search|exec)\s+(.+)', re.I)
    messages   = list(history) + [{"role": "user", "content": user_text}]

    try:
        for chunk in call_stream(
            messages, system=system_prompt,
            max_tokens=MAX_TOKENS, temperature=TEMPERATURE,
            repeat_penalty=REP_PENALTY
        ):
            full += chunk

            if state == "buffering":
                stripped = full.lstrip()
                if "\n" not in stripped:
                    upper = stripped.upper()
                    if "TOOL:".startswith(upper) or upper.startswith("TOOL:"):
                        continue
                    state = "streaming"
                else:
                    first = stripped.split("\n", 1)[0]
                    state = "directive" if _TOOL_RE.match(first) else "streaming"
                    if state == "directive":
                        continue

            if state == "streaming":
                now = time.time()
                if now - last_edit >= EDIT_INT:
                    cleaned = _clean(full)
                    if cleaned and cleaned != last_sent:
                        try:
                            await msg.edit_text(cleaned)
                            last_sent  = cleaned
                            last_edit  = now
                        except Exception:
                            pass

        if state == "directive":
            first = full.strip().split("\n", 1)[0]
            m     = _TOOL_RE.match(first)
            if m:
                return "", (m.group(1).lower(), m.group(2).strip()[:200])

        final = _clean(full)
        if not final:
            final = call(messages, system=system_prompt, max_tokens=MAX_TOKENS,
                         temperature=TEMPERATURE)
        final = final or "\u2026"
        if final != last_sent:
            await msg.edit_text(final)
            last_sent = final
        return final, None

    except Exception as e:
        log_error("STREAM", str(e))
        try:
            error_msg = "\u26a0\ufe0f Error generating response"
            if error_msg != last_sent:
                await msg.edit_text(error_msg)
                last_sent = error_msg
        except Exception:
            pass
        return "", None

def _commit_exchange(user_text: str, response: str):
    conversation_history.append({"role": "user",      "content": user_text})
    conversation_history.append({"role": "assistant",  "content": response})
    add_to_buffer(user_text, response)

def _run_memory_eval(user_text: str, response: str):
    def _both():
        try:
            memory_llm.run(user_text, response)
        except Exception as e:
            log.debug(f"memory_llm.run error: {e}")
        try:
            self_monitor.score_reply(user_text, response)
        except Exception as e:
            log.debug(f"score_reply error: {e}")
    threading.Thread(target=_both, daemon=True, name="nermana-memeval").start()

def _extract_search_facts(query: str, results: list):
    try:
        from memory_engine import store_memory
        stored = 0
        for r in (results or [])[:3]:
            snippet = (r.get("snippet") or "").strip()
            if not snippet or len(snippet) < 20:
                continue
            sentences = re.split(r'(?<=[.!?])\s+', snippet)
            for sent in sentences[:2]:
                sent = sent.strip()
                if (len(sent) > 25 and
                    re.match(r'^[A-Z0-9]', sent) and
                    not re.search(
                        r'(click here|read more|cookie|privacy|javascript)',
                        sent, re.I
                    )):
                    store_memory(f"[F] search:{query[:30]} \u2014 {sent[:70]} [5]", 5)
                    stored += 1
                    if stored >= 3:
                        return
    except Exception as e:
        log.debug(f"search fact extraction: {e}")

async def _run_pipeline(
    update, ctx, user_text: str,
    tool_result: str = "",
    _depth: int = 0,
    _briefing: str = "",
    _memory: dict = None,
    _tool_history_committed: bool = False,
    _msg=None,
):
    chat_id = update.effective_chat.id
    msg     = _msg if _msg is not None else await update.effective_message.reply_text("\u2026")

    if not await asyncio.to_thread(_is_server_awake_cached):
        await msg.edit_text("\u23f3 Waking up\u2026")
        if not await asyncio.to_thread(idle_sleep.ensure_awake):
            await msg.edit_text("\u274c LLM server not responding. Run: nermana start")
            return
        await msg.edit_text("\u2026")

    idle_sleep.record_activity()
    typing = asyncio.create_task(_typing_loop(ctx, chat_id))

    try:
        history_list = list(conversation_history)

        if _depth == 0:
            briefing, memory = await asyncio.to_thread(
                primer.run, user_text, history_list
            )
        else:
            briefing = _briefing
            memory   = _memory or {}

        sys_prompt = build_system_prompt(
            memory      = memory,
            tool_result = tool_result,
            online      = is_online(),
            mood_line   = mood.get_mood_line(),
            allow_tools = (_depth == 0) and not tool_result,
        )

        response, directive = await stream_response(
            msg, ctx, chat_id, user_text, sys_prompt, history_list
        )
        # Log the main LLM call for pipeline visibility
        # Construct approximate full prompt: system + history + current user message
        full_prompt_parts = []
        if sys_prompt:
            full_prompt_parts.append(f"System: {sys_prompt}")
        if history_list:
            for msg in history_list:
                if msg.get("role") == "user":
                    full_prompt_parts.append(f"User: {msg.get('content', '')}")
                elif msg.get("role") == "assistant":
                    full_prompt_parts.append(f"Assistant: {msg.get('content', '')}")
        full_prompt_parts.append(f"User: {user_text}")
        full_prompt = "\n\n".join(full_prompt_parts)
        pipeline_log.log_llm_call("main_llm", full_prompt, response or "")
    finally:
        typing.cancel()

    if directive:
        kind, payload = directive

        if kind == "search":
            await msg.edit_text(f"\ud83d\udd0d Searching: {payload}\u2026")
            results     = await asyncio.to_thread(tools.web_search, payload)
            result_text = tools.format_search_results(payload, results)
            mood.record_tool_use("search")
            log_tool_use("search", "ai", payload, len(result_text))
            try:
                from memory_engine import store_memory as _sm
                _sm(f"[F] searched:{payload[:50]} \u2014 found {len(results)} result(s) [5]", 5)
            except Exception:
                pass
            threading.Thread(
                target=_extract_search_facts, args=(payload, results), daemon=True
            ).start()

            await msg.edit_text(f"\ud83d\udd0d Got results for: {payload}\n\u23f3 Reading\u2026")

            if not _tool_history_committed:
                conversation_history.append({"role": "user",     "content": user_text})
                conversation_history.append({"role": "assistant", "content": f"Searching: {payload}"})
                conversation_history.append({"role": "user",     "content": f"[Search: {payload}]\n{result_text[:1800]}"})

            await _run_pipeline(
                update, ctx, user_text,
                tool_result             = result_text[:1800],
                _depth                  = _depth + 1,
                _briefing               = briefing,
                _memory                 = memory,
                _tool_history_committed = True,
                _msg                    = msg,
            )

        elif kind == "exec":
            if not tools.is_command_allowed(payload):
                await msg.edit_text("Command not allowed.")
                return
            cstate.set_pending(chat_id, "exec", {"cmd": payload, "orig_user": user_text})
            await msg.edit_text(f"Run: `{payload}`\nReply YES to confirm.")
        return

    if response:
        if not _tool_history_committed:
            _commit_exchange(user_text, response)
        else:
            conversation_history.append({"role": "assistant", "content": response})
            add_to_buffer(user_text, response)
        _run_memory_eval(user_text, response)


async def _typing_loop(ctx, chat_id):
    try:
        while True:
            await ctx.bot.send_chat_action(chat_id, ChatAction.TYPING)
            await asyncio.sleep(4)
    except Exception:
        pass


async def _handle_confirmed(update, ctx, pending):
    kind    = pending.get("kind")
    payload = pending.get("payload", {})
    if kind == "weather":
        city = payload.get("city", "")
        w    = tools.get_weather(city)
        weather_text = tools.format_weather(w)
        log_tool_use("weather", "user", city, len(weather_text))
        mood.record_tool_use("weather")
        await _run_pipeline(
            update, ctx, payload.get("orig_user", f"weather in {city}"),
            tool_result = f"Weather for {city}:\n{weather_text}",
        )
    elif kind == "exec":
        cmd      = payload.get("cmd", "")
        res      = tools.run_command(cmd)
        orig_usr = payload.get("orig_user", "")
        mood.record_tool_use("exec")
        log_tool_use("exec", "ai", cmd, len(res.get("output", "")))
        if res["ok"]:
            conversation_history.append({"role": "user",     "content": orig_usr})
            conversation_history.append({"role": "assistant", "content": f"Running: {cmd}"})
            conversation_history.append({"role": "user",     "content": f"[exec: {cmd}]\n{res['output'][:800]}"})
            await _run_pipeline(
                update, ctx, orig_usr,
                tool_result             = f"$ {cmd}\n{res['output'][:800]}",
                _tool_history_committed = True,
            )
        else:
            await update.message.reply_text(f"Failed: {res['error']}")


async def on_message(update, ctx):
    text = (update.message.text or "").strip()
    if not text or text.startswith('/'):
        return

    log_user_message(text)
    mood.record_message(text)
    tools.set_offline_mode(not is_online())
    chat_id = update.effective_chat.id

    # Try heuristic reply for simple factual queries
    if is_online():
        heuristic = _heuristic_reply(text)
        if heuristic:
            await update.message.reply_text(heuristic)
            # Log exchange for memory and context
            conversation_history.append({"role": "user", "content": text})
            conversation_history.append({"role": "assistant", "content": heuristic})
            add_to_buffer(text, heuristic)
            return

    pending = cstate.get_pending(chat_id)
    if pending:
        if cstate.is_affirmative(text):
            cstate.clear_pending(chat_id)
            await _handle_confirmed(update, ctx, pending)
        elif cstate.is_negative(text):
            cstate.clear_pending(chat_id)
            await update.message.reply_text("Cancelled.")
        else:
            if pending.get("kind") == "weather":
                city = text.strip()
                cstate.clear_pending(chat_id)
                await update.message.reply_text(f"Checking weather for {city}\u2026")
                w = await asyncio.to_thread(tools.get_weather, city)
                mood.record_tool_use("weather")
                result_text = tools.format_weather(w)
                conversation_history.append({"role": "user",     "content": text})
                conversation_history.append({"role": "assistant", "content": f"Checking weather for {city}"})
                conversation_history.append({"role": "user",     "content": f"[Weather: {city}]\n{result_text}"})
                await _run_pipeline(
                    update, ctx, text,
                    tool_result             = result_text,
                    _tool_history_committed = True,
                )
            return
        return

    city = tools.extract_city(text)
    if city:
        cstate.set_pending(chat_id, "weather", {"city": city})
        await update.message.reply_text(f"Get weather for {city}? Reply YES (or another city).")
        return

    force_query = should_force_search(text) if is_online() else None
    if force_query:
        searching_msg = await update.message.reply_text(f"\ud83d\udd0d {force_query}")
        results     = await asyncio.to_thread(tools.web_search, force_query)
        result_text = tools.format_search_results(force_query, results)
        mood.record_tool_use("search")
        log_tool_use("search", "user", force_query, len(result_text))
        threading.Thread(
            target=_extract_search_facts, args=(force_query, results), daemon=True
        ).start()
        await searching_msg.edit_text(f"\ud83d\udd0d Got results for: {force_query}\n\u23f3 Reading\u2026")
        conversation_history.append({"role": "user",     "content": text})
        conversation_history.append({"role": "assistant", "content": f"Searching: {force_query}"})
        conversation_history.append({"role": "user",     "content": f"[Search: {force_query}]\n{result_text[:1800]}"})
        await _run_pipeline(
            update, ctx, text,
            tool_result             = result_text[:1800],
            _tool_history_committed = True,
            _msg                    = searching_msg,
        )
        return

    await _run_pipeline(update, ctx, text)


async def cmd_start(u, c):
    await u.message.reply_text(f"NERMANA v{VERSION}. /help")

async def cmd_status(u, c):
    s = get_stats()
    m = mood.get_mood()
    await u.message.reply_text(
        f"Mood: {m['label']} | Buffer:{s['buffer']} LT:{s['long_term']} "
        f"ST:{s['short_term']} History:{len(conversation_history)}")

async def cmd_clearhistory(u, c):
    global conversation_history
    conversation_history.clear()
    await u.message.reply_text("Conversation history cleared.")

async def cmd_reset(u, c):
    from memory_engine import _buffer
    _buffer.clear()
    conversation_history.clear()
    await u.message.reply_text("Session buffer and history cleared.")

async def cmd_forget(u, c):
    from memory_engine import clear_all
    clear_all()
    await u.message.reply_text("All memory cleared.")

async def cmd_test(u, c):
    if not await asyncio.to_thread(_is_server_awake_cached):
        await u.message.reply_text("Waking\u2026")
        await asyncio.to_thread(idle_sleep.ensure_awake)
    r = call([{"role": "user", "content": "Say: OK"}], max_tokens=10)
    await u.message.reply_text(f"LLM: {r[:100]}")

async def cmd_pipeline(u, c):
    ev = get_recent(5)
    if not ev:
        await u.message.reply_text("No events")
        return

    lines = []
    for e in ev:
        # Get timestamp in HH:MM:SS format
        time_str = e.get('ts_human', '')
        if time_str:
            time_str = f"[{time_str}]"
        else:
            time_str = ""

        stage = e.get('stage', 'UNKNOWN')
        data = e.get('data', {})
        if stage == 'USER':
            msg = data.get('message', '')
            lines.append(f"{time_str} 👤 USER: {msg[:80]}{'...' if len(msg)>80 else ''}")
        elif stage == 'MEMORY_QUERY':
            query = data.get('query', '')
            lt_count = data.get('lt_facts_count', 0)
            lines.append(f"{time_str} 🔍 MEMORY_QUERY: '{query}' → {lt_count} long-term facts")
        elif stage == 'PRIMER_CALL':
            source = data.get('source', '')
            briefing_len = data.get('briefing_len', 0)
            result_preview = data.get('result', '')[:80]
            lines.append(f"{time_str} 🧠 PRIMER_CALL[{source}] len={briefing_len}: {result_preview}{'...' if len(data.get('result',''))>80 else ''}")
        elif stage == 'MAIN_LLM':
            response = data.get('response', '')
            lines.append(f"{time_str} 💬 MAIN_LLM RESPONSE: {response[:120]}{'...' if len(response)>120 else ''}")
        elif stage == 'MEMORY_EVAL':
            stored = data.get('stored', '')
            score = data.get('score', 0)
            tier = data.get('tier', '')
            lines.append(f"{time_str} 🧮 MEMORY_EVAL: [{tier}] score={score} → {stored[:80]}{'...' if len(stored)>80 else ''}")
        elif stage == 'ERROR':
            where = data.get('where', '')
            message = data.get('message', '')
            lines.append(f"{time_str} ❌ ERROR[{where}]: {message[:80]}{'...' if len(message)>80 else ''}")
        elif stage == 'TOOL_USE':
            tool = data.get('tool', '')
            source = data.get('source', '')
            query = data.get('query', '')
            result_len = data.get('result_len', 0)
            lines.append(f"{time_str} 🔧 TOOL_USE[{tool}] by {source}: query='{query}' → {result_len} chars")
        elif stage == 'EMBEDDING':
            fact_id = data.get('fact_id', '')
            lines.append(f"{time_str} 🔢 EMBEDDING: fact_id={fact_id}")
        else:
            # fallback
            lines.append(f"{time_str} {stage}: {str(data)[:100]}")
    await u.message.reply_text("\n".join(lines))

async def cmd_time(u, c):
    await u.message.reply_text(get_time_short())

async def cmd_mood(u, c):
    await u.message.reply_text(f"Mood: {mood.get_mood_line()}")

async def cmd_weather(u, c):
    city = " ".join(c.args).strip() or tools.DEFAULT_CITY
    cstate.set_pending(u.effective_chat.id, "weather", {"city": city})
    await u.message.reply_text(f"Get weather for {city}? Reply YES.")

async def cmd_search(u, c):
    q = " ".join(c.args).strip()
    if not q:
        return
    if not is_online():
        await u.message.reply_text("Offline \u2013 cannot search.")
        return
    searching_msg = await u.message.reply_text("Searching\u2026")
    results     = await asyncio.to_thread(tools.web_search, q)
    result_text = tools.format_search_results(q, results)
    mood.record_tool_use("search")
    log_tool_use("search", "user", q, len(result_text))
    threading.Thread(target=_extract_search_facts, args=(q, results), daemon=True).start()
    await searching_msg.edit_text(f"\ud83d\udd0d Got results for: {q}\n\u23f3 Reading\u2026")
    conversation_history.append({"role": "user",     "content": q})
    conversation_history.append({"role": "assistant", "content": f"Searching: {q}"})
    conversation_history.append({"role": "user",     "content": f"[Search: {q}]\n{result_text[:1800]}"})
    await _run_pipeline(
        u, c, q,
        tool_result             = result_text[:1800],
        _tool_history_committed = True,
        _msg                    = searching_msg,
    )

async def cmd_exec(u, c):
    cmd = " ".join(c.args).strip()
    if not tools.is_command_allowed(cmd):
        await u.message.reply_text("Not allowed.")
        return
    cstate.set_pending(u.effective_chat.id, "exec",
                       {"cmd": cmd, "orig_user": f"/exec {cmd}"})
    await u.message.reply_text(f"Run `{cmd}`? Reply YES.")

async def cmd_remind(u, c):
    body   = " ".join(c.args)
    parsed = scheduler.parse_reminder(body)
    if not parsed["ok"]:
        await u.message.reply_text(f"Error: {parsed['error']}")
        return
    scheduler.add_reminder(
        u.effective_chat.id, parsed["text"],
        parsed["hour"], parsed["minute"], parsed["daily"]
    )
    await u.message.reply_text(
        f"Reminder: {parsed['text']} at {parsed['hour']:02d}:{parsed['minute']:02d}")

async def cmd_reminders(u, c):
    r = scheduler.list_reminders(u.effective_chat.id)
    if not r:
        await u.message.reply_text("No reminders.")
    else:
        await u.message.reply_text(
            "\n".join([f"#{x['id']} {x['text']} at {x['hour']:02d}:{x['minute']:02d}"
                       for x in r]))

async def cmd_forgetreminder(u, c):
    if c.args and c.args[0].lstrip('#').isdigit():
        scheduler.remove_reminder(u.effective_chat.id, int(c.args[0].lstrip('#')))
        await u.message.reply_text("Removed.")
    else:
        await u.message.reply_text("Usage: /forgetreminder <id>")

async def cmd_proactivity(u, c):
    if c.args and c.args[0] in "012":
        cfg_file = Path.home() / "nermana" / ".config"
        content  = cfg_file.read_text()
        import re as _re
        content  = _re.sub(
            r'^PROACTIVITY_LEVEL=.*', f'PROACTIVITY_LEVEL={c.args[0]}',
            content, flags=_re.M
        )
        cfg_file.write_text(content)
        await u.message.reply_text(f"Proactivity set to {c.args[0]}")
    else:
        await u.message.reply_text("Usage: /proactivity 0|1|2")

async def cmd_help(u, c):
    await u.message.reply_text(
        "/start /status /clearhistory /reset /forget /test /time /mood "
        "/weather /search /exec /remind /reminders /forgetreminder "
        "/proactivity /pipeline /help")

async def cmd_diagnose(u, c):
    await u.message.reply_text("Running diagnostics\u2026")
    def _run():
        import asyncio
        try:
            from diagnostics import run_and_persist
            r = run_and_persist()
            score_emoji = "\ud83d\udfe2" if r.health_score >= 80 else "\ud83d\udfe1" if r.health_score >= 60 else "\ud83d\udd34"
            lines = [
                f"{score_emoji} Health: {r.health_score}/100",
                f"Latency p95: {r.latency_p95_ms:.0f}ms",
                f"Memory \u2014 LT:{r.lt_count} ST:{r.st_count} Junk:{r.junk_count} ({r.junk_ratio:.0%} junk)",
                f"Memory hit rate: {r.memory_hit_rate:.0%}",
                f"Quality score: {r.avg_quality_score:.1f}/5",
                f"Corrections 24h: {r.correction_count_24h}",
                f"Errors/hr: {r.error_rate_per_hour:.1f}",
                f"Embedding: {'OK' if r.embedding_healthy else 'DOWN'}",
            ]
            if r.alerts:
                lines.append("Alerts:")
                lines += [f"  \u26a0 {a[:80]}" for a in r.alerts[:3]]
            asyncio.run(u.message.reply_text("\n".join(lines)))
        except ImportError:
            asyncio.run(u.message.reply_text("diagnostics module not available"))
        except Exception as e:
            asyncio.run(u.message.reply_text(f"Diagnostic error: {e}"))
    import threading as _t
    _t.Thread(target=_run, daemon=True).start()

async def cmd_reflect(u, c):
    await u.message.reply_text("Starting reflection cycle\u2026")
    def _run():
        import asyncio
        try:
            from reflection_engine import run_now
            result = run_now(quality_trigger=True)
            parts = ["Reflection complete."]
            if result.get("facts_learned"):
                parts.append(f"Learned: {result['facts_learned'][0][:80]}")
            if result.get("contradictions"):
                parts.append(f"Contradictions resolved: {len(result['contradictions'])}")
            if result.get("curiosity_topics"):
                parts.append(f"Searched: {', '.join(result['curiosity_topics'][:3])}")
            if result.get("summary"):
                parts.append(f"Summary: {result['summary'][:150]}")
            asyncio.run(u.message.reply_text("\n".join(parts)))
        except ImportError:
            asyncio.run(u.message.reply_text("reflection_engine not available"))
        except Exception as e:
            asyncio.run(u.message.reply_text(f"Reflection error: {e}"))
    import threading as _t
    _t.Thread(target=_run, daemon=True).start()

async def cmd_curiosity(u, c):
    try:
        from nermana_self_monitor import get_curiosity_queue
        q = get_curiosity_queue()
        if not q:
            await u.message.reply_text("Curiosity queue is empty.")
        else:
            await u.message.reply_text(
                "Pending research:\n" + "\n".join(f"  \u2022 {t}" for t in q[:10]))
    except ImportError:
        await u.message.reply_text("self_monitor not available")

async def cmd_healthlog(u, c):
    try:
        from auto_tuner import get_tune_log
        lines = get_tune_log(10)
        if not lines:
            await u.message.reply_text("No auto-tune actions yet.")
        else:
            await u.message.reply_text("Recent auto-tune log:\n" + "\n".join(lines[-10:]))
    except ImportError:
        await u.message.reply_text("auto_tuner not available")


def main():
    app = ApplicationBuilder().token(TOKEN).build()
    for cmd, func in [
        ("start", cmd_start), ("status", cmd_status),
        ("clearhistory", cmd_clearhistory), ("reset", cmd_reset),
        ("forget", cmd_forget), ("test", cmd_test),
        ("pipeline", cmd_pipeline), ("time", cmd_time),
        ("mood", cmd_mood), ("weather", cmd_weather),
        ("search", cmd_search), ("exec", cmd_exec),
        ("remind", cmd_remind), ("reminders", cmd_reminders),
        ("forgetreminder", cmd_forgetreminder),
        ("proactivity", cmd_proactivity), ("help", cmd_help),
        ("diagnose", cmd_diagnose), ("reflect", cmd_reflect),
        ("curiosity", cmd_curiosity), ("healthlog", cmd_healthlog),
    ]:
        app.add_handler(CommandHandler(cmd, func))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, on_message))

    async def touch(update, ctx):
        idle_sleep.record_activity()
    app.add_handler(TypeHandler(Update, touch), group=-1)

    q = queue.Queue()
    scheduler.start_scheduler(lambda cid, txt: q.put((cid, txt)))
    idle_sleep.start_idle_monitor()

    async def drain(context):
        while not q.empty():
            cid, txt = q.get_nowait()
            try:
                await context.bot.send_message(chat_id=cid, text=txt)
            except Exception:
                pass

    if app.job_queue:
        app.job_queue.run_repeating(drain, interval=5, first=5)
    else:
        async def _drain_loop(bot):
            while True:
                while not q.empty():
                    cid, txt = q.get_nowait()
                    try:
                        await bot.send_message(chat_id=cid, text=txt)
                    except Exception:
                        pass
                await asyncio.sleep(5)
        app.post_init = lambda a: asyncio.create_task(_drain_loop(a.bot))

    memory_llm.start_consolidation_loop()
    try:
        reflection_engine.start_reflection_loop()
    except Exception:
        pass
    try:
        start_diagnostic_loop()
    except Exception:
        pass
    log.info(f"NERMANA v{VERSION} started")
    app.run_polling()


if __name__ == "__main__":
    main()
