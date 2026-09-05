"""Input/output models for the standalone apply automation."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


@dataclass
class Applicant:
    first_name: str
    last_name: str
    email: str
    phone: str

    def full_name(self) -> str:
        return f"{self.first_name.strip()} {self.last_name.strip()}".strip()

    def to_profile(self) -> dict[str, Any]:
        return {
            "first_name": self.first_name.strip(),
            "last_name": self.last_name.strip(),
            "full_name": self.full_name(),
            "email": self.email.strip(),
            "phone": self.phone.strip(),
        }


@dataclass
class ApplyRequest:
    job_url: str
    cv_path: Path
    applicant: Applicant
    dry_run: bool = False
    headless: bool = True
    timeout_ms: int = 60_000


@dataclass
class ApplyResult:
    success: bool
    status: str  # submitted | filled | failed | requires_user_action
    message: str = ""
    provider: str = "generic"
    job_url: str = ""
    final_url: str | None = None
    filled_fields: list[str] = field(default_factory=list)
    skipped_fields: list[str] = field(default_factory=list)
    confirmation_text: str | None = None
    screenshot_path: str | None = None
    screenshot_url: str | None = None
    failure_category: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "success": self.success,
            "status": self.status,
            "message": self.message,
            "provider": self.provider,
            "job_url": self.job_url,
            "final_url": self.final_url,
            "filled_fields": self.filled_fields,
            "skipped_fields": self.skipped_fields,
            "confirmation_text": self.confirmation_text,
            "screenshot_path": self.screenshot_path,
            "screenshot_url": self.screenshot_url,
            "failure_category": self.failure_category,
        }
