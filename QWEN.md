# T-Shirt Factory MAS (Multi-Agent System)

A Python multi-agent simulation of an automated T-shirt customization workshop. The system orchestrates orders through a production pipeline using **LangChain + Ollama** for six distinct LLM-driven decisions: scheduling, pipeline routing, quality control, printer configuration, heat press tuning, and packaging configuration. Orchestration is handled by **LangGraph** with **PostgresSaver** for checkpointing — the simulation can pause and resume across restarts.

## Tech Stack

- **Python** ≥ 3.12
- **Package manager**: uv (with `uv.lock`)
- **Build system**: hatchling
- **Key dependencies**: `langchain` ≥ 0.3.0, `langchain-ollama` ≥ 0.2.0, `langgraph` ≥ 0.2.0, `langgraph-checkpoint-postgres` ≥ 2.0.0, `psycopg[binary]` ≥ 3.2.0, `pydantic` ≥ 2.0.0, `pydantic-settings` ≥ 2.0.0, `streamlit` ≥ 1.41.0
- **LLM backend**: Ollama (default model: `llama3.2`, configurable via `.env`)
- **Persistence**: PostgreSQL 17 (via Docker Compose) for LangGraph checkpointing

## Project Structure

```
src/
├── main.py                  # Entry point, graph invocation, order generation
├── bus.py                   # MessageBus — inter-agent pub/sub communication
├── _entry.py                # Dev entry point (sys.path bootstrap)
├── agents/
│   ├── scheduler_agent.py   # Thin wrapper — state managed by LangGraph graph
│   ├── printer_agent.py     # Printer station agent
│   ├── heat_press_agent.py  # Heat press station agent
│   ├── quality_agent.py     # Quality control agent (LLM-driven QC)
│   └── packaging_agent.py   # Packaging agent
├── config/
│   └── settings.py          # pydantic-settings (reads .env, includes DATABASE_URL)
├── equipment/
│   ├── printer.py           # Printer simulation (random failures)
│   ├── heat_press.py        # Heat press simulation
│   ├── quality_station.py   # QC station (inspection time, verdict from LLM)
│   └── packaging_station.py # Packaging station simulation
├── graph/
│   ├── state.py             # SimulationState — single source of truth, checkpointed
│   ├── nodes.py             # Graph nodes: plan_node, process_order_node
│   ├── pipeline.py          # Per-order pipeline execution (stateless)
│   └── builder.py           # StateGraph builder with PostgresSaver + conditional edges
├── llm/
│   ├── scheduler_chain.py   # LangChain prompt + structured output for scheduling
│   ├── routing_chain.py     # LLM-driven pipeline routing per order
│   ├── qc_chain.py          # LLM-driven quality inspection decisions
│   ├── printer_chain.py     # LLM-driven printer configuration per design
│   ├── heat_press_chain.py  # LLM-driven heat press configuration per design
│   └── packaging_chain.py   # LLM-driven packaging configuration per design
└── models/
    ├── order.py             # Order model + DESIGN_DETAILS catalogue
    ├── messages.py          # AgentMessage model (sender, receiver, type, payload)
    └── llm_models.py        # ScheduleResponse, RoutingDecision, QualityDecision, etc.
└── ui/
    ├── __init__.py
    ├── _entry.py            # Entry point for `uv run ui` (launches Streamlit)
    ├── factory.py           # Shared setup (equipment, chains, agents, config)
    └── streamlit_app.py     # Streamlit dashboard with live streaming
```

## Architecture

- **LangGraph StateGraph** (`graph/builder.py`): Replaces the hand-rolled while-loop. Two nodes — `plan` (LLM scheduling) and `process_order` (pipeline execution) — with conditional edges that route based on pipeline outcomes (completed, failed, rejected, rework). The graph is compiled with `PostgresSaver` for checkpointing.
- **SimulationState** (`graph/state.py`): Single-source-of-truth Pydantic model holding all order collections (pending, in_progress, completed, rejected), the processing queue, counters, and flags. Every field is checkpointed by PostgresSaver, enabling pause/resume across restarts.
- **PostgresSaver**: Checkpoints graph state to PostgreSQL after each node execution. The checkpoint backend is swappable — same graph definition works with SqliteSaver (dev) or AsyncPostgresSaver (production web app).
- **MessageBus** (`bus.py`): Central pub/sub for inter-agent communication. Agents register handlers, send structured `AgentMessage` objects, and `dispatch()` delivers them. **Dispatch returns a dict of non-None handler responses**, enabling agent-driven state changes: the scheduler returns a `ScheduleResponse` on `equipment_failure`, and the QC agent adjusts internal strictness on `station_history`. The caller (graph node) reads these responses to update state.
- **Agents**: Each wraps an equipment simulator. `SchedulerAgent` is now a thin wrapper — its `SchedulerChain` is passed to graph nodes via `configurable`. Station agents (printer, heat_press, QC, packaging) are invoked from `process_order_pipeline()`.
- **Equipment**: Simulates processing with `time.sleep()` and random failure probabilities (Printer, HeatPress, Packaging). QualityStation simulates inspection time only; the actual verdict comes from the QC LLM.
- **Pipeline** (`graph/pipeline.py`): Stateless — all dependencies are passed as parameters. LLM-driven routing decides which stations are required per order based on design description. Not all orders go through all stations.

### Graph Flow

```
START → plan_node ──→ process_order_node ──→ [conditional]
           ↑                    │                  │
           │                    │   completed      │  failed/rejected/rework
           │                    │   (queue left)   │
           │                    └──────────────────┘
           └─────────────────────────────────────── re-plan
                              END (queue empty)
```

### Six LLM Roles

| LLM | Purpose | When called | Output |
|---|---|---|---|
| **Scheduling** | Orders pending orders by priority/urgency | Initial plan + every failure/rejection/rework | `ScheduleResponse` — ordered list of order IDs |
| **Routing** | Decides which pipeline stations an order needs | Once per order at start of processing | `RoutingDecision` — ordered `StationRoute` list with required flags + notes |
| **Printer** | Configures print temperature, ink saturation, passes, color profile per design | Once per order at printer stage | `PrinterDecision` — modulates failure probability by ±15% |
| **Heat Press** | Configures temperature, dwell time, pressure, multi-pass per design | Once per order at heat press stage | `HeatPressDecision` — modulates failure probability by ±18% |
| **Quality Control** | Inspects finished shirts; strictness adjusted by station history via message | During QC stage for each order | `QualityDecision` — pass / rework (with instructions) / fail + defect severity |
| **Packaging** | Configures box type, fold method, extras based on design and priority | Once per order at packaging stage | `PackagingDecision` — modulates failure probability by ±15% |

## Configuration

Copy `.env.example` to `.env` and adjust:

| Variable          | Default                 | Description               |
|-------------------|-------------------------|---------------------------|
| `OLLAMA_API_KEY`  | `ollama`                | API key for Ollama        |
| `OLLAMA_BASE_URL` | `http://localhost:11434` | Ollama API URL (cloud or local) |
| `MODEL_NAME`      | `llama3.2`             | Ollama model to use       |
| `DATABASE_URL`    | `postgresql://tshirt_mas:tshirt_mas@localhost:5432/tshirt_mas` | PostgreSQL connection string for checkpointing |

Settings are loaded via `pydantic-settings` in `config/settings.py`.

## Design Catalogue

Orders are generated with a `design_name` and a rich `design_description` from `src/models/order.py`. Seven designs are defined: `dragon`, `unicorn`, `cyberpunk`, `minimal`, `retro`, `floral`, `geometric`. The LLM uses the natural-language description to make routing decisions (e.g., "minimal" single-color designs may skip heat_press and QC) and nuanced QC assessments.

## Running

```bash
# Start PostgreSQL (required for checkpointing)
docker compose up -d

# CLI — run the simulation once
uv run dev

# Streamlit UI — interactive dashboard with live streaming
uv run ui
```

Requirements: Ollama API key configured in `.env` (cloud or local), PostgreSQL running via Docker Compose.

## Rework Protection

QC verdicts of "rework" trigger re-queuing with no priority escalation (unlike "fail", which escalates to urgent). After 2 consecutive rework attempts on the same order, the system force-completes it to prevent infinite loops.

## Streamlit UI

`src/ui/streamlit_app.py` provides a live dashboard. Key features:

- **Sidebar**: order count slider, urgent ratio slider, start button
- **Live event feed**: plan decisions and order outcomes streamed in real-time via `graph.stream(stream_mode="updates")`
- **Stats panel**: progress bar, completed count, iterations, re-plans
- **Order status table**: per-order status with icons, updates live during the run
- **Final summary**: completion rate, per-order details after the run ends

Shared setup lives in `src/ui/factory.py` — `main.py` (CLI) and `streamlit_app.py` (UI) both use `create_equipment()`, `create_chains()`, `create_agents()`, and `create_config()` to avoid duplication.

## Testing

No test suite exists yet. To add tests:
- Use `pytest` (not yet a dependency — add to `pyproject.toml` under `[project.optional-dependencies]`).
- Test files should live alongside source under `tests/` at the project root.

## Development Conventions

- All code lives under `src/` (flat-layout, multiple top-level packages).
- Package is built with `hatchling`, configured for individual packages under `src/`.
- Logging: `logging.getLogger(__name__)` pattern throughout. Logs written to `logs/app.log` (DEBUG) and stdout (INFO).
- Data models use `pydantic.BaseModel` with type annotations.
- Agents follow a consistent pattern: `__init__(equipment)`, `process(order_id) → dict`, `handle_message(msg) → optional response`, optional `_send()` helper. SchedulerAgent returns a `ScheduleResponse` on `equipment_failure`; QualityControlAgent adjusts `inspection_strictness` on `station_history`. SchedulerAgent's state lives in the LangGraph graph.
- Equipment follows: `__init__(name, failure_probability)`, `process(order_id) → dict`, `reset()`. QualityStation is the exception — `inspect(order_id) → float` since the verdict comes from the LLM.
- Graph nodes follow: `node_name(state: SimulationState, config: RunnableConfig) → dict[str, Any]`. Dependencies (agents, equipment, chains, bus) are passed via `config["configurable"]`.
- Pipeline (`graph/pipeline.py`): Stateless function — all dependencies are parameters. Returns an outcome string.
- UI factory (`ui/factory.py`): Shared setup functions (`create_equipment`, `create_chains`, `create_agents`, `create_config`) used by both CLI (`main.py`) and UI (`streamlit_app.py`). When adding new agents, equipment, or chains, update the factory so both entry points stay in sync.
- Docstrings are minimal; comments explain non-obvious behavior (e.g., forced failure simulation in `nodes.py`, rework counter).
- LLM chains follow the same pattern: system prompt, human template, `_strip_json_comments`, `PydanticOutputParser`, custom error class.
