# NERMANA

**Neural Enhanced Responsive Modular Artificial Neural Architecture** — a Telegram bot with semantic memory, self-learning, diagnostics, and a Flask web UI, designed to run on Termux (Android) via llama.cpp.

## Architecture

```
nermana/
├── modules/               # Core shared libraries
│   ├── llm_client.py      # Priority-semaphore LLM calls (main/bg)
│   ├── memory_engine.py   # Buffer → short-term → long-term promotion
│   ├── semantic_memory.py # Embedding-based neural memory (nomic-embed)
│   ├── tools.py           # Shell command execution with whitelist
│   ├── pipeline_log.py    # Structured JSON logging pipeline
│   ├── prompt_builder.py  # System prompt assembly + force-search logic
│   ├── mood.py            # Affective state tracking
│   ├── scheduler.py       # Cron-like task scheduling
│   ├── idle_sleep.py      # Auto-sleep on inactivity
│   ├── time_context.py    # Location-aware time formatting
│   ├── confirm_state.py   # Confirmation state machine
│   ├── diagnostics.py     # Periodic health checks
│   └── auto_tuner.py      # Self-tuning parameter adjustment
├── bot/                   # Bot-layer modules
│   ├── nermana_bot.py     # Telegram bot (python-telegram-bot v20+)
│   ├── nermana_primer.py  # Relevance-gated context builder
│   ├── nermana_memory_llm.py  # Fact evaluator (imperative-only prompt)
│   ├── nermana_self_monitor.py # Quality scoring + correction detection
│   └── reflection_engine.py    # Nightly self-learning & contradiction resolution
├── web/                   # Web dashboard
│   ├── nermana_web.py     # Flask API + admin UI
│   └── index.html         # Single-page frontend
├── memory/                # On-disk storage hierarchy
│   ├── long_term/         # Scored long-term memories
│   ├── short_term/        # Recent conversation turns
│   ├── junk/              # Low-relevance discarded items
│   ├── buffer/            # Active session ring buffer
│   └── embeddings/        # Vector database (sqlite-vec)
├── knowledge/             # Extracted facts
├── models/embeddings/     # Embedding GGUF models
├── logs/                  # Runtime logs
├── state/                 # Serialized state snapshots
├── .config                # All configuration (see below)
├── nermana_ctl.sh         # Service manager (start/stop/patch/reset)
└── VERSION                # Version + feature flags
```

### Data Flow

```
Telegram ──→ nermana_bot.py ──→ nermana_primer.py (build context)
                   │
                   ├──→ llm_client.py (priority main call)
                   │       └──→ llama.cpp server (port 8080)
                   │
                   ├──→ memory_llm (bg, secondary slot)
                   │       └──→ fact evaluation → short-term
                   │
                   ├──→ semantic_memory (embedding server, port 8081)
                   │       └──→ vector search → nermana_primer
                   │
                   ├──→ tools.py (whitelisted shell commands)
                   ├──→ self_monitor (quality score per reply)
                   └──→ memory_engine (buffer → short → long promotion)
```

## Setup

### Requirements
- **Termux** (Android) from F-Droid (not Play Store)
- Storage access: `termux-setup-storage`
- 5 GB+ free space (models + build)
- Internet connection for first install

### One-command install
```bash
bash <(wget -qO- https://raw.githubusercontent.com/joelagdam/nermana_ai/main/install.sh)
```

The installer will:
1. Check compatibility (Termux, ARM arch, disk space)
2. Remove old nermana files (preserves `models/`)
3. Install packages: `clang cmake make git wget curl python python-pip binutils libandroid-spawn openssl-tool ddgr`
4. Install Python libs: `requests flask python-telegram-bot numpy`
5. Build `llama-server` from source (with `LLAMA_BUILD_SERVER=ON`)
6. Prompt for a GGUF model to download (Phi-3.5-mini / Qwen2.5-3B / SmolLM2-1.7B)
7. Download `nomic-embed-text` embedding model (50 MB)
8. Ask for Telegram bot token, or select offline mode (web UI only)
9. Write `.config` with all paths and settings

### Manual install
```bash
git clone https://github.com/joelagdam/nermana_ai.git ~/nermana
cd ~/nermana
./install.sh
```

### Configuration
Edit `~/.config` after install:
```
TELEGRAM_TOKEN=           # Bot token from @BotFather
ENGINE=llamacpp           # LLM backend
LLAMA_HOST=127.0.0.1
LLAMA_PORT=8080           # Main generation server (port 8080)
LLAMA_EMBED_PORT=8081     # Embedding server (port 8081)
LLAMA_MODEL_PATH=         # Path to GGUF model file
EMBEDDING_MODEL_PATH=     # Path to embedding GGUF (nomic-embed-text)
ACTIVE_MODEL=SmolLM2-1.7B
```

### Run
```bash
nermana start       # Launch LLM servers + Telegram bot
nermana web         # Start web dashboard at http://127.0.0.1:5000
nermana stop        # Stop everything
nermana status      # Health check
nermana reset       # Clear all memories
nermana modules     # Show patchable module list
nermana patch <module> <file>   # Hot-replace any module
```

## Modules List

| `nermana patch` name | File | Lines |
|---|---|---|
| `llm_client` | `modules/llm_client.py` | 176 |
| `memory_engine` | `modules/memory_engine.py` | 223 |
| `semantic_memory` | `modules/semantic_memory.py` | 145 |
| `tools` | `modules/tools.py` | 68 |
| `pipeline_log` | `modules/pipeline_log.py` | 79 |
| `mood` | `modules/mood.py` | 39 |
| `scheduler` | `modules/scheduler.py` | 56 |
| `idle_sleep` | `modules/idle_sleep.py` | 60 |
| `confirm_state` | `modules/confirm_state.py` | 45 |
| `time_context` | `modules/time_context.py` | 61 |
| `prompt_builder` | `modules/prompt_builder.py` | 39 |
| `diagnostics` | `modules/diagnostics.py` | 88 |
| `auto_tuner` | `modules/auto_tuner.py` | 88 |
| `primer` | `bot/nermana_primer.py` | 121 |
| `memory_llm` | `bot/nermana_memory_llm.py` | 164 |
| `bot` | `bot/nermana_bot.py` | 660 |
| `self_monitor` | `bot/nermana_self_monitor.py` | 80 |
| `reflection` | `bot/reflection_engine.py` | 139 |
| `web` | `web/nermana_web.py` | 839 |
| `html` | `web/index.html` | — |
| `ctl` | `nermana_ctl.sh` | 215 |

## Features

- **Priority semaphore**: main replies never wait for background eval
- **Neural memory**: embedding-based retrieval via separate llama.cpp instance
- **Self-learning**: nightly reflection engine resolves contradictions
- **Quality scoring**: self-monitor scores every reply for correction
- **Auto-tuner**: adjusts temperature, repetition penalty, max tokens
- **Diagnostics**: periodic health checks with Telegram alerts
- **Idle sleep**: auto-sleep after inactivity, wake on /talk
- **Tool execution**: whitelisted shell commands with confirmation
- **Hot-patching**: replace any module at runtime without full restart
