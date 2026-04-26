#!/bin/bash
set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
CLAUDE_MD="$HOME/.claude/CLAUDE.md"
TEMPLATE="$SCRIPT_DIR/claude-md-template.md"
MARKER="CONTEXT-ORCHESTRATOR"
PYTHON_PATH="$SCRIPT_DIR/.venv/bin/python"
SRC_PATH="$SCRIPT_DIR/src"

INSTALL_LAUNCHD=1
for arg in "$@"; do
    case "$arg" in
        --no-launchd) INSTALL_LAUNCHD=0 ;;
        -h|--help)
            cat <<EOF
Usage: $0 [--no-launchd]

  --no-launchd    Install MCP server but skip transcript-watcher launchd
                  auto-start. Use if you want to run the watcher manually.
EOF
            exit 0
            ;;
    esac
done

echo "Setting up context-orchestrator..."

# ---------------------------------------------------------------------------
# Prereq check
# ---------------------------------------------------------------------------
PREREQS_OK=1
fail() { echo "  ✗ $1"; PREREQS_OK=0; }
ok()   { echo "  ✓ $1"; }

echo ""
echo "Checking prerequisites..."

if command -v python3 &>/dev/null; then
    PY_VERSION=$(python3 -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")')
    PY_MAJOR=$(echo "$PY_VERSION" | cut -d. -f1)
    PY_MINOR=$(echo "$PY_VERSION" | cut -d. -f2)
    if [ "$PY_MAJOR" -ge 3 ] && [ "$PY_MINOR" -ge 10 ]; then
        ok "Python $PY_VERSION"
    else
        fail "Python $PY_VERSION too old. Need 3.10+."
    fi
else
    fail "python3 not on PATH. Install via Homebrew or python.org."
fi

if command -v git &>/dev/null; then
    ok "git $(git --version | awk '{print $3}')"
else
    fail "git not on PATH. Comes with Xcode CLI tools."
fi

if command -v claude &>/dev/null; then
    ok "claude CLI on PATH"
else
    echo "  ⚠ claude CLI not on PATH. MCP registration will print manual instructions instead."
fi

if [ "$PREREQS_OK" -eq 0 ]; then
    echo ""
    echo "Prerequisites missing. Fix and re-run."
    exit 1
fi
echo ""

# ---------------------------------------------------------------------------
# 1. venv + install
# ---------------------------------------------------------------------------
if [ ! -d "$SCRIPT_DIR/.venv" ]; then
    echo "Creating virtual environment..."
    python3 -m venv "$SCRIPT_DIR/.venv"
fi

echo "Installing dependencies..."
"$SCRIPT_DIR/.venv/bin/pip" install -q --upgrade pip
"$SCRIPT_DIR/.venv/bin/pip" install -q -e "$SCRIPT_DIR"

# ---------------------------------------------------------------------------
# 2. Update ~/.claude/CLAUDE.md (append, idempotent)
# ---------------------------------------------------------------------------
mkdir -p "$HOME/.claude"

if [ -f "$CLAUDE_MD" ] && grep -q "$MARKER" "$CLAUDE_MD"; then
    echo "CLAUDE.md already has context-orchestrator instructions — skipping."
else
    echo "Appending context-orchestrator instructions to $CLAUDE_MD..."
    cat "$TEMPLATE" >> "$CLAUDE_MD"
fi

# ---------------------------------------------------------------------------
# 3. Register MCP server with Claude Code
# ---------------------------------------------------------------------------
if command -v claude &>/dev/null; then
    # Remove existing entry if present (idempotent)
    claude mcp remove --scope user context-orchestrator 2>/dev/null || true

    echo "Registering MCP server with Claude Code..."
    claude mcp add -t stdio -s user \
        -e "PYTHONPATH=$SRC_PATH" \
        -- context-orchestrator "$PYTHON_PATH" -m context_orchestrator.server
else
    cat <<EOF

Claude Code CLI not found. Add manually to ~/.claude.json under "mcpServers":

  "context-orchestrator": {
    "type": "stdio",
    "command": "$PYTHON_PATH",
    "args": ["-m", "context_orchestrator.server"],
    "env": {
      "PYTHONPATH": "$SRC_PATH"
    }
  }
EOF
fi

# ---------------------------------------------------------------------------
# 4. Auto-install transcript-watcher launchd agent
# ---------------------------------------------------------------------------
if [ "$INSTALL_LAUNCHD" -eq 1 ]; then
    echo ""
    echo "Installing transcript-watcher launchd agent..."
    "$SCRIPT_DIR/.venv/bin/transcript-watcher" install
else
    echo ""
    echo "Skipping launchd install (--no-launchd flag)."
fi

# ---------------------------------------------------------------------------
# 5. Done
# ---------------------------------------------------------------------------
cat <<EOF

Done. Diagnostic:
  $SCRIPT_DIR/.venv/bin/transcript-watcher doctor   # health check
  $SCRIPT_DIR/.venv/bin/transcript-watcher status

Restart Claude Code so the new MCP server is loaded.
EOF
