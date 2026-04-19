from project.models import FinalDecision
from project.models.enums import RuleStage
from project.rules.types import RuleDefinition, RuleDiscrepancy, RuleEvaluationResult
from project.workflows.export_lc_sc.payloads import ExportMailPayload

RULE_PACK_ID = "export_lc_sc.default"
RULE_PACK_VERSION = "1.0.0"


def evaluate_export_subject_parseable(context) -> RuleEvaluationResult:
    payload = _require_export_payload(context.workflow_payload)
    if payload.parsed_subject is not None:
        return RuleEvaluationResult(
            rule_id="export_lc_sc.subject_parseable.v1",
            outcome=FinalDecision.PASS,
            rationale="Export subject parsed successfully for optional comparison only.",
        )
    return RuleEvaluationResult(
        rule_id="export_lc_sc.subject_parseable.v1",
        outcome=FinalDecision.PASS,
        rationale="Export subject parsing is optional because ERP rows are the final source of family data.",
    )


def evaluate_export_file_number_present(context) -> RuleEvaluationResult:
    payload = _require_export_payload(context.workflow_payload)
    if payload.file_numbers:
        return RuleEvaluationResult(
            rule_id="export_lc_sc.file_number_present.v1",
            outcome=FinalDecision.PASS,
            rationale="Export mail body yielded canonical file numbers.",
        )
    return RuleEvaluationResult(
        rule_id="export_lc_sc.file_number_present.v1",
        outcome=FinalDecision.HARD_BLOCK,
        rationale="Export mail body must contain at least one canonical file number.",
        discrepancies=(
            RuleDiscrepancy(
                code="export_file_number_missing",
                severity=FinalDecision.HARD_BLOCK,
                message="No canonical file numbers were extracted from the export mail body.",
                subject_scope="mail",
                target_ref=context.mail.mail_id,
                details={
                    "mail_id": context.mail.mail_id,
                    "body_text": context.mail.body_text,
                },
            ),
        ),
    )


def evaluate_export_erp_rows_present(context) -> RuleEvaluationResult:
    payload = _require_export_payload(context.workflow_payload)
    missing = [match.file_number for match in payload.erp_matches if match.canonical_row is None]
    if not missing:
        return RuleEvaluationResult(
            rule_id="export_lc_sc.erp_rows_present.v1",
            outcome=FinalDecision.PASS,
            rationale="All extracted file numbers resolved to canonical ERP rows.",
        )
    return RuleEvaluationResult(
        rule_id="export_lc_sc.erp_rows_present.v1",
        outcome=FinalDecision.HARD_BLOCK,
        rationale="Every extracted export file number must resolve to a canonical ERP row.",
        discrepancies=(
            RuleDiscrepancy(
                code="export_erp_row_missing",
                severity=FinalDecision.HARD_BLOCK,
                message="One or more extracted file numbers did not resolve to ERP rows.",
                subject_scope="mail",
                target_ref=context.mail.mail_id,
                details={
                    "mail_id": context.mail.mail_id,
                    "missing_file_numbers": missing,
                },
            ),
        ),
    )


def evaluate_export_family_consistent(context) -> RuleEvaluationResult:
    payload = _require_export_payload(context.workflow_payload)
    canonical_rows = [match.canonical_row for match in payload.erp_matches if match.canonical_row is not None]
    if not canonical_rows:
        return RuleEvaluationResult(
            rule_id="export_lc_sc.family_consistent.v1",
            outcome=FinalDecision.PASS,
            rationale="Family consistency awaits ERP row resolution.",
        )
    if payload.verified_family is not None:
        return RuleEvaluationResult(
            rule_id="export_lc_sc.family_consistent.v1",
            outcome=FinalDecision.PASS,
            rationale="Resolved ERP rows belong to one LC/SC family.",
        )
    return RuleEvaluationResult(
        rule_id="export_lc_sc.family_consistent.v1",
        outcome=FinalDecision.HARD_BLOCK,
        rationale="Resolved ERP rows must belong to one LC/SC family.",
        discrepancies=(
            RuleDiscrepancy(
                code="export_family_inconsistent",
                severity=FinalDecision.HARD_BLOCK,
                message="Resolved ERP rows do not share one LC/SC number, buyer, and LC/SC date.",
                subject_scope="mail",
                target_ref=context.mail.mail_id,
                details={
                    "mail_id": context.mail.mail_id,
                    "families": [
                        {
                            "file_number": match.file_number,
                            "lc_sc_number": match.canonical_row.lc_sc_number if match.canonical_row else None,
                            "buyer_name": match.canonical_row.buyer_name if match.canonical_row else None,
                            "lc_sc_date": match.canonical_row.lc_sc_date if match.canonical_row else None,
                        }
                        for match in payload.erp_matches
                    ],
                },
            ),
        ),
    )


def evaluate_export_subject_family_match(context) -> RuleEvaluationResult:
    payload = _require_export_payload(context.workflow_payload)
    if payload.parsed_subject is None or payload.verified_family is None:
        return RuleEvaluationResult(
            rule_id="export_lc_sc.subject_family_match.v1",
            outcome=FinalDecision.PASS,
            rationale="Subject-to-family comparison is optional and does not block ERP-driven processing.",
        )
    if (
        payload.parsed_subject.lc_sc_number == payload.verified_family.lc_sc_number
        and payload.parsed_subject.buyer_name == payload.verified_family.buyer_name
    ):
        return RuleEvaluationResult(
            rule_id="export_lc_sc.subject_family_match.v1",
            outcome=FinalDecision.PASS,
            rationale="Parsed subject matches the verified ERP family for optional comparison.",
        )
    return RuleEvaluationResult(
        rule_id="export_lc_sc.subject_family_match.v1",
        outcome=FinalDecision.PASS,
        rationale="Subject-to-ERP family mismatch is advisory only because ERP rows are final.",
    )


def _require_export_payload(payload) -> ExportMailPayload:
    if not isinstance(payload, ExportMailPayload):
        raise ValueError("Export LC/SC rules require an ExportMailPayload")
    return payload


RULE_DEFINITIONS = (
    RuleDefinition(
        rule_id="export_lc_sc.erp_rows_present.v1",
        stage=RuleStage.WORKFLOW_STANDARD,
        evaluator=evaluate_export_erp_rows_present,
    ),
    RuleDefinition(
        rule_id="export_lc_sc.family_consistent.v1",
        stage=RuleStage.WORKFLOW_STANDARD,
        evaluator=evaluate_export_family_consistent,
    ),
    RuleDefinition(
        rule_id="export_lc_sc.file_number_present.v1",
        stage=RuleStage.WORKFLOW_STANDARD,
        evaluator=evaluate_export_file_number_present,
    ),
    RuleDefinition(
        rule_id="export_lc_sc.subject_family_match.v1",
        stage=RuleStage.WORKFLOW_STANDARD,
        evaluator=evaluate_export_subject_family_match,
    ),
    RuleDefinition(
        rule_id="export_lc_sc.subject_parseable.v1",
        stage=RuleStage.WORKFLOW_STANDARD,
        evaluator=evaluate_export_subject_parseable,
    ),
)
