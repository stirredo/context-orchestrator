#!/bin/bash
# enable-gemini-pipeline.sh
#
# One-shot activator for the full Gemini-backed pipeline:
#   - Gemini audio transcription (meeting-capture)
#   - Gemini embeddings + Gemini Flash rerank (context-orchestrator)
#   - User-curated proper-noun corrections at chunk time
#
# Idempotent. Safe to re-run. Skips steps that are already done.
#
# Prereqs (script will validate):
#   - context-orchestrator and meeting-capture both checked out at expected paths
#   - Google API key at $GOOGLE_API_KEY, $GEMINI_API_KEY, or
#     ~/.config/google/key (mode 600)
#   - Chroma daemon already installed via setup.sh (we just toggle the
#     embedding model + reindex)
#
# Defaults to ALL features on. Disable individual features with flags:
#   --no-gemini-audio           keep mlx-whisper as transcriber
#   --no-gemini-embed           keep current embedding model (no reindex)
#   --no-gemini-rerank          don't enable LLM rerank
#   --no-corrections            don't install corrections file
#   --dry-run                   print what would happen, change nothing

set -e

# --- paths ---
# Override via env vars when checkout dirs differ from the defaults below
# (e.g. CONTEXT_ORCH=$HOME/tasks/vector_databases_experiments).
CONTEXT_ORCH="${CONTEXT_ORCH:-$HOME/tasks/context-orchestrator}"
MEETING_CAPTURE="${MEETING_CAPTURE:-$HOME/src/meeting-capture}"
GEMINI_KEY_FILE="$HOME/.config/google/key"
CONFIG_DIR="$HOME/.config/context-orchestrator"
CORRECTIONS_DST="$CONFIG_DIR/corrections.json"
CORRECTIONS_SRC_CANDIDATES=(
    "$HOME/eval-experiments/corrections-cache.json"
)

WATCHER_PLIST="$HOME/Library/LaunchAgents/com.contorch.transcript-watcher.plist"
MEETING_PLIST="$HOME/Library/LaunchAgents/com.contorch.meeting-capture.plist"
CHROMA_PLIST="$HOME/Library/LaunchAgents/com.contorch.context-orchestrator-chroma.plist"

CHROMA_DIR="$HOME/.context-orchestrator/chroma"
WATCHER_STATE="$HOME/.context-orchestrator/watcher_state.json"

# --- flags ---
ENABLE_AUDIO=1
ENABLE_EMBED=1
ENABLE_RERANK=1
ENABLE_CORRECTIONS=1
DRY_RUN=0

for arg in "$@"; do
    case "$arg" in
        --no-gemini-audio)   ENABLE_AUDIO=0 ;;
        --no-gemini-embed)   ENABLE_EMBED=0 ;;
        --no-gemini-rerank)  ENABLE_RERANK=0 ;;
        --no-corrections)    ENABLE_CORRECTIONS=0 ;;
        --dry-run)           DRY_RUN=1 ;;
        -h|--help)
            sed -n '2,30p' "$0"
            exit 0
            ;;
        *) echo "unknown arg: $arg" >&2; exit 1 ;;
    esac
done

run() {
    if [ "$DRY_RUN" = 1 ]; then
        echo "  [dry-run] would: $*"
    else
        echo "  > $*"
        eval "$@"
    fi
}

ok()   { echo "  ✓ $1"; }
warn() { echo "  ⚠ $1"; }
fail() { echo "  ✗ $1"; }

echo "===== enable-gemini-pipeline.sh ====="
echo "  audio:        $([ $ENABLE_AUDIO   = 1 ] && echo on || echo off)"
echo "  embeddings:   $([ $ENABLE_EMBED   = 1 ] && echo on || echo off)"
echo "  rerank:       $([ $ENABLE_RERANK  = 1 ] && echo on || echo off)"
echo "  corrections:  $([ $ENABLE_CORRECTIONS = 1 ] && echo on || echo off)"
echo "  dry-run:      $([ $DRY_RUN = 1 ] && echo yes || echo no)"
echo ""

# --- prereq validation ---
echo "[1/7] Validating prereqs..."
if [ ! -d "$CONTEXT_ORCH" ]; then
    fail "context-orchestrator not at $CONTEXT_ORCH"; exit 1
fi
if [ ! -x "$CONTEXT_ORCH/.venv/bin/python" ]; then
    fail "$CONTEXT_ORCH/.venv missing — run setup.sh first"; exit 1
fi
ok "context-orchestrator at $CONTEXT_ORCH"

if [ "$ENABLE_AUDIO" = 1 ]; then
    if [ ! -d "$MEETING_CAPTURE" ]; then
        fail "meeting-capture not at $MEETING_CAPTURE"; exit 1
    fi
    if [ ! -x "$MEETING_CAPTURE/.venv/bin/python" ]; then
        fail "$MEETING_CAPTURE/.venv missing — run setup.sh first"; exit 1
    fi
    ok "meeting-capture at $MEETING_CAPTURE"
fi

NEED_KEY=0
[ "$ENABLE_AUDIO"  = 1 ] && NEED_KEY=1
[ "$ENABLE_EMBED"  = 1 ] && NEED_KEY=1
[ "$ENABLE_RERANK" = 1 ] && NEED_KEY=1

if [ "$NEED_KEY" = 1 ]; then
    HAVE_KEY=0
    if [ -n "$GOOGLE_API_KEY" ] || [ -n "$GEMINI_API_KEY" ]; then
        HAVE_KEY=1
        ok "Gemini API key in env"
    elif [ -f "$GEMINI_KEY_FILE" ]; then
        HAVE_KEY=1
        ok "Gemini API key at $GEMINI_KEY_FILE"
        # Verify mode 600
        MODE=$(stat -f "%A" "$GEMINI_KEY_FILE" 2>/dev/null)
        if [ "$MODE" != "600" ] && [ "$MODE" != "400" ]; then
            warn "Gemini key file mode is $MODE — recommend 600"
            run "chmod 600 \"$GEMINI_KEY_FILE\""
        fi
    fi
    if [ "$HAVE_KEY" = 0 ]; then
        fail "Gemini API key required. Set GOOGLE_API_KEY or write to $GEMINI_KEY_FILE (mode 600)."
        echo ""
        echo "  Get a key at https://aistudio.google.com/apikey, then:"
        echo "    mkdir -p ~/.config/google && chmod 700 ~/.config/google"
        echo "    echo 'AIza...' > $GEMINI_KEY_FILE"
        echo "    chmod 600 $GEMINI_KEY_FILE"
        exit 1
    fi
fi

# --- install optional extras ---
echo ""
echo "[2/7] Installing optional Python extras..."
PIP_INDEX="https://pypi.org/simple"

NEED_GEMINI_PKG=0
[ "$ENABLE_EMBED" = 1 ] && NEED_GEMINI_PKG=1
[ "$ENABLE_RERANK" = 1 ] && NEED_GEMINI_PKG=1

if [ "$NEED_GEMINI_PKG" = 1 ]; then
    if "$CONTEXT_ORCH/.venv/bin/python" -c "from google import genai" 2>/dev/null; then
        ok "context-orchestrator: google-genai already installed"
    else
        run "$CONTEXT_ORCH/.venv/bin/pip install --index-url $PIP_INDEX --quiet -e \"$CONTEXT_ORCH[embeddings-gemini]\""
        ok "context-orchestrator: [embeddings-gemini] installed"
    fi
fi

if [ "$ENABLE_EMBED" = 1 ]; then
    if "$CONTEXT_ORCH/.venv/bin/python" -c "import sentence_transformers" 2>/dev/null; then
        ok "context-orchestrator: sentence-transformers already installed"
    else
        # nomic fallback path needs sentence-transformers; we don't strictly need
        # it for the Gemini path, but useful to have for fallback.
        warn "sentence-transformers not installed — Gemini embeddings will work but local fallback won't"
    fi
fi

if [ "$ENABLE_AUDIO" = 1 ]; then
    if "$MEETING_CAPTURE/.venv/bin/python" -c "from google import genai" 2>/dev/null; then
        ok "meeting-capture: google-genai already installed"
    else
        run "$MEETING_CAPTURE/.venv/bin/pip install --index-url $PIP_INDEX --quiet -e \"$MEETING_CAPTURE[gemini]\""
        ok "meeting-capture: [gemini] installed"
    fi
fi

# --- corrections file ---
echo ""
echo "[3/7] Setting up corrections file..."
if [ "$ENABLE_CORRECTIONS" = 1 ]; then
    if [ -f "$CORRECTIONS_DST" ]; then
        ok "corrections already at $CORRECTIONS_DST"
    else
        SRC=""
        for cand in "${CORRECTIONS_SRC_CANDIDATES[@]}"; do
            if [ -f "$cand" ]; then
                SRC="$cand"; break
            fi
        done
        if [ -z "$SRC" ]; then
            warn "no corrections source found — creating empty file (corrections will no-op until you populate it)"
            run "mkdir -p \"$CONFIG_DIR\""
            run "echo '{\"corrections\": {}}' > \"$CORRECTIONS_DST\""
        else
            run "mkdir -p \"$CONFIG_DIR\""
            run "cp \"$SRC\" \"$CORRECTIONS_DST\""
            ok "copied $SRC → $CORRECTIONS_DST"
        fi
    fi
else
    ok "skipped (--no-corrections)"
fi

# --- env vars in launchd plists ---
echo ""
echo "[4/7] Wiring env vars into launchd plists + MCP config..."

set_mcp_env_var() {
    local mcp_json="$1" key="$2" value="$3"
    if [ ! -f "$mcp_json" ]; then
        warn "  $mcp_json missing — skipping MCP env wiring"
        return
    fi
    if [ "$DRY_RUN" = 1 ]; then
        echo "  [dry-run] would set $key=$value in $mcp_json (context-orchestrator.env)"
        return
    fi
    "$CONTEXT_ORCH/.venv/bin/python" - "$mcp_json" "$key" "$value" <<'PY'
import json, sys
path, key, value = sys.argv[1], sys.argv[2], sys.argv[3]
with open(path) as f:
    cfg = json.load(f)
env = cfg.setdefault("mcpServers", {}).setdefault("context-orchestrator", {}).setdefault("env", {})
if env.get(key) == value:
    sys.exit(0)
env[key] = value
with open(path, "w") as f:
    json.dump(cfg, f, indent=2)
    f.write("\n")
PY
    echo "  > set $key in $mcp_json (context-orchestrator.env)"
}

set_plist_env_var() {
    local plist="$1" key="$2" value="$3"
    if [ ! -f "$plist" ]; then
        warn "  $plist missing — skipping (run repo's setup.sh to install)"
        return
    fi
    if [ "$DRY_RUN" = 1 ]; then
        echo "  [dry-run] would set $key=$value in $plist"
        return
    fi
    # Use plutil to add/update the key under EnvironmentVariables.
    # First ensure EnvironmentVariables dict exists.
    /usr/bin/plutil -extract EnvironmentVariables raw "$plist" >/dev/null 2>&1 || \
        /usr/bin/plutil -insert EnvironmentVariables -dictionary "$plist"
    # Try to update; if it doesn't exist, insert.
    if /usr/bin/plutil -extract "EnvironmentVariables.$key" raw "$plist" >/dev/null 2>&1; then
        /usr/bin/plutil -replace "EnvironmentVariables.$key" -string "$value" "$plist"
    else
        /usr/bin/plutil -insert "EnvironmentVariables.$key" -string "$value" "$plist"
    fi
    echo "  > set $key in $plist"
}

# Read API key once for inlining into plists (env vars don't inherit from
# user shell into launchd, so we either inline or rely on the file).
API_KEY=""
if [ -n "$GOOGLE_API_KEY" ]; then
    API_KEY="$GOOGLE_API_KEY"
elif [ -n "$GEMINI_API_KEY" ]; then
    API_KEY="$GEMINI_API_KEY"
elif [ -f "$GEMINI_KEY_FILE" ]; then
    API_KEY="$(cat "$GEMINI_KEY_FILE")"
fi

if [ "$ENABLE_AUDIO" = 1 ]; then
    set_plist_env_var "$MEETING_PLIST" MEETING_CAPTURE_TRANSCRIBER gemini
    [ -n "$API_KEY" ] && set_plist_env_var "$MEETING_PLIST" GOOGLE_API_KEY "$API_KEY"
fi
if [ "$ENABLE_EMBED" = 1 ]; then
    set_plist_env_var "$WATCHER_PLIST" CO_EMBEDDING_MODEL gemini-embedding-001
fi
if [ "$ENABLE_EMBED" = 1 ] || [ "$ENABLE_RERANK" = 1 ]; then
    [ -n "$API_KEY" ] && set_plist_env_var "$WATCHER_PLIST" GOOGLE_API_KEY "$API_KEY"
fi

# MCP server: spawned by Claude Code from .mcp.json, does NOT inherit shell
# env. If we don't wire CO_EMBEDDING_MODEL into the json's env block, the
# server falls back to local 384d embeddings and crashes on first upsert
# against the 3072d Gemini collection. API key is read from
# ~/.config/google/key by the server, so no secret goes into .mcp.json.
MCP_JSON="$CONTEXT_ORCH/.mcp.json"
if [ "$ENABLE_EMBED" = 1 ]; then
    set_mcp_env_var "$MCP_JSON" CO_EMBEDDING_MODEL gemini-embedding-001
fi
if [ "$ENABLE_RERANK" = 1 ]; then
    set_mcp_env_var "$MCP_JSON" CO_RERANK_MODEL gemini-flash-latest
fi

# --- chroma wipe (only if switching embedding dim) ---
# Note: we run the daemon health-check even if $CHROMA_DIR doesn't exist —
# the daemon may be running with stale FDs from a prior aborted activation
# that moved the dir aside. We can't tell from the filesystem alone.
echo ""
echo "[5/7] Chroma collection check..."
if [ "$ENABLE_EMBED" = 1 ]; then
    # Detect current embedding dim by querying the daemon
    CURRENT_DIM=$("$CONTEXT_ORCH/.venv/bin/python" -c "
import chromadb, sys
try:
    c = chromadb.HttpClient(host='127.0.0.1', port=8765)
    coll = c.get_collection('context')
    s = coll.get(limit=1, include=['embeddings'])
    embs = s.get('embeddings')
    if embs is not None and len(embs) > 0:
        print(len(embs[0]))
    else:
        print(0)
except Exception:
    print(-1)
" 2>/dev/null | tail -1)
    if [ "$CURRENT_DIM" = "3072" ]; then
        ok "Chroma already on Gemini embeddings (3072d) — no reindex needed"
    elif [ "$CURRENT_DIM" = "0" ]; then
        ok "Chroma empty — fresh state, no reindex needed"
    elif [ "$CURRENT_DIM" = "-1" ]; then
        # Daemon unreachable. Could be down, or wedged with stale FDs from
        # a prior aborted run that moved the data dir out from under it.
        # Bounce it so it comes up clean.
        warn "Chroma daemon unreachable — restarting to clear any stale state"
        if [ -f "$CHROMA_PLIST" ]; then
            run "launchctl unload \"$CHROMA_PLIST\" 2>/dev/null || true"
            run "launchctl load \"$CHROMA_PLIST\""
            ok "chroma daemon restarted"
            sleep 3
        else
            warn "chroma plist not found at $CHROMA_PLIST — daemon may need manual restart"
        fi
    else
        BACKUP="${CHROMA_DIR}.bak-$(date +%Y%m%d-%H%M%S)"
        warn "Chroma is ${CURRENT_DIM}d — Gemini needs 3072d. Will backup + reindex."
        run "launchctl unload \"$WATCHER_PLIST\" 2>/dev/null || true"
        run "mv \"$CHROMA_DIR\" \"$BACKUP\""
        run "rm -f \"$WATCHER_STATE\""
        ok "backed up old chroma to $BACKUP"
        # The chroma daemon caches collection metadata (incl. embedding-fn name)
        # in memory. After wiping the on-disk dir we must restart it, otherwise
        # the watcher's first add() conflicts with the stale persisted "default"
        # embedding-fn and crash-loops.
        if [ -f "$CHROMA_PLIST" ]; then
            run "launchctl unload \"$CHROMA_PLIST\" 2>/dev/null || true"
            run "launchctl load \"$CHROMA_PLIST\""
            ok "chroma daemon restarted with fresh data dir"
            sleep 3
        else
            warn "chroma plist not found at $CHROMA_PLIST — daemon may need manual restart"
        fi
    fi
else
    ok "skipped (--no-gemini-embed)"
fi

# --- restart daemons ---
echo ""
echo "[6/7] Restarting daemons to pick up new env + code..."
if [ "$ENABLE_EMBED" = 1 ] || [ "$ENABLE_CORRECTIONS" = 1 ]; then
    if [ -f "$WATCHER_PLIST" ]; then
        run "launchctl unload \"$WATCHER_PLIST\" 2>/dev/null || true"
        run "launchctl load \"$WATCHER_PLIST\""
        ok "transcript-watcher restarted"
    fi
fi
if [ "$ENABLE_AUDIO" = 1 ]; then
    if [ -f "$MEETING_PLIST" ]; then
        run "launchctl unload \"$MEETING_PLIST\" 2>/dev/null || true"
        run "launchctl load \"$MEETING_PLIST\""
        ok "meeting-capture restarted"
    fi
fi

# --- next steps for user ---
echo ""
echo "[7/7] Manual steps remaining (cannot be done from script)..."
echo ""
echo "  1. Reload the MCP server in Claude Code so it picks up new search code:"
echo "       /mcp"
echo ""
echo "  2. Add to your shell profile (so MCP server inherits at next restart):"
if [ "$ENABLE_RERANK" = 1 ]; then
    echo "       export CO_RERANK_MODEL=gemini-flash-latest"
fi
if [ "$ENABLE_EMBED" = 1 ]; then
    echo "       export CO_EMBEDDING_MODEL=gemini-embedding-001"
fi
if [ "$NEED_KEY" = 1 ] && [ -z "$GOOGLE_API_KEY" ]; then
    echo "       export GOOGLE_API_KEY=\"\$(cat $GEMINI_KEY_FILE)\""
fi
echo ""
if [ "$ENABLE_EMBED" = 1 ]; then
    WAIT_NOTE="(reindex with Gemini embeddings will take ~30-90s)"
else
    WAIT_NOTE=""
fi
echo "  3. Wait for transcript-watcher to finish reindexing $WAIT_NOTE:"
echo "       tail -f ~/.context-orchestrator/watcher.log"
echo ""
echo "  4. Verify everything is working:"
echo "       $CONTEXT_ORCH/.venv/bin/transcript-watcher doctor"
echo ""
echo "Done."
