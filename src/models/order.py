from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field


class Order(BaseModel):
    id: str
    priority: Literal["urgent", "normal"]
    created_at: datetime = Field(default_factory=datetime.now)
    status: str = "pending"
    design_name: str = "default"
    design_description: str = ""
    rework_count: int = 0


# ---------------------------------------------------------------------------
# Design catalogue — maps design_name to a rich natural-language description
# that the LLM can reason about for routing and QC decisions.
# ---------------------------------------------------------------------------

DESIGN_DETAILS: dict[str, str] = {
    "dragon": (
        "Complex multi-color dragon illustration with detailed shading and gradients. "
        "5 colors, requires precise color registration and alignment. "
        "Printed on front chest area. Needs heat curing for durability."
    ),
    "unicorn": (
        "Pastel unicorn design with glitter heat-transfer overlay. "
        "3 base colors plus glitter layer. Requires heat press for glitter adhesion. "
        "Delicate pastel tones need careful color calibration."
    ),
    "cyberpunk": (
        "High-detail cyberpunk cityscape with neon gradients and glow effects. "
        "7 colors with blending and halftone transitions. Very complex, "
        "densely detailed. Printed full front. Needs extended curing time."
    ),
    "minimal": (
        "Simple single-color line art with small text. Minimalist aesthetic. "
        "1 color (black). No special treatments needed. Small print on left chest. "
        "Quick to process, low ink usage."
    ),
    "retro": (
        "Vintage distressed print with intentional crackle texture effect. "
        "2 colors (faded navy + cream). Requires special heat press settings "
        "with lower temperature and longer dwell time for worn look."
    ),
    "floral": (
        "Medium-complexity floral pattern with overlapping petals and leaves. "
        "4 colors with some blending. Needs precise alignment for petal edges. "
        "Full front print. Moderate curing requirements."
    ),
    "geometric": (
        "Clean geometric shapes with sharp edges and precise lines. "
        "2 colors (high contrast). Requires precise edge definition and "
        "no bleeding between colors. Moderate complexity."
    ),
}

# Fallback for any design not in the catalogue
FALLBACK_DESIGN_DESCRIPTION = "Standard single-color design. Basic print with standard curing."
