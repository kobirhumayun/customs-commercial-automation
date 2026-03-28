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
            rationale="Export subject parsed successfully.",
        )
    return RuleEvaluationResult(
        rule_id="export_lc_sc.subject_parseable.v1",
        outcome=FinalDecision.HARD_BLOCK,
        rationale="Export subject must parse into deterministic LC/SC and buyer fields.",
        discrepancies=(
            RuleDiscrepancy(
                code="export_subject_unparseable",
                severity=FinalDecision.HARD_BLOCK,
                message="The export subject does not match a supported LC/SC naming pattern.",
                subject_scope="mail",
                target_ref=context.mail.mail_id,
                details={
                    "mail_id": context.mail.mail_id,
                    "subject_raw": context.mail.subject_raw,
                },
            ),
        ),
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


def _require_export_payload(payload) -> ExportMailPayload:
    if not isinstance(payload, ExportMailPayload):
        raise ValueError("Export LC/SC rules require an ExportMailPayload")
    return payload


RULE_DEFINITIONS = (
    RuleDefinition(
        rule_id="export_lc_sc.file_number_present.v1",
        stage=RuleStage.WORKFLOW_STANDARD,
        evaluator=evaluate_export_file_number_present,
    ),
    RuleDefinition(
        rule_id="export_lc_sc.subject_parseable.v1",
        stage=RuleStage.WORKFLOW_STANDARD,
        evaluator=evaluate_export_subject_parseable,
    ),
)
