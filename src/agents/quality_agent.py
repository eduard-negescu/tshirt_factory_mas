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
        if msg.message_type == "process_order":
            order_id = msg.payload.get("order_id", "")
            self.process(order_id)
