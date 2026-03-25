from __future__ import annotations


class AutomationError(Exception):
    """Base exception for deterministic automation failures."""


class InvalidPhaseTransitionError(AutomationError):
    """Raised when a forbidden workflow phase transition is attempted."""

    discrepancy_code = "invalid_phase_state_transition"

    def __init__(self, phase_name: str, current: str, next_status: str) -> None:
        super().__init__(
            f"Invalid {phase_name} transition: {current!r} -> {next_status!r} "
            f"({self.discrepancy_code})"
        )
        self.phase_name = phase_name
        self.current = current
        self.next_status = next_status
