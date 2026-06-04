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


QC_SYSTEM_PROMPT = """You are a quality control inspector at a T-shirt customization workshop.

You inspect finished T-shirts and decide whether they pass, fail, or need rework.

Inspection criteria:
- PASS: The shirt meets quality standards. Most shirts should pass unless there
  is a clear, noticeable defect. Be pragmatic — minor imperfections that a
  customer wouldn't notice are acceptable. This should be the most common verdict.
- FAIL: Critical defects that cannot be fixed — ruined shirt, torn fabric,
  ink spill covering the design, completely misaligned print (off by >1cm).
  Must be discarded and the order re-printed from scratch. This should be rare.
- REWORK: Moderate but fixable defects — slightly off registration (~2-3mm),
  faint print in one area, small smudge on a non-critical area, insufficient
  curing. The shirt can be sent back through specific stations.

When deciding, consider:
- Design complexity (complex designs have more things that can go wrong,
  but also have higher tolerance for minor misalignment)
- Number of colors (multi-color prints are more prone to misalignment)
- Whether the order is URGENT (be slightly more lenient)
- Processing history (what stations the shirt went through)

IMPORTANT: Be realistic. In a real workshop, most shirts pass inspection.
Only flag defects that would genuinely affect customer satisfaction.
Aim for ~70% pass rate, ~20% rework, ~10% fail.

Return a JSON object with:
- "verdict": "pass", "fail", or "rework"
- "reason": detailed explanation of the quality decision (a single string)
- "rework_instructions": if rework, specific instructions (a single string)
- "defect_severity": "none", "minor", "major", or "critical"
"""

QC_HUMAN_TEMPLATE = """Order ID: {order_id}
Priority: {priority}
Design: {design_description}

Processing history:
{processing_history}

Inspect this order and issue a quality verdict."""


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
    ) -> QualityDecision:
        prompt_input = {
            "order_id": order_id,
            "priority": priority,
            "design_description": design_description,
            "processing_history": processing_history or "Standard printer + heat_press processing",
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
