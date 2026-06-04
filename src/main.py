"""T-shirt Factory MAS — LangGraph-powered multi-agent simulation.

Architecture:
  - LangGraph StateGraph replaces the hand-rolled while-loop
  - PostgresSaver provides checkpointing (pause/resume across restarts)
  - Agents, equipment, and LLM chains are passed as runtime configurable
  - The graph has two nodes: plan (LLM scheduling) and process_order (pipeline)
"""

import logging
import random
import sys
from datetime import datetime
from pathlib import Path

from agents.heat_press_agent import HeatPressAgent
from agents.packaging_agent import PackagingAgent
from agents.printer_agent import PrinterAgent
from agents.quality_agent import QualityControlAgent
from agents.scheduler_agent import SchedulerAgent
from bus import MessageBus
from config.settings import Settings
from equipment.heat_press import HeatPress
from equipment.packaging_station import PackagingStation
from equipment.printer import Printer
from equipment.quality_station import QualityStation
from graph.builder import build_graph
from graph.state import SimulationState
from langgraph.checkpoint.postgres import PostgresSaver
from llm.heat_press_chain import HeatPressChain
from llm.packaging_chain import PackagingChain
from llm.printer_chain import PrinterChain
from llm.qc_chain import QCChain
from llm.routing_chain import RoutingChain
from logging_config import TraceFilter
from models.order import DESIGN_DETAILS, FALLBACK_DESIGN_DESCRIPTION, Order

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
# Order generation
# ---------------------------------------------------------------------------

DESIGNS = ["dragon", "unicorn", "cyberpunk", "minimal", "retro", "floral", "geometric"]


def generate_orders(count: int, urgent_ratio: float = 0.3) -> list[Order]:
    orders = []
    urgent_count = max(1, int(count * urgent_ratio))
    priorities = ["urgent"] * urgent_count + ["normal"] * (count - urgent_count)
    random.shuffle(priorities)

    for i, priority in enumerate(priorities, start=1):
        design_name = random.choice(DESIGNS)
        design_description = DESIGN_DETAILS.get(
            design_name, FALLBACK_DESIGN_DESCRIPTION
        )
        order = Order(
            id=f"O-{i:03d}",
            priority=priority,
            design_name=design_name,
            design_description=design_description,
            created_at=datetime.now(),
        )
        orders.append(order)

    logger.info("Generated %d orders (%d urgent)", count, urgent_count)
    return orders


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def main() -> None:
    setup_logging()
    settings = Settings()

    logger.info("=" * 60)
    logger.info("TSHIRT MAS - Multi-Agent System (LangGraph)")
    logger.info("=" * 60)

    # --- Create message bus ---
    bus = MessageBus()

    # --- Create equipment ---
    printer_eq = Printer(failure_probability=0.08)
    heat_press_eq = HeatPress(failure_probability=0.08)
    qc_eq = QualityStation()
    packaging_eq = PackagingStation(failure_probability=0.05)

    # --- Create LLM chains ---
    routing_chain = RoutingChain(settings)
    qc_chain = QCChain(settings)
    printer_chain = PrinterChain(settings)
    heat_press_chain = HeatPressChain(settings)
    packaging_chain = PackagingChain(settings)

    # --- Create agents ---
    scheduler = SchedulerAgent(settings)
    printer_agent = PrinterAgent(printer_eq, printer_chain=printer_chain)
    hp_agent = HeatPressAgent(heat_press_eq, heat_press_chain=heat_press_chain)
    qc_agent = QualityControlAgent(qc_eq, qc_chain=qc_chain)
    pkg_agent = PackagingAgent(packaging_eq, packaging_chain=packaging_chain)

    # --- Wire message bus ---
    bus.register("scheduler", scheduler.handle_message)
    bus.register("printer", printer_agent.handle_message)
    bus.register("heat_press", hp_agent.handle_message)
    bus.register("quality_control", qc_agent.handle_message)
    bus.register("packaging", pkg_agent.handle_message)

    # Set bus references on agents
    scheduler.bus = bus
    printer_agent.bus = bus
    hp_agent.bus = bus
    qc_agent.bus = bus
    pkg_agent.bus = bus

    # --- Generate orders ---
    orders = generate_orders(10, urgent_ratio=0.3)

    print("\n" + "=" * 60)
    print("  T-SHIRT FACTORY MULTI-AGENT SYSTEM")
    print("  Powered by LangGraph + Ollama")
    print("=" * 60)
    print(f"\n  Generated {len(orders)} orders:")
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
        config = {
            "configurable": {
                "thread_id": f"sim-{datetime.now().strftime('%Y%m%d-%H%M%S')}",
                "equipment": {
                    "printer": printer_eq,
                    "heat_press": heat_press_eq,
                    "quality_control": qc_eq,
                    "packaging": packaging_eq,
                },
                "agents": {
                    "printer": printer_agent,
                    "heat_press": hp_agent,
                    "quality_control": qc_agent,
                    "packaging": pkg_agent,
                },
                "chains": {
                    "routing": routing_chain,
                    "qc": qc_chain,
                    "printer": printer_chain,
                    "heat_press": heat_press_chain,
                    "packaging": packaging_chain,
                },
                "bus": bus,
                "scheduler_chain": scheduler.chain,
            }
        }

        # --- Run the simulation ---
        print("-" * 60)
        print("  Starting pipeline execution...")
        print(f"  Thread: {config['configurable']['thread_id']}")
        print("-" * 60)
        print()

        final_state_dict = graph.invoke(initial_state, config)
        final_state = SimulationState(**final_state_dict)

    # --- Statistics ---
    print("\n" + "=" * 60)
    print("  SIMULATION COMPLETE")
    print("=" * 60)

    completed = len(final_state.completed_orders)
    pending = len(final_state.pending_orders)
    total = len(orders)

    print(f"\n  📊 FINAL STATISTICS")
    print(f"  {'─' * 40}")
    print(f"  Total orders:           {total}")
    print(f"  Completed:              {completed}")
    print(f"  Still pending:          {pending}")
    print(f"  In progress:            {len(final_state.in_progress)}")
    print(f"  Completion rate:        {completed / total * 100:.1f}%")
    print(f"  LLM re-plans:           {final_state.re_plan_count}")
    print(f"  Iterations:             {final_state.iteration}")
    print(f"  Heat press failures:    "
          f"{1 if final_state.heat_press_failure_triggered else 0} (forced)")

    urgent_total = sum(1 for o in orders if o.priority == "urgent")
    urgent_completed = sum(
        1
        for oid, o in final_state.completed_orders.items()
        if o.priority == "urgent"
    )
    print(f"  Urgent orders:          {urgent_completed}/{urgent_total} completed")

    # Per-order status — reconstruct from graph state
    print(f"\n  📋 ORDER STATUS:")
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

    print(f"\n  📝 Full logs: {LOG_DIR / 'app.log'}")
    print("=" * 60)


if __name__ == "__main__":
    main()
