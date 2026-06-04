import logging

from models.messages import AgentMessage
from models.order import Order
from models.llm_models import EquipmentStatusInfo, PendingOrderInfo, ScheduleResponse
from llm.scheduler_chain import SchedulerChain
from config.settings import Settings

logger = logging.getLogger(__name__)


class SchedulerAgent:
    def __init__(self, settings: Settings):
        self.name = "scheduler"
        self.settings = settings
        self.chain = SchedulerChain(settings)
        self.pending_orders: dict[str, Order] = {}
        self.in_progress: dict[str, Order] = {}
        self.completed_orders: dict[str, Order] = {}
        self.rejected_orders: list[str] = []
        self.current_schedule: list[str] = []
        self.schedule_reason: str = ""
        self._bus = None

    @property
    def bus(self):
        return self._bus

    @bus.setter
    def bus(self, message_bus):
        self._bus = message_bus

    def receive_orders(self, orders: list[Order]) -> None:
        for order in orders:
            self.pending_orders[order.id] = order
        logger.info(
            "Scheduler received %d orders. Pending: %d",
            len(orders),
            len(self.pending_orders),
        )

    def _send(self, receiver: str, msg_type: str, payload: dict) -> None:
        if self._bus:
            self._bus.send(
                AgentMessage(
                    sender=self.name,
                    receiver=receiver,
                    message_type=msg_type,
                    payload=payload,
                )
            )

    def plan(
        self,
        equipment_statuses: list[EquipmentStatusInfo],
        failed_equipment: str | None = None,
    ) -> ScheduleResponse:
        pending = [
            PendingOrderInfo(id=o.id, priority=o.priority)
            for o in self.pending_orders.values()
        ]
        # Escalate rejected orders to urgent (already in pending_orders)
        pending_ids = {p.id for p in pending}
        for rid in self.rejected_orders:
            if rid in pending_ids:
                for p in pending:
                    if p.id == rid:
                        p.priority = "urgent"
            else:
                pending.append(PendingOrderInfo(id=rid, priority="urgent"))

        logger.info(
            "SchedulerAgent requesting plan. Equipment: %s, Pending: %d, Failed: %s",
            [(e.name, e.status) for e in equipment_statuses],
            len(pending),
            failed_equipment,
        )

        response = self.chain.invoke(equipment_statuses, pending, failed_equipment)
        self.current_schedule = response.schedule
        self.schedule_reason = response.reason
        self.rejected_orders.clear()

        logger.info("Schedule: %s | Reason: %s", response.schedule, response.reason)
        return response

    def handle_message(self, msg: AgentMessage) -> None:
        logger.debug("Scheduler received: %s from %s", msg.message_type, msg.sender)

        if msg.message_type == "equipment_failure":
            equipment_name = msg.payload.get("equipment", "unknown")
            failed_order = msg.payload.get("order_id")
            logger.warning(
                "Scheduler notified: %s failed (order %s)",
                equipment_name,
                failed_order,
            )

            if failed_order and failed_order in self.in_progress:
                order = self.in_progress.pop(failed_order)
                order.status = "pending"
                self.pending_orders[failed_order] = order

        elif msg.message_type == "processing_complete":
            order_id = msg.payload.get("order_id", "")
            station = msg.sender
            logger.info("Scheduler: %s completed at %s", order_id, station)

        elif msg.message_type == "quality_rejected":
            order_id = msg.payload.get("order_id", "")
            logger.warning("Scheduler: %s was rejected by QC, re-queuing", order_id)
            if order_id not in self.rejected_orders:
                self.rejected_orders.append(order_id)

        elif msg.message_type == "quality_rework":
            order_id = msg.payload.get("order_id", "")
            logger.warning(
                "Scheduler: %s needs rework — %s",
                order_id,
                msg.payload.get("rework_instructions", "no instructions"),
            )
            # Move back to pending so it gets re-planned (no priority escalation for rework)

        elif msg.message_type == "order_completed":
            order_id = msg.payload.get("order_id", "")
            if order_id in self.pending_orders:
                order = self.pending_orders.pop(order_id)
            elif order_id in self.in_progress:
                order = self.in_progress.pop(order_id)
            else:
                logger.warning("Completed unknown order %s", order_id)
                return
            order.status = "completed"
            self.completed_orders[order_id] = order
            logger.info("Scheduler: order %s COMPLETED", order_id)
