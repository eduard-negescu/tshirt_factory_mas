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
    # Strip markdown code fences (```json ... ``` or ``` ... ```)
    text = re.sub(r"^```(?:json)?\s*\n", "", text)
    text = re.sub(r"\n```\s*$", "", text)
    text = re.sub(r"//.*$", "", text, flags=re.MULTILINE)
    text = re.sub(r",\s*([}\]])", r"\1", text)
    return text


HEAT_PRESS_SYSTEM_PROMPT = """Ești un tehnician de presă termică care configurează o presă
de întărire pentru o comandă de personalizare tricouri.

Sarcina ta: având descrierea unui design, decide parametrii optimi pentru presa termică.

Ghid pentru parametri:
- temperature: "low" pentru finisaje vintage/uzate (temperatură mai joasă, întărire mai blândă),
  "medium" pentru imprimări standard, "high" pentru glitter, cerneală groasă sau
  designuri multi-color care necesită întărire completă
- dwell_time: "short" pentru imprimări ușoare/monocrome, "standard" pentru majoritatea designurilor,
  "extended" pentru designuri complexe multi-color sau cu efecte speciale (crackle, glitter)
- pressure: "light" pentru țesături delicate sau imprimări în relief, "medium" pentru standard,
  "firm" pentru imprimări groase multi-strat care necesită aderență puternică
- multi_pass: true pentru designuri cu finisaje speciale (glitter, crackle, cerneală pufoasă)
  sau imprimări foarte groase; false pentru întărire standard într-o singură trecere

Cazuri speciale din descrierile designurilor:
- "vintage distressed" / "crackle texture" → temperatură low + dwell extins
- "glitter heat-transfer overlay" → temperatură high + multi_pass
- "neon gradients" / "extended curing time" → dwell extins
- Design simplu monocrom → standard, trecere unică

Returnează un obiect JSON cu:
- "order_id": string-ul ID-ului comenzii
- "temperature": "low", "medium" sau "high"
- "dwell_time": "short", "standard" sau "extended"
- "pressure": "light", "medium" sau "firm"
- "multi_pass": true sau false
- "notes": un singur string care explică alegerile tale de parametri
"""

HEAT_PRESS_HUMAN_TEMPLATE = """ID comandă: {order_id}
Prioritate: {priority}
Design: {design_description}

Note rutare: {routing_notes}

Configurează presa termică pentru această comandă."""


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
