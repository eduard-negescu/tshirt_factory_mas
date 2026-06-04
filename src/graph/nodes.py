"""LangGraph nodes for the T-shirt factory simulation."""

import logging
import time
from typing import Any

from langgraph.types import RunnableConfig

from bus import MessageBus
from graph.pipeline import process_order_pipeline
from graph.state import SimulationState
from llm.scheduler_chain import SchedulerChain
from logging_config import clear_trace_id, set_trace_id
from models.llm_models import EquipmentStatusInfo, PendingOrderInfo
from models.messages import AgentMessage

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _build_equipment_statuses(config: RunnableConfig) -> list[EquipmentStatusInfo]:
    """Build EquipmentStatusInfo list from config's equipment references."""
    equipment = config["configurable"]["equipment"]
    return [
        EquipmentStatusInfo(name="printer", status=equipment["printer"].status),
        EquipmentStatusInfo(name="heat_press", status=equipment["heat_press"].status),
        EquipmentStatusInfo(
            name="quality_control", status=equipment["quality_control"].status
        ),
        EquipmentStatusInfo(
            name="packaging", status=equipment["packaging"].status
        ),
    ]


def _build_pending_list(
    pending_orders: dict[str, Any],
    rejected_orders: list[str],
) -> list[PendingOrderInfo]:
    """Build PendingOrderInfo list from pending_orders with rejected escalation."""
    pending = [
        PendingOrderInfo(id=oid, priority=o.priority)
        for oid, o in pending_orders.items()
    ]
    pending_ids = {p.id for p in pending}
    for rid in rejected_orders:
        if rid in pending_ids:
            for p in pending:
                if p.id == rid:
                    p.priority = "urgent"
        else:
            pending.append(PendingOrderInfo(id=rid, priority="urgent"))
    return pending


# ---------------------------------------------------------------------------
# Graph nodes
# ---------------------------------------------------------------------------


def plan_node(state: SimulationState, config: RunnableConfig) -> dict[str, Any]:
    """Call the scheduler LLM to produce an updated processing queue."""
    set_trace_id("[plan]")
    try:
        chain: SchedulerChain = config["configurable"]["scheduler_chain"]
        equipment_statuses = _build_equipment_statuses(config)

        # Determine failed equipment from equipment statuses
        failed_eq = None
        for eq in equipment_statuses:
            if eq.status == "failed":
                failed_eq = eq.name
                break

        pending = _build_pending_list(state.pending_orders, state.rejected_orders)

        logger.info(
            "plan_node: %d pending orders, %d rejected, failed=%s",
            len(pending),
            len(state.rejected_orders),
            failed_eq,
        )

        response = chain.invoke(equipment_statuses, pending, failed_eq)

        re_plan_count = state.re_plan_count + 1

        print(f"\n  🔄 {'Plan' if re_plan_count == 1 else 'Re-plan'} inițial "
              f"#{re_plan_count}: {response.schedule}")
        print(f"     Motiv: {response.reason}\n")

        return {
            "queue": list(response.schedule),
            "schedule_reason": response.reason,
            "re_plan_count": re_plan_count,
            "rejected_orders": [],  # cleared after escalation
            "pipeline_result": "",  # reset for next order
        }
    finally:
        clear_trace_id()


def process_order_node(
    state: SimulationState, config: RunnableConfig
) -> dict[str, Any]:
    """Pop an order from the queue and run it through the pipeline.

    Also handles the forced heat-press failure demo after 3 completions.
    """
    equipment = config["configurable"]["equipment"]
    agents = config["configurable"]["agents"]
    chains = config["configurable"]["chains"]
    bus: MessageBus = config["configurable"]["bus"]

    # --- Forced heat press failure (demo) ---
    if (
        state.completed_count >= 3
        and not state.heat_press_failure_triggered
        and state.queue  # only trigger when there's an order to affect
    ):
        order_id = state.queue[0]
        set_trace_id(f"[replan-{order_id}]")
        try:
            print("\n  🔥🔥🔥 DEFECT FORȚAT LA PRESA TERMICĂ! 🔥🔥🔥\n")
            logger.warning("!!! DEFECT FORȚAT LA PRESA TERMICĂ !!!")

            heat_press_eq = equipment["heat_press"]
            heat_press_eq.status = "failed"

            # Build context for scheduler re-plan
            equipment_statuses = _build_equipment_statuses(config)
            pending = _build_pending_list(
                state.pending_orders, state.rejected_orders
            )

            # Send enriched failure message so scheduler can re-plan
            bus.send(
                AgentMessage(
                    sender="simulation",
                    receiver="scheduler",
                    message_type="equipment_failure",
                    payload={
                        "equipment": "heat_press",
                        "order_id": order_id,
                        "reason": "forced_failure_for_demo",
                        "equipment_statuses": [
                            es.model_dump() for es in equipment_statuses
                        ],
                        "pending_orders": [
                            p.model_dump() for p in pending
                        ],
                    },
                )
            )
            responses = bus.dispatch()

            # Extract scheduler's re-plan from message response
            scheduler_responses = responses.get("scheduler", [])
            schedule_response = scheduler_responses[-1] if scheduler_responses else None

            if schedule_response is not None:
                # Put current order at the front, deduplicate the rest
                new_queue = [order_id] + [
                    oid for oid in schedule_response.schedule
                    if oid != order_id
                ]
                reason = schedule_response.reason
            else:
                # Fallback: keep queue as-is if scheduler didn't respond
                logger.warning("Scheduler did not respond to equipment_failure")
                new_queue = list(state.queue)
                reason = "no scheduler response"

            # Repair after short delay
            time.sleep(0.5)
            heat_press_eq.reset()
            logger.info("Presa termică reparată")
            print("  🔧 Presa termică reparată\n")

            print(f"  🔄 Re-plan după defect #{state.re_plan_count + 1}: "
                  f"{new_queue}")
            print(f"     Motiv: {reason}\n")

            return {
                "heat_press_failure_triggered": True,
                "queue": new_queue,
                "schedule_reason": reason,
                "re_plan_count": state.re_plan_count + 1,
                "pipeline_result": "",  # cleared — continue processing
            }
        finally:
            clear_trace_id()

    # --- Normal flow: pop next order from queue ---
    if not state.queue:
        logger.warning("process_order_node called with empty queue")
        return {"pipeline_result": "empty_queue"}

    order_id = state.queue.pop(0)
    set_trace_id(order_id)
    try:
        remaining_queue = list(state.queue)

        # Skip if order not in pending (should not happen, but defensive)
        if order_id not in state.pending_orders:
            logger.debug("Order %s already processed, skipping", order_id)
            return {"queue": remaining_queue, "pipeline_result": "skip"}

        # Move order from pending to in_progress
        order = state.pending_orders.pop(order_id)
        order.status = "in_progress"
        new_pending = dict(state.pending_orders)
        new_in_progress = {**state.in_progress, order_id: order}

        # Build equipment statuses
        equipment_statuses = _build_equipment_statuses(config)

        # Run the pipeline
        outcome = process_order_pipeline(
            order_id=order_id,
            design_description=order.design_description,
            priority=order.priority,
            printer_agent=agents["printer"],
            hp_agent=agents["heat_press"],
            qc_agent=agents["quality_control"],
            pkg_agent=agents["packaging"],
            bus=bus,
            routing_chain=chains["routing"],
            equipment_statuses=equipment_statuses,
        )

        # --- Handle outcome ---
        new_completed: dict = dict(state.completed_orders)
        new_rejected: list = list(state.rejected_orders)
        result: dict[str, Any] = {
            "queue": remaining_queue,
            "pending_orders": new_pending,
            "in_progress": new_in_progress,
            "pipeline_result": outcome,
            "iteration": state.iteration + 1,
        }

        if outcome == "completed":
            order.status = "completed"
            new_completed[order_id] = order
            new_in_progress.pop(order_id, None)
            completed_count = state.completed_count + 1
            result["completed_orders"] = new_completed
            result["in_progress"] = new_in_progress
            result["completed_count"] = completed_count
            print(f"  ✅ {order_id} FINALIZATĂ "
                  f"({completed_count}/{len(state.all_orders)})")

        elif outcome == "failed_printer":
            order.status = "pending"
            new_pending[order_id] = order
            new_in_progress.pop(order_id, None)
            result["pending_orders"] = new_pending
            result["in_progress"] = new_in_progress
            print(f"  ❌ {order_id} A EȘUAT la Imprimantă - readăugată în coadă")

            # Trigger scheduler re-plan via message BEFORE resetting equipment
            # so the scheduler sees the actual failure state
            failed_eq_statuses = _build_equipment_statuses(config)
            pending = _build_pending_list(
                result["pending_orders"], new_rejected
            )
            bus.send(
                AgentMessage(
                    sender="graph",
                    receiver="scheduler",
                    message_type="equipment_failure",
                    payload={
                        "equipment": "printer",
                        "order_id": order_id,
                        "equipment_statuses": [
                            es.model_dump() for es in failed_eq_statuses
                        ],
                        "pending_orders": [
                            p.model_dump() for p in pending
                        ],
                    },
                )
            )
            responses = bus.dispatch()
            sched_resp = responses.get("scheduler", [None])[-1]
            if sched_resp is not None:
                result["queue"] = [order_id] + [
                    oid for oid in sched_resp.schedule if oid != order_id
                ]
                result["pipeline_result"] = ""  # skip re-plan node
                result["schedule_reason"] = sched_resp.reason
                print(f"     🔄 Scheduler re-plan: {result['queue']}")

            # Reset equipment AFTER scheduler has seen the failure
            equipment["printer"].reset()

        elif outcome == "failed_heat_press":
            order.status = "pending"
            new_pending[order_id] = order
            new_in_progress.pop(order_id, None)
            result["pending_orders"] = new_pending
            result["in_progress"] = new_in_progress
            print(f"  ❌ {order_id} A EȘUAT la Presa Termică - readăugată în coadă")

            # Trigger scheduler re-plan via message BEFORE resetting equipment
            failed_eq_statuses = _build_equipment_statuses(config)
            pending = _build_pending_list(
                result["pending_orders"], new_rejected
            )
            bus.send(
                AgentMessage(
                    sender="graph",
                    receiver="scheduler",
                    message_type="equipment_failure",
                    payload={
                        "equipment": "heat_press",
                        "order_id": order_id,
                        "equipment_statuses": [
                            es.model_dump() for es in failed_eq_statuses
                        ],
                        "pending_orders": [
                            p.model_dump() for p in pending
                        ],
                    },
                )
            )
            responses = bus.dispatch()
            sched_resp = responses.get("scheduler", [None])[-1]
            if sched_resp is not None:
                result["queue"] = [order_id] + [
                    oid for oid in sched_resp.schedule if oid != order_id
                ]
                result["pipeline_result"] = ""
                result["schedule_reason"] = sched_resp.reason
                print(f"     🔄 Scheduler re-plan: {result['queue']}")

            # Reset equipment AFTER scheduler has seen the failure
            equipment["heat_press"].reset()

        elif outcome == "failed_packaging":
            order.status = "pending"
            new_pending[order_id] = order
            new_in_progress.pop(order_id, None)
            result["pending_orders"] = new_pending
            result["in_progress"] = new_in_progress
            print(f"  ❌ {order_id} A EȘUAT la Ambalare - readăugată în coadă")

            # Trigger scheduler re-plan via message BEFORE resetting equipment
            failed_eq_statuses = _build_equipment_statuses(config)
            pending = _build_pending_list(
                result["pending_orders"], new_rejected
            )
            bus.send(
                AgentMessage(
                    sender="graph",
                    receiver="scheduler",
                    message_type="equipment_failure",
                    payload={
                        "equipment": "packaging",
                        "order_id": order_id,
                        "equipment_statuses": [
                            es.model_dump() for es in failed_eq_statuses
                        ],
                        "pending_orders": [
                            p.model_dump() for p in pending
                        ],
                    },
                )
            )
            responses = bus.dispatch()
            sched_resp = responses.get("scheduler", [None])[-1]
            if sched_resp is not None:
                result["queue"] = [order_id] + [
                    oid for oid in sched_resp.schedule if oid != order_id
                ]
                result["pipeline_result"] = ""
                result["schedule_reason"] = sched_resp.reason
                print(f"     🔄 Scheduler re-plan: {result['queue']}")

            # Reset equipment AFTER scheduler has seen the failure
            equipment["packaging"].reset()

        elif outcome == "rejected_qc":
            order.status = "pending"
            order.priority = "urgent"
            new_pending[order_id] = order
            new_in_progress.pop(order_id, None)
            new_rejected.append(order_id)
            result["pending_orders"] = new_pending
            result["in_progress"] = new_in_progress
            result["rejected_orders"] = new_rejected
            print(f"  ❌ {order_id} RESPINSĂ la CQ - readăugată ca urgentă "
                  f"(necesită reimprimare completă)")

        elif outcome == "rework_qc":
            order.rework_count += 1
            new_in_progress.pop(order_id, None)
            if order.rework_count >= state.max_rework:
                logger.warning(
                    "Order %s exceeded max rework (%d), force-completing",
                    order_id,
                    order.rework_count,
                )
                order.status = "completed"
                new_completed[order_id] = order
                completed_count = state.completed_count + 1
                result["completed_orders"] = new_completed
                result["completed_count"] = completed_count
                print(f"  ✅ {order_id} FINALIZATĂ FORȚAT după "
                      f"{order.rework_count} refaceri "
                      f"({completed_count}/{len(state.all_orders)})")
            else:
                order.status = "pending"
                new_pending[order_id] = order
                result["pending_orders"] = new_pending
                print(f"  🔧 {order_id} REFACERE cerută de CQ "
                      f"(încercarea {order.rework_count}/{state.max_rework}) "
                      f"- readăugată în coadă")
            result["in_progress"] = new_in_progress

        return result
    finally:
        clear_trace_id()
