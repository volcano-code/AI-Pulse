from typing import Literal
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError
from pydantic import BaseModel, ConfigDict, Field, field_validator

TOPICS = ["LLM", "Agent", "开源", "论文", "多模态", "其他"]

class Preferences(BaseModel):
    model_config = ConfigDict(extra="forbid")
    timezone: str = "America/Los_Angeles"
    topics: list[str] = Field(default_factory=lambda: ["LLM", "Agent", "开源"], max_length=6)
    blocked_keywords: list[str] = Field(default_factory=list, max_length=20)
    max_items: int = Field(default=8, ge=1, le=12)
    lookback_hours: int = Field(default=72, ge=1, le=168)

    @field_validator("timezone")
    @classmethod
    def valid_zone(cls, v):
        try:
            ZoneInfo(v)
        except (ZoneInfoNotFoundError, ValueError):
            raise ValueError("Use a valid IANA timezone, e.g. Asia/Shanghai")
        return v

    @field_validator("topics")
    @classmethod
    def valid_topics(cls, value):
        if any(x not in TOPICS for x in value):
            raise ValueError("Unsupported topic")
        return list(dict.fromkeys(value))

    @field_validator("blocked_keywords")
    @classmethod
    def valid_keywords(cls, value):
        clean = [x.strip() for x in value if x.strip()]
        if any(len(x) > 60 for x in clean):
            raise ValueError("Keyword too long")
        return list(dict.fromkeys(clean))

class RunRequest(BaseModel):
    kind: Literal["ingest", "brief"]
    idempotency_key: str | None = Field(default=None, min_length=1, max_length=128)

class SourcePatch(BaseModel):
    enabled: bool

class BookmarkPatch(BaseModel):
    bookmarked: bool

class Question(BaseModel):
    question: str = Field(min_length=2, max_length=400)
    brief_id: str | None = None

class Approval(BaseModel):
    reviewed: Literal[True]

class LLMDraft(BaseModel):
    model_config = ConfigDict(extra="forbid")
    title: str = Field(min_length=1, max_length=180)
    summary: str = Field(min_length=1, max_length=700)
    evidence_quote: str = Field(min_length=12, max_length=1000)


class ReadMarker(BaseModel):
    model_config = ConfigDict(extra="forbid")
    snapshot_id: str = Field(min_length=1, max_length=36)


class ResearchRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    question: str = Field(min_length=2, max_length=400)
    brief_id: str | None = Field(default=None, max_length=36)
    max_tool_calls: int = Field(default=3, ge=1, le=4)
    max_model_calls: int = Field(default=3, ge=1, le=4)
    idempotency_key: str | None = Field(default=None, min_length=1, max_length=128)

    @field_validator("question")
    @classmethod
    def nonempty_question(cls, value):
        value = value.strip()
        if len(value) < 2:
            raise ValueError("Question must contain at least two non-whitespace characters")
        return value


class ResearchClaim(BaseModel):
    model_config = ConfigDict(extra="forbid")
    text: str = Field(min_length=1, max_length=700)
    evidence_ids: list[str] = Field(min_length=1, max_length=5)


class ResearchDraft(BaseModel):
    model_config = ConfigDict(extra="forbid")
    claims: list[ResearchClaim] = Field(max_length=6)
    insufficient_evidence: bool
