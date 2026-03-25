from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum


class WorkflowId(StrEnum):
    EXPORT_LC_SC = "export_lc_sc"
    UD_IP_EXP = "ud_ip_exp"
    IMPORT_BTB_LC = "import_btb_lc"
    BB_DASHBOARD_VERIFICATION = "bb_dashboard_verification"


class Decision(StrEnum):
    PASS = "pass"
    WARNING = "warning"
    HARD_BLOCK = "hard_block"


class WritePhaseStatus(StrEnum):
    NOT_STARTED = "not_started"
    PREVALIDATING_TARGETS = "prevalidating_targets"
    PREVALIDATED = "prevalidated"
    APPLYING = "applying"
    HARD_BLOCKED_NO_WRITE = "hard_blocked_no_write"
    UNCERTAIN_NOT_COMMITTED = "uncertain_not_committed"
    COMMITTED = "committed"


class PrintPhaseStatus(StrEnum):
    NOT_STARTED = "not_started"
    PLANNED = "planned"
    PRINTING = "printing"
    COMPLETED = "completed"
    HARD_BLOCKED = "hard_blocked"
    UNCERTAIN_INCOMPLETE = "uncertain_incomplete"


class MailMovePhaseStatus(StrEnum):
    NOT_STARTED = "not_started"
    MOVING = "moving"
    COMPLETED = "completed"
    HARD_BLOCKED = "hard_blocked"
    UNCERTAIN_INCOMPLETE = "uncertain_incomplete"


@dataclass(frozen=True, slots=True)
class EmailMessage:
    """Normalized mail metadata required for deterministic ordering and audit."""

    entry_id: str
    received_time_utc: datetime
    subject: str


@dataclass(frozen=True, slots=True)
class MailOrderRecord:
    """Persistable ordering record for run metadata."""

    entry_id: str
    received_time_utc: datetime
    received_time_local_iso: str
    order_index: int


@dataclass(frozen=True, slots=True)
class DiscrepancyEntry:
    """Structured discrepancy payload for report artifacts."""

    code: str
    severity: Decision
    message: str
