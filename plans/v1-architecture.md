# Plan v1: Task-Aware Context Orchestration System

**Status:** Draft
**Date:** 2026-02-22
**Iteration:** 1

## Problem Statement

When working with LLMs, context must be re-provided every session. The user maintains:
- Recorded conversation transcripts (from Cluely app)
- PDFs (specs, docs)
- GitHub/GitLab repo links
- Prior LLM conversations

Each "task" has associated sources. The LLM should automatically know what context belongs to a task, retrieve it without losing information, and manage context window limits intelligently.

## Core Requirements

1. **Task-awareness**: Sources are grouped by task. Mentioning a task loads its context.
2. **Lossless storage**: Full original content is always preserved. Embeddings are an index, not a replacement.
3. **Selective loading**: A lightweight manifest (what exists + structural outline) is always in context. Full content is loaded on demand.
4. **Auto-ingestion**: File watchers detect new transcripts/PDFs/repo changes and ingest automatically.
5. **Cross-session persistence**: Task graph and source store survive context window resets.

## Architecture

```
┌─────────────────────────────────────────────────────┐
│                   YOUR INTERFACE                     │
│         (CLI / API / Chat UI / IDE Plugin)           │
└──────────────────────┬──────────────────────────────┘
                       │
┌──────────────────────▼──────────────────────────────┐
│              CONTEXT ORCHESTRATOR                    │
│                                                      │
│  - Detects which task you're working on              │
│  - Builds a "context manifest" (lightweight map)     │
│  - Decides what to expand to full content            │
│  - Manages the LLM's context window budget           │
└───┬──────────────┬───────────────┬──────────────────┘
    │              │               │
┌───▼───┐    ┌────▼────┐    ┌────▼─────┐
│ TASK  │    │ SOURCE  │    │ VECTOR   │
│ GRAPH │    │ STORE   │    │ INDEX    │
│       │    │         │    │          │
│task → │    │full text│    │embeddings│
│sources│    │of every │    │for       │
│mapping│    │document │    │semantic  │
│       │    │(lossless)│   │search    │
└───────┘    └─────────┘    └──────────┘
                 ▲
    ┌────────────┴────────────────┐
    │      INGESTION PIPELINE     │
    │                              │
    │  Watchers for:               │
    │  - Transcript folders        │
    │  - PDF drops                 │
    │  - Git repo changes          │
    │  - Conversation exports      │
    └──────────────────────────────┘
```

## Three Key Data Structures

### 1. Task Graph (lightweight, always loaded)
```
Task: "Auth Refactor"
├── sources:
│   ├── transcript: /recordings/auth-discussion-feb12.txt
│   ├── pdf: /docs/auth-spec-v2.pdf
│   ├── repo: github.com/org/backend (branch: auth-refactor)
│   └── conversation: /transcripts/claude-session-0214.json
├── created: 2026-02-12
├── status: active
└── related_tasks: ["API Redesign", "Security Audit"]
```

### 2. Source Store (full content, loaded on demand)
Each source stored with:
- Full original content (lossless)
- Structural outline (headings, function signatures, key entities)
- Chunk boundaries (for partial loading)
- Metadata (type, date, size, content hash for dedup)

### 3. Vector Index (for semantic retrieval)
- Embeddings of every chunk, tagged with task ID + source ID
- Used for lookup only, never as a replacement for original content

## How Automatic Retrieval Works

1. User mentions a task → orchestrator looks up task graph
2. Builds a **context manifest**: compact list of sources with structural outlines (~2-5K tokens)
3. LLM sees what exists without full content eating the window
4. When LLM needs detail → orchestrator loads specific chunks/files
5. Manifest always stays → LLM never forgets sources exist

## Tech Stack

| Component | Technology | Why |
|-----------|-----------|-----|
| Source Store | SQLite + filesystem | Simple, portable, lossless |
| Vector Index | ChromaDB or Qdrant | Local-first, good Python APIs |
| Embeddings | text-embedding-3-small or nomic-embed-text | Cheap, fast |
| Task Graph | SQLite (same DB) | Relational data fits naturally |
| Ingestion | Python + watchdog + PyMuPDF + GitPython | Covers all source types |
| Orchestrator | Python service with API | The brain |
| Interface | CLI first, then API | Start simple |

## Build Order

1. Source Store + Ingestion (get content in, store losslessly)
2. Vector Index (embed and index stored content)
3. Task Graph (create tasks, link sources — manual first)
4. Context Manifest Builder (generate lightweight map per task)
5. Orchestrator (decide what to load into context)
6. Auto-detection (file watchers, auto-tagging)

## Open Questions

- What's the interface? CLI tool? MCP server for Claude Code? API?
- Should tasks be explicitly created or inferred from conversation?
- How to handle source updates (e.g., new commit on a linked repo)?
- What's the embedding strategy for code vs. prose vs. transcripts?
