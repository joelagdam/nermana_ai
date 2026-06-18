#!/usr/bin/env python3
# FIX-1 (v4.5.2): sys import
import sys
import re, json, subprocess, shutil, socket, time as _time, os, threading
from pathlib import Path
from flask import Flask, jsonify, request, Response

# sys.path must be set before any local imports
sys.path.insert(0, str(Path.home() / "nermana" / "modules"))
sys.path.insert(0, str(Path.home() / "nermana" / "bot"))
from prompt_builder import build_system_prompt, should_force_search
try:
    import nermana_memory_llm as _memory_llm
    _HAS_MEMORY_LLM = True
except Exception:
    _HAS_MEMORY_LLM = False
try:
    import nermana_primer as _primer
    _HAS_PRIMER = True
except Exception:
    _HAS_PRIMER = False

BASE        = Path.home() / "nermana"
CONFIG_FILE = BASE / ".config"
app         = Flask(__name__)

import mood as _mood_mod
import scheduler as _sched_mod

def _read_cfg():
    cfg = {}
    if CONFIG_FILE.exists():
        for line in CONFIG_FILE.read_text().splitlines():
            if "=" in line and not line.startswith("#"):
                k, v = line.split("=", 1)
                cfg[k.strip()] = v.strip().strip('"').strip("'")
    return cfg

def _write_cfg(key, val):
    if not CONFIG_FILE.exists():
        return
    content = CONFIG_FILE.read_text()
    if re.search(rf'^{key}=', content, re.M):
        content = re.sub(rf'^{key}=.*', f'{key}={val}', content, flags=re.M)
    else:
        content += f"\n{key}={val}"
    CONFIG_FILE.write_text(content)

def _get_pipeline_events(n=100):
    logf = BASE / "logs" / "pipeline.jsonl"
    if not logf.exists():
        return []
    lines = logf.read_text(encoding='utf-8').splitlines()
    return [json.loads(l) for l in lines[-n:] if l.strip()]

def _memory_stats():
    def count(p):
        if not p.exists():
            return 0
        return sum(1 for f in p.glob("*.txt") for _ in open(f, encoding="utf-8"))
    return {
        "long_term":  count(BASE / "memory/long_term"),
        "short_term": count(BASE / "memory/short_term"),
        "junk":       count(BASE / "memory/junk"),
        "buffer":     count(BASE / "memory/buffer"),
    }

def _run(cmd):
    try:
        r = subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=15)
        return (r.stdout + r.stderr).strip()[-500:]
    except:
        return ""

def _is_valid_gguf(path):
    if not path.exists() or path.stat().st_size == 0:
        return False
    try:
        with open(path, 'rb') as f:
            return f.read(4) == b'GGUF'
    except:
        return False

def _fm_resolve(path):
    """Resolve a path relative to BASE, preventing traversal. Returns Path or None."""
    if not path:
        target = BASE
    else:
        target = (BASE / path).resolve()
    try:
        target.relative_to(BASE)
    except ValueError:
        return None
    return target

def _fm_stat(p: Path):
    s = p.stat()
    return {
        "name": p.name, "is_dir": p.is_dir(),
        "size": s.st_size if p.is_file() else None,
        "modified": s.st_mtime, "mode": s.st_mode,
    }

# ── WebUI conversation state ──────────────────────────────────────
# _web_history is the source of truth for what NERMANA knows this session.
# Tool exchanges are stored here as proper message turns (see _commit_tool_exchange).
_web_history      = []
_web_history_lock = threading.Lock()

def _is_online():
    try:
        socket.create_connection(("8.8.8.8", 53), timeout=1)
        return True
    except:
        return False

def _llm_alive():
    cfg  = _read_cfg()
    host = cfg.get("LLAMA_HOST", "127.0.0.1")
    port = cfg.get("LLAMA_PORT", "8080")
    try:
        import requests as _req
        return _req.get(f"http://{host}:{port}/health", timeout=2).status_code == 200
    except:
        return False

def _write_pipeline_event(stage, data):
    try:
        logf = BASE / "logs" / "pipeline.jsonl"
        logf.parent.mkdir(parents=True, exist_ok=True)
        event = {
            "stage": stage, "data": data,
            "ts": _time.time(), "ts_human": _time.strftime("%H:%M:%S"),
            "source": "webui"
        }
        with open(logf, "a") as f:
            f.write(json.dumps(event) + "\n")
    except:
        pass

def _commit_tool_exchange(user_msg, tool_kind, tool_query, tool_result, final_reply):
    """
    ROOT FIX: Write a complete tool exchange into _web_history so every
    future message sees what was searched / run and what NERMANA concluded.

    History shape written:
      [assistant] "Searching for: <query>"          ← tool intent
      [user]      "[Search: <query>]\n<results>"    ← tool result (as user turn)
      [assistant] "<synthesized answer>"            ← NERMANA's conclusion
    """
    with _web_history_lock:
        _web_history.append({
            "role": "assistant",
            "content": f"Searching for: {tool_query}" if tool_kind == "search"
                       else f"Running: {tool_query}"
        })
        _web_history.append({
            "role": "user",
            "content": f"[{tool_kind.capitalize()}: {tool_query}]\n{tool_result[:1200]}"
        })
        _web_history.append({
            "role": "assistant",
            "content": final_reply
        })
    # Also write to disk buffer so bot-side memory sees it
    _store_webui_to_buffer(user_msg, final_reply)

def _store_facts_from_search(query, result_str):
    """FIX-7: Extract and store facts from search snippets (mirrors bot's _extract_search_facts)."""
    try:
        from memory_engine import store_memory
        import re as _re
        # Split result text into rough sentences and store the first few plausible ones
        sentences = _re.split(r'(?<=[.!?])\s+', result_str)
        stored = 0
        for sent in sentences:
            sent = sent.strip()
            if (len(sent) > 30 and
                _re.match(r'^[A-Z0-9]', sent) and
                not _re.search(r'(click here|read more|cookie|privacy|javascript|http)', sent, _re.I)):
                fact = f"[F] search:{query[:30]} — {sent[:70]} [5]"
                store_memory(fact, 5)
                stored += 1
                if stored >= 3:
                    break
    except:
        pass

def _build_webui_system(user_msg="", tool_ctx="", online=True):
    """Shim: delegates to unified prompt_builder.build_system_prompt()"""
    memory = {}
    if _HAS_PRIMER and user_msg:
        try:
            _, memory = _primer.run(user_msg)
        except Exception:
            pass
    try:
        import mood as _m
        mood_line = _m.get_mood_line()
    except Exception:
        mood_line = ""
    return build_system_prompt(
        memory      = memory,
        tool_result = tool_ctx,
        online      = online,
        mood_line   = mood_line,
        allow_tools = not bool(tool_ctx),
    )

def _offline_search_local(query):
    words = set(re.findall(r'[a-zA-Z0-9_]{3,}', query.lower()))
    words -= {"the","and","for","you","this","with","are","from"}
    results = []
    seen = set()
    for d in [BASE/"memory"/"long_term", BASE/"memory"/"short_term", BASE/"knowledge"]:
        if not d.exists():
            continue
        for f in d.glob("*.txt"):
            try:
                for line in f.read_text(encoding="utf-8").splitlines():
                    line = line.strip()
                    if not line or line in seen:
                        continue
                    if any(w in line.lower() for w in words):
                        results.append(line)
                        seen.add(line)
                    if len(results) >= 4:
                        break
            except:
                pass
        if len(results) >= 4:
            break
    if results:
        return "Local memory results:\n" + "\n".join(f"• {r[:150]}" for r in results)
    return f"No results found for: {query} (offline — local memory also empty)"

def _do_search(query, online):
    if online:
        if shutil.which("ddgr"):
            try:
                out = subprocess.run(
                    ["ddgr", "--json", "-n", "3", "--noprompt", query],
                    capture_output=True, text=True, timeout=20)
                if out.returncode == 0 and out.stdout.strip():
                    items = json.loads(out.stdout)
                    lines = [f"{i+1}. {r.get('title','')} — {r.get('url','')}\n   {r.get('abstract','')[:120]}"
                             for i, r in enumerate(items[:3])]
                    return "Web results:\n" + "\n".join(lines), "web"
            except:
                pass
        try:
            import requests as _req
            r = _req.post("https://lite.duckduckgo.com/lite/", data={"q": query},
                          timeout=15, headers={"User-Agent": "Mozilla/5.0 (Linux; Termux)"})
            if r.status_code == 200:
                links = re.findall(r'<a[^>]+class="result-link"[^>]*href="([^"]+)"[^>]*>(.*?)</a>', r.text, re.S)
                snips = re.findall(r'<td[^>]+class="result-snippet"[^>]*>(.*?)</td>', r.text, re.S)
                items = []
                for i, (url, title) in enumerate(links[:3]):
                    tc = re.sub(r"<[^>]+>", "", title).strip()
                    sc = re.sub(r"<[^>]+>", "", snips[i] if i < len(snips) else "").strip()
                    items.append(f"{i+1}. {tc} — {url}\n   {sc[:120]}")
                if items:
                    return "Web results:\n" + "\n".join(items), "web"
        except:
            pass
    return _offline_search_local(query), "offline"

def _do_exec(cmd):
    cfg = _read_cfg()
    whitelist = [c.strip() for c in cfg.get(
        "EXEC_WHITELIST",
        "ls,pwd,df,du,whoami,uptime,date,termux-battery-status,termux-wifi-connectioninfo,free,uname"
    ).split(",")]
    cmd = cmd.strip()
    if not cmd:
        return "Empty command."
    if re.search(r'[;&|`$><\n]', cmd):
        return "Rejected: shell metacharacters not allowed."
    if cmd.split()[0] not in whitelist:
        return f"Not allowed. Whitelist: {', '.join(whitelist)}"
    try:
        r = subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=15)
        return (r.stdout + r.stderr).strip()[:1200] or "(no output)"
    except subprocess.TimeoutExpired:
        return "Timed out."
    except Exception as e:
        return f"Error: {e}"

def _offline_fallback(msg):
    from datetime import datetime
    low = msg.lower().strip()
    if re.search(r'\b(time|date|day|today|now|clock)\b', low):
        return datetime.now().strftime("It's %I:%M %p on %A, %B %d, %Y.") + " (LLM offline — system clock.)"
    if re.match(r'^(hi|hello|hey|sup|yo)[.!?]?$', low):
        return "Hey. LLM server is offline. Run: nermana start"
    local = _offline_search_local(msg)
    if "No results" not in local:
        return f"LLM offline. From local memory:\n{local}"
    return "LLM server is offline. Start it: nermana start\nI can still answer time/date and search local memory."

def _llm_stream_tokens(messages, system, cfg):
    import requests as _req
    host = cfg.get("LLAMA_HOST", "127.0.0.1")
    port = cfg.get("LLAMA_PORT", "8080")
    base = f"http://{host}:{port}"
    full_msgs = [{"role": "system", "content": system}] + messages
    payload = {
        "messages":       full_msgs,
        "max_tokens":     int(cfg.get("MAIN_MAX_TOKENS", "400")),
        "temperature":    float(cfg.get("TEMPERATURE", "0.7")),
        "repeat_penalty": float(cfg.get("REPETITION_PENALTY", "1.15")),
        "stream": True,
    }
    for path in ("/v1/chat/completions", "/chat/completions"):
        try:
            with _req.post(f"{base}{path}", json=payload, stream=True, timeout=120) as r:
                if r.status_code == 404:
                    continue
                for line in r.iter_lines():
                    if not line:
                        continue
                    line = line.decode() if isinstance(line, bytes) else line
                    if line.startswith("data:"):
                        line = line[5:].strip()
                    if line == "[DONE]":
                        return
                    try:
                        chunk = json.loads(line)
                        text = chunk.get("choices", [{}])[0].get("delta", {}).get("content", "")
                        if text:
                            yield text
                    except:
                        pass
            return
        except:
            continue

_TOOL_RE = re.compile(r'^\s*TOOL:\s*(search|exec)\s+(.+)', re.I)

@app.route('/api/chat', methods=['POST'])
def api_chat():
    data     = request.json or {}
    user_msg = data.get("message", "").strip()
    if not user_msg:
        return jsonify({"error": "empty"}), 400

    cfg    = _read_cfg()
    online = _is_online()

    # Snapshot history for this request BEFORE appending the new user message
    with _web_history_lock:
        _web_history.append({"role": "user", "content": user_msg})
        # ctx_messages: everything except the just-appended user message
        ctx_messages = list(_web_history[-14:-1])

    _write_pipeline_event("USER", {"message": user_msg[:200], "source": "webui"})

    def _generate():
        # ── LLM offline fallback ──────────────────────────────────
        if not _llm_alive():
            resp = _offline_fallback(user_msg)
            with _web_history_lock:
                _web_history.append({"role": "assistant", "content": resp})
            _write_pipeline_event("MAIN_LLM", {"response": resp[:200], "offline": True})
            yield f"data: {json.dumps({'text': resp, 'done': True, 'offline': True})}\n\n"
            return

        # PROBLEM 2: force-search pre-flight for recent/time-sensitive queries
        _force_q = should_force_search(user_msg) if online else None
        if _force_q:
            _write_pipeline_event("FORCE_SEARCH", {"query": _force_q})
            yield f'data: {json.dumps({"tool": "search", "query": _force_q})}\n\n'
            _result_str, _src = _do_search(_force_q, online)
            _tool_ctx = f"Search [{_src}] for '{_force_q}':\n{_result_str}"
            import threading as _t
            _t.Thread(
                target=_store_facts_from_search,
                args=(_force_q, _result_str), daemon=True
            ).start()
            _commit_tool_exchange(
                user_msg   = user_msg,
                tool_kind  = "search",
                tool_query = _force_q,
                tool_result = _result_str,
                final_reply = "",  # filled in below
            )
            # Second pass: synthesize from real search data
            system2  = _build_webui_system(user_msg=user_msg, tool_ctx=_tool_ctx, online=online)
            resp2    = ""
            for _chunk in _llm_stream_tokens(messages, system2, cfg):
                resp2 += _chunk
                yield f'data: {json.dumps({"text": _chunk})}\n\n'
            _clean2 = re.sub(r'\*[^*]+\*', '', resp2).strip()
            # Update the placeholder history entry with the actual reply
            with _web_history_lock:
                if _web_history and _web_history[-1]["role"] == "assistant" and not _web_history[-1]["content"]:
                    _web_history[-1]["content"] = _clean2
            _write_pipeline_event("MAIN_LLM", {"response": _clean2[:200], "via": "force_search"})
            yield f'data: {json.dumps({"done": True})}\n\n'
            return

        # Normal path
        system   = _build_webui_system(user_msg=user_msg, online=online)
        messages = ctx_messages + [{"role": "user", "content": user_msg}]

        full  = ""
        state = "buffering"

        for chunk in _llm_stream_tokens(messages, system, cfg):
            full += chunk
            if state == "buffering":
                stripped = full.lstrip()
                if "\n" not in stripped:
                    upper = stripped.upper()
                    if "TOOL:".startswith(upper) or upper.startswith("TOOL:"):
                        continue
                    state = "streaming"
                else:
                    first_line = stripped.split("\n", 1)[0]
                    if _TOOL_RE.match(first_line):
                        state = "directive"
                        continue
                    state = "streaming"

            if state == "streaming":
                yield f"data: {json.dumps({'text': chunk})}\n\n"

        # ── Post-stream ───────────────────────────────────────────
        if state == "directive":
            first_line = full.strip().split("\n", 1)[0]
            m = _TOOL_RE.match(first_line)
            if m:
                kind        = m.group(1).lower()
                payload_str = m.group(2).strip()[:200]

                if kind == "search":
                    yield f"data: {json.dumps({'tool': 'search', 'query': payload_str})}\n\n"
                    _write_pipeline_event("TOOL", {"kind": "search", "query": payload_str})
                    result_str, src = _do_search(payload_str, online)
                    tool_ctx = f"Search [{src}] for '{payload_str}':\n{result_str}"

                    # FIX-7: store search facts to memory
                    threading.Thread(
                        target=_store_facts_from_search,
                        args=(payload_str, result_str), daemon=True
                    ).start()

                elif kind == "exec":
                    yield f"data: {json.dumps({'tool': 'exec', 'cmd': payload_str})}\n\n"
                    _write_pipeline_event("TOOL", {"kind": "exec", "cmd": payload_str})
                    out = _do_exec(payload_str)
                    result_str = out
                    tool_ctx   = f"$ {payload_str}\n{out}"
                else:
                    result_str = f"Unknown tool: {payload_str}"
                    tool_ctx   = result_str

                # Second pass: LLM synthesizes the tool result
                # The context for this pass includes the ORIGINAL ctx_messages
                # (the user's question is already last in ctx_messages via _web_history)
                # FIX-8: pass user_msg explicitly
                system2 = _build_webui_system(user_msg=user_msg, tool_ctx=tool_ctx, online=online)
                resp2   = ""
                for chunk2 in _llm_stream_tokens(messages, system2, cfg):
                    resp2 += chunk2
                    if not re.match(r'^\s*TOOL:', resp2):
                        yield f"data: {json.dumps({'text': chunk2})}\n\n"

                clean2 = re.sub(r'\*[^*]+\*', '', resp2).strip()

                # ROOT FIX: commit the full tool exchange to _web_history
                # so follow-up questions see what was searched and concluded.
                _commit_tool_exchange(
                    user_msg   = user_msg,
                    tool_kind  = kind,
                    tool_query = payload_str,
                    tool_result = result_str,
                    final_reply = clean2,
                )
                _write_pipeline_event("MAIN_LLM", {"response": clean2[:200], "via_tool": kind})
                yield f"data: {json.dumps({'done': True})}\n\n"
                return

        # ── Normal reply ──────────────────────────────────────────
        clean = re.sub(r'\*[^*]+\*', '', full).strip()
        if not clean:
            clean = _offline_fallback(user_msg)
        with _web_history_lock:
            _web_history.append({"role": "assistant", "content": clean})
        _write_pipeline_event("MAIN_LLM", {"response": clean[:200]})
        _store_webui_to_buffer(user_msg, clean)
        # Always evaluate normal replies for memory storage
        if _HAS_MEMORY_LLM:
            import threading as _t
            _t.Thread(target=lambda: _memory_llm.run(user_msg, clean), daemon=True).start()
        yield f"data: {json.dumps({'done': True})}\n\n"

    return Response(_generate(), mimetype="text/event-stream",
                    headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})

def _store_webui_to_buffer(user_msg, bot_reply):
    try:
        buf_file = BASE / "memory" / "buffer" / "current.jsonl"
        buf_file.parent.mkdir(parents=True, exist_ok=True)
        with open(buf_file, "a") as f:
            f.write(json.dumps({
                "user": user_msg[:200], "bot": bot_reply[:400],
                "ts": _time.time(), "source": "webui"
            }) + "\n")
    except:
        pass

@app.route('/api/chat/history')
def api_chat_history():
    with _web_history_lock:
        return jsonify({"history": list(_web_history[-40:])})

@app.route('/api/chat/clear', methods=['POST'])
def api_chat_clear():
    with _web_history_lock:
        _web_history.clear()
    return jsonify({"status": "ok"})

# ── File Manager (full CRUD over ~/nermana/) ────────────────

def _fm_args():
    data = request.json or {}
    return _fm_resolve(data.get("path", "")), data.get("path", "")

def _fm_err(msg, code=400):
    return jsonify({"error": msg}), code

@app.route("/api/files")
def api_files_list():
    rel = request.args.get("path", "")
    target = _fm_resolve(rel)
    if not target or not target.exists():
        return _fm_err("not found", 404)
    if target.is_dir():
        entries = []
        for p in sorted(target.iterdir()):
            s = p.stat()
            entries.append({
                "name": p.name, "is_dir": p.is_dir(),
                "size": s.st_size if p.is_file() else None,
                "modified": int(s.st_mtime),
            })
        return jsonify({"path": rel, "is_dir": True, "entries": entries, "base": str(BASE)})
    else:
        is_binary = False
        try:
            text = target.read_text(encoding="utf-8", errors="strict")[:50000]
        except (UnicodeDecodeError, ValueError):
            is_binary = True
            text = None
        s = target.stat()
        return jsonify({
            "path": rel, "is_dir": False, "is_binary": is_binary,
            "content": text, "size": s.st_size, "modified": int(s.st_mtime),
            "base": str(BASE),
        })

@app.route("/api/files/read", methods=["POST"])
def api_files_read():
    target, rel = _fm_args()
    if not target or not target.is_file():
        return _fm_err("not found", 404)
    try:
        return jsonify({"content": target.read_text(encoding="utf-8")})
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route("/api/files/save", methods=["POST"])
def api_files_save():
    data = request.json or {}
    target = _fm_resolve(data.get("path", ""))
    if not target:
        return _fm_err("invalid path")
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(data.get("content", ""), encoding="utf-8")
        return jsonify({"status": "ok", "path": data.get("path", "")})
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route("/api/files/create", methods=["POST"])
def api_files_create():
    data = request.json or {}
    path = data.get("path", "").strip()
    kind = data.get("kind", "file")
    if not path:
        return _fm_err("path required")
    target = _fm_resolve(path)
    if not target:
        return _fm_err("invalid path")
    if target.exists():
        return _fm_err("already exists", 409)
    try:
        if kind == "dir":
            target.mkdir(parents=True)
        else:
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(data.get("content", ""), encoding="utf-8")
        return jsonify({"status": "ok", "path": path, "is_dir": kind == "dir"})
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route("/api/files/rename", methods=["POST"])
def api_files_rename():
    data = request.json or {}
    target, _ = _fm_args()
    if not target or not target.exists():
        return _fm_err("not found", 404)
    new_name = data.get("name", "").strip()
    if not new_name:
        return _fm_err("new name required")
    new_path = target.parent / new_name
    if new_path.exists():
        return _fm_err("target already exists", 409)
    try:
        target.rename(new_path)
        return jsonify({"status": "ok", "path": str(new_path.relative_to(BASE))})
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route("/api/files/move", methods=["POST"])
def api_files_move():
    data = request.json or {}
    src = _fm_resolve(data.get("path", ""))
    dst = _fm_resolve(data.get("dest", ""))
    if not src or not src.exists():
        return _fm_err("source not found", 404)
    if not dst:
        return _fm_err("invalid destination")
    if dst.exists() and dst.is_dir():
        dst = dst / src.name
    if dst.exists():
        return _fm_err("destination already exists", 409)
    try:
        src.rename(dst)
        return jsonify({"status": "ok", "path": str(dst.relative_to(BASE))})
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route("/api/files/delete", methods=["POST"])
def api_files_delete():
    data = request.json or {}
    path = data.get("path", "")
    target = _fm_resolve(path)
    if not target or not target.exists():
        return _fm_err("not found", 404)
    try:
        if target.is_dir():
            import shutil
            shutil.rmtree(target)
        else:
            target.unlink()
        return jsonify({"status": "ok", "path": path})
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route("/api/files/info")
def api_files_info():
    rel = request.args.get("path", "")
    target = _fm_resolve(rel)
    if not target or not target.exists():
        return _fm_err("not found", 404)
    s = target.stat()
    return jsonify({
        "path": rel, "name": target.name, "is_dir": target.is_dir(),
        "size": s.st_size, "modified": int(s.st_mtime), "mode": oct(s.st_mode),
        "base": str(BASE),
    })

@app.route("/api/files/upload", methods=["POST"])
def api_files_upload():
    if "file" not in request.files:
        return _fm_err("no file provided")
    file = request.files["file"]
    dest_path = request.form.get("path", file.filename or "")
    target = _fm_resolve(dest_path)
    if not target:
        return _fm_err("invalid path")
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        file.save(target)
        return jsonify({"status": "ok", "path": dest_path, "size": target.stat().st_size})
    except Exception as e:
        return jsonify({"error": str(e)}), 500

# ── Dashboard ────────────────────────────────────────────────

@app.route("/api/dashboard")
def api_dashboard():
    mem = _memory_stats()
    cfg = _read_cfg()
    host = cfg.get("LLAMA_HOST", "127.0.0.1")
    port = cfg.get("LLAMA_PORT", "8080")
    llm_ok = False
    try:
        import requests
        llm_ok = requests.get(f"http://{host}:{port}/health", timeout=2).status_code == 200
    except:
        pass
    bot_ok = (BASE / ".bot.pid").exists()
    # file counts
    total_files = 0
    total_dirs = 0
    for root, dirs, files in os.walk(BASE):
        if ".git" in root or "__pycache__" in root:
            continue
        total_dirs += len(dirs)
        total_files += len(files)
    # recent pipeline events
    events = _get_pipeline_events(20)
    return jsonify({
        "llm": llm_ok, "bot": bot_ok,
        "memory": mem,
        "total_files": total_files, "total_dirs": total_dirs,
        "recent_events": events,
    })

@app.route('/api/pipeline_log')
def api_pipeline():
    return jsonify({"events": _get_pipeline_events(int(request.args.get('n', 100)))})

@app.route('/api/pipeline_stream')
def api_pipeline_stream():
    logf = BASE / "logs" / "pipeline.jsonl"
    def gen():
        pos = 0
        while True:
            if logf.exists():
                sz = logf.stat().st_size
                if sz > pos:
                    with open(logf, 'rb') as f:
                        f.seek(pos)
                        new = f.read(sz - pos).decode(errors='replace')
                        pos = sz
                        for line in new.splitlines():
                            if line.strip():
                                yield f"data: {line}\n\n"
            _time.sleep(1)
    return Response(gen(), mimetype="text/event-stream")

@app.route('/api/memory_stats')
def api_mem_stats():
    return jsonify(_memory_stats())

@app.route('/api/settings', methods=['GET', 'POST'])
def api_settings():
    if request.method == 'GET':
        cfg = _read_cfg()
        return jsonify({
            "temperature":       float(cfg.get("TEMPERATURE", "0.7")),
            "repeat_penalty":    float(cfg.get("REPETITION_PENALTY", "1.15")),
            "main_max_tokens":   int(cfg.get("MAIN_MAX_TOKENS", "400")),
            "memory_max_tokens": int(cfg.get("MEMORY_MAX_TOKENS", "150")),
            "mem_eval_interval": int(cfg.get("MEMORY_EVAL_INTERVAL", "5")),
            "buffer_window":     int(cfg.get("BUFFER_WINDOW", "20")),
            "lt_score_min":      int(cfg.get("LONG_TERM_SCORE_MIN", "7")),
            "default_city":      cfg.get("DEFAULT_CITY", "Davao City"),
            "proactivity_level": int(cfg.get("PROACTIVITY_LEVEL", "1")),
            "exec_whitelist":    cfg.get("EXEC_WHITELIST", ""),
            "search_results":    int(cfg.get("SEARCH_RESULTS", "3")),
            "idle_sleep_minutes": int(cfg.get("IDLE_SLEEP_MINUTES", "15")),
            "active_model":      cfg.get("ACTIVE_MODEL", "unknown"),
            "semantic_enabled":  cfg.get("SEMANTIC_MEMORY_ENABLED", "true") == "true",
        })
    else:
        data = request.json
        mapping = {
            "temperature": "TEMPERATURE", "repeat_penalty": "REPETITION_PENALTY",
            "main_max_tokens": "MAIN_MAX_TOKENS", "memory_max_tokens": "MEMORY_MAX_TOKENS",
            "mem_eval_interval": "MEMORY_EVAL_INTERVAL", "buffer_window": "BUFFER_WINDOW",
            "lt_score_min": "LONG_TERM_SCORE_MIN", "default_city": "DEFAULT_CITY",
            "proactivity_level": "PROACTIVITY_LEVEL", "exec_whitelist": "EXEC_WHITELIST",
            "search_results": "SEARCH_RESULTS", "idle_sleep_minutes": "IDLE_SLEEP_MINUTES",
            "semantic_enabled": "SEMANTIC_MEMORY_ENABLED",
        }
        for k, v in mapping.items():
            if k in data:
                _write_cfg(v, str(data[k]))
        _run("nermana restart")
        return jsonify({"status": "ok"})

@app.route('/api/mood')
def api_mood():
    m = _mood_mod.get_mood()
    return jsonify({"label": m.get("label", "neutral"), "line": _mood_mod.get_mood_line()})

@app.route('/api/reminders')
def api_reminders():
    return jsonify({"reminders": _sched_mod.list_reminders()})

@app.route('/api/models')
def api_models():
    MODEL_DIR = BASE / "models"
    PRESETS = [
        {"name": "Phi-3.5-mini",  "file": "phi-3.5-mini-instruct-Q4_K_M.gguf",  "size": "~2.5GB"},
        {"name": "Qwen2.5-3B",    "file": "qwen2.5-3b-instruct-Q4_K_M.gguf",    "size": "~2.0GB"},
        {"name": "SmolLM2-1.7B",  "file": "smollm2-1.7b-instruct-Q4_K_M.gguf", "size": "~1.0GB"},
    ]
    cfg    = _read_cfg()
    active = cfg.get("LLAMA_MODEL_PATH", "")
    models = []
    for p in PRESETS:
        path = MODEL_DIR / p["file"]
        models.append({
            **p,
            "present": path.exists(),
            "valid":   _is_valid_gguf(path) if path.exists() else False,
            "active":  str(path) == active,
        })
    for f in MODEL_DIR.glob("*.gguf"):
        if any(f.name == m["file"] for m in PRESETS):
            continue
        models.append({
            "name":    f.stem.replace("_", " "),
            "file":    f.name,
            "size":    f"~{f.stat().st_size // (1024**2)}MB",
            "present": True,
            "valid":   _is_valid_gguf(f),
            "active":  str(f) == active,
            "custom":  True,
        })
    return jsonify({"models": models, "active_path": active})

_dl_state = {"active": False, "file": "", "progress": "", "error": ""}

@app.route('/api/models/download', methods=['POST'])
def api_model_download():
    data = request.json
    url  = data.get('url')
    file = data.get('file')
    if not url or not file:
        return jsonify({"error": "missing"}), 400
    if _dl_state["active"]:
        return jsonify({"error": "busy"}), 409
    dest = BASE / "models" / file
    def _dl():
        _dl_state.update({"active": True, "file": file, "progress": "starting", "error": ""})
        try:
            proc = subprocess.Popen(
                ["wget", "-c", "--progress=dot:giga", url, "-O", str(dest)],
                stderr=subprocess.PIPE, text=True)
            for line in proc.stderr:
                if line:
                    _dl_state["progress"] = line.strip()[-120:]
            proc.wait()
            _dl_state["progress"] = "done" if proc.returncode == 0 else ""
            if proc.returncode != 0:
                _dl_state["error"] = f"wget error {proc.returncode}"
        except Exception as e:
            _dl_state["error"] = str(e)
        finally:
            _dl_state["active"] = False
    threading.Thread(target=_dl, daemon=True).start()
    return jsonify({"status": "started"})

@app.route('/api/models/download_progress')
def api_dl_progress():
    return jsonify(_dl_state)

@app.route('/api/models/switch', methods=['POST'])
def api_model_switch():
    data     = request.json
    filename = data.get('file')
    name     = data.get('name', filename)
    if not filename:
        return jsonify({"error": "no file"}), 400
    path = BASE / "models" / filename
    if not path.exists():
        return jsonify({"error": "not found"}), 404
    if not _is_valid_gguf(path):
        return jsonify({"error": "corrupted GGUF"}), 400
    _write_cfg("LLAMA_MODEL_PATH", str(path))
    _write_cfg("ACTIVE_MODEL", name)
    _run("nermana stop-server")
    _time.sleep(1)
    _run("nermana start-server")
    return jsonify({"status": "ok"})

@app.route('/api/bot_control', methods=['POST'])
def api_bot_control():
    action = request.json.get('action', '')
    out    = _run(f"nermana {action}")
    return jsonify({"status": "ok", "output": out})

@app.route('/api/bot_log')
def api_bot_log():
    logf = BASE / "logs" / "bot.log"
    if not logf.exists():
        return jsonify({"lines": []})
    return jsonify({"lines": logf.read_text(encoding='utf-8', errors='replace').splitlines()[-100:]})

@app.route('/api/status')
def api_status():
    cfg    = _read_cfg()
    host   = cfg.get("LLAMA_HOST", "127.0.0.1")
    port   = cfg.get("LLAMA_PORT", "8080")
    llm_ok = False
    try:
        import requests
        llm_ok = requests.get(f"http://{host}:{port}/health", timeout=2).status_code == 200
    except:
        pass
    bot_ok = (BASE / ".bot.pid").exists()
    return jsonify({"llm_server": llm_ok, "bot": bot_ok, "sleeping": not llm_ok and bot_ok})

# ── v4.6.0: Diagnostic & Self-Learning endpoints ─────────────────

@app.route("/api/diagnostics")
def api_diagnostics():
    try:
        from diagnostics import get_latest, run_and_persist
        cached = get_latest()
        if not cached:
            cached = _run(lambda: run_and_persist())
        return jsonify(cached)
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route("/api/diagnose_now", methods=["POST"])
def api_diagnose_now():
    try:
        from diagnostics import run_and_persist
        r = run_and_persist()
        from dataclasses import asdict
        return jsonify(asdict(r))
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route("/api/health_history")
def api_health_history():
    days = int(request.args.get("days", 7))
    try:
        from diagnostics import get_history
        return jsonify({"history": get_history(days=days)})
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route("/api/reflection_log")
def api_reflection_log():
    n = int(request.args.get("n", 10))
    try:
        from reflection_engine import get_reflection_log
        return jsonify({"reflections": get_reflection_log(n=n)})
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route("/api/reflect_now", methods=["POST"])
def api_reflect_now():
    try:
        from reflection_engine import run_now
        import threading as _t
        result = {}
        def _run():
            nonlocal result
            result = run_now(quality_trigger=True)
        t = _t.Thread(target=_run, daemon=True)
        t.start()
        t.join(timeout=60)
        return jsonify(result if result else {"status": "running"})
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route("/api/curiosity_queue")
def api_curiosity_queue():
    try:
        from nermana_self_monitor import get_curiosity_queue
        return jsonify({"queue": get_curiosity_queue()})
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route("/api/contradictions")
def api_contradictions():
    n = int(request.args.get("n", 20))
    try:
        from reflection_engine import get_contradictions
        return jsonify({"contradictions": get_contradictions(n=n)})
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route("/api/quality_scores")
def api_quality_scores():
    n = int(request.args.get("n", 20))
    try:
        from nermana_self_monitor import get_recent_quality
        return jsonify({"scores": get_recent_quality(n=n)})
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route("/api/auto_tune_log")
def api_auto_tune_log():
    try:
        from auto_tuner import get_tune_log
        return jsonify({"log": get_tune_log(50)})
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route('/')
def index():
    return Response(open(Path(__file__).parent / "index.html").read(), mimetype="text/html")

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=False, threaded=True)
