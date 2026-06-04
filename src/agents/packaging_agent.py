import logging

from equipment.packaging_station import PackagingStation
from models.messages import AgentMessage

logger = logging.getLogger(__name__)


class PackagingAgent:
    def __init__(self, equipment: PackagingStation):
        self.name = "packaging"
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
        logger.info("PackagingAgent packaging order %s", order_id)
        result = self.equipment.process(order_id)

        if result["success"]:
            self._send(
                "scheduler",
                "order_completed",
                {"order_id": order_id},
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
