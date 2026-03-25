from __future__ import annotations

from customs_automation.core.contracts import (
    MailMovePhaseStatus,
    PrintPhaseStatus,
    WritePhaseStatus,
)
from customs_automation.core.errors import InvalidPhaseTransitionError

WRITE_ALLOWED_TRANSITIONS: dict[WritePhaseStatus, set[WritePhaseStatus]] = {
    WritePhaseStatus.NOT_STARTED: {WritePhaseStatus.PREVALIDATING_TARGETS},
    WritePhaseStatus.PREVALIDATING_TARGETS: {
        WritePhaseStatus.PREVALIDATED,
        WritePhaseStatus.HARD_BLOCKED_NO_WRITE,
    },
    WritePhaseStatus.PREVALIDATED: {WritePhaseStatus.APPLYING},
    WritePhaseStatus.APPLYING: {
        WritePhaseStatus.COMMITTED,
        WritePhaseStatus.UNCERTAIN_NOT_COMMITTED,
    },
    WritePhaseStatus.HARD_BLOCKED_NO_WRITE: set(),
    WritePhaseStatus.UNCERTAIN_NOT_COMMITTED: set(),
    WritePhaseStatus.COMMITTED: set(),
}

PRINT_ALLOWED_TRANSITIONS: dict[PrintPhaseStatus, set[PrintPhaseStatus]] = {
    PrintPhaseStatus.NOT_STARTED: {
        PrintPhaseStatus.PLANNED,
        PrintPhaseStatus.HARD_BLOCKED,
    },
    PrintPhaseStatus.PLANNED: {
        PrintPhaseStatus.PRINTING,
        PrintPhaseStatus.UNCERTAIN_INCOMPLETE,
        PrintPhaseStatus.HARD_BLOCKED,
    },
    PrintPhaseStatus.PRINTING: {
        PrintPhaseStatus.COMPLETED,
        PrintPhaseStatus.UNCERTAIN_INCOMPLETE,
    },
    PrintPhaseStatus.COMPLETED: set(),
    PrintPhaseStatus.HARD_BLOCKED: set(),
    PrintPhaseStatus.UNCERTAIN_INCOMPLETE: set(),
}

MAIL_MOVE_ALLOWED_TRANSITIONS: dict[MailMovePhaseStatus, set[MailMovePhaseStatus]] = {
    MailMovePhaseStatus.NOT_STARTED: {
        MailMovePhaseStatus.MOVING,
        MailMovePhaseStatus.HARD_BLOCKED,
    },
    MailMovePhaseStatus.MOVING: {
        MailMovePhaseStatus.COMPLETED,
        MailMovePhaseStatus.UNCERTAIN_INCOMPLETE,
    },
    MailMovePhaseStatus.COMPLETED: set(),
    MailMovePhaseStatus.HARD_BLOCKED: set(),
    MailMovePhaseStatus.UNCERTAIN_INCOMPLETE: set(),
}


def is_allowed_transition(current: WritePhaseStatus, next_status: WritePhaseStatus) -> bool:
    return next_status in WRITE_ALLOWED_TRANSITIONS[current]


def is_allowed_print_transition(current: PrintPhaseStatus, next_status: PrintPhaseStatus) -> bool:
    return next_status in PRINT_ALLOWED_TRANSITIONS[current]


def is_allowed_mail_move_transition(
    current: MailMovePhaseStatus,
    next_status: MailMovePhaseStatus,
) -> bool:
    return next_status in MAIL_MOVE_ALLOWED_TRANSITIONS[current]


def transition_write_phase(current: WritePhaseStatus, next_status: WritePhaseStatus) -> WritePhaseStatus:
    if not is_allowed_transition(current, next_status):
        raise InvalidPhaseTransitionError("write_phase_status", current.value, next_status.value)
    return next_status


def transition_print_phase(current: PrintPhaseStatus, next_status: PrintPhaseStatus) -> PrintPhaseStatus:
    if not is_allowed_print_transition(current, next_status):
        raise InvalidPhaseTransitionError("print_phase_status", current.value, next_status.value)
    return next_status


def transition_mail_move_phase(
    current: MailMovePhaseStatus,
    next_status: MailMovePhaseStatus,
) -> MailMovePhaseStatus:
    if not is_allowed_mail_move_transition(current, next_status):
        raise InvalidPhaseTransitionError("mail_move_phase_status", current.value, next_status.value)
    return next_status
