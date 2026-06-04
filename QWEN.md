# T-Shirt Factory MAS (Multi-Agent System)

A Python multi-agent simulation of an automated T-shirt customization workshop. The system orchestrates orders through a production pipeline using **LangChain + Ollama** for three distinct LLM-driven decisions: scheduling, pipeline routing, and quality control.

## Tech Stack

- **Python** ≥ 3.12
- **Package manager**: uv (with `uv.lock`)
- **Build system**: hatchling
- **Key dependencies**: `langchain` ≥ 0.3.0, `langchain-ollama` ≥ 0.2.0, `pydantic` ≥ 2.0.0, `pydantic-settings` ≥ 2.0.0
- **LLM backend**: Ollama (default model: `llama3.2`, configurable via `.env`)

## Project Structure

```
src/
├── main.py                  # Entry point, simulation loop, order generation
├── bus.py                   # MessageBus — inter-agent pub/sub communication
├── _entry.py                # Dev entry point (sys.path bootstrap)
├── agents/
│   ├── scheduler_agent.py   # LLM-powered scheduler (prioritizes, re-plans)
│   ├── printer_agent.py     # Printer station agent
│   ├── heat_press_agent.py  # Heat press station agent
│   ├── quality_agent.py     # Quality control agent (LLM-driven QC)
│   └── packaging_agent.py   # Packaging agent
├── config/
│   └── settings.py          # pydantic-settings (reads .env)
├── equipment/
│   ├── printer.py           # Printer simulation (random failures)
│   ├── heat_press.py        # Heat press simulation
│   ├── quality_station.py   # QC station (inspection time, verdict from LLM)
│   └── packaging_station.py # Packaging station simulation
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

- **MessageBus** (`bus.py`): Central pub/sub for agent communication. Agents register handlers, send messages, and call `dispatch()` to deliver queued messages.
- **Agents**: Each wraps an equipment simulator. Agents send status messages (processing_complete, equipment_failure, quality_rejected, quality_rework, order_completed) to the SchedulerAgent.
- **SchedulerAgent**: Uses LangChain + Ollama to produce a `ScheduleResponse` (ordered list of order IDs + reasoning). Re-plans on equipment failures, QC rejections, and QC rework requests.
- **Equipment**: Simulates processing with `time.sleep()` and random failure probabilities (Printer, HeatPress, Packaging). QualityStation simulates inspection time only; the actual verdict comes from the QC LLM.
- **Pipeline**: LLM-driven — each order gets a per-order route from the routing LLM based on its design description. The routing LLM decides which stations are required and in what order. Not all orders go through all stations.

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

Settings are loaded via `pydantic-settings` in `config/settings.py`.

## Design Catalogue

Orders are generated with a `design_name` and a rich `design_description` from `src/models/order.py`. Seven designs are defined: `dragon`, `unicorn`, `cyberpunk`, `minimal`, `retro`, `floral`, `geometric`. The LLM uses the natural-language description to make routing decisions (e.g., "minimal" single-color designs may skip heat_press and QC) and nuanced QC assessments.

## Running

```bash
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
- Agents follow a consistent pattern: `__init__(equipment)`, `process(order_id) → dict`, `handle_message(msg)`, optional `_send()` helper.
- Equipment follows: `__init__(name, failure_probability)`, `process(order_id) → dict`, `reset()`. QualityStation is the exception — `inspect(order_id) → float` since the verdict comes from the LLM.
- Docstrings are minimal; comments explain non-obvious behavior (e.g., forced failure simulation in `main.py`, rework counter).
- LLM chains follow the same pattern: system prompt, human template, `_strip_json_comments`, `PydanticOutputParser`, custom error class.
