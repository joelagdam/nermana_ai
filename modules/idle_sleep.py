import subprocess, threading, time, logging
from pathlib import Path
import requests

# Import learning modules for background processing
try:
    from bot.reflection_engine import run_now as reflection_run_now
    from bot.nermana_memory_llm import _consolidate as memory_consolidate
    from modules import tools
    LEARNING_MODULES_AVAILABLE = True
except ImportError as e:
    LEARNING_MODULES_AVAILABLE = False
    log = logging.getLogger("idle_sleep")
    log.warning(f"Learning modules not available for background processing: {e}")

log = logging.getLogger("idle_sleep")
BASE = Path.home() / "nermana"
CTL = BASE / "nermana_ctl.sh"
CHECK_INTERVAL = 60
_last_activity = time.time()
_wake_lock = threading.Lock()
_restart_lock = threading.Lock()
_health_fail_count = 0
_last_health_fail_time = 0
_RESTART_THRESHOLD = 3
_RESTART_COOLDOWN = 300  # seconds to wait before allowing another restart

# Background learning state
_background_intensified = False
_last_reflection_time = 0
_last_consolidation_time = 0
_last_curiosity_time = 0

# Config cache for performance
_cfg_cache = {}
_cfg_cache_mtime = 0

def _load_cfg():
    global _cfg_cache, _cfg_cache_mtime
    cfg_file = BASE / ".config"

    # Check if file exists and get modification time
    if not cfg_file.exists():
        return {}

    try:
        mtime = cfg_file.stat().st_mtime
    except:
        # If we can't get mtime, fall back to reading file
        mtime = 0

    # Return cached config if file hasn't changed
    if mtime == _cfg_cache_mtime and _cfg_cache:
        return _cfg_cache.copy()

    # Read and cache new config
    cfg = {}
    try:
        for line in cfg_file.read_text().splitlines():
            if "=" in line and not line.startswith("#"):
                k, v = line.split("=", 1)
                cfg[k.strip()] = v.strip().strip('"').strip("'")
    except:
        # If reading fails, return cached config if available, otherwise empty dict
        return _cfg_cache.copy() if _cfg_cache else {}

    _cfg_cache = cfg.copy()
    _cfg_cache_mtime = mtime
    return cfg

def _cfg():
    c = _load_cfg()
    return {
        "idle_minutes":  int(c.get("IDLE_SLEEP_MINUTES", "15")),
        "wake_timeout":  int(c.get("WAKE_TIMEOUT_SECONDS", "150")),
        "llama_host":    c.get("LLAMA_HOST", "127.0.0.1"),
        "llama_port":    c.get("LLAMA_PORT", "8080"),
        # Background learning intervals when intensified (in minutes)
        "bg_reflection_intensified_min": int(c.get("BG_REFLECTION_INTENSIFIED_MIN", "10")),
        "bg_consolidation_intensified_min": int(c.get("BG_CONSOLIDATION_INTENSIFIED_MIN", "30")),
        "bg_curiosity_intensified_min": int(c.get("BG_CURIOSITY_INTENSIFIED_MIN", "5")),
    }

def _attempt_restart():
    """Attempt to restart the LLM server if health checks repeatedly fail."""
    global _health_fail_count, _last_health_fail_time
    with _restart_lock:
        # Check if we are in cooldown period
        now = time.time()
        if now - _last_health_fail_time < _RESTART_COOLDOWN:
            return False
        if _health_fail_count < _RESTART_THRESHOLD:
            return False
        # Try to restart server
        log.info(f"Health check failed {_health_fail_count} times; attempting to restart LLM server")
        try:
            # Stop server
            subprocess.run(["bash", str(CTL), "stop-server"], capture_output=True, timeout=30)
            time.sleep(5)
            # Start server
            result = subprocess.run(["bash", str(CTL), "start-server"], capture_output=True, timeout=60)
            if result.returncode == 0:
                log.info("LLM server restarted successfully")
                _health_fail_count = 0
                _last_health_fail_time = now
                return True
            else:
                log.error(f"Failed to start server: {result.stderr}")
        except Exception as e:
            log.error(f"Error restarting LLM server: {e}")
        # If we get here, restart didn't succeed
        _last_health_fail_time = now
        return False

def record_activity():
    global _last_activity, _background_intensified
    _last_activity = time.time()
    # When user returns, return to normal learning intensity
    if _background_intensified:
        _background_intensified = False
        log.info("User activity detected – returning to normal learning intensity")

def is_server_awake():
    c = _cfg()
    try:
        r = requests.get(f"http://{c['llama_host']}:{c['llama_port']}/health", timeout=5)
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
    """Check if server is awake without attempting to restart it.
    In the passive learning model, we keep the server awake during idle
    through background learning intensification rather than sleep/restart cycles."""
    record_activity()
    return is_server_awake()

def _run_background_reflection():
    """Run a single reflection cycle with conservative settings for background processing"""
    if not LEARNING_MODULES_AVAILABLE:
        return
    try:
        # Use reflection engine with quality_trigger=False for scheduled reflection
        # This runs with existing settings but we can log it
        result = reflection_run_now(quality_trigger=False)
        if result.get("facts_learned"):
            log.debug(f"Background reflection learned: {result['facts_learned'][0][:50] if result['facts_learned'] else 'None'}")
    except Exception as e:
        log.debug(f"Background reflection error: {e}")

def _run_background_consolidation():
    """Run memory consolidation for background processing"""
    if not LEARNING_MODULES_AVAILABLE:
        return
    try:
        memory_consolidate()
        log.debug("Background memory consolidation completed")
    except Exception as e:
        log.debug(f"Background consolidation error: {e}")

def _run_background_curiosity():
    """Process curiosity queue during background processing"""
    if not LEARNING_MODULES_AVAILABLE:
        return
    try:
        # Process a small batch of curiosity items
        from bot.reflection_engine import CURIOSITY_F, _curiosity_search
        import json

        if not CURIOSITY_F.exists():
            return

        queue = json.loads(CURIOSITY_F.read_text()) if CURIOSITY_F.exists() else []
        if not queue:
            return

        processed = 0
        remaining = []

        # Process up to 2 items to minimize resource usage
        for topic in queue:
            if processed >= 2:
                remaining.append(topic)
                continue

            result = _curiosity_search(topic)
            if result:
                log.debug(f"Background curiosity processed: {topic[:30]}")
                processed += 1
            else:
                remaining.append(topic)  # Retry later

        # Update queue with unprocessed items
        if len(remaining) < len(queue):
            CURIOSITY_F.write_text(json.dumps(remaining, indent=2))

    except Exception as e:
        log.debug(f"Background curiosity error: {e}")

def _intensify_background_learning():
    """Increase background learning activity when idle is detected"""
    global _background_intensified
    if not _background_intensified:
        _background_intensified = True
        log.info("Idle threshold reached – intensifying background learning")

def start_idle_monitor():
    def _loop():
        global _last_reflection_time, _last_consolidation_time, _last_curiosity_time
        while True:
            try:
                c = _cfg()
                current_time = time.time()
                idle_time = current_time - _last_activity

                # Health check for LLM server
                awake = is_server_awake()
                with _restart_lock:
                    if awake:
                        _health_fail_count = 0
                    else:
                        _health_fail_count += 1
                        _last_health_fail_time = current_time
                        # If too many failures, attempt restart
                        if _health_fail_count >= _RESTART_THRESHOLD:
                            log.warning(f"LLM server health check failed {_health_fail_count} times; attempting restart")
                            _attempt_restart()

                # Check if we've exceeded idle threshold
                if c["idle_minutes"] > 0 and idle_time >= c["idle_minutes"] * 60:
                    if awake:
                        # Instead of stopping server, intensify background learning
                        _intensify_background_learning()

                        # Run background learning tasks with staggered timing to avoid bursts
                        # Reflection: every N minutes when intensified
                        reflection_interval = c["bg_reflection_intensified_min"] * 60
                        if current_time - _last_reflection_time >= reflection_interval:
                            _run_background_reflection()
                            _last_reflection_time = current_time

                        # Consolidation: every N minutes when intensified
                        consolidation_interval = c["bg_consolidation_intensified_min"] * 60
                        if current_time - _last_consolidation_time >= consolidation_interval:
                            _run_background_consolidation()
                            _last_consolidation_time = current_time

                        # Curiosity processing: every N minutes when intensified
                        curiosity_interval = c["bg_curiosity_intensified_min"] * 60
                        if current_time - _last_curiosity_time >= curiosity_interval:
                            _run_background_curiosity()
                            _last_curiosity_time = current_time
                    else:
                        # Server not awake; we could optionally try to wake it via ctl,
                        # but we rely on the restart mechanism above.
                        pass

                else:
                    # Not idle or idle threshold not reached - normal or reduced background learning
                    if _background_intensified:
                        # User just returned, maintain normal intensity for a bit then reset
                        # For simplicity, we'll reset immediately when user activity detected
                        # (handled in record_activity)
                        pass

            except Exception as e:
                log.debug(f"Idle monitor error: {e}")
            time.sleep(CHECK_INTERVAL)
    threading.Thread(target=_loop, daemon=True).start()
