from enum import StrEnum


class WorkflowId(StrEnum):
    EXPORT_LC_SC = "export_lc_sc"
    UD_IP_EXP = "ud_ip_exp"
    IMPORT_BTB_LC = "import_btb_lc"
    BB_DASHBOARD_VERIFICATION = "bb_dashboard_verification"


class FinalDecision(StrEnum):
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


class RuleStage(StrEnum):
    CORE = "core"
    WORKFLOW_STANDARD = "workflow_standard"
    WORKFLOW_EXCEPTION = "workflow_exception"


class MailProcessingStatus(StrEnum):
    SNAPSHOTTED = "snapshotted"
    VALIDATION_PENDING = "validation_pending"
    VALIDATED = "validated"
    BLOCKED = "blocked"
    STAGED_FOR_WRITE = "staged_for_write"
    WRITTEN = "written"
    PRINTED = "printed"
    MOVED = "moved"
