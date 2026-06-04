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
from datetime import datetime, timezone
from pathlib import Path

from config.settings import Settings
from graph.state import SimulationState
from langgraph.checkpoint.postgres import PostgresSaver
from logging_config import TraceFilter
from models.order import DESIGN_DETAILS, FALLBACK_DESIGN_DESCRIPTION, Order
from ui.factory import create_simulation

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
# Order generation (local copy for CLI path — factory.py has its own)
# ---------------------------------------------------------------------------

DESIGNS = ["dragon", "unicorn", "cyberpunk", "minimal", "retro", "floral", "geometric"]


def _generate_orders(count: int, urgent_ratio: float = 0.3) -> list[Order]:
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
            created_at=datetime.now(timezone.utc),
        )
        orders.append(order)

    logger.info("Generated %d orders (%d urgent)", count, urgent_count)
    return orders


def _print_statistics(final_state: SimulationState, orders: list[Order]) -> None:
    print("\n" + "=" * 60)
    print("  SIMULATION COMPLETE")
    print("=" * 60)

    completed = len(final_state.completed_orders)
    total = len(orders)

    print(f"\n  📊 FINAL STATISTICS")
    print(f"  {'─' * 40}")
    print(f"  Total orders:           {total}")
    print(f"  Completed:              {completed}")
    print(f"  Still pending:          {len(final_state.pending_orders)}")
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


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def main() -> None:
    setup_logging()
    settings = Settings()

    logger.info("=" * 60)
    logger.info("TSHIRT MAS - Multi-Agent System (LangGraph)")
    logger.info("=" * 60)

    orders = _generate_orders(10, urgent_ratio=0.3)

    print("\n" + "=" * 60)
    print("  T-SHIRT FACTORY MULTI-AGENT SYSTEM")
    print("  Powered by LangGraph + Ollama")
    print("=" * 60)
    print(f"\n  Generated {len(orders)} orders:")
    for o in orders:
        tag = "⚡ URGENT" if o.priority == "urgent" else "   normal"
        print(f"    {o.id}  {tag}  [{o.design_name}]")
    print()

    logger.info("Initializing PostgresSaver and setting up checkpoint tables...")

    with PostgresSaver.from_conn_string(settings.database_url) as checkpointer:
        checkpointer.setup()
        graph, initial_state, config = create_simulation(
            checkpointer=checkpointer,
            settings=settings,
            order_count=len(orders),
            urgent_ratio=0.3,
        )

        # Use pre-generated orders (CLI may have different random seed)
        initial_state.pending_orders = {o.id: o for o in orders}
        initial_state.all_orders = {o.id: o for o in orders}

        print("-" * 60)
        print("  Starting pipeline execution...")
        print(f"  Thread: {config['configurable']['thread_id']}")
        print("-" * 60)
        print()

        final_state_dict = graph.invoke(initial_state, config)
        final_state = SimulationState(**final_state_dict)

    _print_statistics(final_state, orders)


if __name__ == "__main__":
    main()
