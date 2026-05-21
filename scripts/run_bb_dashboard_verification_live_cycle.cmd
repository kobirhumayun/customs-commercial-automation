@echo off
setlocal
set SCRIPT_DIR=%~dp0
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%SCRIPT_DIR%run_bb_dashboard_verification_live_cycle.ps1" -Config "D:\customs-automation\workflow.toml" -PauseAtEnd
endlocal
