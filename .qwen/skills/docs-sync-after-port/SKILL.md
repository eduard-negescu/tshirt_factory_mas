---
name: docs-sync-after-port
description: After a major architectural port or refactor, systematically update both QWEN.md (AI-facing) and README.md (human-facing) to reflect the new architecture — covering structure, tech stack, config, run instructions, and conventions.
source: auto-skill
extracted_at: '2026-06-04T15:30:00.000Z'
---

# Sync Documentation After an Architectural Port

Use this when you've completed a significant architectural change (porting orchestration from X to Y, ripping out a framework, adding a new persistence layer) and need to update project documentation so both AI agents and humans have accurate context.

## Step 1 — Identify what changed architecturally

Make a bullet list of the deltas between old and new architecture. Typical categories:

- **Orchestration**: new framework, new control flow pattern
- **State management**: where state lives now, what manages it
- **Persistence**: new database, new checkpointing mechanism
- **New directories/modules**: what was added, what was removed or shrunk
- **New dependencies**: packages added to pyproject.toml / requirements
- **New config variables**: env vars or settings added
- **New runtime prerequisites**: services that must be running (Docker, databases)
- **New coding conventions**: patterns for new module types (graph nodes, pipelines)

## Step 2 — Audit existing docs against current code

Read both QWEN.md and README.md side-by-side with the current code. For each section, ask:

| Section | What to check |
|---|---|
| Title/overview | Does it mention the new orchestration/persistence? |
| Tech Stack | Are new dependencies listed? New infrastructure components? |
| Project Structure | Is the new directory present? Old directories renamed/annotated? |
| Architecture | Does it describe the new control flow, not the old one? Diagram accurate? |
| Configuration | Are new env vars in the table? |
| Running | Do commands include new prerequisites (docker compose up, etc.)? |
| Development Conventions | Are new patterns documented (graph nodes, pipelines, configurable)? |

**Why:** Documentation drifts after ports. The AI agent relies on QWEN.md for context — stale architecture descriptions cause it to reason about code that no longer exists.

## Step 3 — Update QWEN.md first (AI-facing)

QWEN.md is the AI's source of truth. Update it completely before touching README.md:

1. **Opening paragraph**: add the new orchestration/persistence technology
2. **Tech Stack**: add new dependencies and infrastructure (Docker, databases)
3. **Project Structure**: add new directory with brief annotations per file
4. **Architecture**: rewrite to describe the new pattern. If a graph/state-machine, include a flow diagram. Mention what was replaced.
5. **Configuration**: add new variables to the table
6. **Running**: add new prerequisite steps
7. **Development Conventions**: add patterns for new module types (graph nodes, stateless pipelines, configurable passing)

Keep it under ~200 lines. Summarize, don't paste code.

## Step 4 — Update README.md (human-facing)

Mirror the same changes but in human-readable form:

1. **Opening paragraph**: same technology mention
2. **Architecture**: update the ASCII diagram. Rewrite bullet points to describe new components.
3. **Tech Stack table**: add new rows (Orchestration, Persistence)
4. **Prerequisites**: add new requirements (Docker, database)
5. **Configuration table**: add new variables
6. **Run commands**: add new setup step
7. **Project Structure**: add new directory, update annotations

README can be slightly longer than QWEN.md since it's for humans.

## Step 5 — Verify consistency

Spot-check that both docs agree on:
- All mentioned file paths exist
- All commands are runnable
- All config variables match the .env.example
- The architecture description matches the actual control flow
- No references remain to the old architecture (unless intentionally documenting the migration)

## Anti-patterns

- Don't mention both old and new architecture in the docs — pick the current one. Migration notes belong in commit messages, not reference docs.
- Don't leave "simulation loop" in the description when it's now a state graph.
- Don't forget to update the ASCII diagram — a wrong diagram is worse than no diagram.
