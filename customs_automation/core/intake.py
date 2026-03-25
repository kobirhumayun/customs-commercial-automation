from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from customs_automation.core.contracts import EmailMessage


class IntakeAdapter(Protocol):
    def list_working_messages(self) -> list[EmailMessage]:
        """Return all messages currently present in the workflow working queue."""


@dataclass(frozen=True, slots=True)
class StaticIntakeAdapter:
    messages: list[EmailMessage]

    def list_working_messages(self) -> list[EmailMessage]:
        return list(self.messages)
