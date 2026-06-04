import logging
import random
import time

logger = logging.getLogger(__name__)


class PackagingStation:
    def __init__(self, name: str = "packaging", failure_probability: float = 0.05):
        self.name = name
        self.status: str = "available"
        self.queue: list[str] = []
        self.failure_probability = failure_probability

    def process(self, order_id: str) -> dict:
        self.status = "busy"
        processing_time = random.randint(1, 3)
        logger.info(
            "Packaging order %s (estimated %ds)", order_id, processing_time
        )
        time.sleep(processing_time)

        if random.random() < self.failure_probability:
            self.status = "available"
            logger.error("Packaging FAILED for order %s", order_id)
            return {
                "success": False,
                "order_id": order_id,
                "error": "packaging_failure",
            }

        self.status = "available"
        logger.info("Packaging completed order %s", order_id)
        return {"success": True, "order_id": order_id}

    def reset(self) -> None:
        self.status = "available"
        logger.info("PackagingStation reset to available")
