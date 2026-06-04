"""Shared simulation setup — used by both CLI (main.py) and web server."""

import logging
import random
from datetime import datetime, timezone
from typing import Any

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
from models.order import DESIGN_DETAILS, FALLBACK_DESIGN_DESCRIPTION, Order

logger = logging.getLogger(__name__)

DESIGNS = ["dragon", "unicorn", "cyberpunk", "minimal", "retro", "floral", "geometric"]


def create_simulation(
    checkpointer: PostgresSaver,
    settings: Settings | None = None,
    order_count: int = 10,
    urgent_ratio: float = 0.3,
    thread_id: str | None = None,
    event_callback: Any = None,
) -> tuple[Any, SimulationState, dict[str, Any]]:
    """Create and wire up a complete simulation run.

    Returns (graph, initial_state, config) ready for graph.invoke() or graph.astream().
    """
    if settings is None:
        settings = Settings()

    if thread_id is None:
        thread_id = f"sim-{datetime.now(timezone.utc).strftime('%Y%m%d-%H%M%S')}"

    bus = MessageBus()

    # --- Equipment ---
    printer_eq = Printer(failure_probability=0.08)
    heat_press_eq = HeatPress(failure_probability=0.08)
    qc_eq = QualityStation()
    packaging_eq = PackagingStation(failure_probability=0.05)

    # --- LLM chains ---
    routing_chain = RoutingChain(settings)
    qc_chain = QCChain(settings)
    printer_chain = PrinterChain(settings)
    heat_press_chain = HeatPressChain(settings)
    packaging_chain = PackagingChain(settings)

    # --- Agents ---
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

    scheduler.bus = bus
    printer_agent.bus = bus
    hp_agent.bus = bus
    qc_agent.bus = bus
    pkg_agent.bus = bus

    # --- Orders ---
    orders = _generate_orders(order_count, urgent_ratio)

    # --- Graph ---
    graph = build_graph(checkpointer)

    initial_state = SimulationState(
        pending_orders={o.id: o for o in orders},
        all_orders={o.id: o for o in orders},
    )

    config = {
        "configurable": {
            "thread_id": thread_id,
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
            "event_callback": event_callback,
        }
    }

    logger.info(
        "Simulation created: thread=%s orders=%d urgent_ratio=%.2f",
        thread_id,
        order_count,
        urgent_ratio,
    )

    return graph, initial_state, config


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
