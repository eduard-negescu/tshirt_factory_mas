---
name: ui-animated-factory-fastapi-htmx
description: Build an animated, illustrated factory UI for a LangGraph-based MAS simulation using FastAPI + HTMX + SSE + anime.js + Canvas + SVG — with live run and replay from Postgres checkpoints
source: auto-skill
extracted_at: '2026-06-04T14:49:57.642Z'
---

# Animated Factory UI for LangGraph MAS Simulation

Use this approach when building a visual UI for an agent-based simulation orchestrated by LangGraph. The UI is a "fun" illustrated animated factory floor — not a traditional data dashboard.

## Why this stack

| Choice | Reason |
|---|---|
| FastAPI + SSE | Python-native, same process as the graph, one-way stream matches `graph.astream()` perfectly |
| HTMX + `hx-sse` | Zero custom JS for text/DOM updates — server sends HTML fragments, HTMX swaps them |
| Canvas + anime.js | For pixel-level sprite animation (t-shirts on conveyor, station effects). HTMX can't do this |
| SVG illustrations | Assistant can generate flat-design cartoon SVGs (stations, t-shirts, icons, effects) |
| `graph.astream(stream_mode="updates")` | Yields each node's output as it happens — map to SSE events |

**User preference:** The UI should be playful and illustrated — cute station machines, t-shirt sprites with visible designs, thought bubbles for LLM reasoning, steam/fire effects for failures. NOT a table-heavy monitoring dashboard.

## Hybrid rendering: HTMX + Canvas

The page has two rendering zones:

- **HTMX zone** (DOM): queue list, stats panel, LLM reasoning text, run history. Server sends HTML snippets via SSE events prefixed with `hx-sse` headers.
- **Canvas zone** (pixel): animated factory floor with stations, conveyor belt, t-shirt sprites, particle effects. A small `app.js` listens to raw `EventSource` for animation events and drives the canvas at 60fps with `requestAnimationFrame` + anime.js tweens.

This avoids the common mistake of trying to make HTMX do animation (it can't) or writing unnecessary JS for text updates (HTMX handles that for free).

## Pages and routes

| Route | Purpose |
|---|---|
| `GET /` | Landing page — form: order count, urgent ratio, "Start" button |
| `GET /live` | Live factory view with SSE from `graph.astream()` |
| `GET /history` | List past runs from Postgres checkpoints (distinct `thread_id`s) |
| `GET /replay/{thread_id}` | Replay viewer — diffs checkpointed states, streams as SSE |
| `GET /stream` | SSE endpoint for live run |
| `GET /stream/{thread_id}` | SSE endpoint for replay |

## SSE event schema

Design events so the same schema works for both live runs and replay. Each event has a `type` field the frontend switches on:

| Event type | Key payload fields | Frontend action |
|---|---|---|
| `plan` | `queue`, `reason`, `re_plan_count` | HTMX updates queue list + scheduler reasoning |
| `routing` | `order_id`, `route`, `reason` | HTMX shows thought bubble; canvas highlights stations in route |
| `station_start` | `order_id`, `station`, `design_name` | Canvas moves t-shirt sprite to station, station → "busy" |
| `station_done` | `order_id`, `station`, `success` | Canvas shows ✅/❌; if failed → fire animation |
| `qc_verdict` | `order_id`, `verdict`, `reason`, `defect_severity` | Canvas magnifying glass hover + verdict icon |
| `order_complete` | `order_id` | Canvas t-shirt slides to packaging area |
| `stats` | `completed`, `failed`, `rework`, `iteration` | HTMX updates stats panel |
| `done` | — | Canvas overlay: "Simulation complete" |

## Replay from Postgres checkpoints

LangGraph's PostgresSaver stores checkpoints at each node boundary. For replay:

1. **List runs:** Query distinct `thread_id`s from the `checkpoints` table. Each `thread_id` is one simulation run.
2. **Replay:** Use `graph.aget_state_history(config)` which returns an async iterator of all checkpointed `SimulationState` snapshots in order. Diff consecutive states to reconstruct what changed (which order moved to which station, what the LLM decided) and emit the same SSE events.
3. **Key benefit:** No LLM calls during replay — deterministic, free, and fast.

The replay viewer uses the exact same canvas and HTMX components as the live view — just points the `EventSource` at `/stream/{thread_id}` instead of `/stream`.

## Shared setup factory

Extract the simulation setup from `main.py` into a reusable function so both CLI and server can use it:

```python
# src/ui/factory.py
def create_simulation(settings, order_count=10, urgent_ratio=0.3):
    """Returns (graph, initial_state, config) tuple."""
    # Create equipment, agents, chains, bus, graph
    # Generate orders based on order_count and urgent_ratio
    # Build config dict with thread_id
    return graph, initial_state, config
```

The server calls this per-run. The CLI (`main.py`) calls it once. Same code path.

## SVG illustrations to generate

Flat-design cartoon style (geometric, clean — like Duolingo icons, not photorealistic):

- 4 station machines: `printer.svg`, `heat_press.svg`, `qc_station.svg`, `packaging.svg`
- 1 base t-shirt + 7 design variants matching the design catalogue: `tshirt_base.svg`, `tshirt_dragon.svg`, `tshirt_unicorn.svg`, `tshirt_cyberpunk.svg`, `tshirt_minimal.svg`, `tshirt_retro.svg`, `tshirt_floral.svg`, `tshirt_geometric.svg`
- 1 conveyor belt segment: `conveyor.svg`
- 1 thought bubble: `thought_bubble.svg`
- 3 status icons: `icons_pass.svg`, `icons_fail.svg`, `icons_rework.svg`
- 2 effects: `fire.svg`, `steam.svg`

## Dependencies to add

```
fastapi
uvicorn[standard]
```

No JS dependencies beyond anime.js (loaded via CDN in the HTML) and HTMX (also CDN). No npm/build step — single HTML file served by FastAPI.

## What NOT to change

- LangGraph graph definition, nodes, pipeline — untouched
- Agents, equipment, LLM chains — untouched
- MessageBus — untouched
- Existing CLI path (`uv run dev`) — still works after extracting `factory.py`
