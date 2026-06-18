import json, time, logging
from pathlib import Path
from datetime import datetime

log = logging.getLogger("mood")
BASE = Path.home() / "nermana"
STATE_DIR = BASE / "state"
STATE_DIR.mkdir(exist_ok=True)
MOOD_FILE = STATE_DIR / "mood.json"

POSITIVE = {"thanks","great","awesome","love","nice","good","cool","happy","yes","yay","amazing","glad","lol","fun","win","works"}
NEGATIVE = {"ugh","annoying","hate","bad","broken","error","fail","frustrated","angry","sad","tired","stuck","wrong","sucks","no","never","wtf"}

def _load():
    if MOOD_FILE.exists():
        try:
            return json.loads(MOOD_FILE.read_text())
        except:
            pass
    return {"sentiment_score":0, "last_tool":None, "last_tool_ts":0, "label":"neutral"}

def _save(state):
    MOOD_FILE.write_text(json.dumps(state))

def record_message(text):
    state = _load()
    low = text.lower()
    words = set(low.split())
    delta = len(words & POSITIVE) - len(words & NEGATIVE)
    score = state["sentiment_score"] + delta
    if score > 0:
        score -= 1
    elif score < 0:
        score += 1
    score = max(-10, min(10, score))
    state["sentiment_score"] = score
    if score >= 5:
        state["label"] = "delighted"
    elif score >= 2:
        state["label"] = "upbeat"
    elif score <= -4:
        state["label"] = "frustrated"
    elif score <= -2:
        state["label"] = "low"
    else:
        state["label"] = "neutral"
    _save(state)

def record_tool_use(tool):
    state = _load()
    state["last_tool"] = tool
    state["last_tool_ts"] = time.time()
    _save(state)

def get_mood():
    state = _load()
    hour = datetime.now().hour
    if 5 <= hour < 11:
        tod = "fresh"
    elif 11 <= hour < 17:
        tod = "steady"
    elif 17 <= hour < 22:
        tod = "relaxed"
    else:
        tod = "tired"
    if state.get("last_tool_ts", 0) > time.time() - 600 and state["sentiment_score"] > -4:
        state["label"] = "curious"
    return state

def get_mood_line():
    m = get_mood()
    labels = {
        "fresh": "fresh and alert",
        "steady": "steady",
        "relaxed": "relaxed",
        "tired": "a bit tired",
        "curious": "curious",
        "upbeat": "upbeat",
        "delighted": "delighted",
        "low": "a bit low",
        "frustrated": "frustrated",
        "neutral": "neutral"
    }
    return labels.get(m["label"], "neutral")
