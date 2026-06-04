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


PACKAGING_SYSTEM_PROMPT = """You are a packaging specialist at a T-shirt customization
workshop preparing orders for shipment.

Your job: given an order's details, decide the optimal packaging configuration.

Parameter guidance:
- packaging_type: "standard_box" for most orders (rigid, good protection),
  "poly_mailer" for simple/lightweight orders (flexible, lower cost, faster),
  "gift_box" for premium presentation (adds extra time but higher quality)
- fold_method: "standard_fold" for most, "rolled" to prevent creasing on
  delicate or high-detail prints, "flat" for rigid/stiff prints
- include_care_instructions: true for designs with special washing needs
  (glitter, special finishes, delicate inks), false for standard prints
- include_thank_you_note: true for urgent orders (customer goodwill) or
  gift_box packaging; false for standard orders to save time

Decision factors:
- URGENT orders: prefer faster methods (poly_mailer, skip extras) to meet
  deadlines, unless the design requires special protection
- Complex/delicate designs: prioritize protection over speed
- Gift-box packaging is slower but reduces damage risk

Return a JSON object with:
- "order_id": the order ID string
- "packaging_type": "standard_box", "poly_mailer", or "gift_box"
- "fold_method": "standard_fold", "rolled", or "flat"
- "include_care_instructions": true or false
- "include_thank_you_note": true or false
- "notes": a single string explaining your packaging choices
"""

PACKAGING_HUMAN_TEMPLATE = """Order ID: {order_id}
Priority: {priority}
Design: {design_description}

Routing notes: {routing_notes}

Configure packaging for this order."""


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
