# T-Shirt Factory MAS (Multi-Agent System)

A Python multi-agent simulation of an automated T-shirt customization workshop. The system orchestrates orders through a production pipeline using **LangChain + Ollama** for three distinct LLM-driven decisions: scheduling, pipeline routing, and quality control. Orchestration is handled by **LangGraph** with **PostgresSaver** for checkpointing — the simulation can pause and resume across restarts.

## Tech Stack

- **Python** ≥ 3.12
- **Package manager**: uv (with `uv.lock`)
- **Build system**: hatchling
- **Key dependencies**: `langchain` ≥ 0.3.0, `langchain-ollama` ≥ 0.2.0, `langgraph` ≥ 0.2.0, `langgraph-checkpoint-postgres` ≥ 2.0.0, `psycopg[binary]` ≥ 3.2.0, `pydantic` ≥ 2.0.0, `pydantic-settings` ≥ 2.0.0
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
│   └── qc_chain.py          # LLM-driven quality inspection decisions
└── models/
    ├── order.py             # Order model + DESIGN_DETAILS catalogue
    ├── messages.py          # AgentMessage model (sender, receiver, type, payload)
    └── llm_models.py        # ScheduleResponse, RoutingDecision, QualityDecision, etc.
```

## Architecture

- **LangGraph StateGraph** (`graph/builder.py`): Replaces the hand-rolled while-loop. Two nodes — `plan` (LLM scheduling) and `process_order` (pipeline execution) — with conditional edges that route based on pipeline outcomes (completed, failed, rejected, rework). The graph is compiled with `PostgresSaver` for checkpointing.
- **SimulationState** (`graph/state.py`): Single-source-of-truth Pydantic model holding all order collections (pending, in_progress, completed, rejected), the processing queue, counters, and flags. Every field is checkpointed by PostgresSaver, enabling pause/resume across restarts.
- **PostgresSaver**: Checkpoints graph state to PostgreSQL after each node execution. The checkpoint backend is swappable — same graph definition works with SqliteSaver (dev) or AsyncPostgresSaver (production web app).
- **MessageBus** (`bus.py`): Central pub/sub for inter-agent communication. Agents register handlers, send messages, and call `dispatch()` to deliver queued messages. Still used within pipeline nodes for agent-to-agent signaling.
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

### Three LLM Roles

| LLM | Purpose | When called | Output |
|---|---|---|---|
| **Scheduling** | Orders pending orders by priority/urgency | Initial plan + every failure/rejection/rework | `ScheduleResponse` — ordered list of order IDs |
| **Routing** | Decides which pipeline stations an order needs | Once per order at start of processing | `RoutingDecision` — ordered `StationRoute` list with required flags + notes |
| **Quality Control** | Inspects finished shirts instead of random rejection | During QC stage for each order | `QualityDecision` — pass / rework (with instructions) / fail + defect severity |

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

# Install dependencies and run the simulation
uv run dev
```

Or directly via the module:

```bash
uv run python -m src.tshirt_mas.main
```

Requires an Ollama API key configured in `.env` (cloud or local).

## Rework Protection

QC verdicts of "rework" trigger re-queuing with no priority escalation (unlike "fail", which escalates to urgent). After 2 consecutive rework attempts on the same order, the system force-completes it to prevent infinite loops.

## Testing

No test suite exists yet. To add tests:
- Use `pytest` (not yet a dependency — add to `pyproject.toml` under `[project.optional-dependencies]`).
- Test files should live alongside source under `tests/` at the project root.

## Development Conventions

- All code lives under `src/` (flat-layout, multiple top-level packages).
- Package is built with `hatchling`, configured for individual packages under `src/`.
- Logging: `logging.getLogger(__name__)` pattern throughout. Logs written to `logs/app.log` (DEBUG) and stdout (INFO).
- Data models use `pydantic.BaseModel` with type annotations.
- Agents follow a consistent pattern: `__init__(equipment)`, `process(order_id) → dict`, `handle_message(msg)`, optional `_send()` helper. SchedulerAgent is the exception — a thin wrapper since state lives in the LangGraph graph.
- Equipment follows: `__init__(name, failure_probability)`, `process(order_id) → dict`, `reset()`. QualityStation is the exception — `inspect(order_id) → float` since the verdict comes from the LLM.
- Graph nodes follow: `node_name(state: SimulationState, config: RunnableConfig) → dict[str, Any]`. Dependencies (agents, equipment, chains, bus) are passed via `config["configurable"]`.
- Pipeline (`graph/pipeline.py`): Stateless function — all dependencies are parameters. Returns an outcome string.
- Docstrings are minimal; comments explain non-obvious behavior (e.g., forced failure simulation in `nodes.py`, rework counter).
- LLM chains follow the same pattern: system prompt, human template, `_strip_json_comments`, `PydanticOutputParser`, custom error class.
