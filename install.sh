#!/data/data/com.termux/files/usr/bin/bash
# ================================================================
# NERMANA Installer v4.7.2
# Installs NERMANA on Termux with AI models, Telegram bot / offline
# Repository: https://github.com/joelagdam/nermana_ai
#
# Usage:
#   bash install.sh                          # interactive
#   bash install.sh --quick                  # skip model prompts, use defaults
# ================================================================

_QUICK_MODE=false
for _arg in "$@"; do [ "$_arg" = "--quick" ] && _QUICK_MODE=true; done

GREEN="\e[32m"; RED="\e[31m"; CYAN="\e[36m"; YELLOW="\e[33m"; BOLD="\e[1m"; DIM="\e[2m"; RESET="\e[0m"
ok()      { echo -e "${GREEN}[✓]${RESET} $1"; }
warn()    { echo -e "${YELLOW}[!]${RESET} $1"; }
fail()    { echo -e "${RED}[✗]${RESET} $1"; exit 1; }
info()    { echo -e "${CYAN}[i]${RESET} $1"; }
section() { echo -e "\n${BOLD}${CYAN}━━━ $1 ━━━${RESET}"; }
prompt_yn() { local d=$2; read -p "$1 [Y/n]: " r; [[ "$r" =~ ^[nN] ]] && return 1; return 0; }
prompt_choose() { read -p "$1 " r; echo "$r"; }

REPO_URL="https://github.com/joelagdam/nermana_ai.git"
NERMANA_DIR="$HOME/nermana"
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
CONFIG_FILE="$NERMANA_DIR/.config"
LOG_FILE="$NERMANA_DIR/install.log"
START_EPOCH=$(date +%s)

mkdir -p "$NERMANA_DIR"
exec 2>>"$LOG_FILE"  # capture stderr (append, don't overwrite in case of re-run)

# ─────────────────────────────────────────
# 0. Compatibility check
# ─────────────────────────────────────────
section "Compatibility check"

if [ ! -d "/data/data/com.termux" ] && [ ! -f "/data/data/com.termux/files/usr/bin/termux-info" ]; then
    # not Termux — warn but proceed (user may be on Linux)
    warn "Not running in Termux (Android). Some features may not work."
    if [ "$(uname -s)" != "Linux" ]; then
        fail "Unsupported OS: $(uname -s). This installer targets Termux / Linux."
    fi
else
    ok "Termux environment detected"
fi

ARCH=$(uname -m)
case "$ARCH" in
    aarch64|arm64) ok "Architecture: $ARCH (64-bit ARM)" ;;
    armv7l|armv8l) warn "Architecture: $ARCH — llama.cpp may be slow. Consider a 64-bit device." ;;
    x86_64|amd64)  ok "Architecture: $ARCH (x86_64)" ;;
    *)             warn "Architecture: $ARCH — not tested, may have issues" ;;
esac

# check disk space (need ~5GB for models + build)
if command -v df &>/dev/null; then
    SPACE_KB=$(df "$HOME" 2>/dev/null | awk 'NR==2 {print $4}')
    if [ -n "$SPACE_KB" ] && [ "$SPACE_KB" -lt 5000000 ] 2>/dev/null; then
        warn "Low disk space: ~$(( SPACE_KB / 1000 )) MB free. Need ~5 GB for models + build."
        prompt_yn "Continue anyway?" || fail "Aborted by user"
    else
        ok "Disk space: ~$(( SPACE_KB / 1000 )) MB free"
    fi
fi

# ─────────────────────────────────────────
# 1. Remove existing nermana except models
# ─────────────────────────────────────────
section "Cleaning previous installation"
if [ -d "$NERMANA_DIR" ]; then
    info "Removing old nermana files (preserving models/)..."
    for item in "$NERMANA_DIR"/*; do
        name="$(basename "$item")"
        if [ "$name" = "models" ]; then
            ok "Preserved $name/"
        else
            rm -rf "$item"
            info "Removed $name"
        fi
    done
    # also remove hidden files except models/
    for item in "$NERMANA_DIR"/.*; do
        name="$(basename "$item")"
        [ "$name" = "." ] || [ "$name" = ".." ] || [ "$name" = "models" ] && continue
        rm -rf "$item"
    done
    ok "Old installation cleaned"
else
    mkdir -p "$NERMANA_DIR"
fi

# ─────────────────────────────────────────
# 2. System update
# ─────────────────────────────────────────
section "System update"
if command -v pkg &>/dev/null; then
    pkg update -y 2>&1 | tail -1 || warn "pkg update had issues"
    ok "Package list updated"
fi

# ─────────────────────────────────────────
# 3. Dependencies
# ─────────────────────────────────────────
section "Installing dependencies"

DEPS_PKG="clang cmake make git wget curl python python-pip binutils libandroid-spawn termux-tools openssl-tool ddgr"
DEPS_MISSING=""

for pkg in $DEPS_PKG; do
    if command -v "$pkg" &>/dev/null || pkg list-installed 2>/dev/null | grep -qi "^$pkg "; then
        info "Already installed: $pkg"
    else
        DEPS_MISSING="$DEPS_MISSING $pkg"
    fi
done

if [ -n "$DEPS_MISSING" ]; then
    info "Installing:$DEPS_MISSING"
    pkg install -y $DEPS_MISSING 2>&1 | tail -3
    ok "System packages installed"
else
    ok "All system packages already present"
fi

PIP_DEPS="requests flask"
PIP_MISSING=""
for pkg in $PIP_DEPS; do
    if python3 -c "import $pkg" 2>/dev/null; then
        info "Already installed: $pkg"
    else
        PIP_MISSING="$PIP_MISSING $pkg"
    fi
done
if [ -n "$PIP_MISSING" ]; then
    pip install $PIP_MISSING --break-system-packages -q 2>&1 || pip install $PIP_MISSING -q 2>&1
    ok "Python packages installed"
fi

# Telegram bot (optional — skip if offline mode)
if python3 -c "import telegram" 2>/dev/null; then
    info "Already installed: python-telegram-bot"
else
    info "Installing python-telegram-bot..."
    pip install "python-telegram-bot[job-queue]" --break-system-packages -q 2>&1 || pip install "python-telegram-bot[job-queue]" -q 2>&1
    ok "python-telegram-bot installed"
fi

# numpy — prefer termux pre-compiled binary
if python3 -c "import numpy" 2>/dev/null; then
    info "Already installed: numpy"
else
    if command -v pkg &>/dev/null && pkg install -y python-numpy 2>/dev/null; then
        ok "numpy installed via pkg (pre-compiled)"
    else
        pip install numpy --break-system-packages -q 2>&1 || pip install numpy -q 2>&1
        ok "numpy installed via pip"
    fi
fi

# ─────────────────────────────────────────
# 4. Clone / copy NERMANA files
# ─────────────────────────────────────────
section "Installing NERMANA files"

if [ -d "$SCRIPT_DIR/.git" ] && git -C "$SCRIPT_DIR" config --get remote.origin.url 2>/dev/null | grep -q "nermana_ai"; then
    # we are inside the git repo — copy files
    info "Copying from $SCRIPT_DIR → $NERMANA_DIR"
    for item in "$SCRIPT_DIR"/*; do
        name="$(basename "$item")"
        [ "$name" = "models" ] && continue
        [ "$name" = ".git" ] && continue
        [ "$name" = "install.sh" ] && continue
        cp -r "$item" "$NERMANA_DIR/" 2>/dev/null || true
    done
    for item in "$SCRIPT_DIR"/.[!.]*; do
        [ -e "$item" ] || continue
        name="$(basename "$item")"
        [ "$name" = ".git" ] && continue
        cp -r "$item" "$NERMANA_DIR/" 2>/dev/null || true
    done
    cp "$SCRIPT_DIR/install.sh" "$NERMANA_DIR/install.sh"
    ok "Files copied from local repo"
elif [ -d "$NERMANA_DIR/bot" ] && [ -f "$NERMANA_DIR/nermana_ctl.sh" ]; then
    # already installed — skip clone
    info "NERMANA already present at $NERMANA_DIR — skipping clone"
else
    # standalone install.sh — clone from GitHub
    info "Cloning from $REPO_URL"
    info "(This may take a moment depending on your connection.)"
    # save models/ aside before clone overwrites
    if [ -d "$NERMANA_DIR/models" ]; then
        mv "$NERMANA_DIR/models" "/tmp/nermana_models_backup"
    fi
    rm -rf "$NERMANA_DIR"
    GIT_OUT=$(git clone --depth=1 "$REPO_URL" "$NERMANA_DIR" 2>&1) && ok "Repository cloned" || {
        # restore models/ before failing
        [ -d "/tmp/nermana_models_backup" ] && mv "/tmp/nermana_models_backup" "$NERMANA_DIR" 2>/dev/null
        fail "Clone failed:\n$GIT_OUT"
    }
    # restore preserved models/
    if [ -d "/tmp/nermana_models_backup" ]; then
        rm -rf "$NERMANA_DIR/models"
        mv "/tmp/nermana_models_backup" "$NERMANA_DIR/models"
        info "Preserved models/ restored"
    fi
fi

mkdir -p "$NERMANA_DIR"/{bot,web,logs,memory/{long_term,short_term,junk,buffer,embeddings},knowledge,modules,state}

# ─────────────────────────────────────────
# 5. Build llama.cpp
# ─────────────────────────────────────────
section "Building llama.cpp"
LLAMA_DIR="$HOME/llama.cpp"
LLAMA_SERVER="$LLAMA_DIR/build/bin/llama-server"

if [ -f "$LLAMA_SERVER" ]; then
    ok "llama-server already built"
else
    if [ -d "$LLAMA_DIR" ]; then
        warn "llama.cpp directory exists but no binary — rebuilding"
        rm -rf "$LLAMA_DIR/build"
    else
        info "Cloning llama.cpp..."
        git clone --depth=1 https://github.com/ggerganov/llama.cpp "$LLAMA_DIR"
    fi
    cd "$LLAMA_DIR"
    info "Configuring cmake..."
    CMAKE_FLAGS="-B build -DLLAMA_BUILD_SERVER=ON -DLLAMA_BUILD_TESTS=OFF -DCMAKE_BUILD_TYPE=Release"
    cmake $CMAKE_FLAGS 2>&1 || fail "cmake configuration failed"
    info "Building llama-server (this takes 10-30 minutes on device)..."
    info "  ↳ Be patient — this only happens once."
    cmake --build build --config Release --target llama-server -j2 2>&1 || {
        warn "Build had warnings; checking binary..."
    }
    if [ -f "$LLAMA_SERVER" ]; then
        ok "llama-server built successfully"
    else
        fail "llama-server build failed. See $LLAMA_DIR/build/CMakeFiles/CMakeOutput.log"
    fi
fi

# ─────────────────────────────────────────
# 6. Model selection
# ─────────────────────────────────────────
section "Model selection"
MODEL_DIR="$NERMANA_DIR/models"
mkdir -p "$MODEL_DIR"

EXISTING_MODEL_PATH=""
ACTIVE_MODEL=""
if [ -f "$CONFIG_FILE" ]; then
    EXISTING_MODEL_PATH=$(grep "^LLAMA_MODEL_PATH=" "$CONFIG_FILE" 2>/dev/null | cut -d= -f2 | tr -d '[:space:]')
    ACTIVE_MODEL=$(grep "^ACTIVE_MODEL=" "$CONFIG_FILE" 2>/dev/null | cut -d= -f2-)
fi

if [ -n "$EXISTING_MODEL_PATH" ] && [ -f "$EXISTING_MODEL_PATH" ]; then
    MODEL_PATH="$EXISTING_MODEL_PATH"
    ok "Existing model: $(basename "$MODEL_PATH")"
    prompt_yn "Use this model?" || EXISTING_MODEL_PATH=""
fi

if [ -z "$EXISTING_MODEL_PATH" ] || [ ! -f "$EXISTING_MODEL_PATH" ]; then
    echo ""
    echo "  ${BOLD}Select a GGUF model:${RESET}"
    echo "    1) Phi-3.5-mini-instruct Q4_K_M (2.5GB) — best reasoning"
    echo "    2) Qwen2.5-3B-Instruct Q4_K_M   (2.0GB) — fast & strong"
    echo "    3) SmolLM2-1.7B Q4_K_M           (1.0GB) — fastest, weak devices"
    echo "    4) Skip download (use existing model file)"
    echo ""
    read -p "  Choice [1-4]: " ch
    case $ch in
        2) MODEL_FILE="qwen2.5-3b-instruct-Q4_K_M.gguf"
           MODEL_URL="https://huggingface.co/bartowski/Qwen2.5-3B-Instruct-GGUF/resolve/main/Qwen2.5-3B-Instruct-Q4_K_M.gguf"
           ACTIVE_MODEL="Qwen2.5-3B" ;;
        3) MODEL_FILE="smollm2-1.7b-instruct-Q4_K_M.gguf"
           MODEL_URL="https://huggingface.co/bartowski/SmolLM2-1.7B-Instruct-GGUF/resolve/main/SmolLM2-1.7B-Instruct-Q4_K_M.gguf"
           ACTIVE_MODEL="SmolLM2-1.7B" ;;
        4) MODEL_PATH=""; ACTIVE_MODEL="custom" ;;
        *) MODEL_FILE="phi-3.5-mini-instruct-Q4_K_M.gguf"
           MODEL_URL="https://huggingface.co/bartowski/Phi-3.5-mini-instruct-GGUF/resolve/main/Phi-3.5-mini-instruct-Q4_K_M.gguf"
           ACTIVE_MODEL="Phi-3.5-mini" ;;
    esac
    if [ -n "$MODEL_FILE" ]; then
        MODEL_PATH="$MODEL_DIR/$MODEL_FILE"
        if [ ! -f "$MODEL_PATH" ]; then
            info "Downloading $MODEL_FILE (~$( [ "$ch" = "3" ] && echo "1" || echo "2" )GB)..."
            wget -c --show-progress "$MODEL_URL" -O "$MODEL_PATH" || fail "Download failed"
            ok "$MODEL_FILE downloaded"
        else
            ok "$MODEL_FILE already exists"
        fi
    fi
fi

# ─────────────────────────────────────────
# 7. Embedding model
# ─────────────────────────────────────────
section "Embedding model"
EMBED_DIR="$NERMANA_DIR/models/embeddings"
mkdir -p "$EMBED_DIR"
EMBED_FILE="nomic-embed-text-v1.5.Q4_K_M.gguf"
EMBED_PATH="$EMBED_DIR/$EMBED_FILE"

if [ ! -f "$EMBED_PATH" ]; then
    info "Downloading embedding model (50 MB)..."
    wget -c --show-progress "https://huggingface.co/nomic-ai/nomic-embed-text-v1.5-GGUF/resolve/main/nomic-embed-text-v1.5.Q4_K_M.gguf" -O "$EMBED_PATH" || warn "Embedding download failed — will use keyword fallback"
fi
if [ -f "$EMBED_PATH" ]; then
    ok "Embedding model ready"
fi

# ─────────────────────────────────────────
# 8. Telegram token / offline mode
# ─────────────────────────────────────────
section "Telegram setup"
TELEGRAM_TOKEN=""

echo "  Choose mode:"
echo "    1) Telegram bot — requires token from @BotFather"
echo "    2) Offline mode — no Telegram, use web UI + direct LLM"
echo ""
read -p "  Choice [1-2]: " mode_ch

if [ "$mode_ch" = "2" ]; then
    info "Offline mode selected — Telegram bot will not start"
    info "Use: nermana web  →  http://127.0.0.1:5000"
    TELEGRAM_TOKEN=""
else
    # check existing token
    if [ -f "$CONFIG_FILE" ]; then
        EXISTING_TOKEN=$(grep "^TELEGRAM_TOKEN=" "$CONFIG_FILE" 2>/dev/null | cut -d= -f2 | tr -d '[:space:]')
        if [ -n "$EXISTING_TOKEN" ]; then
            if curl -s "https://api.telegram.org/bot${EXISTING_TOKEN}/getMe" | grep -q '"ok":true'; then
                TELEGRAM_TOKEN="$EXISTING_TOKEN"
                ok "Existing token valid"
            fi
        fi
    fi
    if [ -z "$TELEGRAM_TOKEN" ]; then
        read -p "Paste your Telegram Bot Token (from @BotFather): " TELEGRAM_TOKEN
        if [ -n "$TELEGRAM_TOKEN" ]; then
            info "Validating token..."
            if curl -s "https://api.telegram.org/bot${TELEGRAM_TOKEN}/getMe" | grep -q '"ok":true'; then
                ok "Token validated"
            else
                warn "Token validation failed — check and try again later"
                warn "You can edit $CONFIG_FILE later"
            fi
        else
            warn "No token provided — bot will not start"
            warn "Run: nermana web  →  http://127.0.0.1:5000"
        fi
    fi
fi

# ─────────────────────────────────────────
# 9. Write configuration
# ─────────────────────────────────────────
section "Configuration"

cat > "$CONFIG_FILE" << CFGEOF
TELEGRAM_TOKEN=$TELEGRAM_TOKEN
ENGINE=llamacpp
LLAMA_HOST=127.0.0.1
LLAMA_PORT=8080
LLAMA_EMBED_PORT=8081
LLAMA_THREADS=6
LLAMA_CONTEXT=4096
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
ok "Config written to $CONFIG_FILE"

# ─────────────────────────────────────────
# 10. Make ctl executable + symlink
# ─────────────────────────────────────────
section "Final setup"
chmod +x "$NERMANA_DIR/nermana_ctl.sh"

# add to PATH via ~/.bashrc if not already
if ! grep -q "nermana_ctl" "$HOME/.bashrc" 2>/dev/null; then
    echo 'export PATH="$HOME/nermana:$PATH"' >> "$HOME/.bashrc"
    echo 'alias nermana="$HOME/nermana/nermana_ctl.sh"' >> "$HOME/.bashrc"
    ok "Added nermana alias to ~/.bashrc"
else
    ok "nermana alias already in ~/.bashrc"
fi
source "$HOME/.bashrc" 2>/dev/null || true

# install completion reminder
ELAPSED=$(( $(date +%s) - START_EPOCH ))

section "Installation complete (${ELAPSED}s)"
echo ""
echo -e "  ${BOLD}NERMANA v4.7.2${RESET} installed to ${CYAN}$NERMANA_DIR${RESET}"
echo -e "  ${DIM}Repository: $REPO_URL${RESET}"
echo ""
echo -e "  ${BOLD}Quick start:${RESET}"
echo ""
echo -e "  ${YELLOW}IMPORTANT:${RESET} Run this first to activate the 'nermana' command:"
echo -e "    ${BOLD}source ~/.bashrc${RESET}"
echo ""
echo -e "  Then:"
if [ -n "$TELEGRAM_TOKEN" ]; then
    echo -e "    ${CYAN}nermana start${RESET}     Launch LLM servers + Telegram bot"
fi
echo -e "    ${CYAN}nermana web${RESET}       Start web dashboard at http://127.0.0.1:5000"
echo -e "    ${CYAN}nermana status${RESET}    Check all services"
echo -e "    ${CYAN}nermana stop${RESET}      Stop everything"
echo -e "    ${CYAN}nermana reset${RESET}     Clear all memories"
echo ""
echo -e "  Or use the full path directly:"
echo -e "    ${DIM}bash ~/nermana/nermana_ctl.sh start${RESET}"
echo ""
echo -e "  ${DIM}Edit $CONFIG_FILE to tune settings.${RESET}"
echo ""

# prompt to start now
if [ "$_QUICK_MODE" = false ]; then
    echo ""
    if prompt_yn "Start NERMANA now?"; then
        echo ""
        # Source the alias and start
        if [ -f "$HOME/.bashrc" ]; then
            source "$HOME/.bashrc" 2>/dev/null || true
        fi
        if command -v nermana &>/dev/null; then
            nermana start || warn "Start had issues — check 'nermana status'"
        else
            bash "$NERMANA_DIR/nermana_ctl.sh" start || warn "Start had issues"
        fi
        echo ""
        if [ -n "$TELEGRAM_TOKEN" ]; then
            ok "NERMANA bot + servers launching. Check: bash ~/nermana/nermana_ctl.sh status"
        else
            ok "LLM servers launching. Open http://127.0.0.1:5000 in a browser."
        fi
    else
        echo ""
        info "Run later with: source ~/.bashrc && nermana start"
    fi
fi
echo ""
