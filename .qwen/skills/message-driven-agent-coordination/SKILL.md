---
name: message-driven-agent-coordination
description: Make agent behavior genuinely driven by inter-agent messages — not just logging — by returning actionable responses from handle_message, sending enriched context in message payloads, and having the message bus support request-response dispatch.
source: auto-skill
extracted_at: '2026-06-04T19:46:07.451Z'
---

# Message-Driven Agent Coordination

Use this when agents need to actually react to inter-agent messages with behavior changes, not just log them. The default pattern — fire-and-forget pub/sub with no-return handlers — is fine for observability but doesn't satisfy "agents coordinate through communication." This skill upgrades it to bidirectional, context-rich, behavior-altering message exchange.

## The problem

A message bus where `handle_message()` only does `logger.debug(...)` is cosmetic. The bus exists, messages flow, but no agent changes behavior based on received messages. Real coordination happens elsewhere (graph nodes, pipeline functions), bypassing the bus. This fails the spirit of "inter-agent communication" requirements — messages must *drive* decisions.

## Step 1 — Make dispatch return handler responses

Change `bus.dispatch()` from `-> None` to `-> dict[str, list]`:

```python
def dispatch(self) -> dict[str, list]:
    responses: dict[str, list] = {}
    for msg in self.messages:
        if msg.receiver in self.handlers:
            result = self.handlers[msg.receiver](msg)
            if result is not None:
                responses.setdefault(msg.receiver, []).append(result)
    self.messages.clear()
    return responses
```

**Why:** Callers can now inspect what agents decided in response to messages. A graph node can ask "did the scheduler re-plan in response to this failure?" and use the returned `ScheduleResponse`.

Existing callers that ignore the return value are unaffected — they just discard the dict.

## Step 2 — Design handler return types that are actionable

Each `handle_message()` should return something the caller can use directly:

| Message type | Handler returns | Caller does |
|---|---|---|
| `equipment_failure` | `ScheduleResponse` with new queue | Uses `response.schedule` as new queue |
| `station_history` | `None` (adjusts internal state) | Caller reads `agent.inspection_strictness` later |
| `processing_complete` | `None` (logged for observability) | No action needed |

**Rule:** Return a domain object only when the message demands a decision that changes system state. Return `None` for informational messages.

## Step 3 — Include full context in message payloads

When the original event producer (e.g., a station agent) lacks the context needed for a decision, have the consumer of the message (e.g., a graph node) send an **enriched follow-up message**:

```python
# Original message from station agent (basic, sent during pipeline):
printer_agent._send("scheduler", "equipment_failure", {
    "equipment": "printer",
    "order_id": order_id,
})

# Enriched message from graph node (full context, after pipeline returns):
bus.send(AgentMessage(
    sender="graph",
    receiver="scheduler",
    message_type="equipment_failure",
    payload={
        "equipment": "printer",
        "order_id": order_id,
        "equipment_statuses": [es.model_dump() for es in failed_eq_statuses],
        "pending_orders": [p.model_dump() for p in pending],
    },
))
responses = bus.dispatch()
sched_resp = responses.get("scheduler", [None])[-1]
```

**Why two messages:** The station agent knows it failed but doesn't have the global equipment statuses or pending order list. The graph node does — it enriches and re-dispatches. The pipeline's internal dispatches deliver the basic message (harmless — handler returns `None`). The graph node's enriched message triggers the actual re-plan.

## Step 4 — Have handlers deserialize payloads robustly

Messages carry serialized dicts (`model_dump()`). Handlers must reconstruct domain objects:

```python
def _handle_equipment_failure(self, msg: AgentMessage):
    equipment_statuses = [
        EquipmentStatusInfo(**e) if isinstance(e, dict) else e
        for e in msg.payload.get("equipment_statuses", [])
    ]
    pending = [
        PendingOrderInfo(**p) if isinstance(p, dict) else p
        for p in msg.payload.get("pending_orders", [])
    ]
    failed_eq = msg.payload.get("equipment", "")
    
    response = self.chain.invoke(equipment_statuses, pending, failed_eq)
    return response  # ScheduleResponse — captured by dispatch()
```

The `isinstance(e, dict)` check makes it work with both raw dicts (from serialization) and already-constructed objects (from tests).

## Step 5 — Adjust agent state from messages before process() is called

For agents that need contextual adjustments (not a one-shot decision), use messages to set internal state that `process()` later reads:

```python
class QualityControlAgent:
    def __init__(self, ...):
        self.inspection_strictness = "normal"
    
    def _adjust_from_station_history(self, msg):
        stations_used = msg.payload.get("stations_used", [])
        printer_cfg = msg.payload.get("printer_config", {})
        heat_press_cfg = msg.payload.get("heat_press_config", {})
        
        # Fewer stations → stricter
        if len(stations_used) <= 1:
            self.inspection_strictness = "high"
        elif len(stations_used) == 2:
            self.inspection_strictness = "elevated"
        else:
            self.inspection_strictness = "normal"
        
        # Risky parameters → stricter
        if printer_cfg.get("ink_saturation") == "heavy":
            self.inspection_strictness = max_strictness(...)
    
    def process(self, ...):
        # Uses self.inspection_strictness set by the message
        decision = self.qc_chain.invoke(..., inspection_strictness=self.inspection_strictness)
```

**Key timing:** The pipeline sends the `station_history` message and calls `bus.dispatch()` *before* calling `qc_agent.process()`. This ensures the state is adjusted before the decision is made.

## Step 6 — Send timing-critical messages at the right point

The pipeline must interleave message dispatch with processing:

```python
# In pipeline.py, BEFORE QC runs:
bus.send(AgentMessage(
    sender="pipeline",
    receiver="quality_control",
    message_type="station_history",
    payload={"stations_used": [...], "printer_config": {...}, ...},
))
bus.dispatch()  # QC agent adjusts strictness NOW

# THEN run QC with the adjusted state:
result_qc = qc_agent.process(...)
bus.dispatch()  # QC verdict messages delivered
```

If you dispatch after `process()`, the adjustment is too late and won't affect the current order.

## Anti-patterns

- **Handler that only logs**: `handle_message` must either change state or return a decision. Logging alone means the bus is cosmetic.
- **Handler calls chain but doesn't return result**: The caller can't use the decision. Always return domain objects from decision-making handlers.
- **Message lacks context**: If the handler needs equipment statuses but the message only has `order_id`, the handler can't act. Either enrich upstream or send a follow-up message with context.
- **Dispatch after state change**: If the message is meant to adjust behavior for the *current* processing step, dispatch must happen before that step runs.
