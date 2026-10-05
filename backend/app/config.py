from pathlib import Path
from typing import Literal
from zoneinfo import ZoneInfo
from pydantic import Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")
    database_url: str = "sqlite:///./data/ai-pulse.db"
    data_mode: Literal["live", "replay"] = "live"
    llm_mode: Literal["extractive", "live"] = "extractive"
    llm_base_url: str = "https://api.openai.com/v1"
    llm_api_key: str = ""
    llm_model: str = ""
    llm_response_format: Literal["json_schema", "json_object"] = "json_schema"
    llm_max_output_tokens: int = Field(default=700, ge=200, le=2000)
    retrieval_mode: Literal["lexical", "hybrid"] = "lexical"
    embedding_provider: Literal["", "fixture", "openai"] = ""
    embedding_base_url: str = "https://api.openai.com/v1"
    embedding_api_key: str = ""
    embedding_timeout_seconds: float = Field(default=20, ge=1, le=60)
    embedding_model: str = ""
    embedding_dim: int = Field(default=0, ge=0, le=16000)
    retrieval_lexical_k: int = Field(default=50, ge=1, le=100)
    retrieval_vector_k: int = Field(default=50, ge=1, le=100)
    retrieval_final_k: int = Field(default=20, ge=1, le=50)
    admin_token: str = ""
    timezone: str = "America/Los_Angeles"
    max_fetch_bytes: int = Field(default=2_000_000, ge=1024, le=5_000_000)
    fetch_timeout_seconds: float = Field(default=12, ge=1, le=30)
    fetch_deadline_seconds: float = Field(default=20, ge=1, le=90)
    research_deadline_seconds: float = Field(default=60, ge=5, le=180)
    fetch_user_agent: str = "AI-Pulse/0.3 (+local research feed reader)"
    allowed_fetch_hosts: str = "huggingface.co,export.arxiv.org,api.github.com"
    auto_init_db: bool = True
    task_mode: Literal["local", "durable"] = "local"
    worker_poll_seconds: float = Field(default=2, ge=0.1, le=60)
    worker_lease_seconds: int = Field(default=120, ge=10, le=600)
    job_max_attempts: int = Field(default=3, ge=1, le=5)
    retry_base_seconds: int = Field(default=10, ge=1, le=300)
    delivery_transport: Literal["file", "smtp"] = "file"
    outbox_dir: str = "./data/outbox"
    mail_from: str = "ai-pulse@example.invalid"
    mail_to: str = "reader@example.invalid"
    smtp_host: str = ""
    smtp_port: int = Field(default=587, ge=1, le=65535)
    smtp_security: Literal["starttls", "ssl"] = "starttls"
    smtp_username: str = ""
    smtp_password: str = ""
    smtp_timeout_seconds: int = Field(default=20, ge=1, le=60)

    @model_validator(mode="after")
    def validate_settings(self):
        ZoneInfo(self.timezone)
        if self.admin_token and len(self.admin_token) < 24:
            raise ValueError("ADMIN_TOKEN must contain at least 24 characters")
        if self.llm_mode == "live" and (not self.llm_api_key or not self.llm_model):
            raise ValueError("Live LLM mode requires LLM_API_KEY and LLM_MODEL")
        if self.retrieval_mode == "hybrid":
            metadata = (bool(self.embedding_provider), bool(self.embedding_model), bool(self.embedding_dim))
            if any(metadata) and not all(metadata):
                raise ValueError("Hybrid embedding metadata requires EMBEDDING_PROVIDER, EMBEDDING_MODEL and EMBEDDING_DIM")
            if self.embedding_provider == "fixture" and self.embedding_model != "fixture-sha256-v1":
                raise ValueError("Fixture embedding provider requires EMBEDDING_MODEL=fixture-sha256-v1")
            if self.embedding_provider == "openai":
                if not self.embedding_api_key:
                    raise ValueError("OpenAI embedding provider requires EMBEDDING_API_KEY")
                if self.data_mode == "replay":
                    raise ValueError("Synthetic replay data must not be sent to a paid embedding provider")
        if self.data_mode == "replay" and self.llm_mode == "live":
            raise ValueError("Replay data must not be sent to a paid model. Use extractive mode.")
        from email.headerregistry import Address
        for address in (self.mail_from, self.mail_to):
            if any(c in address for c in "\r\n") or len(address) > 320:
                raise ValueError("Use one mailbox without header control characters")
            try:
                parsed = Address(addr_spec=address)
                if not parsed.username or not parsed.domain:
                    raise ValueError("Mailbox must contain a domain")
            except Exception as exc:
                raise ValueError("MAIL_FROM and MAIL_TO must be single valid mailboxes") from exc
        if self.delivery_transport == "smtp":
            if self.data_mode == "replay":
                raise ValueError("Synthetic replay data cannot be sent through SMTP")
            if not self.smtp_host or any(x in self.smtp_host for x in "/@\r\n"):
                raise ValueError("Configure a trusted SMTP_HOST")
            if any(a.endswith(".invalid") for a in (self.mail_from, self.mail_to)):
                raise ValueError("SMTP requires real, explicitly configured mailboxes")
        return self

    @property
    def fetch_hosts(self) -> set[str]:
        return {h.strip().lower() for h in self.allowed_fetch_hosts.split(",") if h.strip()}

    def prepare_sqlite_dir(self):
        if self.database_url.startswith("sqlite:///./"):
            Path(self.database_url.removeprefix("sqlite:///")).parent.mkdir(parents=True, exist_ok=True)
