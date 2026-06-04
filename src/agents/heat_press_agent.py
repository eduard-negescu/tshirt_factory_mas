import logging
from typing import Optional

from equipment.heat_press import HeatPress
from llm.heat_press_chain import HeatPressChain, HeatPressLLMError
from models.llm_models import HeatPressDecision
from models.messages import AgentMessage

logger = logging.getLogger(__name__)


class HeatPressAgent:
    def __init__(
        self,
        equipment: HeatPress,
        heat_press_chain: Optional[HeatPressChain] = None,
    ):
        self.name = "heat_press"
        self.equipment = equipment
        self.heat_press_chain = heat_press_chain
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

    def _adjust_failure_probability(self, decision: HeatPressDecision) -> float:
        """Modulate failure probability based on LLM parameter choices."""
        base = self._base_failure_probability
        risk = 1.0

        # Multi-pass = more careful handling
        if decision.multi_pass:
            risk -= 0.12

        # Extended dwell time increases scorching risk
        if decision.dwell_time == "extended":
            risk += 0.15

        # High temperature + firm pressure = highest risk combo
        if decision.temperature == "high" and decision.pressure == "firm":
            risk += 0.18
        elif decision.temperature == "high":
            risk += 0.10

        # Low temperature may cause incomplete curing
        if decision.temperature == "low":
            risk += 0.08

        return max(0.02, min(0.25, base * risk))

    def process(
        self,
        order_id: str,
        design_description: str = "",
        priority: str = "normal",
        routing_notes: str = "",
    ) -> dict:
        logger.info("HeatPressAgent processing order %s", order_id)

        # LLM-driven heat press configuration
        if self.heat_press_chain is not None:
            try:
                decision: HeatPressDecision = self.heat_press_chain.invoke(
                    order_id=order_id,
                    design_description=design_description,
                    priority=priority,
                    routing_notes=routing_notes,
                )
            except HeatPressLLMError as e:
                logger.warning("HeatPress LLM failed, using defaults: %s", e)
                decision = HeatPressDecision(
                    order_id=order_id,
                    temperature="medium",
                    dwell_time="standard",
                    pressure="medium",
                    multi_pass=False,
                    notes=f"LLM error, defaulting: {e}",
                )
        else:
            decision = HeatPressDecision(
                order_id=order_id,
                temperature="medium",
                dwell_time="standard",
                pressure="medium",
                multi_pass=False,
                notes="No LLM configured — using defaults",
            )

        # Adjust failure probability based on LLM decision
        adjusted_prob = self._adjust_failure_probability(decision)
        self.equipment.failure_probability = adjusted_prob
        logger.debug(
            "HeatPress failure probability: %.3f → %.3f (base=%.3f)",
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
                {"order_id": order_id, "station": "heat_press"},
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
