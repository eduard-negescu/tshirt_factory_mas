---
name: project-analysis-qwen-md
description: Systematic methodology for analyzing an unfamiliar project directory and generating a comprehensive QWEN.md reference file. Covers initial exploration, iterative deep-dive, project-type identification, and structured output for both code and non-code projects.
source: auto-skill
extracted_at: '2026-06-02T20:47:43.345Z'
---

# Project Analysis & QWEN.md Generation

Use this methodology when asked to analyze a project directory and produce a `QWEN.md` file that serves as instructional context for future interactions.

## Phase 1 — Initial Exploration (parallel)

Make several parallel calls to build a high-level mental model:

1. **List the root directory** — get folder/file overview (limit to ~20 items if large).
2. **Glob for README/readme** — `**/*.md` at the repo root (ignore `.venv`). Read any README found.
3. **Read the build/config file** — `pyproject.toml`, `package.json`, `go.mod`, `Cargo.toml`, `CMakeLists.txt`, `Makefile`, etc. This reveals the tech stack, dependencies, and build system.
4. **Read env files** — `.env.example`, `.env`, or similar — reveals runtime configuration.

## Phase 2 — Iterative Deep Dive

Based on Phase 1 findings, explore deeper. The order below is a guide; let discoveries drive the next reads:

1. **List `src/` or source directories** to see package/module structure.
2. **Read the main entry point** (e.g., `main.py`, `index.js`, `main.go`) — reveals execution flow.
3. **List and read subdirectories** — `config/`, `models/`, `agents/`, `equipment/`, `llm/`, etc. Read all `.py`/`.js`/`.go` files in these directories (or at least the most important ones).
4. **Read remaining source files** — `bus.py`, `utils.py`, helpers, etc.

**Parallelize reads** whenever possible. Batch related files (e.g., all agents at once, all equipment at once) to reduce round-trips. Typical exploration uses 6–12 file reads total.

## Phase 3 — Identify Project Type

- **Code Project**: Has `package.json`, `requirements.txt`, `pyproject.toml`, `go.mod`, `Cargo.toml`, `build.gradle`, or a `src/` directory with source files.
- **Non-Code Project**: No code-related files — likely documentation, research, notes.

## Phase 4 — Generate QWEN.md

### For Code Projects

Structure the output with these sections:

| Section | Content |
|---|---|
| **Project Overview** | One-paragraph summary: purpose, main technologies, architecture pattern |
| **Tech Stack** | Language, package manager, build system, key dependencies |
| **Project Structure** | Directory tree with brief annotations for each file/directory |
| **Architecture** | Explain the architecture: how components interact, patterns used (e.g., MessageBus, Agent pattern, pipeline stages) |
| **Configuration** | Table of config variables, defaults, descriptions. How to set up (.env, flags, etc.) |
| **Running** | Exact commands to build/run/start, including prerequisites (e.g., "requires Ollama running locally") |
| **Testing** | How to run tests, test framework, test location conventions. If none exist, note that and suggest how to add them. |
| **Development Conventions** | Coding patterns observed: naming, logging style, model patterns, class structure, docstring conventions |

Tailor sections to what you actually found — omit sections that don't apply.

### For Non-Code Projects

| Section | Content |
|---|---|
| **Directory Overview** | Purpose and contents of the directory |
| **Key Files** | Most important files with brief explanations |
| **Usage** | How the contents are intended to be used |

## Anti-patterns to Avoid

- Don't read every file in `.venv/` or `node_modules/` — exclude vendored dependencies.
- Don't guess at build/run commands — infer them from config files (scripts in `pyproject.toml`, `Makefile` targets, etc.). If truly unclear, use a `TODO` placeholder.
- Don't paste large code blocks into QWEN.md — summarize patterns.
- Keep QWEN.md under ~200 lines; it's a reference, not a novel.
