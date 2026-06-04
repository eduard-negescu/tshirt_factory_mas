# T-Shirt Factory MAS

Multi-Agent System simulating an automated T-shirt customization workshop. Orchestrates orders through a production pipeline using **LangChain + Ollama** for six LLM-driven decisions: scheduling, pipeline routing, quality control, printer configuration, heat press tuning, and packaging configuration. Orchestration is handled by **LangGraph** with **PostgresSaver** for checkpointing — the simulation can pause and resume across restarts.

## Architecture

```
                  ┌──────────────┐
                  │  LangGraph   │  StateGraph + PostgresSaver
                  │  StateGraph  │
                  └──────┬───────┘
         ┌───────────────┼───────────────┐
         ▼               ▼               ▼
   ┌──────────┐   ┌──────────┐   ┌──────────┐
   │ plan_node│   │process_  │   │Equipment │
   │  (LLM)   │   │order_node│   │(sim)     │
   └──────────┘   └──────────┘   └──────────┘
         │               │               │
         └───────┬───────┘               │
                 ▼                       │
          ┌──────────┐                   │
          │ MessageBus│◄─────────────────┘
          └──────────┘
```

- **LangGraph StateGraph** — replaces the hand-rolled while-loop. Two nodes (`plan`, `process_order`) with conditional edges route orders through the pipeline based on outcomes (completed, failed, rejected, rework).
- **PostgresSaver** — checkpoints the full `SimulationState` to PostgreSQL after each node. Enables pause/resume across restarts. Backend is swappable (SqliteSaver for dev, PostgresSaver for production).
- **MessageBus** — central pub/sub for inter-agent communication. Agents send structured JSON messages and react to incoming ones. The scheduler triggers re-plans on `equipment_failure` messages; the QC agent adjusts its inspection strictness on `station_history` messages. Dispatch returns handler responses so callers can act on agent-driven decisions.
- **Equipment Agents** — each wraps an equipment simulator (`Printer`, `HeatPress`, `QualityStation`, `PackagingStation`). Printer, HeatPress, and Packaging have configurable random failure probabilities; QualityStation simulates inspection time while the actual pass/fail/rework verdict comes from the QC LLM.
- **Equipment** — simulates processing time with `time.sleep()` and random failures. Each station has `process(order_id) → dict` and `reset()`.

### Six LLM Roles

| LLM | Purpose | Output |
|---|---|---|
| **Scheduling** | Orders pending orders by priority/urgency | `ScheduleResponse` — ordered list of order IDs |
| **Routing** | Decides which pipeline stations each order needs based on its design | `RoutingDecision` — ordered `StationRoute` list with required flags |
| **Printer** | Configures print temperature, ink saturation, passes, and color profile per design | `PrinterDecision` — modulates failure probability by ±15% |
| **Heat Press** | Configures temperature, dwell time, pressure, and multi-pass per design | `HeatPressDecision` — modulates failure probability by ±18% |
| **Quality Control** | Inspects shirts with design-specific reasoning, strictness adjusted by station history | `QualityDecision` — pass / rework (with instructions) / fail + severity |
| **Packaging** | Configures box type, fold method, and extras based on design and priority | `PackagingDecision` — modulates failure probability by ±15% |

### Pipeline — LLM-Driven per Order

Not all orders go through all stations. The routing LLM reads the order's design description and decides:

- **Simple designs** (e.g., "minimal" — single-color text) → may skip heat_press and QC
- **Complex designs** (e.g., "cyberpunk" — 7 colors, gradients) → all stations required
- **Special effects** (e.g., "retro" — crackle texture) → heat_press with special settings

QC is similarly design-aware: the QC LLM is more lenient on complex designs and urgent orders, and provides specific rework instructions (e.g., "recalibrate printer registration to fix ~3mm misalignment on petal edges").

### Message-Driven Coordination

The MessageBus is not just a logging sidecar — it drives real agent behavior:

- **Scheduler re-planning**: When equipment fails, the graph node sends an enriched `equipment_failure` message with full equipment status and pending orders. The scheduler's handler calls its LLM chain and returns a new `ScheduleResponse`, which the graph node uses as the updated processing queue — skipping a separate `plan_node` invocation.
- **QC strictness adjustment**: Before inspecting each order, the pipeline sends a `station_history` message to the QC agent detailing which stations ran and their parameters. The QC agent adjusts its inspection strictness (`high`, `elevated`, `normal`) based on station count and risky configurations (e.g., heavy ink + high temp). This strictness is injected into the QC LLM prompt, biasing the verdict.

### Rework Protection

QC verdicts of "rework" trigger re-queuing without priority escalation. After 2 rework attempts on the same order, it force-completes to prevent infinite loops.

## Tech Stack

| Layer | Technology |
|---|---|
| Language | Python ≥ 3.12 |
| Package manager | [uv](https://docs.astral.sh/uv/) |
| Build system | hatchling |
| Orchestration | LangGraph ≥ 0.2.0 (StateGraph + PostgresSaver) |
| LLM framework | LangChain ≥ 0.3.0 + langchain-ollama |
| LLM backend | Ollama (cloud API or local) |
| UI | Streamlit ≥ 1.41.0 (live dashboard) |
| Persistence | PostgreSQL 17 (via Docker Compose) |
| Data models | Pydantic ≥ 2.0 |
| Config | pydantic-settings (reads `.env`) |

## Getting Started

### Prerequisites

- Python 3.12+
- [uv](https://docs.astral.sh/uv/) (or pip + venv)
- [Docker](https://docs.docker.com/) (for PostgreSQL)
- Ollama API key (sign up at [ollama.com](https://ollama.com) — free tier works, or run Ollama locally)

### Installation

```bash
# Clone and enter the project
git clone <repo-url> && cd tshirt_factory_mas

# Install dependencies with uv
uv sync
```

### Configuration

Copy the example env file and fill in your Ollama credentials:

```bash
cp .env.example .env
```

| Variable | Default | Description |
|---|---|---|
| `OLLAMA_API_KEY` | `ollama` | Ollama API key |
| `OLLAMA_BASE_URL` | `http://localhost:11434` | Ollama API URL (cloud or local) |
| `MODEL_NAME` | `llama3.2` | Ollama model to use |
| `DATABASE_URL` | `postgresql://tshirt_mas:tshirt_mas@localhost:5432/tshirt_mas` | PostgreSQL connection string for checkpointing |

### Run

```bash
# Start PostgreSQL (required for checkpointing)
docker compose up -d

# CLI — run the simulation once (generates 10 orders, prints stats)
uv run dev

# Streamlit UI — interactive dashboard with live streaming
uv run ui
```

The CLI simulation generates 10 orders (30% urgent) with design descriptions from a catalogue of 7 designs, runs them through the LangGraph pipeline with LLM-driven routing and QC, forces a heat press failure mid-run for recovery demo, and prints statistics. Full debug logs are written to `logs/app.log`. The graph state is checkpointed to PostgreSQL after each node.

The Streamlit UI provides a live dashboard with configurable order count and urgent ratio, real-time event feed, progress bar, and per-order status table.

## Project Structure

```
src/
├── main.py                  # Entry point, graph invocation, order generation
├── bus.py                   # MessageBus — inter-agent pub/sub
├── _entry.py                # Dev entry point (sys.path bootstrap)
├── agents/
│   ├── scheduler_agent.py   # Thin wrapper — state managed by LangGraph
│   ├── printer_agent.py     # Printer station agent
│   ├── heat_press_agent.py  # Heat press station agent
│   ├── quality_agent.py     # Quality control agent (LLM-driven)
│   └── packaging_agent.py   # Packaging agent
├── config/
│   └── settings.py          # pydantic-settings (reads .env)
├── equipment/
│   ├── printer.py           # Printer simulation (random failures)
│   ├── heat_press.py        # Heat press simulation
│   ├── quality_station.py   # QC station (inspection time, verdict from LLM)
│   └── packaging_station.py # Packaging station simulation
├── graph/
│   ├── state.py             # SimulationState — checkpointed by PostgresSaver
│   ├── nodes.py             # Graph nodes: plan_node, process_order_node
│   ├── pipeline.py          # Per-order pipeline execution (stateless)
│   └── builder.py           # StateGraph builder + PostgresSaver + conditional edges
├── llm/
│   ├── scheduler_chain.py   # LangChain prompt + structured output for scheduling
│   ├── routing_chain.py     # LLM-driven pipeline routing per order
│   ├── qc_chain.py          # LLM-driven quality inspection decisions
│   ├── printer_chain.py     # LLM-driven printer configuration per design
│   ├── heat_press_chain.py  # LLM-driven heat press configuration per design
│   └── packaging_chain.py   # LLM-driven packaging configuration per design
└── models/
    ├── order.py             # Order model + DESIGN_DETAILS catalogue
    ├── messages.py          # AgentMessage model
    └── llm_models.py        # ScheduleResponse, RoutingDecision, QualityDecision, etc.
└── ui/
    ├── _entry.py            # Entry point for `uv run ui`
    ├── factory.py           # Shared setup (equipment, chains, agents, config)
    └── streamlit_app.py     # Streamlit dashboard with live streaming
```

## Design Catalogue

Seven designs with rich natural-language descriptions drive the LLM's routing and QC decisions:

| Design | Colors | Complexity | Key trait |
|---|---|---|---|
| dragon | 5 | Complex | Multi-color gradients, precise registration |
| unicorn | 3 + glitter | Moderate | Glitter heat-transfer overlay |
| cyberpunk | 7 | Very complex | Neon gradients, halftones, extended curing |
| minimal | 1 | Simple | Single-color line art, quick processing |
| retro | 2 | Moderate | Vintage crackle texture, special heat settings |
| floral | 4 | Moderate | Overlapping petals, alignment-critical |
| geometric | 2 | Moderate | Sharp edges, no bleeding tolerance |

## Development

### Conventions

- Python ≥ 3.12, formatted with the project's default tooling.
- Logging via `logging.getLogger(__name__)` — DEBUG → `logs/app.log`, INFO → stdout.
- Data models use `pydantic.BaseModel` with type annotations.
- Agents follow: `__init__(equipment)`, `process(order_id) → dict`, `handle_message(msg) → optional response`. Scheduler's handler returns a `ScheduleResponse` on `equipment_failure`; QC agent's handler adjusts internal state on `station_history`.
- Equipment follows: `__init__(name, failure_probability)`, `process(order_id) → dict`, `reset()`. QualityStation uses `inspect(order_id) → float` (verdict from LLM).
- LLM chains follow the same pattern: system prompt, human template, `_strip_json_comments`, `PydanticOutputParser`, custom error class.

### Testing

No test suite yet. To add tests:

```bash
uv add --dev pytest
mkdir tests
# Write tests under tests/
uv run pytest
```

## License

MIT
