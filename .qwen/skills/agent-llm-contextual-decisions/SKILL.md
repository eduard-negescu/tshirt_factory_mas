---
name: agent-llm-contextual-decisions
description: Upgrade deterministic agents (random dice, fixed heuristics) to make context-aware LLM decisions that have real consequences on simulation outcomes — following the pattern established across PrinterAgent, HeatPressAgent, and PackagingAgent.
source: auto-skill
extracted_at: '2026-06-04T14:04:47.935Z'
---

# Adding LLM-Driven Contextual Decisions to Agents

Use this when an agent currently makes a trivial deterministic decision (random dice, hardcoded thresholds, no reasoning) and you need to upgrade it to a non-trivial LLM-driven decision that uses unstructured context (design descriptions, processing history, routing notes).

## The pattern (established on PrinterAgent, HeatPressAgent, PackagingAgent)

Each agent follows this structure:

```
Agent.process(order_id, design_description, priority, routing_notes)
  → LLM chain decides operational parameters (structured output)
  → Heuristic adjusts equipment failure probability based on decision
  → Equipment runs with adjusted probability
  → Restore base probability
  → Return result with LLM decision embedded
```

## Step 1 — Design the LLM decision model

Create a Pydantic model that captures the agent's operational choices. Each field should be a genuine decision the LLM must reason about, not a decorative label.

```python
class PrinterDecision(BaseModel):
    order_id: str
    print_temperature: Literal["low", "standard", "high"]
    ink_saturation: Literal["light", "normal", "heavy"]
    number_of_passes: int = Field(ge=1, le=5)
    color_profile: Literal["standard", "vibrant", "accurate"]
    notes: str  # LLM's reasoning — logged, not decorative
```

**Why structured output:** The LLM must produce specific parameter choices, not free-text advice. This forces it to reason about the design and commit to concrete settings.

**Include a `notes` field:** Use a `@field_validator` to coerce `null → ""` — LLMs commonly return null for optional strings.

## Step 2 — Build the LLM chain

Follow the project's established chain pattern (see `llm/scheduler_chain.py`, `llm/qc_chain.py` for reference):

```python
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
            temperature=0,           # deterministic output
            format="json",           # force JSON for structured parsing
        )
        self._parser = PydanticOutputParser(pydantic_object=PrinterDecision)
        self._prompt = ChatPromptTemplate.from_messages([
            ("system", SYSTEM_PROMPT),
            ("human", HUMAN_TEMPLATE),
        ])
        self._chain = (
            self._prompt
            | self._llm
            | RunnableLambda(_log_raw_response)  # always log raw output
            | RunnableLambda(_strip_json_comments)
            | self._parser
        )
```

**Chain pattern rules:**
- Lazy init in `_init_llm()` — don't connect to Ollama at import time
- `temperature=0` for consistent, reproducible decisions
- `format="json"` to reduce parser failures
- Always insert `_log_raw_response` before `_strip_json_comments` before the parser (see `langchain-log-raw-llm-output` skill)
- Custom error class per chain (e.g., `PrinterLLMError`)

## Step 3 — Write a context-rich system prompt

The prompt must teach the LLM how to choose parameters based on design characteristics:

```
You are a print technician configuring a DTG printer.

Parameter guidance:
- print_temperature: "low" for delicate fabrics, "standard" for normal,
  "high" for designs needing deep penetration
- number_of_passes: 1 for simple, 2-3 for multi-color, 4-5 for complex
  designs with gradients

Consider the design complexity, number of colors, and special effects.
```

**Key prompt ingredients:**
- Role assignment ("You are a print technician...")
- Concrete guidance per parameter (what each value means, when to choose it)
- Connection to design characteristics (colors, complexity, special effects)
- Reference to specific design patterns from the catalogue (e.g., "vintage crackle → low temp + extended dwell")

## Step 4 — Pass context to the agent's `process()`

Extend the agent's `process()` signature to accept what the LLM needs:

```python
def process(
    self,
    order_id: str,
    design_description: str = "",    # the unstructured context
    priority: str = "normal",        # influences speed-vs-quality trade-offs
    routing_notes: str = "",         # per-station hints from routing LLM
) -> dict:
```

**Why these three:** The design description is the primary context. Priority affects trade-offs (urgent → faster settings). Routing notes carry cross-agent hints (e.g., "use lower temperature for crackle texture").

The pipeline (`graph/pipeline.py`) extracts routing notes from `StationRoute.notes` and passes them:

```python
routing_notes = {r.station: r.notes for r in routing.route}
result = agent.process(order_id, design_description, priority,
                       routing_notes=routing_notes.get("printer", ""))
```

## Step 5 — Make the LLM decision consequential

The LLM decision must affect the simulation outcome, not just get logged. The pattern: **adjust equipment failure probability based on parameter choices**.

```python
def _adjust_failure_probability(self, decision: PrinterDecision) -> float:
    base = self._base_failure_probability  # saved at __init__
    risk = 1.0

    # Good choices → lower risk
    if decision.number_of_passes >= 2:
        risk -= 0.15          # more passes = more careful

    # Risky choices → higher risk
    if decision.ink_saturation == "heavy":
        risk += 0.12          # heavy ink = more can go wrong

    return max(0.02, min(0.25, base * risk))  # clamp to sane range
```

**Heuristic design rules:**
- Each adjustment should be small (0.05–0.20) so LLM choices matter but don't dominate
- Clamp the result (e.g., 2%–25%) so no single decision is catastrophic
- Heuristics encode domain knowledge: more passes = more careful, extended dwell = scorching risk, gift box = better protection
- Save and restore the base failure probability around each `equipment.process()` call

## Step 6 — Handle LLM failures gracefully

Never let an LLM failure block the pipeline:

```python
try:
    decision = self.printer_chain.invoke(...)
except PrinterLLMError as e:
    logger.warning("Printer LLM failed, using defaults: %s", e)
    decision = PrinterDecision(
        order_id=order_id,
        print_temperature="standard",
        ink_saturation="normal",
        number_of_passes=2,
        color_profile="standard",
        notes=f"LLM error, defaulting: {e}",
    )
```

Also handle the case where no chain is configured (`self.printer_chain is None`) — produce the same safe defaults. This lets the simulation run without Ollama.

## Step 7 — Wire into main.py

```python
# Initialize chain
printer_chain = PrinterChain(settings)

# Pass to agent
printer_agent = PrinterAgent(printer_eq, printer_chain=printer_chain)

# Add to configurable chains dict (for graph access)
config = {
    "configurable": {
        "chains": {
            "printer": printer_chain,
            ...
        }
    }
}
```

## Verification

Run with 3 diverse designs (complex, simple, special-effect) and verify:
1. Each agent logs an LLM decision with context-appropriate parameters
2. The `notes` field explains *why* those parameters were chosen
3. Equipment failure probability adjustments are visible in debug logs
4. LLM unavailability is handled gracefully (falls back to defaults)
5. The simulation completes without errors

## Anti-patterns

- **Decorative LLM calls**: Don't call the LLM just to produce a string that gets logged and ignored. The decision must affect behavior.
- **Over-fitted heuristics**: Don't encode the LLM's expected output into the heuristic. The heuristic should evaluate the *consequences* of parameter choices, not reward "correct" answers.
- **Passing nothing**: Don't call `agent.process(order_id)` without design_description and routing_notes — the LLM has nothing to reason about.
