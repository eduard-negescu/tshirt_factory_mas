import logging

from equipment.printer import Printer
from models.messages import AgentMessage

logger = logging.getLogger(__name__)


class PrinterAgent:
    def __init__(self, equipment: Printer):
        self.name = "printer"
        self.equipment = equipment
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

    def process(self, order_id: str) -> dict:
        logger.info("PrinterAgent processing order %s", order_id)
        result = self.equipment.process(order_id)

        if result["success"]:
            self._send(
                "scheduler",
                "processing_complete",
                {"order_id": order_id, "station": "printer"},
            )
        else:
            self._send(
                "scheduler",
                "equipment_failure",
                {
                    "equipment": self.equipment.name,
                    "order_id": order_id,
                    "error": result.get("error"),
                },
            )

        return result

    def handle_message(self, msg: AgentMessage) -> None:
        if msg.message_type == "process_order":
            order_id = msg.payload.get("order_id", "")
            self.process(order_id)
