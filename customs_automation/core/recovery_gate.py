from __future__ import annotations

import json
from pathlib import Path

from customs_automation.core.contracts import WritePhaseStatus



def find_blocking_prior_run(base_dir: Path, workflow_id: str) -> str | None:
    if not base_dir.exists():
        return None

    run_dirs = sorted((path for path in base_dir.iterdir() if path.is_dir()), reverse=True)
    for run_dir in run_dirs:
        state_path = run_dir / "run_state.json"
        if not state_path.exists():
            continue

        payload = json.loads(state_path.read_text(encoding="utf-8"))
        if payload.get("workflow_id") != workflow_id:
            continue

        if payload.get("write_phase_status") == WritePhaseStatus.UNCERTAIN_NOT_COMMITTED.value:
            return str(payload.get("run_id") or run_dir.name)

    return None
