import pytest

from customs_automation.core.contracts import PrintPhaseStatus, WritePhaseStatus
from customs_automation.core.errors import InvalidPhaseTransitionError
from customs_automation.core.phases import (
    is_allowed_print_transition,
    is_allowed_transition,
    transition_write_phase,
)


def test_write_phase_status_rejects_invalid_transition() -> None:
    assert not is_allowed_transition(WritePhaseStatus.NOT_STARTED, WritePhaseStatus.APPLYING)


def test_print_phase_status_allows_planned_to_printing() -> None:
    assert is_allowed_print_transition(PrintPhaseStatus.PLANNED, PrintPhaseStatus.PRINTING)


def test_transition_write_phase_raises_for_invalid_transition() -> None:
    with pytest.raises(InvalidPhaseTransitionError):
        transition_write_phase(WritePhaseStatus.NOT_STARTED, WritePhaseStatus.APPLYING)
