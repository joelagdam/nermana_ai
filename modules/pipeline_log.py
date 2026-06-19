import json, time, logging
from pathlib import Path
from collections import deque

BASE = Path.home() / "nermana"
LOG_DIR = BASE / "logs"
LOG_DIR.mkdir(exist_ok=True)
PIPE_LOG = LOG_DIR / "pipeline.jsonl"
_events = deque(maxlen=100)

def _write(event):
    event["ts"] = time.time()
    event["ts_human"] = time.strftime("%H:%M:%S")
    _events.append(event)
    with open(PIPE_LOG, "a") as f:
        f.write(json.dumps(event) + "\n")

def log_user_message(text):
    _write({"stage":"USER", "data":{"message":text[:300]}})

def log_memory_query(query, memory):
    _write({"stage":"MEMORY_QUERY", "data":{"query":query[:100], "lt_facts_count":len(memory.get("lt_facts",[]))}})

def log_primer_call(source, user_msg, briefing):
    """source: 'cached'|'deterministic'; briefing is what gets injected into LLM."""
    _write({"stage":"PRIMER_CALL", "data":{"source":source, "briefing_len":len(briefing), "result":briefing[:300] if briefing else ""}})

def log_main_call(full, resp):
    _write({"stage":"MAIN_LLM", "data":{"response":resp[:400]}})

def log_memory_eval(exchange, raw, stored, score, tier):
    _write({"stage":"MEMORY_EVAL", "data":{"stored":stored[:200], "score":score, "tier":tier}})

def log_error(stage, msg):
    _write({"stage":"ERROR", "data":{"where":stage, "message":msg[:300]}})

def log_tool_use(tool: str, source: str, query: str = "", result_len: int = 0):
    """
    tool   : "search" | "weather" | "exec"
    source : "ai"   — NERMANA emitted TOOL: directive
             "user" — user message triggered direct tool call (e.g. /search)
    """
    _write({"stage": "TOOL_USE", "data": {
        "tool": tool,
        "source": source,
        "query": query[:120],
        "result_len": result_len
    }})


def log_embedding(fact_id: str):
    _write({"stage": "EMBEDDING", "data": {"fact_id": fact_id}})

def get_recent(n=50):
    return list(_events)[-n:]
