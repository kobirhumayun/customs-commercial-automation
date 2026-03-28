from __future__ import annotations

import unittest

from project.models.enums import WorkflowId
from project.rules import load_rule_pack


class RuleLoaderTests(unittest.TestCase):
    def test_loader_returns_empty_but_valid_export_rule_pack(self) -> None:
        rule_pack = load_rule_pack(WorkflowId.EXPORT_LC_SC)

        self.assertEqual(rule_pack.rule_pack_id, "export_lc_sc.default")
        self.assertEqual(rule_pack.rule_pack_version, "1.0.0")
        self.assertEqual(rule_pack.rule_definitions, ())


if __name__ == "__main__":
    unittest.main()
