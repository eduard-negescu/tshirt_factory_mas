import logging
import random
import time

logger = logging.getLogger(__name__)


class QualityStation:
    """Simulates the physical QC inspection station.

    The station handles the mechanical inspection time, but the actual
    pass/fail/rework verdict comes from the LLM (via QualityControlAgent).
    """

    def __init__(self, name: str = "quality_control"):
        self.name = name
        self.status: str = "available"
        self.queue: list[str] = []

    def inspect(self, order_id: str) -> float:
        """Simulate inspection time. Returns the time spent inspecting."""
        self.status = "busy"
        processing_time = random.uniform(0.5, 2.0)
        logger.info(
            "QualityControl inspecting order %s (%.1fs)",
            order_id,
            processing_time,
        )
        time.sleep(processing_time)
        self.status = "available"
        return processing_time

    def reset(self) -> None:
        self.status = "available"
        logger.info("QualityStation reset to available")
