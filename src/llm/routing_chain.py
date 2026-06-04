import json
import logging
import re

from langchain_core.output_parsers import PydanticOutputParser
from langchain_core.prompts import ChatPromptTemplate
from langchain_core.runnables import RunnableLambda
from langchain_ollama import ChatOllama

from config.settings import Settings
from models.llm_models import EquipmentStatusInfo, RoutingDecision

logger = logging.getLogger(__name__)


class RoutingLLMError(Exception):
    """Raised when the LLM call for pipeline routing fails."""


def _log_raw_response(text) -> str:
    """Log the raw LLM output before any parsing, so it survives parse failures."""
    content = text.content if hasattr(text, "content") else text
    logger.debug("Raw LLM output:\n%s", content)
    return text


def _strip_json_comments(text: str) -> str:
    """Remove // comments and trailing commas so the JSON is parseable."""
    if hasattr(text, "content"):
        text = text.content
    text = re.sub(r"//.*$", "", text, flags=re.MULTILINE)
    text = re.sub(r",\s*([}\]])", r"\1", text)
    return text


ROUTING_SYSTEM_PROMPT = """You are a production engineer determining the optimal pipeline
route for a T-shirt customization order.

The workshop has four stations:
1. printer — prints the design onto the shirt
2. heat_press — cures/fixes the print (required for multi-color, glitter, or special finishes)
3. quality_control — inspects the finished shirt for defects
4. packaging — wraps the shirt for shipping

Your job: given an order's design description and current equipment status,
decide which stations the order MUST go through and in what order.

Routing rules:
- ALL orders must go through packaging.
- Simple single-color designs (like "minimal", basic text) can skip heat_press
  if the print doesn't need curing and skip quality_control if risk is low.
- Multi-color designs always need printer, heat_press, and quality_control.
- Designs needing special effects (glitter, crackle texture, vintage finish)
  MUST go through heat_press with special settings noted.
- Complex designs (5+ colors, gradients, halftones) MUST go through quality_control.
- If a station is FAILED, mark it as required=false and explain why in notes
  (the order will be re-routed when the station is repaired).
- The route list must be in processing order.

Return a JSON object with:
- "order_id": the order ID string
- "route": array of {{ "station": "...", "required": true/false, "notes": "..." }}
- "reason": brief explanation of the routing decision
"""

ROUTING_HUMAN_TEMPLATE = """Order ID: {order_id}
Design: {design_description}
Priority: {priority}

Current equipment status:
{equipment_status}

Decide the pipeline route for this order."""


class RoutingChain:
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
        self._parser = PydanticOutputParser(pydantic_object=RoutingDecision)
        self._prompt = ChatPromptTemplate.from_messages(
            [
                ("system", ROUTING_SYSTEM_PROMPT),
                ("human", ROUTING_HUMAN_TEMPLATE),
            ]
        )
        self._chain = (
            self._prompt | self._llm | RunnableLambda(_log_raw_response) | RunnableLambda(_strip_json_comments) | self._parser
        )

    def invoke(
        self,
        order_id: str,
        design_description: str,
        priority: str,
        equipment_statuses: list[EquipmentStatusInfo],
    ) -> RoutingDecision:
        status_lines = []
        for eq in equipment_statuses:
            status_lines.append(f"  - {eq.name}: {eq.status}")

        prompt_input = {
            "order_id": order_id,
            "design_description": design_description,
            "priority": priority,
            "equipment_status": "\n".join(status_lines),
        }

        self._init_llm()
        logger.info(
            "Calling LLM for routing of %s. Prompt:\n%s",
            order_id,
            json.dumps(prompt_input, indent=2),
        )

        try:
            response: RoutingDecision = self._chain.invoke(prompt_input)
        except Exception as e:
            logger.error("Routing LLM call failed for %s: %s", order_id, e)
            raise RoutingLLMError(
                f"Failed to get routing decision from LLM: {e}"
            ) from e

        logger.info(
            "LLM routing for %s: %s | Reason: %s",
            order_id,
            [(r.station, r.required, r.notes) for r in response.route],
            response.reason,
        )
        return response
