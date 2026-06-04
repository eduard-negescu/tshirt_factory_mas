import json
import logging
import re

from langchain_core.output_parsers import PydanticOutputParser
from langchain_core.prompts import ChatPromptTemplate
from langchain_core.runnables import RunnableLambda
from langchain_ollama import ChatOllama

from config.settings import Settings
from models.llm_models import EquipmentStatusInfo, PendingOrderInfo, ScheduleResponse

logger = logging.getLogger(__name__)


class SchedulerLLMError(Exception):
    """Raised when the LLM call for scheduling fails."""


def _log_raw_response(text) -> str:
    """Log the raw LLM output before any parsing, so it survives parse failures."""
    content = text.content if hasattr(text, "content") else text
    logger.debug("Raw LLM output:\n%s", content)
    return text


def _strip_json_comments(text: str) -> str:
    """Remove // comments and trailing commas so the JSON is parseable."""
    if hasattr(text, "content"):
        text = text.content
    # Remove // line comments
    text = re.sub(r"//.*$", "", text, flags=re.MULTILINE)
    # Remove trailing commas before ] or }
    text = re.sub(r",\s*([}\]])", r"\1", text)
    return text


SYSTEM_PROMPT = """You are a production scheduler for a T-shirt customization workshop.

The workshop has four stations in sequence:
1. Printer -> 2. HeatPress -> 3. QualityControl -> 4. Packaging

Your job is to create an optimal processing schedule.

Rules:
- URGENT orders must be processed before NORMAL orders.
- Within the same priority level, minimize total waiting time.
- If any equipment has FAILED, note it and schedule around it
  (failed equipment cannot process orders until repaired).
- Orders that were rejected by QualityControl must be re-inserted
  into the schedule for re-processing.

Return a JSON object with:
- "schedule": a flat list of order ID strings (e.g. ["O-001", "O-003", "O-002"])
- "reason": a single string with a brief explanation of your scheduling decisions
"""

HUMAN_TEMPLATE = """Current equipment status:
{equipment_status}

Pending orders:
{pending_orders}

{failure_note}

Please produce the processing schedule."""


def _build_equipment_status_text(statuses: list[EquipmentStatusInfo]) -> str:
    lines = []
    for eq in statuses:
        lines.append(f"  - {eq.name}: {eq.status}")
    return "\n".join(lines)


def _build_pending_orders_text(orders: list[PendingOrderInfo]) -> str:
    lines = []
    for o in orders:
        lines.append(f"  - {o.id} (priority: {o.priority})")
    return "\n".join(lines)


class SchedulerChain:
    def __init__(self, settings: Settings):
        self.settings = settings
        self._llm = None
        self._chain = None

    def _init_llm(self):
        if self._llm is not None:
            return
        self._llm = ChatOllama(
            model=self.settings.model_name,
            base_url=self.settings.ollama_base_url,
            temperature=0,
            format="json",
            client_kwargs={
                "headers": {
                    "Authorization": f"Bearer {self.settings.ollama_api_key}"
                }
            },
        )
        self._parser = PydanticOutputParser(pydantic_object=ScheduleResponse)
        self._prompt = ChatPromptTemplate.from_messages(
            [
                ("system", SYSTEM_PROMPT),
                ("human", HUMAN_TEMPLATE),
            ]
        )
        self._chain = self._prompt | self._llm | RunnableLambda(_log_raw_response) | RunnableLambda(_strip_json_comments) | self._parser

    def invoke(
        self,
        equipment_status: list[EquipmentStatusInfo],
        pending_orders: list[PendingOrderInfo],
        failed_equipment: str | None = None,
    ) -> ScheduleResponse:
        failure_note = ""
        if failed_equipment:
            failure_note = (
                f"IMPORTANT: {failed_equipment} has FAILED and cannot process orders."
            )

        prompt_input = {
            "equipment_status": _build_equipment_status_text(equipment_status),
            "pending_orders": _build_pending_orders_text(pending_orders),
            "failure_note": failure_note,
        }

        self._init_llm()
        logger.info(
            "Calling LLM for scheduling. Prompt:\n%s",
            json.dumps(prompt_input, indent=2),
        )

        try:
            response: ScheduleResponse = self._chain.invoke(prompt_input)
        except Exception as e:
            logger.error("LLM call failed: %s", e)
            raise SchedulerLLMError(
                f"Failed to get scheduling decision from LLM: {e}"
            ) from e

        logger.info(
            "LLM response: schedule=%s reason=%s",
            response.schedule,
            response.reason,
        )
        return response
