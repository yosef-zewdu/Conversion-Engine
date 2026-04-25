from typing import Any, Optional
from pydantic import BaseModel, Field

class ResendData(BaseModel):
    from_email: str = Field(alias="from")
    to: list[str] = []
    text: Optional[str] = None
    html: Optional[str] = None
    tags: dict[str, str] = {}

class ResendPayload(BaseModel):
    type: str
    data: ResendData

class CalcomPayload(BaseModel):
    triggerEvent: str
    payload: dict[str, Any]

class CampaignRunRequest(BaseModel):
    campaign_id: str = "series-ab-ai-data-capacity"
    target_segments: list[str] = ["S1", "S4"]
    limit: int = 10
    mode: str = "staff_sink"
    first_channel: str = "email"

class StartOutreachRequest(BaseModel):
    inbound_text: str = "Hello, I'm interested in learning more."
    channel: str = "email"

class SimulateReplyRequest(BaseModel):
    lead_id: str
    scenario: str = "interested_positive"
    channel: str = "email"
    custom_text: str | None = None

class CreateLeadRequest(BaseModel):
    company_name: str
    company_id: str = ""
    contact_name: str = "Engineering Leader"
    email: str = ""
    phone: str | None = None
    timezone: str = "UTC"
    preferred_channel: str = "email"
    current_state: str = "cold"
    segment: str | None = None
    icp_confidence: float | None = None
    ai_maturity_score: int | None = None
    bench_mismatch: bool | None = None
    hiring_signal_brief: dict | None = None
    competitor_gap_brief: dict | None = None
    icp_result: dict | None = None
    source_refs: dict | None = None
