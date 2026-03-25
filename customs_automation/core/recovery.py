from __future__ import annotations

from enum import StrEnum

from customs_automation.core.contracts import WritePhaseStatus


class ProbeClassification(StrEnum):
    MATCHES_POST_WRITE = "matches_post_write"
    MATCHES_PRE_WRITE = "matches_pre_write"
    MISMATCH_UNKNOWN = "mismatch_unknown"


class RecoveryOutcome(StrEnum):
    SAFE_RESUME = "safe_resume"
    SAFE_REAPPLY_STAGED_WRITES = "safe_reapply_staged_writes"
    HARD_BLOCK = "hard_block"


class RecoveryReason(StrEnum):
    ARTIFACTS_INVALID = "artifacts_invalid"
    BACKUP_HASH_MISMATCH = "backup_hash_mismatch"
    STAGED_PLAN_HASH_INVALID = "staged_plan_hash_invalid"
    PROBE_MISMATCH_UNKNOWN = "probe_mismatch_unknown"
    PROBE_MIXED_STATE = "probe_mixed_state"
    PHASE_METADATA_CONTRADICTION = "phase_metadata_contradiction"
    SAFE_RESUME_CONDITIONS_MET = "safe_resume_conditions_met"
    SAFE_REAPPLY_CONDITIONS_MET = "safe_reapply_conditions_met"



def evaluate_recovery_decision(
    *,
    write_phase_status: WritePhaseStatus,
    probe_classifications: list[ProbeClassification],
    artifacts_valid: bool,
    backup_hash_matches: bool,
    staged_plan_hash_valid: bool,
) -> tuple[RecoveryOutcome, RecoveryReason]:
    if not artifacts_valid:
        return RecoveryOutcome.HARD_BLOCK, RecoveryReason.ARTIFACTS_INVALID
    if not backup_hash_matches:
        return RecoveryOutcome.HARD_BLOCK, RecoveryReason.BACKUP_HASH_MISMATCH
    if not staged_plan_hash_valid:
        return RecoveryOutcome.HARD_BLOCK, RecoveryReason.STAGED_PLAN_HASH_INVALID
    if not probe_classifications:
        return RecoveryOutcome.HARD_BLOCK, RecoveryReason.PROBE_MISMATCH_UNKNOWN

    probe_set = set(probe_classifications)
    if ProbeClassification.MISMATCH_UNKNOWN in probe_set:
        return RecoveryOutcome.HARD_BLOCK, RecoveryReason.PROBE_MISMATCH_UNKNOWN

    only_post_write = probe_set == {ProbeClassification.MATCHES_POST_WRITE}
    only_pre_write = probe_set == {ProbeClassification.MATCHES_PRE_WRITE}

    if not (only_post_write or only_pre_write):
        return RecoveryOutcome.HARD_BLOCK, RecoveryReason.PROBE_MIXED_STATE

    if only_post_write:
        if write_phase_status != WritePhaseStatus.COMMITTED:
            return RecoveryOutcome.HARD_BLOCK, RecoveryReason.PHASE_METADATA_CONTRADICTION
        return RecoveryOutcome.SAFE_RESUME, RecoveryReason.SAFE_RESUME_CONDITIONS_MET

    if write_phase_status not in {
        WritePhaseStatus.NOT_STARTED,
        WritePhaseStatus.UNCERTAIN_NOT_COMMITTED,
    }:
        return RecoveryOutcome.HARD_BLOCK, RecoveryReason.PHASE_METADATA_CONTRADICTION

    return RecoveryOutcome.SAFE_REAPPLY_STAGED_WRITES, RecoveryReason.SAFE_REAPPLY_CONDITIONS_MET
