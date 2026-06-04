---
name: llm-loop-prevention
description: Prevent infinite loops when an LLM's decisions create re-entrant states — e.g. QC keeps returning 'rework', causing the same order to cycle forever. Pattern: add a counter, force-terminate after threshold, and tune the prompt to reduce loop likelihood.
source: auto-skill
extracted_at: '2026-06-04T10:35:25.575Z'
---

# LLM Decision Loop Prevention

Use this when an LLM makes a classification/verdict decision that can send work back to an earlier stage, creating a potential infinite loop (e.g., QC → rework → QC → rework → ...).

## The problem

An LLM acting as a quality inspector examines work and returns: pass, fail, or rework. Rework sends the item back through the pipeline for another attempt. If the LLM is strict (correctly flagging minor defects on complex work), it can keep returning "rework" indefinitely — the LLM has no memory of previous attempts and sees the same processing history each time.

## Step 1 — Add a rework counter to the data model

```python
class Order(BaseModel):
    # ... existing fields ...
    rework_count: int = 0
```

This persists across rework cycles. Increment it each time the LLM returns "rework."

## Step 2 — Force-terminate after threshold

In the main loop, after the LLM returns "rework":

```python
order.rework_count += 1
max_rework = 2
if order.rework_count >= max_rework:
    # Force-complete: assume acceptable quality after N attempts
    order.status = "completed"
    completed_count += 1
else:
    # Re-queue for another attempt
    order.status = "pending"
    scheduler.pending_orders[order_id] = order
```

Choose the threshold based on real-world tolerance. For a demo/simulation, 2 works well. For production, consider the cost of rework vs. the cost of shipping a substandard item.

## Step 3 — Tune the LLM prompt to reduce loop likelihood

If the LLM is reworking too often, the prompt needs adjustment:

- **Set explicit rate expectations**: "Aim for ~70% pass rate, ~20% rework, ~10% fail." LLMs respond to distribution hints.
- **Define what constitutes a pass vs. rework more precisely**: Give concrete thresholds ("misalignment under 2mm = pass, 2-5mm = rework, >5mm = fail").
- **Remind the LLM to be pragmatic**: "Most shirts pass inspection. Only flag defects a customer would notice."
- **Provide context about complexity tolerance**: "Complex designs have higher tolerance for minor misalignment."

## Step 4 — Handle edge cases in the LLM error path

If the LLM call fails entirely (API error, bad JSON), default to "pass" — don't let a transient failure cause rework:

```python
try:
    decision = qc_chain.invoke(...)
except Exception:
    decision = QualityDecision(verdict="pass", reason="LLM error, defaulting to pass")
```

The fallback should be the least disruptive outcome.

## Related: Pydantic null coercion

LLMs commonly return `null` for optional string fields, which Pydantic rejects. Use a field validator:

```python
@field_validator("rework_instructions", mode="before")
@classmethod
def coerce_null_to_empty(cls, v):
    if v is None:
        return ""
    return v
```
