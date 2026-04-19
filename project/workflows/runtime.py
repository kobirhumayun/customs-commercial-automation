from __future__ import annotations

from dataclasses import dataclass

from project.models import MailOutcomeRecord


@dataclass(slots=True, frozen=True)
class WorkflowRuntimeState:
    mail_outcomes: list[MailOutcomeRecord]
