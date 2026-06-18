import subprocess, threading, time, logging
from pathlib import Path
import requests

log = logging.getLogger("idle_sleep")
BASE = Path.home() / "nermana"
CTL = BASE / "nermana_ctl.sh"
CHECK_INTERVAL = 60
_last_activity = time.time()
_wake_lock = threading.Lock()

def _load_cfg():
    cfg = {}
    cfg_file = BASE / ".config"
    if cfg_file.exists():
        for line in cfg_file.read_text().splitlines():
            if "=" in line and not line.startswith("#"):
                k, v = line.split("=", 1)
                cfg[k.strip()] = v.strip().strip('"').strip("'")
    return cfg

def _cfg():
    c = _load_cfg()
    return {
        "idle_minutes":  int(c.get("IDLE_SLEEP_MINUTES", "15")),
        "wake_timeout":  int(c.get("WAKE_TIMEOUT_SECONDS", "150")),
        "llama_host":    c.get("LLAMA_HOST", "127.0.0.1"),
        "llama_port":    c.get("LLAMA_PORT", "8080"),
    }

def record_activity():
    global _last_activity
    _last_activity = time.time()

def is_server_awake():
    c = _cfg()
    try:
        r = requests.get(f"http://{c['llama_host']}:{c['llama_port']}/health", timeout=2)
        return r.status_code == 200
    except:
        return False

def _run_ctl(arg):
    try:
        subprocess.run(["bash", str(CTL), arg], capture_output=True, timeout=180)
        return True
    except:
        return False

def ensure_awake(notify=None):
    record_activity()
    if is_server_awake():
        return True
    with _wake_lock:
        if is_server_awake():
            return True
        log.info("Server asleep – attempting restart")
        if notify:
            try:
                notify()
            except:
                pass
        time.sleep(2)
        if is_server_awake():
            log.info("Server responded after brief wait — no restart needed")
            return True
        _run_ctl("stop-server")
        time.sleep(2)
        _run_ctl("start-server")
        c = _cfg()
        deadline = time.time() + c["wake_timeout"]
        poll = 2
        while time.time() < deadline:
            if is_server_awake():
                log.info("Server restarted successfully")
                return True
            time.sleep(poll)
            poll = min(poll + 1, 8)
        log.warning(f"Server failed to wake within {c['wake_timeout']}s")
        return False

def start_idle_monitor():
    def _loop():
        while True:
            try:
                c = _cfg()
                if c["idle_minutes"] > 0 and (time.time() - _last_activity) >= c["idle_minutes"] * 60:
                    if is_server_awake():
                        log.info("Idle threshold reached – stopping server")
                        _run_ctl("stop-server")
            except:
                pass
            time.sleep(CHECK_INTERVAL)
    threading.Thread(target=_loop, daemon=True).start()
