import json
import logging
import re

from langchain_core.output_parsers import PydanticOutputParser
from langchain_core.prompts import ChatPromptTemplate
from langchain_core.runnables import RunnableLambda
from langchain_ollama import ChatOllama

from config.settings import Settings
from models.llm_models import HeatPressDecision

logger = logging.getLogger(__name__)


class HeatPressLLMError(Exception):
    """Raised when the LLM call for heat press configuration fails."""


def _log_raw_response(text) -> str:
    content = text.content if hasattr(text, "content") else text
    logger.debug("Raw LLM output:\n%s", content)
    return text


def _strip_json_comments(text: str) -> str:
    if hasattr(text, "content"):
        text = text.content
    text = re.sub(r"//.*$", "", text, flags=re.MULTILINE)
    text = re.sub(r",\s*([}\]])", r"\1", text)
    return text


HEAT_PRESS_SYSTEM_PROMPT = """You are a heat press technician configuring a curing press
for a T-shirt customization order.

Your job: given a design description, decide the optimal heat press parameters.

Parameter guidance:
- temperature: "low" for vintage/distressed finishes (lower temp, gentler cure),
  "medium" for standard prints, "high" for glitter, thick ink, or multi-color
  designs needing thorough curing
- dwell_time: "short" for light/single-color prints, "standard" for most designs,
  "extended" for complex multi-color or special-effect designs (crackle, glitter)
- pressure: "light" for delicate fabrics or raised prints, "medium" for standard,
  "firm" for thick multi-layer prints needing strong adhesion
- multi_pass: true for designs with special finishes (glitter, crackle, puff ink)
  or very thick prints; false for standard single-pass curing

Special cases from the design descriptions:
- "vintage distressed" / "crackle texture" → low temperature + extended dwell
- "glitter heat-transfer overlay" → high temperature + multi_pass
- "neon gradients" / "extended curing time" → extended dwell
- Simple single-color → standard, single pass

Return a JSON object with:
- "order_id": the order ID string
- "temperature": "low", "medium", or "high"
- "dwell_time": "short", "standard", or "extended"
- "pressure": "light", "medium", or "firm"
- "multi_pass": true or false
- "notes": a single string explaining your parameter choices
"""

HEAT_PRESS_HUMAN_TEMPLATE = """Order ID: {order_id}
Priority: {priority}
Design: {design_description}

Routing notes: {routing_notes}

Configure the heat press for this order."""


class HeatPressChain:
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
        self._parser = PydanticOutputParser(pydantic_object=HeatPressDecision)
        self._prompt = ChatPromptTemplate.from_messages(
            [
                ("system", HEAT_PRESS_SYSTEM_PROMPT),
                ("human", HEAT_PRESS_HUMAN_TEMPLATE),
            ]
        )
        self._chain = (
            self._prompt
            | self._llm
            | RunnableLambda(_log_raw_response)
            | RunnableLambda(_strip_json_comments)
            | self._parser
        )

    def invoke(
        self,
        order_id: str,
        design_description: str,
        priority: str = "normal",
        routing_notes: str = "",
    ) -> HeatPressDecision:
        prompt_input = {
            "order_id": order_id,
            "priority": priority,
            "design_description": design_description,
            "routing_notes": routing_notes or "(none)",
        }

        self._init_llm()
        logger.info(
            "Calling LLM for heat press config of %s. Prompt:\n%s",
            order_id,
            json.dumps(prompt_input, indent=2),
        )

        try:
            response: HeatPressDecision = self._chain.invoke(prompt_input)
        except Exception as e:
            logger.error("HeatPress LLM call failed for %s: %s", order_id, e)
            raise HeatPressLLMError(
                f"Failed to get heat press config from LLM: {e}"
            ) from e

        logger.info(
            "LLM heat press config for %s: temp=%s dwell=%s pressure=%s "
            "multi_pass=%s | %s",
            order_id,
            response.temperature,
            response.dwell_time,
            response.pressure,
            response.multi_pass,
            response.notes,
        )
        return response
