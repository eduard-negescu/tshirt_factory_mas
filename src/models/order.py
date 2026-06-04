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
        "Ilustrație complexă de dragon multi-color cu umbriri detaliate și degradeuri. "
        "5 culori, necesită înregistrare precisă a culorilor și aliniere. "
        "Imprimat pe zona frontală a pieptului. Necesită întărire termică pentru durabilitate."
    ),
    "unicorn": (
        "Design pastelat cu unicorn și suprapunere glitter prin transfer termic. "
        "3 culori de bază plus strat de glitter. Necesită presă termică pentru aderența glitter-ului. "
        "Tonurile pastelate delicate necesită calibrare atentă a culorilor."
    ),
    "cyberpunk": (
        "Peisaj urban cyberpunk foarte detaliat cu degradeuri neon și efecte de strălucire. "
        "7 culori cu amestecuri și tranziții semiton. Foarte complex, "
        "dens detaliat. Imprimat pe toată fața. Necesită timp de întărire extins."
    ),
    "minimal": (
        "Artă liniară simplă monocromă cu text mic. Estetică minimalistă. "
        "1 culoare (negru). Nu necesită tratamente speciale. Imprimare mică pe piept stânga. "
        "Rapid de procesat, consum redus de cerneală."
    ),
    "retro": (
        "Imprimare vintage cu efect intenționat de textură crackle (crăpată). "
        "2 culori (bleumarin decolorat + crem). Necesită setări speciale la presa termică "
        "cu temperatură mai joasă și timp de presare mai lung pentru aspect uzat."
    ),
    "floral": (
        "Model floral de complexitate medie cu petale și frunze suprapuse. "
        "4 culori cu unele amestecuri. Necesită aliniere precisă pentru marginile petalelor. "
        "Imprimare pe toată fața. Cerințe moderate de întărire."
    ),
    "geometric": (
        "Forme geometrice clare cu margini precise și linii exacte. "
        "2 culori (contrast ridicat). Necesită definire precisă a marginilor și "
        "fără sângerare între culori. Complexitate moderată."
    ),
}

# Fallback pentru orice design care nu este în catalog
FALLBACK_DESIGN_DESCRIPTION = "Design standard monocrom. Imprimare de bază cu întărire standard."
