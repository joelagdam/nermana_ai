import re, json, subprocess, shutil, logging, socket, time
from pathlib import Path
import requests

log = logging.getLogger("tools")
_offline_mode = False

# Cache for offline fallback
_WEATHER_CACHE = {}  # city -> (data, timestamp)
_WEB_SEARCH_CACHE = {}  # query -> (results, timestamp)
_CACHE_TTL = 300  # 5 minutes

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
    city = (city or DEFAULT_CITY).strip()

    # If we have a cached result and it's still fresh, return it
    if city in _WEATHER_CACHE:
        data, timestamp = _WEATHER_CACHE[city]
        if time.time() - timestamp < _CACHE_TTL:
            return data

    if not is_online():
        # Return cached data if available (even if expired), otherwise offline error
        if city in _WEATHER_CACHE:
            data, timestamp = _WEATHER_CACHE[city]
            # Mark as cached data
            data = data.copy()
            data["_cached"] = True
            data["_note"] = "Showing cached data (offline)"
            return data
        return {"ok": False, "error": "Offline – cannot fetch weather", "city": city}

    try:
        r = requests.get(f"https://wttr.in/{requests.utils.quote(city)}?format=j1", timeout=10)
        if r.status_code != 200:
            return {"ok": False, "error": f"HTTP {r.status_code}", "city": city}
        cur = r.json().get("current_condition", [{}])[0]
        result = {
            "ok": True,
            "city": city,
            "temp_c": cur.get("temp_C"),
            "condition": (cur.get("weatherDesc", [{}])[0] or {}).get("value", "unknown")
        }
        # Cache the successful result
        _WEATHER_CACHE[city] = (result, time.time())
        return result
    except Exception as e:
        # Return cached data on error if available
        if city in _WEATHER_CACHE:
            data, timestamp = _WEATHER_CACHE[city]
            data = data.copy()
            data["_cached"] = True
            data["_note"] = "Showing cached data (error)"
            return data
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
    n = n or SEARCH_RESULTS

    # If we have a cached result and it's still fresh, return it
    cache_key = f"{query}:{n}"
    if cache_key in _WEB_SEARCH_CACHE:
        results, timestamp = _WEB_SEARCH_CACHE[cache_key]
        if time.time() - timestamp < _CACHE_TTL:
            # Mark results as cached
            for r in results:
                r["_cached"] = True
            return results

    if not is_online():
        # Return cached data if available (even if expired)
        if cache_key in _WEB_SEARCH_CACHE:
            results, timestamp = _WEB_SEARCH_CACHE[cache_key]
            # Mark results as cached
            for r in results:
                r["_cached"] = True
                r["_note"] = "Showing cached results (offline)"
            return results
        return []

    # Fallback 1: duckduckgo.com lite with improved headers and retry
    for attempt in range(2):  # Try twice
        try:
            headers = {
                'User-Agent': 'Mozilla/5.0 (Linux; Android 10; K) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/114.0.0.0 Mobile Safari/537.36'
            }
            r = requests.post("https://lite.duckduckgo.com/lite/", data={"q": query}, headers=headers, timeout=15)
            if r.status_code == 200:
                html = r.text
                links = re.findall(r'<a[^>]+class="result-link"[^>]*href="([^"]+)"[^>]*>(.*?)</a>', html, re.S)
                snippets = re.findall(r'<td[^>]+class="result-snippet"[^>]*>(.*?)<table>', html, re.S)
                results = []
                for i, (url, title) in enumerate(links[:n]):
                    snippet = re.sub(r"<[^>]+>", "", snippets[i] if i < len(snippets) else "").strip()
                    results.append({"title": re.sub(r"<[^>]+>", "", title).strip(), "url": url, "snippet": snippet})
                # Cache successful results
                _WEB_SEARCH_CACHE[cache_key] = (results, time.time())
                return results
            # If we get here, status wasn't 200, try again
        except Exception:
            if attempt == 0:  # First attempt failed, wait a bit before retry
                time.sleep(1)
            continue  # Try again or fall through to fallback 2

    # Fallback 2: duckduckgo.com html (different endpoint)
    try:
        headers = {
            'User-Agent': 'Mozilla/5.0 (Linux; Android 10; K) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/114.0.0.0 Mobile Safari/537.36'
        }
        r = requests.get("https://duckduckgo.com/html/", params={"q": query}, headers=headers, timeout=15)
        if r.status_code == 200:
            html = r.text
            # DuckDuckGo HTML result parsing
            results = []
            # Look for result containers
            from html import unescape
            import re

            # Pattern for result URLs and titles in HTML version
            link_pattern = r'<a[^>]+class="result__url"[^>]*>[^<]*<a[^>]+href="([^"]+)"[^>]*>[^<]*</a>[^<]*</div>[^<]*<a[^>]+class="result__snippet"[^>]*>([^<]+)'
            matches = re.findall(link_pattern, html, re.IGNORECASE | re.DOTALL)

            if not matches:
                # Alternative pattern
                link_pattern = r'<h2[^>]*class="result__title"[^>]*>[^<]*<a[^>]+href="([^"]+)"[^>]*>[^<]*</a>[^<]*</h2>[^<]*<a[^>]+class="result__snippet"[^>]*>([^<]+)'
                matches = re.findall(link_pattern, html, re.IGNORECASE | re.DOTALL)

            if not matches:
                # Simpler pattern
                link_pattern = r'<a[^>]+class="result__url"[^>]*>[^<]*<a[^>]+href="([^"]+)"'
                url_matches = re.findall(link_pattern, html, re.IGNORECASE | re.DOTALL)
                snippet_pattern = r'<a[^>]+class="result__snippet"[^>]*>([^<]+)'
                snippet_matches = re.findall(snippet_pattern, html, re.IGNORECASE | re.DOTALL)
                matches = list(zip(url_matches, snippet_matches)) if url_matches and snippet_matches else []

            for i, match in enumerate(matches[:n]):
                if isinstance(match, tuple) and len(match) >= 2:
                    url, snippet = match[0], match[1]
                else:
                    # Handle case where we only got URLs
                    url = match if isinstance(match, str) else ""
                    snippet = ""

                # Clean up URL (might be /url?q=...)
                if url.startswith('/url?q='):
                    import urllib.parse
                    try:
                        url = urllib.parse.parse_qs(urllib.parse.urlparse(url).query).get('q', [''])[0]
                    except:
                        pass

                title = url.split('/')[2] if '//' in url and len(url.split('/')) > 2 else url[:50]
                results.append({
                    "title": unescape(title.strip()[:100]),
                    "url": url,
                    "snippet": unescape(snippet.strip()[:200])
                })

            if results:
                # Cache successful results
                _WEB_SEARCH_CACHE[cache_key] = (results, time.time())
                return results
    except Exception:
        pass

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
