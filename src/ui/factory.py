"""Shared simulation setup — used by both CLI (main.py) and Streamlit UI."""

import random
from datetime import datetime

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
from llm.heat_press_chain import HeatPressChain
from llm.packaging_chain import PackagingChain
from llm.printer_chain import PrinterChain
from llm.qc_chain import QCChain
from llm.routing_chain import RoutingChain
from models.order import DESIGN_DETAILS, FALLBACK_DESIGN_DESCRIPTION, Order


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

    return orders


def create_equipment() -> dict:
    return {
        "printer": Printer(failure_probability=0.08),
        "heat_press": HeatPress(failure_probability=0.08),
        "quality_control": QualityStation(),
        "packaging": PackagingStation(failure_probability=0.05),
    }


def create_chains(settings: Settings) -> dict:
    return {
        "routing": RoutingChain(settings),
        "qc": QCChain(settings),
        "printer": PrinterChain(settings),
        "heat_press": HeatPressChain(settings),
        "packaging": PackagingChain(settings),
    }


def create_agents(
    equipment: dict,
    chains: dict,
    bus: MessageBus,
    settings: Settings,
) -> dict:
    scheduler = SchedulerAgent(settings)
    printer_agent = PrinterAgent(equipment["printer"], printer_chain=chains["printer"])
    hp_agent = HeatPressAgent(
        equipment["heat_press"], heat_press_chain=chains["heat_press"]
    )
    qc_agent = QualityControlAgent(
        equipment["quality_control"], qc_chain=chains["qc"]
    )
    pkg_agent = PackagingAgent(
        equipment["packaging"], packaging_chain=chains["packaging"]
    )

    # Wire message bus
    bus.register("scheduler", scheduler.handle_message)
    bus.register("printer", printer_agent.handle_message)
    bus.register("heat_press", hp_agent.handle_message)
    bus.register("quality_control", qc_agent.handle_message)
    bus.register("packaging", pkg_agent.handle_message)

    # Set bus references
    scheduler.bus = bus
    printer_agent.bus = bus
    hp_agent.bus = bus
    qc_agent.bus = bus
    pkg_agent.bus = bus

    return {
        "scheduler": scheduler,
        "printer": printer_agent,
        "heat_press": hp_agent,
        "quality_control": qc_agent,
        "packaging": pkg_agent,
    }


def create_config(
    thread_id: str,
    equipment: dict,
    agents: dict,
    chains: dict,
    bus: MessageBus,
    scheduler_chain,
) -> dict:
    return {
        "configurable": {
            "thread_id": thread_id,
            "equipment": equipment,
            "agents": agents,
            "chains": chains,
            "bus": bus,
            "scheduler_chain": scheduler_chain,
        }
    }
