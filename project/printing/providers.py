from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from project.models import PrintBatch


class PrintProvider(Protocol):
    def print_group(self, batch: PrintBatch, *, blank_page_after_group: bool) -> None:
        """Print one deterministic mail-group payload."""


@dataclass(slots=True, frozen=True)
class SimulatedPrintProvider:
    def print_group(self, batch: PrintBatch, *, blank_page_after_group: bool) -> None:
        for document_path in batch.document_paths:
            if not Path(document_path).exists():
                raise FileNotFoundError(document_path)
