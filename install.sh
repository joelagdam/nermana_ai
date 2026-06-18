#!/data/data/com.termux/files/usr/bin/bash
# ================================================================
# NERMANA Installer v4.7.3
# Installs NERMANA on Termux with AI models, Telegram bot / offline
# Repository: https://github.com/joelagdam/nermana_ai
#
# Usage:
#   bash install.sh                 interactive (recommended)
#   bash install.sh --quick         skip prompts, auto-select first model
# ================================================================

set -Eeuo pipefail
trap 'echo -e "\n \e[31m✖\e[0m Installation interrupted. models/ is preserved in ~/nermana/models"; exit 1' INT TERM

_QUICK_MODE=false
for _arg in "$@"; do [ "$_arg" = "--quick" ] && _QUICK_MODE=true; done

# ── Terminal formatting ──────────────────────────
BOLD="\e[1m"; DIM="\e[2m"; RESET="\e[0m"
GREEN="\e[32m"; RED="\e[31m"; CYAN="\e[36m"; YELLOW="\e[33m"; MAGENTA="\e[35m"

ok()      { echo -e " ${GREEN}✔${RESET} $1"; }
warn()    { echo -e " ${YELLOW}⚠${RESET} $1"; }
fail()    { echo -e " ${RED}✖${RESET} $1"; exit 1; }
info()    { echo -e " ${CYAN}ℹ${RESET} $1"; }
section() { echo -e "\n ${BOLD}${MAGENTA}◆${RESET} ${BOLD}$1${RESET}"; }
sub()     { echo -e "    $1"; }
prompt_yn() { read -p " ${CYAN}?${RESET} $1 [Y/n] " r; [[ "$r" =~ ^[nN] ]] && return 1; return 0; }

# ── Download progress ────────────────────────────
download_file() {
    local url="$1" path="$2" label="$3"
    local dir; dir=$(dirname "$path")
    mkdir -p "$dir"
    if [ -f "$path" ]; then
        local size; size=$(stat -c%s "$path" 2>/dev/null || stat -f%z "$path" 2>/dev/null || echo 0)
        if [ "$size" -gt 1000000 ]; then
            ok "$label already exists ($(( size / 1048576 )) MB)"
            return 0
        fi
    fi
    echo -ne " ${CYAN}↓${RESET} Downloading ${BOLD}$label${RESET}...\n"
    wget -c --show-progress "$url" -O "$path" || {
        echo -ne "\r ${RED}✖${RESET} Download failed: $label\n"
        return 1
    }
    local size2; size2=$(stat -c%s "$path" 2>/dev/null || stat -f%z "$path" 2>/dev/null || echo 0)
    if [ "$size2" -gt 0 ]; then
        echo -ne "\r ${GREEN}✔${RESET} $label — $(( size2 / 1048576 )) MB downloaded\n"
    fi
    return 0
}

verify_gguf() {
    local path="$1"
    [ -f "$path" ] || return 1
    local magic
    magic=$(head -c 4 "$path" 2>/dev/null || true)
    [ "$magic" = "GGUF" ] && return 0
    return 1
}

build_spinner() {
    local pid=$1 msg="$2"
    local spin='⣷⣯⣟⡿⢿⣻⣽⣾'
    local i=0
    while kill -0 "$pid" 2>/dev/null; do
        printf "\r ${CYAN}%s${RESET} %s" "${spin:$i:1}" "$msg"
        i=$(( (i + 1) % 8 ))
        sleep .3
    done
    printf "\r${DIM}  Done${RESET}  %s\n" "$msg"
}

# ── Paths ───────────────────────────────────────
REPO_URL="https://github.com/joelagdam/nermana_ai.git"
NERMANA_DIR="$HOME/nermana"
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
CONFIG_FILE="$NERMANA_DIR/.config"
LOG_FILE="$NERMANA_DIR/install.log"
START_EPOCH=$(date +%s)

mkdir -p "$NERMANA_DIR"
exec 2>>"$LOG_FILE"

# ═══════════════════════════════════════════════
# 0. Compatibility check
# ═══════════════════════════════════════════════
section "Checking environment"

if [ ! -d "/data/data/com.termux" ] && [ ! -f "/data/data/com.termux/files/usr/bin/termux-info" ]; then
    warn "Not running in Termux. Some features may not work."
    [ "$(uname -s)" != "Linux" ] && fail "Unsupported OS: $(uname -s). Target: Termux / Linux."
else
    ok "Termux detected"
fi

ARCH=$(uname -m)
case "$ARCH" in
    aarch64|arm64) ok "Architecture: $ARCH";;
    armv7l|armv8l) warn "$ARCH — llama.cpp may be slow. 64-bit ARM recommended.";;
    x86_64|amd64)  ok "Architecture: $ARCH";;
    *)             warn "$ARCH — untested";;
esac

if command -v df &>/dev/null; then
    SPACE_KB=$(df "$HOME" 2>/dev/null | awk 'NR==2 {print $4}')
    if [ -n "$SPACE_KB" ] && [ "$SPACE_KB" -lt 5000000 ] 2>/dev/null; then
        warn "Low disk: ~$(( SPACE_KB / 1000 )) MB free (need ~5 GB)"
        prompt_yn "Continue anyway?" || fail "Aborted"
    else
        ok "Disk: ~$(( SPACE_KB / 1000 )) MB free"
    fi
fi

# ═══════════════════════════════════════════════
# 1. Remove old nermana (preserve models)
# ═══════════════════════════════════════════════
section "Cleaning previous install"

if [ -d "$NERMANA_DIR" ]; then
    info "Removing old files (preserving models/)..."
    shopt -s dotglob nullglob
    for item in "$NERMANA_DIR"/*; do
        name="$(basename "$item")"
        [ "$name" = "models" ] && { ok "Preserved $name/"; continue; }
        rm -rf -- "$item"
        sub "Removed $name"
    done
    for item in "$NERMANA_DIR"/.*; do
        name="$(basename "$item")"
        [ "$name" = "." ] || [ "$name" = ".." ] || [ "$name" = "models" ] && continue
        rm -rf -- "$item"
    done
    shopt -u dotglob nullglob
    ok "Cleaned"
else
    mkdir -p "$NERMANA_DIR"
fi

# ═══════════════════════════════════════════════
# 2. System packages
# ═══════════════════════════════════════════════
section "System packages"

if command -v pkg &>/dev/null; then
    info "Updating package lists..."
    pkg update -y 2>&1 | tail -1 >/dev/null && ok "Packages updated" || warn "Package update skipped"
fi

DEPS_PKG="clang cmake make git wget curl python python-pip binutils libandroid-spawn termux-tools openssl-tool ddgr"
DEPS_MISSING=""
for pkg in $DEPS_PKG; do
    if pkg list-installed 2>/dev/null | grep -qi "^${pkg}[[:space:]]" || command -v "$pkg" &>/dev/null; then
        info "$pkg"
    else
        DEPS_MISSING="$DEPS_MISSING $pkg"
    fi
done

if [ -n "$DEPS_MISSING" ]; then
    info "Installing:$DEPS_MISSING"
    pkg install -y $DEPS_MISSING 2>&1 | tail -3
fi
ok "System packages ready"

# ═══════════════════════════════════════════════
# 3. Python packages
# ═══════════════════════════════════════════════
section "Python packages"

for pkg in requests flask; do
    python3 -c "import $pkg" 2>/dev/null && info "$pkg" || {
        info "Installing $pkg..."
        pip install $pkg --break-system-packages -q 2>/dev/null || pip install $pkg -q || warn "pip $pkg failed"
    }
done

python3 -c "import telegram" 2>/dev/null && info "python-telegram-bot" || {
    info "Installing python-telegram-bot..."
    pip install "python-telegram-bot[job-queue]" --break-system-packages -q 2>/dev/null ||
        pip install "python-telegram-bot[job-queue]" -q || warn "pip python-telegram-bot failed"
}

if python3 -c "import numpy" 2>/dev/null; then
    info "numpy"
else
    if command -v pkg &>/dev/null && pkg install -y python-numpy 2>/dev/null; then
        ok "numpy (pre-compiled)"
    else
        info "Installing numpy..."
        pip install numpy --break-system-packages -q 2>/dev/null || pip install numpy -q || warn "pip numpy failed"
    fi
fi
ok "Python packages ready"

# ═══════════════════════════════════════════════
# 4. Clone / copy NERMANA
# ═══════════════════════════════════════════════
section "Installing NERMANA files"

if [ -d "$SCRIPT_DIR/.git" ] && git -C "$SCRIPT_DIR" config --get remote.origin.url 2>/dev/null | grep -q "nermana_ai"; then
    info "Copying from local repo → $NERMANA_DIR"
    for item in "$SCRIPT_DIR"/*; do
        name="$(basename "$item")"
        [ "$name" = "models" ] || [ "$name" = ".git" ] || [ "$name" = "install.sh" ] && continue
        cp -r "$item" "$NERMANA_DIR/" 2>/dev/null || true
    done
    for item in "$SCRIPT_DIR"/.[!.]*; do
        [ -e "$item" ] || continue; name="$(basename "$item")"
        [ "$name" = ".git" ] && continue
        cp -r "$item" "$NERMANA_DIR/" 2>/dev/null || true
    done
    cp "$SCRIPT_DIR/install.sh" "$NERMANA_DIR/install.sh"
    ok "Copied"

elif [ -d "$NERMANA_DIR/bot" ] && [ -f "$NERMANA_DIR/nermana_ctl.sh" ]; then
    info "Already installed — skipping clone"

else
    info "Fetching from GitHub..."
    CLONE_DIR="${NERMANA_DIR}.clone"
    rm -rf -- "$CLONE_DIR"
    GIT_OUT=$(git clone --depth=1 "$REPO_URL" "$CLONE_DIR" 2>&1) || {
        rm -rf -- "$CLONE_DIR"
        fail "Clone failed:\n$GIT_OUT\n\nmodels/ is intact at $NERMANA_DIR/models"
    }
    ok "Repository cloned"

    if [ ! -f "$CLONE_DIR/nermana_ctl.sh" ] || [ ! -d "$CLONE_DIR/bot" ] || [ ! -d "$CLONE_DIR/web" ]; then
        rm -rf -- "$CLONE_DIR"
        fail "Clone verification failed — missing essential files"
    fi

    shopt -s dotglob nullglob
    for item in "$CLONE_DIR"/*; do
        name="$(basename "$item")"
        [ "$name" = ".git" ] && continue
        cp -r "$item" "$NERMANA_DIR/" 2>/dev/null || true
    done
    for item in "$CLONE_DIR"/.[!.]*; do
        [ -e "$item" ] || continue
        name="$(basename "$item")"
        [ "$name" = ".git" ] && continue
        cp -r "$item" "$NERMANA_DIR/" 2>/dev/null || true
    done
    shopt -u dotglob nullglob
    rm -rf -- "$CLONE_DIR"
    ok "Files installed"
fi

mkdir -p "$NERMANA_DIR"/{bot,web,logs,memory/{long_term,short_term,junk,buffer,embeddings},knowledge,modules,state}

# ═══════════════════════════════════════════════
# 5. Build llama.cpp
# ═══════════════════════════════════════════════
section "Building llama.cpp"
LLAMA_DIR="$HOME/llama.cpp"
LLAMA_SERVER="$LLAMA_DIR/build/bin/llama-server"

if [ -f "$LLAMA_SERVER" ]; then
    ok "llama-server already built"
else
    if [ -d "$LLAMA_DIR" ]; then
        warn "llama.cpp dir exists — rebuilding"
        rm -rf -- "$LLAMA_DIR/build"
    else
        info "Cloning llama.cpp..."
        git clone --depth=1 https://github.com/ggerganov/llama.cpp "$LLAMA_DIR"
    fi
    cd "$LLAMA_DIR"
    info "Configuring with cmake..."
    cmake -B build -DLLAMA_BUILD_SERVER=ON -DLLAMA_BUILD_TESTS=OFF -DCMAKE_BUILD_TYPE=Release 2>&1 || fail "cmake failed"
    info "Compiling llama-server (10-30 min first time)..."
    cmake --build build --config Release --target llama-server -j2 2>&1 || {
        warn "Build had warnings — checking binary..."
    }
    if [ -f "$LLAMA_SERVER" ]; then
        ok "llama-server built ✓"
    else
        fail "Build failed. Logs: $LLAMA_DIR/build/CMakeFiles/CMakeOutput.log"
    fi
fi

# ═══════════════════════════════════════════════
# 6. Model selection
# ═══════════════════════════════════════════════
section "AI Model"

MODEL_DIR="$NERMANA_DIR/models"
mkdir -p "$MODEL_DIR"

declare -A MODELS
MODELS[1]="Phi-3.5-mini|phi-3.5-mini-instruct-Q4_K_M.gguf|https://huggingface.co/bartowski/Phi-3.5-mini-instruct-GGUF/resolve/main/Phi-3.5-mini-instruct-Q4_K_M.gguf"
MODELS[2]="Qwen2.5-3B|qwen2.5-3b-instruct-Q4_K_M.gguf|https://huggingface.co/bartowski/Qwen2.5-3B-Instruct-GGUF/resolve/main/Qwen2.5-3B-Instruct-Q4_K_M.gguf"
MODELS[3]="SmolLM2-1.7B|smollm2-1.7b-instruct-Q4_K_M.gguf|https://huggingface.co/bartowski/SmolLM2-1.7B-Instruct-GGUF/resolve/main/SmolLM2-1.7B-Instruct-Q4_K_M.gguf"

echo ""
echo -e "  ${BOLD}Available models:${RESET}"
for k in 1 2 3; do
    IFS='|' read -r name file url <<< "${MODELS[$k]}"
    path="$MODEL_DIR/$file"
    status=""
    if [ -f "$path" ]; then
        sz=$(stat -c%s "$path" 2>/dev/null || stat -f%z "$path" 2>/dev/null || echo 0)
        if [ "$sz" -gt 1000000 ]; then
            status=" ${GREEN}✔${RESET} ${DIM}$(( sz / 1048576 )) MB${RESET}"
        else
            status=" ${YELLOW}⚠${RESET} ${DIM}partial${RESET}"
        fi
    else
        status=" ${DIM}— not downloaded${RESET}"
    fi
    echo -e "    ${CYAN}$k${RESET}) ${BOLD}$name${RESET} $status"
done
echo -e "    ${CYAN}s${RESET}) ${BOLD}Skip${RESET} — keep existing model(s)"
echo ""

DEFAULT_CH=1
for k in 1 2 3; do
    IFS='|' read -r name file url <<< "${MODELS[$k]}"
    [ -f "$MODEL_DIR/$file" ] && DEFAULT_CH="$k"
done

if [ "$_QUICK_MODE" = true ]; then
    ch="$DEFAULT_CH"
    info "Quick mode — using default model"
else
    read -p "  ${CYAN}?${RESET} Choose a model to download [1-3, s]: " ch
    ch="${ch:-$DEFAULT_CH}"
fi

if [ "$ch" != "s" ] && [ "$ch" != "S" ]; then
    IFS='|' read -r ACTIVE_MODEL MODEL_FILE MODEL_URL <<< "${MODELS[$ch]:-${MODELS[1]}}"
    MODEL_PATH="$MODEL_DIR/$MODEL_FILE"

    if [ -f "$MODEL_PATH" ] && verify_gguf "$MODEL_PATH"; then
        sz=$(stat -c%s "$MODEL_PATH" 2>/dev/null || stat -f%z "$MODEL_PATH" 2>/dev/null || echo 0)
        ok "${BOLD}$ACTIVE_MODEL${RESET} already downloaded ($(( sz / 1048576 )) MB) — skipping"
    else
        info "Downloading ${BOLD}$ACTIVE_MODEL${RESET}..."
        if wget -c --show-progress "$MODEL_URL" -O "$MODEL_PATH"; then
            if verify_gguf "$MODEL_PATH"; then
                sz=$(stat -c%s "$MODEL_PATH" 2>/dev/null || stat -f%z "$MODEL_PATH" 2>/dev/null || echo 0)
                ok "${BOLD}$ACTIVE_MODEL${RESET} — $(( sz / 1048576 )) MB"
            else
                warn "Downloaded file is not a valid GGUF model — may be corrupted"
            fi
        else
            fail "Download failed for $ACTIVE_MODEL"
        fi
    fi
else
    MODEL_PATH=""
    ACTIVE_MODEL="custom"
    info "Skipping model download"
    EXISTING=$(ls "$MODEL_DIR"/*.gguf 2>/dev/null | head -1 || true)
    if [ -n "$EXISTING" ] && verify_gguf "$EXISTING"; then
        MODEL_PATH="$EXISTING"
        ACTIVE_MODEL=$(basename "$EXISTING" .gguf)
        ok "Using existing: $(basename "$EXISTING")"
    elif [ -n "$EXISTING" ]; then
        warn "Existing GGUF file may be corrupted — you may need to re-download via web UI"
    fi
fi

# ═══════════════════════════════════════════════
# 7. Embedding model
# ═══════════════════════════════════════════════
section "Embedding model"
EMBED_FILE="nomic-embed-text-v1.5.Q4_K_M.gguf"
EMBED_PATH="$NERMANA_DIR/models/embeddings/$EMBED_FILE"

download_file \
    "https://huggingface.co/nomic-ai/nomic-embed-text-v1.5-GGUF/resolve/main/nomic-embed-text-v1.5.Q4_K_M.gguf" \
    "$EMBED_PATH" \
    "nomic-embed-text (neural memory)" || warn "Embedding model unavailable — using keyword fallback"

# ═══════════════════════════════════════════════
# 8. Telegram / offline mode
# ═══════════════════════════════════════════════
section "Telegram setup"
TELEGRAM_TOKEN=""

if [ "$_QUICK_MODE" = true ]; then
    if [ -f "$CONFIG_FILE" ]; then
        EXISTING_TOKEN=$(grep "^TELEGRAM_TOKEN=" "$CONFIG_FILE" 2>/dev/null | cut -d= -f2 | tr -d '[:space:]' || true)
        if [ -n "$EXISTING_TOKEN" ]; then
            TELEGRAM_TOKEN="$EXISTING_TOKEN"
            ok "Using existing token"
        fi
    fi
    [ -z "$TELEGRAM_TOKEN" ] && info "Quick mode — offline (no Telegram)"
else
    echo ""
    echo -e "  ${BOLD}Choose mode:${RESET}"
    echo -e "    ${CYAN}1${RESET}) Telegram bot — requires token from @BotFather"
    echo -e "    ${CYAN}2${RESET}) Offline mode — web UI only"
    echo ""
    read -p "  ${CYAN}?${RESET} Mode [1-2]: " mode_ch

    if [ "$mode_ch" != "2" ]; then
        if [ -f "$CONFIG_FILE" ]; then
            EXISTING_TOKEN=$(grep "^TELEGRAM_TOKEN=" "$CONFIG_FILE" 2>/dev/null | cut -d= -f2 | tr -d '[:space:]' || true)
            if [ -n "$EXISTING_TOKEN" ] && curl -s "https://api.telegram.org/bot${EXISTING_TOKEN}/getMe" | grep -q '"ok":true'; then
                TELEGRAM_TOKEN="$EXISTING_TOKEN"
                ok "Token valid"
            fi
        fi
        if [ -z "$TELEGRAM_TOKEN" ]; then
            read -p "  ${CYAN}?${RESET} Paste your Telegram Bot Token: " TELEGRAM_TOKEN
            if [ -n "$TELEGRAM_TOKEN" ]; then
                info "Validating..."
                curl -s "https://api.telegram.org/bot${TELEGRAM_TOKEN}/getMe" | grep -q '"ok":true' \
                    && ok "Token validated" \
                    || warn "Validation failed — you can edit .config later"
            fi
        fi
    fi
    [ -z "$TELEGRAM_TOKEN" ] && info "Offline mode — use web UI at http://127.0.0.1:5000"
fi

# ═══════════════════════════════════════════════
# 9. Write config
# ═══════════════════════════════════════════════
section "Configuration"

case "$ACTIVE_MODEL" in
    *Phi*) CFG_CTX=4096 ;;
    *Qwen*) CFG_CTX=8192 ;;
    *) CFG_CTX=4096 ;;
esac

THREADS=$(nproc 2>/dev/null || echo 2)
[ "$THREADS" -gt 1 ] && THREADS=$((THREADS - 1))

cat > "$CONFIG_FILE" << CFGEOF
TELEGRAM_TOKEN=$TELEGRAM_TOKEN
ENGINE=llamacpp
LLAMA_HOST=127.0.0.1
LLAMA_PORT=8080
LLAMA_EMBED_PORT=8081
LLAMA_THREADS=$THREADS
LLAMA_CONTEXT=$CFG_CTX
LLAMA_BATCH=256
ACTIVE_MODEL="$ACTIVE_MODEL"
LLAMA_MODEL_PATH=$MODEL_PATH
EMBEDDING_MODEL_PATH=$EMBED_PATH
TEMPERATURE=0.7
REPETITION_PENALTY=1.15
MAIN_MAX_TOKENS=400
MEMORY_MAX_TOKENS=320
MEMORY_EVAL_INTERVAL=5
BUFFER_WINDOW=20
LONG_TERM_SCORE_MIN=7
DEFAULT_CITY="Davao City"
PROACTIVITY_LEVEL=1
EXEC_WHITELIST=ls,pwd,df,du,whoami,uptime,date,termux-battery-status,termux-wifi-connectioninfo,free,uname
SEARCH_RESULTS=3
CONFIRM_TIMEOUT=120
IDLE_SLEEP_MINUTES=15
WAKE_TIMEOUT_SECONDS=150
SEMANTIC_MEMORY_ENABLED=true
CFGEOF
ok "Config written"

# ═══════════════════════════════════════════════
# 10. Final setup
# ═══════════════════════════════════════════════
section "Final setup"
chmod +x "$NERMANA_DIR/nermana_ctl.sh"

if ! grep -q "nermana_ctl" "$HOME/.bashrc" 2>/dev/null; then
    echo 'export PATH="$HOME/nermana:$PATH"' >> "$HOME/.bashrc"
    echo 'alias nermana="$HOME/nermana/nermana_ctl.sh"' >> "$HOME/.bashrc"
    ok "Added nermana command to ~/.bashrc"
else
    ok "nermana command already in ~/.bashrc"
fi
source "$HOME/.bashrc" 2>/dev/null || true

ELAPSED=$(( $(date +%s) - START_EPOCH ))

section "Install complete (${ELAPSED}s)"
echo ""
echo -e "  ${BOLD}NERMANA v4.7.3${RESET} installed to ${CYAN}$NERMANA_DIR${RESET}"
echo -e "  ${DIM}Repository: $REPO_URL${RESET}"
echo ""
echo -e "  ${BOLD}Quick start:${RESET}"
echo ""
echo -e "  ${YELLOW}➜${RESET} Run this first (one time only):"
echo -e "      ${BOLD}source ~/.bashrc${RESET}"
echo ""
echo -e "  ${YELLOW}➜${RESET} Then start:"
if [ -n "$TELEGRAM_TOKEN" ]; then
    echo -e "      ${CYAN}nermana start${RESET}     LLM servers + Telegram bot"
fi
echo -e "      ${CYAN}nermana web${RESET}       Web dashboard → http://127.0.0.1:5000"
echo -e "      ${CYAN}nermana status${RESET}    Check services"
echo -e "      ${CYAN}nermana stop${RESET}      Stop everything"
echo -e "      ${CYAN}nermana reset${RESET}     Clear memories"
echo ""
echo -e "  ${DIM}Or without alias: bash ~/nermana/nermana_ctl.sh start${RESET}"
echo ""

if [ "$_QUICK_MODE" = false ]; then
    if prompt_yn "Start NERMANA now?"; then
        echo ""
        if command -v nermana &>/dev/null; then
            nermana start
        else
            bash "$NERMANA_DIR/nermana_ctl.sh" start
        fi
        echo ""
        if [ -n "$TELEGRAM_TOKEN" ]; then
            ok "Bot + servers launching"
        else
            ok "LLM servers starting — open http://127.0.0.1:5000"
        fi
    else
        info "Run later: source ~/.bashrc && nermana start"
    fi
fi
echo ""
