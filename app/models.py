from __future__ import annotations

from typing import Dict, List, Literal, Optional
from pydantic import BaseModel, Field


class AskRequest(BaseModel):
    thread_id: str
    question: str
    schema_text: Optional[str] = None


class ResumeRequest(BaseModel):
    thread_id: str
    human_answer: str


class AgentResponse(BaseModel):
    status: Literal["answered", "needs_human"]
    answer: Optional[str] = None
    follow_up_question: Optional[str] = None
    confidence: Optional[float] = None
    extracted_objects: Dict[str, List[str]] = Field(default_factory=dict)