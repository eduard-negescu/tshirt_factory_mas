import logging
from typing import Optional

from equipment.packaging_station import PackagingStation
from llm.packaging_chain import PackagingChain, PackagingLLMError
from models.llm_models import PackagingDecision
from models.messages import AgentMessage

logger = logging.getLogger(__name__)


class PackagingAgent:
    def __init__(
        self,
        equipment: PackagingStation,
        packaging_chain: Optional[PackagingChain] = None,
    ):
        self.name = "packaging"
        self.equipment = equipment
        self.packaging_chain = packaging_chain
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

    def _adjust_failure_probability(self, decision: PackagingDecision) -> float:
        """Modulate failure probability based on LLM parameter choices."""
        base = self._base_failure_probability
        risk = 1.0

        # Gift box = more careful handling = lower risk
        if decision.packaging_type == "gift_box":
            risk -= 0.15

        # Poly mailer = less protection = higher risk
        if decision.packaging_type == "poly_mailer":
            risk += 0.10

        # Rolled folding reduces creasing and handling issues
        if decision.fold_method == "rolled":
            risk -= 0.08

        # Extra items mean more handling steps
        extras = 0
        if decision.include_care_instructions:
            extras += 1
        if decision.include_thank_you_note:
            extras += 1
        if extras:
            risk += 0.05 * extras

        return max(0.01, min(0.20, base * risk))

    def process(
        self,
        order_id: str,
        design_description: str = "",
        priority: str = "normal",
        routing_notes: str = "",
    ) -> dict:
        logger.info("PackagingAgent packaging order %s", order_id)

        # LLM-driven packaging configuration
        if self.packaging_chain is not None:
            try:
                decision: PackagingDecision = self.packaging_chain.invoke(
                    order_id=order_id,
                    design_description=design_description,
                    priority=priority,
                    routing_notes=routing_notes,
                )
            except PackagingLLMError as e:
                logger.warning("Packaging LLM failed, using defaults: %s", e)
                decision = PackagingDecision(
                    order_id=order_id,
                    packaging_type="standard_box",
                    fold_method="standard_fold",
                    include_care_instructions=False,
                    include_thank_you_note=False,
                    notes=f"LLM error, defaulting: {e}",
                )
        else:
            decision = PackagingDecision(
                order_id=order_id,
                packaging_type="standard_box",
                fold_method="standard_fold",
                include_care_instructions=False,
                include_thank_you_note=False,
                notes="No LLM configured — using defaults",
            )

        # Adjust failure probability based on LLM decision
        adjusted_prob = self._adjust_failure_probability(decision)
        self.equipment.failure_probability = adjusted_prob
        logger.debug(
            "Packaging failure probability: %.3f → %.3f (base=%.3f)",
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
