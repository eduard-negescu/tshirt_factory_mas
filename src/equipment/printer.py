import logging
import random
import time

logger = logging.getLogger(__name__)


class Printer:
    def __init__(self, name: str = "printer", failure_probability: float = 0.1):
        self.name = name
        self.status: str = "available"
        self.queue: list[str] = []
        self.failure_probability = failure_probability

    def process(self, order_id: str) -> dict:
        self.status = "busy"
        processing_time = random.randint(2, 5)
        logger.info(
            "Printer starting order %s (estimated %ds)", order_id, processing_time
        )
        time.sleep(processing_time)

        if random.random() < self.failure_probability:
            self.status = "failed"
            logger.error("Printer FAILED while processing order %s", order_id)
            return {"success": False, "order_id": order_id, "error": "printer_failure"}

        self.status = "available"
        logger.info("Printer completed order %s", order_id)
        return {"success": True, "order_id": order_id}

    def reset(self) -> None:
        self.status = "available"
        logger.info("Printer reset to available")
