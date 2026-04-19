from __future__ import annotations

from decimal import Decimal
import unittest

from project.workflows.ud_ip_exp import (
    DocumentExtractionField,
    EXPDocumentPayload,
    IPDocumentPayload,
    UDDocumentPayload,
    UDIPEXPDocumentKind,
    UDIPEXPQuantity,
    format_shared_column_entry,
    format_shared_column_values,
)


class UDIPEXPModelTests(unittest.TestCase):
    def test_ud_payload_normalizes_quantity_unit(self) -> None:
        payload = UDDocumentPayload(
            document_number=DocumentExtractionField("UD-LC-0043"),
            document_date=DocumentExtractionField("2026-04-01"),
            lc_sc_number=DocumentExtractionField("LC-0043"),
            quantity=UDIPEXPQuantity(amount=Decimal("3000"), unit="yards"),
        )

        self.assertEqual(payload.document_kind, UDIPEXPDocumentKind.UD)
        self.assertEqual(payload.quantity.amount, Decimal("3000"))
        self.assertEqual(payload.quantity.unit, "YDS")

    def test_shared_column_entry_prefixes_only_exp_and_ip(self) -> None:
        self.assertEqual(format_shared_column_entry(UDIPEXPDocumentKind.UD, "UD-123"), "UD-123")
        self.assertEqual(format_shared_column_entry(UDIPEXPDocumentKind.EXP, "EXP-123"), "EXP: EXP-123")
        self.assertEqual(format_shared_column_entry(UDIPEXPDocumentKind.IP, "IP-123"), "IP: IP-123")

    def test_shared_column_values_order_exp_before_ip(self) -> None:
        documents = [
            IPDocumentPayload(
                document_number=DocumentExtractionField("IP-002"),
                document_date=DocumentExtractionField("2026-04-03"),
                lc_sc_number=DocumentExtractionField("LC-0043"),
            ),
            EXPDocumentPayload(
                document_number=DocumentExtractionField("EXP-001"),
                document_date=DocumentExtractionField("2026-04-02"),
                lc_sc_number=DocumentExtractionField("LC-0043"),
            ),
        ]

        self.assertEqual(format_shared_column_values(documents), "EXP: EXP-001\nIP: IP-002")


if __name__ == "__main__":
    unittest.main()
