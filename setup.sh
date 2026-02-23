#!/bin/bash
set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
CLAUDE_MD="$HOME/.claude/CLAUDE.md"
TEMPLATE="$SCRIPT_DIR/claude-md-template.md"
MARKER="CONTEXT-ORCHESTRATOR"
PYTHON_PATH="$SCRIPT_DIR/.venv/bin/python"
SRC_PATH="$SCRIPT_DIR/src"

echo "Setting up context-orchestrator..."

# 1. Create venv and install
if [ ! -d "$SCRIPT_DIR/.venv" ]; then
    echo "Creating virtual environment..."
    python3 -m venv "$SCRIPT_DIR/.venv"
fi

echo "Installing dependencies..."
"$SCRIPT_DIR/.venv/bin/pip" install -q -e "$SCRIPT_DIR"

# 2. Update ~/.claude/CLAUDE.md (append, don't overwrite)
mkdir -p "$HOME/.claude"

if [ -f "$CLAUDE_MD" ] && grep -q "$MARKER" "$CLAUDE_MD"; then
    echo "CLAUDE.md already has context-orchestrator instructions — skipping."
else
    echo "Appending context-orchestrator instructions to $CLAUDE_MD..."
    cat "$TEMPLATE" >> "$CLAUDE_MD"
    echo "Updated $CLAUDE_MD"
fi

# 3. Register MCP server with Claude Code
if command -v claude &> /dev/null; then
    # Remove existing entry if present (idempotent)
    claude mcp remove --scope user context-orchestrator 2>/dev/null || true

    echo "Registering MCP server with Claude Code..."
    claude mcp add -t stdio -s user \
        -e "PYTHONPATH=$SRC_PATH" \
        -- context-orchestrator "$PYTHON_PATH" -m context_orchestrator.server

    echo ""
    echo "Done! Restart Claude Code and the context-orchestrator will be available."
else
    echo ""
    echo "Claude Code CLI not found. Add this manually to ~/.claude.json under \"mcpServers\":"
    echo ""
    echo "  \"context-orchestrator\": {"
    echo "    \"type\": \"stdio\","
    echo "    \"command\": \"$PYTHON_PATH\","
    echo "    \"args\": [\"-m\", \"context_orchestrator.server\"],"
    echo "    \"env\": {"
    echo "      \"PYTHONPATH\": \"$SRC_PATH\""
    echo "    }"
    echo "  }"
    echo ""
    echo "Then restart Claude Code."
fi
