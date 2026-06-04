from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator


class EquipmentStatusInfo(BaseModel):
    name: str
    status: str


class PendingOrderInfo(BaseModel):
    id: str
    priority: str


class ScheduleResponse(BaseModel):
    schedule: list[str] = Field(description="Ordered list of order IDs to process")
    reason: str = Field(description="Brief explanation of the scheduling decision")


# ---------------------------------------------------------------------------
# Pipeline routing models
# ---------------------------------------------------------------------------


class StationRoute(BaseModel):
    """A single pipeline stage in the routing plan."""

    station: Literal["printer", "heat_press", "quality_control", "packaging"]
    required: bool = Field(
        description="Whether this station must process the order"
    )
    notes: str = Field(
        default="",
        description="Any special instructions for this station (e.g. 'use lower temp')",
    )


class RoutingDecision(BaseModel):
    """LLM decides which stations an order needs, and in what order."""

    order_id: str
    route: list[StationRoute] = Field(
        description="Ordered list of pipeline stations to process the order"
    )
    reason: str = Field(description="Explanation of the routing decision")


# ---------------------------------------------------------------------------
# Quality Control models
# ---------------------------------------------------------------------------


class QualityDecision(BaseModel):
    """LLM-driven quality inspection result."""

    verdict: Literal["pass", "fail", "rework"] = Field(
        description="Quality inspection verdict"
    )
    reason: str = Field(
        description="Detailed explanation of the quality decision"
    )
    rework_instructions: str = Field(
        default="",
        description="If verdict is 'rework', specific instructions for what needs fixing",
    )
    defect_severity: Literal["none", "minor", "major", "critical"] = Field(
        default="none",
        description="Severity level of any defects found",
    )

    @field_validator("rework_instructions", mode="before")
    @classmethod
    def coerce_null_to_empty(cls, v: Any) -> str:
        if v is None:
            return ""
        return v
