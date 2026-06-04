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


# ---------------------------------------------------------------------------
# Printer models
# ---------------------------------------------------------------------------


class PrinterDecision(BaseModel):
    """LLM-driven printer configuration for an order."""

    order_id: str
    print_temperature: Literal["low", "standard", "high"] = Field(
        description="Print head temperature setting"
    )
    ink_saturation: Literal["light", "normal", "heavy"] = Field(
        description="Ink flow intensity"
    )
    number_of_passes: int = Field(
        ge=1, le=5, description="How many print head passes (1-5)"
    )
    color_profile: Literal["standard", "vibrant", "accurate"] = Field(
        description="Color rendering profile"
    )
    notes: str = Field(
        default="", description="Reasoning for printer configuration choices"
    )

    @field_validator("notes", mode="before")
    @classmethod
    def coerce_null_to_empty(cls, v: Any) -> str:
        if v is None:
            return ""
        return v


# ---------------------------------------------------------------------------
# HeatPress models
# ---------------------------------------------------------------------------


class HeatPressDecision(BaseModel):
    """LLM-driven heat press configuration for an order."""

    order_id: str
    temperature: Literal["low", "medium", "high"] = Field(
        description="Heat press temperature setting"
    )
    dwell_time: Literal["short", "standard", "extended"] = Field(
        description="How long the press holds"
    )
    pressure: Literal["light", "medium", "firm"] = Field(
        description="Press pressure level"
    )
    multi_pass: bool = Field(
        description="Whether multiple press cycles are needed"
    )
    notes: str = Field(
        default="", description="Reasoning for heat press configuration choices"
    )

    @field_validator("notes", mode="before")
    @classmethod
    def coerce_null_to_empty(cls, v: Any) -> str:
        if v is None:
            return ""
        return v


# ---------------------------------------------------------------------------
# Packaging models
# ---------------------------------------------------------------------------


class PackagingDecision(BaseModel):
    """LLM-driven packaging configuration for an order."""

    order_id: str
    packaging_type: Literal["standard_box", "poly_mailer", "gift_box"] = Field(
        description="Packaging container type"
    )
    fold_method: Literal["standard_fold", "rolled", "flat"] = Field(
        description="How the shirt is folded"
    )
    include_care_instructions: bool = Field(
        description="Whether to include care label instructions"
    )
    include_thank_you_note: bool = Field(
        description="Whether to include a thank-you card"
    )
    notes: str = Field(
        default="", description="Reasoning for packaging configuration choices"
    )

    @field_validator("notes", mode="before")
    @classmethod
    def coerce_null_to_empty(cls, v: Any) -> str:
        if v is None:
            return ""
        return v
