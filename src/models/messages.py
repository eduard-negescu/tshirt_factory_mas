from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field


class AgentMessage(BaseModel):
    sender: str
    receiver: str
    message_type: str
    payload: dict[str, Any] = Field(default_factory=dict)
    timestamp: datetime = Field(default_factory=datetime.now)
