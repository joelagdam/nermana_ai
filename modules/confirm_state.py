import json, time, logging
from pathlib import Path

log = logging.getLogger("confirm_state")
BASE = Path.home() / "nermana"
STATE_FILE = BASE / "state" / "pending_confirmations.json"
CONFIRM_TIMEOUT = 120

def _load():
    if STATE_FILE.exists():
        try:
            return json.loads(STATE_FILE.read_text())
        except:
            pass
    return {}

def _save(data):
    STATE_FILE.write_text(json.dumps(data))

def set_pending(chat_id, kind, payload):
    data = _load()
    data[str(chat_id)] = {"kind": kind, "payload": payload, "ts": time.time()}
    _save(data)

def get_pending(chat_id):
    data = _load()
    entry = data.get(str(chat_id))
    if entry and time.time() - entry.get("ts", 0) > CONFIRM_TIMEOUT:
        clear_pending(chat_id)
        return {}
    return entry or {}

def clear_pending(chat_id):
    data = _load()
    data.pop(str(chat_id), None)
    _save(data)

def is_affirmative(text):
    return text.strip().lower() in ("yes", "y", "yep", "yeah", "confirm", "ok", "okay")

def is_negative(text):
    return text.strip().lower() in ("no", "n", "nope", "cancel")
