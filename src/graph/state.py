from pydantic import BaseModel, Field

from models.order import Order


class SimulationState(BaseModel):
    """Complete simulation state — the single source of truth for the graph.

    Every field is checkpointed by PostgresSaver, enabling pause/resume.
    """

    # Order collections (moved from SchedulerAgent)
    pending_orders: dict[str, Order] = Field(default_factory=dict)
    in_progress: dict[str, Order] = Field(default_factory=dict)
    completed_orders: dict[str, Order] = Field(default_factory=dict)
    rejected_orders: list[str] = Field(default_factory=list)

    # All orders snapshot (for final statistics)
    all_orders: dict[str, Order] = Field(default_factory=dict)

    # Current schedule queue
    queue: list[str] = Field(default_factory=list)
    schedule_reason: str = ""

    # Outcome of last pipeline run (used by conditional edges)
    pipeline_result: str = ""

    # Counters
    iteration: int = 0
    re_plan_count: int = 0
    completed_count: int = 0

    # Flags
    heat_press_failure_triggered: bool = False

    # Limits
    max_iterations: int = 50
    max_rework: int = 2
