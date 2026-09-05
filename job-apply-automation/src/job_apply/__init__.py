"""Standalone job application form automation.

Completely independent from ai-job-agent / resume-agent-web.
Accepts a job URL, CV file, and contact details; fills the form and submits.
"""

from job_apply.models import Applicant, ApplyRequest, ApplyResult
from job_apply.engine import apply_to_job

__all__ = [
    "Applicant",
    "ApplyRequest",
    "ApplyResult",
    "apply_to_job",
]

__version__ = "0.1.0"
