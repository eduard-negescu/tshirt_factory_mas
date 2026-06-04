# T-Shirt Factory MAS

Multi-Agent System simulating an automated T-shirt customization workshop. Orchestrates orders through a production pipeline using **LangChain + Ollama** for three LLM-driven decisions: scheduling, pipeline routing, and quality control. Orchestration is handled by **LangGraph** with **PostgresSaver** for checkpointing — the simulation can pause and resume across restarts.

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
- **MessageBus** — central pub/sub for inter-agent communication within pipeline nodes.
- **Equipment Agents** — each wraps an equipment simulator (`Printer`, `HeatPress`, `QualityStation`, `PackagingStation`). Printer, HeatPress, and Packaging have configurable random failure probabilities; QualityStation simulates inspection time while the actual pass/fail/rework verdict comes from the QC LLM.
- **Equipment** — simulates processing time with `time.sleep()` and random failures. Each station has `process(order_id) → dict` and `reset()`.

### Three LLM Roles

| LLM | Purpose | Output |
|---|---|---|
| **Scheduling** | Orders pending orders by priority/urgency | `ScheduleResponse` — ordered list of order IDs |
| **Routing** | Decides which pipeline stations each order needs based on its design | `RoutingDecision` — ordered `StationRoute` list with required flags |
| **Quality Control** | Inspects shirts with design-specific reasoning (replaces random rejection) | `QualityDecision` — pass / rework (with instructions) / fail + severity |

### Pipeline — LLM-Driven per Order

Not all orders go through all stations. The routing LLM reads the order's design description and decides:

- **Simple designs** (e.g., "minimal" — single-color text) → may skip heat_press and QC
- **Complex designs** (e.g., "cyberpunk" — 7 colors, gradients) → all stations required
- **Special effects** (e.g., "retro" — crackle texture) → heat_press with special settings

QC is similarly design-aware: the QC LLM is more lenient on complex designs and urgent orders, and provides specific rework instructions (e.g., "recalibrate printer registration to fix ~3mm misalignment on petal edges").

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

# Run the simulation
uv run dev
```

Or directly via the module:

```bash
uv run python -m src.tshirt_mas.main
```

The simulation generates 10 orders (30% urgent) with design descriptions from a catalogue of 7 designs, runs them through the LangGraph pipeline with LLM-driven routing and QC, forces a heat press failure mid-run for recovery demo, and prints statistics. Full debug logs are written to `logs/app.log`. The graph state is checkpointed to PostgreSQL after each node.

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
│   └── qc_chain.py          # LLM-driven quality inspection decisions
└── models/
    ├── order.py             # Order model + DESIGN_DETAILS catalogue
    ├── messages.py          # AgentMessage model
    └── llm_models.py        # ScheduleResponse, RoutingDecision, QualityDecision, etc.
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
- Agents follow: `__init__(equipment)`, `process(order_id) → dict`, `handle_message(msg)`.
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
