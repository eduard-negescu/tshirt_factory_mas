import json
import logging
import re

from langchain_core.output_parsers import PydanticOutputParser
from langchain_core.prompts import ChatPromptTemplate
from langchain_core.runnables import RunnableLambda
from langchain_ollama import ChatOllama

from config.settings import Settings
from models.llm_models import PackagingDecision

logger = logging.getLogger(__name__)


class PackagingLLMError(Exception):
    """Raised when the LLM call for packaging configuration fails."""


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


PACKAGING_SYSTEM_PROMPT = """Ești un specialist în ambalare la un atelier de personalizare
tricouri care pregătește comenzile pentru expediere.

Sarcina ta: având detaliile unei comenzi, decide configurația optimă de ambalare.

Ghid pentru parametri:
- packaging_type: "standard_box" pentru majoritatea comenzilor (rigid, protecție bună),
  "poly_mailer" pentru comenzi simple/ușoare (flexibil, cost mai mic, mai rapid),
  "gift_box" pentru prezentare premium (adaugă timp suplimentar dar calitate mai ridicată)
- fold_method: "standard_fold" pentru majoritatea, "rolled" pentru a preveni cutarea
  pe imprimări delicate sau cu detalii fine, "flat" pentru imprimări rigide/dure
- include_care_instructions: true pentru designuri cu nevoi speciale de spălare
  (glitter, finisaje speciale, cerneluri delicate), false pentru imprimări standard
- include_thank_you_note: true pentru comenzi urgente (bunăvoință client) sau
  ambalare gift_box; false pentru comenzi standard pentru a economisi timp

Factori de decizie:
- Comenzi URGENT: preferă metode mai rapide (poly_mailer, sări peste extra) pentru
  a respecta termenele, cu excepția cazului în care designul necesită protecție specială
- Designuri complexe/delicate: prioritizează protecția în fața vitezei
- Ambalarea gift-box este mai lentă dar reduce riscul de deteriorare

Returnează un obiect JSON cu:
- "order_id": string-ul ID-ului comenzii
- "packaging_type": "standard_box", "poly_mailer" sau "gift_box"
- "fold_method": "standard_fold", "rolled" sau "flat"
- "include_care_instructions": true sau false
- "include_thank_you_note": true sau false
- "notes": un singur string care explică alegerile tale de ambalare
"""

PACKAGING_HUMAN_TEMPLATE = """ID comandă: {order_id}
Prioritate: {priority}
Design: {design_description}

Note rutare: {routing_notes}

Configurează ambalarea pentru această comandă."""


class PackagingChain:
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
        self._parser = PydanticOutputParser(pydantic_object=PackagingDecision)
        self._prompt = ChatPromptTemplate.from_messages(
            [
                ("system", PACKAGING_SYSTEM_PROMPT),
                ("human", PACKAGING_HUMAN_TEMPLATE),
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
    ) -> PackagingDecision:
        prompt_input = {
            "order_id": order_id,
            "priority": priority,
            "design_description": design_description,
            "routing_notes": routing_notes or "(none)",
        }

        self._init_llm()
        logger.info(
            "Calling LLM for packaging config of %s. Prompt:\n%s",
            order_id,
            json.dumps(prompt_input, indent=2),
        )

        try:
            response: PackagingDecision = self._chain.invoke(prompt_input)
        except Exception as e:
            logger.error("Packaging LLM call failed for %s: %s", order_id, e)
            raise PackagingLLMError(
                f"Failed to get packaging config from LLM: {e}"
            ) from e

        logger.info(
            "LLM packaging config for %s: type=%s fold=%s care=%s thank=%s | %s",
            order_id,
            response.packaging_type,
            response.fold_method,
            response.include_care_instructions,
            response.include_thank_you_note,
            response.notes,
        )
        return response
