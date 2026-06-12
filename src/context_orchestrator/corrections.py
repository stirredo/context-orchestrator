"""User-curated proper-noun corrections applied at chunk time.

Whisper-large-v3-turbo and similar local models routinely mishear team-
specific proper nouns (engineer names, internal product names). Searching
for the correct spelling then misses chunks that contain the misspelled
version.

Applying corrections at the embedding layer (not in the source .md file)
keeps the original transcript intact for audit while making search work as
the user expects.

Format: a YAML or JSON file at one of:
  - $CO_CORRECTIONS_FILE if set
  - ~/.config/context-orchestrator/corrections.yaml
  - ~/.config/context-orchestrator/corrections.json

Schema (either format):

  corrections:
    <misheard form>: <canonical form>
    <misheard form>: <canonical form>
    # ...

Build your machine's correction list from your own transcripts using
`context-orchestrator-corrections suggest` (scans recent transcripts for
likely misspellings and prompts to add each one). Avoid hand-curating
unless you already know the misspellings — what each user needs depends
on their accent, microphone, vocabulary, and project domain.

The keys are matched case-insensitively with word boundaries; the values
preserve the casing the user wrote. Default behavior when no file exists:
empty dict — `apply` is a no-op.

Designed to be reloadable cheaply — `load_corrections()` rereads on each
call. Indexers can cache the dict per-batch for a small win.
"""
from __future__ import annotations

import json
import logging
import os
import re
from pathlib import Path
from typing import Optional

logger = logging.getLogger("context-orchestrator")

DEFAULT_CONFIG_DIR = Path.home() / ".config" / "context-orchestrator"
DEFAULT_FILENAMES = ("corrections.yaml", "corrections.yml", "corrections.json")
ENV_OVERRIDE = "CO_CORRECTIONS_FILE"


def _resolve_corrections_path() -> Optional[Path]:
    """Return the first existing corrections file, or None."""
    if ENV_OVERRIDE in os.environ:
        p = Path(os.environ[ENV_OVERRIDE]).expanduser()
        return p if p.exists() else None
    for name in DEFAULT_FILENAMES:
        p = DEFAULT_CONFIG_DIR / name
        if p.exists():
            return p
    return None


def _load_yaml_simple(text: str) -> dict:
    """Tiny YAML subset parser sufficient for our flat `key: value` mapping
    under a `corrections:` root. Avoids adding pyyaml as a dependency for
    such a simple format. JSON is also accepted.
    """
    text = text.strip()
    if not text:
        return {}
    # Try JSON first (it's a strict subset of YAML for our case)
    try:
        data = json.loads(text)
        if isinstance(data, dict):
            return data.get("corrections", data)
    except json.JSONDecodeError:
        pass

    # Plain key: value lines after an optional `corrections:` header
    out: dict[str, str] = {}
    in_block = False
    for raw in text.splitlines():
        line = raw.split("#", 1)[0].rstrip()
        if not line:
            continue
        stripped = line.strip()
        if stripped.lower().rstrip(":") == "corrections":
            in_block = True
            continue
        # Determine if line is indented (under corrections:) or at top level
        if not in_block and (line.startswith(" ") or line.startswith("\t")):
            continue
        if ":" not in stripped:
            continue
        key, _, value = stripped.partition(":")
        key = key.strip().strip("'\"")
        value = value.strip().strip("'\"")
        if not key or not value:
            continue
        out[key] = value
    return out


def load_corrections(path: Optional[Path] = None) -> dict[str, str]:
    """Load corrections from the configured path, or return {}.

    Lower-cased keys are returned regardless of how the user wrote them
    (matching is case-insensitive). Values preserve user casing.
    """
    p = path or _resolve_corrections_path()
    if p is None:
        return {}
    try:
        text = p.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as e:
        logger.warning(f"could not read corrections file {p}: {e}")
        return {}
    parsed = _load_yaml_simple(text)
    return {str(k).lower(): str(v) for k, v in parsed.items()}


def build_corrections_re(corrections: dict[str, str]) -> Optional[re.Pattern]:
    """Compile a single case-insensitive word-boundary regex for the
    correction map. Returns None for an empty dict (caller can skip).
    Longer keys are tried first so multi-word entries work cleanly.
    """
    if not corrections:
        return None
    pattern_parts = sorted((re.escape(k) for k in corrections), key=lambda x: -len(x))
    return re.compile(r"\b(" + "|".join(pattern_parts) + r")\b", re.IGNORECASE)


def apply(text: str, corrections: dict[str, str], pattern: Optional[re.Pattern] = None) -> str:
    """Apply the correction map to `text`. Word-bounded, case-insensitive
    matching; replacement preserves the casing the user wrote in the
    config. Returns the original text unchanged if `corrections` is empty.

    Pass `pattern` to reuse a compiled regex across many calls.
    """
    if not corrections:
        return text
    if pattern is None:
        pattern = build_corrections_re(corrections)
        if pattern is None:
            return text

    def _sub(m: re.Match) -> str:
        return corrections[m.group(0).lower()]

    return pattern.sub(_sub, text)
