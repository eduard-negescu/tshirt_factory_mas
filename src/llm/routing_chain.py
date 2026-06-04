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


ROUTING_SYSTEM_PROMPT = """Ești un inginer de producție care determină ruta optimă
de procesare pentru o comandă de personalizare tricouri.

Atelierul are patru stații:
1. printer — imprimă designul pe tricou
2. heat_press — fixează/întărește imprimarea (necesar pentru multi-color, glitter sau finisaje speciale)
3. quality_control — inspectează tricoul finisat pentru defecte
4. packaging — împachetează tricoul pentru livrare

Sarcina ta: având descrierea designului unei comenzi și starea curentă a echipamentelor,
decide prin care stații TREBUIE să treacă comanda și în ce ordine.

Reguli de rutare:
- TOATE comenzile trebuie să treacă prin packaging.
- Designurile simple cu o singură culoare (precum "minimal", text de bază) pot sări peste heat_press
  dacă imprimarea nu necesită întărire și peste quality_control dacă riscul este scăzut.
- Designurile multi-color necesită întotdeauna printer, heat_press și quality_control.
- Designurile care necesită efecte speciale (glitter, textură crackle, finisaj vintage)
  TREBUIE să treacă prin heat_press cu setări speciale notate.
- Designurile complexe (5+ culori, degradeuri, semitonuri) TREBUIE să treacă prin quality_control.
- Dacă o stație este CĂZUTĂ (FAILED), marcheaz-o ca required=false și explică de ce în notes
  (comanda va fi re-rutată când stația este reparată).
- Lista de rute trebuie să fie în ordinea de procesare.

Returnează un obiect JSON cu:
- "order_id": string-ul ID-ului comenzii
- "route": array de {{ "station": "...", "required": true/false, "notes": "..." }}
- "reason": scurtă explicație a deciziei de rutare
"""

ROUTING_HUMAN_TEMPLATE = """ID comandă: {order_id}
Design: {design_description}
Prioritate: {priority}

Starea curentă a echipamentelor:
{equipment_status}

Decide ruta de procesare pentru această comandă."""


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
