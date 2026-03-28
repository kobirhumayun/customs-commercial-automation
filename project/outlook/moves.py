from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol

from project.models import MailMoveOperation


class MailMoveSourceLocationError(RuntimeError):
    """Raised when a mail is no longer in the expected source folder."""


class MailMoveProvider(Protocol):
    def move_mail(self, operation: MailMoveOperation) -> None:
        """Move one mail according to the deterministic move operation."""


@dataclass(slots=True)
class SimulatedMailMoveProvider:
    current_folder_by_entry_id: dict[str, str] = field(default_factory=dict)

    def move_mail(self, operation: MailMoveOperation) -> None:
        current_folder = self.current_folder_by_entry_id.get(operation.entry_id)
        if current_folder is not None and current_folder != operation.source_folder:
            raise MailMoveSourceLocationError(
                f"Mail {operation.entry_id} expected in {operation.source_folder}, found {current_folder}."
            )
        self.current_folder_by_entry_id[operation.entry_id] = operation.destination_folder
