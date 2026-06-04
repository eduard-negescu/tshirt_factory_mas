import logging
import random
import time

logger = logging.getLogger(__name__)


class HeatPress:
    def __init__(self, name: str = "heat_press", failure_probability: float = 0.1):
        self.name = name
        self.status: str = "available"
        self.queue: list[str] = []
        self.failure_probability = failure_probability

    def process(self, order_id: str) -> dict:
        self.status = "busy"
        processing_time = random.randint(2, 5)
        logger.info(
            "HeatPress starting order %s (estimated %ds)", order_id, processing_time
        )
        time.sleep(processing_time)

        if random.random() < self.failure_probability:
            self.status = "failed"
            logger.error("HeatPress FAILED while processing order %s", order_id)
            return {
                "success": False,
                "order_id": order_id,
                "error": "heat_press_failure",
            }

        self.status = "available"
        logger.info("HeatPress completed order %s", order_id)
        return {"success": True, "order_id": order_id}

    def reset(self) -> None:
        self.status = "available"
        logger.info("HeatPress reset to available")
