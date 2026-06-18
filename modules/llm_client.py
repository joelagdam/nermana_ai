import requests, logging, time, threading, json
from pathlib import Path
from collections import deque

log = logging.getLogger("llm_client")

# E: Priority semaphore — main LLM calls use the primary slot,
# background tasks (memory eval, consolidation, hedging search) use the
# secondary slot. A main reply is never delayed by a background eval.
# Implementation: two tokens in the semaphore, but main calls always
# drain/refill via a priority event instead of a plain acquire().
import threading as _threading

_main_lock = _threading.Lock()       # primary slot: only one main call
_bg_lock   = _threading.Lock()       # background slot: only one bg call
# _semaphore kept for backward-compat with any direct importers
_semaphore = _threading.Semaphore(1)

def _main_call_ctx():
    """Context manager: acquire main slot, also block background if it's running."""
    class _Ctx:
        def __enter__(self):
            _main_lock.acquire()
            return self
        def __exit__(self, *a):
            _main_lock.release()
    return _Ctx()

def _bg_call_ctx():
    """Context manager: wait for main slot to be free first, then run background."""
    class _Ctx:
        def __enter__(self):
            # background waits for main to finish if one is running
            _main_lock.acquire()
            _main_lock.release()
            _bg_lock.acquire()
            return self
        def __exit__(self, *a):
            _bg_lock.release()
    return _Ctx()

def _load_cfg():
    cfg = {}
    cfg_file = Path.home() / "nermana" / ".config"
    if cfg_file.exists():
        for line in cfg_file.read_text().splitlines():
            if "=" in line and not line.startswith("#"):
                k, v = line.split("=", 1)
                cfg[k.strip()] = v.strip().strip('"').strip("'")
    return cfg

CFG = _load_cfg()
LLAMA_HOST = CFG.get("LLAMA_HOST", "127.0.0.1")
LLAMA_PORT = CFG.get("LLAMA_PORT", "8080")
EMBED_HOST = CFG.get("LLAMA_HOST", "127.0.0.1")
EMBED_PORT = CFG.get("LLAMA_EMBED_PORT", "8081")
RETRY_ATTEMPTS = 2
_endpoint_cache = None
_embed_endpoint_cache = None

def get_endpoint():
    global _endpoint_cache
    if _endpoint_cache:
        return _endpoint_cache
    base = f"http://{LLAMA_HOST}:{LLAMA_PORT}"
    for path in ("/v1/chat/completions", "/chat/completions"):
        try:
            r = requests.post(f"{base}{path}", json={"messages":[{"role":"user","content":"hi"}],"max_tokens":1}, timeout=2)
            if r.status_code != 404:
                _endpoint_cache = f"{base}{path}"
                return _endpoint_cache
        except:
            pass
    return None

def get_embed_endpoint():
    global _embed_endpoint_cache
    if _embed_endpoint_cache:
        return _embed_endpoint_cache
    base = f"http://{EMBED_HOST}:{EMBED_PORT}"
    try:
        r = requests.post(f"{base}/embedding", json={"content":"test"}, timeout=2)
        if r.status_code != 404:
            _embed_endpoint_cache = f"{base}/embedding"
            return _embed_endpoint_cache
    except:
        pass
    return None

def embed(text: str, timeout: int = 15) -> list:
    """Call embedding server, return vector list."""
    ep = get_embed_endpoint()
    if not ep:
        return []
    try:
        r = requests.post(ep, json={"content": text}, timeout=timeout)
        if r.status_code == 200:
            data = r.json()
            return data.get("embedding", [])
    except Exception as e:
        log.error(f"Embedding error: {e}")
    return []

def call(messages, max_tokens=200, temperature=0.7, repeat_penalty=1.15, stop=None, system=None, _bg=False):
    """Main LLM call. Set _bg=True for background tasks (memory eval, etc.)."""
    ctx = _bg_call_ctx() if _bg else _main_call_ctx()
    with ctx:
        ep = get_endpoint()
        if not ep:
            return ""
        full_messages = []
        if system:
            full_messages.append({"role":"system","content":system})
        full_messages.extend(messages)
        payload = {
            "messages": full_messages,
            "max_tokens": max_tokens,
            "temperature": temperature,
            "repeat_penalty": repeat_penalty,
            "stream": False
        }
        if stop:
            payload["stop"] = stop
        for attempt in range(RETRY_ATTEMPTS):
            try:
                r = requests.post(ep, json=payload, timeout=120)
                if r.status_code == 200:
                    data = r.json()
                    choice = data.get("choices", [{}])[0]
                    return choice.get("message", {}).get("content", choice.get("text", "")).strip()
            except Exception as e:
                log.error(f"LLM error: {e}")
                global _endpoint_cache
                _endpoint_cache = None
                time.sleep(0.5 * (2 ** attempt))
        return ""

def call_stream(messages, max_tokens=200, temperature=0.7, repeat_penalty=1.15, stop=None, system=None):
    with _main_call_ctx():
        ep = get_endpoint()
        if not ep:
            return
        full_messages = []
        if system:
            full_messages.append({"role":"system","content":system})
        full_messages.extend(messages)
        payload = {
            "messages": full_messages,
            "max_tokens": max_tokens,
            "temperature": temperature,
            "repeat_penalty": repeat_penalty,
            "stream": True
        }
        if stop:
            payload["stop"] = stop
        try:
            with requests.post(ep, json=payload, stream=True, timeout=120) as r:
                if r.status_code == 200:
                    for line in r.iter_lines():
                        if not line:
                            continue
                        line = line.decode() if isinstance(line, bytes) else line
                        if line.startswith("data:"):
                            line = line[5:].strip()
                        if line == "[DONE]":
                            break
                        try:
                            chunk = json.loads(line)
                            delta = chunk.get("choices", [{}])[0].get("delta", {})
                            text = delta.get("content", "")
                            if text:
                                yield text
                        except:
                            pass
        except Exception as e:
            log.error(f"Stream error: {e}")
