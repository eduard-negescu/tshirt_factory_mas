import logging
import random
import sys
import time
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
from llm.qc_chain import QCChain
from llm.routing_chain import RoutingChain
from models.llm_models import EquipmentStatusInfo, QualityDecision, RoutingDecision
from models.messages import AgentMessage
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

    file_handler = logging.FileHandler(log_file, encoding="utf-8")
    file_handler.setLevel(logging.DEBUG)
    file_fmt = logging.Formatter(
        "%(asctime)s | %(levelname)-8s | %(name)s | %(message)s"
    )
    file_handler.setFormatter(file_fmt)

    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setLevel(logging.INFO)
    console_fmt = logging.Formatter("%(levelname)-8s | %(name)s | %(message)s")
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
# Equipment status helpers
# ---------------------------------------------------------------------------


def get_equipment_statuses(printer, heat_press, qc, packaging):
    return [
        EquipmentStatusInfo(name="printer", status=printer.status),
        EquipmentStatusInfo(name="heat_press", status=heat_press.status),
        EquipmentStatusInfo(name="quality_control", status=qc.status),
        EquipmentStatusInfo(name="packaging", status=packaging.status),
    ]


# ---------------------------------------------------------------------------
# Pipeline processing
# ---------------------------------------------------------------------------


def process_order_pipeline(
    order_id: str,
    printer_agent: PrinterAgent,
    hp_agent: HeatPressAgent,
    qc_agent: QualityControlAgent,
    pkg_agent: PackagingAgent,
    bus: MessageBus,
    routing_chain: RoutingChain,
    equipment_statuses: list[EquipmentStatusInfo],
    design_description: str = "",
    priority: str = "normal",
) -> str:
    """Run one order through the pipeline using LLM-driven routing.

    The LLM decides which stations are required and in what order.
    Returns the outcome string.
    """

    # --- Stage 0: Get LLM routing decision ---
    logger.info("=== Processing %s: requesting LLM routing ===", order_id)
    try:
        routing: RoutingDecision = routing_chain.invoke(
            order_id=order_id,
            design_description=design_description,
            priority=priority,
            equipment_statuses=equipment_statuses,
        )
    except Exception as e:
        logger.error("Routing LLM failed for %s, using default route: %s", order_id, e)
        # Fallback: all stations required in default order
        from models.llm_models import StationRoute

        routing = RoutingDecision(
            order_id=order_id,
            route=[
                StationRoute(station="printer", required=True, notes="default fallback"),
                StationRoute(station="heat_press", required=True, notes="default fallback"),
                StationRoute(station="quality_control", required=True, notes="default fallback"),
                StationRoute(station="packaging", required=True, notes="default fallback"),
            ],
            reason=f"LLM error, using default route: {e}",
        )

    print(f"  🧭 LLM route for {order_id}: {[(r.station, r.required) for r in routing.route]}")
    print(f"     {routing.reason}")

    # Build processing history for QC
    processing_parts: list[str] = []

    # --- Execute each station in the route ---
    for step in routing.route:
        station = step.station
        required = step.required

        if not required:
            logger.info("Skipping %s for %s: %s", station, order_id, step.notes)
            processing_parts.append(f"{station}: skipped ({step.notes})")
            continue

        if station == "printer":
            logger.info("=== Processing %s: Printer stage ===", order_id)
            if printer_agent.equipment.status == "failed":
                printer_agent.equipment.reset()
            result = printer_agent.process(order_id)
            bus.dispatch()
            if not result["success"]:
                logger.error("Order %s failed at Printer", order_id)
                return "failed_printer"
            processing_parts.append(f"printer: completed")

        elif station == "heat_press":
            logger.info("=== Processing %s: HeatPress stage ===", order_id)
            if hp_agent.equipment.status == "failed":
                result_hp = {
                    "success": False,
                    "order_id": order_id,
                    "error": "heat_press_failure",
                }
            else:
                result_hp = hp_agent.process(order_id)
            bus.dispatch()
            if not result_hp["success"]:
                logger.error("Order %s failed at HeatPress", order_id)
                return "failed_heat_press"
            processing_parts.append(f"heat_press: completed")

        elif station == "quality_control":
            logger.info("=== Processing %s: QualityControl stage ===", order_id)
            processing_history = "; ".join(processing_parts)
            result_qc = qc_agent.process(
                order_id,
                design_description=design_description,
                priority=priority,
                processing_history=processing_history,
            )
            bus.dispatch()
            if result_qc.get("passed") is False:
                verdict = result_qc.get("verdict", "fail")
                logger.warning(
                    "Order %s QC verdict: %s — %s",
                    order_id,
                    verdict,
                    result_qc.get("reason", ""),
                )
                if verdict == "rework":
                    return "rework_qc"
                else:
                    return "rejected_qc"
            processing_parts.append(
                f"quality_control: passed ({result_qc.get('reason', '')})"
            )

        elif station == "packaging":
            logger.info("=== Processing %s: Packaging stage ===", order_id)
            if pkg_agent.equipment.status == "failed":
                pkg_agent.equipment.reset()
            result_pkg = pkg_agent.process(order_id)
            bus.dispatch()
            if not result_pkg["success"]:
                logger.error("Order %s failed at Packaging", order_id)
                return "failed_packaging"
            processing_parts.append("packaging: completed")

    return "completed"


# ---------------------------------------------------------------------------
# Main simulation
# ---------------------------------------------------------------------------


def main() -> None:
    setup_logging()
    settings = Settings()

    logger.info("=" * 60)
    logger.info("TSHIRT MAS - Multi-Agent System Demo")
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

    # --- Create agents ---
    scheduler = SchedulerAgent(settings)
    printer_agent = PrinterAgent(printer_eq)
    hp_agent = HeatPressAgent(heat_press_eq)
    qc_agent = QualityControlAgent(qc_eq, qc_chain=qc_chain)
    pkg_agent = PackagingAgent(packaging_eq)

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
    scheduler.receive_orders(orders)

    print("\n" + "=" * 60)
    print("  T-SHIRT FACTORY MULTI-AGENT SYSTEM")
    print("=" * 60)
    print(f"\n  Generated {len(orders)} orders:")
    for o in orders:
        tag = "⚡ URGENT" if o.priority == "urgent" else "   normal"
        print(f"    {o.id}  {tag}  [{o.design_name}]")
    print()

    # --- Initial LLM schedule ---
    equipment_statuses = get_equipment_statuses(
        printer_eq, heat_press_eq, qc_eq, packaging_eq
    )
    schedule_resp = scheduler.plan(equipment_statuses)
    queue = list(schedule_resp.schedule)

    print(f"  Initial LLM schedule: {queue}")
    print(f"  Reason: {schedule_resp.reason}")
    print()

    # --- Simulation state ---
    heat_press_failure_triggered = False
    failure_trigger_after = 3
    completed_count = 0
    re_plan_count = 0
    max_iterations = 50
    iteration = 0

    all_orders_map: dict[str, Order] = {o.id: o for o in orders}

    print("-" * 60)
    print("  Starting pipeline execution...")
    print("-" * 60)
    print()

    while scheduler.pending_orders and iteration < max_iterations:
        iteration += 1

        # Get a fresh schedule if queue is empty
        if not queue:
            equipment_statuses = get_equipment_statuses(
                printer_eq, heat_press_eq, qc_eq, packaging_eq
            )
            failed_eq = (
                "heat_press"
                if heat_press_eq.status == "failed"
                else (
                    "printer"
                    if printer_eq.status == "failed"
                    else None
                )
            )
            schedule_resp = scheduler.plan(equipment_statuses, failed_eq)
            queue = list(schedule_resp.schedule)
            re_plan_count += 1
            print(f"\n  🔄 Re-plan #{re_plan_count}: {queue}")
            print(f"     Reason: {schedule_resp.reason}\n")

            if not queue:
                logger.warning("No orders in schedule, breaking")
                break

        order_id = queue.pop(0)

        if order_id not in scheduler.pending_orders:
            logger.debug("Order %s already processed, skipping", order_id)
            continue

        order = scheduler.pending_orders.pop(order_id)
        order.status = "in_progress"
        scheduler.in_progress[order_id] = order

        # --- Force heat press failure ---
        if (
            not heat_press_failure_triggered
            and completed_count >= failure_trigger_after
        ):
            heat_press_eq.status = "failed"
            heat_press_failure_triggered = True
            print("\n  🔥🔥🔥 FORCED HEAT PRESS FAILURE TRIGGERED! 🔥🔥🔥\n")
            logger.warning("!!! FORCED HEAT PRESS FAILURE !!!")

            bus.send(
                AgentMessage(
                    sender="simulation",
                    receiver="scheduler",
                    message_type="equipment_failure",
                    payload={
                        "equipment": "heat_press",
                        "order_id": order_id,
                        "reason": "forced_failure_for_demo",
                    },
                )
            )
            bus.dispatch()

            # Re-plan
            equipment_statuses = get_equipment_statuses(
                printer_eq, heat_press_eq, qc_eq, packaging_eq
            )
            schedule_resp = scheduler.plan(equipment_statuses, "heat_press")
            queue = list(schedule_resp.schedule)
            re_plan_count += 1
            print(f"  🔄 Re-plan after failure #{re_plan_count}: {queue}")
            print(f"     Reason: {schedule_resp.reason}\n")

            # Repair heat press after re-plan
            time.sleep(0.5)
            heat_press_eq.reset()
            logger.info("Heat press repaired")
            print("  🔧 Heat press repaired\n")

            # Put current order back in pending
            order.status = "pending"
            scheduler.pending_orders[order_id] = order
            if order_id in scheduler.in_progress:
                del scheduler.in_progress[order_id]
            if order_id not in queue:
                queue.insert(0, order_id)
            continue

        # --- Run through pipeline ---
        # Build equipment statuses for this iteration
        equipment_statuses = get_equipment_statuses(
            printer_eq, heat_press_eq, qc_eq, packaging_eq
        )
        outcome = process_order_pipeline(
            order_id,
            printer_agent,
            hp_agent,
            qc_agent,
            pkg_agent,
            bus,
            routing_chain,
            equipment_statuses,
            design_description=order.design_description,
            priority=order.priority,
        )

        if outcome == "completed":
            order.status = "completed"
            scheduler.completed_orders[order_id] = order
            if order_id in scheduler.in_progress:
                del scheduler.in_progress[order_id]
            completed_count += 1
            print(f"  ✅ {order_id} COMPLETED ({completed_count}/{len(orders)})")

        elif outcome == "failed_printer":
            printer_eq.reset()
            order.status = "pending"
            scheduler.pending_orders[order_id] = order
            if order_id in scheduler.in_progress:
                del scheduler.in_progress[order_id]
            print(f"  ❌ {order_id} FAILED at Printer - re-queued")
            # Re-plan
            equipment_statuses = get_equipment_statuses(
                printer_eq, heat_press_eq, qc_eq, packaging_eq
            )
            schedule_resp = scheduler.plan(equipment_statuses, "printer")
            queue = list(schedule_resp.schedule)
            re_plan_count += 1

        elif outcome == "failed_heat_press":
            heat_press_eq.reset()
            order.status = "pending"
            scheduler.pending_orders[order_id] = order
            if order_id in scheduler.in_progress:
                del scheduler.in_progress[order_id]
            print(f"  ❌ {order_id} FAILED at HeatPress - re-queued")
            equipment_statuses = get_equipment_statuses(
                printer_eq, heat_press_eq, qc_eq, packaging_eq
            )
            schedule_resp = scheduler.plan(equipment_statuses, "heat_press")
            queue = list(schedule_resp.schedule)
            re_plan_count += 1

        elif outcome == "rejected_qc":
            order.status = "pending"
            order.priority = "urgent"
            scheduler.pending_orders[order_id] = order
            scheduler.rejected_orders.append(order_id)
            if order_id in scheduler.in_progress:
                del scheduler.in_progress[order_id]
            print(f"  ❌ {order_id} FAILED QC - re-queued as urgent (full reprint needed)")
            equipment_statuses = get_equipment_statuses(
                printer_eq, heat_press_eq, qc_eq, packaging_eq
            )
            schedule_resp = scheduler.plan(equipment_statuses)
            queue = list(schedule_resp.schedule)
            re_plan_count += 1

        elif outcome == "rework_qc":
            order.rework_count += 1
            max_rework = 2
            if order.rework_count >= max_rework:
                # Force-pass after too many rework attempts
                logger.warning(
                    "Order %s exceeded max rework (%d), force-completing",
                    order_id,
                    order.rework_count,
                )
                order.status = "completed"
                scheduler.completed_orders[order_id] = order
                if order_id in scheduler.in_progress:
                    del scheduler.in_progress[order_id]
                completed_count += 1
                print(f"  ✅ {order_id} FORCE-COMPLETED after {order.rework_count} reworks ({completed_count}/{len(orders)})")
            else:
                order.status = "pending"
                scheduler.pending_orders[order_id] = order
                if order_id in scheduler.in_progress:
                    del scheduler.in_progress[order_id]
                print(f"  🔧 {order_id} REWORK by QC (attempt {order.rework_count}/{max_rework}) - re-queued")
                equipment_statuses = get_equipment_statuses(
                    printer_eq, heat_press_eq, qc_eq, packaging_eq
                )
                schedule_resp = scheduler.plan(equipment_statuses)
                queue = list(schedule_resp.schedule)
                re_plan_count += 1

        elif outcome == "failed_packaging":
            packaging_eq.reset()
            order.status = "pending"
            scheduler.pending_orders[order_id] = order
            if order_id in scheduler.in_progress:
                del scheduler.in_progress[order_id]
            print(f"  ❌ {order_id} FAILED at Packaging - re-queued")
            equipment_statuses = get_equipment_statuses(
                printer_eq, heat_press_eq, qc_eq, packaging_eq
            )
            schedule_resp = scheduler.plan(equipment_statuses, "packaging")
            queue = list(schedule_resp.schedule)
            re_plan_count += 1

    # --- End of simulation ---
    print("\n" + "=" * 60)
    print("  SIMULATION COMPLETE")
    print("=" * 60)

    # --- Statistics ---
    completed = len(scheduler.completed_orders)
    pending = len(scheduler.pending_orders)
    in_progress = len(scheduler.in_progress)
    total = len(orders)

    print(f"\n  📊 FINAL STATISTICS")
    print(f"  {'─' * 40}")
    print(f"  Total orders:           {total}")
    print(f"  Completed:              {completed}")
    print(f"  Still pending:          {pending}")
    print(f"  In progress:            {in_progress}")
    print(f"  Completion rate:        {completed / total * 100:.1f}%")
    print(f"  LLM re-plans:           {re_plan_count}")
    print(f"  Heat press failures:    {1 if heat_press_failure_triggered else 0} (forced)")

    urgent_total = sum(1 for o in orders if o.priority == "urgent")
    urgent_completed = sum(
        1
        for o in scheduler.completed_orders.values()
        if o.priority == "urgent"
    )
    print(f"  Urgent orders:          {urgent_completed}/{urgent_total} completed")

    # Per-order status
    print(f"\n  📋 ORDER STATUS:")
    for o in orders:
        status_icon = {
            "completed": "✅",
            "pending": "⏳",
            "in_progress": "🔄",
        }.get(o.status, "❓")
        print(f"    {status_icon} {o.id} [{o.priority:6s}] {o.status}")

    print(f"\n  📝 Full logs: {LOG_DIR / 'app.log'}")
    print("=" * 60)


if __name__ == "__main__":
    main()
