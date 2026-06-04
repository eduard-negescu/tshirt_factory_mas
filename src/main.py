"""T-shirt Factory MAS — LangGraph-powered multi-agent simulation.

Architecture:
  - LangGraph StateGraph replaces the hand-rolled while-loop
  - PostgresSaver provides checkpointing (pause/resume across restarts)
  - Agents, equipment, and LLM chains are passed as runtime configurable
  - The graph has two nodes: plan (LLM scheduling) and process_order (pipeline)
"""

import logging
import sys
from datetime import datetime
from pathlib import Path

from bus import MessageBus
from config.settings import Settings
from graph.builder import build_graph
from graph.state import SimulationState
from langgraph.checkpoint.postgres import PostgresSaver
from logging_config import TraceFilter
from ui.factory import (
    create_agents,
    create_chains,
    create_config,
    create_equipment,
    generate_orders,
)

# ---------------------------------------------------------------------------
# Logging setup
# ---------------------------------------------------------------------------

LOG_DIR = Path(__file__).resolve().parent.parent.parent / "logs"


def setup_logging() -> None:
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    log_file = LOG_DIR / "app.log"

    root_logger = logging.getLogger()
    root_logger.setLevel(logging.DEBUG)

    trace_filter = TraceFilter()

    file_handler = logging.FileHandler(log_file, encoding="utf-8")
    file_handler.setLevel(logging.DEBUG)
    file_handler.addFilter(trace_filter)
    file_fmt = logging.Formatter(
        "%(asctime)s | %(levelname)-8s | %(trace_id)-8s | %(name)s | %(message)s"
    )
    file_handler.setFormatter(file_fmt)

    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setLevel(logging.INFO)
    console_handler.addFilter(trace_filter)
    console_fmt = logging.Formatter(
        "%(levelname)-8s | %(trace_id)-8s | %(name)s | %(message)s"
    )
    console_handler.setFormatter(console_fmt)

    root_logger.addHandler(file_handler)
    root_logger.addHandler(console_handler)

    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)


logger = logging.getLogger("main")

# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def main() -> None:
    setup_logging()
    settings = Settings()

    logger.info("=" * 60)
    logger.info("TSHIRT MAS - Sistem Multi-Agent (LangGraph)")
    logger.info("=" * 60)

    # --- Create message bus ---
    bus = MessageBus()

    # --- Create equipment, chains, and agents (via shared factory) ---
    equipment = create_equipment()
    chains = create_chains(settings)
    agents = create_agents(equipment, chains, bus, settings)

    # --- Generate orders ---
    orders = generate_orders(10, urgent_ratio=0.3)

    print("\n" + "=" * 60)
    print("  FABRICA DE TRICOURI - SISTEM MULTI-AGENT")
    print("  Bazat pe LangGraph + Ollama")
    print("=" * 60)
    print(f"\n  S-au generat {len(orders)} comenzi:")
    for o in orders:
        tag = "⚡ URGENT" if o.priority == "urgent" else "   normal"
        print(f"    {o.id}  {tag}  [{o.design_name}]")
    print()

    # --- Build the graph with PostgresSaver ---
    logger.info("Initializing PostgresSaver and setting up checkpoint tables...")

    with PostgresSaver.from_conn_string(settings.database_url) as checkpointer:
        checkpointer.setup()
        graph = build_graph(checkpointer)

        # --- Initialize simulation state ---
        initial_state = SimulationState(
            pending_orders={o.id: o for o in orders},
            all_orders={o.id: o for o in orders},
        )

        # --- Runtime config (agents, equipment, chains passed via configurable) ---
        config = create_config(
            thread_id=f"sim-{datetime.now().strftime('%Y%m%d-%H%M%S')}",
            equipment=equipment,
            agents=agents,
            chains=chains,
            bus=bus,
            scheduler_chain=agents["scheduler"].chain,
        )

        # --- Run the simulation ---
        print("-" * 60)
        print("  Pornire execuție procesare...")
        print(f"  Thread: {config['configurable']['thread_id']}")
        print("-" * 60)
        print()

        final_state_dict = graph.invoke(initial_state, config)
        final_state = SimulationState(**final_state_dict)

    # --- Statistics ---
    print("\n" + "=" * 60)
    print("  SIMULARE COMPLETĂ")
    print("=" * 60)

    completed = len(final_state.completed_orders)
    pending = len(final_state.pending_orders)
    total = len(orders)

    print(f"\n  📊 STATISTICI FINALE")
    print(f"  {'─' * 40}")
    print(f"  Total comenzi:          {total}")
    print(f"  Finalizate:              {completed}")
    print(f"  În așteptare:            {pending}")
    print(f"  În procesare:            {len(final_state.in_progress)}")
    print(f"  Rată de finalizare:       {completed / total * 100:.1f}%")
    print(f"  Re-planificări LLM:       {final_state.re_plan_count}")
    print(f"  Iterații:                 {final_state.iteration}")
    print(f"  Defecte presă termică:    "
          f"{1 if final_state.heat_press_failure_triggered else 0} (forțat)")

    urgent_total = sum(1 for o in orders if o.priority == "urgent")
    urgent_completed = sum(
        1
        for oid, o in final_state.completed_orders.items()
        if o.priority == "urgent"
    )
    print(f"  Comenzi urgente:         {urgent_completed}/{urgent_total} finalizate")

    # Per-order status — reconstruct from graph state
    print(f"\n  📋 STATUS COMENZI:")
    for o in orders:
        if o.id in final_state.completed_orders:
            status = "completed"
        elif o.id in final_state.pending_orders:
            status = "pending"
        elif o.id in final_state.in_progress:
            status = "in_progress"
        else:
            status = o.status

        status_icon = {
            "completed": "✅",
            "pending": "⏳",
            "in_progress": "🔄",
        }.get(status, "❓")
        print(f"    {status_icon} {o.id} [{o.priority:6s}] {status}")

    print(f"\n  📝 Log complet: {LOG_DIR / 'app.log'}")
    print("=" * 60)


if __name__ == "__main__":
    main()
