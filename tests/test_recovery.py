from customs_automation.core.contracts import WritePhaseStatus
from customs_automation.core.recovery import (
    ProbeClassification,
    RecoveryOutcome,
    RecoveryReason,
    evaluate_recovery_decision,
)


def test_recovery_safe_resume_when_all_post_write_and_committed() -> None:
    outcome, reason = evaluate_recovery_decision(
        write_phase_status=WritePhaseStatus.COMMITTED,
        probe_classifications=[ProbeClassification.MATCHES_POST_WRITE],
        artifacts_valid=True,
        backup_hash_matches=True,
        staged_plan_hash_valid=True,
    )

    assert outcome == RecoveryOutcome.SAFE_RESUME
    assert reason == RecoveryReason.SAFE_RESUME_CONDITIONS_MET


def test_recovery_safe_reapply_when_all_pre_write_and_uncertain() -> None:
    outcome, reason = evaluate_recovery_decision(
        write_phase_status=WritePhaseStatus.UNCERTAIN_NOT_COMMITTED,
        probe_classifications=[ProbeClassification.MATCHES_PRE_WRITE],
        artifacts_valid=True,
        backup_hash_matches=True,
        staged_plan_hash_valid=True,
    )

    assert outcome == RecoveryOutcome.SAFE_REAPPLY_STAGED_WRITES
    assert reason == RecoveryReason.SAFE_REAPPLY_CONDITIONS_MET


def test_recovery_hard_blocks_on_mixed_probe_state() -> None:
    outcome, reason = evaluate_recovery_decision(
        write_phase_status=WritePhaseStatus.UNCERTAIN_NOT_COMMITTED,
        probe_classifications=[
            ProbeClassification.MATCHES_PRE_WRITE,
            ProbeClassification.MATCHES_POST_WRITE,
        ],
        artifacts_valid=True,
        backup_hash_matches=True,
        staged_plan_hash_valid=True,
    )

    assert outcome == RecoveryOutcome.HARD_BLOCK
    assert reason == RecoveryReason.PROBE_MIXED_STATE


def test_recovery_hard_blocks_on_phase_contradiction() -> None:
    outcome, reason = evaluate_recovery_decision(
        write_phase_status=WritePhaseStatus.NOT_STARTED,
        probe_classifications=[ProbeClassification.MATCHES_POST_WRITE],
        artifacts_valid=True,
        backup_hash_matches=True,
        staged_plan_hash_valid=True,
    )

    assert outcome == RecoveryOutcome.HARD_BLOCK
    assert reason == RecoveryReason.PHASE_METADATA_CONTRADICTION
