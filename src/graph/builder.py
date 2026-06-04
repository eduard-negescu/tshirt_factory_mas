"""LangGraph builder for the T-shirt factory simulation.

Returns a compiled StateGraph with PostgresSaver checkpointing.
"""

import logging

from langgraph.checkpoint.postgres import PostgresSaver
from langgraph.graph import END, START, StateGraph

from graph.nodes import plan_node, process_order_node
from graph.state import SimulationState

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Conditional edge routers
# ---------------------------------------------------------------------------


def _after_plan(state: SimulationState) -> str:
    """After plan_node: process next order, or end if nothing to do."""
    if not state.queue:
        logger.info("No orders in schedule — ending")
        return "end"
    return "process_order"


def _after_process_order(state: SimulationState) -> str:
    """After process_order_node: decide next step based on outcome."""
    if state.iteration >= state.max_iterations:
        logger.warning("Max iterations (%d) reached", state.max_iterations)
        return "end"

    result = state.pipeline_result

    # Pipeline result cleared (e.g. forced failure handled) — continue
    if not result:
        return "process_order"

    # Failures, rejections, rework — need a fresh plan
    if result.startswith("failed_") or result in ("rejected_qc", "rework_qc"):
        return "plan"

    # Completed successfully
    if result == "completed":
        if state.queue:
            return "process_order"
        elif state.pending_orders:
            return "plan"
        else:
            logger.info("All orders processed — ending")
            return "end"

    # Skip / empty queue
    if result in ("skip", "empty_queue"):
        if state.pending_orders:
            return "plan"
        else:
            return "end"

    logger.warning("Unknown pipeline_result '%s' — ending", result)
    return "end"


# ---------------------------------------------------------------------------
# Graph builder
# ---------------------------------------------------------------------------


def build_graph(checkpointer: PostgresSaver) -> StateGraph:
    """Build and compile the simulation state graph with PostgresSaver."""
    builder = StateGraph(SimulationState)

    builder.add_node("plan", plan_node)
    builder.add_node("process_order", process_order_node)

    builder.add_edge(START, "plan")

    builder.add_conditional_edges(
        "plan",
        _after_plan,
        {"process_order": "process_order", "end": END},
    )

    builder.add_conditional_edges(
        "process_order",
        _after_process_order,
        {"process_order": "process_order", "plan": "plan", "end": END},
    )

    graph = builder.compile(checkpointer=checkpointer)
    logger.info("Graph compiled with PostgresSaver")

    return graph
