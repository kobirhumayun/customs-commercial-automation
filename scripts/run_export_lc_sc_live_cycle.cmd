@echo off
setlocal
set SCRIPT_DIR=%~dp0
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%SCRIPT_DIR%run_live_cycle.ps1" -Workflow "export_lc_sc" -Config "D:\customs-automation\export_lc_sc.toml" -PauseAtEnd
endlocal
