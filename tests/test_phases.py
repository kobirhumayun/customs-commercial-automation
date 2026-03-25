from customs_automation.core.contracts import PrintPhaseStatus, WritePhaseStatus
from customs_automation.core.phases import is_allowed_print_transition, is_allowed_transition


def test_write_phase_status_rejects_invalid_transition() -> None:
    assert not is_allowed_transition(WritePhaseStatus.NOT_STARTED, WritePhaseStatus.APPLYING)


def test_print_phase_status_allows_planned_to_printing() -> None:
    assert is_allowed_print_transition(PrintPhaseStatus.PLANNED, PrintPhaseStatus.PRINTING)
