import json, re, time, threading, logging, subprocess
from pathlib import Path
from datetime import datetime

log = logging.getLogger("scheduler")
BASE = Path.home() / "nermana"
REMINDERS_FILE = BASE / "state" / "reminders.json"
CHECK_INTERVAL = 60
_lock = threading.Lock()

def _load():
    with _lock:
        if REMINDERS_FILE.exists():
            try:
                return json.loads(REMINDERS_FILE.read_text())
            except:
                pass
        return []

def _save(reminders):
    with _lock:
        REMINDERS_FILE.write_text(json.dumps(reminders, indent=2))

_TIME_PATTERN = re.compile(r"\bat\s+(?:every\s+)?(\d{1,2})(?::(\d{2}))?\s*(am|pm)?\b", re.I)
_EVERY_PATTERN = re.compile(r"\bevery\b", re.I)

def parse_reminder(text):
    m = _TIME_PATTERN.search(text)
    if not m:
        return {"ok": False, "error": "No time found"}
    hour = int(m.group(1))
    minute = int(m.group(2) or 0)
    meridiem = (m.group(3) or "").lower()
    if meridiem == "pm" and hour < 12:
        hour += 12
    elif meridiem == "am" and hour == 12:
        hour = 0
    if not (0 <= hour <= 23 and 0 <= minute <= 59):
        return {"ok": False, "error": "Invalid time"}
    daily = bool(_EVERY_PATTERN.search(text))
    desc = _TIME_PATTERN.sub("", text).strip()
    desc = re.sub(r"\bevery\b", "", desc, flags=re.I).strip()
    if not desc:
        desc = "reminder"
    return {"ok": True, "text": desc, "hour": hour, "minute": minute, "daily": daily}

def add_reminder(chat_id, text, hour, minute, daily):
    reminders = _load()
    entry = {
        "id": int(time.time() * 1000),
        "chat_id": chat_id,
        "text": text,
        "hour": hour,
        "minute": minute,
        "daily": daily,
        "last_fired_date": None
    }
    reminders.append(entry)
    _save(reminders)
    return entry

def list_reminders(chat_id=None):
    reminders = _load()
    if chat_id is not None:
        reminders = [r for r in reminders if str(r["chat_id"]) == str(chat_id)]
    return reminders

def remove_reminder(chat_id, rid):
    reminders = _load()
    new = [r for r in reminders if not (str(r["chat_id"]) == str(chat_id) and r["id"] == rid)]
    _save(new)
    return len(new) < len(reminders)

def start_scheduler(send_callback):
    def _loop():
        while True:
            try:
                now = datetime.now()
                reminders = _load()
                changed = False
                for r in reminders:
                    if r["hour"] == now.hour and r["minute"] == now.minute and r.get("last_fired_date") != now.strftime("%Y-%m-%d"):
                        send_callback(r["chat_id"], f"Reminder: {r['text']}")
                        try:
                            subprocess.run(["termux-notification", "--title", "NERMANA", "--content", f"Reminder: {r['text']}"], timeout=5)
                        except:
                            pass
                        r["last_fired_date"] = now.strftime("%Y-%m-%d")
                        changed = True
                if changed:
                    _save([r for r in reminders if r.get("daily", True) or r.get("last_fired_date") is None])
            except Exception as e:
                log.error(f"Scheduler error: {e}")
            time.sleep(CHECK_INTERVAL)
    threading.Thread(target=_loop, daemon=True).start()
