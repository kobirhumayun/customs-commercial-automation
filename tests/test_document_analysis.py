from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from project.documents import JsonManifestSavedDocumentAnalysisProvider
from project.models import SavedDocument


class SavedDocumentAnalysisProviderTests(unittest.TestCase):
    def test_json_manifest_provider_matches_by_destination_path(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            manifest_path = Path(temp_dir) / "document-analysis.json"
            manifest_path.write_text(
                json.dumps(
                    [
                        {
                            "destination_path": "C:/docs/supporting.pdf",
                            "extracted_pi_number": "PDL-26-0042",
                            "clause_related_lc_sc_number": "LC-0038",
                            "clause_excerpt": "PI ref PDL-26-0042 under LC-0038",
                            "clause_confidence": 0.99,
                        }
                    ]
                ),
                encoding="utf-8",
            )

            analysis = JsonManifestSavedDocumentAnalysisProvider(manifest_path).analyze(
                saved_document=SavedDocument(
                    saved_document_id="doc-1",
                    mail_id="mail-1",
                    attachment_name="supporting.pdf",
                    normalized_filename="supporting.pdf",
                    destination_path="C:/docs/supporting.pdf",
                    file_sha256="a" * 64,
                    save_decision="saved_new",
                )
            )

        self.assertEqual(analysis.analysis_basis, "json_manifest")
        self.assertEqual(analysis.extracted_pi_number, "PDL-26-0042")
        self.assertEqual(analysis.clause_related_lc_sc_number, "LC-0038")
        self.assertEqual(analysis.clause_confidence, 0.99)


if __name__ == "__main__":
    unittest.main()
