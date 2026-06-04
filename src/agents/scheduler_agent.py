import logging

from models.messages import AgentMessage
from models.order import Order
from models.llm_models import EquipmentStatusInfo, PendingOrderInfo, ScheduleResponse
from llm.scheduler_chain import SchedulerChain
from config.settings import Settings

logger = logging.getLogger(__name__)


class SchedulerAgent:
    """Thin scheduler wrapper — state is managed by the LangGraph graph.

    Kept for bus registration contract and setup convenience.
    The plan() method delegates to SchedulerChain; graph nodes call the
    chain directly via configurable for checkpointing compatibility.
    """

    def __init__(self, settings: Settings):
        self.name = "scheduler"
        self.settings = settings
        self.chain = SchedulerChain(settings)
        self._bus = None

    @property
    def bus(self):
        return self._bus

    @bus.setter
    def bus(self, message_bus):
        self._bus = message_bus

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

    def receive_orders(self, orders: list[Order]) -> dict[str, Order]:
        """Return order dict for graph state initialization."""
        return {o.id: o for o in orders}

    def plan(
        self,
        equipment_statuses: list[EquipmentStatusInfo],
        pending: list[PendingOrderInfo],
        failed_equipment: str | None = None,
    ) -> ScheduleResponse:
        """Delegate to the scheduler LLM chain."""
        logger.info(
            "SchedulerAgent requesting plan. Equipment: %s, Pending: %d, Failed: %s",
            [(e.name, e.status) for e in equipment_statuses],
            len(pending),
            failed_equipment,
        )
        response = self.chain.invoke(equipment_statuses, pending, failed_equipment)
        logger.info("Schedule: %s | Reason: %s", response.schedule, response.reason)
        return response

    def handle_message(self, msg: AgentMessage):
        """React to incoming messages with agent-driven decisions.

        - equipment_failure: triggers an immediate re-plan via the LLM
          chain and returns a ScheduleResponse with the new queue.
        - All other message types are logged for observability.
        """
        if msg.message_type == "equipment_failure":
            return self._handle_equipment_failure(msg)

        logger.debug(
            "Scheduler received: %s from %s (state managed by graph)",
            msg.message_type,
            msg.sender,
        )
        return None

    def _handle_equipment_failure(self, msg: AgentMessage):
        """Re-plan on equipment failure using context from the message."""
        payload = msg.payload

        equipment_statuses_raw = payload.get("equipment_statuses", [])
        equipment_statuses = [
            EquipmentStatusInfo(**e) if isinstance(e, dict) else e
            for e in equipment_statuses_raw
        ]

        pending_raw = payload.get("pending_orders", [])
        pending = [
            PendingOrderInfo(**p) if isinstance(p, dict) else p
            for p in pending_raw
        ]

        failed_eq = payload.get("equipment", payload.get("failed_equipment", ""))

        logger.info(
            "Scheduler re-planning due to %s failure. Equipment: %s, Pending: %d",
            failed_eq,
            [(e.name, e.status) for e in equipment_statuses],
            len(pending),
        )

        response = self.chain.invoke(equipment_statuses, pending, failed_eq)
        logger.info(
            "Message-driven re-plan: %s | Reason: %s",
            response.schedule,
            response.reason,
        )
        return response
