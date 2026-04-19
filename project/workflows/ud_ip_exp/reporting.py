from __future__ import annotations

from typing import Any

from project.workflows.ud_ip_exp.matching import UDAllocationCandidate, UDAllocationResult


def build_ud_selection_report(allocation_result: UDAllocationResult) -> dict[str, Any]:
    return {
        "required_quantity": allocation_result.required_quantity,
        "quantity_unit": allocation_result.quantity_unit,
        "candidate_count": allocation_result.candidate_count,
        "candidates": [
            _candidate_report(candidate)
            for candidate in sorted(allocation_result.candidates, key=lambda item: item.candidate_id)
        ],
        "final_decision": _selection_final_decision(allocation_result),
        "final_decision_reason": allocation_result.final_decision_reason,
        "selected_candidate_id": allocation_result.selected_candidate_id,
        "discrepancy_code": allocation_result.discrepancy_code,
    }


def _candidate_report(candidate: UDAllocationCandidate) -> dict[str, Any]:
    return {
        "candidate_id": candidate.candidate_id,
        "row_indexes": list(candidate.row_indexes),
        "matched_quantities": list(candidate.matched_quantities),
        "quantity_sum": candidate.quantity_sum,
        "ignored_excess_quantity": candidate.ignored_excess_quantity,
        "score_keys": _score_keys_report(candidate.score_keys),
        "prewrite_blank_targets_count": candidate.prewrite_blank_targets_count,
        "prewrite_nonblank_optional_count": candidate.prewrite_nonblank_optional_count,
        "selected": candidate.selected,
        "rejection_reason": candidate.rejection_reason,
    }


def _score_keys_report(score_keys: dict[str, Any]) -> dict[str, Any]:
    blank_priority = score_keys.get("blank_field_priority_key", {})
    return {
        "row_index_key": list(score_keys.get("row_index_key", [])),
        "amendment_recency_key": [
            list(item)
            for item in score_keys.get("amendment_recency_key", [])
        ],
        "blank_field_priority_key": {
            "blank_target_count_desc": blank_priority.get("blank_target_count_desc"),
            "nonblank_optional_count_asc": blank_priority.get("nonblank_optional_count_asc"),
        },
        "stable_candidate_id_key": score_keys.get("stable_candidate_id_key"),
    }


def _selection_final_decision(allocation_result: UDAllocationResult) -> str:
    if allocation_result.final_decision == "selected":
        return "selected"
    if allocation_result.discrepancy_code == "ud_candidate_tie_after_full_tiebreak":
        return "hard_block_tie"
    return "hard_block"
