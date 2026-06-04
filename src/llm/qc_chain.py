import json
import logging
import re

from langchain_core.output_parsers import PydanticOutputParser
from langchain_core.prompts import ChatPromptTemplate
from langchain_core.runnables import RunnableLambda
from langchain_ollama import ChatOllama

from config.settings import Settings
from models.llm_models import QualityDecision

logger = logging.getLogger(__name__)


class QCLLMError(Exception):
    """Raised when the LLM call for quality inspection fails."""


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


QC_SYSTEM_PROMPT = """Ești un inspector de control al calității la un atelier de personalizare tricouri.

Inspectezi tricourile finisate și decizi dacă trec, sunt respinse sau necesită refacere.

Criterii de inspecție:
- PASS: Tricoul îndeplinește standardele de calitate. Majoritatea tricourilor ar trebui să treacă,
  cu excepția cazurilor cu defecte clare și vizibile. Fii pragmatic — imperfecțiunile minore
  pe care un client nu le-ar observa sunt acceptabile. Acesta ar trebui să fie cel mai frecvent verdict.
- FAIL: Defecte critice care nu pot fi reparate — tricou ruinat, țesătură ruptă,
  pată de cerneală care acoperă designul, imprimare complet dezaliniată (abatere >1cm).
  Trebuie aruncat și comanda reimprimată de la zero. Acesta ar trebui să fie rar.
- REWORK: Defecte moderate dar reparabile — ușoară dezalinire (~2-3mm),
  imprimare ștearsă într-o zonă, mică pată pe o zonă necritică, întărire insuficientă.
  Tricoul poate fi retrimis prin stațiile specifice.

Când decizi, ia în considerare:
- Complexitatea designului (designurile complexe au mai multe lucruri care pot merge prost,
  dar au și toleranță mai mare pentru mici dezaliniri)
- Numărul de culori (imprimările multi-color sunt mai predispuse la dezalinire)
- Dacă comanda este URGENT (fii puțin mai indulgent)
- Istoricul procesării (prin ce stații a trecut tricoul)

IMPORTANT: Fii realist. Într-un atelier real, majoritatea tricourilor trec inspecția.
Semnalizează doar defectele care ar afecta în mod real satisfacția clientului.
Țintește spre ~70% rată de trecere, ~20% refacere, ~10% respingere.

Returnează un obiect JSON cu:
- "verdict": "pass", "fail" sau "rework"
- "reason": explicație detaliată a deciziei de calitate (un singur string)
- "rework_instructions": dacă e rework, instrucțiuni specifice (un singur string)
- "defect_severity": "none", "minor", "major" sau "critical"
"""

QC_HUMAN_TEMPLATE = """ID comandă: {order_id}
Prioritate: {priority}
Design: {design_description}

Istoric procesare:
{processing_history}

{strictness_note}
Inspectează această comandă și emite un verdict de calitate."""


class QCChain:
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
        self._parser = PydanticOutputParser(pydantic_object=QualityDecision)
        self._prompt = ChatPromptTemplate.from_messages(
            [
                ("system", QC_SYSTEM_PROMPT),
                ("human", QC_HUMAN_TEMPLATE),
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
        processing_history: str | None = None,
        inspection_strictness: str = "normal",
    ) -> QualityDecision:
        strictness_notes = {
            "high": (
                "NOTĂ: Fii deosebit de riguros. Această comandă a trecut prin "
                "puține stații, deci riscul de defecte ascunse este mai mare. "
                "Verifică cu atenție sporită."
            ),
            "elevated": (
                "NOTĂ: Fii mai atent decât de obicei. Procesarea a fost "
                "limitată — inspectează cu grijă zonele de risc."
            ),
            "normal": "",
        }
        strictness_note = strictness_notes.get(inspection_strictness, "")

        prompt_input = {
            "order_id": order_id,
            "priority": priority,
            "design_description": design_description,
            "processing_history": processing_history or "Standard printer + heat_press processing",
            "strictness_note": strictness_note,
        }

        self._init_llm()
        logger.info(
            "Calling LLM for QC inspection of %s. Prompt:\n%s",
            order_id,
            json.dumps(prompt_input, indent=2),
        )

        try:
            response: QualityDecision = self._chain.invoke(prompt_input)
        except Exception as e:
            logger.error("QC LLM call failed for %s: %s", order_id, e)
            raise QCLLMError(
                f"Failed to get QC decision from LLM: {e}"
            ) from e

        logger.info(
            "LLM QC for %s: verdict=%s severity=%s | Reason: %s",
            order_id,
            response.verdict,
            response.defect_severity,
            response.reason,
        )
        return response
