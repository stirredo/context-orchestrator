#!/usr/bin/env bash
# install-claude-context.sh — wire Claude Code's UserPromptSubmit hook so it
# auto-loads context-orchestrator search results + git context on every
# prompt, and append the proactive-search guidance to ~/.claude/CLAUDE.md.
#
# Idempotent. Safe to re-run after `git pull`.
#
# Usage:
#   ./install-claude-context.sh                # copy hook (default)
#   ./install-claude-context.sh --symlink      # symlink hook for live updates
#   ./install-claude-context.sh --uninstall    # remove what we installed

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CLAUDE_DIR="$HOME/.claude"
HOOKS_DIR="$CLAUDE_DIR/hooks"
SETTINGS="$CLAUDE_DIR/settings.json"
CLAUDE_MD="$CLAUDE_DIR/CLAUDE.md"

HOOK_SRC="$REPO_ROOT/hooks/auto-context.py"
HOOK_DST="$HOOKS_DIR/auto-context.py"
SNIPPET_SRC="$REPO_ROOT/templates/claude-md-snippet.md"

PYTHON="${PYTHON:-python3}"
MODE="copy"
ACTION="install"

for arg in "$@"; do
    case "$arg" in
        --symlink)   MODE="symlink" ;;
        --copy)      MODE="copy" ;;
        --uninstall) ACTION="uninstall" ;;
        -h|--help)
            sed -n '2,15p' "$0" | sed 's/^# \{0,1\}//'
            exit 0 ;;
        *)
            echo "unknown arg: $arg (try --help)" >&2
            exit 2 ;;
    esac
done

ok()   { printf '\033[32m✓\033[0m %s\n' "$*"; }
warn() { printf '\033[33m!\033[0m %s\n' "$*"; }
fail() { printf '\033[31m✗\033[0m %s\n' "$*" >&2; exit 1; }

# --- preflight ---
[ -f "$HOOK_SRC" ] || fail "missing $HOOK_SRC — run from the context-orchestrator repo root"
[ -f "$SNIPPET_SRC" ] || fail "missing $SNIPPET_SRC"
command -v "$PYTHON" >/dev/null || fail "$PYTHON not found in PATH"

# ============================================================ uninstall
if [ "$ACTION" = "uninstall" ]; then
    [ -e "$HOOK_DST" ] && rm -f "$HOOK_DST" && ok "removed $HOOK_DST" || ok "no hook to remove"

    if [ -f "$SETTINGS" ]; then
        "$PYTHON" - "$SETTINGS" "$HOOK_DST" <<'PY'
import json, sys
path, hook_cmd = sys.argv[1], sys.argv[2]
with open(path) as f:
    cfg = json.load(f)
hooks = cfg.get("hooks", {})
ups = hooks.get("UserPromptSubmit", [])
changed = False
for block in ups:
    block["hooks"] = [h for h in block.get("hooks", [])
                      if not (h.get("type") == "command" and h.get("command") == hook_cmd)]
    if not block["hooks"]:
        changed = True
hooks["UserPromptSubmit"] = [b for b in ups if b.get("hooks")]
if not hooks["UserPromptSubmit"]:
    hooks.pop("UserPromptSubmit", None)
if not hooks:
    cfg.pop("hooks", None)
with open(path, "w") as f:
    json.dump(cfg, f, indent=2); f.write("\n")
print("✓ stripped UserPromptSubmit hook entry from settings.json")
PY
    fi

    if [ -f "$CLAUDE_MD" ] && grep -q "BEGIN auto-context-section" "$CLAUDE_MD"; then
        # delete from BEGIN to END marker (inclusive)
        "$PYTHON" - "$CLAUDE_MD" <<'PY'
import re, sys
path = sys.argv[1]
with open(path) as f:
    text = f.read()
new = re.sub(
    r"\n?<!-- BEGIN auto-context-section.*?<!-- END auto-context-section -->\n?",
    "\n", text, flags=re.DOTALL,
)
with open(path, "w") as f:
    f.write(new)
print("✓ removed auto-context section from CLAUDE.md")
PY
    fi
    ok "uninstall complete"
    exit 0
fi

# ============================================================ install
mkdir -p "$HOOKS_DIR"

# 1. hook script
if [ "$MODE" = "symlink" ]; then
    if [ -L "$HOOK_DST" ] && [ "$(readlink "$HOOK_DST")" = "$HOOK_SRC" ]; then
        ok "hook already symlinked"
    else
        rm -f "$HOOK_DST"
        ln -s "$HOOK_SRC" "$HOOK_DST"
        ok "symlinked $HOOK_DST → $HOOK_SRC"
    fi
else
    if cmp -s "$HOOK_SRC" "$HOOK_DST" 2>/dev/null; then
        ok "hook already up to date"
    else
        rm -f "$HOOK_DST"
        cp "$HOOK_SRC" "$HOOK_DST"
        chmod +x "$HOOK_DST"
        ok "copied hook to $HOOK_DST"
    fi
fi

# 2. settings.json — merge UserPromptSubmit entry without clobbering anything else
"$PYTHON" - "$SETTINGS" "$HOOK_DST" <<'PY'
import json, os, sys
path, hook_cmd = sys.argv[1], sys.argv[2]
cfg = {}
if os.path.exists(path):
    with open(path) as f:
        try:
            cfg = json.load(f)
        except Exception as e:
            print(f"\033[31m✗ {path} is not valid JSON ({e}); back it up and re-run\033[0m", file=sys.stderr)
            sys.exit(1)

hooks = cfg.setdefault("hooks", {})
ups = hooks.setdefault("UserPromptSubmit", [])

# Find any existing entry pointing at our hook (by command path)
already = False
for block in ups:
    for h in block.get("hooks", []):
        if h.get("type") == "command" and h.get("command") == hook_cmd:
            already = True
            break
    if already:
        break

if already:
    print("✓ hook already wired in settings.json")
else:
    ups.append({
        "matcher": "*",
        "hooks": [{"type": "command", "command": hook_cmd, "timeout": 10}],
    })
    with open(path, "w") as f:
        json.dump(cfg, f, indent=2); f.write("\n")
    print(f"✓ added UserPromptSubmit hook entry to {path}")
PY

# 3. CLAUDE.md — append the snippet block once (idempotent via marker)
if [ -f "$CLAUDE_MD" ] && grep -q "BEGIN auto-context-section" "$CLAUDE_MD"; then
    ok "CLAUDE.md already has auto-context section (re-edit by hand or --uninstall + re-run to refresh)"
else
    [ -f "$CLAUDE_MD" ] || touch "$CLAUDE_MD"
    # Ensure newline before append
    [ -s "$CLAUDE_MD" ] && [ "$(tail -c1 "$CLAUDE_MD")" != "" ] && echo "" >> "$CLAUDE_MD"
    cat "$SNIPPET_SRC" >> "$CLAUDE_MD"
    ok "appended auto-context guidance to $CLAUDE_MD"
fi

# 4. Sanity check that the hook can run + locate its dependencies
if "$HOOK_DST" </dev/null >/dev/null 2>&1; then
    ok "hook runs cleanly"
else
    warn "hook returned non-zero on a probe run — check $HOOK_DST manually"
fi

cat <<EOF

Done. Restart Claude Code to pick up the hook (settings.json is read at startup).

What you got:
  • Hook:       $HOOK_DST
  • Settings:   $SETTINGS  (merged UserPromptSubmit entry)
  • Guidance:   $CLAUDE_MD (appended auto-context-section)

The hook auto-locates context-orchestrator under common paths:
  - \$CO_REPO env var (if set)
  - ~/tasks/vector_databases_experiments
  - ~/tasks/context-orchestrator
  - ~/src/context-orchestrator
  - ~/code/context-orchestrator
On a machine where the repo lives elsewhere, set CO_REPO=/path/to/repo
in your shell rc.
EOF
