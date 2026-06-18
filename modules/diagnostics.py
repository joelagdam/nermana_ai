"""
diagnostics.py — NERMANA Self-Diagnostic Engine v4.6.0
Pure observer. No LLM calls. No state mutation.
"""
import json, logging, re, time, threading
from dataclasses import dataclass, asdict, field
from pathlib import Path
from typing import List
from datetime import datetime, timedelta

log = logging.getLogger("diagnostics")

BASE   = Path.home() / "nermana"
STATE  = BASE / "state"
LOGS   = BASE / "logs"
MEMORY = BASE / "memory"
STATE.mkdir(parents=True, exist_ok=True)

HEALTH_SUMMARY  = STATE / "health_summary.json"
HEALTH_JSONL    = STATE / "health.jsonl"
QUALITY_JSONL   = STATE / "quality_scores.jsonl"
CONSOL_TS_FILE  = STATE / "last_consolidation.txt"
REFLECT_TS_FILE = STATE / "last_reflection.txt"
PIPELINE_JSONL  = LOGS  / "pipeline.jsonl"
HEALTH_RETAIN_DAYS = 30

@dataclass
class DiagnosticReport:
    ts:                           float = 0.0
    ts_human:                     str   = ""
    latency_p50_ms:               float = 0.0
    latency_p95_ms:               float = 0.0
    lt_count:                     int   = 0
    st_count:                     int   = 0
    junk_count:                   int   = 0
    junk_ratio:                   float = 0.0
    memory_hit_rate:              float = 0.0
    error_rate_per_hour:          float = 0.0
    tool_trigger_rate:            float = 0.0
    hallucination_suspicion_rate: float = 0.0
    embedding_healthy:            bool  = False
    embedding_last_ok_ts:         float = 0.0
    consolidation_last_ts:        float = 0.0
    consolidation_facts_removed:  int   = 0
    reflection_last_ts:           float = 0.0
    total_exchanges_24h:          int   = 0
    avg_quality_score:            float = 0.0
    correction_count_24h:         int   = 0
    health_score:                 float = 0.0
    alerts:                       List[str] = field(default_factory=list)

def _read_pipeline(hours=24):
    if not PIPELINE_JSONL.exists():
        return []
    cutoff = time.time() - hours * 3600
    events = []
    try:
        for line in PIPELINE_JSONL.read_text(encoding="utf-8").splitlines():
            try:
                ev = json.loads(line.strip())
                if ev.get("ts", 0) >= cutoff:
                    events.append(ev)
            except Exception:
                pass
    except Exception:
        pass
    return events

def _latency(events):
    latencies, user_ts = [], {}
    for ev in events:
        stage = ev.get("stage", "")
        src   = ev.get("data", {}).get("source", "default")
        ts    = ev.get("ts", 0)
        if stage == "USER":
            user_ts[src] = ts
        elif stage == "MAIN_LLM" and src in user_ts:
            ms = (ts - user_ts.pop(src)) * 1000
            if 100 < ms < 120000:
                latencies.append(ms)
    if not latencies:
        return 0.0, 0.0
    latencies.sort()
    n = len(latencies)
    return round(latencies[n // 2], 1), round(latencies[min(int(n * 0.95), n - 1)], 1)

def _memory_hit_rate(events):
    total = hit = 0
    for ev in events:
        if ev.get("stage") == "PRIMER_CALL":
            total += 1
            r = ev.get("data", {}).get("result", "")
            if r and r not in ("(empty)", "", "(no context)"):
                hit += 1
    return round(hit / total, 3) if total else 0.0

def _error_rate(events):
    cutoff = time.time() - 3600
    return float(sum(1 for ev in events
                     if ev.get("stage") == "ERROR" and ev.get("ts", 0) >= cutoff))

def _tool_rates(events):
    user_c  = sum(1 for ev in events if ev.get("stage") == "USER")
    force_c = sum(1 for ev in events if ev.get("stage") == "FORCE_SEARCH")
    tool_c  = sum(1 for ev in events if ev.get("stage") == "TOOL")
    halluc  = sum(1 for ev in events
                  if ev.get("stage") == "MAIN_LLM"
                  and ev.get("data", {}).get("via_tool")
                  and not ev.get("data", {}).get("force_search"))
    ttr = round(force_c / user_c, 3) if user_c else 0.0
    hsr = round(halluc  / tool_c,  3) if tool_c  else 0.0
    return ttr, hsr

def _memory_counts():
    def count(p):
        if not p.exists():
            return 0
        n = 0
        for f in p.glob("*.txt"):
            try:
                n += sum(1 for l in f.read_text(encoding="utf-8").splitlines() if l.strip())
            except Exception:
                pass
        return n
    return count(MEMORY/"long_term"), count(MEMORY/"short_term"), count(MEMORY/"junk")

def _embedding_status():
    healthy = False
    try:
        from semantic_memory import is_available
        healthy = is_available()
    except Exception:
        pass
    last_ok = 0.0
    try:
        for line in PIPELINE_JSONL.read_text(encoding="utf-8").splitlines()[-500:]:
            try:
                ev = json.loads(line)
                if ev.get("stage") == "EMBED_OK":
                    last_ok = max(last_ok, ev.get("ts", 0))
            except Exception:
                pass
    except Exception:
        pass
    return healthy, last_ok

def _consolidation_status():
    last_ts = 0.0
    try:
        if CONSOL_TS_FILE.exists():
            last_ts = float(CONSOL_TS_FILE.read_text().strip())
    except Exception:
        pass
    removed = 0
    try:
        for line in PIPELINE_JSONL.read_text(encoding="utf-8").splitlines()[-1000:]:
            try:
                ev = json.loads(line)
                if ev.get("stage") == "CONSOLIDATION":
                    removed = max(removed, ev.get("data", {}).get("removed", 0))
            except Exception:
                pass
    except Exception:
        pass
    return last_ts, removed

def _reflection_ts():
    try:
        if REFLECT_TS_FILE.exists():
            return float(REFLECT_TS_FILE.read_text().strip())
    except Exception:
        pass
    return 0.0

def _quality_stats():
    if not QUALITY_JSONL.exists():
        return 0.0, 0
    cutoff = time.time() - 86400
    scores, corrections = [], 0
    try:
        for line in QUALITY_JSONL.read_text(encoding="utf-8").splitlines():
            try:
                rec = json.loads(line)
                if rec.get("ts", 0) >= cutoff:
                    s = rec.get("score", 0)
                    if 1 <= s <= 5:
                        scores.append(s)
                    if rec.get("correction"):
                        corrections += 1
            except Exception:
                pass
    except Exception:
        pass
    avg = round(sum(scores) / len(scores), 2) if scores else 0.0
    return avg, corrections

def _health_score(r):
    score = 100.0
    if r.latency_p95_ms > 10000:  score -= 20
    elif r.latency_p95_ms > 6000: score -= 10
    elif r.latency_p95_ms > 4000: score -= 5
    if r.junk_ratio > 0.7:        score -= 15
    elif r.junk_ratio > 0.5:      score -= 8
    if r.memory_hit_rate < 0.2 and r.total_exchanges_24h > 5:  score -= 10
    elif r.memory_hit_rate < 0.4: score -= 5
    if r.error_rate_per_hour > 10: score -= 20
    elif r.error_rate_per_hour > 5: score -= 10
    elif r.error_rate_per_hour > 2: score -= 5
    if r.hallucination_suspicion_rate > 0.4: score -= 15
    elif r.hallucination_suspicion_rate > 0.2: score -= 7
    if not r.embedding_healthy:  score -= 10
    if r.consolidation_last_ts > 0 and time.time() - r.consolidation_last_ts > 172800:
        score -= 5
    if r.avg_quality_score > 0:
        if r.avg_quality_score < 2.5:   score -= 15
        elif r.avg_quality_score < 3.5: score -= 5
    if r.correction_count_24h > 5:   score -= 10
    elif r.correction_count_24h > 2: score -= 5
    return max(0.0, round(score, 1))

def _build_alerts(r):
    a = []
    if r.latency_p95_ms > 8000:
        a.append(f"HIGH LATENCY: p95={r.latency_p95_ms:.0f}ms — consider reducing MAX_TOKENS")
    if r.junk_ratio > 0.7:
        a.append(f"MEMORY QUALITY: {r.junk_ratio:.0%} junk — raise LT_SCORE_MIN")
    if r.memory_hit_rate < 0.2 and r.total_exchanges_24h > 5:
        a.append("LOW MEMORY HIT RATE: primer returning empty — LT memory sparse")
    if r.error_rate_per_hour > 5:
        a.append(f"HIGH ERROR RATE: {r.error_rate_per_hour:.1f}/hr — check bot.log")
    if r.hallucination_suspicion_rate > 0.3:
        a.append(f"HALLUCINATION RISK: {r.hallucination_suspicion_rate:.0%} tool calls suggest cold-answer corrections")
    if not r.embedding_healthy:
        a.append("EMBEDDING SERVER DOWN: semantic memory degraded to keyword search")
    if r.consolidation_last_ts > 0 and time.time() - r.consolidation_last_ts > 172800:
        a.append("CONSOLIDATION OVERDUE: last run >48h ago")
    if r.avg_quality_score > 0 and r.avg_quality_score < 2.5:
        a.append(f"LOW QUALITY: avg score {r.avg_quality_score:.1f}/5 — reflection needed")
    if r.correction_count_24h > 5:
        a.append(f"CORRECTION SPIKE: {r.correction_count_24h} user corrections in 24h")
    if r.lt_count == 0 and r.total_exchanges_24h > 20:
        a.append("NO LONG-TERM MEMORY: >20 exchanges but 0 LT facts stored")
    return a

def run() -> DiagnosticReport:
    now = time.time()
    events = _read_pipeline(hours=24)
    r = DiagnosticReport()
    r.ts, r.ts_human = now, datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    r.latency_p50_ms, r.latency_p95_ms = _latency(events)
    r.memory_hit_rate = _memory_hit_rate(events)
    r.error_rate_per_hour = _error_rate(events)
    r.tool_trigger_rate, r.hallucination_suspicion_rate = _tool_rates(events)
    r.total_exchanges_24h = sum(1 for ev in events if ev.get("stage") == "USER")
    r.lt_count, r.st_count, r.junk_count = _memory_counts()
    total = r.lt_count + r.st_count + r.junk_count
    r.junk_ratio = round(r.junk_count / total, 3) if total else 0.0
    r.embedding_healthy, r.embedding_last_ok_ts = _embedding_status()
    r.consolidation_last_ts, r.consolidation_facts_removed = _consolidation_status()
    r.reflection_last_ts = _reflection_ts()
    r.avg_quality_score, r.correction_count_24h = _quality_stats()
    r.health_score = _health_score(r)
    r.alerts = _build_alerts(r)
    log.info(f"Diagnostic: health={r.health_score} p95={r.latency_p95_ms}ms alerts={len(r.alerts)}")
    return r

def run_and_persist() -> DiagnosticReport:
    r = run()
    data = asdict(r)
    try:
        HEALTH_SUMMARY.write_text(json.dumps(data, indent=2))
    except Exception as e:
        log.warning(f"health_summary write failed: {e}")
    try:
        with open(HEALTH_JSONL, "a", encoding="utf-8") as f:
            f.write(json.dumps(data) + "\n")
        _prune_health()
    except Exception as e:
        log.warning(f"health.jsonl append failed: {e}")
    try:
        from auto_tuner import check as _tune
        _tune(r)
    except Exception as e:
        log.debug(f"auto_tuner.check failed: {e}")
    return r

def _prune_health():
    if not HEALTH_JSONL.exists():
        return
    cutoff = time.time() - HEALTH_RETAIN_DAYS * 86400
    try:
        lines = HEALTH_JSONL.read_text(encoding="utf-8").splitlines()
        kept = []
        for l in lines:
            try:
                if json.loads(l).get("ts", 0) >= cutoff:
                    kept.append(l)
            except Exception:
                pass
        HEALTH_JSONL.write_text("\n".join(kept) + "\n", encoding="utf-8")
    except Exception:
        pass

def get_history(days=7):
    if not HEALTH_JSONL.exists():
        return []
    cutoff = time.time() - days * 86400
    result = []
    try:
        for line in HEALTH_JSONL.read_text(encoding="utf-8").splitlines():
            try:
                rec = json.loads(line)
                if rec.get("ts", 0) >= cutoff:
                    result.append(rec)
            except Exception:
                pass
    except Exception:
        pass
    return result

def get_latest():
    if not HEALTH_SUMMARY.exists():
        return {}
    try:
        return json.loads(HEALTH_SUMMARY.read_text())
    except Exception:
        return {}

def start_diagnostic_loop():
    def _loop():
        threading.Event().wait(300)
        while True:
            try:
                run_and_persist()
            except Exception as e:
                log.warning(f"Diagnostic loop error: {e}")
            threading.Event().wait(6 * 3600)
    threading.Thread(target=_loop, daemon=True, name="nermana-diagnostics").start()
    log.info("Diagnostic loop started (6h interval)")
