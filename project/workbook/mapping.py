from __future__ import annotations

from dataclasses import dataclass

from project.workbook.models import WorkbookSnapshot


@dataclass(slots=True, frozen=True)
class HeaderMappingSpec:
    column_key: str
    required_header_text: str
    allowed_aliases: tuple[str, ...] = ()
    required_column_index: int | None = None


EXPORT_HEADER_SPECS = (
    HeaderMappingSpec("file_no", "File No.", ("FILE NO", "File Number")),
    HeaderMappingSpec("lc_sc_no", "L/C No.", ("LC/SC No.", "LC No.")),
    HeaderMappingSpec("buyer_name", "Buyer Name", ("Buyer",)),
    HeaderMappingSpec("lc_issuing_bank", "L/C Issuing Bank"),
    HeaderMappingSpec("lc_issue_date", "LC Issue Date"),
    HeaderMappingSpec("export_amount", "Amount", required_column_index=6),
    HeaderMappingSpec("shipment_date", "Shipment Date"),
    HeaderMappingSpec("expiry_date", "Expiry Date"),
    HeaderMappingSpec("quantity_fabrics", "Quantity of Fabrics (Yds/Mtr)"),
    HeaderMappingSpec("lc_amnd_no", "L/C Amnd No."),
    HeaderMappingSpec("lc_amnd_date", "L/C Amnd Date"),
    HeaderMappingSpec("lien_bank", "Lien Bank"),
    HeaderMappingSpec("master_lc_no", "Master L/C No."),
    HeaderMappingSpec("master_lc_issue_date", "Master L/C Issue Dt."),
)


def resolve_header_mapping(
    snapshot: WorkbookSnapshot,
    specs: tuple[HeaderMappingSpec, ...],
) -> dict[str, int] | None:
    mapping: dict[str, int] = {}
    for spec in specs:
        matches = [
            header.column_index
            for header in snapshot.headers
            if _matches_header_text(header.text, spec)
        ]
        if spec.required_column_index is not None:
            matches = [column for column in matches if column == spec.required_column_index]
        if len(matches) != 1:
            return None
        mapping[spec.column_key] = matches[0]
    return mapping


def _matches_header_text(text: str, spec: HeaderMappingSpec) -> bool:
    normalized = text.strip()
    return normalized == spec.required_header_text or normalized in spec.allowed_aliases
