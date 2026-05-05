@echo off
setlocal
set SCRIPT_DIR=%~dp0
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%SCRIPT_DIR%run_live_cycle.ps1" -Workflow "ud_ip_exp" -Config "D:\customs-automation\workflow.toml" -PauseAtEnd
endlocal

