"""
auto_tuner.py — NERMANA Config Auto-Tuner v4.6.0
Bounded, annotated config adjustments. Never restarts the service.
"""
import json, logging, re, time
from pathlib import Path

log = logging.getLogger("auto_tuner")

BASE        = Path.home() / "nermana"
CONFIG_FILE = BASE / ".config"
STATE       = BASE / "state"
TUNE_LOG    = STATE / "auto_tune_log.txt"
TUNE_STATE  = STATE / "auto_tune_state.json"
STATE.mkdir(parents=True, exist_ok=True)

BOUNDS = {
    "MAIN_MAX_TOKENS":     (100, 600),
    "LONG_TERM_SCORE_MIN": (4,   9),
    "TEMPERATURE":         (0.3, 0.9),
    "REPETITION_PENALTY":  (1.0, 1.3),
}
BREACH_THRESHOLD   = 2
MIN_INTERVAL_HOURS = 24

def _read_cfg():
    cfg = {}
    if not CONFIG_FILE.exists():
        return cfg
    for line in CONFIG_FILE.read_text().splitlines():
        if "=" in line and not line.startswith("#"):
            k, v = line.split("=", 1)
            cfg[k.strip()] = v.strip().strip('"').strip("'")
    return cfg

def _write_cfg(key, value, reason):
    if not CONFIG_FILE.exists():
        return False
    content = CONFIG_FILE.read_text()
    ts_str  = time.strftime("%Y-%m-%d %H:%M")
    comment = f"# [auto_tuner {ts_str}] {reason}"
    if re.search(rf"^{key}=", content, re.M):
        content = re.sub(
            rf"^(# \[auto_tuner.*\]\n)?{key}=.*",
            f"{comment}\n{key}={value}",
            content, flags=re.M
        )
    else:
        content += f"\n{comment}\n{key}={value}\n"
    CONFIG_FILE.write_text(content)
    try:
        with open(TUNE_LOG, "a", encoding="utf-8") as f:
            f.write(f"[{ts_str}] {key}: -> {value} | reason: {reason}\n")
    except Exception:
        pass
    log.info(f"Auto-tuned {key}={value} ({reason})")
    return True

def _load_state():
    try:
        if TUNE_STATE.exists():
            return json.loads(TUNE_STATE.read_text())
    except Exception:
        pass
    return {"breach_counts": {}, "last_tune_ts": {}}

def _save_state(state):
    try:
        TUNE_STATE.write_text(json.dumps(state, indent=2))
    except Exception:
        pass

def _clamp(value, key):
    lo, hi = BOUNDS.get(key, (None, None))
    if lo is None:
        return value
    return max(lo, min(hi, value))

def _can_tune(state, key):
    return time.time() - state["last_tune_ts"].get(key, 0) >= MIN_INTERVAL_HOURS * 3600

def _record_tune(state, key):
    state["last_tune_ts"][key]   = time.time()
    state["breach_counts"][key]  = 0

def check(report) -> list:
    cfg     = _read_cfg()
    state   = _load_state()
    actions = []

    def breach(metric, triggered):
        counts = state["breach_counts"]
        counts[metric] = counts.get(metric, 0) + 1 if triggered else 0
        return counts.get(metric, 0) >= BREACH_THRESHOLD

    if breach("latency", report.latency_p95_ms > 8000 and report.latency_p95_ms > 0):
        if _can_tune(state, "MAIN_MAX_TOKENS"):
            cur = int(cfg.get("MAIN_MAX_TOKENS", 400))
            nv  = _clamp(cur - 50, "MAIN_MAX_TOKENS")
            if nv != cur:
                _write_cfg("MAIN_MAX_TOKENS", str(nv),
                           f"latency p95={report.latency_p95_ms:.0f}ms >8000ms")
                _record_tune(state, "MAIN_MAX_TOKENS")
                actions.append(f"MAIN_MAX_TOKENS {cur}->{nv} (high latency)")

    if breach("memory_hit", report.memory_hit_rate < 0.2 and report.total_exchanges_24h > 10):
        if _can_tune(state, "LONG_TERM_SCORE_MIN"):
            cur = int(cfg.get("LONG_TERM_SCORE_MIN", 7))
            nv  = _clamp(cur - 1, "LONG_TERM_SCORE_MIN")
            if nv != cur:
                _write_cfg("LONG_TERM_SCORE_MIN", str(nv),
                           f"memory hit {report.memory_hit_rate:.0%} <20%")
                _record_tune(state, "LONG_TERM_SCORE_MIN")
                actions.append(f"LONG_TERM_SCORE_MIN {cur}->{nv} (sparse LT)")

    if breach("junk_ratio", report.junk_ratio > 0.7):
        if _can_tune(state, "LONG_TERM_SCORE_MIN"):
            cur = int(cfg.get("LONG_TERM_SCORE_MIN", 7))
            nv  = _clamp(cur + 1, "LONG_TERM_SCORE_MIN")
            if nv != cur:
                _write_cfg("LONG_TERM_SCORE_MIN", str(nv),
                           f"junk ratio {report.junk_ratio:.0%} >70%")
                _record_tune(state, "LONG_TERM_SCORE_MIN")
                actions.append(f"LONG_TERM_SCORE_MIN {cur}->{nv} (too much junk)")

    if breach("rep_quality",
              report.avg_quality_score >= 4.0 and
              float(cfg.get("REPETITION_PENALTY", "1.15")) > 1.2):
        if _can_tune(state, "REPETITION_PENALTY"):
            cur = float(cfg.get("REPETITION_PENALTY", "1.15"))
            nv  = _clamp(round(cur - 0.05, 2), "REPETITION_PENALTY")
            if nv != cur:
                _write_cfg("REPETITION_PENALTY", str(nv),
                           f"quality {report.avg_quality_score:.1f}/5 is high")
                _record_tune(state, "REPETITION_PENALTY")
                actions.append(f"REPETITION_PENALTY {cur}->{nv}")

    breach("hallucination", report.hallucination_suspicion_rate > 0.3)
    if state["breach_counts"].get("hallucination", 0) >= BREACH_THRESHOLD:
        msg = (f"ALERT: hallucination suspicion {report.hallucination_suspicion_rate:.0%} >30% "
               f"— manual review recommended")
        log.warning(msg)
        try:
            with open(TUNE_LOG, "a") as f:
                f.write(f"[{time.strftime('%Y-%m-%d %H:%M')}] {msg}\n")
        except Exception:
            pass
        actions.append(msg)

    if report.error_rate_per_hour > 10:
        msg = f"CRITICAL: error rate {report.error_rate_per_hour:.1f}/hr — check bot.log"
        log.error(msg)
        try:
            with open(TUNE_LOG, "a") as f:
                f.write(f"[{time.strftime('%Y-%m-%d %H:%M')}] {msg}\n")
        except Exception:
            pass
        actions.append(msg)

    _save_state(state)
    return actions

def get_tune_log(n=50):
    if not TUNE_LOG.exists():
        return []
    try:
        return TUNE_LOG.read_text(encoding="utf-8").splitlines()[-n:]
    except Exception:
        return []
