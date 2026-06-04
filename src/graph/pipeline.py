"""Per-order pipeline execution — extracted from main.py.

Runs one order through stations based on LLM routing decision.
Stateless: all dependencies are passed as parameters.
"""

import logging

from bus import MessageBus
from llm.routing_chain import RoutingChain
from models.llm_models import EquipmentStatusInfo, RoutingDecision, StationRoute
from models.messages import AgentMessage

logger = logging.getLogger(__name__)


def process_order_pipeline(
    order_id: str,
    design_description: str,
    priority: str,
    printer_agent,
    hp_agent,
    qc_agent,
    pkg_agent,
    bus: MessageBus,
    routing_chain: RoutingChain,
    equipment_statuses: list[EquipmentStatusInfo],
) -> str:
    """Run one order through the pipeline using LLM-driven routing.

    Returns one of: "completed", "failed_printer", "failed_heat_press",
    "failed_packaging", "rejected_qc", "rework_qc".
    """

    logger.info("=== Processing %s: requesting LLM routing ===", order_id)
    try:
        routing: RoutingDecision = routing_chain.invoke(
            order_id=order_id,
            design_description=design_description,
            priority=priority,
            equipment_statuses=equipment_statuses,
        )
    except Exception as e:
        logger.error(
            "Routing LLM failed for %s, using default route: %s", order_id, e
        )
        routing = RoutingDecision(
            order_id=order_id,
            route=[
                StationRoute(
                    station="printer", required=True, notes="default fallback"
                ),
                StationRoute(
                    station="heat_press", required=True, notes="default fallback"
                ),
                StationRoute(
                    station="quality_control",
                    required=True,
                    notes="default fallback",
                ),
                StationRoute(
                    station="packaging", required=True, notes="default fallback"
                ),
            ],
            reason=f"LLM error, using default route: {e}",
        )

    print(
        f"  🧭 Rută LLM pentru {order_id}: "
        f"{[(r.station, r.required) for r in routing.route]}"
    )
    print(f"     {routing.reason}")

    # Build per-station routing notes to pass to agents
    routing_notes: dict[str, str] = {
        r.station: r.notes for r in routing.route
    }

    processing_parts: list[str] = []
    printer_cfg: dict = {}
    heat_press_cfg: dict = {}

    for step in routing.route:
        station = step.station
        required = step.required

        if not required:
            logger.info("Skipping %s for %s: %s", station, order_id, step.notes)
            processing_parts.append(f"{station}: sărită ({step.notes})")
            continue

        if station == "printer":
            logger.info("=== Processing %s: Printer stage ===", order_id)
            if printer_agent.equipment.status == "failed":
                printer_agent.equipment.reset()
            result = printer_agent.process(
                order_id,
                design_description=design_description,
                priority=priority,
                routing_notes=routing_notes.get("printer", ""),
            )
            bus.dispatch()
            if not result["success"]:
                logger.error("Order %s failed at Printer", order_id)
                return "failed_printer"
            processing_parts.append("printer: completed")
            if "llm_decision" in result:
                printer_cfg = result["llm_decision"]

        elif station == "heat_press":
            logger.info("=== Processing %s: HeatPress stage ===", order_id)
            if hp_agent.equipment.status == "failed":
                result_hp = {
                    "success": False,
                    "order_id": order_id,
                    "error": "heat_press_failure",
                }
            else:
                result_hp = hp_agent.process(
                    order_id,
                    design_description=design_description,
                    priority=priority,
                    routing_notes=routing_notes.get("heat_press", ""),
                )
            bus.dispatch()
            if not result_hp["success"]:
                logger.error("Order %s failed at HeatPress", order_id)
                return "failed_heat_press"
            processing_parts.append("heat_press: completed")
            if "llm_decision" in result_hp:
                heat_press_cfg = result_hp["llm_decision"]

        elif station == "quality_control":
            logger.info("=== Processing %s: QualityControl stage ===", order_id)

            # Send station history to QC agent so it can adjust inspection criteria
            stations_used = [
                p.split(":")[0] for p in processing_parts if ": completed" in p
            ]
            bus.send(
                AgentMessage(
                    sender="pipeline",
                    receiver="quality_control",
                    message_type="station_history",
                    payload={
                        "order_id": order_id,
                        "stations_used": stations_used,
                        "history": "; ".join(processing_parts),
                        "printer_config": printer_cfg,
                        "heat_press_config": heat_press_cfg,
                    },
                )
            )
            bus.dispatch()

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
            result_pkg = pkg_agent.process(
                order_id,
                design_description=design_description,
                priority=priority,
                routing_notes=routing_notes.get("packaging", ""),
            )
            bus.dispatch()
            if not result_pkg["success"]:
                logger.error("Order %s failed at Packaging", order_id)
                return "failed_packaging"
            processing_parts.append("packaging: completed")

    return "completed"
