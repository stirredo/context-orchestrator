#!/bin/bash
set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
CLAUDE_MD="$HOME/.claude/CLAUDE.md"
TEMPLATE="$SCRIPT_DIR/claude-md-template.md"
MARKER="CONTEXT-ORCHESTRATOR"

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

# 3. Print MCP config for the user
PYTHON_PATH="$SCRIPT_DIR/.venv/bin/python"
SRC_PATH="$SCRIPT_DIR/src"

echo ""
echo "Done! Add this to ~/.claude/settings.json under \"mcpServers\":"
echo ""
echo "  \"context-orchestrator\": {"
echo "    \"command\": \"$PYTHON_PATH\","
echo "    \"args\": [\"-m\", \"context_orchestrator.server\"],"
echo "    \"env\": {"
echo "      \"PYTHONPATH\": \"$SRC_PATH\""
echo "    }"
echo "  }"
echo ""
echo "Then restart Claude Code."
