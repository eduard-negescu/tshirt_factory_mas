import json
import logging
import re

from langchain_core.output_parsers import PydanticOutputParser
from langchain_core.prompts import ChatPromptTemplate
from langchain_core.runnables import RunnableLambda
from langchain_ollama import ChatOllama

from config.settings import Settings
from models.llm_models import PrinterDecision

logger = logging.getLogger(__name__)


class PrinterLLMError(Exception):
    """Raised when the LLM call for printer configuration fails."""


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


PRINTER_SYSTEM_PROMPT = """You are a print technician configuring a DTG (direct-to-garment)
printer for a T-shirt customization order.

Your job: given a design description, decide the optimal print parameters.

Parameter guidance:
- print_temperature: "low" for delicate fabrics/light inks, "standard" for normal,
  "high" for designs needing deep penetration or thick inks
- ink_saturation: "light" for subtle/minimal designs, "normal" for most,
  "heavy" for vibrant/multi-color designs needing rich color
- number_of_passes: 1 for simple designs, 2-3 for multi-color, 4-5 for very
  complex designs with gradients and halftones
- color_profile: "standard" for everyday prints, "vibrant" for neon/saturated
  designs, "accurate" for designs needing precise color matching

Consider the design complexity, number of colors, and any special effects
mentioned in the description.

Return a JSON object with:
- "order_id": the order ID string
- "print_temperature": "low", "standard", or "high"
- "ink_saturation": "light", "normal", or "heavy"
- "number_of_passes": integer 1-5
- "color_profile": "standard", "vibrant", or "accurate"
- "notes": a single string explaining your parameter choices
"""

PRINTER_HUMAN_TEMPLATE = """Order ID: {order_id}
Priority: {priority}
Design: {design_description}

Routing notes: {routing_notes}

Configure the printer for this order."""


class PrinterChain:
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
        self._parser = PydanticOutputParser(pydantic_object=PrinterDecision)
        self._prompt = ChatPromptTemplate.from_messages(
            [
                ("system", PRINTER_SYSTEM_PROMPT),
                ("human", PRINTER_HUMAN_TEMPLATE),
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
    ) -> PrinterDecision:
        prompt_input = {
            "order_id": order_id,
            "priority": priority,
            "design_description": design_description,
            "routing_notes": routing_notes or "(none)",
        }

        self._init_llm()
        logger.info(
            "Calling LLM for printer config of %s. Prompt:\n%s",
            order_id,
            json.dumps(prompt_input, indent=2),
        )

        try:
            response: PrinterDecision = self._chain.invoke(prompt_input)
        except Exception as e:
            logger.error("Printer LLM call failed for %s: %s", order_id, e)
            raise PrinterLLMError(
                f"Failed to get printer config from LLM: {e}"
            ) from e

        logger.info(
            "LLM printer config for %s: temp=%s sat=%s passes=%d profile=%s | %s",
            order_id,
            response.print_temperature,
            response.ink_saturation,
            response.number_of_passes,
            response.color_profile,
            response.notes,
        )
        return response
