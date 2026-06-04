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

    def handle_message(self, msg: AgentMessage) -> None:
        """Log incoming messages — state transitions are handled by graph nodes."""
        logger.debug(
            "Scheduler received: %s from %s (state managed by graph)",
            msg.message_type,
            msg.sender,
        )
