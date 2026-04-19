from project.models import FinalDecision
from project.models.enums import RuleStage
from project.rules.types import RuleDefinition, RuleDiscrepancy, RuleEvaluationResult

RULE_PACK_ID = "core.default"
RULE_PACK_VERSION = "1.0.0"


def evaluate_mail_sender_present(context) -> RuleEvaluationResult:
    sender = context.mail.sender_address.strip()
    if sender:
        return RuleEvaluationResult(
            rule_id="core.mail.sender_present.v1",
            outcome=FinalDecision.PASS,
            rationale="Sender address is present.",
        )
    return RuleEvaluationResult(
        rule_id="core.mail.sender_present.v1",
        outcome=FinalDecision.HARD_BLOCK,
        rationale="Sender address is required for deterministic mail lineage.",
        discrepancies=(
            RuleDiscrepancy(
                code="mail_sender_missing",
                severity=FinalDecision.HARD_BLOCK,
                message="The mail sender address is missing or blank.",
                subject_scope="mail",
                target_ref=context.mail.mail_id,
                details={
                    "mail_id": context.mail.mail_id,
                    "entry_id": context.mail.entry_id,
                    "workflow_id": context.workflow_id.value,
                },
            ),
        ),
    )


def evaluate_mail_subject_present(context) -> RuleEvaluationResult:
    subject = context.mail.subject_raw.strip()
    if subject:
        return RuleEvaluationResult(
            rule_id="core.mail.subject_present.v1",
            outcome=FinalDecision.PASS,
            rationale="Mail subject is present.",
        )
    return RuleEvaluationResult(
        rule_id="core.mail.subject_present.v1",
        outcome=FinalDecision.HARD_BLOCK,
        rationale="Mail subject is required for deterministic workflow classification.",
        discrepancies=(
            RuleDiscrepancy(
                code="mail_subject_missing",
                severity=FinalDecision.HARD_BLOCK,
                message="The mail subject is missing or blank.",
                subject_scope="mail",
                target_ref=context.mail.mail_id,
                details={
                    "mail_id": context.mail.mail_id,
                    "entry_id": context.mail.entry_id,
                    "workflow_id": context.workflow_id.value,
                },
            ),
        ),
    )


RULE_DEFINITIONS = (
    RuleDefinition(
        rule_id="core.mail.sender_present.v1",
        stage=RuleStage.CORE,
        evaluator=evaluate_mail_sender_present,
    ),
    RuleDefinition(
        rule_id="core.mail.subject_present.v1",
        stage=RuleStage.CORE,
        evaluator=evaluate_mail_subject_present,
    ),
)
