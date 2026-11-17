from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, model_validator

ShortText = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=200)]
DetailText = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=4000)]


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class StatusArgs(StrictModel):
    service: Literal["all", "vpn", "wifi", "email", "dns", "account", "application"] = "all"


class LookupArgs(StrictModel):
    category: Literal["vpn", "wifi", "email", "dns", "account", "application"]
    query: Annotated[str, StringConstraints(strip_whitespace=True, max_length=200)] = ""
    limit: int = Field(default=3, ge=1, le=5)


class TicketArgs(StrictModel):
    ticket_id: int = Field(ge=1)


class CreateTicketArgs(StrictModel):
    title: ShortText
    description: DetailText
    category: Literal["vpn", "wifi", "email", "dns", "account", "application"]
    priority: Literal["low", "medium", "high"] = "medium"


class UpdateTicketArgs(TicketArgs):
    status: Literal["open", "in_progress", "resolved"] | None = None
    priority: Literal["low", "medium", "high"] | None = None
    note: DetailText | None = None

    @model_validator(mode="after")
    def has_change(self):
        if self.status is None and self.priority is None and self.note is None:
            raise ValueError("At least one of status, priority, or note is required")
        return self


class ChatRequest(StrictModel):
    message: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=4000)]


class ApprovalRequest(StrictModel):
    approved: bool
