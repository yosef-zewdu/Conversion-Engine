from typing import Any, Optional
from pydantic import BaseModel, Field

class ResendTag(BaseModel):
    name: str
    value: str

class ResendData(BaseModel):
    model_config = {"populate_by_name": True}

    from_email: str = Field(alias="from", default="")
    to: list[str] = []
    subject: Optional[str] = None
    text: Optional[str] = None
    html: Optional[str] = None
    # Resend sends tags as a list of {name, value} objects
    tags: list[ResendTag] = []
    email_id: Optional[str] = None

    def get_tag(self, name: str) -> Optional[str]:
        for t in self.tags:
            if t.name == name:
                return t.value
        return None

    def extract_prospect_id_from_subject(self) -> Optional[str]:
        """Extract prospect_id embedded in subject line as [Lead: <id>]."""
        import re
        if self.subject:
            m = re.search(r"\[Lead:\s*([^\]]+)\]", self.subject)
            if m:
                return m.group(1).strip()
        return None

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
    custom_sink_email: str = ""
    first_channel: str = "email"
    auto_outreach: bool = False
    model: str = "qwen/qwen3-235b-a22b"

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
