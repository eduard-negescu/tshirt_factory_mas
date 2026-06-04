import logging
from typing import Optional

from equipment.printer import Printer
from llm.printer_chain import PrinterChain, PrinterLLMError
from models.llm_models import PrinterDecision
from models.messages import AgentMessage

logger = logging.getLogger(__name__)


class PrinterAgent:
    def __init__(
        self,
        equipment: Printer,
        printer_chain: Optional[PrinterChain] = None,
    ):
        self.name = "printer"
        self.equipment = equipment
        self.printer_chain = printer_chain
        self._base_failure_probability = equipment.failure_probability
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

    def _adjust_failure_probability(self, decision: PrinterDecision) -> float:
        """Modulate failure probability based on LLM parameter choices."""
        base = self._base_failure_probability
        risk = 1.0

        # More passes → more careful execution
        if decision.number_of_passes >= 3:
            risk -= 0.15
        elif decision.number_of_passes >= 2:
            risk -= 0.08

        # Heavy ink saturation increases risk of smudging
        if decision.ink_saturation == "heavy":
            risk += 0.12

        # High temperature can cause scorching or bleeding
        if decision.print_temperature == "high":
            risk += 0.10

        # Low temperature with complex designs may cause poor adhesion
        if decision.print_temperature == "low":
            risk += 0.05

        return max(0.02, min(0.25, base * risk))

    def process(
        self,
        order_id: str,
        design_description: str = "",
        priority: str = "normal",
        routing_notes: str = "",
    ) -> dict:
        logger.info("PrinterAgent processing order %s", order_id)

        # LLM-driven printer configuration
        if self.printer_chain is not None:
            try:
                decision: PrinterDecision = self.printer_chain.invoke(
                    order_id=order_id,
                    design_description=design_description,
                    priority=priority,
                    routing_notes=routing_notes,
                )
            except PrinterLLMError as e:
                logger.warning("Printer LLM failed, using defaults: %s", e)
                decision = PrinterDecision(
                    order_id=order_id,
                    print_temperature="standard",
                    ink_saturation="normal",
                    number_of_passes=2,
                    color_profile="standard",
                    notes=f"LLM error, defaulting: {e}",
                )
        else:
            decision = PrinterDecision(
                order_id=order_id,
                print_temperature="standard",
                ink_saturation="normal",
                number_of_passes=2,
                color_profile="standard",
                notes="No LLM configured — using defaults",
            )

        # Adjust failure probability based on LLM decision
        adjusted_prob = self._adjust_failure_probability(decision)
        self.equipment.failure_probability = adjusted_prob
        logger.debug(
            "Printer failure probability: %.3f → %.3f (base=%.3f)",
            self._base_failure_probability,
            adjusted_prob,
            self._base_failure_probability,
        )

        result = self.equipment.process(order_id)

        # Restore base failure probability
        self.equipment.failure_probability = self._base_failure_probability

        result["llm_decision"] = decision.model_dump()

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
