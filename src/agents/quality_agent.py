import logging
from typing import Optional

from equipment.quality_station import QualityStation
from llm.qc_chain import QCChain
from models.messages import AgentMessage
from models.llm_models import QualityDecision

logger = logging.getLogger(__name__)


class QualityControlAgent:
    def __init__(
        self,
        equipment: QualityStation,
        qc_chain: Optional[QCChain] = None,
    ):
        self.name = "quality_control"
        self.equipment = equipment
        self.qc_chain = qc_chain
        self._bus = None
        self.station_history_context: str = ""
        self.inspection_strictness: str = "normal"

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

    def process(
        self,
        order_id: str,
        design_description: str = "",
        priority: str = "normal",
        processing_history: str = "",
    ) -> dict:
        logger.info("QualityControlAgent inspecting order %s", order_id)

        # Simulate physical inspection time
        self.equipment.inspect(order_id)

        # Get quality verdict from LLM
        if self.qc_chain is not None:
            try:
                decision: QualityDecision = self.qc_chain.invoke(
                    order_id=order_id,
                    design_description=design_description,
                    priority=priority,
                    processing_history=processing_history,
                    inspection_strictness=self.inspection_strictness,
                )
            except Exception as e:
                logger.error("QC LLM failed, defaulting to pass: %s", e)
                decision = QualityDecision(
                    verdict="pass",
                    reason=f"LLM error, defaulting to pass: {e}",
                    defect_severity="none",
                )
        else:
            # Fallback: deterministic pass (no LLM available)
            decision = QualityDecision(
                verdict="pass",
                reason="No LLM configured — auto-passing",
                defect_severity="none",
            )

        logger.info(
            "QC verdict for %s: %s (severity=%s) — %s",
            order_id,
            decision.verdict,
            decision.defect_severity,
            decision.reason,
        )

        # Build result and send appropriate message
        if decision.verdict == "pass":
            self._send(
                "scheduler",
                "processing_complete",
                {"order_id": order_id, "station": "quality_control"},
            )
            return {
                "success": True,
                "order_id": order_id,
                "passed": True,
                "verdict": "pass",
                "reason": decision.reason,
                "defect_severity": decision.defect_severity,
            }
        elif decision.verdict == "rework":
            self._send(
                "scheduler",
                "quality_rework",
                {
                    "order_id": order_id,
                    "reason": decision.reason,
                    "rework_instructions": decision.rework_instructions,
                    "defect_severity": decision.defect_severity,
                },
            )
            return {
                "success": True,
                "order_id": order_id,
                "passed": False,
                "verdict": "rework",
                "reason": decision.reason,
                "rework_instructions": decision.rework_instructions,
                "defect_severity": decision.defect_severity,
            }
        else:  # fail
            self._send(
                "scheduler",
                "quality_rejected",
                {
                    "order_id": order_id,
                    "reason": decision.reason,
                    "defect_severity": decision.defect_severity,
                },
            )
            return {
                "success": True,
                "order_id": order_id,
                "passed": False,
                "verdict": "fail",
                "reason": decision.reason,
                "defect_severity": decision.defect_severity,
            }

    def handle_message(self, msg: AgentMessage) -> None:
        if msg.message_type == "station_history":
            self._adjust_from_station_history(msg)
        elif msg.message_type == "process_order":
            order_id = msg.payload.get("order_id", "")
            self.process(order_id)

    def _adjust_from_station_history(self, msg: AgentMessage) -> None:
        """Adjust inspection strictness based on which stations processed the order.

        Fewer stations → stricter inspection (less processing = higher defect risk).
        More stations → normal/lenient (thorough processing = lower risk).
        """
        stations_used: list[str] = msg.payload.get("stations_used", [])
        printer_cfg = msg.payload.get("printer_config", {})
        heat_press_cfg = msg.payload.get("heat_press_config", {})

        # Base strictness on number of stations the order went through
        station_count = len(stations_used)
        if station_count <= 1:
            self.inspection_strictness = "high"
        elif station_count == 2:
            self.inspection_strictness = "elevated"
        else:
            self.inspection_strictness = "normal"

        # Modulate based on risky station parameters
        risk_modifiers = 0

        # Heavy ink saturation → more smudging risk → stricter
        if printer_cfg.get("ink_saturation") == "heavy":
            risk_modifiers += 1

        # High temp + firm pressure on heat press → scorching risk
        if heat_press_cfg.get("temperature") == "high" and heat_press_cfg.get("pressure") == "firm":
            risk_modifiers += 1

        # Upgrade strictness if risk modifiers push it up
        if risk_modifiers >= 2 and self.inspection_strictness == "normal":
            self.inspection_strictness = "elevated"
        elif risk_modifiers >= 1 and self.inspection_strictness == "normal":
            self.inspection_strictness = "elevated"

        self.station_history_context = msg.payload.get("history", "")

        logger.info(
            "QC strictness adjusted to '%s' (stations=%d, risk_modifiers=%d, "
            "stations_used=%s)",
            self.inspection_strictness,
            station_count,
            risk_modifiers,
            stations_used,
        )
