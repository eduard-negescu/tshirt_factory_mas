---
name: llm-impact-audit
description: Audit an LLM's role in a codebase — identify when it's doing trivial work a deterministic function could replace, and how to find decisions where LLM reasoning adds genuine, non-replicable value.
source: auto-skill
extracted_at: '2026-06-04T10:35:25.575Z'
---

# LLM Impact Audit — Making AI Earn Its Place

Use this when evaluating whether an LLM in a system is pulling its weight or could be replaced by a 10-line function. Applied here to a T-shirt factory multi-agent scheduler, but the pattern generalizes.

## Step 1 — Map what the LLM currently does

Trace every LLM call:
- What inputs does it receive? (structured data? natural language?)
- What outputs does it produce? (which fields drive behavior vs. which are decorative?)
- Which outputs are **actually consumed** by the system vs. logged-and-ignored?

In the T-shirt factory case: the LLM received equipment status + order list and returned an ordered list. The `reason` string was logged but never drove logic. Only the `schedule` list mattered — and that was just "urgent first, then FIFO."

## Step 2 — Ask: could a deterministic function replace this?

If the LLM's consumed output is derivable from a simple sort/comparison/filter, it's under-utilized. Test: write the deterministic equivalent in pseudocode. If it's ≤20 lines, the LLM is dead weight.

**Heuristic**: If the LLM only reorders, filters, or selects from structured data with clear rules, it's replaceable.

## Step 3 — Find decisions that are genuinely non-deterministic

Look for points in the system where:
- **Context richness matters**: The decision depends on nuanced, unstructured information (design descriptions, free-text instructions, processing history).
- **Multiple competing constraints exist**: Trade-offs between speed, quality, cost — things hard to encode in a sort key.
- **Human-like judgment is needed**: Pass/fail with shades of gray, not binary rules.
- **The output changes system behavior**: Not just ordering, but routing, skipping stages, adding steps.

In this case:
- **Pipeline routing**: Which stations does an order need? Simple designs skip heat press; complex designs need everything. Design descriptions are unstructured natural language.
- **Quality control**: Is this 3mm misalignment acceptable for a complex 7-color design? For a simple 1-color logo? The answer differs. Random rejection can't capture this nuance.

## Step 4 — Give the LLM rich context

The LLM can't reason about what it doesn't know. Add context fields:
- Design descriptions (not just names): colors, complexity, special treatments
- Processing history: what stations processed it, any issues
- Priority and deadlines
- Equipment capabilities and current status

## Step 5 — Design the output to drive behavior

The LLM's output should directly control system flow:
- `RoutingDecision` with `required` flags → pipeline skips unnecessary stations
- `QualityDecision` with `verdict` (pass/fail/rework) + `rework_instructions` → nuanced QC

Every field in the output model should have a code path that acts on it.

## Anti-pattern: decorative LLM output

If the LLM produces a `reason` string that only gets logged, the LLM is partly decorative. Either wire it into behavior (show it to users, use it for auditing) or drop it.
