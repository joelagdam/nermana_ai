#!/data/data/com.termux/files/usr/bin/bash
NERMANA_DIR="$HOME/nermana"
BOT_DIR="$NERMANA_DIR/bot"
CONFIG_FILE="$NERMANA_DIR/.config"
LOG_DIR="$NERMANA_DIR/logs"
PID_BOT="$NERMANA_DIR/.bot.pid"
PID_SRV="$NERMANA_DIR/.server.pid"
PID_EMBED="$NERMANA_DIR/.embed.pid"
PID_WEB="$NERMANA_DIR/.web.pid"
mkdir -p "$LOG_DIR"
GREEN="\e[32m"; RED="\e[31m"; CYAN="\e[36m"; YELLOW="\e[33m"; RESET="\e[0m"
ok() { echo -e "${GREEN}✓${RESET} $1"; }
fail() { echo -e "${RED}✗${RESET} $1"; exit 1; }
warn() { echo -e "${YELLOW}⚠${RESET} $1"; }
cfg_get() { grep "^$1=" "$CONFIG_FILE" 2>/dev/null | cut -d= -f2- | sed 's/^["'"'"']//;s/["'"'"']$//' | tr -d '[:space:]' || echo "$2"; }
LLAMA_HOST=$(cfg_get LLAMA_HOST 127.0.0.1)
LLAMA_PORT=$(cfg_get LLAMA_PORT 8080)
LLAMA_EMBED_PORT=$(cfg_get LLAMA_EMBED_PORT 8081)
LLAMA_THREADS=$(cfg_get LLAMA_THREADS 6)
LLAMA_CONTEXT=$(cfg_get LLAMA_CONTEXT 4096)
LLAMA_BATCH=$(cfg_get LLAMA_BATCH 256)
MODEL_PATH=$(cfg_get LLAMA_MODEL_PATH "$HOME/nermana/models/smollm2-1.7b-instruct-Q4_K_M.gguf")
EMBED_PATH=$(cfg_get EMBEDDING_MODEL_PATH "$HOME/nermana/models/embeddings/nomic-embed-text-v1.5.Q4_K_M.gguf")
LLAMA_SERVER="$HOME/llama.cpp/build/bin/llama-server"
BOLD="\e[1m"
DIM="\e[2m"

start_servers() {
    # Main generation server
    if ! curl -s "http://$LLAMA_HOST:$LLAMA_PORT/health" >/dev/null 2>&1; then
        [ ! -f "$LLAMA_SERVER" ] && fail "llama-server not found"
        [ ! -f "$MODEL_PATH" ] && fail "Model not found: $MODEL_PATH"
        nohup "$LLAMA_SERVER" -m "$MODEL_PATH" --host "$LLAMA_HOST" --port "$LLAMA_PORT" -t "$LLAMA_THREADS" -c "$LLAMA_CONTEXT" -b "$LLAMA_BATCH" --log-disable > "$LOG_DIR/server.log" 2>&1 &
        echo $! > "$PID_SRV"
        sleep 3
        curl -s "http://$LLAMA_HOST:$LLAMA_PORT/health" >/dev/null && ok "Main server started on port $LLAMA_PORT" || warn "Main server may still be loading"
    else
        ok "Main server already running"
    fi

    # Embedding server (separate instance, port 8081, with --embedding)
    if [ -f "$EMBED_PATH" ]; then
        if ! curl -s "http://$LLAMA_HOST:$LLAMA_EMBED_PORT/health" >/dev/null 2>&1; then
            nohup "$LLAMA_SERVER" -m "$EMBED_PATH" --host "$LLAMA_HOST" --port "$LLAMA_EMBED_PORT" -t 2 --embedding --log-disable > "$LOG_DIR/embed.log" 2>&1 &
            echo $! > "$PID_EMBED"
            sleep 2
            curl -s "http://$LLAMA_HOST:$LLAMA_EMBED_PORT/health" >/dev/null && ok "Embedding server started on port $LLAMA_EMBED_PORT" || warn "Embedding server may be slow to start"
        else
            ok "Embedding server already running"
        fi
    else
        warn "No embedding model found at $EMBED_PATH – neural memory disabled"
    fi
}

stop_servers() {
    local p
    p=$(cat "$PID_SRV" 2>/dev/null) && kill "$p" 2>/dev/null && rm -f "$PID_SRV"
    p=$(cat "$PID_EMBED" 2>/dev/null) && kill "$p" 2>/dev/null && rm -f "$PID_EMBED"
    pkill -f "llama-server" 2>/dev/null
    ok "All servers stopped"
}

start_bot() {
    if pgrep -f "nermana_bot.py" >/dev/null; then ok "Bot already running"; return; fi
    export PYTHONPATH="$NERMANA_DIR/modules:$BOT_DIR:${PYTHONPATH:-}"
    nohup python3 "$BOT_DIR/nermana_bot.py" > "$LOG_DIR/bot.log" 2>&1 &
    local pid=$!
    echo $pid > "$PID_BOT"
    sleep 3
    if kill -0 "$pid" 2>/dev/null; then
        ok "Bot started (pid $pid)"
    else
        echo -e "${RED}✗${RESET} Bot crashed on startup. Last 10 lines of bot.log:"
        tail -10 "$LOG_DIR/bot.log"
        rm -f "$PID_BOT"
    fi
}

stop_bot() {
    local p=$(cat "$PID_BOT" 2>/dev/null)
    [ -n "$p" ] && kill "$p" 2>/dev/null && rm -f "$PID_BOT"
    pkill -f "nermana_bot.py" 2>/dev/null
    ok "Bot stopped"
}

start_web() {
    if pgrep -f "nermana_web.py" >/dev/null; then ok "Web already running"; return; fi
    export PYTHONPATH="$NERMANA_DIR/modules:$BOT_DIR:${PYTHONPATH:-}"
    nohup python3 "$NERMANA_DIR/web/nermana_web.py" > "$LOG_DIR/web.log" 2>&1 &
    local pid=$!
    echo $pid > "$PID_WEB"
    sleep 2
    if kill -0 "$pid" 2>/dev/null; then
        ok "WebUI at http://127.0.0.1:5000"
    else
        echo -e "${RED}✗${RESET} WebUI crashed. Last 10 lines of web.log:"
        tail -10 "$LOG_DIR/web.log"
        rm -f "$PID_WEB"
    fi
}

stop_web() {
    local p=$(cat "$PID_WEB" 2>/dev/null)
    [ -n "$p" ] && kill "$p" 2>/dev/null && rm -f "$PID_WEB"
    pkill -f "nermana_web.py" 2>/dev/null
    ok "Web stopped"
}

patch_module() {
    # Hot-replace a single module without full reinstall.
    # Usage: nermana patch <module_name> [source_file]
    local module="$1"
    local src="$2"
    declare -A MODULE_PATHS=(
        ["llm_client"]="$NERMANA_DIR/modules/llm_client.py"
        ["memory_engine"]="$NERMANA_DIR/modules/memory_engine.py"
        ["semantic_memory"]="$NERMANA_DIR/modules/semantic_memory.py"
        ["tools"]="$NERMANA_DIR/modules/tools.py"
        ["pipeline_log"]="$NERMANA_DIR/modules/pipeline_log.py"
        ["mood"]="$NERMANA_DIR/modules/mood.py"
        ["scheduler"]="$NERMANA_DIR/modules/scheduler.py"
        ["idle_sleep"]="$NERMANA_DIR/modules/idle_sleep.py"
        ["confirm_state"]="$NERMANA_DIR/modules/confirm_state.py"
        ["time_context"]="$NERMANA_DIR/modules/time_context.py"
        ["primer"]="$NERMANA_DIR/bot/nermana_primer.py"
        ["memory_llm"]="$NERMANA_DIR/bot/nermana_memory_llm.py"
        ["bot"]="$NERMANA_DIR/bot/nermana_bot.py"
        ["web"]="$NERMANA_DIR/web/nermana_web.py"
        ["html"]="$NERMANA_DIR/web/index.html"
        ["ctl"]="$NERMANA_DIR/nermana_ctl.sh"
    )
    if [ -z "$module" ]; then
        echo "Usage: nermana patch <module_name> [source_file]"
        echo "Modules: ${!MODULE_PATHS[@]}"
        return 1
    fi
    local dest="${MODULE_PATHS[$module]}"
    if [ -z "$dest" ]; then
        echo -e "${RED}Unknown module: $module${RESET}"
        echo "Known: ${!MODULE_PATHS[@]}"
        return 1
    fi
    if [ -n "$src" ] && [ -f "$src" ]; then
        cp "$dest" "${dest}.bak.$(date +%Y%m%d%H%M%S)" 2>/dev/null
        cp "$src" "$dest"
        ok "Patched $module → $dest"
    else
        echo "Current path: $dest"
        echo "To apply: nermana patch $module /path/to/new_${module}.py"
        return 0
    fi
    # Restart affected service
    case "$module" in
        bot|primer|memory_llm)
            stop_bot; sleep 1; start_bot
            ok "Bot restarted with patched $module" ;;
        web|html)
            stop_web; sleep 1; start_web
            ok "WebUI restarted with patched $module" ;;
        llm_client|memory_engine|semantic_memory|tools|mood|scheduler|idle_sleep|confirm_state|time_context|pipeline_log)
            stop_bot; sleep 1; start_bot
            ok "Bot restarted (module $module is imported at bot startup)" ;;
        ctl)
            ok "Control script patched. Changes take effect on next nermana call." ;;
    esac
}

list_modules() {
    echo ""
    echo -e "${BOLD}NERMANA module structure (for patching):${RESET}"
    echo ""
    echo -e "${CYAN}Core modules (modules/):${RESET}"
    for f in llm_client memory_engine semantic_memory tools pipeline_log mood scheduler idle_sleep confirm_state time_context; do
        local path="$NERMANA_DIR/modules/${f}.py"
        local size=$(wc -l < "$path" 2>/dev/null || echo "?")
        echo "  nermana patch $f   →  modules/${f}.py  ($size lines)"
    done
    echo ""
    echo -e "${CYAN}Bot modules (bot/):${RESET}"
    for f in primer:nermana_primer.py memory_llm:nermana_memory_llm.py bot:nermana_bot.py; do
        local name="${f%%:*}"; local file="${f##*:}"
        local size=$(wc -l < "$NERMANA_DIR/bot/$file" 2>/dev/null || echo "?")
        echo "  nermana patch $name   →  bot/$file  ($size lines)"
    done
    echo ""
    echo -e "${CYAN}Web modules (web/):${RESET}"
    echo "  nermana patch web    →  web/nermana_web.py  ($(wc -l < "$NERMANA_DIR/web/nermana_web.py" 2>/dev/null || echo ?) lines)"
    echo "  nermana patch html   →  web/index.html  ($(wc -l < "$NERMANA_DIR/web/index.html" 2>/dev/null || echo ?) lines)"
    echo ""
    echo -e "${CYAN}Control (root):${RESET}"
    echo "  nermana patch ctl    →  nermana_ctl.sh"
    echo ""
    echo -e "${DIM}Backups are auto-saved as <file>.bak.<timestamp> before each patch.${RESET}"
    echo ""
}

case "$1" in
    patch) patch_module "$2" "$3" ;;
    modules) list_modules ;;
    start) start_servers; start_bot ;;
    stop) stop_bot; stop_servers ;;
    restart) stop_bot; stop_servers; sleep 1; start_servers; start_bot ;;
    start-server) start_servers ;;
    stop-server) stop_servers ;;
    web) start_web ;;
    web-stop) stop_web ;;
    status)
        curl -s "http://$LLAMA_HOST:$LLAMA_PORT/health" >/dev/null && ok "Main LLM online" || fail "Main LLM offline"
        curl -s "http://$LLAMA_HOST:$LLAMA_EMBED_PORT/health" >/dev/null && ok "Embedding online" || warn "Embedding offline"
        pgrep -f "nermana_bot.py" >/dev/null && ok "Bot running" || fail "Bot stopped"
        ;;
    reset) rm -rf "$NERMANA_DIR/memory/long_term" "$NERMANA_DIR/memory/short_term" "$NERMANA_DIR/memory/buffer" "$NERMANA_DIR/knowledge/facts.txt" "$NERMANA_DIR/memory/embeddings/vectors.db"; mkdir -p "$NERMANA_DIR/memory/long_term" "$NERMANA_DIR/memory/short_term" "$NERMANA_DIR/memory/buffer"; ok "Memory cleared" ;;
    *) echo "Usage: nermana {start|stop|restart|web|web-stop|status|reset|patch|modules}" ;;
esac
