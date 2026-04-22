from __future__ import annotations

import re
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Any

from project.documents import (
    SavedDocumentAnalysis,
    SavedDocumentAnalysisProvider,
    extract_saved_document_raw_report,
)
from project.erp.normalization import normalize_lc_sc_date
from project.models import SavedDocument
from project.workflows.ud_ip_exp.payloads import normalize_quantity_unit

PIONEER_SUPPLIERS = ("PIONEER DENIM LIMITED", "PIONEER DENIM LTD")


@dataclass(slots=True, frozen=True)
class StructuredUDExtractionContext:
    erp_lc_sc_number: str
    erp_ship_remarks: str = ""


@dataclass(slots=True, frozen=True)
class StructuredUDSavedDocumentAnalysisProvider:
    base_provider: SavedDocumentAnalysisProvider
    context: StructuredUDExtractionContext

    def analyze(self, *, saved_document: SavedDocument) -> SavedDocumentAnalysis:
        try:
            report = extract_saved_document_raw_report(saved_document=saved_document, mode="layered")
            structured = extract_structured_ud_analysis(
                report=report,
                context=self.context,
            )
        except Exception:
            structured = None
        if structured is not None:
            return structured
        return self.base_provider.analyze(saved_document=saved_document)


def extract_structured_ud_analysis(
    *,
    report: dict[str, Any],
    context: StructuredUDExtractionContext,
) -> SavedDocumentAnalysis | None:
    document_subtype = _classify_document(report)
    if document_subtype is None:
        return None

    document_number, document_date, document_provenance = _extract_document_number_and_date(
        report=report,
        document_subtype=document_subtype,
    )
    lc_row = _extract_lc_table_row(
        report=report,
        document_subtype=document_subtype,
        context=context,
    )
    quantity_by_unit, quantity_provenance = _extract_supplier_quantities(
        report=report,
        document_subtype=document_subtype,
    )
    primary_unit, primary_quantity = _primary_quantity(quantity_by_unit)

    return SavedDocumentAnalysis(
        analysis_basis="structured_ud_layered_table",
        extracted_document_number=document_number,
        extracted_document_number_confidence=1.0 if document_number else None,
        extracted_document_date=document_date,
        extracted_document_date_confidence=1.0 if document_date else None,
        extracted_document_subtype=document_subtype,
        extracted_lc_sc_number=lc_row["matched_identifier"] if lc_row else None,
        extracted_lc_sc_confidence=1.0 if lc_row else None,
        extracted_lc_sc_date=lc_row["date"] if lc_row else None,
        extracted_lc_sc_value=lc_row["value"] if lc_row else None,
        extracted_lc_sc_value_currency=lc_row["currency"] if lc_row else None,
        extracted_quantity=primary_quantity,
        extracted_quantity_unit=primary_unit,
        extracted_quantity_by_unit=quantity_by_unit or None,
        extracted_document_number_provenance=document_provenance if document_number else None,
        extracted_document_date_provenance=document_provenance if document_date else None,
        extracted_lc_sc_provenance=lc_row["provenance"] if lc_row else None,
        extracted_lc_sc_date_provenance=lc_row["provenance"] if lc_row else None,
        extracted_lc_sc_value_provenance=lc_row["provenance"] if lc_row else None,
        extracted_quantity_provenance=quantity_provenance if quantity_by_unit else None,
    )


def _classify_document(report: dict[str, Any]) -> str | None:
    first_page_text = _page_text(report, page_number=1).casefold()
    if "amendment authenticating authority".casefold() in first_page_text:
        return "ud_amendment"
    if "ud authenticating authority".casefold() in first_page_text:
        return "base_ud"
    combined_text = str(report.get("combined_text", "") or "").casefold()
    if "amendment authenticating authority".casefold() in combined_text:
        return "ud_amendment"
    if "ud authenticating authority".casefold() in combined_text:
        return "base_ud"
    return None


def _extract_document_number_and_date(
    *,
    report: dict[str, Any],
    document_subtype: str,
) -> tuple[str | None, str | None, dict[str, Any]]:
    target_token = "/AM/" if document_subtype == "ud_amendment" else "/UD/"
    for table in _iter_tables(report):
        if table["page_number"] != 1:
            continue
        rows = table["rows"]
        for row_index, row in enumerate(rows):
            for cell_index, cell in enumerate(row):
                value = _clean_cell(cell)
                if target_token not in value.upper() or "/W/" in value.upper():
                    continue
                document_number = _extract_bgmea_number(value, target_token)
                document_date = _row_date(row, preferred_start=cell_index + 1)
                return (
                    document_number,
                    document_date,
                    {
                        "page_number": table["page_number"],
                        "table_index": table["table_index"],
                        "row_index": row_index,
                        "column_index": cell_index,
                        "extraction_method": "structured_table",
                    },
                )
    return None, None, {"extraction_method": "structured_table"}


def _extract_lc_table_row(
    *,
    report: dict[str, Any],
    document_subtype: str,
    context: StructuredUDExtractionContext,
) -> dict[str, Any] | None:
    header_needle = "BACK-TO-BACK" if document_subtype == "ud_amendment" else "IMPORT L/C NO"
    value_column = 4 if document_subtype == "ud_amendment" else 3
    priority_identifiers = [
        value
        for value in (context.erp_ship_remarks.strip(), context.erp_lc_sc_number.strip())
        if value
    ]
    target_tables = []
    for table in _iter_tables(report):
        rows = table["rows"]
        if not rows or len(rows[0]) < 3:
            continue
        header_cell = _clean_cell(rows[0][1] if len(rows[0]) > 1 else "")
        if header_needle not in header_cell.upper():
            continue
        target_tables.append(table)

    for exact_identifier in priority_identifiers:
        match = _find_lc_table_row_for_identifier(
            tables=target_tables,
            exact_identifier=exact_identifier,
            value_column=value_column,
        )
        if match is not None:
            return match
    return None


def _find_lc_table_row_for_identifier(
    *,
    tables: list[dict[str, Any]],
    exact_identifier: str,
    value_column: int,
) -> dict[str, Any] | None:
    for table in tables:
        rows = table["rows"]
        for row_index, row in enumerate(rows[1:], start=1):
            if len(row) <= value_column:
                continue
            identifier = _clean_cell(row[1] if len(row) > 1 else "")
            if exact_identifier != identifier:
                continue
            raw_date = _clean_cell(row[2] if len(row) > 2 else "")
            raw_value = _clean_cell(row[value_column])
            amount, currency = _parse_money(raw_value)
            if amount is None:
                continue
            return {
                "matched_identifier": exact_identifier,
                "date": normalize_lc_sc_date(raw_date),
                "value": _format_decimal(amount),
                "currency": currency,
                "provenance": {
                    "page_number": table["page_number"],
                    "table_index": table["table_index"],
                    "row_index": row_index,
                    "matched_identifier": exact_identifier,
                    "extraction_method": "structured_table",
                },
            }
    return None


def _extract_supplier_quantities(
    *,
    report: dict[str, Any],
    document_subtype: str,
) -> tuple[dict[str, str], dict[str, Any]]:
    header_needle = "FABRIC/YARN DESCRIPTION" if document_subtype == "ud_amendment" else "FABRIC DESCRIPTION"
    totals: dict[str, Decimal] = {}
    last_supplier = ""
    started = False
    provenance_tables: list[dict[str, int]] = []

    for table in _iter_tables(report):
        rows = table["rows"]
        if not rows:
            continue
        first_cell = _clean_cell(rows[0][0] if rows[0] else "").upper()
        is_header = header_needle in first_cell
        if is_header:
            started = True
            data_rows = rows[1:]
            provenance_tables.append({"page_number": table["page_number"], "table_index": table["table_index"]})
        elif started and len(rows[0]) >= 7:
            data_rows = rows
        else:
            continue

        for row in data_rows:
            if len(row) < 7:
                continue
            if header_needle in _clean_cell(row[0]).upper():
                continue
            if _clean_cell(row[0]).upper().startswith("TOTAL"):
                started = False
                break
            supplier = _clean_cell(row[6])
            if supplier.upper() == "DO":
                supplier = last_supplier
            elif supplier:
                last_supplier = supplier
            if not _is_pioneer_supplier(supplier):
                continue
            quantity = _parse_decimal(_clean_cell(row[1]))
            if quantity is None:
                continue
            unit = normalize_quantity_unit(_clean_cell(row[2]))
            if unit not in {"YDS", "MTR"}:
                continue
            totals[unit] = totals.get(unit, Decimal("0")) + quantity

    return (
        {unit: _format_decimal(amount) for unit, amount in sorted(totals.items())},
        {"extraction_method": "structured_table", "tables": provenance_tables},
    )


def _iter_tables(report: dict[str, Any]) -> list[dict[str, Any]]:
    tables: list[dict[str, Any]] = []
    for page in _iter_pages(report):
        page_number = int(page.get("page_number", len(tables) + 1) or 1)
        for fallback_index, table in enumerate(page.get("tables", []) or [], start=1):
            if not isinstance(table, dict):
                continue
            rows = table.get("rows", [])
            if not isinstance(rows, list):
                continue
            tables.append(
                {
                    "page_number": page_number,
                    "table_index": int(table.get("table_index", fallback_index) or fallback_index),
                    "rows": [
                        [_clean_cell(cell) for cell in row]
                        for row in rows
                        if isinstance(row, list)
                    ],
                    "combined_text": str(table.get("combined_text", "") or ""),
                }
            )
    return tables


def _iter_pages(report: dict[str, Any]) -> list[dict[str, Any]]:
    pages: list[dict[str, Any]] = []
    raw_pages = report.get("pages", [])
    if isinstance(raw_pages, list):
        pages.extend(page for page in raw_pages if isinstance(page, dict))
    categories = report.get("categories", {})
    if isinstance(categories, dict):
        for category in ("table", "img2table"):
            raw_category = categories.get(category, {})
            if isinstance(raw_category, dict):
                raw_category_pages = raw_category.get("pages", [])
                if isinstance(raw_category_pages, list):
                    pages.extend(page for page in raw_category_pages if isinstance(page, dict))
    seen: set[tuple[int, int]] = set()
    unique_pages: list[dict[str, Any]] = []
    for page in pages:
        key = (int(page.get("page_number", 0) or 0), id(page))
        if key in seen:
            continue
        seen.add(key)
        unique_pages.append(page)
    return unique_pages


def _page_text(report: dict[str, Any], *, page_number: int) -> str:
    parts: list[str] = []
    for page in _iter_pages(report):
        if int(page.get("page_number", 0) or 0) != page_number:
            continue
        for key in ("searchable_text", "combined_text", "text"):
            value = str(page.get(key, "") or "")
            if value:
                parts.append(value)
        for table in page.get("tables", []) or []:
            if isinstance(table, dict):
                parts.append(str(table.get("combined_text", "") or ""))
    return "\n".join(parts)


def _clean_cell(value: object) -> str:
    return " ".join(str(value or "").replace("\ufffe", " ").split())


def _extract_bgmea_number(value: str, target_token: str) -> str | None:
    pattern = rf"BGMEA/DHK/{re.escape(target_token.strip('/'))}/[A-Z0-9./-]+"
    match = re.search(pattern, value.upper())
    if match is None:
        return None
    return match.group(0).rstrip(".,;:")


def _row_date(row: list[str], *, preferred_start: int) -> str | None:
    for value in row[preferred_start:] + row:
        normalized = normalize_lc_sc_date(_clean_cell(value))
        if normalized and re.search(r"\d", normalized):
            return normalized
    return None


def _parse_money(value: str) -> tuple[Decimal | None, str | None]:
    currency_match = re.search(r"\b[A-Z]{3}\b", value.upper())
    amount = _parse_decimal(value)
    return amount, currency_match.group(0) if currency_match else None


def _parse_decimal(value: str) -> Decimal | None:
    cleaned = re.sub(r"[^0-9.\-]", "", value.replace(",", ""))
    if not cleaned or cleaned in {"-", "."}:
        return None
    try:
        return Decimal(cleaned)
    except InvalidOperation:
        return None


def _primary_quantity(quantity_by_unit: dict[str, str]) -> tuple[str | None, str | None]:
    if not quantity_by_unit:
        return None, None
    unit = "YDS" if "YDS" in quantity_by_unit else sorted(quantity_by_unit)[0]
    return unit, quantity_by_unit[unit]


def _is_pioneer_supplier(value: str) -> bool:
    normalized = value.upper()
    return any(supplier in normalized for supplier in PIONEER_SUPPLIERS)


def _format_decimal(value: Decimal) -> str:
    normalized = format(value.normalize(), "f")
    if "." in normalized:
        normalized = normalized.rstrip("0").rstrip(".")
    return normalized or "0"
