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
    # Strip markdown code fences (```json ... ``` or ``` ... ```)
    text = re.sub(r"^```(?:json)?\s*\n", "", text)
    text = re.sub(r"\n```\s*$", "", text)
    text = re.sub(r"//.*$", "", text, flags=re.MULTILINE)
    text = re.sub(r",\s*([}\]])", r"\1", text)
    return text


PRINTER_SYSTEM_PROMPT = """Ești un tehnician de imprimare care configurează o imprimantă DTG
(direct-to-garment) pentru o comandă de personalizare tricouri.

Sarcina ta: având descrierea unui design, decide parametrii optimi de imprimare.

Ghid pentru parametri:
- print_temperature: "low" pentru țesături delicate/cerneluri ușoare, "standard" pentru normal,
  "high" pentru designuri care necesită penetrare adâncă sau cerneluri groase
- ink_saturation: "light" pentru designuri subtile/minimale, "normal" pentru majoritatea,
  "heavy" pentru designuri vibrante/multi-color care necesită culori bogate
- number_of_passes: 1 pentru designuri simple, 2-3 pentru multi-color, 4-5 pentru
  designuri foarte complexe cu degradeuri și semitonuri
- color_profile: "standard" pentru imprimări obișnuite, "vibrant" pentru designuri
  neon/saturate, "accurate" pentru designuri care necesită potrivire precisă a culorilor

Ia în considerare complexitatea designului, numărul de culori și orice efecte speciale
menționate în descriere.

Returnează un obiect JSON cu:
- "order_id": string-ul ID-ului comenzii
- "print_temperature": "low", "standard" sau "high"
- "ink_saturation": "light", "normal" sau "heavy"
- "number_of_passes": număr întreg 1-5
- "color_profile": "standard", "vibrant" sau "accurate"
- "notes": un singur string care explică alegerile tale de parametri
"""

PRINTER_HUMAN_TEMPLATE = """ID comandă: {order_id}
Prioritate: {priority}
Design: {design_description}

Note rutare: {routing_notes}

Configurează imprimanta pentru această comandă."""


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
