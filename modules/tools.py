import re, json, subprocess, shutil, logging, socket
from pathlib import Path
import requests

log = logging.getLogger("tools")
_offline_mode = False

def set_offline_mode(offline):
    global _offline_mode
    _offline_mode = offline

def is_online():
    try:
        socket.create_connection(("8.8.8.8", 53), timeout=1)
        return True
    except:
        return False

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
DEFAULT_CITY = CFG.get("DEFAULT_CITY", "Tagum City")
SEARCH_RESULTS = int(CFG.get("SEARCH_RESULTS", "3"))
EXEC_WHITELIST = [c.strip() for c in CFG.get("EXEC_WHITELIST", "ls,pwd,df,du,whoami,uptime,date,termux-battery-status,termux-wifi-connectioninfo,free,uname").split(",")]

def get_weather(city=None):
    if not is_online():
        return {"ok": False, "error": "Offline – cannot fetch weather", "city": city or DEFAULT_CITY}
    city = (city or DEFAULT_CITY).strip()
    try:
        r = requests.get(f"https://wttr.in/{requests.utils.quote(city)}?format=j1", timeout=10)
        if r.status_code != 200:
            return {"ok": False, "error": f"HTTP {r.status_code}", "city": city}
        cur = r.json().get("current_condition", [{}])[0]
        return {
            "ok": True,
            "city": city,
            "temp_c": cur.get("temp_C"),
            "condition": (cur.get("weatherDesc", [{}])[0] or {}).get("value", "unknown")
        }
    except Exception as e:
        return {"ok": False, "error": str(e), "city": city}

def format_weather(w):
    if not w.get("ok"):
        return f"Can't get weather for {w.get('city', 'that city')}: {w.get('error')}"
    return f"Weather in {w['city']}: {w['condition']}, {w['temp_c']}°C"

def extract_city(text):
    m = re.search(r"weather\s+(?:in|for|at)\s+([A-Za-z][A-Za-z\s,.\-]{1,40})", text, re.I)
    if not m:
        return ""
    city = m.group(1).strip()
    city = re.sub(r"[?!.]+$", "", city).strip()
    return city[:50]

def extract_search_query(text):
    m = re.search(r"^\s*(?:hey\s+)?(?:nermana[,:]?\s*)?(?:search(?:\s+(?:for|up))?|look\s*up|google)\b[:\s]+(.{2,150})", text, re.I)
    if not m:
        return ""
    query = m.group(1).strip()
    if re.match(r"^(is|are|was|were|has|have|had|can|could|would|will|means|stands)", query, re.I):
        return ""
    return query[:150]

def web_search(query, n=None):
    if not is_online():
        return []
    n = n or SEARCH_RESULTS
    if shutil.which("ddgr"):
        try:
            out = subprocess.run(["ddgr", "--json", "-n", str(n), "--noprompt", query], capture_output=True, text=True, timeout=20)
            if out.returncode == 0 and out.stdout:
                data = json.loads(out.stdout)
                return [{"title": item.get("title", ""), "url": item.get("url", ""), "snippet": item.get("abstract", "")} for item in data[:n]]
        except:
            pass
    try:
        r = requests.post("https://lite.duckduckgo.com/lite/", data={"q": query}, timeout=15)
        if r.status_code != 200:
            return []
        html = r.text
        links = re.findall(r'<a[^>]+class="result-link"[^>]*href="([^"]+)"[^>]*>(.*?)</a>', html, re.S)
        snippets = re.findall(r'<td[^>]+class="result-snippet"[^>]*>(.*?)<table>', html, re.S)
        results = []
        for i, (url, title) in enumerate(links[:n]):
            snippet = re.sub(r"<[^>]+>", "", snippets[i] if i < len(snippets) else "").strip()
            results.append({"title": re.sub(r"<[^>]+>", "", title).strip(), "url": url, "snippet": snippet})
        return results
    except:
        return []

def _query_words(text):
    """Extract meaningful words from a query for heuristic matching."""
    import re
    STOP = {"the","and","for","you","this","that","with","from","are","was",
            "have","not","its","can","will","does","did","but","just","like",
            "what","who","when","where","why","how","is","of","in","on","at","to"}
    words = set(re.findall(r'\b[a-zA-Z0-9_]{3,}\b', text.lower()))
    return words - STOP

def format_search_results(query, results):
    if not results:
        return f"No results for: {query}"
    # Heuristic: rank results by snippet relevance to query words
    qwords = _query_words(query)
    scored = []
    for r in results:
        snippet = r.get('snippet', '').lower()
        title = r.get('title', '').lower()
        # Simple score: count of query words in snippet + title
        score = sum(1 for w in qwords if w in snippet) + sum(1 for w in qwords if w in title)
        scored.append((score, r))
    # Sort descending by score, take top 2
    scored.sort(key=lambda x: x[0], reverse=True)
    top_results = [r for _, r in scored[:2]]
    lines = [f"Top results for \"{query}\":"]
    for i, r in enumerate(top_results, 1):
        # Provide concise info: title and a short snippet
        title = r.get('title', '')[:80]
        snippet = r.get('snippet', '').strip()
        # Take first 2 sentences or up to 200 chars
        if snippet:
            # Split by sentence endings
            import re
            sentences = re.split(r'(?<=[.!?])\s+', snippet)
            snippet = ' '.join(sentences[:2])  # first two sentences
            if len(snippet) > 200:
                snippet = snippet[:200] + '...'
        lines.append(f"{i}. {title}: {snippet}")
    return "\n".join(lines)

def is_command_allowed(cmd):
    cmd = cmd.strip()
    if not cmd:
        return False
    if re.search(r'[;&|\x60$><\n]', cmd):
        return False
    return cmd.split()[0] in EXEC_WHITELIST

def run_command(cmd, timeout=15):
    if not is_command_allowed(cmd):
        return {"ok": False, "output": "", "error": "command not allowed"}
    try:
        r = subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=timeout)
        return {"ok": True, "output": (r.stdout + r.stderr).strip()[:1500], "error": ""}
    except subprocess.TimeoutExpired:
        return {"ok": False, "output": "", "error": "timeout"}
    except Exception as e:
        return {"ok": False, "output": "", "error": str(e)}
