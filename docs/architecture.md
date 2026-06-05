# Architecture Deep-Dive

This document explains the internals of the T-Shirt Factory MAS: what each agent does, the equipment it uses and how that equipment behaves, how LLM reasoning affects outcomes, the inter-agent communication protocol, and how results cascade through the system.

---

## 1. Scheduler Agent — Production Planner

**Equipment:** None. The scheduler is a pure decision agent. Its "equipment awareness" comes from reading `EquipmentStatusInfo` objects passed to it.

**What it does:** Orders pending orders into a prioritized processing queue via the `SchedulerChain` LLM. It is called:

- At simulation start (`plan_node`) to produce the initial queue.
- On any equipment failure (via `handle_message` → `_handle_equipment_failure`) to re-plan around the broken station.
- After a QC rejection escalates an order to `urgent` — the next `plan_node` call re-sorts accordingly.

**How reasoning affects output:** The `ScheduleResponse` Pydantic model contains:

| Field | Type | Description |
|---|---|---|
| `schedule` | `list[str]` | Ordered list of order IDs, e.g. `["O-001", "O-003", "O-002"]` |
| `reason` | `str` | Natural-language explanation of the scheduling decision |

The LLM receives a prompt that includes:

- Live equipment statuses (`printer: available`, `heat_press: failed`, …).
- The pending orders list with priorities.
- A failure note if any equipment is down.

The system prompt teaches it to: sort urgent before normal, minimize total waiting time, and plan around failed equipment. The `reason` field is visible in logs and the Streamlit UI but does not directly drive logic — the `schedule` list is what sets `state.queue`.

**Cascade effects:** The queue determines processing order for the entire simulation. A bad schedule means suboptimal throughput. The scheduler is **not** in the per-order pipeline path; it only sets the queue.

**Message handling:**

- `equipment_failure` → extracts `equipment_statuses` and `pending_orders` from the payload, calls the LLM chain, returns a new `ScheduleResponse`. The caller (`process_order_node`) uses this to update `state.queue`.

---

## 2. Printer Agent + Printer Equipment

### Equipment: `Printer`

```python
class Printer:
    status: str          # "available" | "busy" | "failed"
    failure_probability: float  # default 0.1 (10%)
```

**Behavior:**

1. Sets `status = "busy"`, sleeps `random.randint(2, 5)` seconds.
2. Rolls `random.random() < failure_probability`:
   - Hit → `status = "failed"`, returns `{"success": False, "error": "printer_failure"}`.
   - Miss → `status = "available"`, returns `{"success": True}`.
3. `reset()` sets status back to `"available"`.

This is a stateless machine — it knows nothing about the design or the order beyond the ID.

### Agent: `PrinterAgent`

**What the agent does on top of the equipment:**

1. Calls the `PrinterChain` LLM to decide *how* to print, producing a `PrinterDecision`.
2. Modulates the equipment's `failure_probability` based on the LLM's choices.
3. Runs the equipment.
4. Restores the base failure probability.
5. Sends a message to the scheduler about the outcome.

**LLM-driven parameters and their risk effects:**

| Parameter | Options | Risk adjustment |
|---|---|---|
| `print_temperature` | `low` / `standard` / `high` | `high` → +10% risk (scorching/bleeding). `low` → +5% (poor adhesion). |
| `ink_saturation` | `light` / `normal` / `heavy` | `heavy` → +12% risk (smudging). |
| `number_of_passes` | 1–5 | 3+ passes → -15% risk (more careful execution). 2 passes → -8%. |
| `color_profile` | `standard` / `vibrant` / `accurate` | No direct risk effect (informational). |

The `_adjust_failure_probability()` method applies these multipliers:

```python
adjusted = base * risk_multiplier
# Clamped to [0.02, 0.25]
```

**Example:** A `cyberpunk` design (7 colors, gradients) might get `high` temperature, `heavy` saturation, 4 passes:

```
risk = 1.0 + 0.10(high temp) + 0.12(heavy ink) - 0.15(4 passes) = 1.07
adjusted = 0.1 * 1.07 = 0.107
```

A `minimal` design (1 color, simple) might get `low` temperature, `light` saturation, 1 pass:

```
risk = 1.0 + 0.05(low temp) + 0.0(light ink) - 0.0(1 pass) = 1.05
adjusted = 0.1 * 1.05 = 0.105
```

**The LLM's reasoning directly changes the probability of equipment failure.**

**Messages sent:**

| Outcome | `message_type` | Payload |
|---|---|---|
| Success | `processing_complete` | `{"order_id": …, "station": "printer"}` |
| Failure | `equipment_failure` | `{"equipment": "printer", "order_id": …, "error": "printer_failure"}` |

**Cascade on failure:**

1. `process_order_node` catches `failed_printer`.
2. Order goes back to `pending_orders`.
3. `equipment_failure` message sent to scheduler with current equipment statuses + pending orders.
4. Scheduler re-plans the queue.
5. Equipment is reset (`printer.reset()`).

---

## 3. Heat Press Agent + Heat Press Equipment

### Equipment: `HeatPress`

Identical pattern to Printer: 2–5s sleep, `random.random() < failure_probability` (base 0.1), `status` cycling through `available` / `busy` / `failed`.

### Agent: `HeatPressAgent`

Same structure as `PrinterAgent`: LLM-driven configuration → risk modulation → equipment execution → message to scheduler.

**LLM-driven parameters and their risk effects:**

| Parameter | Options | Risk adjustment |
|---|---|---|
| `temperature` | `low` / `medium` / `high` | `high` alone → +10%. `high` + `firm` pressure → +18% (worst combo). `low` → +8% (incomplete curing). |
| `dwell_time` | `short` / `standard` / `extended` | `extended` → +15% (scorching risk). |
| `pressure` | `light` / `medium` / `firm` | Combines with temperature. No independent risk. |
| `multi_pass` | `true` / `false` | `true` → -12% risk (more careful handling). |

**Example:** A `retro` design ("crackle texture, low temperature, extended press"):

```
risk = 1.0 + 0.08(low temp) + 0.15(extended dwell) = 1.23
adjusted = 0.1 * 1.23 = 0.123
```

A `dragon` design (5 colors, standard curing):

```
risk = 1.0 + 0.0(medium temp) + 0.0(standard dwell) = 1.0
adjusted = 0.1
```

**Special cases encoded in the prompt:** The LLM prompt explicitly maps design keywords to parameters (e.g. "vintage distressed" → low temp + extended dwell; "glitter heat-transfer" → high temp + multi_pass).

**Forced failure demo:** `process_order_node` contains a demonstration: after 3 successful completions, the heat press is artificially set to `failed`, the scheduler is messaged with full context, then the equipment is "repaired" after 0.5 seconds. This exercises the re-plan loop without requiring an actual random failure.

**Cascade on failure:** Identical to Printer — back to pending, scheduler re-plan, equipment reset.

---

## 4. Quality Control Agent + Quality Station

### Equipment: `QualityStation`

**Unlike the other equipment, the QualityStation has no failure probability and no random pass/fail.** It only simulates physical inspection *time*:

```python
class QualityStation:
    def inspect(self, order_id):
        self.status = "busy"
        time.sleep(random.uniform(0.5, 2.0))  # inspection time
        self.status = "available"
        return processing_time
```

The actual verdict is **entirely LLM-driven**.

### Agent: `QualityControlAgent`

**Stateful agent** — maintains two instance variables that carry context across orders:

| State | Type | Purpose |
|---|---|---|
| `inspection_strictness` | `str` | `"normal"`, `"elevated"`, or `"high"` — injected into the QC LLM prompt |
| `station_history_context` | `str` | Raw history string from the pipeline (for debugging/logging) |

**Process flow:**

1. `equipment.inspect(order_id)` — simulates physical inspection time.
2. Calls `QCChain` LLM with:
   - `design_description` — complexity, colors, effects.
   - `processing_history` — which stations ran and what happened (e.g. `"printer: completed; heat_press: completed"`).
   - `inspection_strictness` — set by a prior message from the pipeline.
3. LLM returns a `QualityDecision`.
4. Agent sends a message to the scheduler and returns the result to the pipeline.

**QC LLM behavior:** The prompt instructs the LLM to be pragmatic — target roughly:

- ~70% **pass** — minor imperfections are acceptable.
- ~20% **rework** — moderate but repairable defects.
- ~10% **fail** — critical, non-repairable defects.

Factors that influence the verdict:

| Factor | Effect |
|---|---|
| Design complexity | Complex designs have more things that can go wrong, but also higher tolerance for minor misalignments. |
| Number of colors | Multi-color prints are more prone to registration errors. |
| Urgent priority | Slightly more lenient (the prompt says "fii puțin mai indulgent"). |
| Processing history | Fewer stations → higher hidden defect risk → stricter inspection. |
| Inspection strictness | `"high"` → added prompt note: "Fii deosebit de riguros… verifică cu atenție sporită." |

**Verdict outcomes:**

| Verdict | Action | Cascade |
|---|---|---|
| `pass` | Order continues to next station (or completes). | Proceeds normally. |
| `rework` | Order goes back to `pending_orders`. `rework_count` incremented. No priority escalation. | After `max_rework` (2) attempts → **force-complete** to prevent infinite loops. |
| `fail` | Order gets `priority = "urgent"`, added to `rejected_orders` list, re-added to pending. | Scheduler re-plans with this order escalated to front. Full re-print from scratch. |

### Message-driven strictness adjustment

Before QC runs, the pipeline sends a `station_history` message:

```python
bus.send(AgentMessage(
    sender="pipeline",
    receiver="quality_control",
    message_type="station_history",
    payload={
        "order_id": order_id,
        "stations_used": ["printer", "heat_press"],
        "history": "printer: completed; heat_press: completed",
        "printer_config": {"ink_saturation": "heavy", "print_temperature": "standard", …},
        "heat_press_config": {"temperature": "high", "pressure": "firm", …},
    },
))
```

The agent's `_adjust_from_station_history()` method then sets strictness:

- **≤1 station used** → `"high"` (less processing = higher hidden defect risk)
- **2 stations** → `"elevated"`
- **3+ stations** → `"normal"`
- **Bonus risk modifiers:** +1 if printer used `heavy` ink saturation; +1 if heat press used `high` temp + `firm` pressure.
- If `risk_modifiers ≥ 2` → strictness upgrades one level (e.g. `normal` → `elevated`).

This strictness is injected into the LLM prompt as a natural-language note, influencing the QC verdict. **This is a direct example of one agent's output (printer/heat-press parameters) affecting another agent's behavior (QC strictness).**

---

## 5. Packaging Agent + Packaging Station

### Equipment: `PackagingStation`

Same pattern as Printer/HeatPress but faster (1–3s sleep) and lower base failure probability (0.05 instead of 0.1).

### Agent: `PackagingAgent`

LLM-driven packaging configuration → risk modulation → equipment execution.

**LLM-driven parameters and their risk effects:**

| Parameter | Options | Risk adjustment |
|---|---|---|
| `packaging_type` | `standard_box` / `poly_mailer` / `gift_box` | `gift_box` → -15% risk (more careful). `poly_mailer` → +10% (less protection). |
| `fold_method` | `standard_fold` / `rolled` / `flat` | `rolled` → -8% risk (reduces creasing/handling issues). |
| `include_care_instructions` | `true` / `false` | Each extra item → +5% risk (more handling steps). |
| `include_thank_you_note` | `true` / `false` | (counted as extra item). |

The LLM prompt teaches it to balance speed vs. protection:
- **Urgent orders** → prefer faster methods (poly_mailer, skip extras) unless the design requires special protection.
- **Complex/delicate designs** → prioritize protection over speed.
- **Gift box** → slower but premium, reduces damage risk.

**Message sent on completion:** `order_completed` (the final notification — this order is done).

**Cascade on failure:** Back to pending, scheduler re-plan, equipment reset.

---

## 6. The Routing LLM — Per-Order Orchestrator

The `RoutingChain` is not an agent but is invoked from `process_order_pipeline()` before any station runs. It decides **which stations an order needs** and **in what order**, based on the design description and current equipment statuses.

**Example routing for a `minimal` design:**

```json
{
  "order_id": "O-005",
  "route": [
    {"station": "printer", "required": true, "notes": ""},
    {"station": "heat_press", "required": false, "notes": "single color, no curing needed"},
    {"station": "quality_control", "required": false, "notes": "low risk design"},
    {"station": "packaging", "required": true, "notes": ""}
  ],
  "reason": "Design simplu monocrom — doar printer și packaging sunt necesare."
}
```

**Routing rules encoded in the LLM prompt:**

- All orders must go through **packaging**.
- Single-color simple designs can skip **heat_press** and **QC**.
- Multi-color always requires printer + heat_press + QC.
- Special effects (glitter, crackle) must go through **heat_press** with special settings noted.
- Complex designs (5+ colors, gradients, halftones) must go through **QC**.
- Failed equipment → mark `required=false` with explanation in notes.

**Fallback:** If the LLM fails, all four stations are marked `required` with `"default fallback"` notes.

**Routing notes propagation:** The `notes` field from each `StationRoute` is passed to the corresponding agent as `routing_notes`, which gets injected into the station LLM's prompt. For example, if routing writes `"use lower temp"` for heat_press, the `HeatPressChain` prompt receives `"Note rutare: use lower temp"`.

---

## 7. Communication Protocol — MessageBus

The `MessageBus` is a **synchronous publish/subscribe** system. Agents register handlers by name; messages are queued and dispatched in rounds.

```python
class MessageBus:
    def register(self, agent_name, handler): …
    def send(self, message: AgentMessage): …        # queues a message
    def dispatch(self) -> dict[str, list]:           # delivers all queued, returns responses
```

**Message structure:**

```python
class AgentMessage(BaseModel):
    sender: str            # e.g. "printer", "pipeline", "graph", "simulation"
    receiver: str          # e.g. "scheduler", "quality_control"
    message_type: str      # semantic type, e.g. "equipment_failure", "station_history"
    payload: dict          # arbitrary context
    timestamp: datetime
```

### Message catalog

| `message_type` | Sender | Receiver | Payload highlights | Effect |
|---|---|---|---|---|
| `equipment_failure` | Station agents, `graph`, `simulation` | `scheduler` | `equipment`, `order_id`, `equipment_statuses[]`, `pending_orders[]` | Triggers immediate LLM re-plan. The scheduler returns a new `ScheduleResponse` used to update `state.queue`. |
| `processing_complete` | Printer, HeatPress | `scheduler` | `order_id`, `station` | Logged for observability. No state change. |
| `quality_rework` | QC agent | `scheduler` | `order_id`, `reason`, `rework_instructions`, `defect_severity` | Logged for observability. |
| `quality_rejected` | QC agent | `scheduler` | `order_id`, `reason`, `defect_severity` | Logged for observability. |
| `station_history` | `pipeline` | `quality_control` | `order_id`, `stations_used[]`, `history`, `printer_config`, `heat_press_config` | Adjusts QC `inspection_strictness` before the next inspection. |
| `order_completed` | Packaging | `scheduler` | `order_id` | Final notification. |

### Example: equipment failure triggers re-plan

1. Printer fails → `PrinterAgent._send("scheduler", "equipment_failure", {...})`
2. `pipeline.py` calls `bus.dispatch()` → delivers the message to `SchedulerAgent.handle_message()`
3. `SchedulerAgent._handle_equipment_failure(msg)` extracts `equipment_statuses` and `pending_orders` from the payload, calls `self.chain.invoke(...)`, returns a `ScheduleResponse`
4. `bus.dispatch()` collects it in `responses["scheduler"]`
5. `process_order_node` reads `responses["scheduler"][-1].schedule` → sets `state.queue`

### Example: QC strictness from cross-agent context

1. Pipeline has run printer + heat_press → sends `station_history` message with both configs
2. `bus.dispatch()` → `QualityControlAgent._adjust_from_station_history(msg)` reads `printer_config` and `heat_press_config`
3. Sets `self.inspection_strictness = "high"` or `"elevated"` based on station count and risk modifiers
4. Next call to `qc_agent.process()` injects the strictness into the LLM prompt

### Dispatch response pattern

`bus.dispatch()` returns `dict[str, list]` mapping receiver names to lists of non-None handler return values. Callers can inspect these for agent-driven state changes:

```python
responses = bus.dispatch()
scheduler_responses = responses.get("scheduler", [])
schedule_response = scheduler_responses[-1] if scheduler_responses else None
```

---

## 8. End-to-End Cascade Flow

```
plan_node (Scheduler LLM)
  │
  │  produces queue: ["O-001", "O-003", "O-002"]
  ▼
process_order_node (pops first from queue)
  │
  ▼
Routing LLM
  │  decides route: [printer✓, heat_press✓, qc✗, packaging✓]
  ▼
┌─────────────────────────────────────────────────────┐
│ PrinterAgent                                        │
│   PrinterChain LLM → sets print params              │
│   _adjust_failure_probability() → modulates risk    │
│   equipment.process() → random + modulated risk     │
│   ├─ success → sends processing_complete            │
│   └─ failure → back to pending, scheduler re-plan   │
├─────────────────────────────────────────────────────┤
│ HeatPressAgent                                      │
│   HeatPressChain LLM → sets press params            │
│   _adjust_failure_probability() → modulates risk    │
│   equipment.process() → random + modulated risk     │
│   ├─ success → sends processing_complete            │
│   └─ failure → back to pending, scheduler re-plan   │
├─────────────────────────────────────────────────────┤
│ Pipeline sends station_history → QC agent           │
│   QC agent adjusts inspection_strictness            │
├─────────────────────────────────────────────────────┤
│ QualityControlAgent                                 │
│   equipment.inspect() → physical time only          │
│   QCChain LLM → verdict based on design + history   │
│   ├─ pass → continue                                │
│   ├─ rework → back to pending (counter tracks)      │
│   └─ fail → escalated to urgent, added to rejected  │
├─────────────────────────────────────────────────────┤
│ PackagingAgent                                      │
│   PackagingChain LLM → sets packaging params        │
│   _adjust_failure_probability() → modulates risk    │
│   equipment.process() → random + modulated risk     │
│   ├─ success → order_completed, completed_count++   │
│   └─ failure → back to pending, scheduler re-plan   │
└─────────────────────────────────────────────────────┘
```

---

## Key Design Principles

1. **Every LLM decision has real consequence.** Printer/heat-press/packaging LLM choices modulate failure probability. QC LLM verdict determines whether the order completes, reworks, or gets re-printed. Routing LLM determines which stations even run. Scheduler LLM determines global processing order.

2. **Equipment is dumb; agents are smart.** The equipment classes are stateless simulators with `time.sleep()` and `random()` — they know nothing about the orders. The agents wrap them, call LLMs for domain decisions, and modulate equipment behavior based on LLM output.

3. **MessageBus enables cross-agent context.** The `station_history` message from pipeline → QC agent is the clearest example: printer and heat-press LLM decisions flow through the bus and influence QC strictness. Without the bus, QC would have no awareness of upstream processing quality.

4. **Rework protection prevents infinite loops.** If QC keeps returning `rework`, the `rework_count` on the `Order` model tracks attempts. After `max_rework` (2), the order is force-completed. This is a safety net, not a primary control — the QC prompt already targets realistic pass/rework/fail ratios.

5. **Stateless pipeline, stateful graph.** `process_order_pipeline()` is a pure function — all dependencies are parameters. State lives in `SimulationState`, checkpointed by PostgresSaver after each graph node. This clean separation makes the pipeline testable and the state durable.
