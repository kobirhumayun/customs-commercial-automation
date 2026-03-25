from __future__ import annotations

import argparse

from customs_automation.core.rule_pack import validate_rule_pack_version
from customs_automation.workflows import (
    bb_dashboard_verification,
    export_lc_sc,
    import_btb_lc,
    ud_ip_exp,
)

WORKFLOW_HANDLERS = {
    "export-lc-sc": export_lc_sc,
    "ud-ip-exp": ud_ip_exp,
    "import-btb-lc": import_btb_lc,
    "bb-dashboard-verification": bb_dashboard_verification,
}


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="customs-commercial-automation",
        description="Manually triggered CLI tools for customs/commercial workflows.",
    )

    subparsers = parser.add_subparsers(dest="command", required=True)

    for command in WORKFLOW_HANDLERS:
        subparsers.add_parser(command)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    workflow_module = WORKFLOW_HANDLERS[args.command]
    validate_rule_pack_version(workflow_module.RULE_PACK_VERSION)
    return workflow_module.run()
